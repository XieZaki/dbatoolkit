"""Version-aware, atomic SQL and collection contracts for disk-growth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.permissions import requirements_for_profile


@dataclass(frozen=True)
class DiskGrowthSqlProfile:
    profile_id: str
    required: tuple[str, ...]
    optional: tuple[str, ...]

    @property
    def emergency(self) -> tuple[str, ...]:
        """Existing minimum sources useful for an initial space/retention triage."""
        priority = {"disk_tempdb", "disk_temp_usage", "disk_transactions", "transactions",
                    "disk_slots", "disk_slots_17_plus", "disk_archiver", "disk_binlog_config",
                    "disk_binlog_config_legacy", "disk_binlogs", "disk_fra", "disk_fra_usage",
                    "disk_archive", "disk_archive_volume", "disk_redo"}
        names = self.required + tuple(name for name in self.optional if name in priority)
        return tuple(dict.fromkeys(names))

    @property
    def ordered_scripts(self) -> tuple[str, ...]:
        return self.required + self.optional


_SQLSERVER_REQUIRED = ("disk_files", "disk_log")
_SQLSERVER_OPTIONAL = ("disk_tempdb", "disk_temp_usage", "disk_log_backup", "disk_ag", "disk_transactions")
_MYSQL_REQUIRED = ("disk_schemas",)
_MYSQL_OPTIONAL = ("disk_binlog_config", "disk_tables", "disk_tablespaces", "disk_binlogs", "disk_replica",
                   "disk_innodb", "disk_transactions")
_PG_REQUIRED = ("disk_database",)
_PG_OPTIONAL = ("disk_relations", "disk_slots", "disk_archiver", "disk_replicas",
                "disk_transactions")
_ORACLE_REQUIRED = ("disk_tablespaces", "disk_files", "disk_temp")
_ORACLE_OPTIONAL = ("disk_fra", "disk_fra_usage", "disk_archive", "disk_archive_volume", "disk_redo",
                    "disk_transactions")

DISK_GROWTH_SQL_PROFILES: dict[str, DiskGrowthSqlProfile] = {
    "sqlserver_disk_2008r2": DiskGrowthSqlProfile(
        "sqlserver_disk_2008r2", ("disk_files", "disk_log_legacy"),
        ("disk_tempdb", "disk_temp_usage", "disk_log_backup", "disk_transactions")),
    "sqlserver_disk_2012": DiskGrowthSqlProfile(
        "sqlserver_disk_2012", ("disk_files", "disk_log_legacy"),
        ("disk_tempdb", "disk_temp_usage", "disk_log_backup", "disk_ag", "disk_transactions")),
    "sqlserver_disk_2014_2019": DiskGrowthSqlProfile(
        "sqlserver_disk_2014_2019", _SQLSERVER_REQUIRED, _SQLSERVER_OPTIONAL),
    "sqlserver_disk_2022": DiskGrowthSqlProfile(
        "sqlserver_disk_2022", _SQLSERVER_REQUIRED, _SQLSERVER_OPTIONAL),
    "mysql_disk_57": DiskGrowthSqlProfile(
        "mysql_disk_57", _MYSQL_REQUIRED,
        ("disk_binlog_config_legacy", "disk_tables", "disk_tablespaces", "disk_binlogs",
         "disk_replica", "disk_innodb", "disk_transactions")),
    "mysql_disk_80": DiskGrowthSqlProfile("mysql_disk_80", _MYSQL_REQUIRED, _MYSQL_OPTIONAL),
    "mysql_disk_84": DiskGrowthSqlProfile("mysql_disk_84", _MYSQL_REQUIRED, _MYSQL_OPTIONAL),
    "postgresql_disk_13_16": DiskGrowthSqlProfile("postgresql_disk_13_16", _PG_REQUIRED, _PG_OPTIONAL),
    "postgresql_disk_17_18": DiskGrowthSqlProfile(
        "postgresql_disk_17_18", _PG_REQUIRED,
        ("disk_relations", "disk_slots_17_plus", "disk_archiver", "disk_replicas",
         "disk_transactions")),
    "oracle_disk_11gr2": DiskGrowthSqlProfile("oracle_disk_11gr2", _ORACLE_REQUIRED, _ORACLE_OPTIONAL),
    "oracle_disk_12c_121": DiskGrowthSqlProfile("oracle_disk_12c_121", _ORACLE_REQUIRED, _ORACLE_OPTIONAL),
    "oracle_disk_12c_122": DiskGrowthSqlProfile("oracle_disk_12c_122", _ORACLE_REQUIRED, _ORACLE_OPTIONAL),
    "oracle_disk_modern": DiskGrowthSqlProfile("oracle_disk_modern", _ORACLE_REQUIRED, _ORACLE_OPTIONAL),
}

DISK_GROWTH_PARSER_CONTRACTS = frozenset({"disk_growth_normalized_v1"})


def profile_for_disk(adapter: Any) -> Any | None:
    profile = getattr(adapter, "disk_growth_profile", None)
    if profile is not None and profile.feature == "disk-growth":
        return profile
    return None


def sql_profile_for_disk(profile_id: str) -> DiskGrowthSqlProfile:
    try:
        return DISK_GROWTH_SQL_PROFILES[profile_id]
    except KeyError:
        raise ValueError("Resolved profile has no disk-growth SQL contract") from None


def scripts_for_disk(profile_id: str) -> tuple[str, ...]:
    return sql_profile_for_disk(profile_id).ordered_scripts


def validate_disk_binding(binding: Any) -> tuple[str, ...]:
    errors: list[str] = []
    sql_profile = DISK_GROWTH_SQL_PROFILES.get(binding.sql_profile_id)
    if sql_profile is None:
        errors.append(f"missing disk-growth SQL profile: {binding.sql_profile_id}")
    elif (binding.sql_profile_id != binding.profile_family
          or binding.parser_contract_id not in DISK_GROWTH_PARSER_CONTRACTS):
        errors.append(f"invalid disk-growth parser/profile reference: {binding.version_family}")
    try:
        permissions = requirements_for_profile(binding.permission_profile_id)
    except ValueError:
        errors.append(f"missing disk-growth permission profile: {binding.permission_profile_id}")
    else:
        if tuple(permissions.required) != binding.required_privileges:
            errors.append(f"disk-growth permission contract mismatch: {binding.version_family}")
    if sql_profile is not None:
        if not sql_profile.required or set(sql_profile.required) & set(sql_profile.optional):
            errors.append(f"invalid disk-growth collector roles: {binding.sql_profile_id}")
        if not set(sql_profile.required) <= set(sql_profile.emergency) \
                or not set(sql_profile.emergency) <= set(sql_profile.required + sql_profile.optional):
            errors.append(f"invalid emergency disk-growth collector roles: {binding.sql_profile_id}")
    return tuple(errors)


def validate_disk_profiles(registry: Any, bindings: tuple[Any, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    supported = [item for item in bindings
                 if item.feature == "disk-growth" and item.support_state.value == "SUPPORTED"]
    for binding in supported:
        sql_profile = DISK_GROWTH_SQL_PROFILES.get(binding.sql_profile_id)
        if sql_profile is None:
            errors.append(f"missing disk-growth SQL profile {binding.sql_profile_id}")
            continue
        required = set(sql_profile.required)
        optional = set(sql_profile.optional)
        if required & optional:
            errors.append(f"overlapping required/optional disk-growth SQL: {binding.sql_profile_id}")
        for name, role in [(name, "required") for name in sql_profile.required] + [
                (name, "optional") for name in sql_profile.optional]:
            try:
                script = registry.get(name, binding.db_type)
                registry.authorize(script, binding.db_type)
            except Exception:
                errors.append(f"unavailable reviewed disk-growth SQL {binding.db_type.value}/{name}")
                continue
            metadata = script.metadata
            if metadata.feature != "disk-growth" or metadata.profile_role != role:
                errors.append(f"disk-growth SQL metadata role mismatch: {binding.db_type.value}/{name}")
            if metadata.version_families and binding.version_family not in metadata.version_families:
                errors.append(f"disk-growth SQL version applicability mismatch: {binding.db_type.value}/{name}/{binding.version_family}")
            if metadata.runtime_variants and binding.runtime_variant not in metadata.runtime_variants:
                errors.append(f"disk-growth SQL variant applicability mismatch: {binding.db_type.value}/{name}/{binding.runtime_variant}")
            if metadata.risk != "READ_ONLY" or not metadata.reviewed or metadata.max_rows < 2:
                errors.append(f"unsafe or insufficiently bounded disk-growth SQL: {binding.db_type.value}/{name}")
    for binding in bindings:
        if binding.feature == "disk-growth" and binding.support_state.value == "SUPPORTED":
            errors.extend(validate_disk_binding(binding))
    return tuple(dict.fromkeys(errors))
