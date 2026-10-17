from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.incident import build_incident_summary
from dba.permissions import (
    REGISTRY,
    PermissionStatus,
    assess_permissions,
    render_sql,
    scan_registry_objects,
)
from dba.registry import SqlScriptRegistry
from dba.replication import diagnose_replication
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.security import check_sql

ROOT = Path(__file__).resolve().parents[1]


def _result(name: str, rows: tuple[dict[str, Any], ...], *, success: bool = True,
            error: str | None = None) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("replica", name, now, now, 0.1, rows, len(rows), False,
                           success, None if success else "QueryDenied", error)


class _FakeRegistry:
    def get(self, name: str, _database_type: DatabaseType) -> SimpleNamespace:
        return SimpleNamespace(metadata=SimpleNamespace(name=name))


class _FakeAdapter:
    def __init__(self, db_type: DatabaseType, version: str, rows: dict[str, CollectorResult],
                 permissions: set[str] | None = None) -> None:
        self.instance = DatabaseInstance("replica", "Replica Test", db_type, "db.example.invalid",
                                         1, "monitor", database="master")
        self.registry = _FakeRegistry()
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
        return self.rows.get(name, _result(name, ()))


def _mysql_permissions() -> set[str]:
    return {"GRANT REPLICATION CLIENT ON *.* TO 'monitor'@'%';"}


def _pg_permissions() -> set[str]:
    return {"pg_monitor", "CONNECT"}


def _oracle_permissions() -> set[str]:
    return {"CREATE SESSION", "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$DATAGUARD_STATS",
            "SELECT SYS.V_$MANAGED_STANDBY", "SELECT SYS.V_$ARCHIVE_DEST_STATUS"}


def _sqlserver_permissions() -> set[str]:
    return {"VIEW SERVER STATE"}


@pytest.mark.parametrize("db_type", [DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                       DatabaseType.MYSQL, DatabaseType.ORACLE])
def test_replication_permission_registry_and_sql_alignment(db_type: DatabaseType) -> None:
    assert "replication" in REGISTRY[db_type]
    scanned = scan_registry_objects(ROOT / "sql")
    declared = {name.lower() for item in REGISTRY[db_type].values() for name in item.objects}
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
    assert adapter.calls == ["replication_server_info", "replication_status"]


@pytest.mark.regression
def test_mysql_null_lag_and_configured_delay_are_not_false_healthy_lag() -> None:
    adapter = _FakeAdapter(DatabaseType.MYSQL, "8.0.21", {
        "replication_server_info": _result("replication_server_info", ({"read_only": 1},)),
        "replication_status_legacy": _result("replication_status_legacy", ({
            "Replica_IO_Running": "Yes", "Replica_SQL_Running": "Yes",
            "Seconds_Behind_Source": None, "SQL_Delay": 60, "Relay_Log_Space": 100,
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
    assert not old.calls


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


def test_mysql_replica_status_registry_uses_only_fixed_commands() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    for name, statement in (("replication_status", "SHOW REPLICA STATUS"),
                            ("replication_status_legacy", "SHOW SLAVE STATUS")):
        script = registry.get(name, DatabaseType.MYSQL)
        assert script.text.strip() == statement
        registry.authorize(script, DatabaseType.MYSQL)
