"""Version-aware Oracle cached execution-plan routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.core import DatabaseType
from dba.versioning import ProfileBinding

ORACLE_PLAN_SUPPORTED_FAMILIES = frozenset({
    "11gr2", "12c", "18c", "19c", "21c", "23ai", "26ai",
})
ORACLE_PLAN_PROFILE_ID = "oracle_cached_plan_v1"
ORACLE_PLAN_PARSER_CONTRACT_ID = "oracle_cached_plan_rows_v1"
MAX_PLAN_OPERATORS = 500
PLAN_QUERY_ROWS = MAX_PLAN_OPERATORS + 1


@dataclass(frozen=True)
class OraclePlanProfile:
    profile_id: str
    identity_script: str
    context_script: str | None
    rows_script: str
    parser_contract_id: str


@dataclass(frozen=True)
class OraclePlanParserContract:
    contract_id: str
    max_operators: int


ORACLE_PLAN_11G = OraclePlanProfile(
    ORACLE_PLAN_PROFILE_ID,
    "oracle_plan_identity_11g",
    None,
    "oracle_plan_rows_11g",
    ORACLE_PLAN_PARSER_CONTRACT_ID,
)
ORACLE_PLAN_MODERN = OraclePlanProfile(
    ORACLE_PLAN_PROFILE_ID,
    "oracle_plan_identity_modern",
    "oracle_plan_context_modern",
    "oracle_plan_rows_modern",
    ORACLE_PLAN_PARSER_CONTRACT_ID,
)
ORACLE_PLAN_PROFILES = {ORACLE_PLAN_PROFILE_ID: ORACLE_PLAN_11G}
ORACLE_PLAN_CONTRACTS = {
    ORACLE_PLAN_PARSER_CONTRACT_ID: OraclePlanParserContract(
        ORACLE_PLAN_PARSER_CONTRACT_ID, MAX_PLAN_OPERATORS,
    ),
}


def oracle_plan_profile(profile_id: str, family: str) -> OraclePlanProfile:
    if profile_id != ORACLE_PLAN_PROFILE_ID or family not in ORACLE_PLAN_SUPPORTED_FAMILIES:
        raise ValueError("Resolved profile has no supported Oracle cached-plan definition")
    return ORACLE_PLAN_11G if family == "11gr2" else ORACLE_PLAN_MODERN


def oracle_plan_contract(contract_id: str) -> OraclePlanParserContract:
    try:
        return ORACLE_PLAN_CONTRACTS[contract_id]
    except KeyError:
        raise ValueError("Resolved profile has no Oracle plan contract") from None


def validate_oracle_plan_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.db_type != DatabaseType.ORACLE or binding.feature != "plan":
        errors.append("Oracle plan profile must be an Oracle plan route")
        return tuple(errors)
    if binding.version_family not in ORACLE_PLAN_SUPPORTED_FAMILIES:
        errors.append(f"unknown Oracle plan version family: {binding.version_family}")
    expected_variant = {"12_1", "12_2_plus"} if binding.version_family == "12c" else {"default"}
    if binding.runtime_variant not in expected_variant:
        errors.append(f"unknown Oracle plan runtime variant: {binding.runtime_variant}")
    if (binding.sql_profile_id != ORACLE_PLAN_PROFILE_ID
            or binding.parser_contract_id != ORACLE_PLAN_PARSER_CONTRACT_ID
            or binding.profile_family != ORACLE_PLAN_PROFILE_ID):
        errors.append(f"Oracle plan profile reference mismatch: {binding.version_family}/{binding.runtime_variant}")
    try:
        oracle_plan_contract(binding.parser_contract_id)
    except ValueError as exc:
        errors.append(str(exc))
    return tuple(errors)


def validate_oracle_plan_profiles(registry: Any, bindings: tuple[ProfileBinding, ...]) -> tuple[str, ...]:
    """Ensure every supported Oracle Plan route has matching reviewed SQL."""
    errors: list[str] = []
    routes = [binding for binding in bindings
              if binding.db_type == DatabaseType.ORACLE and binding.feature == "plan"]
    expected = {
        ("11gr2", "default"), ("12c", "12_1"), ("12c", "12_2_plus"),
        *((family, "default") for family in ("18c", "19c", "21c", "23ai", "26ai")),
    }
    actual = {(item.version_family, item.runtime_variant) for item in routes
              if item.support_state.value == "SUPPORTED"}
    if actual != expected:
        errors.append("Oracle Plan supported-version routes do not match the declared 11gR2-26ai families")
    for binding in routes:
        errors.extend(validate_oracle_plan_binding(binding))
        if binding.support_state.value != "SUPPORTED":
            continue
        try:
            profile = oracle_plan_profile(binding.sql_profile_id, binding.version_family)
            oracle_plan_contract(binding.parser_contract_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        scripts = [profile.identity_script, profile.rows_script]
        if profile.context_script:
            scripts.append(profile.context_script)
        for name in scripts:
            try:
                script = registry.get(name, DatabaseType.ORACLE)
                registry.authorize(script, DatabaseType.ORACLE)
            except Exception as exc:
                errors.append(f"Oracle Plan Registry script {name} is invalid: {type(exc).__name__}")
                continue
            metadata = script.metadata
            expected_rows = PLAN_QUERY_ROWS if name == profile.rows_script else 2
            if (metadata.feature != "plan" or binding.version_family not in metadata.version_families
                    or binding.runtime_variant not in metadata.runtime_variants
                    or metadata.risk != "READ_ONLY" or not metadata.reviewed
                    or metadata.max_rows != expected_rows):
                errors.append(f"Oracle Plan Registry metadata does not match {binding.version_family}: {name}")
    return tuple(errors)
