from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.permissions import (
    REGISTRY,
    VERSION_DETECTION_PRIVILEGES,
    PermissionStatus,
    assess_permissions,
    render_sql,
    requirements_for_profile,
    scan_registry_objects,
)
from dba.presentation import render_replication_summary
from dba.registry import SqlScriptRegistry
from dba.replication import diagnose_replication
from dba.replication_collectors import (
    REPLICATION_PARSER_CONTRACTS,
    REPLICATION_SQL_PROFILES,
    validate_replication_profiles,
)
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.security import check_sql
from dba.versioning import (
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    VersionStatus,
    normalize_version,
)

ROOT = Path(__file__).resolve().parents[1]


def _result(name: str, rows: tuple[dict[str, Any], ...], *, success: bool = True,
            error: str | None = None,
            database_error: dict[str, Any] | None = None) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("replica", name, now, now, 0.1, rows, len(rows), False,
                           success, None if success else "QueryDenied", error, database_error)


class _FakeAdapter:
    def __init__(self, db_type: DatabaseType, version: str, rows: dict[str, CollectorResult],
                 permissions: set[str] | None = None) -> None:
        version_num = None
        if db_type == DatabaseType.POSTGRESQL:
            if version.isdigit() and len(version) >= 6:
                version_num = int(version)
            else:
                parts = version.split(".")
                version_num = int(parts[0]) * 10000 + int(parts[1] if len(parts) > 1 else 0) * 100
        detected = normalize_version(db_type, version, server_version_num=version_num)
        self.instance = DatabaseInstance("replica", "Replica Test", db_type, "db.example.invalid",
                                         1, "monitor", database="master",
                                         version_family=detected.normalized_family)
        self.registry = SqlScriptRegistry(ROOT / "sql")
        self.version = version
        self.rows = rows
        self.permission_observations = permissions or set()
        self.current_user = "monitor"
        self.calls: list[str] = []

    def get_server_version(self) -> str:
        return self.version

    def execute_readonly(self, script: SimpleNamespace) -> CollectorResult:
        name = script.metadata.name
        self.calls.append(name)
        if name == "version_detect":
            row: dict[str, Any] = {"raw_version": self.version}
            if self.instance.db_type == DatabaseType.POSTGRESQL:
                parts = self.version.split(".")
                row["server_version_num"] = (
                    int(self.version) if self.version.isdigit() and len(self.version) >= 6
                    else int(parts[0]) * 10000 + int(parts[1] if len(parts) > 1 else 0) * 100
                )
            return _result(name, (row,))
        return self.rows.get(name, _result(name, ()))


def _mysql_permissions() -> set[str]:
    return {"GRANT REPLICATION CLIENT ON *.* TO 'monitor'@'%';"}


def _pg_permissions() -> set[str]:
    return {"pg_monitor", "CONNECT"}


def _oracle_permissions() -> set[str]:
    return {"CREATE SESSION", "SELECT SYS.V_$VERSION", "SELECT SYS.V_$DATABASE",
            "SELECT SYS.V_$DATAGUARD_STATS", "SELECT SYS.V_$MANAGED_STANDBY",
            "SELECT SYS.V_$DATAGUARD_PROCESS", "SELECT SYS.V_$ARCHIVE_DEST_STATUS"}


def _sqlserver_permissions() -> set[str]:
    return {"VIEW SERVER STATE", "VIEW SERVER PERFORMANCE STATE"}


@pytest.mark.parametrize("db_type", [DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                       DatabaseType.MYSQL, DatabaseType.ORACLE])
def test_replication_permission_registry_and_sql_alignment(db_type: DatabaseType) -> None:
    assert "replication" in REGISTRY[db_type]
    scanned = scan_registry_objects(ROOT / "sql")
    declared = {name.lower() for item in REGISTRY[db_type].values() for name in item.objects}
    declared.update(item.removeprefix("SELECT ").lower() for item in
                    VERSION_DETECTION_PRIVILEGES.get(db_type, ()))
    assert scanned[db_type] <= declared
    scripts = SqlScriptRegistry(ROOT / "sql").validate()
    assert all(script.metadata.risk == "READ_ONLY" and script.metadata.reviewed
               for script in scripts if "replication" in script.metadata.name)


