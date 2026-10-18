"""Atomic version-aware SQL and parser contracts for slow diagnosis."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.errors import ToolkitError
from dba.permissions import PermissionStatus, assess_permissions, requirements_for_profile
from dba.versioning import (
    CANONICAL_VERSION_FAMILIES,
    ProfileBinding,
    ResolvedDiagnosticProfile,
    SupportState,
    VersionStatus,
)


@dataclass(frozen=True)
class SlowParserContract:
    required_fields: tuple[str, ...]
    aliases: tuple[tuple[str, tuple[str, ...]], ...]
    optional_fields: tuple[str, ...] = ()


_REQUIRED_COMMON_FIELDS = ("session_id",)
_OPTIONAL_COMMON_FIELDS = (
    "database", "username", "client_host", "client_ip", "application", "status",
    "running_seconds", "transaction_seconds", "wait_type", "wait_event", "sql_text",
    "blocking_session_id", "sql_identifier",
)


SLOW_PARSER_CONTRACTS: dict[str, SlowParserContract] = {
    "sqlserver_slow": SlowParserContract(_REQUIRED_COMMON_FIELDS, (
        ("session_id", ("session_id",)), ("database", ("database_name",)),
        ("username", ("login_name",)), ("client_host", ("host_name",)),
        ("client_ip", ("client_net_address",)), ("application", ("program_name",)),
        ("status", ("status",)), ("running_seconds", ("elapsed_ms",)),
        ("transaction_seconds", ("transaction_age_seconds",)),
        ("wait_type", ("wait_type",)), ("wait_event", ("wait_type",)),
        ("blocking_session_id", ("blocking_session_id",)),
        ("sql_text", ("sql_text",)), ("sql_identifier", ("sql_handle",)),
    ), _OPTIONAL_COMMON_FIELDS),
    "mysql_slow": SlowParserContract(_REQUIRED_COMMON_FIELDS, (
        ("session_id", ("processlist_id",)), ("database", ("database_name",)),
        ("username", ("user_name", "processlist_user")), ("client_host", ("host_raw",)),
        ("client_ip", ("host_ip",)), ("application", ("program_name",)),
        ("status", ("command_name", "state_name")),
        ("running_seconds", ("duration_ms", "time_seconds")),
        ("transaction_seconds", ("transaction_age_seconds",)),
        ("wait_type", ("current_wait",)), ("wait_event", ("current_event", "current_wait")),
        ("blocking_session_id", ("blocker_pid",)), ("sql_text", ("sql_text", "trx_query")),
        ("sql_identifier", ("thread_id",)),
    ), _OPTIONAL_COMMON_FIELDS),
    "postgresql_slow": SlowParserContract(_REQUIRED_COMMON_FIELDS, (
        ("session_id", ("pid",)), ("database", ("datname",)),
        ("username", ("usename",)), ("client_host", ("client_hostname",)),
        ("client_ip", ("client_addr",)), ("application", ("application_name",)),
        ("status", ("state",)), ("running_seconds", ("duration_ms",)),
        ("transaction_seconds", ("transaction_age_seconds",)),
        ("wait_type", ("wait_event_type",)), ("wait_event", ("wait_event",)),
        ("blocking_session_id", ("blocker_pid",)), ("sql_text", ("query",)),
        ("sql_identifier", ()),
    ), _OPTIONAL_COMMON_FIELDS),
    "oracle_slow": SlowParserContract(_REQUIRED_COMMON_FIELDS, (
        ("session_id", ("sid",)), ("database", ("database_name",)),
        ("username", ("username",)), ("client_host", ("machine",)),
        ("client_ip", ()), ("application", ("program", "module")),
        ("status", ("status",)), ("running_seconds", ("duration_seconds", "last_call_et")),
        ("transaction_seconds", ("transaction_age_seconds",)),
        ("wait_type", ("wait_class",)), ("wait_event", ("event",)),
        ("blocking_session_id", ("blocking_session",)),
        ("sql_text", ("sql_text", "previous_sql_text")), ("sql_identifier", ("sql_id",)),
    ), _OPTIONAL_COMMON_FIELDS),
}


# Values are fixed reviewed Registry script names. Adapter capability fallbacks
# may choose only among the profile's already-approved statement/blocking slots.
SLOW_SQL_PROFILES: dict[str, dict[str, str]] = {
    "sqlserver_2008r2_slow": {
        "active_sessions": "active_sessions_legacy", "active_requests": "active_requests",
        "transactions": "transactions", "connections": "connections",
    },
    "sqlserver_shared_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "connections": "connections",
    },
    "mysql57_slow": {
        "active_sessions": "active_sessions_legacy", "active_requests": "active_requests_legacy",
        "transactions": "transactions", "blocking": "blocking_legacy",
        "recent_statements": "recent_statements",
    },
    "mysql80_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking",
        "recent_statements": "recent_statements",
    },
    "mysql84_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking",
        "recent_statements": "recent_statements",
    },
    "pg13_18_slow": {
        "active_sessions": "active_sessions", "long_running": "long_running",
        "transactions": "transactions", "blocking": "blocking",
    },
    "oracle11gr2_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking_basic",
        "previous_sql": "previous_sql",
    },
    "oracle12c_121_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking_basic",
        "previous_sql": "previous_sql",
    },
    "oracle12c_122_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking_basic",
        "previous_sql": "previous_sql",
    },
    "oracle_modern_slow": {
        "active_sessions": "active_sessions", "active_requests": "active_requests",
        "transactions": "transactions", "blocking": "blocking_basic",
        "previous_sql": "previous_sql",
    },
}


SLOW_OPTIONAL_COLLECTORS: dict[str, tuple[str, ...]] = {
    "sqlserver_2008r2_slow": ("transactions", "connections"),
    "sqlserver_shared_slow": ("transactions", "connections"),
    "mysql57_slow": ("transactions", "blocking", "recent_statements"),
    "mysql80_slow": ("transactions", "blocking", "recent_statements"),
    "mysql84_slow": ("transactions", "blocking", "recent_statements"),
    "pg13_18_slow": ("long_running", "transactions", "blocking"),
    "oracle11gr2_slow": ("transactions", "blocking", "previous_sql"),
    "oracle12c_121_slow": ("transactions", "blocking", "previous_sql"),
    "oracle12c_122_slow": ("transactions", "blocking", "previous_sql"),
    "oracle_modern_slow": ("transactions", "blocking", "previous_sql"),
}

SLOW_REQUIRED_COLLECTORS: dict[str, tuple[str, ...]] = {
    "sqlserver_2008r2_slow": ("active_sessions", "active_requests"),
    "sqlserver_shared_slow": ("active_sessions", "active_requests"),
    "mysql57_slow": ("active_sessions", "active_requests"),
    "mysql80_slow": ("active_sessions", "active_requests"),
    "mysql84_slow": ("active_sessions", "active_requests"),
    "pg13_18_slow": ("active_sessions",),
    "oracle11gr2_slow": ("active_sessions", "active_requests"),
    "oracle12c_121_slow": ("active_sessions", "active_requests"),
    "oracle12c_122_slow": ("active_sessions", "active_requests"),
    "oracle_modern_slow": ("active_sessions", "active_requests"),
}

# Small synchronous first-pass evidence bundle. These are existing reviewed
# Registry-backed collectors; the remaining mapped collectors are supplemental.
SLOW_EMERGENCY_COLLECTORS: dict[str, tuple[str, ...]] = {
    "sqlserver_2008r2_slow": ("active_sessions", "active_requests", "transactions"),
    "sqlserver_shared_slow": ("active_sessions", "active_requests", "transactions"),
    "mysql57_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "mysql80_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "mysql84_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "pg13_18_slow": ("active_sessions", "transactions", "blocking"),
    "oracle11gr2_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "oracle12c_121_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "oracle12c_122_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
    "oracle_modern_slow": ("active_sessions", "active_requests", "transactions", "blocking"),
}


def validate_slow_binding(binding: ProfileBinding) -> tuple[str, ...]:
    errors: list[str] = []
    if binding.sql_profile_id not in SLOW_SQL_PROFILES:
        errors.append(f"unknown slow SQL profile: {binding.sql_profile_id}")
    if binding.parser_contract_id not in SLOW_PARSER_CONTRACTS:
        errors.append(f"unknown slow parser contract: {binding.parser_contract_id}")
    if binding.sql_profile_id not in SLOW_REQUIRED_COLLECTORS:
        errors.append(f"missing required collector declaration: {binding.sql_profile_id}")
    return tuple(errors)


def validate_slow_profiles(registry: Any, bindings: tuple[Any, ...]) -> tuple[str, ...]:
    """Verify slow route bundles, required permissions and parser contracts."""
    errors: list[str] = []
    routed = [binding for binding in bindings if binding.feature == "slow"]
    expected_routes = {
        (db_type, family, variant)
        for db_type, families in CANONICAL_VERSION_FAMILIES.items()
        for family in families
        for variant in (
            ("pre_8022", "8022_plus") if db_type == DatabaseType.MYSQL and family == "8.0"
            else ("12_1", "12_2_plus") if db_type == DatabaseType.ORACLE and family == "12c"
            else ("default",)
        )
    }
    actual_routes = {(item.db_type, item.version_family, item.runtime_variant) for item in routed}
    for db_type, family, variant in sorted(expected_routes - actual_routes,
                                           key=lambda value: (value[0].value, value[1], value[2])):
        errors.append(f"missing slow profile route: {db_type.value}/{family}/{variant}")
    for binding in bindings:
        if binding.feature != "slow" or binding.support_state not in {
            SupportState.SUPPORTED, SupportState.PARTIAL,
        }:
            continue
        scripts = SLOW_SQL_PROFILES.get(binding.sql_profile_id)
        contract = SLOW_PARSER_CONTRACTS.get(binding.parser_contract_id)
        try:
            requirement = requirements_for_profile(binding.permission_profile_id)
        except ValueError:
            requirement = None
        if not scripts:
            errors.append(f"{binding.db_type.value}/{binding.version_family}: unknown slow SQL profile")
            continue
        required = set(SLOW_REQUIRED_COLLECTORS.get(binding.sql_profile_id, ()))
        optional = set(SLOW_OPTIONAL_COLLECTORS.get(binding.sql_profile_id, ()))
        if not required or required & optional:
            errors.append(f"{binding.db_type.value}/{binding.version_family}: invalid required/optional collector contract")
        if not (required | optional) <= set(scripts):
            errors.append(f"{binding.db_type.value}/{binding.version_family}: collector contract references unmapped SQL")
        emergency = set(SLOW_EMERGENCY_COLLECTORS.get(binding.sql_profile_id, ()))
        if not required <= emergency or not emergency <= set(scripts):
            errors.append(f"{binding.db_type.value}/{binding.version_family}: invalid emergency collector contract")
        if contract is None or any(not dict(contract.aliases).get(field)
                                   for field in contract.required_fields):
            errors.append(f"{binding.db_type.value}/{binding.version_family}: invalid slow parser contract")
        if requirement is None or tuple(requirement.required) != binding.required_privileges:
            errors.append(f"{binding.db_type.value}/{binding.version_family}: slow permission contract mismatch")
        for name in scripts.values():
            try:
                script = registry.get(name, binding.db_type)
                registry.authorize(script, binding.db_type)
            except Exception:
                errors.append(f"{binding.db_type.value}/{binding.version_family}: missing or invalid slow SQL {name}")
                continue
            metadata = script.metadata
            if metadata.feature != "slow":
                errors.append(f"SQL {name} is missing slow feature metadata")
            if binding.version_family not in metadata.version_families:
                errors.append(f"SQL {name} does not declare slow version family {binding.version_family}")
            if binding.runtime_variant not in metadata.runtime_variants:
                errors.append(f"SQL {name} does not declare slow runtime variant {binding.runtime_variant}")
            if metadata.risk != "READ_ONLY" or not metadata.reviewed:
                errors.append(f"SQL {name} is not reviewed read-only slow SQL")
            if metadata.max_rows == 1 and not any(
                marker in script.text.upper() for marker in ("TOP (1)", "TOP 1", "LIMIT 1", "ROWNUM = 1")
            ):
                errors.append(f"SQL {name} uses max_rows=1 without a SQL-side single-row limit")
    return tuple(errors)


def profile_for_adapter(adapter: Any) -> ResolvedDiagnosticProfile | None:
    profile = getattr(adapter, "slow_profile", None)
    return profile if isinstance(profile, ResolvedDiagnosticProfile) and profile.feature == "slow" else None


def required_collectors_for_adapter(adapter: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    profile = profile_for_adapter(adapter)
    if profile is None:
        return fallback
    return SLOW_REQUIRED_COLLECTORS.get(profile.sql_profile_id or "", fallback)


def optional_collectors_for_adapter(adapter: Any) -> tuple[str, ...]:
    profile = profile_for_adapter(adapter)
    if profile is None:
        return ()
    return SLOW_OPTIONAL_COLLECTORS.get(profile.sql_profile_id or "", ())


def emergency_collectors_for_adapter(adapter: Any) -> tuple[str, ...]:
    profile = profile_for_adapter(adapter)
    if profile is None:
        return ("active_sessions", "active_requests", "transactions", "blocking")
    return SLOW_EMERGENCY_COLLECTORS.get(profile.sql_profile_id or "", ())


def collection_completeness(
    results: tuple[CollectorResult, ...], required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> tuple[bool, str]:
    by_name = {result.collector_name: result for result in results}
    incomplete = any(
        name not in by_name or not by_name[name].success or by_name[name].truncated
        for name in required
    )
    optional_limited = any(
        name not in by_name or not by_name[name].success or by_name[name].truncated
        for name in optional
    )
    if incomplete:
        return True, "PARTIAL"
    if optional_limited:
        return False, "PARTIAL"
    return False, "FULL"


def script_for_slow(adapter: Any, collector_name: str, fallback: str) -> str:
    profile = profile_for_adapter(adapter)
    if profile is None or profile.support_state != SupportState.SUPPORTED:
        return fallback
    scripts = SLOW_SQL_PROFILES.get(profile.sql_profile_id or "")
    if scripts is None or collector_name not in scripts:
        raise ToolkitError("ProfileRegistryInvalid", "Resolved slow profile has no collector SQL mapping.")
    name = scripts[collector_name]
    if collector_name == "active_requests" and adapter.instance.db_type == DatabaseType.MYSQL:
        capability_script = getattr(adapter, "statement_script", None)
        if name == "active_requests" and capability_script in {
            "active_requests", "active_requests_basic", "active_requests_no_waits",
        }:
            name = capability_script
    if collector_name == "blocking" and adapter.instance.db_type == DatabaseType.ORACLE:
        capability_script = getattr(adapter, "blocking_script", None)
        if name == "blocking_basic" and capability_script in {"blocking", "blocking_basic"}:
            name = capability_script
    return name


def normalize_slow_result(
    profile: ResolvedDiagnosticProfile, result: CollectorResult,
) -> CollectorResult:
    contract = SLOW_PARSER_CONTRACTS.get(profile.parser_contract_id or "")
    if contract is None:
        raise ToolkitError("ProfileRegistryInvalid", "Resolved slow parser contract is not registered.")
    aliases = dict(contract.aliases)
    rows: list[dict[str, Any]] = []
    for row in result.rows:
        normalized: dict[str, Any] = {}
        for field in (*contract.required_fields, *contract.optional_fields):
            value = next((row.get(key) for key in aliases[field] if row.get(key) is not None), None)
            if field == "running_seconds" and isinstance(value, (int, float)):
                source = next((key for key in aliases[field] if row.get(key) is not None), "")
                if source.endswith("_ms"):
                    value = value / 1000
            normalized[field] = value
        rows.append({**row, "normalized": normalized})
    return replace(result, rows=tuple(rows))


def profile_permission_status(profile: ResolvedDiagnosticProfile, observed: set[str]) -> Any:
    requirement = requirements_for_profile(profile.permission_profile_id or "")
    if profile.version_status in {VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN,
                                  VersionStatus.MISMATCH, VersionStatus.AUTO_DETECTED}:
        return {"status": PermissionStatus.UNKNOWN, "missing_required": (),
                "missing_optional": (), "available": ()}
    return assess_permissions(requirement, observed)
