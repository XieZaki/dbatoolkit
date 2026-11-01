"""Version-aware profile validation for SQL Server active-request rankings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dba.core import DatabaseType

if TYPE_CHECKING:
    from dba.versioning import ProfileBinding

SUPPORTED_FAMILIES = frozenset({"2008r2", "2012", "2014", "2016", "2017", "2019", "2022"})
ALL_FAMILIES = SUPPORTED_FAMILIES | {"2025"}
SQL_PROFILE_ID = "sqlserver_active_sql_v1"
PARSER_CONTRACT_ID = "sqlserver_active_sql_v1"


def validate_active_sql_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.db_type != DatabaseType.SQLSERVER or binding.feature != "active-sql":
        return ("Active SQL profile must be a SQL Server active-sql route",)
    if binding.version_family not in ALL_FAMILIES:
        errors.append(f"unknown SQL Server Active SQL family: {binding.version_family}")
    if binding.version_family == "2025" and binding.support_state.value == "UNKNOWN":
        if binding.sql_profile_id or binding.parser_contract_id or binding.profile_family:
            errors.append("SQL Server Active SQL 2025 route must not bind an unverified profile")
        return tuple(errors)
    if binding.sql_profile_id != SQL_PROFILE_ID or binding.parser_contract_id != PARSER_CONTRACT_ID:
        errors.append(f"SQL Server Active SQL profile reference mismatch: {binding.version_family}")
    return tuple(errors)


def validate_active_sql_profiles(registry: Any, bindings: tuple[ProfileBinding, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    routes = [binding for binding in bindings if binding.feature == "active-sql"]
    actual_supported = {binding.version_family for binding in routes
                        if binding.support_state.value == "SUPPORTED"}
    if actual_supported != SUPPORTED_FAMILIES:
        errors.append("SQL Server Active SQL supported-version routes do not match reviewed families")
    if {binding.version_family for binding in routes} != ALL_FAMILIES:
        errors.append("SQL Server Active SQL must retain an explicit unverified SQL Server 2025 route")
    for binding in routes:
        if binding.version_family == "2025" and binding.support_state.value != "UNKNOWN":
            errors.append("SQL Server Active SQL 2025 route must remain unverified")
        errors.extend(validate_active_sql_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        try:
            script = registry.get("active_sql", DatabaseType.SQLSERVER)
            registry.authorize(script, DatabaseType.SQLSERVER)
        except Exception:
            errors.append("SQL Server Active SQL Registry script is unavailable or invalid: active_sql")
            continue
        metadata = script.metadata
        if (metadata.feature != "active-sql" or binding.version_family not in metadata.version_families
                or binding.runtime_variant not in metadata.runtime_variants
                or metadata.risk != "READ_ONLY" or not metadata.reviewed
                or metadata.max_rows != 301):
            errors.append(f"SQL Server Active SQL Registry applicability mismatch: {binding.version_family}")
    return tuple(errors)