@pytest.mark.regression
def test_mysql_multichannel_threads_lag_errors_gtid_and_backlog() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.4.1", {
        "replication_server_info": _result("replication_server_info", ({"server_uuid": "node-a",
            "server_id": 1, "read_only": 1, "super_read_only": 1, "gtid_mode": "ON"},)),
        "replication_status": _result("replication_status", (
            {"Channel_Name": "west", "Source_Host": "source-a", "Replica_IO_Running": "Yes",
             "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 4, "SQL_Delay": 0,
             "Relay_Log_Space": 100, "Retrieved_Gtid_Set": "a:1-10", "Executed_Gtid_Set": "a:1-10"},
            {"Channel_Name": "east", "Source_Host": "source-b", "Replica_IO_Running": "No",
             "Replica_SQL_Running": "No", "Seconds_Behind_Source": None, "SQL_Delay": 0,
             "Relay_Log_Space": 10, "Last_IO_Error": "connect error", "Last_SQL_Error": "apply error"},
        )),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.local_role == "REPLICA"
    assert len(report.members) == 2
    assert {issue.code for issue in report.issues} >= {"SOURCE_CONNECTION_ERROR", "SQL_APPLY_ERROR"}
    assert any(item.kind == "time" and item.value == 4 for item in report.lag)
    assert report.summary.overall == "CRITICAL"
    assert report.details["permission_readiness"]["status"] == PermissionStatus.READY
    assert adapter.calls == ["version_detect", "replication_server_info", "replication_status"]


@pytest.mark.regression
def test_mysql_null_lag_and_configured_delay_are_not_false_healthy_lag() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.0.21", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 1},)),
        "replication_status_legacy": _result("replication_status_legacy", ({
            "Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes",
            "Seconds_Behind_Master": None, "SQL_Delay": 60, "Relay_Log_Space": 100,
        },)),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert "replication_status_legacy" in adapter.calls
    assert report.summary.overall == "HEALTHY"
    assert any(item.kind == "configured_delay" and item.value == 60 for item in report.lag)
    assert not any(item.code == "REPLICATION_LAG" for item in report.issues)


