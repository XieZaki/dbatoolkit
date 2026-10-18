from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from dba.inventory import ValidationError, load_yaml


def _diagnostic_threshold(data: object) -> int | None:
    if not isinstance(data, dict) or "diagnostics" not in data:
        return None
    diagnostics = data["diagnostics"]
    if not isinstance(diagnostics, dict) or set(diagnostics) != {"slow"}:
        raise ValidationError("diagnostics must contain only slow.threshold_seconds")
    slow = diagnostics["slow"]
    if not isinstance(slow, dict) or set(slow) != {"threshold_seconds"}:
        raise ValidationError("diagnostics.slow must contain threshold_seconds")
    value = slow["threshold_seconds"]
    if type(value) is not int or not 1 <= value <= 86400:
        raise ValidationError("diagnostics.slow.threshold_seconds must be an integer in 1..86400")
    return value


def apply_diagnostic_threshold(settings: object, path: Path | None) -> object:
    """Apply the shared slow threshold without changing per-engine settings contracts."""
    if path is None:
        return settings
    data = load_yaml(path)
    threshold = _diagnostic_threshold(data)
    return cast(Any, replace(cast(Any, settings), long_running_seconds=threshold)) \
        if threshold is not None else settings


def validate_diagnostic_settings(path: Path | None) -> None:
    if path is not None:
        _diagnostic_threshold(load_yaml(path))


@dataclass(frozen=True)
class SQLServerSettings:
    connect_timeout: int = 5
    query_timeout: int = 10
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
        for key in (
            "connect_timeout",
            "query_timeout",
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

    @classmethod
    def load(cls, path: Path) -> "SQLServerSettings":
        data = load_yaml(path)
        if not isinstance(data, dict) or "sqlserver" not in data or not set(data) <= {
            "sqlserver", "postgresql", "mysql", "oracle", "diagnostics"
        }:
            raise ValidationError("Settings must contain sqlserver and optional postgresql")
        values = data["sqlserver"]
        if not isinstance(values, dict) or not set(values) <= {f.name for f in fields(cls)}:
            raise ValidationError("Unknown or forbidden SQL Server setting")
        return cls(**values)


@dataclass(frozen=True)
class PostgreSQLSettings:
    connect_timeout: int = 5
    query_timeout: int = 10
    sslmode: str = "verify-full"
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    idle_transaction_seconds: int = 60

    def __post_init__(self) -> None:
        for key in ("connect_timeout", "query_timeout", "long_running_seconds", "slow_top_n",
                    "high_concurrency_threshold", "idle_transaction_seconds"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("PostgreSQL limits must be integers in 1..86400")
        if type(self.sslmode) is not str or self.sslmode not in {
            "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
        }:
            raise ValidationError("Invalid PostgreSQL sslmode")

    @classmethod
    def load(cls, path: Path) -> "PostgreSQLSettings":
        data = load_yaml(path)
        if not isinstance(data, dict) or "postgresql" not in data or not set(data) <= {
            "sqlserver", "postgresql", "mysql", "oracle", "diagnostics"
        }:
            raise ValidationError("Settings must contain postgresql and optional sqlserver")
        values = data["postgresql"]
        if not isinstance(values, dict) or not set(values) <= {f.name for f in fields(cls)}:
            raise ValidationError("Unknown or forbidden PostgreSQL setting")
        return cls(**values)


@dataclass(frozen=True)
class MySQLSettings:
    connect_timeout: int = 5
    query_timeout: int = 10
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
        for key in ("connect_timeout", "query_timeout", "long_running_seconds",
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

    @classmethod
    def load(cls, path: Path) -> "MySQLSettings":
        data = load_yaml(path)
        if not isinstance(data, dict) or "mysql" not in data or not set(data) <= {
            "sqlserver", "postgresql", "mysql", "oracle", "diagnostics"
        }:
            raise ValidationError("Settings must contain mysql and optional other database sections")
        values = data["mysql"]
        if not isinstance(values, dict) or not set(values) <= {f.name for f in fields(cls)}:
            raise ValidationError("Unknown or forbidden MySQL setting")
        return cls(**values)


@dataclass(frozen=True)
class OracleSettings:
    connect_timeout: int = 5
    call_timeout: int = 10
    long_running_seconds: int = 10
    slow_top_n: int = 10
    high_concurrency_threshold: int = 50
    idle_transaction_seconds: int = 60

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValidationError("Oracle limits must be integers in 1..86400")

    @classmethod
    def load(cls, path: Path) -> "OracleSettings":
        data = load_yaml(path)
        if not isinstance(data, dict) or "oracle" not in data or not set(data) <= {
            "sqlserver", "postgresql", "mysql", "oracle", "diagnostics"
        }:
            raise ValidationError("Settings must contain oracle and optional other database sections")
        values = data["oracle"]
        if not isinstance(values, dict) or not set(values) <= {f.name for f in fields(cls)}:
            raise ValidationError("Unknown or forbidden Oracle setting")
        return cls(**values)
