"""Registry-backed read-only replication snapshots for supported SQL engines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseType


@dataclass(frozen=True)
class ReplicationEvidence:
    database_type: DatabaseType
    version: str | None
    samples: tuple[CollectorResult, ...]
    supported: bool = True
    unsupported_reason: str | None = None


_SCRIPTS: dict[DatabaseType, tuple[str, ...]] = {
    DatabaseType.SQLSERVER: ("replication_ag",),
    DatabaseType.POSTGRESQL: (
        "replication_node", "replication_members", "replication_receiver",
        "replication_slots", "replication_archiver",
    ),
    DatabaseType.MYSQL: ("replication_server_info", "replication_status"),
    DatabaseType.ORACLE: (
        "replication_database", "replication_metrics", "replication_processes",
        "replication_destinations",
    ),
}


def _version_parts(version: str | None) -> tuple[int, int, int]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version or "")
    return (int(match.group(1)), int(match.group(2) or 0),
            int(match.group(3) or 0)) if match else (0, 0, 0)


def _script_names(database_type: DatabaseType, version: str | None) -> tuple[str, ...]:
    names = _SCRIPTS[database_type]
    if database_type == DatabaseType.MYSQL and _version_parts(version) < (8, 0, 22):
        return ("replication_server_info", "replication_status_legacy")
    if database_type == DatabaseType.ORACLE and _version_parts(version) < (12, 2):
        return ("replication_database", "replication_metrics", "replication_processes",
                "replication_destinations")
    if database_type == DatabaseType.ORACLE:
        return ("replication_database", "replication_metrics", "replication_processes_modern",
                "replication_destinations")
    return names


def collect_replication_evidence(adapter: Any) -> ReplicationEvidence:
    """Collect only reviewed scripts for the single connected Inventory instance."""
    database_type = adapter.instance.db_type
    if database_type not in _SCRIPTS:
        raise ValueError("Replication is not implemented for this database type.")
    version = adapter.get_server_version()
    major, _minor, _patch = _version_parts(version)
    if database_type == DatabaseType.SQLSERVER and major and major < 11:
        return ReplicationEvidence(
            database_type, version, (), False,
            "Availability Groups require SQL Server 2012 or later.",
        )
    if database_type == DatabaseType.ORACLE and major and major < 12:
        return ReplicationEvidence(
            database_type, version, (), False,
            "Data Guard diagnosis is supported for Oracle 12c and later.",
        )
    if database_type == DatabaseType.POSTGRESQL and major and major < 13:
        return ReplicationEvidence(
            database_type, version, (), False,
            "This replication collector targets PostgreSQL 13 and later.",
        )

    registry = adapter.registry
    samples: list[CollectorResult] = []
    for name in _script_names(database_type, version):
        script = registry.get(name, database_type)
        result = adapter.execute_readonly(script)
        samples.append(result)
    return ReplicationEvidence(database_type, version, tuple(samples))
