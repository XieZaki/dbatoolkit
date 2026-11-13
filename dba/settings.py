from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from dba.inventory import ValidationError, load_yaml
from dba.sql_timeout import (
    DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS,
    effective_sql_timeout,
    validate_diagnostic_sql_timeout,
)

# Shared by SQL Server disk growth and Slow triage for observed active transactions.
SQLSERVER_LONG_TRANSACTION_SECONDS = 3600


_DIAGNOSTIC_SQL_TIMEOUT = "diagnostic_sql_timeout_seconds"


def _diagnostic_overrides(data: object) -> dict[str, int]:
    """Validate the shared diagnostics section and map it onto engine settings fields."""
    if not isinstance(data, dict) or "diagnostics" not in data:
        return {}
    diagnostics = data["diagnostics"]
    if (not isinstance(diagnostics, dict) or not diagnostics
            or not set(diagnostics) <= {"slow", _DIAGNOSTIC_SQL_TIMEOUT}):
        raise ValidationError(
            "diagnostics may contain only slow.threshold_seconds and diagnostic_sql_timeout_seconds")
    overrides: dict[str, int] = {}
    if "slow" in diagnostics:
        slow = diagnostics["slow"]
        if not isinstance(slow, dict) or set(slow) != {"threshold_seconds"}:
            raise ValidationError("diagnostics.slow must contain threshold_seconds")
        value = slow["threshold_seconds"]
        if type(value) is not int or not 1 <= value <= 86400:
            raise ValidationError("diagnostics.slow.threshold_seconds must be an integer in 1..86400")
        overrides["long_running_seconds"] = value
    if _DIAGNOSTIC_SQL_TIMEOUT in diagnostics:
        overrides[_DIAGNOSTIC_SQL_TIMEOUT] = validate_diagnostic_sql_timeout(
            diagnostics[_DIAGNOSTIC_SQL_TIMEOUT])
    return overrides


def apply_diagnostic_settings(settings: object, path: Path | None) -> object:
    """Apply the shared diagnostics section without changing per-engine settings contracts."""
    if path is None:
        return settings
    overrides = _diagnostic_overrides(load_yaml(path))
    return cast(Any, replace(cast(Any, settings), **overrides)) if overrides else settings


apply_diagnostic_threshold = apply_diagnostic_settings


def validate_diagnostic_settings(path: Path | None) -> None:
    if path is not None:
        _diagnostic_overrides(load_yaml(path))


def _engine_values(data: object, section: str, cls: type, missing: str, unknown: str) -> dict[str, Any]:
    if not isinstance(data, dict) or section not in data or not set(data) <= {
        "sqlserver", "postgresql", "mysql", "oracle", "diagnostics"
    }:
        raise ValidationError(missing)
    values = data[section]
    if not isinstance(values, dict) or not set(values) <= {f.name for f in fields(cls)}:
        raise ValidationError(unknown)
    if _DIAGNOSTIC_SQL_TIMEOUT in values:
        raise ValidationError(
            "diagnostic_sql_timeout_seconds is global; set it only under diagnostics")
    return values


def _validate_sql_timeouts(settings: Any, cap_field: str, label: str) -> None:
    validate_diagnostic_sql_timeout(settings.diagnostic_sql_timeout_seconds)
    cap = getattr(settings, cap_field)
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 86400):
        raise ValidationError(f"{label} {cap_field} must be null or an integer in 1..86400")


@dataclass(frozen=True)
class SQLServerSettings:
    connect_timeout: int = 5
    # Optional stricter legacy cap; the global diagnostic SQL timeout is the default ceiling.
    query_timeout: int | None = None
    diagnostic_sql_timeout_seconds: int = DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS
    encrypt: bool = True
    trust_server_certificate: bool = False
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    plan_cardinality_ratio_warning: int = 10
    plan_cardinality_ratio_critical: int = 100
    plan_top_operators: int = 10
    plan_max_xml_size: int = 1048576
    plan_large_scan_rows: int = 100000

    def __post_init__(self) -> None:
        _validate_sql_timeouts(self, "query_timeout", "SQL Server")
        for key in (
            "connect_timeout",
            "long_running_seconds",
            "slow_top_n",
            "high_concurrency_threshold",
            "plan_cardinality_ratio_warning",
            "plan_cardinality_ratio_critical",
            "plan_top_operators",
        ):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("Timeouts and thresholds must be integers in 1..86400")
        if (self.plan_cardinality_ratio_critical <= self.plan_cardinality_ratio_warning):
            raise ValidationError("Critical cardinality ratio must exceed warning ratio")
        if type(self.plan_large_scan_rows) is not int or not 1 <= self.plan_large_scan_rows <= 1000000000:
            raise ValidationError("Large scan row threshold must be an integer in 1..1000000000")
        if type(self.plan_max_xml_size) is not int or not 4096 <= self.plan_max_xml_size <= 4 * 1048576:
            raise ValidationError("Plan XML size must be an integer in 4096..4194304 bytes")
        for key in ("encrypt", "trust_server_certificate"):
            if type(getattr(self, key)) is not bool:
                raise ValidationError("TLS options must be booleans")

    def effective_sql_timeout(self, script_timeout: int) -> int:
        return effective_sql_timeout(script_timeout, self.diagnostic_sql_timeout_seconds, self.query_timeout)

    @classmethod
    def load(cls, path: Path) -> "SQLServerSettings":
        return cls(**_engine_values(load_yaml(path), "sqlserver", cls,
                                    "Settings must contain sqlserver and optional postgresql",
                                    "Unknown or forbidden SQL Server setting"))


