"""Version-aware SQL Server execution-plan SQL and parser contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.core import DatabaseType
from dba.permissions import requirements_for_profile
from dba.versioning import ProfileBinding


@dataclass(frozen=True)
class ExecutionPlanProfile:
    profile_id: str
    request_script: str
    cached_script: str
    parser_contract_id: str


@dataclass(frozen=True)
class ExecutionPlanParserContract:
    contract_id: str
    max_xml_bytes: int
    max_elements: int
    max_operators: int


SQLSERVER_PLAN_PROFILE = ExecutionPlanProfile(
    "sqlserver_plan_shared", "plan_request", "plan_cached", "sqlserver_showplan_v1"
)

PLAN_PARSER_CONTRACTS: dict[str, ExecutionPlanParserContract] = {
    "sqlserver_showplan_v1": ExecutionPlanParserContract(
        "sqlserver_showplan_v1", 1_048_576, 50_000, 10_000,
    ),
}

PLAN_SQL_PROFILES: dict[str, ExecutionPlanProfile] = {
    SQLSERVER_PLAN_PROFILE.profile_id: SQLSERVER_PLAN_PROFILE,
}


def execution_plan_profile(profile_id: str) -> ExecutionPlanProfile:
    try:
        return PLAN_SQL_PROFILES[profile_id]
    except KeyError:
        raise ValueError("Resolved profile has no execution-plan SQL definition") from None


def parser_contract(contract_id: str) -> ExecutionPlanParserContract:
    try:
        return PLAN_PARSER_CONTRACTS[contract_id]
    except KeyError:
        raise ValueError("Resolved profile has no execution-plan parser contract") from None


def validate_plan_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.support_state.value != "SUPPORTED" and not any((
        binding.sql_profile_id, binding.permission_profile_id, binding.parser_contract_id
    )):
        return ()
    sql_profile = PLAN_SQL_PROFILES.get(binding.sql_profile_id)
    if binding.db_type != DatabaseType.SQLSERVER or binding.feature != "plan":
        errors.append("execution-plan profile must be a SQL Server plan route")
        return tuple(errors)
    if sql_profile is None:
        errors.append(f"unknown execution-plan SQL profile: {binding.sql_profile_id}")
    elif binding.profile_family != sql_profile.profile_id:
        errors.append(f"execution-plan profile family mismatch: {binding.sql_profile_id}")
    if binding.parser_contract_id not in PLAN_PARSER_CONTRACTS:
        errors.append(f"unknown execution-plan parser contract: {binding.parser_contract_id}")
    try:
        permissions = requirements_for_profile(binding.permission_profile_id)
    except ValueError:
        errors.append(f"missing execution-plan permission profile: {binding.permission_profile_id}")
    else:
        if permissions.required != binding.required_privileges:
            errors.append(f"execution-plan permission contract mismatch: {binding.version_family}")
    return tuple(errors)


def validate_plan_profiles(registry: Any, bindings: tuple[ProfileBinding, ...]) -> tuple[str, ...]:
    """Validate every supported Plan route against reviewed Registry metadata."""
    errors: list[str] = []
    plan_bindings = [binding for binding in bindings if binding.feature == "plan"]
    expected = {
        (DatabaseType.SQLSERVER, family)
        for family in ("2008r2", "2012", "2014", "2016", "2017", "2019", "2022")
    }
    actual = {(binding.db_type, binding.version_family) for binding in plan_bindings
              if binding.support_state.value == "SUPPORTED"}
    if actual != expected:
        errors.append("SQL Server Plan supported-version routes do not match the approved families")
    for binding in plan_bindings:
        errors.extend(validate_plan_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        try:
            profile = execution_plan_profile(binding.sql_profile_id)
            parser_contract(binding.parser_contract_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        for name in (profile.request_script, profile.cached_script):
            try:
                script = registry.get(name, DatabaseType.SQLSERVER)
                registry.authorize(script, DatabaseType.SQLSERVER)
            except Exception:
                errors.append(f"execution-plan Registry script is unavailable or invalid: {name}")
                continue
            metadata = script.metadata
            if (metadata.feature != "plan" or binding.version_family not in metadata.version_families
                    or binding.runtime_variant not in metadata.runtime_variants
                    or metadata.risk != "READ_ONLY" or not metadata.reviewed):
                errors.append(f"execution-plan Registry applicability mismatch: {name}/{binding.version_family}")
    return tuple(errors)
