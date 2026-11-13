import json
from dataclasses import asdict, replace
from html import unescape
from pathlib import Path
from typing import Any

import pytest
import yaml

import dba.cli as cli_module
from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider, MockCredentialProvider, Mode
from dba.errors import ToolkitError
from dba.incident import build_incident_summary, build_priority_candidates, manual_action_decisions
from dba.oracle import OracleAdapter
from dba.oracle_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    LongRunningCollector,
)
from dba.oracle_identity import OraclePrincipal
from dba.oracle_slow import (
    blocking_graph,
    build_slow_phase_report,
    build_slow_report,
    collect_slow_phase,
    diagnose_slow,
)
from dba.oracle_slow_reporter import render_markdown
from dba.presentation import render_emergency_triage, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.settings import OracleSettings
from dba.slow_profiles import emergency_collectors_for_adapter
from dba.versioning import resolve_profile_for_adapter

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] | None = None
        self.arraysize = 100
        self.prefetchrows = 100
        self.rows: list[dict[str, Any]] = []
        self.offset = 0
        self.closed = False

    def execute(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        name = self.connection.sql_names[sql]
        if name in self.connection.fail:
            raise self.connection.fail[name]
        self.rows = self.connection.data.get(name, [])
        self.offset = 0
        self.description = [(key.upper(),) for key in self.rows[0]] if self.rows else [("EMPTY",)]
        self.connection.calls.append((name, self.connection.call_timeout))
        self.connection.bound_parameters.append((name, parameters))

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        rows = self.rows[self.offset:self.offset + size]
        self.offset += len(rows)
        return [tuple(row.values()) for row in rows]

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
        self.bound_parameters: list[tuple[str, dict[str, Any] | None]] = []
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
    names = (*OracleAdapter.REQUIRED, "version_detect", "top_sql_11g", "top_sql_modern",
             "top_sql_container")
    sql_names = {registry.get(name, DatabaseType.ORACLE).text: name for name in names}
    data: dict[str, list[dict[str, Any]]] = {
        "session_identity": [{"session_user": "MONITOR"}],
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
        "top_sql_container": [{"con_id": 3, "con_name": "FREEPDB1"}],
        "top_sql_modern": [{"sql_id": "8s3k1", "child_number": 0, "plan_hash_value": 44,
                            "con_id": 3, "cpu_time": 55, "elapsed_time": 70,
                            "buffer_gets": 12, "disk_reads": 2, "executions": 3,
                            "ranking_value": 55}],
        "top_sql_11g": [{"sql_id": "8s3k1", "child_number": 0, "plan_hash_value": 44,
                         "cpu_time": 55, "elapsed_time": 70, "buffer_gets": 12,
                         "disk_reads": 2, "executions": 3, "ranking_value": 55}],
    }
    data["long_running"] = data["active_requests"]
    conn = FakeConnection(sql_names, data)
    driver = FakeDriver(conn)
    provider = MockCredentialProvider({"oracle-monitor": Credential("MONITOR", "TOPSECRET")})
    return instance, registry, provider, driver


def adapter_for_test(*, settings: OracleSettings | None = None) -> tuple[OracleAdapter, FakeDriver]:
    instance, registry, provider, driver = fixture()
    return OracleAdapter(instance, provider, registry, settings, Mode.DEVELOPMENT, driver=driver), driver


@pytest.mark.parametrize("settings,connect_ms,collector_ms", [
    (OracleSettings(), 30000, 10000),
    (OracleSettings(diagnostic_sql_timeout_seconds=4), 4000, 4000),
    (OracleSettings(diagnostic_sql_timeout_seconds=20, call_timeout=6), 6000, 6000),
])
def test_oracle_call_timeout_uses_global_effective_timeout(
    settings: OracleSettings, connect_ms: int, collector_ms: int,
) -> None:
    adapter, driver = adapter_for_test(settings=settings)
    assert settings.effective_sql_timeout(60) * 1000 == connect_ms
    with adapter:
        LongRunningCollector(adapter).collect(adapter.instance)
        assert driver.connection.calls[-1][1] == collector_ms


def test_oracle_fetch_call_timeout_uses_remaining_deadline_and_discards_connection(
    monkeypatch: pytest.MonkeyPatch, sql_deadline_clock: Any,
) -> None:
    adapter, driver = adapter_for_test(settings=OracleSettings(diagnostic_sql_timeout_seconds=4))
    observed_fetch_timeouts: list[int] = []
    with adapter:
        execute = FakeCursor.execute
        fetchmany = FakeCursor.fetchmany

        def timed_execute(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
            execute(cursor, sql, parameters)
            if cursor.connection.calls[-1][0] == "active_requests":
                sql_deadline_clock.advance(1)

        def timed_fetch(cursor: FakeCursor, size: int) -> list[tuple[Any, ...]]:
            if cursor.connection.calls[-1][0] == "active_requests":
                observed_fetch_timeouts.append(cursor.connection.call_timeout)
                sql_deadline_clock.advance(1.6)
            return fetchmany(cursor, size)

        monkeypatch.setattr(FakeCursor, "execute", timed_execute)
        monkeypatch.setattr(FakeCursor, "fetchmany", timed_fetch)
        result = adapter.execute_readonly(adapter.registry.get("active_requests", DatabaseType.ORACLE))

    assert not result.success and result.error_type == "FetchTimeout"
    assert observed_fetch_timeouts[0] == 3000
    assert 1399 <= observed_fetch_timeouts[1] <= 1401
    assert result.database_error is not None
    timeout = result.database_error["timeout"]
    assert timeout["execution_phase"] == "Fetch"
    assert timeout["cancellation_status"] == "CANCEL_STATUS_UNKNOWN"
    assert timeout["connection_status"] == "DISCARDED"
    assert driver.connection.closed


def test_oracle_one_execute_can_exceed_deadline_across_round_trips(
    monkeypatch: pytest.MonkeyPatch, sql_deadline_clock: Any,
) -> None:
    adapter, driver = adapter_for_test(settings=OracleSettings(diagnostic_sql_timeout_seconds=3))
    with adapter:
        execute = FakeCursor.execute

        def multi_trip(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
            execute(cursor, sql, parameters)
            assert cursor.connection.call_timeout == 3000
            for _ in range(2):
                sql_deadline_clock.advance(2)

        monkeypatch.setattr(FakeCursor, "execute", multi_trip)
        monkeypatch.setattr(FakeCursor, "fetchmany", lambda *_: pytest.fail("Execute exhausted Deadline"))
        result = adapter.execute_readonly(adapter.registry.get("active_requests", DatabaseType.ORACLE))
    assert result.error_type == "ExecuteTimeout"
    assert result.database_error is not None
    assert result.database_error["timeout"]["connection_status"] == "DISCARDED"
    assert driver.connection.closed


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
        assert driver.connection.calls[0][0] == "session_identity"
        assert adapter.current_user == "MONITOR"
        assert adapter.principal == OraclePrincipal.from_runtime("MONITOR")
    assert driver.connection.closed
    assert all(cursor.closed for cursor in driver.connection.cursors)
    assert "TOPSECRET" not in repr(adapter)


def test_oracle_session_identity_failure_precedes_monitoring_preflight_and_closes() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.fail["session_identity"] = RuntimeError("ORA-01017: invalid credentials")
    with pytest.raises(ToolkitError, match="session principal could not be verified"):
        adapter.connect()
    assert driver.connection.calls == []
    assert driver.connection.closed
    assert adapter.principal is None


def test_version_view_is_checked_by_its_reviewed_query_not_preflight() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_object_grants"] = [
        row for row in driver.connection.data["preflight_object_grants"]
        if row["table_name"] != "V_$VERSION"
    ]
    with adapter:
        assert adapter.get_server_version().startswith("19.")
        assert "probe_lock" not in driver.connection.sql_names.values()


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
    driver.connection.fail["preflight_privileges"] = RuntimeError("password=TOPSECRET")
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
    assert "docs/DATABASE_PERMISSIONS.md" in caught.value.database_error["guidance"]
    assert "TOPSECRET" not in repr(caught.value.database_error)
    assert driver.connection.closed


def test_preflight_rejects_privileged_role() -> None:
    adapter, driver = adapter_for_test()
    driver.connection.data["preflight_privileges"] = [{"kind": "ROLE", "privilege_name": "DBA"}]
    with pytest.raises(ToolkitError, match="unreviewed role"):
        adapter.connect()
    assert driver.connection.closed


def test_preflight_rejects_write_grant_and_does_not_probe_feature_views() -> None:
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
        assert adapter.blocking_script == "blocking"
        assert adapter.capabilities["lock_evidence"]
        assert not any(name.startswith("probe_") for name, _ in driver.connection.calls)


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
    driver.connection.fail["preflight_owned_objects"] = TimeoutError("password=TOPSECRET")
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


def _connected_oracle_slow_adapter() -> tuple[OracleAdapter, FakeDriver]:
    adapter, driver = adapter_for_test()
    adapter.instance = replace(adapter.instance, version_family="19c")
    adapter.connect()
    profile, _ = resolve_profile_for_adapter(adapter, "slow")
    adapter.slow_profile = profile
    return adapter, driver


def _assert_oracle_query_error(sample: dict[str, Any]) -> None:
    assert sample["success"] is False
    assert sample["error_type"] == "QueryDenied"
    assert "ORA-01031" in sample["error_message"]
    assert "insufficient privileges" in sample["error_message"]
    assert sample["database_error"]["vendor_code"] == "ORA-01031"


def test_oracle_slow_required_collector_failure_preserves_original_error_and_closes() -> None:
    adapter, driver = _connected_oracle_slow_adapter()
    driver.connection.fail["active_sessions"] = RuntimeError(
        "ORA-01031: insufficient privileges"
    )
    try:
        with pytest.raises(ToolkitError) as caught:
            diagnose_slow(adapter)
        assert caught.value.error_type == "QueryDenied"
        assert "ORA-01031" in str(caught.value)
        assert isinstance(caught.value.database_error, dict)
        assert caught.value.database_error["vendor_code"] == "ORA-01031"
        assert driver.connection.closed
        assert adapter._ready is False
    finally:
        adapter.disconnect()


def test_oracle_slow_optional_collector_failure_keeps_report_after_close() -> None:
    adapter, driver = _connected_oracle_slow_adapter()
    driver.connection.fail["previous_sql"] = RuntimeError(
        "ORA-01031: insufficient privileges"
    )
    try:
        report = diagnose_slow(adapter)
        assert driver.connection.closed
        assert adapter._ready is False
        sample = next(item for item in report.details["samples"]
                      if item["collector"] == "previous_sql")
        _assert_oracle_query_error(sample)
        assert report.details["incomplete"] is False
        assert report.details["data_visibility"] == "PARTIAL"
    finally:
        adapter.disconnect()


def test_oracle_staged_required_failure_keeps_error_in_emergency_report_after_close() -> None:
    adapter, driver = _connected_oracle_slow_adapter()
    driver.connection.fail["active_sessions"] = RuntimeError(
        "ORA-01031: insufficient privileges"
    )
    try:
        names = emergency_collectors_for_adapter(adapter)
        results = collect_slow_phase(adapter, names)
        assert driver.connection.closed
        assert adapter._ready is False
        report = build_slow_phase_report(adapter, results, emergency=True)
        sample = next(item for item in report.details["samples"]
                      if item["collector"] == "active_sessions")
        _assert_oracle_query_error(sample)
        assert report.details["incomplete"] is True
        assert report.details["data_visibility"] == "PARTIAL"
    finally:
        adapter.disconnect()


def test_oracle_staged_optional_failure_keeps_final_report_after_close() -> None:
    adapter, driver = _connected_oracle_slow_adapter()
    try:
        names = emergency_collectors_for_adapter(adapter)
        results = collect_slow_phase(adapter, names)
        assert all(result.success for result in results)
        emergency_report = build_slow_phase_report(adapter, results, emergency=True)
        assert emergency_report.details["emergency_evidence_complete"] is True
        driver.connection.fail["previous_sql"] = RuntimeError(
            "ORA-01031: insufficient privileges"
        )
        results = collect_slow_phase(adapter, ("previous_sql",), results)
        assert driver.connection.closed
        assert adapter._ready is False
        interim_report = build_slow_phase_report(adapter, results, emergency=True)
        assert interim_report.details["emergency_evidence_complete"] is True
        calls_after_failure = len(driver.connection.calls)
        results = collect_slow_phase(
            adapter,
            ("active_sessions", "active_requests", "transactions", "blocking", "previous_sql"),
            results,
        )
        assert len(driver.connection.calls) == calls_after_failure
        report = build_slow_phase_report(adapter, results, emergency=False)
        sample = next(item for item in report.details["samples"]
                      if item["collector"] == "previous_sql")
        _assert_oracle_query_error(sample)
        assert report.details["incomplete"] is False
        assert report.details["data_visibility"] == "PARTIAL"
    finally:
        adapter.disconnect()


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


def test_oracle_blocking_profiles_sample_blocker_serial_for_identity_recheck() -> None:
    for name in ("blocking", "blocking_basic"):
        sql = (ROOT / "sql" / "oracle" / f"{name}.sql").read_text(encoding="utf-8")
        assert "blocker_serial_number" in sql
        assert "LEFT JOIN v$session b" in sql


def test_idle_transaction_root_reference_and_report() -> None:
    instance, _, _, _ = fixture()
    sessions = [{"inst_id": 1, "sid": 10, "serial_number": 99,
                 "logon_time": "2026-01-01 00:00:00", "saddr": b"session10", "taddr": b"tx10",
                 "status": "INACTIVE", "username": "APP", "machine": "host",
                 "program": "client",
                 "prev_sql_id": "abc"},
                {"inst_id": 1, "sid": 20, "serial_number": 100, "status": "ACTIVE",
                 "state": "WAITING", "event": "enq: TX - row lock contention"}]
    results = (_result("active_sessions", sessions),
               _result("active_requests", [{"inst_id": 1, "sid": 20, "serial_number": 100,
                                            "duration_seconds": 70, "sql_text": "SELECT 1"}]),
               _result("transactions", [{"inst_id": 1, "sid": 10,
                                         "serial_number": 99, "logon_time": "2026-01-01 00:00:00",
                                         "saddr": b"session10", "taddr": b"tx10",
                                         "transaction_addr": b"tx10", "xidusn": 1, "xidslot": 2,
                                         "xidsqn": 3,
                                         "transaction_start_time": "2026-01-01",
                                         "transaction_age_seconds": 90, "used_ublk": 3,
                                         "used_urec": 4}]),
               _result("blocking", [{"inst_id": 1, "blocked_sid": 20,
                                     "blocked_serial_number": 100,
                                     "blocker_inst_id": 1, "blocker_sid": 10,
                                     "blocker_serial_number": 99}]),
               _result("previous_sql", [{"inst_id": 1, "sid": 10,
                                         "previous_sql_text": "SELECT 2"}]))
    report = build_slow_report(instance, results, OracleSettings(), "19.0")
    titles = {finding.title for finding in report.findings}
    assert {"BLOCKING", "ROOT_BLOCKER", "IDLE_TRANSACTION",
            "WAITING_SQL"} <= titles
    assert "LONG_RUNNING_SQL" not in titles
    root = next(f for f in report.findings if f.title == "ROOT_BLOCKER")
    assert root.reference_commands[0].manual_only
    assert not hasattr(root.reference_commands[0], "execute")
    assert root.reference_commands[0].text == "ALTER SYSTEM KILL SESSION '10,99' IMMEDIATE;"
    assert root.evidence["transaction_id"] == "1:2:3"
    assert root.evidence["unfinished_transaction_confirmed"] is True
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


def _oracle_chain_results(*, identity_complete: bool = True) -> tuple[CollectorResult, ...]:
    root = {"inst_id": 1, "sid": 227, "serial_number": 37872, "status": "INACTIVE",
            "username": "APP", "machine": "app-host", "program": "client",
            "logon_time": "2026-01-01 00:00:00", "saddr": b"root-session", "taddr": b"root-tx"}
    if identity_complete:
        tx = {"inst_id": 1, "sid": 227, "serial_number": 37872,
              "logon_time": root["logon_time"], "saddr": root["saddr"], "taddr": root["taddr"],
              "transaction_addr": root["taddr"], "xidusn": 4, "xidslot": 2, "xidsqn": 781,
              "transaction_start_time": "2026-01-01 00:00:02", "transaction_age_seconds": 21}
    else:
        tx = {"inst_id": 1, "sid": 227, "serial_number": 37872,
              "transaction_start_time": "2026-01-01 00:00:02", "transaction_age_seconds": 21}
    sessions = [root,
                {"inst_id": 1, "sid": 58, "serial_number": 38001, "status": "ACTIVE",
                 "state": "WAITING", "event": "enq: TX - row lock contention"},
                {"inst_id": 1, "sid": 46, "serial_number": 38002, "status": "ACTIVE",
                 "state": "WAITING", "event": "enq: TX - row lock contention"},
                {"inst_id": 1, "sid": 55, "serial_number": 38003, "status": "ACTIVE",
                 "state": "WAITING", "event": "PL/SQL lock timer"},
                {"inst_id": 1, "sid": 57, "serial_number": 38004, "status": "ACTIVE",
                 "state": "WAITING", "event": "db file sequential read"}]
    requests = [
        {"inst_id": 1, "sid": 58, "serial_number": 38001, "duration_seconds": 20,
         "sql_text": "SELECT id FROM DBA_INCIDENT_LAB WHERE id=:1 FOR UPDATE WAIT 310"},
        {"inst_id": 1, "sid": 46, "serial_number": 38002, "duration_seconds": 19,
         "sql_text": "SELECT id FROM DBA_INCIDENT_LAB WHERE id=:1 FOR UPDATE WAIT 310"},
        {"inst_id": 1, "sid": 55, "serial_number": 38003, "duration_seconds": 21,
         "state": "WAITING", "event": "PL/SQL lock timer",
         "sql_text": "BEGIN DBMS_SESSION.SLEEP(:1); END;", "sql_text_truncated": 0},
        {"inst_id": 1, "sid": 57, "serial_number": 38004, "duration_seconds": 15,
         "state": "WAITING", "event": "db file sequential read", "sql_text": "SELECT * FROM T"},
    ]
    edges = [{"inst_id": 1, "blocked_sid": 58, "blocked_serial_number": 38001,
              "blocker_inst_id": 1, "blocker_sid": 227, "blocker_serial_number": 37872},
             {"inst_id": 1, "blocked_sid": 46, "blocked_serial_number": 38002,
              "blocker_inst_id": 1, "blocker_sid": 58, "blocker_serial_number": 38001}]
    return (_result("active_sessions", sessions), _result("active_requests", requests),
            _result("transactions", [tx]), _result("blocking", edges),
            _result("previous_sql", []))


def test_oracle_root_gate_requires_correlated_transaction_identity() -> None:
    instance, _, _, _ = fixture()
    unsafe = build_slow_report(instance, _oracle_chain_results(identity_complete=False),
                               OracleSettings(), "23.0.0.0.0")
    root = next(item for item in unsafe.findings if item.title == "ROOT_BLOCKER")
    assert not root.reference_commands
    assert root.evidence["manual_action"]["status"] == "SUPPRESSED"
    assert "事务证据未通过" in root.evidence["manual_action"]["reason"]
    assert "SADDR matches" in root.evidence["manual_action"]["reason"]
    assert manual_action_decisions(unsafe)[0]["status"] == "SUPPRESSED"


def test_oracle_chain_victims_sleep_and_long_sql_are_separate() -> None:
    instance, _, _, _ = fixture()
    report = build_slow_report(instance, _oracle_chain_results(), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.evidence["blocked_count"] == 2
    assert root.evidence["direct_blocked_count"] == 1
    assert root.evidence["indirect_blocked_count"] == 1
    assert root.evidence["unfinished_transaction_confirmed"] is True
    assert root.evidence["transaction_id"] == "4:2:781"
    assert root.reference_commands[0].text == "ALTER SYSTEM KILL SESSION '227,37872' IMMEDIATE;"
    assert [row["session_key"] for row in report.details["blocked_victims"]] == ["1:46", "1:58"]
    assert [row["sid"] for row in report.details["active_waits"]] == [55]
    assert [row["sid"] for row in report.details["long_running"]] == [57]
    assert "KILL SESSION" not in " ".join(row["sql_text"] for row in report.details["long_running"])


def test_oracle_transaction_observation_is_separate_from_identity_verification() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    transactions = [dict(row) for row in results[2].rows]
    transactions[0]["taddr"] = b"different-transaction"
    results[2] = _result("transactions", transactions)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    evidence = root.evidence
    assert evidence["transaction_observed"] is True
    assert evidence["transaction_identity_verified"] is False
    assert evidence["unfinished_transaction_confirmed"] is None
    assert evidence["transaction_id"] == "4:2:781"
    assert evidence["transaction_age_seconds"] == 21
    assert evidence["manual_action"]["status"] == "SUPPRESSED"
    assert "TADDR matches transaction ADDR" in evidence["manual_action"]["reason"]
    assert build_incident_summary(report).root_cause_candidate["open_transaction"] == "UNKNOWN"


def test_oracle_suppression_reports_only_missing_session_identity_fields() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    sessions = [dict(row) for row in results[0].rows]
    sessions[0]["logon_time"] = None
    results[0] = _result("active_sessions", sessions)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    reason = root.evidence["manual_action"]["reason"]
    assert "LOGON_TIME" in reason
    assert "SERIAL#" not in reason
    assert "logon time matches" in reason


def test_oracle_missing_transaction_row_is_unconfirmed_not_no() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    results[2] = _result("transactions", [])
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.evidence["transaction_observed"] is False
    assert root.evidence["transaction_identity_verified"] is None
    assert root.evidence["unfinished_transaction_confirmed"] is None
    assert build_incident_summary(report).root_cause_candidate["open_transaction"] == "UNKNOWN"
    results[2] = replace(_result("transactions", []), success=False, error_type="QueryFailed")
    incomplete = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    incomplete_root = next(item for item in incomplete.findings if item.title == "ROOT_BLOCKER")
    assert incomplete_root.evidence["transaction_observed"] is None
    assert incomplete_root.evidence["transaction_identity_verified"] is None
    assert incomplete_root.evidence["unfinished_transaction_confirmed"] is None


def test_oracle_truncated_transaction_sample_is_observed_but_not_verified() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    results[2] = replace(_result("transactions", [dict(results[2].rows[0])]), truncated=True)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.evidence["transaction_observed"] is True
    assert root.evidence["transaction_identity_verified"] is False
    assert root.evidence["unfinished_transaction_confirmed"] is None
    assert "transaction sample complete" in root.evidence["manual_action"]["reason"]
    assert not root.reference_commands


def test_oracle_transaction_identity_is_instance_qualified() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    transactions = [dict(row) for row in results[2].rows]
    transactions.append({**transactions[0], "inst_id": 2, "sid": 227,
                         "serial_number": 991, "saddr": b"other-session",
                         "taddr": b"other-tx", "transaction_addr": b"other-tx",
                         "xidusn": 9, "xidslot": 9, "xidsqn": 9})
    results[2] = _result("transactions", transactions)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.evidence["session_key"] == "1:227"
    assert root.evidence["transaction_id"] == "4:2:781"
    assert root.evidence["transaction_identity_verified"] is True
    assert root.reference_commands


def test_oracle_raw_address_bytes_and_hex_values_compare_semantically() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    transactions = [dict(row) for row in results[2].rows]
    transactions[0]["saddr"] = b"root-session".hex().upper()
    transactions[0]["taddr"] = b"root-tx".hex().upper()
    transactions[0]["transaction_addr"] = b"root-tx".hex().upper()
    results[2] = _result("transactions", transactions)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.evidence["transaction_identity_verified"] is True
    assert root.reference_commands
    assert report.details["roots"][0]["session_address"] == "[REDACTED OBJECT]"


def test_oracle_previous_sql_context_is_explicit_and_duration_matches_html() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    sessions = [dict(row) for row in results[0].rows]
    sessions[0].update(sql_id=None, prev_sql_id="prev-176")
    results[0] = _result("active_sessions", sessions)
    requests = [dict(row) for row in results[1].rows]
    requests[2]["duration_seconds"] = 9
    requests[2]["duration_ms"] = 10000
    results[1] = _result("active_requests", requests)
    results[4] = _result("previous_sql", [{"inst_id": 1, "sid": 227,
                                           "serial_number": 37872,
                                           "logon_time": "2026-01-01 00:00:00",
                                           "prev_sql_id": "prev-176",
                                           "previous_sql_text": "UPDATE T SET C=1"}])
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    report.details["manual_action_decisions"] = manual_action_decisions(report)
    candidates = build_priority_candidates(report)
    report.details["priority_candidates"] = candidates
    summary = build_incident_summary(report)
    cli = render_emergency_triage(report, candidates, capture_started_at="sample")
    model = build_report_model(report, summary)
    html = HtmlReportRenderer().render(model)
    context = next(section for section in model.sections
                   if section.section_id == "oracle-root-sql-context")
    payload = render_json({"report": asdict(report), "incident_summary": asdict(summary),
                           "priority_candidates": candidates})
    assert "PREV_SQL_ID：prev-176" in cli
    assert "上一条 SQL：UPDATE T SET C=1" in cli
    assert "上一条 SQL 不等于一定是持锁 SQL" in cli
    previous_sql_index = context.tables[0].headers.index("Previous SQL / Availability")
    assert context.tables[0].rows[0][previous_sql_index] == "UPDATE T SET C=1"
    assert "UPDATE T SET C=1" in html
    assert '"previous_sql": "UPDATE T SET C=1"' in payload
    assert "运行时间：9 秒" in cli
    assert "9" in html
    assert "2026-01-01 00:00:02" in html
    candidate = candidates[0]
    assert candidate["identity_quality"] == "VERIFIED_AT_SAMPLE"
    assert candidate["transaction_identity_quality"] == "VERIFIED"
    assert candidate["manual_action_status"] == "AVAILABLE"
    assert "采样时核验通过" in cli
    assert "执行前必须实时复核" in cli
    transaction_section = next(section for section in model.sections
                               if section.section_id == "transaction-analysis")
    transaction_row = transaction_section.tables[0].rows[0]
    assert transaction_row[2].startswith("2026-01-01 00:00:02")
    assert transaction_row[3] == "V$TRANSACTION.START_DATE"
    assert transaction_row[4] == "00:00:21"
    assert '"transaction_start_time_source": "V$TRANSACTION.START_DATE"' in payload
    assert "时间来源：V$TRANSACTION.START_DATE" in cli
    current_sql_index = context.tables[0].headers.index("Current SQL / Availability")
    assert "inactive" in context.tables[0].rows[0][current_sql_index]


def test_oracle_suppressed_root_keeps_sample_identity_separate_from_action_status() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    tx = dict(results[2].rows[0])
    tx["taddr"] = b"conflicting-tx"
    results[2] = _result("transactions", [tx])
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    candidate = build_priority_candidates(report)[0]
    assert candidate["identity_quality"] == "VERIFIED_AT_SAMPLE"
    assert candidate["transaction_identity_quality"] == "UNVERIFIED"
    assert candidate["manual_action_status"] == "SUPPRESSED"
    report.details["manual_action_decisions"] = manual_action_decisions(report)
    cli = render_emergency_triage(report, (candidate,), capture_started_at="sample")
    assert "采样时核验通过" in cli
    assert "事务归属：未核验" in cli
    assert "人工处置参考：未生成" in cli
    assert "具体命令：暂不生成" in cli


def test_oracle_cli_collects_previous_sql_before_first_emergency_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _, _, driver = fixture()

    class RuntimeProvider:
        def get_credential(self, _alias: str) -> Credential:
            return Credential("MONITOR", "TEST_ONLY_SECRET")

    results = list(_oracle_chain_results())
    root_session = dict(results[0].rows[0])
    root_session.update(sql_id=None, prev_sql_id="previous-227")
    results[0] = _result("active_sessions", [root_session, *results[0].rows[1:]])
    previous = _result("previous_sql", [{"inst_id": 1, "sid": 227, "serial_number": 37872,
                                          "logon_time": root_session["logon_time"],
                                          "prev_sql_id": "previous-227",
                                          "previous_sql_text":
                                          "UPDATE INCIDENT_LAB SET VALUE=2 WHERE ID=1"}])
    core_results = tuple(result for result in results if result.collector_name != "previous_sql")
    calls: list[tuple[str, ...]] = []

    def fake_collect(_adapter: Any, names: tuple[str, ...],
                     existing: tuple[CollectorResult, ...] = ()) -> tuple[CollectorResult, ...]:
        calls.append(names)
        if not existing:
            return core_results
        if names == ("previous_sql",):
            return (*existing, previous)
        return existing

    def fake_build(adapter: Any, collected: tuple[CollectorResult, ...], *, emergency: bool) -> Any:
        report = build_slow_report(adapter.instance, collected, adapter.settings,
                                   "19.0.0.0.0", rac_enabled=False)
        report.details.update({"emergency_evidence_complete": True,
                               "emergency_evidence_status": "COMPLETE",
                               "incomplete": False,
                               "supplemental_not_collected": () if not emergency else
                               ("previous_sql",) if not any(
                                   row.collector_name == "previous_sql" for row in collected) else ()})
        return report

    monkeypatch.setattr(cli_module, "collect_oracle_slow_phase", fake_collect)
    monkeypatch.setattr(cli_module, "build_oracle_slow_phase_report", fake_build)
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        **asdict(instance), "db_type": "oracle", "version_family": "19c"}]}), encoding="utf-8")
    report_path = tmp_path / "oracle-slow.html"
    prefix = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql")]
    result = main([*prefix, "diagnose", instance.id, "slow", "--report", str(report_path)],
                  provider=RuntimeProvider(), driver=driver)
    captured = capsys.readouterr()
    output = captured.out
    assert result == 0, f"stdout={output} stderr={captured.err}"
    assert "上一条 SQL：UPDATE INCIDENT_LAB SET VALUE=2 WHERE ID=1" in output, repr(output)
    assert "TEST_ONLY_SECRET" not in output
    assert calls[0] == ("active_sessions", "active_requests", "transactions", "blocking")
    assert calls[1] == ("previous_sql",)


