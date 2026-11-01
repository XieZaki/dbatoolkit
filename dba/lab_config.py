"""Allowlisted, non-executing reader for local lab driver configuration."""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NoReturn, SupportsIndex, TypeVar

LabDatabase = Literal["mysql", "pg", "oracle", "sqlserver"]
_DATABASES: Final[frozenset[str]] = frozenset({"mysql", "pg", "oracle", "sqlserver"})
_FIELDS: Final[frozenset[str]] = frozenset({"host", "port", "database", "user", "password"})
_PUBLIC_FIELDS: Final[frozenset[str]] = frozenset({"host", "port", "database", "user"})
_MAX_SOURCE_BYTES: Final[int] = 256 * 1024
_MAX_AST_NODES: Final[int] = 10_000

_SAFE_MESSAGES: Final[dict[str, str]] = {
    "READ_FAILED": "Lab configuration could not be read.",
    "DECODE_FAILED": "Lab configuration encoding is unsupported.",
    "PARSE_FAILED": "Lab configuration is not valid static Python data.",
    "INVALID_STRUCTURE": "Lab configuration structure is unsupported.",
    "INVALID_FIELD": "Lab configuration contains a missing or unsupported field.",
    "INVALID_TYPE": "Lab configuration contains a field with an invalid type.",
    "INVALID_VALUE": "Lab configuration contains an invalid field value.",
    "UNKNOWN_DATABASE": "The requested lab database is not allowlisted.",
    "SOURCE_TOO_LARGE": "Lab configuration exceeds the supported size limit.",
    "CONNECTION_FAILED": "Lab database connection failed; driver details were suppressed.",
}


class LabConfigError(Exception):
    """Sanitized configuration error; never retains source text or path values."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if code in _SAFE_MESSAGES else "INVALID_STRUCTURE"
        super().__init__(_SAFE_MESSAGES[self.code])

    def __repr__(self) -> str:
        return f"LabConfigError(code={self.code!r})"


class LabConnectionError(Exception):
    """Generic connection failure that does not retain a driver error message."""

    def __init__(self) -> None:
        super().__init__(_SAFE_MESSAGES["CONNECTION_FAILED"])

    def __str__(self) -> str:
        return "Lab database connection failed; driver details were suppressed."


@dataclass(frozen=True, slots=True)
class LabDatabaseConfig:
    """Allowlisted connection metadata. Password is intentionally absent."""

    host: str
    port: int
    database: str
    user: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
        }


class Secret:
    """A connection secret whose ordinary string representations are redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __format__(self, format_spec: str) -> str:
        del format_spec
        return "<redacted>"

    @property
    def is_configured(self) -> bool:
        return bool(self.__value) and not self.__value.startswith("填写")

    def reveal_for_connection(self) -> str:
        """Return the value only at the explicit database-connection boundary."""
        return self.__value

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("Secret values cannot be serialized.")


T = TypeVar("T")


