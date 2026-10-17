"""Validated, non-secret per-instance connection option resolution."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass, replace
from typing import Any, cast

from dba.core import DatabaseType

_TIMEOUT_FIELDS = {
    "connect_timeout",
    "query_timeout",
    "server_selection_timeout",
    "socket_timeout",
    "socket_connect_timeout",
}
_BOOLEAN_FIELDS = {
    "encrypt",
    "trust_server_certificate",
    "ssl_disabled",
    "ssl_verify_cert",
    "ssl_verify_identity",
    "tls",
    "tls_allow_invalid_certificates",
    "ssl",
}
_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|token)\s*[:=]|://[^\s/]*:[^\s/]*@"
)

# MongoDB and Redis have runtime package definitions but no Adapter yet. Their
# option sets intentionally remain empty until a real connection path can use
# and test them.
CONNECTION_FIELDS: dict[DatabaseType, frozenset[str]] = {
    DatabaseType.SQLSERVER: frozenset({
        "encrypt", "trust_server_certificate", "connect_timeout", "query_timeout"
    }),
    DatabaseType.POSTGRESQL: frozenset({"sslmode", "connect_timeout", "query_timeout"}),
    DatabaseType.MYSQL: frozenset({
        "ssl_disabled", "ssl_verify_cert", "ssl_verify_identity", "ssl_ca",
        "connect_timeout", "query_timeout",
    }),
    DatabaseType.ORACLE: frozenset({"connect_timeout", "query_timeout"}),
    DatabaseType.MONGODB: frozenset(),
    DatabaseType.REDIS: frozenset(),
}

_SENSITIVE_PARTS = (
    "password", "passwd", "pwd", "secret", "token", "privatekey",
    "credential", "connectionstring", "dsnwithpassword", "authorization",
)
_SENSITIVE_EXACT = {"username", "user", "uid", "access_token", "private_key"}


class ConnectionConfigError(ValueError):
    pass


def _normalized_key(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _contains_sensitive_key(value: object) -> bool:
    raw = str(value).casefold()
    normalized = _normalized_key(value)
    return raw in _SENSITIVE_EXACT or normalized in {
        _normalized_key(item) for item in _SENSITIVE_EXACT
    } or any(part in normalized for part in _SENSITIVE_PARTS)


def _reject_nested_sensitive(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _contains_sensitive_key(key):
                raise ConnectionConfigError("Sensitive connection fields are prohibited")
            _reject_nested_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nested_sensitive(item)


def validate_connection_options(
    database_type: DatabaseType, value: Any
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConnectionConfigError("connection must be a mapping")
    _reject_nested_sensitive(value)
    allowed = CONNECTION_FIELDS[database_type]
    unknown = set(value) - allowed
    if unknown:
        label = {
            DatabaseType.SQLSERVER: "SQL Server",
            DatabaseType.POSTGRESQL: "PostgreSQL",
            DatabaseType.MYSQL: "MySQL",
            DatabaseType.ORACLE: "Oracle",
            DatabaseType.MONGODB: "MongoDB",
            DatabaseType.REDIS: "Redis",
        }[database_type]
        option = sorted(str(item) for item in unknown)[0]
        raise ConnectionConfigError(f"Unknown {label} connection option: {option}")
    result = dict(value)
    for key, item in result.items():
        if key in _TIMEOUT_FIELDS:
            if type(item) is not int or not 1 <= item <= 86400:
                raise ConnectionConfigError(f"{key} must be an integer in 1..86400")
        elif key in _BOOLEAN_FIELDS:
            if type(item) is not bool:
                raise ConnectionConfigError(f"{key} must be a boolean")
        elif key == "sslmode":
            if not isinstance(item, str) or item not in _SSLMODES:
                raise ConnectionConfigError("Invalid PostgreSQL sslmode")
        elif key == "ssl_ca":
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ConnectionConfigError("ssl_ca must be a nonempty path or null")
            if isinstance(item, str) and _SENSITIVE_VALUE.search(item):
                raise ConnectionConfigError("Sensitive connection values are prohibited")
    return result


def resolve_connection_settings(
    database_type: DatabaseType,
    inventory_connection: dict[str, Any],
    runtime_defaults: Any,
) -> Any:
    """Apply validated instance overrides to frozen database runtime defaults."""
    options = validate_connection_options(database_type, inventory_connection)
    overrides = dict(options)
    if database_type == DatabaseType.ORACLE and "query_timeout" in overrides:
        overrides["call_timeout"] = overrides.pop("query_timeout")
    if database_type == DatabaseType.MYSQL and "ssl_disabled" in overrides:
        overrides["ssl_enabled"] = not overrides.pop("ssl_disabled")
    return cast(Any, replace)(runtime_defaults, **overrides)


def public_connection_settings(database_type: DatabaseType, settings: object) -> dict[str, Any]:
    if not is_dataclass(settings):
        raise ConnectionConfigError("Effective settings must be a dataclass")
    values = asdict(cast(Any, settings))
    if database_type == DatabaseType.SQLSERVER:
        keys: tuple[str, ...] = (
            "encrypt", "trust_server_certificate", "connect_timeout", "query_timeout"
        )
    elif database_type == DatabaseType.POSTGRESQL:
        keys = ("sslmode", "connect_timeout", "query_timeout")
    elif database_type == DatabaseType.MYSQL:
        return {
            "ssl_disabled": not bool(values["ssl_enabled"]),
            "ssl_verify_cert": bool(values["ssl_enabled"] and values["ssl_verify_cert"]),
            "ssl_verify_identity": bool(values["ssl_enabled"] and values["ssl_verify_identity"]),
            "ssl_ca": values["ssl_ca"],
            "connect_timeout": values["connect_timeout"],
            "query_timeout": values["query_timeout"],
        }
    elif database_type == DatabaseType.ORACLE:
        return {
            "mode": "thin",
            "connect_timeout": values["connect_timeout"],
            "query_timeout": values["call_timeout"],
        }
    else:
        return {}
    return {key: values[key] for key in keys}
