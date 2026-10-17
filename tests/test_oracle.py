import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider, MockCredentialProvider, Mode
from dba.errors import ToolkitError
from dba.oracle import OracleAdapter
from dba.oracle_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    LongRunningCollector,
)
from dba.oracle_slow import blocking_graph, build_slow_report
from dba.oracle_slow_reporter import render_markdown
from dba.registry import SqlScriptRegistry
from dba.settings import OracleSettings

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] | None = None
        self.arraysize = 100
        self.prefetchrows = 100
        self.rows: list[dict[str, Any]] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        name = self.connection.sql_names[sql]
        if name in self.connection.fail:
            raise self.connection.fail[name]
        self.rows = self.connection.data.get(name, [])
        self.description = [(key.upper(),) for key in self.rows[0]] if self.rows else [("EMPTY",)]
        self.connection.calls.append((name, self.connection.call_timeout))

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return [tuple(row.values()) for row in self.rows[:size]]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, sql_names: dict[str, str], data: dict[str, list[dict[str, Any]]],
                 fail: dict[str, Exception] | None = None) -> None:
        self.sql_names = sql_names
        self.data = data
        self.fail = fail or {}
        self.autocommit = False
        self.call_timeout = 0
        self.closed = False
        self.rolled_back = False
        self.calls: list[tuple[str, int]] = []
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self, connection: FakeConnection, fail: Exception | None = None) -> None:
        self.connection = connection
        self.fail = fail
        self.kwargs: dict[str, Any] = {}

    def connect(self, **kwargs: Any) -> FakeConnection:
        self.kwargs = kwargs
        if self.fail:
            raise self.fail
        return self.connection


def fixture() -> tuple[DatabaseInstance, SqlScriptRegistry, MockCredentialProvider, FakeDriver]:
    instance = DatabaseInstance("oracle-test", "Oracle Test", DatabaseType.ORACLE,
                                "example.invalid", 1521, "oracle-monitor", service_name="FREEPDB1")
    registry = SqlScriptRegistry(ROOT / "sql")
    names = (*OracleAdapter.REQUIRED, "version_detect")
    sql_names = {registry.get(name, DatabaseType.ORACLE).text: name for name in names}
    data: dict[str, list[dict[str, Any]]] = {
        "preflight": [{"server_version": "19.0.0.0.0", "inst_id": 1, "rac_mode": "NO",
                       "monitor_user": "MONITOR"}],
        "version_detect": [{"raw_version": "Oracle Database 19c Enterprise Edition "
                                           "Release 19.0.0.0.0 - Production"}],
        "preflight_privileges": [{"kind": "SYSTEM", "privilege_name": "CREATE SESSION"}],
        "preflight_object_grants": [
            {"owner": "SYS", "table_name": name, "privilege": "SELECT", "grantable": "NO"}
            for name in ("V_$INSTANCE", "V_$DATABASE", "V_$VERSION", "V_$SESSION", "V_$SQL",
                         "V_$TRANSACTION", "V_$LOCK", "V_$LOCKED_OBJECT",
                         "V_$PROCESS", "V_$SESSION_WAIT")],
        "preflight_owned_objects": [{"owned_objects": 0}],
        "server_info": [{"database_name": "FREE", "server_version": "19.0.0.0.0"}],
        "database_status": [{"database_name": "FREE", "open_mode": "READ WRITE"}],
        "active_sessions": [{"inst_id": 1, "sid": 10, "serial_number": 20,
                             "username": "APP", "status": "ACTIVE", "machine": "host"}],
        "active_requests": [{"inst_id": 1, "sid": 10, "serial_number": 20,
                             "duration_seconds": 65, "sql_text": "SELECT 1"}],
    }
    data["long_running"] = data["active_requests"]
    conn = FakeConnection(sql_names, data)
    driver = FakeDriver(conn)
    provider = MockCredentialProvider({"oracle-monitor": Credential("MONITOR", "TOPSECRET")})
    return instance, registry, provider, driver