def test_oracle_transaction_start_time_is_not_inferred_when_missing() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    tx = dict(results[2].rows[0])
    tx["transaction_start_time"] = None
    tx["transaction_start_time_text"] = "12:28:53"
    results[2] = _result("transactions", [tx])
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER").evidence
    assert root["transaction_start_time"] is None
    assert root["transaction_age_seconds"] == 21
    candidate = build_priority_candidates(report)[0]
    assert candidate["transaction_start_time"] is None
    summary = build_incident_summary(report)
    model = build_report_model(report, summary)
    transaction_section = next(section for section in model.sections
                                if section.section_id == "transaction-analysis")
    assert transaction_section.tables[0].rows[0][2] == "未采集（事务记录缺少开始时间）"
    assert transaction_section.tables[0].rows[0][3] == "V$TRANSACTION.START_DATE"
    cli = render_emergency_triage(report, build_priority_candidates(report), capture_started_at="sample")
    assert "事务开始时间：未知（V$TRANSACTION.START_DATE 未提供有效值）" in cli
    assert "不作为完整开始时间" in cli
    assert "2025-12-31 23:59:41" not in HtmlReportRenderer().render(model)


def test_oracle_priority_candidate_reason_join_has_clean_chinese_punctuation() -> None:
    instance, _, _, _ = fixture()
    report = build_slow_report(instance, _oracle_chain_results(), OracleSettings(), "23.0.0.0.0")
    candidate = build_priority_candidates(report)[0]
    assert "。；" not in candidate["reason"]
    assert candidate["reason"].endswith("会话与未结束事务证据关联")