@dataclass(frozen=True)
class PostgreSQLSettings:
    connect_timeout: int = 5
    query_timeout: int | None = None
    diagnostic_sql_timeout_seconds: int = DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS
    sslmode: str = "verify-full"
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    idle_transaction_seconds: int = 60

    def __post_init__(self) -> None:
        _validate_sql_timeouts(self, "query_timeout", "PostgreSQL")
        for key in ("connect_timeout", "long_running_seconds", "slow_top_n",
                    "high_concurrency_threshold", "idle_transaction_seconds"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("PostgreSQL limits must be integers in 1..86400")
        if type(self.sslmode) is not str or self.sslmode not in {
            "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
        }:
            raise ValidationError("Invalid PostgreSQL sslmode")

    def effective_sql_timeout(self, script_timeout: int) -> int:
        return effective_sql_timeout(script_timeout, self.diagnostic_sql_timeout_seconds, self.query_timeout)

    @classmethod
    def load(cls, path: Path) -> "PostgreSQLSettings":
        return cls(**_engine_values(load_yaml(path), "postgresql", cls,
                                    "Settings must contain postgresql and optional sqlserver",
                                    "Unknown or forbidden PostgreSQL setting"))


@dataclass(frozen=True)
class MySQLSettings:
    connect_timeout: int = 5
    query_timeout: int | None = None
    diagnostic_sql_timeout_seconds: int = DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS
    charset: str = "utf8mb4"
    ssl_enabled: bool = True
    ssl_ca: str | None = None
    ssl_verify_cert: bool = True
    ssl_verify_identity: bool = True
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    idle_transaction_seconds: int = 60

    def __post_init__(self) -> None:
        _validate_sql_timeouts(self, "query_timeout", "MySQL")
        for key in ("connect_timeout", "long_running_seconds",
                    "slow_top_n", "high_concurrency_threshold", "idle_transaction_seconds"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("MySQL limits must be integers in 1..86400")
        if self.charset != "utf8mb4":
            raise ValidationError("MySQL charset must be utf8mb4")
        if any(type(getattr(self, key)) is not bool for key in (
            "ssl_enabled", "ssl_verify_cert", "ssl_verify_identity"
        )):
            raise ValidationError("MySQL SSL options must be booleans")
        if self.ssl_ca is not None and (type(self.ssl_ca) is not str or not self.ssl_ca.strip()):
            raise ValidationError("MySQL ssl_ca must be a nonempty path")

    def effective_sql_timeout(self, script_timeout: int) -> int:
        return effective_sql_timeout(script_timeout, self.diagnostic_sql_timeout_seconds, self.query_timeout)

    @classmethod
    def load(cls, path: Path) -> "MySQLSettings":
        return cls(**_engine_values(load_yaml(path), "mysql", cls,
                                    "Settings must contain mysql and optional other database sections",
                                    "Unknown or forbidden MySQL setting"))


@dataclass(frozen=True)
class OracleSettings:
    connect_timeout: int = 5
    call_timeout: int | None = None
    diagnostic_sql_timeout_seconds: int = DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    idle_transaction_seconds: int = 60

    def __post_init__(self) -> None:
        _validate_sql_timeouts(self, "call_timeout", "Oracle")
        for item in fields(self):
            if item.name in {"call_timeout", "diagnostic_sql_timeout_seconds"}:
                continue
            value = getattr(self, item.name)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("Oracle limits must be integers in 1..86400")

    def effective_sql_timeout(self, script_timeout: int) -> int:
        return effective_sql_timeout(script_timeout, self.diagnostic_sql_timeout_seconds, self.call_timeout)

    @classmethod
    def load(cls, path: Path) -> "OracleSettings":
        return cls(**_engine_values(load_yaml(path), "oracle", cls,
                                    "Settings must contain oracle and optional other database sections",
                                    "Unknown or forbidden Oracle setting"))
