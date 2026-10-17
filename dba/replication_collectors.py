"""Version-profile-routed, Registry-backed replication snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.errors import ToolkitError
from dba.permissions import requirements_for_profile
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus


@dataclass(frozen=True)
class ReplicationEvidence:
    database_type: DatabaseType
    version: str | None
    samples: tuple[CollectorResult, ...]
    profile: ResolvedDiagnosticProfile
    required_collectors: tuple[str, ...] = ()
    supported: bool = True
    unsupported_reason: str | None = None


# Each executable route is a fixed bundle of reviewed Registry script names.
# The Resolver chooses exactly one bundle; this module never parses versions.
REPLICATION_SQL_PROFILES: dict[str, tuple[str, ...]] = {
    "mysql57_replication": ("replication_server_info", "replication_status_legacy"),
    "mysql80_pre_8022_replication": ("replication_server_info", "replication_status_legacy"),
    "mysql80_8022_plus_replication": ("replication_server_info", "replication_status"),
    "mysql84_replication": ("replication_server_info", "replication_status"),
    "pg13_18_replication": (
        "replication_node", "replication_members", "replication_receiver",
        "replication_slots", "replication_archiver",
    ),
    "sqlserver_ag_pre2022": ("replication_ag",),
    "sqlserver_ag_2022": ("replication_ag",),
    "oracle11gr2_replication": (
        "replication_database", "replication_metrics", "replication_processes",
        "replication_destinations",
    ),
    "oracle12c_121_replication": (
        "replication_database", "replication_metrics", "replication_processes",
        "replication_destinations",
    ),
    "oracle12c_122_replication": (
        "replication_database", "replication_metrics", "replication_processes_modern",
        "replication_destinations",
    ),
    "oracle_modern_replication": (
        "replication_database", "replication_metrics", "replication_processes_modern",
        "replication_destinations",
    ),
}


@dataclass(frozen=True)
class ReplicationParserContract:
    required_fields: tuple[str, ...]
    aliases: tuple[tuple[str, tuple[str, ...]], ...]


REPLICATION_PARSER_CONTRACTS: dict[str, ReplicationParserContract] = {
    "mysql57_replication": ReplicationParserContract(
        ("io_running", "sql_running", "source_host", "source_port", "seconds_behind"),
        (("io_running", ("Slave_IO_Running",)), ("sql_running", ("Slave_SQL_Running",)),
         ("source_host", ("Master_Host",)), ("source_port", ("Master_Port",)),
         ("seconds_behind", ("Seconds_Behind_Master",)),
         ("last_io_error", ("Last_IO_Error",)), ("last_sql_error", ("Last_SQL_Error",)),
         ("retrieved_gtid_set", ("Retrieved_Gtid_Set",)), ("executed_gtid_set", ("Executed_Gtid_Set",)))),
    "mysql80_pre_8022_replication": ReplicationParserContract(
        ("io_running", "sql_running", "source_host", "source_port", "seconds_behind"),
        (("io_running", ("Slave_IO_Running",)), ("sql_running", ("Slave_SQL_Running",)),
         ("source_host", ("Master_Host",)), ("source_port", ("Master_Port",)),
         ("seconds_behind", ("Seconds_Behind_Master",)),
         ("last_io_error", ("Last_IO_Error",)), ("last_sql_error", ("Last_SQL_Error",)),
         ("retrieved_gtid_set", ("Retrieved_Gtid_Set",)), ("executed_gtid_set", ("Executed_Gtid_Set",)))),
    "mysql80_8022_plus_replication": ReplicationParserContract(
        ("io_running", "sql_running", "source_host", "source_port", "seconds_behind"),
        (("io_running", ("Replica_IO_Running",)), ("sql_running", ("Replica_SQL_Running",)),
         ("source_host", ("Source_Host",)), ("source_port", ("Source_Port",)),
         ("seconds_behind", ("Seconds_Behind_Source",)),
         ("last_io_error", ("Last_IO_Error",)), ("last_sql_error", ("Last_SQL_Error",)),
         ("retrieved_gtid_set", ("Retrieved_Gtid_Set",)), ("executed_gtid_set", ("Executed_Gtid_Set",)))),
    "mysql84_replication": ReplicationParserContract(
        ("io_running", "sql_running", "source_host", "source_port", "seconds_behind"),
        (("io_running", ("Replica_IO_Running",)), ("sql_running", ("Replica_SQL_Running",)),
         ("source_host", ("Source_Host",)), ("source_port", ("Source_Port",)),
         ("seconds_behind", ("Seconds_Behind_Source",)),
         ("last_io_error", ("Last_IO_Error",)), ("last_sql_error", ("Last_SQL_Error",)),
         ("retrieved_gtid_set", ("Retrieved_Gtid_Set",)), ("executed_gtid_set", ("Executed_Gtid_Set",)))),
    "pg13_18_replication": ReplicationParserContract(
        ("in_recovery",), (("in_recovery", ("in_recovery",)),)),
    "sqlserver_ag_shared": ReplicationParserContract(
        ("role_desc", "replica_server_name"),
        (("role_desc", ("role_desc",)), ("replica_server_name", ("replica_server_name",)))),
    "oracle11gr2_replication": ReplicationParserContract(
        ("process_name", "process_status"),
        (("process_name", ("process",)), ("process_status", ("status",)))),
    "oracle12c_121_replication": ReplicationParserContract(
        ("process_name", "process_status"),
        (("process_name", ("process",)), ("process_status", ("status",)))),
    "oracle12c_122_replication": ReplicationParserContract(
        ("process_name", "process_status"),
        (("process_name", ("name",)), ("process_status", ("action",)))),
    "oracle_modern_replication": ReplicationParserContract(
        ("process_name", "process_status"),
        (("process_name", ("name",)), ("process_status", ("action",)))),
}


def validate_replication_profiles(registry: Any, bindings: tuple[Any, ...]) -> tuple[str, ...]:
    """Check every executable replication route binds valid reviewed SQL assets."""
    errors: list[str] = []
    for binding in bindings:
        if binding.feature != "replication" or binding.support_state not in {
            SupportState.SUPPORTED, SupportState.PARTIAL,
        }:
            continue
        names = REPLICATION_SQL_PROFILES.get(binding.sql_profile_id)
        if not names:
            errors.append(f"{binding.db_type.value}/{binding.version_family}: unknown SQL profile")
            continue
        contract = REPLICATION_PARSER_CONTRACTS.get(binding.parser_contract_id)
        if contract is None or any(
            not dict(contract.aliases).get(field) for field in contract.required_fields
        ):
            errors.append(f"{binding.db_type.value}/{binding.version_family}: invalid parser contract")
        try:
            permission = requirements_for_profile(binding.permission_profile_id)
        except ValueError:
            errors.append(f"{binding.db_type.value}/{binding.version_family}: missing permission profile")
        else:
            if tuple(permission.required) != binding.required_privileges:
                errors.append(f"{binding.db_type.value}/{binding.version_family}: permission contract mismatch")
        for name in names:
            try:
                script = registry.get(name, binding.db_type)
                registry.authorize(script, binding.db_type)
            except Exception:
                errors.append(f"{binding.db_type.value}/{binding.version_family}: missing or invalid SQL {name}")
                continue
            metadata = script.metadata
            if getattr(metadata, "feature", None) != "replication":
                errors.append(f"SQL {name} is missing replication feature metadata")
            families = getattr(metadata, "version_families", ())
            if binding.version_family not in families:
                errors.append(f"SQL {name} does not declare version family {binding.version_family}")
            variants = getattr(metadata, "runtime_variants", ())
            if binding.runtime_variant not in variants:
                errors.append(f"SQL {name} does not declare runtime variant {binding.runtime_variant}")
            if metadata.risk != "READ_ONLY" or not metadata.reviewed:
                errors.append(f"SQL {name} is not reviewed read-only SQL")
    return tuple(errors)


def collect_replication_evidence(adapter: Any, profile: ResolvedDiagnosticProfile) -> ReplicationEvidence:
    """Execute the one atomic profile's reviewed script bundle on its target only."""
    database_type = adapter.instance.db_type
    detected_version = profile.detected_raw_version
    if profile.version_status in {
        VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN, VersionStatus.MISMATCH,
        VersionStatus.AUTO_DETECTED,
    }:
        raise ToolkitError(
            "VersionProfileUnavailable",
            "Replication SQL was not run because a declared and verified version family is required "
            f"(version status: {profile.version_status.value}).",
        )
    if profile.support_state == SupportState.UNSUPPORTED:
        return ReplicationEvidence(database_type, detected_version, (), profile, (), False,
                                   "Replication is unsupported for this database family.")
    if profile.support_state != SupportState.SUPPORTED or not profile.sql_profile_id:
        raise ToolkitError("VersionProfileUnavailable",
                           "No verified replication profile is available for this database version.")
    script_names = REPLICATION_SQL_PROFILES.get(profile.sql_profile_id)
    if not script_names:
        raise ToolkitError("ProfileRegistryInvalid", "Resolved replication SQL profile is not registered.")
    samples: list[CollectorResult] = []
    for name in script_names:
        script = adapter.registry.get(name, database_type)
        adapter.registry.authorize(script, database_type)
        samples.append(adapter.execute_readonly(script))
    required = script_names
    return ReplicationEvidence(database_type, detected_version, tuple(samples), profile, required)