def test_postgresql_primary_multiple_members_slots_and_wal_retention() -> None:
    adapter = _FakeAdapter(DatabaseType.POSTGRESQL, "160013", {
        "replication_node": _result("replication_node", ({"in_recovery": False,
            "wal_level": "replica", "max_wal_senders": "10", "max_replication_slots": "10",
            "current_wal_lsn": "0/1000"},)),
        "replication_members": _result("replication_members", (
            {"application_name": "standby-a", "state": "streaming", "write_lag_bytes": 10,
             "flush_lag_bytes": 20, "replay_lag_bytes": 40},
            {"application_name": "standby-b", "state": "catchup", "replay_lag_bytes": 100},
        )),
        "replication_receiver": _result("replication_receiver", ()),
        "replication_slots": _result("replication_slots", ({"slot_name": "orphan",
            "slot_type": "physical", "active": False, "retained_wal_bytes": 2_000_000_000,
            "wal_status": "extended"},)),
        "replication_archiver": _result("replication_archiver", ({"failed_count": 0},)),
    }, _pg_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.local_role == "PRIMARY"
    assert len(report.members) == 2
    assert {item.kind for item in report.lag} >= {"write", "flush", "replay"}
    assert {item.code for item in report.issues} >= {"INACTIVE_PHYSICAL_SLOT", "LARGE_RETAINED_WAL"}
    assert report.summary.data_completeness == "COMPLETE"


def test_postgresql_standby_without_receiver_is_disconnected_and_timestamp_is_context_only() -> None:
    adapter = _FakeAdapter(DatabaseType.POSTGRESQL, "16.4", {
        "replication_node": _result("replication_node", ({"in_recovery": True,
            "receive_replay_bytes": 500, "last_replay_timestamp": datetime.now(timezone.utc)},)),
        "replication_members": _result("replication_members", ()),
        "replication_receiver": _result("replication_receiver", ()),
        "replication_slots": _result("replication_slots", ()),
        "replication_archiver": _result("replication_archiver", ()),
    }, _pg_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.local_role == "STANDBY"
    assert report.summary.transport_status == "DISCONNECTED"
    assert any(item.code == "DISCONNECTED_REPLICA" for item in report.issues)
    assert any(item.kind == "replay_timestamp_age" and item.severity == "INFO" for item in report.lag)


@pytest.mark.regression
def test_postgresql_unconfigured_is_not_critical() -> None:
    adapter = _FakeAdapter(DatabaseType.POSTGRESQL, "15.8", {
        "replication_node": _result("replication_node", ({"in_recovery": False,
            "wal_level": "minimal", "max_wal_senders": "0"},)),
        "replication_members": _result("replication_members", ()),
        "replication_receiver": _result("replication_receiver", ()),
        "replication_slots": _result("replication_slots", ()),
        "replication_archiver": _result("replication_archiver", ()),
    }, _pg_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.overall == "HEALTHY"
    assert not any(issue.severity in {"HIGH", "CRITICAL"} for issue in report.issues)
    assert "Not applicable because replication is not configured." in render_replication_summary(
        report, report_path="report.html"
    )


@pytest.mark.regression
def test_sqlserver_ag_role_and_queue_units_and_old_version_unsupported() -> None:
    adapter = _FakeAdapter(DatabaseType.SQLSERVER, "16.0.4305.5", {
        "replication_ag": _result("replication_ag", ({"ag_name": "ag-a",
            "replica_server_name": "sql-a", "is_local": True, "role_desc": "PRIMARY",
            "connected_state_desc": "CONNECTED", "synchronization_state_desc": "SYNCHRONIZED",
            "replica_synchronization_health": "HEALTHY", "log_send_queue_size": 1024,
            "log_send_rate": 256, "redo_queue_size": 2048, "redo_rate": 512,
            "database_name": "app"},)),
    }, _sqlserver_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.local_role == "PRIMARY"
    assert any(item.unit == "KB" and item.kind == "log_send_queue_kb" for item in report.lag)
    assert any(item.severity == "INFO" and item.unit == "estimated_seconds" for item in report.lag)

    old = _FakeAdapter(DatabaseType.SQLSERVER, "10.50.6000", {}, _sqlserver_permissions())
    old_report = diagnose_replication(old)
    assert old_report.summary.status == "UNSUPPORTED"
    assert old.calls == ["version_detect"]


@pytest.mark.regression
def test_sqlserver_no_ag_is_not_configured() -> None:
    adapter = _FakeAdapter(DatabaseType.SQLSERVER, "15.0.4312", {
        "replication_ag": _result("replication_ag", ()),
    }, _sqlserver_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.overall == "HEALTHY"


@pytest.mark.regression
def test_oracle_primary_no_data_guard_and_standby_vendor_error() -> None:
    primary = _FakeAdapter(DatabaseType.ORACLE, "23.0.0", {
        "replication_database": _result("replication_database", ({"database_role": "PRIMARY",
            "open_mode": "READ WRITE", "protection_mode": "MAXIMUM PERFORMANCE"},)),
        "replication_metrics": _result("replication_metrics", ()),
        "replication_processes": _result("replication_processes", ()),
        "replication_destinations": _result("replication_destinations", ()),
    }, _oracle_permissions())
    report = diagnose_replication(primary)
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.overall == "HEALTHY"

    standby = _FakeAdapter(DatabaseType.ORACLE, "19.0.0", {
        "replication_database": _result("replication_database", ({"database_role": "PHYSICAL STANDBY"},)),
        "replication_metrics": _result("replication_metrics", ({"name": "transport lag",
            "value": "+00 00:06:00", "time_computed": "now"},)),
        "replication_processes": _result("replication_processes", ({"process": "MRP0",
            "status": "APPLYING_LOG"},)),
        "replication_destinations": _result("replication_destinations", ({"dest_id": 2,
            "destination": "standby-dest", "status": "ERROR", "error": "ORA-12154"},)),
    }, _oracle_permissions())
    standby_report = diagnose_replication(standby)
    assert standby_report.summary.local_role == "STANDBY"
    assert any(item.code == "TRANSPORT_FAILURE" and item.vendor_message == "ORA-12154"
               for item in standby_report.issues)
    assert any(item.kind == "transport_lag" and item.value == 360 for item in standby_report.lag)


def test_replication_permission_assessment_and_minimal_grants() -> None:
    definition = REGISTRY[DatabaseType.MYSQL]["replication"]
    assert assess_permissions(definition, None)["status"] == PermissionStatus.UNKNOWN
    assert assess_permissions(definition, ())["status"] == PermissionStatus.MISSING
    assert assess_permissions(definition, _mysql_permissions())["status"] == PermissionStatus.READY
    sql = render_sql(DatabaseType.MYSQL, ("replication",), "monitor")
    assert "GRANT REPLICATION CLIENT" in sql
    assert "SUPER" not in sql and "STOP REPLICA" not in sql
    assert "does NOT execute" in sql


def test_replication_report_html_is_bounded_and_discloses_manual_only() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.4.1", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 1},)),
        "replication_status": _result("replication_status", ({"Channel_Name": "west",
            "Source_Host": "<img>", "Replica_IO_Running": "Yes",
            "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 0},)),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    model = build_report_model(report, build_incident_summary(report))
    page = HtmlReportRenderer().render(model)
    assert "Replication Summary" in page and "Topology" in page
    assert "Toolkit Executed: NO" in page
    assert "<img>" not in page
    assert "&lt;img&gt;" in page
    assert "PROMOTE" not in page and "START REPLICA" not in page


def test_replication_registry_sql_is_reviewed_read_only_and_no_mutation() -> None:
    scripts = SqlScriptRegistry(ROOT / "sql").validate()
    for script in scripts:
        if "replication" in script.metadata.name:
            assert script.metadata.reviewed and script.metadata.risk == "READ_ONLY"
            if not script.text.startswith("SHOW "):
                check_sql(script.text)
    for name in ("replication_node", "replication_archiver"):
        script = SqlScriptRegistry(ROOT / "sql").get(name, DatabaseType.POSTGRESQL)
        assert script.metadata.max_rows == 2
    with pytest.raises(Exception):
        check_sql("START REPLICA")
    with pytest.raises(Exception):
        check_sql("SHOW DATABASES")


def test_replication_routes_bind_sql_permissions_parser_and_support_atomically() -> None:
    routes = [item for item in PROFILE_RESOLVER.bindings if item.feature == "replication"]
    assert validate_replication_profiles(SqlScriptRegistry(ROOT / "sql"), tuple(routes)) == ()
    for route in routes:
        if route.support_state.value == "SUPPORTED":
            assert route.sql_profile_id in REPLICATION_SQL_PROFILES
            assert route.parser_contract_id in REPLICATION_PARSER_CONTRACTS
            assert route.permission_profile_id == route.sql_profile_id or (
                route.db_type == DatabaseType.SQLSERVER
                and route.permission_profile_id in {"sqlserver_ag_pre2022", "sqlserver_ag_2022"}
            )


@pytest.mark.parametrize(("raw", "variant", "script"), [
    ("8.0.21", "pre_8022", "replication_status_legacy"),
    ("8.0.22", "8022_plus", "replication_status"),
    ("8.0.36", "8022_plus", "replication_status"),
])
def test_mysql_80_replication_boundary_selects_exact_profile(raw: str, variant: str, script: str) -> None:
    detected = normalize_version(DatabaseType.MYSQL, raw)
    profile = PROFILE_RESOLVER.resolve(DatabaseType.MYSQL, "8.0", detected, "replication")
    assert profile.version_status == VersionStatus.VERIFIED
    assert profile.runtime_variant == variant
    assert script in REPLICATION_SQL_PROFILES[profile.sql_profile_id or ""]
    assert profile.permission_profile_id == profile.sql_profile_id
    assert profile.parser_contract_id == profile.sql_profile_id


def test_no_legacy_sql_fallback_after_resolved_modern_query_failure() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.0.22", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 0},)),
        "replication_status": _result("replication_status", (), success=False,
                                       error="replica status denied"),
        "replication_status_legacy": _result("replication_status_legacy", ({
            "Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes"},)),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert "replication_status" in adapter.calls
    assert "replication_status_legacy" not in adapter.calls
    assert report.summary.data_completeness == "INCOMPLETE"


@pytest.mark.parametrize(("raw", "variant", "sql_profile"), [
    ("Oracle Database 12c Release 12.1.0.2.0", "12_1", "oracle12c_121_replication"),
    ("Oracle Database 12c Release 12.2.0.1.0", "12_2_plus", "oracle12c_122_replication"),
])
def test_oracle_12c_minor_version_selects_atomic_data_guard_route(
    raw: str, variant: str, sql_profile: str,
) -> None:
    detected = normalize_version(DatabaseType.ORACLE, raw)
    profile = PROFILE_RESOLVER.resolve(DatabaseType.ORACLE, "12c", detected, "replication")
    assert profile.runtime_variant == variant
    assert profile.sql_profile_id == profile.permission_profile_id == sql_profile
    assert profile.parser_contract_id == sql_profile
    assert ("replication_processes" if variant == "12_1" else "replication_processes_modern") \
        in REPLICATION_SQL_PROFILES[sql_profile]


def test_postgresql_13_16_18_reuse_only_same_reviewed_replication_profile() -> None:
    for version, family in (("13.15", "13"), ("16.4", "16"), ("18.0", "18")):
        detected = normalize_version(DatabaseType.POSTGRESQL, version)
        profile = PROFILE_RESOLVER.resolve(DatabaseType.POSTGRESQL, family, detected, "replication")
        assert profile.sql_profile_id == "pg13_18_replication"
        assert profile.permission_profile_id == profile.parser_contract_id == "pg13_18_replication"


def test_mysql_57_replication_uses_legacy_aliases_and_complete_empty_snapshot() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "5.7.44-log", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 0},)),
        "replication_status_legacy": _result("replication_status_legacy", ()),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert "replication_status_legacy" in adapter.calls
    assert "replication_status" not in adapter.calls
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.data_completeness == "COMPLETE"
    assert "show slave status" in REGISTRY[DatabaseType.MYSQL]["replication"].objects
    assert dict(REPLICATION_PARSER_CONTRACTS["mysql57_replication"].aliases)["io_running"] == (
        "Slave_IO_Running",
    )


@pytest.mark.parametrize(("version", "status_name"), [
    ("8.0.21", "replication_status_legacy"),
    ("8.0.22", "replication_status"),
    ("8.4.11", "replication_status"),
])
def test_mysql_successful_empty_status_is_complete_not_configured(
    version: str, status_name: str,
) -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, version, {
        "replication_server_info": _result("replication_server_info", ({"read_only": 0},)),
        status_name: _result(status_name, ()),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert status_name in adapter.calls
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.overall == "HEALTHY"
    assert report.summary.data_completeness == "COMPLETE"
    assert report.summary.transport_status == "NOT_CONFIGURED"
    assert report.summary.apply_status == "NOT_CONFIGURED"
    assert report.members == ()
    assert report.issues == ()
    assert "Not applicable because replication is not configured." in render_replication_summary(
        report, report_path=None
    )


def test_mysql_replica_status_query_failure_is_incomplete_and_preserves_vendor_evidence() -> None:
    vendor_error = {"database_type": "mysql", "driver": "PyMySQL", "vendor_code": 1142,
                    "message": "SELECT command denied; user=test denied"}
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.4.11", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 0},)),
        "replication_status": _result("replication_status", (), success=False,
                                       error="Query failed", database_error=vendor_error),
    }, _mysql_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.status == "UNKNOWN"
    assert report.summary.overall == "UNKNOWN"
    assert report.summary.data_completeness == "INCOMPLETE"
    assert report.summary.transport_status == "UNKNOWN"
    assert "REQUIRED_EVIDENCE_MISSING" in {issue.code for issue in report.issues}
    failed = next(sample for sample in report.details["samples"]
                  if sample["collector"] == "replication_status")
    assert failed["success"] is False
    assert failed["vendor_error"] == vendor_error


def test_singleton_replication_limits_allow_one_row_without_truncation() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    for database_type, script_name in (
        (DatabaseType.MYSQL, "replication_server_info"),
        (DatabaseType.POSTGRESQL, "replication_receiver"),
        (DatabaseType.ORACLE, "replication_database"),
    ):
        script = registry.get(script_name, database_type)
        assert script.metadata.max_rows >= 2


def test_oracle_11gr2_uses_legacy_data_guard_bundle_and_grants() -> None:
    adapter = _FakeAdapter(DatabaseType.ORACLE, "Oracle Database 11g Release 11.2.0.4.0", {
        "replication_database": _result("replication_database", ({"database_role": "PRIMARY"},)),
        "replication_metrics": _result("replication_metrics", ()),
        "replication_processes": _result("replication_processes", ()),
        "replication_destinations": _result("replication_destinations", ()),
    }, {"CREATE SESSION", "SELECT SYS.V_$VERSION", "SELECT SYS.V_$DATABASE",
        "SELECT SYS.V_$DATAGUARD_STATS", "SELECT SYS.V_$MANAGED_STANDBY",
        "SELECT SYS.V_$ARCHIVE_DEST_STATUS"})
    report = diagnose_replication(adapter)
    assert "replication_processes" in adapter.calls
    assert "replication_processes_modern" not in adapter.calls
    assert report.summary.status == "NOT_CONFIGURED"
    requirement = requirements_for_profile(report.details["version_profile"]["permission_profile_id"])
    assert "SELECT SYS.V_$MANAGED_STANDBY" in requirement.required
    assert "SELECT SYS.V_$DATAGUARD_PROCESS" not in requirement.required
    assert "SELECT SYS.V_$VERSION" in requirement.required


def test_version_mismatch_blocks_replication_before_feature_sql() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.4.1", {}, _mysql_permissions())
    adapter.instance = adapter.instance.__class__(
        adapter.instance.id, adapter.instance.name, adapter.instance.db_type,
        adapter.instance.host, adapter.instance.port, adapter.instance.credential_alias,
        database=adapter.instance.database, version_family="8.0",
    )
    with pytest.raises(Exception, match="verified version family"):
        diagnose_replication(adapter)
    assert adapter.calls == ["version_detect"]


def test_oracle_unverified_profile_stops_before_feature_sql() -> None:
    adapter = _FakeAdapter(DatabaseType.ORACLE, "Oracle Database 26ai 26.0.0.0.0", {},
                           _oracle_permissions())
    calls: list[str] = []

    def failed_version_probe(script: SimpleNamespace) -> CollectorResult:
        calls.append(script.metadata.name)
        return _result(script.metadata.name, (), success=False,
                       error="ORA-01045: monitor session cannot verify version")

    adapter.execute_readonly = failed_version_probe  # type: ignore[method-assign]
    with pytest.raises(ToolkitError, match="verified version family"):
        diagnose_replication(adapter)
    assert calls == ["version_detect"]


def test_unconfigured_postgresql_primary_is_healthy_with_sender_capacity() -> None:
    adapter = _FakeAdapter(DatabaseType.POSTGRESQL, "16.4", {
        "replication_node": _result("replication_node", ({"in_recovery": False,
            "wal_level": "replica", "max_wal_senders": 10},)),
        "replication_members": _result("replication_members", ()),
        "replication_receiver": _result("replication_receiver", ()),
        "replication_slots": _result("replication_slots", ()),
        "replication_archiver": _result("replication_archiver", ()),
    }, _pg_permissions())
    report = diagnose_replication(adapter)
    assert report.summary.status == "NOT_CONFIGURED"
    assert report.summary.overall == "HEALTHY"
    assert report.summary.data_completeness == "COMPLETE"
    assert report.details["permission_readiness"]["account_security"]["classification"] == "NON_PRIVILEGED"


def test_unknown_sqlserver_2025_replication_route_fails_closed() -> None:
    profile = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2025", DetectedDatabaseVersion("17.0.1", "2025"), "replication"
    )
    assert profile.support_state.value == "UNKNOWN"
    assert profile.sql_profile_id is None


def test_mysql_replica_status_registry_uses_only_fixed_commands() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    for name, statement in (("replication_status", "SHOW REPLICA STATUS"),
                            ("replication_status_legacy", "SHOW SLAVE STATUS")):
        script = registry.get(name, DatabaseType.MYSQL)
        assert script.text.strip() == statement
        registry.authorize(script, DatabaseType.MYSQL)