def adapter_for_test(*, settings: OracleSettings | None = None) -> tuple[OracleAdapter, FakeDriver]:
    instance, registry, provider, driver = fixture()
    return OracleAdapter(instance, provider, registry, settings, Mode.DEVELOPMENT, driver=driver), driver


def test_oracle_connect_collect_and_close() -> None:
    adapter, driver = adapter_for_test(settings=OracleSettings(call_timeout=3))
    with adapter:
        assert adapter.get_server_version().startswith("19.")
        assert all(adapter.preflight_checks.values())
        assert driver.kwargs["service_name"] == "FREEPDB1"
        assert driver.kwargs["tcp_connect_timeout"] == 5
        assert driver.connection.autocommit
        assert ActiveSessionsCollector(adapter).collect(adapter.instance).rows[0]["sid"] == 10
        assert ActiveRequestsCollector(adapter).collect(adapter.instance).rows[0]["sql_text"] == "SELECT 1"
        assert LongRunningCollector(adapter).collect(adapter.instance).row_count == 1
        assert driver.connection.calls[-1][1] == 3000
    assert driver.connection.closed
    assert all(cursor.closed for cursor in driver.connection.cursors)
    assert "TOPSECRET" not in repr(adapter)


def test_version_view_is_reported_as_detection_capability_without_auto_grant() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_object_grants"] = [
        row for row in driver.connection.data["preflight_object_grants"]
        if row["table_name"] != "V_$VERSION"
    ]
    with adapter:
        assert adapter.preflight_checks["version_detection_view"] is False
        assert "SELECT SYS.V_$VERSION" not in adapter.permission_observations
        assert adapter.get_server_version().startswith("19.")


def test_sid_target_and_invalid_targets() -> None:
    instance, registry, provider, driver = fixture()
    sid_target = DatabaseInstance(**{**asdict(instance), "service_name": None, "sid": "ORCL"})
    with OracleAdapter(sid_target, provider, registry, mode=Mode.DEVELOPMENT, driver=driver):
        assert driver.kwargs["sid"] == "ORCL"
    with pytest.raises(ToolkitError, match="exactly one"):
        OracleAdapter(DatabaseInstance(**{**asdict(instance), "sid": "ORCL"}),
                      provider, registry, mode=Mode.DEVELOPMENT, driver=driver)


def test_driver_error_redaction_and_cleanup() -> None:
    adapter, driver = adapter_for_test()
    driver.fail = RuntimeError("password=TOPSECRET")
    with pytest.raises(ToolkitError) as caught:
        adapter.connect()
    assert "TOPSECRET" not in str(caught.value)
    adapter, driver = adapter_for_test()
    driver.connection.fail["active_sessions"] = RuntimeError("password=TOPSECRET")
    with pytest.raises(ToolkitError):
        adapter.connect()
    assert driver.connection.closed


def test_preflight_failure_preserves_sanitized_vendor_error() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.fail["preflight"] = RuntimeError(
        "ORA-01045: user lacks CREATE SESSION; password=TOPSECRET"
    )
    with pytest.raises(ToolkitError) as caught:
        adapter.connect()
    assert caught.value.error_type == "PreflightFailed"
    assert isinstance(caught.value.database_error, dict)
    assert caught.value.database_error["vendor_code"] == "ORA-01045"
    assert "ORA-01045" in caught.value.database_error["message"]
    assert "TOPSECRET" not in repr(caught.value.database_error)
    assert driver.connection.closed


def test_preflight_rejects_privileged_role_and_missing_view() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_privileges"] = [{"kind": "ROLE", "privilege_name": "DBA"}]
    with pytest.raises(ToolkitError, match="unreviewed role"):
        adapter.connect()
    assert driver.connection.closed