def test_oracle_current_and_previous_sql_ids_remain_distinct_when_text_missing() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    sessions = [dict(row) for row in results[0].rows]
    sessions[0].update(sql_id="current-176", prev_sql_id="previous-176")
    results[0] = _result("active_sessions", sessions)
    requests = [dict(row) for row in results[1].rows]
    requests.append({"inst_id": 1, "sid": 227, "serial_number": 37872,
                     "sql_id": "current-176", "sql_text": "UPDATE CURRENT_TABLE SET C=1"})
    results[1] = _result("active_requests", requests)
    results[4] = _result("previous_sql", [{"inst_id": 1, "sid": 227,
                                           "serial_number": 37872,
                                           "prev_sql_id": "previous-176",
                                           "previous_sql_text": None}])
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER").evidence
    assert root["current_sql_id"] == "current-176"
    assert root["current_sql"] == "UPDATE CURRENT_TABLE SET C=1"
    assert root["prev_sql_id"] == "previous-176"
    assert root["previous_sql"] is None
    assert "SQL text not obtained" in root["previous_sql_reason"]


def test_oracle_previous_sql_failure_does_not_change_reference_gate() -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    results[4] = replace(results[4], success=False, error_type="QueryFailed")
    report = build_slow_report(instance, tuple(results), OracleSettings(), "23.0.0.0.0")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert root.reference_commands
    assert root.evidence["manual_action"]["status"] == "AVAILABLE"


