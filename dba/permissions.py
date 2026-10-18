"""Reviewed, text-only monitor permission registry and grant renderer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, TypedDict

from dba.core import DatabaseType
from dba.mysql_privileges import grant_satisfies_requirement


class PermissionStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FeaturePermissions:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    impact: str = "Some monitoring evidence may be unavailable."
    objects: tuple[str, ...] = ()
    grant_template: str = "reviewed_dialect_grant"
    revoke_template: str = "reviewed_dialect_revoke"
    version_constraints: tuple[str, ...] = ()
    permission_reasons: tuple[tuple[str, str], ...] = ()
    scope_requirements: tuple[tuple[str, str, str], ...] = ()


class PermissionAssessment(TypedDict):
    status: PermissionStatus
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]
    available: tuple[str, ...]


@dataclass(frozen=True)
class OraclePrincipal:
    """Oracle login principal, separate from the credential's login string."""

    raw_name: str | None
    source: str
    verified: bool

    @classmethod
    def from_runtime(cls, raw_name: str) -> OraclePrincipal:
        if not raw_name or any(ord(char) < 32 or ord(char) == 127 for char in raw_name):
            raise ValueError("Oracle runtime principal is empty or contains control characters")
        return cls(raw_name, "RUNTIME_SESSION_USER", True)

    @classmethod
    def unverified(cls, credential_username: str | None) -> OraclePrincipal:
        return cls(credential_username, "CREDENTIAL_USERNAME", False)

    @property
    def quoted_required(self) -> bool:
        return not bool(self.raw_name and re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", self.raw_name))

    @property
    def verification(self) -> str:
        return "VERIFIED" if self.verified else "UNVERIFIED"

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "credential_username": self.raw_name if not self.verified else None,
            "database_principal": self.raw_name if self.verified else "UNKNOWN",
            "source": self.source,
            "verified": self.verified,
            "verification": self.verification,
            "quoted_required": self.quoted_required if self.verified else False,
        }