def connect_with_lab_secret(
    config_password: Secret | str,
    connector: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    """Call a lab connection factory, revealing a Secret only for that call.

    A caller-provided string remains supported as an explicit in-memory override
    for existing lab wrappers. The connector is expected to establish a DB
    connection immediately; errors are replaced so driver messages cannot echo
    connection secrets.
    """
    if isinstance(config_password, Secret):
        password = config_password.reveal_for_connection()
    elif type(config_password) is str:
        password = config_password
    else:
        raise LabConfigError("INVALID_TYPE")

    if not password or password.startswith("填写"):
        raise LabConfigError("INVALID_VALUE")

    try:
        return connector(*args, password, **kwargs)
    except Exception:
        pass
    # Raise after leaving the handler so the original driver exception is not
    # retained as an inspectable __context__ on the sanitized exception.
    raise LabConnectionError() from None


def read_lab_database_config(path: str | Path, database: str) -> LabDatabaseConfig:
    """Read only fixed non-secret fields for one supported lab database."""
    selected = _validate_database(database)
    values = _load_database_values(path, selected)
    return LabDatabaseConfig(
        host=_string_value(values, "host"),
        port=_port_value(values),
        database=_string_value(values, "database"),
        user=_string_value(values, "user"),
    )


def read_lab_connection_secret(path: str | Path, database: str) -> Secret:
    """Read the fixed password field through the dedicated connection API."""
    selected = _validate_database(database)
    values = _load_database_values(path, selected)
    password = values.get("password")
    if type(password) is not str:
        raise LabConfigError("INVALID_TYPE")
    return Secret(password)


def read_lab_connection_profile(path: str | Path, database: str) -> tuple[LabDatabaseConfig, Secret]:
    """Read public fields and the connection-only secret from one parsed snapshot."""
    selected = _validate_database(database)
    values = _load_database_values(path, selected)
    password = values.get("password")
    if type(password) is not str:
        raise LabConfigError("INVALID_TYPE")
    config = LabDatabaseConfig(
        host=_string_value(values, "host"),
        port=_port_value(values),
        database=_string_value(values, "database"),
        user=_string_value(values, "user"),
    )
    return config, Secret(password)


def _validate_database(database: str) -> LabDatabase:
    if type(database) is not str or database not in _DATABASES:
        raise LabConfigError("UNKNOWN_DATABASE")
    return database  # type: ignore[return-value]


def _load_database_values(path: str | Path, database: LabDatabase) -> dict[str, object]:
    source = _read_source(path)
    module: ast.Module | None = None
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        pass
    if module is None:
        raise LabConfigError("PARSE_FAILED")

    nodes = list(ast.walk(module))
    if len(nodes) > _MAX_AST_NODES:
        raise LabConfigError("SOURCE_TOO_LARGE")

    assignments: list[ast.expr] = []
    for index, statement in enumerate(module.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and type(statement.value.value) is str
        ):
            continue
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == "DATABASES":
                assignments.append(statement.value)
                continue
        raise LabConfigError("INVALID_STRUCTURE")

    if len(assignments) != 1:
        raise LabConfigError("INVALID_STRUCTURE")
    root = _static_value(assignments[0])
    if type(root) is not dict or set(root) != _DATABASES:
        raise LabConfigError("INVALID_STRUCTURE")
    entry = root.get(database)
    if type(entry) is not dict:
        raise LabConfigError("INVALID_STRUCTURE")
    if set(entry) != _FIELDS:
        raise LabConfigError("INVALID_FIELD")

    for field_name in _PUBLIC_FIELDS | {"password"}:
        if field_name not in entry:
            raise LabConfigError("INVALID_FIELD")
    if any(type(entry[name]) is not str for name in ("host", "database", "user", "password")):
        raise LabConfigError("INVALID_TYPE")
    if type(entry["port"]) is not int:
        raise LabConfigError("INVALID_TYPE")
    _port_value(entry)
    if not entry["host"] or not entry["database"] or not entry["user"]:
        raise LabConfigError("INVALID_VALUE")
    return entry


def _read_source(path: str | Path) -> str:
    raw: bytes | None = None
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        pass
    if raw is None:
        raise LabConfigError("READ_FAILED")
    if len(raw) > _MAX_SOURCE_BYTES:
        raise LabConfigError("SOURCE_TOO_LARGE")
    decoded: str | None = None
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        decoded = raw.decode(encoding)
    except (SyntaxError, UnicodeError, LookupError):
        pass
    if decoded is None:
        raise LabConfigError("DECODE_FAILED")
    return decoded


def _static_value(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        if type(node.value) in (str, int, bool, float, type(None)):
            return node.value
        raise LabConfigError("INVALID_STRUCTURE")
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                raise LabConfigError("INVALID_STRUCTURE")
            key = _static_value(key_node)
            if type(key) is not str or key in result:
                raise LabConfigError("INVALID_STRUCTURE")
            result[key] = _static_value(value_node)
        return result
    raise LabConfigError("INVALID_STRUCTURE")


def _string_value(values: dict[str, object], field_name: str) -> str:
    value = values.get(field_name)
    if type(value) is not str:
        raise LabConfigError("INVALID_TYPE")
    return value


def _port_value(values: dict[str, object]) -> int:
    value = values.get("port")
    if type(value) is not int:
        raise LabConfigError("INVALID_TYPE")
    if not 1 <= value <= 65535:
        raise LabConfigError("INVALID_VALUE")
    return value
