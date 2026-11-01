"""Version-aware SQL Server memory grant Registry contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dba.core import DatabaseType

if TYPE_CHECKING:
    from dba.registry import SqlScriptRegistry
    from dba.versioning import ProfileBinding

SQL_PROFILE_ID = "sqlserver_memory_grant_v1"
PARSER_CONTRACT_ID = "sqlserver_memory_grant_v1"
SUPPORTED_FAMILIES = frozenset({"2008r2", "2012", "2014", "2016", "2017", "2019", "2022"})
ALL_FAMILIES = SUPPORTED_FAMILIES | {"2025"}


def validate_memory_grant_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.db_type != DatabaseType.SQLSERVER or binding.feature != "memory-grant":
        return ("Memory Grant profile must be a SQL Server memory-grant route",)
    if binding.version_family not in ALL_FAMILIES:
        errors.append(f"unknown SQL Server Memory Grant family: {binding.version_family}")
    if binding.version_family == "2025" and binding.support_state.value == "UNKNOWN":
        if binding.sql_profile_id or binding.parser_contract_id or binding.profile_family:
            errors.append("SQL Server Memory Grant 2025 route must not bind an unverified profile")
        return tuple(errors)
    if (binding.sql_profile_id != SQL_PROFILE_ID
            or binding.parser_contract_id != PARSER_CONTRACT_ID
            or binding.profile_family != SQL_PROFILE_ID):
        errors.append(f"SQL Server Memory Grant profile reference mismatch: {binding.version_family}")
    if binding.version_family == "2025" and binding.support_state.value != "UNKNOWN":
        errors.append("SQL Server Memory Grant 2025 route must remain unverified")
    return tuple(errors)


def validate_memory_grant_profiles(registry: SqlScriptRegistry,
                                   bindings: tuple[ProfileBinding, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    routes = [binding for binding in bindings if binding.feature == "memory-grant"]
    if {binding.version_family for binding in routes} != ALL_FAMILIES:
        errors.append("SQL Server Memory Grant must retain explicit reviewed families and an unverified 2025 route")
    actual_supported = {binding.version_family for binding in routes
                        if binding.support_state.value == "SUPPORTED"}
    if actual_supported != SUPPORTED_FAMILIES:
        errors.append("SQL Server Memory Grant supported-version routes do not match reviewed families")
    for binding in routes:
        errors.extend(validate_memory_grant_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        for name in ("memory_grant_waiting", "memory_grant_granted"):
            try:
                script = registry.get(name, DatabaseType.SQLSERVER)
                registry.authorize(script, DatabaseType.SQLSERVER)
            except Exception:
                errors.append(f"SQL Server Memory Grant Registry script is unavailable or invalid: {name}")
                continue
            metadata = script.metadata
            if (metadata.feature != "memory-grant"
                    or binding.version_family not in metadata.version_families
                    or binding.runtime_variant not in metadata.runtime_variants
                    or metadata.risk != "READ_ONLY" or not metadata.reviewed
                    or metadata.timeout_seconds != 5 or metadata.max_rows != 101):
                errors.append(f"SQL Server Memory Grant Registry applicability mismatch: {name}")
    return tuple(dict.fromkeys(errors))
