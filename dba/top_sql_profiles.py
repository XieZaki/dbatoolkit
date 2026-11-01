"""Version-aware SQL Server cached Top SQL profiles."""

from __future__ import annotations

from typing import Any

from dba.core import DatabaseType
from dba.versioning import ProfileBinding

SUPPORTED_FAMILIES = frozenset({"2008r2", "2012", "2014", "2016", "2017", "2019", "2022"})
ALL_FAMILIES = SUPPORTED_FAMILIES | {"2025"}
SQL_PROFILE_ID = "sqlserver_top_sql_shared"
PARSER_CONTRACT_ID = "sqlserver_top_sql_v1"
POSTGRESQL_SUPPORTED_FAMILIES = frozenset({"14", "15", "16", "17", "18"})
POSTGRESQL_SQL_PROFILE_ID = "postgresql_pg_stat_statements"
POSTGRESQL_PARSER_CONTRACT_ID = "postgresql_pg_stat_statements_v1"
MYSQL_SUPPORTED_FAMILIES = frozenset({"5.7", "8.0", "8.4"})
MYSQL_SQL_PROFILE_ID = "mysql_performance_schema_digest"
MYSQL_PARSER_CONTRACT_ID = "mysql_performance_schema_digest_v1"
ORACLE_SUPPORTED_FAMILIES = frozenset({"11gr2", "12c", "18c", "19c", "21c", "23ai", "26ai"})
ORACLE_SQL_PROFILE_ID = "oracle_v_sql_child_top_sql"
ORACLE_PARSER_CONTRACT_ID = "oracle_v_sql_child_top_sql_v1"


def validate_top_sql_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.feature != "top-sql":
        return ("Top SQL profile must use the top-sql feature route",)
    if binding.db_type == DatabaseType.POSTGRESQL:
        if binding.version_family not in POSTGRESQL_SUPPORTED_FAMILIES:
            errors.append(f"unknown PostgreSQL Top SQL family: {binding.version_family}")
        if (binding.sql_profile_id != POSTGRESQL_SQL_PROFILE_ID
                or binding.parser_contract_id != POSTGRESQL_PARSER_CONTRACT_ID):
            errors.append(f"PostgreSQL Top SQL profile reference mismatch: {binding.version_family}")
        return tuple(errors)
    if binding.db_type == DatabaseType.MYSQL:
        if binding.version_family not in MYSQL_SUPPORTED_FAMILIES:
            errors.append(f"unknown MySQL Top SQL family: {binding.version_family}")
        if (binding.sql_profile_id != MYSQL_SQL_PROFILE_ID
                or binding.parser_contract_id != MYSQL_PARSER_CONTRACT_ID):
            errors.append(f"MySQL Top SQL profile reference mismatch: {binding.version_family}")
        return tuple(errors)
    if binding.db_type == DatabaseType.ORACLE:
        expected_variants = {"default"} if binding.version_family != "12c" else {"12_1", "12_2_plus"}
        if binding.version_family not in ORACLE_SUPPORTED_FAMILIES:
            errors.append(f"unknown Oracle Top SQL family: {binding.version_family}")
        if binding.runtime_variant not in expected_variants:
            errors.append(f"unknown Oracle Top SQL runtime variant: {binding.runtime_variant}")
        if (binding.sql_profile_id != ORACLE_SQL_PROFILE_ID
                or binding.parser_contract_id != ORACLE_PARSER_CONTRACT_ID):
            errors.append(f"Oracle Top SQL profile reference mismatch: {binding.version_family}")
        return tuple(errors)
    if binding.db_type != DatabaseType.SQLSERVER:
        return ("Top SQL profile has no supported database route",)
    if binding.version_family not in ALL_FAMILIES:
        errors.append(f"unknown SQL Server Top SQL family: {binding.version_family}")
    if binding.version_family == "2025" and binding.support_state.value == "UNKNOWN":
        if binding.sql_profile_id or binding.parser_contract_id or binding.profile_family:
            errors.append("SQL Server Top SQL 2025 route must not bind an unverified profile")
        return tuple(errors)
    if binding.sql_profile_id != SQL_PROFILE_ID or binding.parser_contract_id != PARSER_CONTRACT_ID:
        errors.append(f"SQL Server Top SQL profile reference mismatch: {binding.version_family}")
    return tuple(errors)


