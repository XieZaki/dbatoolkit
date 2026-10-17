from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.permissions import PermissionStatus, requirements_for_profile
from dba.registry import SqlScriptRegistry
from dba.slow_profiles import (
    SLOW_OPTIONAL_COLLECTORS,
    SLOW_PARSER_CONTRACTS,
    SLOW_REQUIRED_COLLECTORS,
    SLOW_SQL_PROFILES,
    collection_completeness,
    normalize_slow_result,
    profile_permission_status,
    script_for_slow,
    validate_slow_profiles,
)
from dba.versioning import (
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    SupportState,
    VersionStatus,
)

ROOT = Path(__file__).resolve().parents[1]


def test_all_slow_routes_bind_sql_permissions_parser_and_applicability() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert PROFILE_RESOLVER.validate() == ()
    assert validate_slow_profiles(registry, PROFILE_BINDINGS) == ()
    for binding in PROFILE_BINDINGS:
        if binding.feature != "slow" or binding.support_state != SupportState.SUPPORTED:
            continue
        assert binding.sql_profile_id in SLOW_SQL_PROFILES
        assert binding.parser_contract_id in SLOW_PARSER_CONTRACTS
        assert SLOW_REQUIRED_COLLECTORS[binding.sql_profile_id]
        assert binding.required_privileges == requirements_for_profile(
            binding.permission_profile_id
        ).required


def test_validate_fails_when_a_slow_family_route_is_missing() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    remaining = tuple(binding for binding in PROFILE_BINDINGS if not (
        binding.db_type == DatabaseType.ORACLE and binding.version_family == "11gr2"
        and binding.feature == "slow"
    ))
    errors = validate_slow_profiles(registry, remaining)
    assert any("missing slow profile route: oracle/11gr2/default" in error for error in errors)


def test_sqlserver_2008_and_mysql57_select_legacy_sql() -> None:
    cases = (
        (DatabaseType.SQLSERVER, "2008r2", "10.50.1", "active_sessions", "active_sessions_legacy"),
        (DatabaseType.SQLSERVER, "2022", "16.0.1", "active_sessions", "active_sessions"),
        (DatabaseType.MYSQL, "5.7", "5.7.44", "active_requests", "active_requests_legacy"),
        (DatabaseType.MYSQL, "5.7", "5.7.44", "blocking", "blocking_legacy"),
        (DatabaseType.ORACLE, "11gr2", "Oracle 11.2", "blocking", "blocking_basic"),
    )
    for db_type, declared, raw, collector, expected in cases:
        detected_family = declared
        if declared == "2008r2":
            detected_family = "2008r2"
        profile = PROFILE_RESOLVER.resolve(
            db_type, declared, DetectedDatabaseVersion(raw, detected_family), "slow"
        )
        adapter = SimpleNamespace(
            slow_profile=profile,
            instance=SimpleNamespace(db_type=db_type),
            statement_script="active_requests",
            blocking_script="blocking_basic",
        )
        assert script_for_slow(adapter, collector, collector) == expected


def test_mysql57_legacy_sql_avoids_mysql8_lock_views() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    for name in ("active_sessions_legacy", "active_requests_legacy", "blocking_legacy"):
        script = registry.get(name, DatabaseType.MYSQL)
        assert script.metadata.feature == "slow"
        assert "DATA_LOCKS" not in script.text.upper()
        assert "DATA_LOCK_WAITS" not in script.text.upper()
        assert script.metadata.risk == "READ_ONLY" and script.metadata.reviewed


def test_sqlserver_2025_is_unknown_and_auto_detected_permission_is_unknown() -> None:
    unknown = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2025", DetectedDatabaseVersion("17.0.1", "2025"), "slow"
    )
    assert unknown.support_state == SupportState.UNKNOWN
    assert unknown.sql_profile_id is None
    auto = PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, None, DetectedDatabaseVersion("8.4.11", "8.4"), "slow"
    )
    assert auto.version_status == VersionStatus.AUTO_DETECTED
    assert profile_permission_status(auto, {"PROCESS", "SELECT performance_schema"})["status"] \
        == PermissionStatus.UNKNOWN


def test_parser_contract_adds_normalized_fields_without_losing_evidence() -> None:
    profile = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2022", DetectedDatabaseVersion("16.0.1", "2022"), "slow"
    )
    now = datetime.now(timezone.utc)
    result = CollectorResult(
        "sample", "active_requests", now, now, 1.5,
        ({"session_id": 44, "database_name": "master", "login_name": "monitor",
          "elapsed_ms": 2500, "wait_type": "LCK_M_S", "sql_text": "SELECT 1"},),
        1, False, True,
    )
    normalized = normalize_slow_result(profile, result)
    assert normalized.rows[0]["sql_text"] == "SELECT 1"
    assert normalized.rows[0]["normalized"]["session_id"] == 44
    assert normalized.rows[0]["normalized"]["database"] == "master"
    assert normalized.rows[0]["normalized"]["running_seconds"] == 2.5
    assert normalized.rows[0]["normalized"]["client_ip"] is None


def test_optional_evidence_failure_downgrades_visibility_not_required_success() -> None:
    now = datetime.now(timezone.utc)
    required = CollectorResult("sample", "active_sessions", now, now, 1.0, (), 0, False, True)
    optional_failure = CollectorResult(
        "sample", "blocking", now, now, 1.0, (), 0, False, False, "CapabilityUnavailable",
    )
    incomplete, visibility = collection_completeness(
        (required, optional_failure), ("active_sessions",), ("blocking",)
    )
    assert incomplete is False
    assert visibility == "PARTIAL"
    missing_required, required_visibility = collection_completeness(
        (optional_failure,), ("active_sessions",), ("blocking",)
    )
    assert missing_required is True
    assert required_visibility == "PARTIAL"
    missing_optional_complete, missing_optional_visibility = collection_completeness(
        (required,), ("active_sessions",), ("blocking",)
    )
    assert missing_optional_complete is False
    assert missing_optional_visibility == "PARTIAL"


def test_oracle_slow_permissions_include_bootstrap_and_only_used_optional_views() -> None:
    requirement = requirements_for_profile("oracle_modern_slow")
    assert requirement.required == (
        "CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION",
        "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL",
        "SELECT SYS.V_$TRANSACTION",
    )
    assert requirement.optional == ("SELECT SYS.V_$LOCK", "SELECT SYS.V_$LOCKED_OBJECT")


def test_collector_requirement_sets_are_separate_from_slow_thresholds() -> None:
    assert SLOW_REQUIRED_COLLECTORS["oracle11gr2_slow"] == (
        "active_sessions", "active_requests"
    )
    assert "blocking" in SLOW_OPTIONAL_COLLECTORS["oracle11gr2_slow"]
    assert all("threshold" not in item for item in SLOW_SQL_PROFILES)