# Privilege names describe effective monitor capabilities; grant renderer below
# maps only this closed vocabulary to reviewed dialect-specific statements.
REGISTRY: dict[DatabaseType, dict[str, FeaturePermissions]] = {
    DatabaseType.SQLSERVER: {
        "slow": FeaturePermissions(("VIEW SERVER STATE", "VIEW ANY DATABASE"),
                                    ("VIEW SERVER PERFORMANCE STATE",),
                                    "Cross-session activity and request SQL may be unavailable.",
                                    ("sys.dm_exec_sessions", "sys.dm_exec_requests", "sys.dm_exec_connections",
                                     "sys.dm_exec_sql_text", "sys.dm_tran_session_transactions",
                                     "sys.dm_tran_active_transactions", "sys.all_columns", "sys.fn_my_permissions",
                                     "sys.login_token", "sys.server_principals", "sys.database_principals",
                                     "sys.server_permissions", "sys.database_permissions", "sys.sql_modules",
                                     "sys.schemas", "sys.objects", "sys.user_token", "sys.sysprocesses"),
                                    version_constraints=("SQL Server 10.50-15.x: VIEW SERVER STATE",
                                                         "SQL Server 16+: VIEW SERVER PERFORMANCE STATE")),
        "plan": FeaturePermissions(("VIEW SERVER STATE",), ("VIEW SERVER PERFORMANCE STATE",),
                                    "Cached and current execution plans may be unavailable.",
                                    ("sys.dm_exec_query_plan", "sys.dm_exec_query_stats", "sys.dm_exec_sql_text",
                                     "sys.dm_exec_requests", "sys.dm_exec_connections"),
                                    version_constraints=("SQL Server 10.50-15.x: VIEW SERVER STATE",
                                                         "SQL Server 16+: VIEW SERVER PERFORMANCE STATE")),
        "disk-growth": FeaturePermissions(("VIEW SERVER STATE", "VIEW ANY DATABASE"),
                                          ("SELECT msdb.dbo.backupset", "VIEW SERVER PERFORMANCE STATE"),
                                          "Backup history or AG/tempdb details may be unavailable.",
                                          ("sys.databases", "sys.database_files", "sys.dm_db_log_space_usage",
                                           "sys.dm_db_file_space_usage", "msdb.dbo.backupset",
                                           "sys.dm_hadr_database_replica_states", "sys.availability_replicas"),
                                          version_constraints=("SQL Server 10.50-15.x: VIEW SERVER STATE",
                                                               "SQL Server 16+: VIEW SERVER PERFORMANCE STATE")),
        "replication": FeaturePermissions(
            ("VIEW SERVER STATE",), ("VIEW SERVER PERFORMANCE STATE",),
            "Availability Group topology and queue evidence may be unavailable.",
            ("sys.availability_groups", "sys.availability_replicas",
             "sys.dm_hadr_availability_replica_states", "sys.dm_hadr_database_replica_states",
             "sys.availability_databases_cluster"),
            version_constraints=("SQL Server 2008 R2: Availability Groups unsupported",
                                 "SQL Server 2012-2019: VIEW SERVER STATE",
                                 "SQL Server 2022+: VIEW SERVER PERFORMANCE STATE")),
    },
    DatabaseType.POSTGRESQL: {
        "slow": FeaturePermissions(("pg_monitor", "CONNECT"), (), "Cross-session statistics may be unavailable.",
                                    ("pg_stat_activity", "pg_locks", "pg_stat_database", "pg_settings",
                                     "pg_replication_slots", "pg_stat_archiver", "pg_stat_replication", "pg_roles")),
        "disk-growth": FeaturePermissions(("pg_monitor", "CONNECT"), (), "WAL, archive, or replication details may be unavailable.",
                                          ("pg_database", "pg_stat_database", "pg_stat_archiver",
                                           "pg_stat_replication", "pg_replication_slots", "pg_stat_activity",
                                           "pg_class", "pg_namespace")),
        "replication": FeaturePermissions(
            ("pg_monitor", "CONNECT"), (),
            "WAL sender, receiver, slot, or archive evidence may be unavailable.",
            ("pg_stat_replication", "pg_stat_wal_receiver", "pg_replication_slots",
             "pg_stat_archiver", "pg_settings")),
    },
    DatabaseType.MYSQL: {
        "slow": FeaturePermissions(("PROCESS", "SELECT performance_schema"), (),
                                    "Other users' sessions or statement/wait details may be unavailable.",
                                    ("performance_schema.threads", "performance_schema.events_statements_current",
                                     "performance_schema.events_waits_current", "performance_schema.data_lock_waits",
                                     "performance_schema.data_locks", "performance_schema.setup_consumers",
                                     "performance_schema.global_status", "information_schema.innodb_trx",
                                     "information_schema.processlist", "information_schema.innodb_lock_waits",
                                     "information_schema.innodb_locks",
                                     "information_schema.user_privileges", "information_schema.schema_privileges",
                                     "information_schema.table_privileges", "information_schema.column_privileges",
                                     "information_schema.schemata", "show grants"),
                                    permission_reasons=(
                                        ("PROCESS", "Required to inspect sessions across accounts."),
                                        ("SELECT performance_schema", "Required to read reviewed session, statement, lock and instrumentation views."),
                                    ),
                                    scope_requirements=(
                                        ("PROCESS", "PROCESS", "*.*"),
                                        ("SELECT performance_schema", "SELECT", "performance_schema.*"),
                                    )),
        "disk-growth": FeaturePermissions((), ("PROCESS", "REPLICATION CLIENT", "SELECT performance_schema"),
                                          "Transaction, binary-log inventory, and InnoDB evidence may be unavailable.",
                                          ("information_schema.tables", "information_schema.files",
                                           "information_schema.innodb_trx",
                                           "performance_schema.global_variables",
                                           "performance_schema.replication_connection_status", "SHOW BINARY LOGS",
                                           "information_schema.user_privileges", "information_schema.schema_privileges",
                                           "information_schema.table_privileges", "information_schema.column_privileges",
                                           "performance_schema.setup_consumers", "performance_schema.global_status",
                                           "performance_schema.threads", "information_schema.schemata",
                                           "show grants"),
                                          permission_reasons=(
                                              ("PROCESS", "Optional cross-account transaction or tablespace evidence."),
                                              ("REPLICATION CLIENT", "Optional SHOW BINARY LOGS inventory."),
                                              ("SELECT performance_schema", "Optional InnoDB and replication metadata."),
                                          ),
                                          scope_requirements=(
                                              ("PROCESS", "PROCESS", "*.*"),
                                              ("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),
                                              ("SELECT performance_schema", "SELECT", "performance_schema.*"),
                                          )),
        "replication": FeaturePermissions(
            ("REPLICATION CLIENT",), (),
            "Replication channel status and thread errors may be unavailable.",
            ("show replica status", "show slave status"),
            permission_reasons=(("REPLICATION CLIENT", "Required for reviewed replication status metadata."),),
            scope_requirements=(("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),)),
    },
    DatabaseType.ORACLE: {
        "slow": FeaturePermissions(("CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$DATABASE",
                                     "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL", "SELECT SYS.V_$TRANSACTION"),
                                    ("SELECT SYS.V_$LOCK", "SELECT SYS.V_$LOCKED_OBJECT",
                                     "SELECT SYS.V_$PROCESS", "SELECT SYS.V_$SESSION_WAIT"),
                                    "Lock, process, wait, or SQL evidence may be unavailable.",
                                    ("SYS.V_$INSTANCE", "SYS.V_$DATABASE", "SYS.V_$SESSION", "SYS.V_$SQL",
                                     "SYS.V_$TRANSACTION", "SYS.V_$LOCK", "SYS.V_$LOCKED_OBJECT",
                                     "SYS.V_$PROCESS", "SYS.V_$SESSION_WAIT", "SYS.USER_TAB_PRIVS_RECD",
                                     "SYS.USER_ROLE_PRIVS", "SYS.SESSION_PRIVS", "SYS.USER_OBJECTS", "DUAL",
                                     "USER_TAB_PRIVS_RECD", "USER_ROLE_PRIVS", "SESSION_PRIVS", "USER_OBJECTS")),
        "disk-growth": FeaturePermissions(("CREATE SESSION", "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
                                           "SELECT SYS.DBA_TABLESPACES", "SELECT SYS.DBA_DATA_FILES",
                                           "SELECT SYS.DBA_TEMP_FREE_SPACE"),
                                          ("SELECT SYS.V_$RECOVERY_FILE_DEST", "SELECT SYS.V_$RECOVERY_AREA_USAGE",
                                           "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$ARCHIVED_LOG",
                                           "SELECT SYS.V_$LOG_HISTORY", "SELECT SYS.V_$DATABASE"),
                                          "FRA, archive, or redo retention details may be unavailable.",
                                          ("SYS.DBA_TABLESPACE_USAGE_METRICS", "SYS.DBA_TABLESPACES",
                                           "SYS.DBA_DATA_FILES", "SYS.DBA_TEMP_FREE_SPACE",
                                           "SYS.V_$RECOVERY_FILE_DEST", "SYS.V_$RECOVERY_AREA_USAGE",
                                           "SYS.V_$ARCHIVE_DEST_STATUS", "SYS.V_$ARCHIVED_LOG", "SYS.V_$LOG_HISTORY",
                                           "SYS.V_$DATABASE", "SYS.V_$INSTANCE", "SYS.V_$SESSION", "SYS.V_$SQL",
                                           "SYS.V_$TRANSACTION", "SYS.V_$LOCK", "SYS.V_$LOCKED_OBJECT",
                                           "SYS.V_$PROCESS", "SYS.V_$SESSION_WAIT", "SYS.USER_TAB_PRIVS_RECD",
                                           "SYS.USER_ROLE_PRIVS", "SYS.SESSION_PRIVS", "SYS.USER_OBJECTS", "DUAL",
                                           "USER_TAB_PRIVS_RECD", "USER_ROLE_PRIVS", "SESSION_PRIVS", "USER_OBJECTS")),
        "replication": FeaturePermissions(
            ("CREATE SESSION", "SELECT SYS.V_$DATABASE"),
            ("SELECT SYS.V_$DATAGUARD_STATS", "SELECT SYS.V_$MANAGED_STANDBY",
             "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$DATAGUARD_PROCESS"),
            "Data Guard lag, recovery process, or remote destination evidence may be unavailable.",
            ("SYS.V_$DATABASE", "SYS.V_$DATAGUARD_STATS", "SYS.V_$MANAGED_STANDBY",
             "SYS.V_$ARCHIVE_DEST_STATUS", "SYS.V_$DATAGUARD_PROCESS")),
    },
}


ALL_FEATURES = ("slow", "plan", "disk-growth", "replication")

# Oracle replication resolves its version only after connecting and completing
# the common monitor preflight. Keep the full, fixed bootstrap contract in the
# Permission Registry so offline grants cover the same path as runtime checks.
ORACLE_COMMON_MONITOR_REQUIREMENTS: tuple[str, ...] = (
    "CREATE SESSION",
    "SELECT SYS.V_$INSTANCE",
    "SELECT SYS.V_$VERSION",
    "SELECT SYS.V_$DATABASE",
    "SELECT SYS.V_$SESSION",
    "SELECT SYS.V_$SQL",
    "SELECT SYS.V_$TRANSACTION",
)

# Bootstrap privilege used by the fixed version detector. Other feature
# profiles may include it through ORACLE_COMMON_MONITOR_REQUIREMENTS.
VERSION_DETECTION_PRIVILEGES: dict[DatabaseType, tuple[str, ...]] = {
    DatabaseType.ORACLE: ("SELECT SYS.V_$VERSION",),
}


def features_for(database_type: DatabaseType) -> tuple[str, ...]:
    return tuple(feature for feature in ALL_FEATURES if feature in REGISTRY.get(database_type, {}))


def feature_supported(database_type: DatabaseType, feature: str, version: str | None) -> bool:
    """Return known feature-version support; unknown versions remain assessable."""
    if feature != "replication" or not version:
        return True
    match = re.search(r"\d+(?:\.\d+){0,2}", version)
    if not match:
        return True
    major = int(match.group().split(".", maxsplit=1)[0])
    if database_type == DatabaseType.POSTGRESQL and major >= 10000:
        major //= 10000
    if database_type == DatabaseType.MYSQL:
        release = tuple(int(part) for part in match.group().split(".")[:2])
        return release >= (5, 7)
    if database_type == DatabaseType.ORACLE:
        release = tuple(int(part) for part in match.group().split(".")[:2])
        return release >= (11, 2)
    minimum = {
        DatabaseType.SQLSERVER: 11,
        DatabaseType.POSTGRESQL: 13,
    }.get(database_type)
    return major >= minimum if minimum is not None else False


def status_for(
    requirements: FeaturePermissions, observed: Iterable[str] | None,
) -> tuple[PermissionStatus, tuple[str, ...]]:
    evidence = assess_permissions(requirements, observed)
    return evidence["status"], evidence["missing_required"]


def assess_permissions(
    requirements: FeaturePermissions, observed: Iterable[str] | None,
) -> PermissionAssessment:
    if observed is None:
        return {"status": PermissionStatus.UNKNOWN, "missing_required": (),
                "missing_optional": (), "available": ()}
    granted = set(observed)
    scoped_requirements = {label: (privilege, scope)
                           for label, privilege, scope in requirements.scope_requirements}

    def is_satisfied(label: str) -> bool:
        scoped = scoped_requirements.get(label)
        if scoped:
            return any(grant_satisfies_requirement(statement, *scoped) for statement in granted)
        return label in granted

    missing = tuple(item for item in requirements.required if not is_satisfied(item))
    missing_optional = tuple(item for item in requirements.optional if not is_satisfied(item))
    if missing:
        status = PermissionStatus.MISSING
    elif missing_optional:
        status = PermissionStatus.PARTIAL
    else:
        status = PermissionStatus.READY
    return {"status": status, "missing_required": missing,
            "missing_optional": missing_optional,
            "available": tuple(item for item in (*requirements.required, *requirements.optional)
                                if is_satisfied(item))}


def privilege_reasons(requirements: FeaturePermissions, privileges: Iterable[str]) -> tuple[dict[str, str], ...]:
    """Explain only capabilities missing from the current Feature assessment."""
    reasons = dict(requirements.permission_reasons)
    return tuple({"privilege": item, "reason": reasons.get(
        item, "Required by the Feature's reviewed monitoring capability set.")}
        for item in privileges)


def requirements_for(database_type: DatabaseType, feature: str, version: str | None = None) -> FeaturePermissions:
    definition = REGISTRY.get(database_type, {}).get(feature)
    if definition is None:
        raise KeyError(feature)
    if database_type == DatabaseType.SQLSERVER and version:
        major = _major(version)
        if major:
            # SQL Server 2022 introduced the narrower performance-state grant.
            required = tuple("VIEW SERVER PERFORMANCE STATE" if item == "VIEW SERVER STATE" and major >= 16 else item
                             for item in definition.required)
            optional = tuple(item for item in definition.optional
                             if item != "VIEW SERVER PERFORMANCE STATE")
            return FeaturePermissions(required, optional, definition.impact, definition.objects,
                                      definition.grant_template, definition.revoke_template,
                                      definition.version_constraints, definition.permission_reasons,
                                      definition.scope_requirements)
    if database_type == DatabaseType.ORACLE and feature == "replication" and version:
        version_match = re.match(r"\s*(\d+)\.(\d+)", version)
        if version_match and (int(version_match.group(1)), int(version_match.group(2))) >= (12, 2):
            optional = tuple(item for item in definition.optional
                             if item != "SELECT SYS.V_$MANAGED_STANDBY")
            return FeaturePermissions(definition.required, optional, definition.impact,
                                      definition.objects, definition.grant_template,
                                      definition.revoke_template, definition.version_constraints,
                                      definition.permission_reasons, definition.scope_requirements)
    return definition


_REPLICATION_PROFILE_REQUIREMENTS: dict[str, FeaturePermissions] = {
    "mysql57_replication": FeaturePermissions(
        ("REPLICATION CLIENT",), (),
        "MySQL 5.7 replication channel status is unavailable.",
        ("SHOW SLAVE STATUS",),
        permission_reasons=(("REPLICATION CLIENT", "Required by the reviewed SHOW SLAVE STATUS script."),),
        scope_requirements=(("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),)),
    "mysql80_pre_8022_replication": FeaturePermissions(
        ("REPLICATION CLIENT",), (),
        "MySQL 8.0.21 and earlier replication channel status is unavailable.",
        ("SHOW SLAVE STATUS",),
        permission_reasons=(("REPLICATION CLIENT", "Required by the reviewed legacy replication status script."),),
        scope_requirements=(("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),)),
    "mysql80_8022_plus_replication": FeaturePermissions(
        ("REPLICATION CLIENT",), (),
        "MySQL 8.0.22+ replication channel status is unavailable.",
        ("SHOW REPLICA STATUS",),
        permission_reasons=(("REPLICATION CLIENT", "Required by the reviewed replica status script."),),
        scope_requirements=(("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),)),
    "mysql84_replication": FeaturePermissions(
        ("REPLICATION CLIENT",), (), "MySQL 8.4 replication channel status is unavailable.",
        ("SHOW REPLICA STATUS",),
        permission_reasons=(("REPLICATION CLIENT", "Required by the reviewed replica status script."),),
        scope_requirements=(("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),)),
    "pg13_18_replication": FeaturePermissions(
        ("pg_monitor", "CONNECT"), (),
        "PostgreSQL replication sender, receiver, slots, or archive evidence is unavailable.",
        ("pg_stat_replication", "pg_stat_wal_receiver", "pg_replication_slots", "pg_stat_archiver")),
    "sqlserver_ag_pre2022": FeaturePermissions(
        ("VIEW SERVER STATE",), (), "Availability Group state and queue evidence is unavailable.",
        ("sys.availability_groups", "sys.availability_replicas",
         "sys.dm_hadr_availability_replica_states", "sys.dm_hadr_database_replica_states",
         "sys.availability_databases_cluster"),
        version_constraints=("SQL Server 2012-2019",)),
    "sqlserver_ag_2022": FeaturePermissions(
        ("VIEW SERVER PERFORMANCE STATE",), (),
        "Availability Group state and queue evidence is unavailable.",
        ("sys.availability_groups", "sys.availability_replicas",
         "sys.dm_hadr_availability_replica_states", "sys.dm_hadr_database_replica_states",
         "sys.availability_databases_cluster"),
        version_constraints=("SQL Server 2022",)),
}


_SLOW_PROFILE_REQUIREMENTS: dict[str, FeaturePermissions] = {
    "sqlserver_slow_legacy": FeaturePermissions(
        ("VIEW SERVER STATE", "VIEW ANY DATABASE"), ("VIEW SERVER PERFORMANCE STATE",),
        "Cross-session activity and request SQL may be unavailable.",
        REGISTRY[DatabaseType.SQLSERVER]["slow"].objects,
        permission_reasons=(("VIEW SERVER STATE", "Required by reviewed session, request, connection and transaction DMV queries."),
                            ("VIEW ANY DATABASE", "Required for reliable database-name visibility."),
                            ("VIEW SERVER PERFORMANCE STATE", "Optional newer SQL Server performance-state visibility.")),
        version_constraints=("SQL Server 2008 R2-2019: VIEW SERVER STATE",)),
    "sqlserver_slow_2022": FeaturePermissions(
        ("VIEW SERVER PERFORMANCE STATE", "VIEW ANY DATABASE"), ("VIEW SERVER STATE",),
        "Cross-session activity and request SQL may be unavailable.",
        REGISTRY[DatabaseType.SQLSERVER]["slow"].objects,
        permission_reasons=(("VIEW SERVER PERFORMANCE STATE", "Required by the reviewed SQL Server 2022 performance DMV route."),
                            ("VIEW ANY DATABASE", "Required for reliable database-name visibility."),
                            ("VIEW SERVER STATE", "Optional legacy server-state visibility.")),
        version_constraints=("SQL Server 2022: VIEW SERVER PERFORMANCE STATE",)),
    "mysql57_slow": REGISTRY[DatabaseType.MYSQL]["slow"],
    "mysql80_slow": REGISTRY[DatabaseType.MYSQL]["slow"],
    "mysql84_slow": REGISTRY[DatabaseType.MYSQL]["slow"],
    "pg13_18_slow": REGISTRY[DatabaseType.POSTGRESQL]["slow"],
    **{
        profile: FeaturePermissions(
            ORACLE_COMMON_MONITOR_REQUIREMENTS,
            ("SELECT SYS.V_$LOCK", "SELECT SYS.V_$LOCKED_OBJECT"),
            "Optional lock evidence may be unavailable.",
            ("SYS.V_$INSTANCE", "SYS.V_$VERSION", "SYS.V_$DATABASE", "SYS.V_$SESSION",
             "SYS.V_$SQL", "SYS.V_$TRANSACTION", "SYS.V_$LOCK", "SYS.V_$LOCKED_OBJECT",
             ),
            permission_reasons=tuple((item, "Optional evidence path; the reviewed session-based slow report remains available.")
                                     for item in ("SELECT SYS.V_$LOCK", "SELECT SYS.V_$LOCKED_OBJECT",
                                                  )),
        )
        for profile in ("oracle11gr2_slow", "oracle12c_121_slow", "oracle12c_122_slow", "oracle_modern_slow")
    },
}


_DISK_GROWTH_PROFILE_REQUIREMENTS: dict[str, FeaturePermissions] = {
    "sqlserver_disk_pre2014_permissions": FeaturePermissions(
        ("VIEW SERVER STATE", "VIEW ANY DATABASE"), ("SELECT msdb.dbo.backupset",),
        "Backup-history details may be unavailable.", REGISTRY[DatabaseType.SQLSERVER]["disk-growth"].objects,
        permission_reasons=(
            ("VIEW SERVER STATE", "Required by the reviewed SQL Server 2008 R2 log-space DMV route."),
            ("VIEW ANY DATABASE", "Required for reliable database-name visibility."),
            ("SELECT msdb.dbo.backupset", "Optional last-log-backup evidence."),
        ), version_constraints=("SQL Server 2008 R2 and 2012: VIEW SERVER STATE",)),
    "sqlserver_disk_2014_2019_permissions": FeaturePermissions(
        ("VIEW SERVER STATE", "VIEW ANY DATABASE"), ("SELECT msdb.dbo.backupset",),
        "Backup-history details may be unavailable.", REGISTRY[DatabaseType.SQLSERVER]["disk-growth"].objects,
        permission_reasons=(
            ("VIEW SERVER STATE", "Required by the reviewed log-space and TempDB monitoring DMV routes."),
            ("VIEW ANY DATABASE", "Required for reliable database-name visibility."),
            ("SELECT msdb.dbo.backupset", "Optional last-log-backup evidence."),
        ), version_constraints=("SQL Server 2014 through 2019: VIEW SERVER STATE",)),
    "sqlserver_disk_2022_permissions": FeaturePermissions(
        ("VIEW SERVER PERFORMANCE STATE", "VIEW ANY DATABASE"),
        ("SELECT msdb.dbo.backupset", "VIEW SERVER STATE"),
        "Backup-history details may be unavailable.", REGISTRY[DatabaseType.SQLSERVER]["disk-growth"].objects,
        permission_reasons=(
            ("VIEW SERVER PERFORMANCE STATE", "Required by SQL Server 2022 DMV permission rules."),
            ("VIEW ANY DATABASE", "Required for reliable database-name visibility."),
            ("SELECT msdb.dbo.backupset", "Optional last-log-backup evidence."),
            ("VIEW SERVER STATE", "Optional legacy server-state visibility."),
        ), version_constraints=("SQL Server 2022+: VIEW SERVER PERFORMANCE STATE",)),
    "mysql_disk_57_permissions": FeaturePermissions(
        (), ("PROCESS", "REPLICATION CLIENT", "SELECT performance_schema"),
        "Transaction, binary-log inventory, and InnoDB configuration evidence may be unavailable.",
        (*REGISTRY[DatabaseType.MYSQL]["disk-growth"].objects,
         "information_schema.files", "information_schema.tables"),
        permission_reasons=(
            ("PROCESS", "Optional cross-account transaction context."),
            ("REPLICATION CLIENT", "Optional SHOW BINARY LOGS inventory; required only when that evidence is requested."),
            ("SELECT performance_schema", "Optional InnoDB and replica metadata."),
        ), scope_requirements=(
            ("PROCESS", "PROCESS", "*.*"),
            ("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),
            ("SELECT performance_schema", "SELECT", "performance_schema.*"),
        ), version_constraints=("MySQL 5.7: expire_logs_days query variant",)),
    "mysql_disk_80_permissions": FeaturePermissions(
        (), ("PROCESS", "REPLICATION CLIENT", "SELECT performance_schema"),
        "Transaction, binary-log inventory, and InnoDB configuration evidence may be unavailable.",
        (*REGISTRY[DatabaseType.MYSQL]["disk-growth"].objects,
         "information_schema.files", "information_schema.tables"),
        permission_reasons=(
            ("PROCESS", "Optional cross-account transaction context."),
            ("REPLICATION CLIENT", "Optional SHOW BINARY LOGS inventory."),
            ("SELECT performance_schema", "Optional InnoDB and replica metadata."),
        ), scope_requirements=(
            ("PROCESS", "PROCESS", "*.*"),
            ("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),
            ("SELECT performance_schema", "SELECT", "performance_schema.*"),
        )),
    "mysql_disk_84_permissions": FeaturePermissions(
        (), ("PROCESS", "REPLICATION CLIENT", "SELECT performance_schema"),
        "Transaction, binary-log inventory, and InnoDB configuration evidence may be unavailable.",
        (*REGISTRY[DatabaseType.MYSQL]["disk-growth"].objects,
         "information_schema.files", "information_schema.tables"),
        permission_reasons=(
            ("PROCESS", "Optional cross-account transaction context."),
            ("REPLICATION CLIENT", "Optional SHOW BINARY LOGS inventory."),
            ("SELECT performance_schema", "Optional InnoDB and replica metadata."),
        ), scope_requirements=(
            ("PROCESS", "PROCESS", "*.*"),
            ("REPLICATION CLIENT", "REPLICATION CLIENT", "*.*"),
            ("SELECT performance_schema", "SELECT", "performance_schema.*"),
        )),
    "postgresql_disk_13_18_permissions": FeaturePermissions(
        ("pg_monitor", "CONNECT"), (),
        "Database size and monitor-only WAL/archive evidence are available through pg_monitor.",
        REGISTRY[DatabaseType.POSTGRESQL]["disk-growth"].objects,
        version_constraints=("PostgreSQL 13 through 18",)),
    "oracle_disk_11gr2_permissions": FeaturePermissions(
        (*ORACLE_COMMON_MONITOR_REQUIREMENTS, "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
         "SELECT SYS.DBA_TABLESPACES", "SELECT SYS.DBA_DATA_FILES", "SELECT SYS.DBA_TEMP_FREE_SPACE"),
        ("SELECT SYS.V_$RECOVERY_FILE_DEST", "SELECT SYS.V_$RECOVERY_AREA_USAGE",
         "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$ARCHIVED_LOG",
         "SELECT SYS.V_$LOG_HISTORY", "SELECT SYS.V_$DATABASE"),
        "Optional FRA, archive, and redo-history evidence may be unavailable.",
        REGISTRY[DatabaseType.ORACLE]["disk-growth"].objects,
        version_constraints=("Oracle 11gR2 minimum direct object grants",)),
    "oracle_disk_12c_121_permissions": FeaturePermissions(
        (*ORACLE_COMMON_MONITOR_REQUIREMENTS, "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
         "SELECT SYS.DBA_TABLESPACES", "SELECT SYS.DBA_DATA_FILES", "SELECT SYS.DBA_TEMP_FREE_SPACE"),
        ("SELECT SYS.V_$RECOVERY_FILE_DEST", "SELECT SYS.V_$RECOVERY_AREA_USAGE",
         "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$ARCHIVED_LOG",
         "SELECT SYS.V_$LOG_HISTORY", "SELECT SYS.V_$DATABASE"),
        "Optional FRA, archive, and redo-history evidence may be unavailable.",
        REGISTRY[DatabaseType.ORACLE]["disk-growth"].objects),
    "oracle_disk_12c_122_permissions": FeaturePermissions(
        (*ORACLE_COMMON_MONITOR_REQUIREMENTS, "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
         "SELECT SYS.DBA_TABLESPACES", "SELECT SYS.DBA_DATA_FILES", "SELECT SYS.DBA_TEMP_FREE_SPACE"),
        ("SELECT SYS.V_$RECOVERY_FILE_DEST", "SELECT SYS.V_$RECOVERY_AREA_USAGE",
         "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$ARCHIVED_LOG",
         "SELECT SYS.V_$LOG_HISTORY", "SELECT SYS.V_$DATABASE"),
        "Optional FRA, archive, and redo-history evidence may be unavailable.",
        REGISTRY[DatabaseType.ORACLE]["disk-growth"].objects),
    "oracle_disk_modern_permissions": FeaturePermissions(
        (*ORACLE_COMMON_MONITOR_REQUIREMENTS, "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
         "SELECT SYS.DBA_TABLESPACES", "SELECT SYS.DBA_DATA_FILES", "SELECT SYS.DBA_TEMP_FREE_SPACE"),
        ("SELECT SYS.V_$RECOVERY_FILE_DEST", "SELECT SYS.V_$RECOVERY_AREA_USAGE",
         "SELECT SYS.V_$ARCHIVE_DEST_STATUS", "SELECT SYS.V_$ARCHIVED_LOG",
         "SELECT SYS.V_$LOG_HISTORY", "SELECT SYS.V_$DATABASE"),
        "Optional FRA, archive, and redo-history evidence may be unavailable.",
        REGISTRY[DatabaseType.ORACLE]["disk-growth"].objects),
}

_PLAN_PROFILE_REQUIREMENTS: dict[str, FeaturePermissions] = {
    "sqlserver_plan_legacy": FeaturePermissions(
        ("VIEW SERVER STATE",), (),
        "Existing cached or active execution plans may be unavailable.",
        ("sys.dm_exec_requests", "sys.dm_exec_connections", "sys.dm_exec_query_stats",
         "sys.dm_exec_sql_text", "sys.dm_exec_query_plan"),
        version_constraints=("SQL Server 2008 R2-2019: VIEW SERVER STATE",),
        permission_reasons=(("VIEW SERVER STATE", "Required by the reviewed SQL Server plan DMV queries."),),
    ),
    "sqlserver_plan_2022": FeaturePermissions(
        ("VIEW SERVER PERFORMANCE STATE",), (),
        "Existing cached or active execution plans may be unavailable.",
        ("sys.dm_exec_requests", "sys.dm_exec_connections", "sys.dm_exec_query_stats",
         "sys.dm_exec_sql_text", "sys.dm_exec_query_plan"),
        version_constraints=("SQL Server 2022: VIEW SERVER PERFORMANCE STATE",),
        permission_reasons=(("VIEW SERVER PERFORMANCE STATE",
                             "Required by the reviewed SQL Server 2022 plan DMV queries."),),
    ),
}

for _profile_id, _objects in {
    "oracle11gr2_replication": ("SYS.V_$DATAGUARD_STATS", "SYS.V_$MANAGED_STANDBY",
                                "SYS.V_$ARCHIVE_DEST_STATUS"),
    "oracle12c_121_replication": ("SYS.V_$DATAGUARD_STATS", "SYS.V_$MANAGED_STANDBY",
                                  "SYS.V_$ARCHIVE_DEST_STATUS"),
    "oracle12c_122_replication": ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                  "SYS.V_$ARCHIVE_DEST_STATUS"),
    "oracle_modern_replication": ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                  "SYS.V_$ARCHIVE_DEST_STATUS"),
}.items():
    _REPLICATION_PROFILE_REQUIREMENTS[_profile_id] = FeaturePermissions(
        (*ORACLE_COMMON_MONITOR_REQUIREMENTS, *("SELECT " + item for item in _objects)), (),
        "Data Guard role, lag, process, or archive destination evidence is unavailable.",
        (*tuple(item.removeprefix("SELECT ") for item in ORACLE_COMMON_MONITOR_REQUIREMENTS
                if item.startswith("SELECT ")), *_objects),
    )


def requirements_for_profile(profile_id: str) -> FeaturePermissions:
    """Return the complete permission contract bound to one resolved route."""
    try:
        return _REPLICATION_PROFILE_REQUIREMENTS[profile_id]
    except KeyError:
        try:
            return _SLOW_PROFILE_REQUIREMENTS[profile_id]
        except KeyError:
            try:
                return _DISK_GROWTH_PROFILE_REQUIREMENTS[profile_id]
            except KeyError:
                try:
                    return _PLAN_PROFILE_REQUIREMENTS[profile_id]
                except KeyError:
                    raise ValueError("Resolved profile has no permission definition") from None


def render_profile_sql(profile_id: str, database_type: DatabaseType, account: str | OraclePrincipal,
                       database: str | None = None, version: str | None = None,
                       *, revoke: bool = False) -> str:
    requirements = requirements_for_profile(profile_id)
    return render_privileges_sql(database_type, requirements.required, account, database,
                                 version, revoke=revoke)


def _quote_identifier(value: str, database_type: DatabaseType) -> str:
    if not value or "\x00" in value:
        raise ValueError("Empty or NUL identifier")
    if database_type == DatabaseType.SQLSERVER:
        return "[" + value.replace("]", "]]" ) + "]"
    if database_type == DatabaseType.MYSQL:
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def render_oracle_principal(principal: str | OraclePrincipal) -> str:
    """Render only a database-confirmed Oracle principal; otherwise use a placeholder."""
    identity = principal if isinstance(principal, OraclePrincipal) else OraclePrincipal.unverified(principal)
    if not identity.verified or not identity.raw_name:
        return "<ORACLE_MONITOR_USER>"
    name = identity.raw_name
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("Oracle principal contains control characters")
    if not identity.quoted_required:
        return name
    return _quote_identifier(name, DatabaseType.ORACLE)


def _grant(permission: str, database_type: DatabaseType, account: str | OraclePrincipal, database: str | None,
           version: str | None) -> tuple[str, str] | None:
    if database_type == DatabaseType.ORACLE:
        user = render_oracle_principal(account)
    elif isinstance(account, str):
        user = _quote_identifier(account, database_type)
    else:
        raise ValueError("Database principal type does not match grant dialect")
    if database_type == DatabaseType.SQLSERVER:
        if permission == "VIEW SERVER STATE" and not version:
            return (
                f"-- SQL Server 2008 R2 through 2019\nGRANT VIEW SERVER STATE TO {user};\n"
                f"-- SQL Server 2022 and later\nGRANT VIEW SERVER PERFORMANCE STATE TO {user};",
                f"-- SQL Server 2008 R2 through 2019\nREVOKE VIEW SERVER STATE FROM {user};\n"
                f"-- SQL Server 2022 and later\nREVOKE VIEW SERVER PERFORMANCE STATE FROM {user};",
            )
        if permission == "VIEW SERVER STATE" and version and _major(version) >= 16:
            return (f"GRANT VIEW SERVER PERFORMANCE STATE TO {user};", f"REVOKE VIEW SERVER PERFORMANCE STATE FROM {user};")
        if permission == "VIEW SERVER PERFORMANCE STATE" and version and _major(version) < 16:
            return None
        if permission in {"VIEW SERVER STATE", "VIEW SERVER PERFORMANCE STATE", "VIEW ANY DATABASE"}:
            return (f"GRANT {permission} TO {user};", f"REVOKE {permission} FROM {user};")
        if permission == "SELECT msdb.dbo.backupset":
            return (f"GRANT SELECT ON [msdb].[dbo].[backupset] TO {user};", f"REVOKE SELECT ON [msdb].[dbo].[backupset] FROM {user};")
    elif database_type == DatabaseType.POSTGRESQL:
        if permission == "pg_monitor":
            return (f"GRANT pg_monitor TO {user};", f"REVOKE pg_monitor FROM {user};")
        if permission == "CONNECT" and database:
            db = _quote_identifier(database, database_type)
            return (f"GRANT CONNECT ON DATABASE {db} TO {user};", f"REVOKE CONNECT ON DATABASE {db} FROM {user};")
    elif database_type == DatabaseType.MYSQL:
        if not isinstance(account, str):
            raise ValueError("Database principal type does not match grant dialect")
        identity = account.split("@", 1) if "@" in account else [account, "<approved_monitor_client_host>"]
        username, host = identity
        mysql_user = "'" + username.replace("\\", "\\\\").replace("'", "''") + "'@'" + host.replace("\\", "\\\\").replace("'", "''") + "'"
        if permission in {"PROCESS", "REPLICATION CLIENT"}:
            return (f"GRANT {permission} ON *.* TO {mysql_user};", f"REVOKE {permission} ON *.* FROM {mysql_user};")
        if permission == "SELECT performance_schema":
            return (f"GRANT SELECT ON `performance_schema`.* TO {mysql_user};", f"REVOKE SELECT ON `performance_schema`.* FROM {mysql_user};")
    elif database_type == DatabaseType.ORACLE:
        if permission == "CREATE SESSION":
            return (f"GRANT CREATE SESSION TO {user};", f"REVOKE CREATE SESSION FROM {user};")
        match = re.fullmatch(r"SELECT (SYS\.[A-Z0-9_$#]+)", permission)
        if match:
            obj = match.group(1)
            return (f"GRANT SELECT ON {obj} TO {user};", f"REVOKE SELECT ON {obj} FROM {user};")
    return None


def _major(version: str) -> int:
    match = re.search(r"\d+", version)
    return int(match.group()) if match else 0


def scan_registry_objects(sql_root: Path) -> dict[DatabaseType, frozenset[str]]:
    """Conservative source scanner for referenced catalog/monitoring objects."""
    pattern = re.compile(r"\b(?:FROM|JOIN|APPLY)\s+([A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*){0,3})", re.I)
    result: dict[DatabaseType, set[str]] = {}
    for database_type in (DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                          DatabaseType.MYSQL, DatabaseType.ORACLE):
        objects: set[str] = set()
        for file in (sql_root / database_type.value).glob("*.sql"):
            source = file.read_text(encoding="utf-8")
            for match in pattern.finditer(source):
                name = match.group(1).rstrip(",;")
                lower = name.lower()
                if database_type == DatabaseType.ORACLE:
                    lower = ("sys.v_$" + lower[2:] if lower.startswith("v$") else "sys." + lower) if lower.startswith(("v$", "dba_")) else lower
                if database_type == DatabaseType.SQLSERVER and lower.startswith("tempdb.sys."):
                    lower = lower[len("tempdb."):]
                if lower == "lateral":
                    continue
                objects.add(lower)
            if database_type == DatabaseType.MYSQL and re.search(r"\bSHOW\s+BINARY\s+LOGS\b", source, re.I):
                objects.add("show binary logs")
            if database_type == DatabaseType.MYSQL and re.search(r"\bSHOW\s+GRANTS\b", source, re.I):
                objects.add("show grants")
            if database_type == DatabaseType.MYSQL and re.search(r"\bSHOW\s+(?:REPLICA|SLAVE)\s+STATUS\b", source, re.I):
                objects.add("show replica status" if re.search(r"\bSHOW\s+REPLICA\s+STATUS\b", source, re.I)
                            else "show slave status")
        result[database_type] = objects
    return {key: frozenset(value) for key, value in result.items()}


def render_sql(database_type: DatabaseType, features: Iterable[str], account: str | OraclePrincipal,
               database: str | None = None, version: str | None = None,
               *, revoke: bool = False) -> str:
    definitions = REGISTRY.get(database_type, {})
    privileges: list[str] = []
    for feature in features:
        if feature not in definitions:
            raise ValueError("Unsupported feature")
        for privilege in definitions[feature].required:
            if privilege not in privileges:
                privileges.append(privilege)
    return render_privileges_sql(database_type, privileges, account, database, version,
                                 revoke=revoke)


def render_privileges_sql(database_type: DatabaseType, privileges: Iterable[str], account: str | OraclePrincipal,
                          database: str | None = None, version: str | None = None,
                          *, revoke: bool = False) -> str:
    statements: list[str] = []
    for privilege in dict.fromkeys(privileges):
        rendered = _grant(privilege, database_type, account, database, version)
        if rendered:
            statement = rendered[int(revoke)]
            if statement not in statements:
                statements.append(statement)
    header = ("-- Generated by DBA Toolkit\n-- Review before execution\n"
              "-- DBA Toolkit does NOT execute this script\n")
    return header + ("\n".join(statements) if statements else "-- No grantable privileges for these features.") + "\n"