def test_oracle_raw_address_normalization_preserves_identity_bytes() -> None:
    adapter, _ = adapter_for_test()
    assert adapter._safe_value(b"\x00\xAF") == "00AF"
    assert adapter._safe_value(bytearray(b"\x00\xAF")) == "00AF"


def test_oracle_suppression_is_shared_by_cli_html_and_json() -> None:
    instance, _, _, _ = fixture()
    report = build_slow_report(instance, _oracle_chain_results(identity_complete=False),
                               OracleSettings(), "23.0.0.0.0")
    report.details["manual_action_decisions"] = manual_action_decisions(report)
    summary = build_incident_summary(report)
    cli = render_emergency_triage(report, capture_started_at="sample")
    model = build_report_model(report, summary)
    html = HtmlReportRenderer().render(model)
    payload = render_json({"report": report, "incident_summary": summary,
                           "manual_action_decisions": report.details["manual_action_decisions"]})
    assert "具体命令：暂不生成" in cli
    assert "TADDR matches transaction ADDR" in cli
    assert "ALTER SYSTEM KILL SESSION '227,37872' IMMEDIATE;" not in cli
    assert "ALTER SYSTEM KILL SESSION '227,37872' IMMEDIATE;" not in html
    assert "ALTER SYSTEM KILL SESSION '227,37872' IMMEDIATE;" not in payload
    assert '"status": "SUPPRESSED"' in payload


