from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DatabaseType(str, Enum):
    SQLSERVER = "sqlserver"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    ORACLE = "oracle"
    REDIS = "redis"
    MONGODB = "mongodb"


class Severity(str, Enum):
    INFO = "INFO"
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DatabaseInstance:
    id: str
    name: str
    db_type: DatabaseType
    host: str
    port: int
    credential_alias: str
    database: str | None = None
    service_name: str | None = None
    sid: str | None = None
    instance_name: str | None = None
    environment: str | None = None
    level: str | None = None
    role: str | None = None
    cluster: str | None = None
    business: str | None = None
    owner: str | None = None
    enabled: bool = True
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    connection: dict[str, Any] = field(default_factory=dict)
    version_family: str | None = None


@dataclass(frozen=True)
class ReferenceCommand:
    text: str
    description: str
    manual_only: bool = field(default=True, init=False)


@dataclass(frozen=True)
class Finding:
    title: str
    severity: Severity
    evidence: dict[str, Any]
    recommendation: str
    reference_commands: tuple[ReferenceCommand, ...] = ()
    session: str | None = None
    login: str | None = None
    host: str | None = None
    ip: str | None = None
    sql: str | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    instance: DatabaseInstance
    diagnosed_at: datetime
    scenario: str
    findings: tuple[Finding, ...]