def validate_top_sql_profiles(registry: Any, bindings: tuple[ProfileBinding, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    routes = [binding for binding in bindings
              if binding.feature == "top-sql" and binding.db_type == DatabaseType.SQLSERVER]
    actual_supported = {binding.version_family for binding in routes
                        if binding.support_state.value == "SUPPORTED"}
    if actual_supported != SUPPORTED_FAMILIES:
        errors.append("SQL Server Top SQL supported-version routes do not match approved families")
    if {binding.version_family for binding in routes} != ALL_FAMILIES:
        errors.append("SQL Server Top SQL must declare all target routes, including an unverified SQL Server 2025 route")
    for binding in routes:
        errors.extend(validate_top_sql_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        try:
            script = registry.get("top_sql", DatabaseType.SQLSERVER)
            registry.authorize(script, DatabaseType.SQLSERVER)
        except Exception:
            errors.append("SQL Server Top SQL Registry script is unavailable or invalid: top_sql")
            continue
        metadata = script.metadata
        for family in SUPPORTED_FAMILIES:
            if (metadata.feature != "top-sql" or family not in metadata.version_families
                    or "default" not in metadata.runtime_variants
                    or metadata.risk != "READ_ONLY" or not metadata.reviewed):
                errors.append(f"SQL Server Top SQL Registry applicability mismatch: {family}")
                break
    pg_routes = [binding for binding in bindings
                 if binding.feature == "top-sql" and binding.db_type == DatabaseType.POSTGRESQL]
    pg_supported = {binding.version_family for binding in pg_routes
                    if binding.support_state.value == "SUPPORTED"}
    if pg_supported != POSTGRESQL_SUPPORTED_FAMILIES:
        errors.append("PostgreSQL Top SQL routes must cover exactly PostgreSQL 14-18")
    if {binding.version_family for binding in pg_routes} != POSTGRESQL_SUPPORTED_FAMILIES:
        errors.append("PostgreSQL Top SQL must not add routes outside PostgreSQL 14-18")
    for binding in pg_routes:
        errors.extend(validate_top_sql_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        for name, expected_max_rows in (("top_sql", 101), ("top_sql_source", 2), ("top_sql_info", 2)):
            try:
                script = registry.get(name, DatabaseType.POSTGRESQL)
                registry.authorize(script, DatabaseType.POSTGRESQL)
            except Exception:
                errors.append(f"PostgreSQL Top SQL Registry script is unavailable or invalid: {name}")
                continue
            metadata = script.metadata
            if (metadata.feature != "top-sql" or binding.version_family not in metadata.version_families
                    or "default" not in metadata.runtime_variants or metadata.max_rows != expected_max_rows
                    or metadata.risk != "READ_ONLY" or not metadata.reviewed):
                errors.append(f"PostgreSQL Top SQL Registry applicability mismatch: {name}/{binding.version_family}")
    mysql_routes = [binding for binding in bindings
                    if binding.feature == "top-sql" and binding.db_type == DatabaseType.MYSQL]
    expected_mysql_routes = {("5.7", "default"), ("8.0", "pre_8022"),
                             ("8.0", "8022_plus"), ("8.4", "default")}
    actual_mysql_routes = {(binding.version_family, binding.runtime_variant) for binding in mysql_routes
                           if binding.support_state.value == "SUPPORTED"}
    if actual_mysql_routes != expected_mysql_routes:
        errors.append("MySQL Top SQL routes must cover 5.7, both 8.0 variants, and 8.4")
    if {(binding.version_family, binding.runtime_variant) for binding in mysql_routes} != expected_mysql_routes:
        errors.append("MySQL Top SQL must not add routes outside 5.7, 8.0, and 8.4")
    for binding in mysql_routes:
        errors.extend(validate_top_sql_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        for name, expected_max_rows in (("top_sql_source", 2), ("top_sql_info", 2),
                                        ("top_sql", 101)):
            try:
                script = registry.get(name, DatabaseType.MYSQL)
                registry.authorize(script, DatabaseType.MYSQL)
            except Exception:
                errors.append(f"MySQL Top SQL Registry script is unavailable or invalid: {name}")
                continue
            metadata = script.metadata
            if (metadata.feature != "top-sql" or binding.version_family not in metadata.version_families
                    or binding.runtime_variant not in metadata.runtime_variants
                    or metadata.max_rows != expected_max_rows or metadata.risk != "READ_ONLY"
                    or not metadata.reviewed):
                errors.append(f"MySQL Top SQL Registry applicability mismatch: {name}/{binding.version_family}")
    oracle_routes = [binding for binding in bindings
                     if binding.feature == "top-sql" and binding.db_type == DatabaseType.ORACLE]
    expected_oracle_routes = {
        ("11gr2", "default"), ("12c", "12_1"), ("12c", "12_2_plus"),
        *((family, "default") for family in ("18c", "19c", "21c", "23ai", "26ai")),
    }
    actual_oracle_routes = {(binding.version_family, binding.runtime_variant) for binding in oracle_routes
                            if binding.support_state.value == "SUPPORTED"}
    if actual_oracle_routes != expected_oracle_routes:
        errors.append("Oracle Top SQL routes must cover 11gR2, both 12c variants, 18c, 19c, 21c, 23ai and 26ai")
    if {(binding.version_family, binding.runtime_variant) for binding in oracle_routes} != expected_oracle_routes:
        errors.append("Oracle Top SQL must not add routes outside the approved Oracle families")
    for binding in oracle_routes:
        errors.extend(validate_top_sql_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        script_name = "top_sql_11g" if binding.version_family == "11gr2" else "top_sql_modern"
        expected_scripts = [(script_name, 101)]
        if binding.version_family != "11gr2":
            expected_scripts.append(("top_sql_container", 2))
        for name, expected_max_rows in expected_scripts:
            try:
                script = registry.get(name, DatabaseType.ORACLE)
                registry.authorize(script, DatabaseType.ORACLE)
            except Exception:
                errors.append(f"Oracle Top SQL Registry script is unavailable or invalid: {name}")
                continue
            metadata = script.metadata
            if (metadata.feature != "top-sql" or binding.version_family not in metadata.version_families
                    or binding.runtime_variant not in metadata.runtime_variants
                    or metadata.max_rows != expected_max_rows or metadata.risk != "READ_ONLY"
                    or not metadata.reviewed):
                errors.append(f"Oracle Top SQL Registry applicability mismatch: {name}/{binding.version_family}")
    return tuple(errors)