def test_oracle_allowed_reference_is_identical_across_cli_html_and_json() -> None:
    instance, _, _, _ = fixture()
    report = build_slow_report(instance, _oracle_chain_results(), OracleSettings(), "23.0.0.0.0")
    report.details["manual_action_decisions"] = manual_action_decisions(report)
    summary = build_incident_summary(report)
    cli = render_emergency_triage(report, capture_started_at="sample")
    model = build_report_model(report, summary)
    html = HtmlReportRenderer().render(model)
    payload = render_json({"report": report, "incident_summary": summary,
                           "manual_action_decisions": report.details["manual_action_decisions"]})
    command = "ALTER SYSTEM KILL SESSION '227,37872' IMMEDIATE;"
    assert report.details["manual_action_decisions"][0]["commands"] == (command,)
    assert summary.recovery_actions[0].reference_command == command
    recovery = next(section for section in model.sections if section.section_id == "immediate-recovery")
    assert recovery.code_blocks and recovery.code_blocks[0].content == command
    assert command in cli, repr(cli[-1500:])
    assert command in unescape(html)
    assert command in payload
    assert "Oracle Profile 路由版本" in cli and "产品发行名称未独立核实" in cli
    assert "事务 DMV" not in html and "CPU、logical reads、reads、writes" not in html