def test_preflight_rejects_write_grant_and_downgrades_optional_views() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_object_grants"].append(
        {"owner": "APP", "table_name": "T", "privilege": "UPDATE", "grantable": "NO"})
    with pytest.raises(ToolkitError, match="non-read-only"):
        adapter.connect()
    assert driver.connection.closed
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_object_grants"] = [
        row for row in driver.connection.data["preflight_object_grants"]
        if row["table_name"] not in {"V_$LOCK", "V_$LOCKED_OBJECT", "V_$PROCESS",
                                     "V_$SESSION_WAIT"}]
    with adapter:
        assert adapter.blocking_script == "blocking_basic"
        assert not adapter.capabilities["lock_evidence"]
        assert not adapter.preflight_checks["probe_wait"]
    adapter, driver = adapter_for_test()
    driver.connection.fail["probe_lock"] = RuntimeError("ORA-00942 password=TOPSECRET")
    with pytest.raises(ToolkitError, match="probe_lock"):
        adapter.connect()
    assert driver.connection.closed


def test_timeout_and_registry_restrictions() -> None:
    adapter, driver = adapter_for_test()
    with adapter:
        script = adapter.registry.get("server_info", DatabaseType.ORACLE)
        assert adapter.execute_readonly(script).success
        other = SqlScriptRegistry(ROOT / "sql").get("server_info", DatabaseType.ORACLE)
        result = adapter.execute_readonly(other)
        assert not result.success and result.error_type == "RegistryRejected"
    assert driver.connection.closed
    adapter, driver = adapter_for_test()
    with adapter:
        driver.connection.fail["active_requests"] = TimeoutError("password=TOPSECRET")
        timed = ActiveRequestsCollector(adapter).collect(adapter.instance)
        assert not timed.success and timed.error_type == "QueryTimeout"
        assert "TOPSECRET" not in str(timed)
        assert driver.connection.closed
    adapter, driver = adapter_for_test()
    driver.connection.fail["server_info"] = TimeoutError("password=TOPSECRET")
    with pytest.raises(ToolkitError):
        adapter.connect()
    assert driver.connection.closed