@pytest.mark.parametrize("change", ["missing_serial", "duplicate_session", "duplicate_tx",
                                     "address_mismatch", "request_identity_conflict", "rac"])
def test_oracle_identity_conflicts_never_issue_reference(change: str) -> None:
    instance, _, _, _ = fixture()
    results = list(_oracle_chain_results())
    sessions = [dict(row) for row in results[0].rows]
    requests = [dict(row) for row in results[1].rows]
    transactions = [dict(row) for row in results[2].rows]
    if change == "missing_serial":
        sessions[0]["serial_number"] = None
    elif change == "duplicate_session":
        sessions.append(dict(sessions[0]))
    elif change == "duplicate_tx":
        transactions.append(dict(transactions[0]))
    elif change == "address_mismatch":
        transactions[0]["taddr"] = b"other-tx"
    elif change == "request_identity_conflict":
        requests.append({"inst_id": 1, "sid": 227, "serial_number": 999,
                         "duration_seconds": 1, "sql_text": "SELECT 1"})
    results[0] = _result("active_sessions", sessions)
    results[1] = _result("active_requests", requests)
    results[2] = _result("transactions", transactions)
    report = build_slow_report(instance, tuple(results), OracleSettings(), "19.0",
                               rac_enabled=change == "rac")
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert not root.reference_commands


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
        **asdict(instance), "db_type": "oracle", "version_family": "19c"}]}), encoding="utf-8")
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
    assert json.loads(capsys.readouterr().out)["incident_summary"]["impact"]["long_running"] == 1
    page = report_path.read_text(encoding="utf-8")
    assert 'id="sql-resource-001"' in page and 'id="page-sql"' in page and "运行时长估计" in page
    assert "TOPSECRET" not in page
    _, _, _, driver = fixture()
    assert main([*prefix, "diagnose", instance.id, "top-sql", "--json"],
                provider=provider, driver=driver) == 0
    top_sql = json.loads(capsys.readouterr().out)["top_sql"]
    assert top_sql["ranking_metric"] == "cpu_time"
    assert top_sql["metric_scope"] == "CHILD_CURSOR"
    assert top_sql["collection_scope"] == "DATABASE"
    assert top_sql["items"][0]["sql_identity"]["fields"] == {
        "sql_id": "8s3k1", "child_number": 0, "inst_id": 1, "con_id": 3,
    }
    assert driver.connection.bound_parameters[-1] == (
        "top_sql_modern", {"metric": "cpu_time", "top_n": 10, "con_id": 3},
    )