def test_interrupt_and_sql_value_redaction() -> None:
    adapter, driver = adapter_for_test()
    adapter.connect()
    driver.connection.data["active_requests"][0]["sql_text"] = "SELECT 'password=TOPSECRET'"
    result = ActiveRequestsCollector(adapter).collect(adapter.instance)
    assert result.success and "TOPSECRET" not in str(result.rows)
    driver.connection.fail["active_requests"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ActiveRequestsCollector(adapter).collect(adapter.instance)
    assert driver.connection.closed


def test_max_rows_and_long_running_threshold() -> None:
    adapter, driver = adapter_for_test(settings=OracleSettings(long_running_seconds=70))
    with adapter:
        driver.connection.data["active_requests"].append(
            {"inst_id": 1, "sid": 11, "serial_number": 21,
             "duration_seconds": 100, "sql_text": "SELECT 2"})
        long = LongRunningCollector(adapter).collect(adapter.instance)
        assert [row["sid"] for row in long.rows] == [11]
        driver.connection.data["active_requests"] *= 500
        result = ActiveRequestsCollector(adapter).collect(adapter.instance)
        assert result.truncated and result.row_count == 1000


def _result(name: str, rows: list[dict[str, Any]]) -> CollectorResult:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return CollectorResult("oracle-test", name, now, now, 1.0, tuple(rows), len(rows), False, True)


def test_blocking_rac_cycles_and_missing_root() -> None:
    sessions = [{"inst_id": 1, "sid": 10}, {"inst_id": 1, "sid": 20},
                {"inst_id": 2, "sid": 10}, {"inst_id": 2, "sid": 30}]
    edges = [{"inst_id": 1, "blocked_sid": 20, "blocker_inst_id": 1, "blocker_sid": 10},
             {"inst_id": 2, "blocked_sid": 30, "blocker_inst_id": 2, "blocker_sid": 10}]
    graph = blocking_graph(edges, sessions)
    assert graph["roots"] == ["1:10", "2:10"]
    assert graph["children"]["1:10"] == ["1:20"]
    missing = blocking_graph([{"inst_id": 1, "blocked_sid": 20,
                               "blocker_inst_id": 3, "blocker_sid": 90}], sessions)
    assert "3:90" in missing["unresolved"]
    cycle = blocking_graph([*edges, {"inst_id": 1, "blocked_sid": 10,
                                     "blocker_inst_id": 1, "blocker_sid": 20}], sessions)
    assert cycle["cycles"]


def test_idle_transaction_root_reference_and_report() -> None:
    instance, _, _, _ = fixture()
    sessions = [{"inst_id": 1, "sid": 10, "serial_number": 99,
                 "status": "INACTIVE", "username": "APP", "machine": "host",
                 "prev_sql_id": "abc"},
                {"inst_id": 1, "sid": 20, "serial_number": 100, "status": "ACTIVE"}]
    results = (_result("active_sessions", sessions),
               _result("active_requests", [{"inst_id": 1, "sid": 20,
                                            "duration_seconds": 70, "sql_text": "SELECT 1"}]),
               _result("transactions", [{"inst_id": 1, "sid": 10,
                                         "transaction_start_time": "2026-01-01",
                                         "transaction_age_seconds": 90, "used_ublk": 3,
                                         "used_urec": 4}]),
               _result("blocking", [{"inst_id": 1, "blocked_sid": 20,
                                     "blocker_inst_id": 1, "blocker_sid": 10}]),
               _result("previous_sql", [{"inst_id": 1, "sid": 10,
                                         "previous_sql_text": "SELECT 2"}]))
    report = build_slow_report(instance, results, OracleSettings(), "19.0")
    titles = {finding.title for finding in report.findings}
    assert {"BLOCKING", "ROOT_BLOCKER", "IDLE_TRANSACTION", "LONG_RUNNING_SQL",
            "WAITING_SQL"} <= titles
    root = next(f for f in report.findings if f.title == "ROOT_BLOCKER")
    assert root.reference_commands[0].manual_only
    assert not hasattr(root.reference_commands[0], "execute")
    assert root.reference_commands[0].text == "ALTER SYSTEM KILL SESSION '10,99' IMMEDIATE;"
    assert "client_ip" in render_markdown(report)
    assert "Toolkit did NOT execute" in render_markdown(report)


def test_no_reference_when_snapshot_missing_root_or_cycle() -> None:
    instance, _, _, _ = fixture()
    sessions = [{"inst_id": 1, "sid": 20, "status": "ACTIVE"}]
    results = (_result("active_sessions", sessions), _result("active_requests", []),
               _result("transactions", []),
               _result("blocking", [{"inst_id": 1, "blocked_sid": 20,
                                     "blocker_inst_id": 2, "blocker_sid": 10}]))
    report = build_slow_report(instance, results, OracleSettings(), "19.0")
    assert not any(f.reference_commands for f in report.findings)
    assert report.details["blocking_graph"]["unresolved"]
    rac = build_slow_report(instance, results, OracleSettings(), "19.0", rac_enabled=True)
    assert rac.details["incomplete"]
    assert not any(f.reference_commands for f in rac.findings)


@pytest.mark.integration
def test_cli_oracle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    instance, _, _, driver = fixture()

    class TestProvider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            assert alias == "oracle-monitor"
            return Credential("MONITOR", "TOPSECRET")

    provider = TestProvider()
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        **asdict(instance), "db_type": "oracle"}]}), encoding="utf-8")
    prefix = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql")]
    assert main([*prefix, "test-connection", instance.id, "--json"], provider=provider, driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["version"].startswith("19")
    _, _, _, driver = fixture()
    assert main([*prefix, "collect", instance.id, "active-sessions", "--json"],
                provider=provider, driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["row_count"] == 1
    _, _, _, driver = fixture()
    report_path = tmp_path / "oracle.html"
    assert main([*prefix, "diagnose", instance.id, "slow", "--report", str(report_path), "--json"],
                provider=provider, driver=driver) == 0
    assert "Long Running Requests" in report_path.read_text(encoding="utf-8")
    assert "TOPSECRET" not in report_path.read_text(encoding="utf-8")
