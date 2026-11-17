from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from test_mysql import FakeConnection, FakeCursor, adapter

from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.credentials import Credential
from dba.inventory import ValidationError
from dba.mysql import _DeadlineSocket
from dba.sql_timeout import SqlDeadline, SqlDeadlineExpired
from scripts.acceptance import verify_mysql_timeout as harness


def test_single_asset_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "target.yaml"
    path.touch()
    target, _ = adapter()
    asset = replace(target.instance, id="mysql84", host="127.0.0.1", environment="test")
    monkeypatch.setattr(harness, "TARGET", path)
    mock = SimpleNamespace(instances=[asset])
    monkeypatch.setattr(harness.Inventory, "load", lambda _: mock)
    assert harness.load_asset(path) == asset
    info_schema_asset = replace(asset, database="information_schema")
    mock.instances = [info_schema_asset]
    assert harness.load_asset(path) == info_schema_asset
    another_database = replace(asset, database="existing_lab_schema")
    mock.instances = [another_database]
    assert harness.load_asset(path) == another_database
    mock.instances = [replace(asset, database=None)]
    assert harness.load_asset(path).database == "information_schema"
    mock.instances = [asset]
    for invalid in (replace(asset, environment="production"), replace(asset, port=3307),
                    replace(asset, host="example.invalid"), replace(asset, enabled=False)):
        mock.instances = [invalid]
        with pytest.raises(harness.GateError):
            harness.load_asset(path)
    mock.instances = [asset, asset]
    with pytest.raises(harness.GateError, match="exactly one"):
        harness.load_asset(path)
    with pytest.raises(harness.GateError, match="designated"):
        harness.load_asset(tmp_path)


def test_fixtures_are_registered_and_tampering_is_rejected(tmp_path: Path) -> None:
    registry = harness.stage_registry(tmp_path)
    script = registry.get("timeout_session", DatabaseType.MYSQL)
    assert script.metadata.timeout_seconds == 1 and "MAX_EXECUTION_TIME" not in script.text
    assert script.metadata.max_rows == 2 and script.metadata.max_result_bytes == 1024
    fetch = registry.get("timeout_fetch", DatabaseType.MYSQL)
    registry.authorize(fetch, DatabaseType.MYSQL)
    assert fetch.text.strip() == harness.FIXTURE_SQL["timeout_fetch"]
    assert fetch.metadata.timeout_seconds == 2 and fetch.metadata.max_rows == 4
    (tmp_path / "mysql/timeout_session.sql").write_text("SELECT 2", encoding="utf8")
    with pytest.raises(ValidationError, match="changed"):
        registry.authorize(script, DatabaseType.MYSQL)


def test_fetch_streaming_is_an_explicit_single_adapter_opt_in(tmp_path: Path) -> None:
    target, _ = adapter()
    target.registry = harness.stage_registry(tmp_path)
    assert "timeout_fetch" not in target.STREAMING_SCRIPTS
    class_default = target.STREAMING_SCRIPTS
    harness.enable_fetch_fixture_streaming(target)
    assert "timeout_fetch" in target.STREAMING_SCRIPTS
    assert target.STREAMING_SCRIPTS is not class_default
    assert "timeout_fetch" not in type(target).STREAMING_SCRIPTS


def test_observation_requires_active_then_same_ended_event() -> None:
    obs = harness.Observation("SELECT 1")
    obs.sample([(1, 2, 3, "SELECT 1")], historical=True)
    assert obs.evidence()["status"] == "INSUFFICIENT"
    obs.sample([(1, 2, None, "SELECT 1")])
    assert obs.evidence()["status"] == "RUNNING"
    obs.sample([])
    obs.sample([(1, 3, 4, "SELECT 1"), (1, 2, 3, "SELECT 2")])
    obs.sample([(1, 2, 0, "SELECT 1")])
    assert obs.evidence()["status"] == "HISTORY_NOT_VISIBLE"
    obs.sample([(1, 2, 3, "SELECT 1")], historical=True)
    assert obs.evidence()["status"] == "ENDED"
    assert obs.evidence()["end_event_id"] == 3


def _controlled_observer_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [0.0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


def _observer_consumers() -> dict[str, str]:
    return {"events_statements_current": "YES", "events_statements_history": "YES"}


def test_observer_reports_running_event_without_claiming_it_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()
    _controlled_observer_clock(monkeypatch)

    def read(_: Any, sql: str, args: tuple[int, ...], until: float) -> list[tuple[Any, ...]]:
        if sql == harness.CURRENT:
            return [(55, 8, None, "SELECT 1")]
        if sql == harness.HISTORY:
            return []
        if sql == harness.THREAD:
            return [(55, "YES", "YES")]
        raise AssertionError(sql)

    monkeypatch.setattr(harness, "observer_read", read)
    evidence = harness.observe(observer, 55, 700, harness.Observation("SELECT 1", 55),
                              _observer_consumers())
    assert evidence["status"] == "RUNNING"
    assert evidence["end_event_id"] is None
    assert evidence["connection_visibility"] == "PRESENT"


def test_observer_reports_connection_disappeared_without_calling_it_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()
    _controlled_observer_clock(monkeypatch)
    current_calls = 0

    def read(_: Any, sql: str, args: tuple[int, ...], until: float) -> list[tuple[Any, ...]]:
        nonlocal current_calls
        if sql == harness.CURRENT:
            current_calls += 1
            return [(55, 8, None, "SELECT 1")] if current_calls == 1 else []
        if sql == harness.HISTORY:
            return []
        if sql == harness.THREAD:
            return [(55, "YES", "YES")] if current_calls == 1 else []
        raise AssertionError(sql)

    monkeypatch.setattr(harness, "observer_read", read)
    evidence = harness.observe(observer, 55, 700, harness.Observation("SELECT 1", 55),
                              _observer_consumers())
    assert evidence["status"] == "CONNECTION_DISAPPEARED"
    assert evidence["end_event_id"] is None
    assert evidence["connection_visibility"] == "DISAPPEARED"


@pytest.mark.parametrize(("rows", "expected", "adapter_detached"), [
    ([], "ABSENT", True),
    ([], "ABSENT", False),
    ([(55, "YES", "YES")], "PRESENT", False),
    ([(56, "YES", "YES")], "CONNECTION_ID_REBOUND", True),
    ([(55, "YES", "YES"), (55, "YES", "YES")], "UNKNOWN", True),
])
def test_explicit_session_visibility_probe_is_separate_from_query_stop(
    monkeypatch: pytest.MonkeyPatch, rows: list[tuple[Any, ...]], expected: str,
    adapter_detached: bool,
) -> None:
    observer, _ = adapter()
    calls: list[tuple[str, tuple[int, ...]]] = []

    def read(_: Any, sql: str, args: tuple[int, ...] = (), until: float | None = None) -> list[tuple[Any, ...]]:
        calls.append((sql, args))
        return rows

    monkeypatch.setattr(harness, "observer_read", read)
    evidence = harness.verify_database_session_visibility(
        observer, 700, 55, adapter_detached=adapter_detached,
    )
    assert calls == [(harness.THREAD, (700,))]
    assert evidence["status"] == expected
    if expected == "ABSENT":
        assert evidence["query_stop_confirmed"] is False
        assert evidence["observed_thread_id"] is None
        assert evidence["adapter_detached_at_probe"] is adapter_detached


def test_session_visibility_probe_errors_are_unknown_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(1142, "SELECT denied; password=fake-secret")

    monkeypatch.setattr(harness, "observer_read", fail)
    evidence = harness.verify_database_session_visibility(observer, 700, 55, adapter_detached=True)
    assert evidence["status"] == "UNKNOWN"
    assert evidence["error"]["vendor_code"] == 1142
    assert "fake-secret" not in repr(evidence)


def test_observer_reports_history_not_visible_when_event_disappears_but_thread_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()
    _controlled_observer_clock(monkeypatch)
    current_calls = 0

    def read(_: Any, sql: str, args: tuple[int, ...], until: float) -> list[tuple[Any, ...]]:
        nonlocal current_calls
        if sql == harness.CURRENT:
            current_calls += 1
            return [(55, 8, None, "SELECT 1")] if current_calls == 1 else []
        if sql == harness.HISTORY:
            return []
        if sql == harness.THREAD:
            return [(55, "YES", "YES")]
        raise AssertionError(sql)

    monkeypatch.setattr(harness, "observer_read", read)
    evidence = harness.observe(observer, 55, 700, harness.Observation("SELECT 1", 55),
                              _observer_consumers())
    assert evidence["status"] == "HISTORY_NOT_VISIBLE"
    assert evidence["end_event_id"] is None
    assert evidence["connection_visibility"] == "PRESENT"


def test_observer_distinguishes_missing_event_and_rebound_connection_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()
    _controlled_observer_clock(monkeypatch)
    monkeypatch.setattr(
        harness, "observer_read",
        lambda _adapter, sql, _args, _until: [(99, "YES", "YES")] if sql == harness.THREAD else [],
    )
    evidence = harness.observe(observer, 55, 700, harness.Observation("SELECT 1", 55),
                              _observer_consumers())
    assert evidence["status"] == "INSUFFICIENT"
    assert evidence["connection_visibility"] == "REBOUND"
    assert evidence["end_event_id"] is None


def test_disabled_consumer_and_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    target, _ = adapter()

    def deny(*args: Any) -> Any:
        raise RuntimeError(1142, "internal credential detail")

    monkeypatch.setattr(harness, "observer_read", deny)
    assert harness.observe(target, 3, 3, harness.Observation("SELECT 1"), {})[
        "reason"] == "required_statement_consumer_unavailable"
    result = harness.observe(target, 3, 3, harness.Observation("SELECT 1"), {
        "events_statements_current": "YES", "events_statements_history": "YES",
    })
    assert result["status"] == "INSUFFICIENT"
    assert len(result["observation_errors"]) == 1
    assert result["observation_errors"][0]["source"] == "current"
    assert result["observation_errors"][0]["vendor_code"] == 1142
    assert "internal credential detail" in result["observation_errors"][0]["message"]


def test_observer_uses_bound_target_thread_and_exact_history_key(monkeypatch: pytest.MonkeyPatch) -> None:
    target, _ = adapter()
    calls: list[tuple[str, tuple[int, ...]]] = []

    def read(_: Any, sql: str, args: tuple[int, ...], until: float) -> Any:
        calls.append((sql, args))
        assert until > harness.time.monotonic()
        return [(55, 8, None if sql == harness.CURRENT else 9, "SELECT 1")]

    monkeypatch.setattr(harness, "observer_read", read)
    assert harness.observe(target, 55, 900, harness.Observation("SELECT 1", 55), {
        "events_statements_current": "YES", "events_statements_history": "YES",
    })["status"] == "ENDED"
    assert calls == [(harness.CURRENT, (55,)), (harness.HISTORY, (55, 8))]


def test_extra_redaction_keeps_error_evidence_without_fixed_username() -> None:
    assert harness.hide_monitor_identity(
        {"vendor_code": 1142, "message": "denied to app_observer@localhost"},
        ("app_observer@localhost", "'app_observer'@'localhost'"),
    ) == {
        "vendor_code": 1142, "message": "denied to [REDACTED]",
    }
    assert harness.hide_monitor_identity({"current_user": "app_observer@localhost"}) == {
        "current_user": "[REDACTED]",
    }
    error = harness.safe_database_error(
        RuntimeError(1142, "SELECT denied to 'app_observer'; password=fake-secret"),
        ("'app_observer'",),
    )
    assert error["vendor_code"] == 1142 and "SELECT denied" in error["message"]
    assert "fake-secret" not in error["message"] and "app_observer" not in error["message"]


@pytest.mark.parametrize(("fail", "expected"), [
    (RuntimeError(3024, "Query execution was interrupted"), "SERVER_3024"),
    (TimeoutError("read timed out"), "CLIENT_SOCKET_TIMEOUT"),
    (SqlDeadlineExpired("Execute"), "CLIENT_DEADLINE_TIMEOUT"),
    (RuntimeError(2013, "Lost connection"), "CONNECTION_LOST"),
])
def test_real_adapter_path_with_fake_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: Exception, expected: str,
) -> None:
    target, driver = adapter()
    target.registry = harness.stage_registry(tmp_path)
    old = driver.connection
    for name in harness.NAMES:
        old.scripts[target.registry.get(name, DatabaseType.MYSQL).text] = name
    old.results["preflight"][0]["server_version"] = "8.4.11"
    old.results["preflight"][0]["monitor_user"] = "app_observer@localhost"
    old.results["timeout_identity"] = [{"connection_id": 9, "version": "8.4.11",
                                        "product": "MySQL Community Server - GPL", "session_limit_ms": 3750}]
    old.failures["timeout_session"] = fail
    with harness.capture_audit() as audit:
        audit.add_redactions(target.instance.credential_alias)
        target.connect()
        # FakeCursor does not emulate @@SESSION.max_execution_time values.
        # The real harness passes audit.records and checks that value on MySQL.
        connection, cid, baseline = harness.identity(target)
        assert connection is old and cid == 9
        trace: list[dict[str, Any]] = []

        clock = [0.0]
        monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])

        class ClockCarrier:
            _deadline = SqlDeadline.start(1)
            _phase = "Execute"

            def set_deadline(self, deadline: SqlDeadline, phase: str = "Execute") -> None:
                self._deadline = deadline
                self._phase = phase

            def sendall(self, data: bytes) -> None:
                pass

            def _expired_socket_error(self, exc: OSError) -> NoReturn:
                raise SqlDeadlineExpired(self._phase) from exc

        setattr(target, "_deadline_socket", ClockCarrier())
        execute = FakeCursor.execute

        def wire(cursor: FakeCursor, sql: str, parameters: Any = None) -> None:
            if sql == harness.SESSION_SET or "timeout_session_probe" in sql:
                target._deadline_socket.sendall(b"fake wire")  # type: ignore[union-attr]
                clock[0] += 0.125
            if "timeout_session_probe" in sql and isinstance(fail, TimeoutError):
                cursor.connection.executed.append("timeout_session")
                target._deadline_socket._expired_socket_error(fail)  # type: ignore[union-attr]
            execute(cursor, sql, parameters)

        with monkeypatch.context() as local:
            local.setattr(FakeCursor, "execute", wire)
            local.setattr(SqlDeadline, "start",
                          classmethod(lambda cls, seconds: cls(seconds, clock[0], lambda: clock[0])))
            result = harness.timed_query(target, old, "timeout_session", trace)
        assert harness.classify(result, trace) == expected
        assert result.database_error is not None
        assert result.database_error["timeout"]["execution_phase"] == "Execute"
        assert result.database_error["timeout"]["cancellation_status"] == (
            "CANCELLED_CONFIRMED" if expected == "SERVER_3024" else "CANCEL_STATUS_UNKNOWN"
        )
        assert result.database_error["timeout"]["connection_status"] == "DISCARDED"
        assert [e["operation"] for e in trace] == ["session", "select"]
        assert trace[0]["server_execution_limit_ms"] == 500
        assert trace[1]["remaining_before_ms"] <= trace[0]["remaining_before_ms"]
        assert "elapsed_ms" in trace[0]
        session_audit = next(item for item in reversed(audit.records)
                             if item.get("sql_script") == "timeout_session")
        assert harness.session_timing_valid(trace, session_audit)
        assert harness.server_first_window(trace, session_audit)["status"] == (
            "SERVER_BEFORE_CLIENT_WINDOW_OBSERVED"
        )
        assert not harness.session_timing_valid(trace[:1], session_audit)
        assert target._connection is None and target._session_identity is None and old.closed
        assert old.executed.count("timeout_session") == 1
        artifacts, parity = harness.render_evidence(target.instance, target.settings, baseline, result, audit.records)
        assert all(parity.values()), parity
        assert target.instance.credential_alias not in "".join(artifacts.values())
        driver.connection = FakeConnection(old.scripts)
        driver.connection.results["preflight"][0].update(server_version="8.4.11",
                                                         monitor_user="app_observer@localhost", connection_id=19)
        driver.connection.results["timeout_identity"] = [{**baseline.rows[0], "connection_id": 19}]
        target.connect()
        assert harness.identity(target)[1] == 19
        assert driver.connection.executed[:3] == ["preflight", "preflight_roles", "monitor_grants"]
        assert baseline.rows[0]["connection_id"] == 9
        target.disconnect()



@pytest.mark.parametrize("setup_delay", [0.150, 1.001])
def test_mysql_server_first_guard_accounts_for_actual_setup_delay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, setup_delay: float,
) -> None:
    """SET consumes the original deadline; insufficient budget prevents SELECT."""
    target, driver = adapter()
    target.registry = harness.stage_registry(tmp_path)
    old = driver.connection
    for name in harness.NAMES:
        old.scripts[target.registry.get(name, DatabaseType.MYSQL).text] = name
    old.results["preflight"][0]["server_version"] = "8.4.11"
    old.results["timeout_identity"] = [{"connection_id": 9, "version": "8.4.11",
                                       "product": "MySQL Community Server - GPL", "session_limit_ms": 3999}]
    target.connect()
    clock = [0.0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])

    class ClockCarrier:
        _deadline = SqlDeadline.start(1)
        _phase = "Execute"

        def set_deadline(self, deadline: SqlDeadline, phase: str = "Execute") -> None:
            self._deadline = deadline
            self._phase = phase

        def sendall(self, data: bytes) -> None:
            pass

        def _expired_socket_error(self, exc: OSError) -> NoReturn:
            raise SqlDeadlineExpired(self._phase) from exc

    setattr(target, "_deadline_socket", ClockCarrier())
    real_execute = FakeCursor.execute

    def delayed_setup(cursor: FakeCursor, sql: str, parameters: Any = None) -> None:
        if "timeout_session_probe" in sql:
            target._deadline_socket.sendall(b"fake wire")  # type: ignore[union-attr]
        real_execute(cursor, sql, parameters)
        if sql == harness.SESSION_SET:
            clock[0] += setup_delay

    trace: list[dict[str, Any]] = []
    with monkeypatch.context() as scoped:
        scoped.setattr(FakeCursor, "execute", delayed_setup)
        scoped.setattr(SqlDeadline, "start",
                       classmethod(lambda cls, seconds: cls(seconds, clock[0], lambda: clock[0])))
        outcome = harness.timed_query(target, old, "timeout_session", trace)
    assert old.server_limits[-1] == 500
    audit_record = {
        "client_remaining_before_session_ms": 1000,
        "server_execution_limit_ms": 500,
        "session_setup_reserve_ms": 250,
        "server_timeout_response_margin_ms": 250,
    }
    window = harness.server_first_window(trace, audit_record)
    if setup_delay < 1:
        assert outcome.success
        assert old.executed.count("timeout_session") == 1
        assert harness.session_timing_valid(trace, audit_record) is True
        assert window["status"] == "SERVER_BEFORE_CLIENT_WINDOW_OBSERVED"
        assert window["margin_ms"] >= 250
    else:
        assert old.executed.count("timeout_session") == 0
        assert [event["operation"] for event in trace] == ["session"]
        assert trace[0]["execute_status"] == "OK" and trace[0]["remaining_after_ms"] == 0
        assert harness.classify(outcome, trace) == "CLIENT_BUDGET_REFUSED"
        assert window["status"] == "NOT_OBSERVED"
        assert target._connection is None and old.closed
    target.disconnect()


def test_fetch_phase_kept_separate_in_classifier() -> None:
    now = datetime.now(timezone.utc)
    evidence = {"error_category": "TIMEOUT", "vendor_code": 3024, "message": "statement timed out",
                "timeout": {"cancellation_status": "CANCELLED_CONFIRMED",
                            "mechanism": "MYSQL_MAX_EXECUTION_TIME", "execution_phase": "Fetch"}}
    result = CollectorResult("mysql84", "fixture", now, now, 1000, (), 0, False, False,
                             database_error=evidence)
    assert harness.classify(result) == "SERVER_3024"
    assert result.database_error is not None
    assert result.database_error["timeout"]["execution_phase"] == "Fetch"


@pytest.mark.parametrize(("failure", "expected", "category", "cancellation"), [
    (RuntimeError(3024, "Query execution was interrupted; password=fake-secret"),
     "SERVER_3024", "TIMEOUT",
     "CANCELLED_CONFIRMED"),
    (TimeoutError("read timed out; password=fake-secret"), "CLIENT_SOCKET_TIMEOUT", "TIMEOUT",
     "CANCEL_STATUS_UNKNOWN"),
])
def test_fetch_timeout_uses_streaming_cursor_and_discards_incomplete_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception,
    expected: str, category: str, cancellation: str,
) -> None:
    from pymysql.cursors import SSCursor

    target, driver = adapter()
    driver.cursors = SimpleNamespace(SSCursor=SSCursor)
    target.registry = harness.stage_registry(tmp_path)
    script = target.registry.get("timeout_fetch", DatabaseType.MYSQL)
    old = driver.connection
    old.scripts[script.text] = "timeout_fetch"
    old.results["timeout_fetch"] = [{"probe": 1}, {"probe": 2}]
    old.results["server_info"] = [{"version": "8.4.11"}]
    target.connect()
    previous = target.execute_readonly(target.registry.get("server_info", DatabaseType.MYSQL))
    assert previous.success and previous.rows[0]["version"] == "8.4.11"

    harness.enable_fetch_fixture_streaming(target)
    clock = [0.0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])

    class ClockCarrier:
        _deadline: SqlDeadline | None = None
        _phase = "Execute"

        def set_deadline(self, deadline: SqlDeadline, phase: str = "Execute") -> None:
            self._deadline = deadline
            self._phase = phase

        def sendall(self, data: bytes) -> None:
            return None

        def _expired_socket_error(self, exc: OSError) -> NoReturn:
            raise SqlDeadlineExpired(self._phase) from exc

    setattr(target, "_deadline_socket", ClockCarrier())
    real_fetchmany = FakeCursor.fetchmany

    def fail_after_one_row(cursor: FakeCursor, size: int) -> list[tuple[Any, ...]]:
        if cursor.script_name == "timeout_fetch" and cursor.offset:
            raise failure
        return real_fetchmany(cursor, size)

    monkeypatch.setattr(FakeCursor, "fetchmany", fail_after_one_row)
    trace: list[dict[str, Any]] = []
    with harness.capture_audit() as audit:
        audit.add_redactions(target.instance.credential_alias)
        result = harness.timed_query(target, old, "timeout_fetch", trace)
        artifacts, parity = harness.render_evidence(
            target.instance, target.settings, previous, result, audit.records,
        )

    assert harness.classify(result, trace) == expected
    assert result.database_error is not None
    detail = result.database_error
    assert detail["error_category"] == category
    assert detail["timeout"]["cancellation_status"] == cancellation
    assert detail["timeout"]["execution_phase"] == "Fetch"
    assert [event["operation"] for event in trace] == ["session", "select", "fetch", "fetch"]
    assert trace[1]["execute_status"] == "OK"
    assert trace[1]["same_deadline_as_setup"] is True
    assert trace[1]["streaming_cursor_requested"] is True
    assert trace[2]["same_deadline_as_setup"] is True
    assert trace[3]["same_deadline_as_setup"] is True
    assert trace[2]["rows_received"] == 1 and trace[2]["fetch_status"] == "OK"
    assert trace[3]["fetch_status"] == "FAILED"
    assert old.cursor_classes[-1] is SSCursor
    assert old.executed.count("timeout_fetch") == 1
    assert result.success is False and result.rows == () and result.row_count == 0
    assert previous.success and previous.rows[0]["version"] == "8.4.11"
    assert all(parity.values()), parity
    assert "fake-secret" not in "".join(artifacts.values())
    assert target._connection is None and target._session_identity is None and old.closed
    assert old.lifecycle[-2:] == ["connection.close", "cursor.close"]
    with pytest.raises(harness.ToolkitError) as not_ready:
        target.execute_readonly(script)
    assert not_ready.value.error_type == "PreflightRequired"
    assert old.executed.count("timeout_fetch") == 1

    if expected == "SERVER_3024":
        new = FakeConnection(old.scripts)
        new.results["preflight"][0].update(connection_id=19, server_version="8.4.11")
        new.results["server_info"] = [{"version": "8.4.11"}]
        driver.connection = new
        target.connect()
        assert target.preflight_checks["connect"] and target.preflight_checks["performance_schema"]
        assert target.preflight_checks["privilege_classification"]
        assert new.executed[:3] == ["preflight", "preflight_roles", "monitor_grants"]
        independent = target.execute_readonly(target.registry.get("server_info", DatabaseType.MYSQL))
        assert independent.success and independent.rows[0]["version"] == "8.4.11"
        assert new.results["preflight"][0]["connection_id"] != 9
        target.disconnect()


def test_native_3024_and_observer_lifecycle_evidence_remain_separate() -> None:
    case: dict[str, Any] = {
        "status": "EXECUTED",
        "classification": "SERVER_3024",
        "error": {"timeout": {"execution_phase": "Execute"}},
        "server_stop": {
            "status": "HISTORY_NOT_VISIBLE",
            "event_lifecycle_status": "HISTORY_NOT_VISIBLE",
            "end_event_id": None,
        },
        "server_error_evidence": {
            "status": "CONFIRMED", "source": "MYSQL_MAX_EXECUTION_TIME_ERROR", "vendor_code": 3024,
        },
        "connection_disposition_consistent": True,
        "recovery": {"status": "PASS"},
        "select_attempts": 1,
        "baseline_retained": True,
        "output_parity": {"json": True, "html": True, "txt": True, "jsonl": True},
    }
    assert harness.verified_case(case)
    assert case["server_stop"]["status"] == "HISTORY_NOT_VISIBLE"
    assert case["server_error_evidence"]["vendor_code"] == 3024


def _safe_privilege_summary() -> dict[str, Any]:
    evidence = {
        "classification": "NON_PRIVILEGED",
        "high_risk_privileges": [],
        "write_privileges": [],
        "active_roles": "NONE",
        "role_grants_present": False,
        "unclassified_dynamic_privileges": [],
    }
    return dict(evidence)


def test_read_only_gate_accepts_any_user_when_privilege_evidence_is_safe() -> None:
    account = SimpleNamespace(
        current_user="app_observer@localhost",
        privilege_classification=_safe_privilege_summary(),
    )
    result = harness.readonly_privilege_evidence(account)
    assert result["safe"] is True
    assert result["classification"] == "NON_PRIVILEGED"


@pytest.mark.parametrize(("field", "value"), [
    ("high_risk_privileges", ["SUPER"]),
    ("write_privileges", ["UPDATE on *.*"]),
    ("unclassified_dynamic_privileges", ["UNKNOWN_PRIVILEGE"]),
    ("active_roles", "UNKNOWN"),
])
def test_read_only_gate_rejects_non_read_only_or_incomplete_evidence(field: str, value: Any) -> None:
    summary = _safe_privilege_summary()
    summary[field] = value
    account = SimpleNamespace(current_user="another_user@%", privilege_classification=summary)
    assert harness.readonly_privilege_evidence(account)["safe"] is False


def test_read_only_gate_does_not_override_adapter_unresolved_role_denial() -> None:
    summary = _safe_privilege_summary()
    summary["active_roles"] = "READ_ONLY_MONITORING"
    summary["role_grants_present"] = True
    account = SimpleNamespace(current_user="observer_role_user@%", privilege_classification=summary)
    assert harness.readonly_privilege_evidence(account)["safe"] is False


def test_read_only_gate_rejects_missing_identity_or_classification() -> None:
    safe = _safe_privilege_summary()
    assert not harness.readonly_privilege_evidence(SimpleNamespace(
        current_user=None, privilege_classification=safe))["safe"]
    assert not harness.readonly_privilege_evidence(SimpleNamespace(
        current_user="app@%", privilege_classification={}))["safe"]


def test_observer_preflight_checks_all_read_paths_before_workload(monkeypatch: pytest.MonkeyPatch) -> None:
    target, _ = adapter()
    calls: list[tuple[str, tuple[int, ...]]] = []

    def read(_: Any, sql: str, args: tuple[int, ...] = (), until: float | None = None) -> list[tuple[Any, ...]]:
        calls.append((sql, args))
        if sql == harness.CONSUMERS:
            return [(name, "YES") for name in harness.REQUIRED_CONSUMERS]
        if sql == harness.INSTRUMENTS:
            return [("statement/sql/select", "YES", "YES")]
        if sql == harness.THREAD:
            return [(88, "YES", "YES")]
        if sql == harness.CURRENT:
            return []
        if sql == harness.HISTORY_PROBE:
            return [(88, 12, 12, harness.FIXTURE_SQL["timeout_identity"])]
        if sql == harness.HISTORY:
            return []
        raise AssertionError("unexpected observer query")

    monkeypatch.setattr(harness, "observer_read", read)
    result = harness.observer_preflight(target, 9)
    assert result["status"] == "READY", result
    assert {sql for sql, _ in calls} == {
        harness.CONSUMERS, harness.INSTRUMENTS, harness.THREAD, harness.CURRENT, harness.HISTORY_PROBE,
    }


def test_observer_preflight_requires_correct_target_history_event(monkeypatch: pytest.MonkeyPatch) -> None:
    target, _ = adapter()

    def read(_: Any, sql: str, args: tuple[int, ...] = (),
             until: float | None = None) -> list[tuple[Any, ...]]:
        if sql == harness.CONSUMERS:
            return [(name, "YES") for name in harness.REQUIRED_CONSUMERS]
        if sql == harness.INSTRUMENTS:
            return [("statement/sql/select", "YES", "YES")]
        if sql == harness.THREAD:
            return [(88, "YES", "YES")]
        if sql == harness.CURRENT:
            return []
        if sql == harness.HISTORY_PROBE:
            return [(88, 12, 13, "SELECT something_else")]
        raise AssertionError(sql)

    monkeypatch.setattr(harness, "observer_read", read)
    result = harness.observer_preflight(target, 9)
    assert result["status"] == "INSUFFICIENT"
    assert result["target_history_probe"]["status"] == "INSUFFICIENT"
    assert any(item.get("reason") == "target_identity_event_not_retained" for item in result["errors"])


def test_observer_preflight_blocks_before_workload_when_history_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, _ = adapter()

    def read(_: Any, sql: str, args: tuple[int, ...] = (), until: float | None = None) -> list[tuple[Any, ...]]:
        if sql == harness.CONSUMERS:
            return [(name, "YES") for name in harness.REQUIRED_CONSUMERS]
        if sql == harness.INSTRUMENTS:
            return [("statement/sql/select", "YES", "YES")]
        if sql == harness.THREAD:
            return [(88, "YES", "YES")]
        if sql == harness.HISTORY_PROBE:
            raise RuntimeError(1142, "SELECT command denied to observer_user")
        if sql == harness.CURRENT:
            return []
        raise AssertionError("unexpected observer query")

    monkeypatch.setattr(harness, "observer_read", read)
    result = harness.observer_preflight(target, 9, ("observer_user",))
    assert result["status"] == "INSUFFICIENT"
    assert result["errors"][0]["vendor_code"] == 1142
    assert "observer_user" not in result["errors"][0]["message"]
    assert result["denied_select_objects"] == ["performance_schema.events_statements_history"]


def test_observer_preflight_blocks_when_required_consumer_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, _ = adapter()

    def read(_: Any, sql: str, args: tuple[int, ...] = (), until: float | None = None) -> list[tuple[Any, ...]]:
        if sql == harness.CONSUMERS:
            return [(name, "NO" if name == "events_statements_history" else "YES")
                    for name in harness.REQUIRED_CONSUMERS]
        if sql == harness.INSTRUMENTS:
            return [("statement/sql/select", "YES", "YES")]
        if sql == harness.THREAD:
            return [(88, "YES", "YES")]
        if sql == harness.CURRENT:
            return []
        if sql == harness.HISTORY_PROBE:
            return [(88, 12, 12, harness.FIXTURE_SQL["timeout_identity"])]
        if sql == harness.HISTORY:
            return []
        raise AssertionError("unexpected observer query")

    monkeypatch.setattr(harness, "observer_read", read)
    result = harness.observer_preflight(target, 9)
    assert result["status"] == "INSUFFICIENT"
    assert "events_statements_history" in result["disabled_consumers"]



def test_observer_preflight_blocks_when_target_thread_history_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, _ = adapter()

    def read(_: Any, sql: str, args: tuple[Any, ...] = (), until: float | None = None) -> list[tuple[Any, ...]]:
        if sql == harness.CONSUMERS:
            return [(name, "YES") for name in harness.REQUIRED_CONSUMERS]
        if sql == harness.INSTRUMENTS:
            return [("statement/sql/select", "YES", "YES")]
        if sql == harness.THREAD:
            return [(88, "YES", "NO")]
        if sql == harness.CURRENT:
            return []
        if sql == harness.HISTORY_PROBE:
            return []
        if sql == harness.HISTORY:
            return []
        raise AssertionError("unexpected observer query")

    monkeypatch.setattr(harness, "observer_read", read)
    result = harness.observer_preflight(target, 9)
    assert result["status"] == "INSUFFICIENT"
    assert result["target_thread_history_enabled"] is False
    assert any(item.get("reason") == "target_thread_history_not_enabled" for item in result["errors"])
    assert result["denied_select_objects"] == []


def test_session_timing_and_server_first_window_include_server_reserves() -> None:
    base = {
        "operation": "session", "execute_start_monotonic": 0.0,
        "execute_end_monotonic": 0.02, "server_execution_limit_ms": 500,
        "remaining_before_ms": 1000, "remaining_after_ms": 980, "execute_status": "OK",
        "remaining_at_cursor_creation_ms": 1000,
    }
    select = {
        "operation": "select", "execute_start_monotonic": 0.02,
        "send_completed_monotonic": 0.03, "execute_end_monotonic": 0.95,
        "remaining_before_ms": 980, "remaining_at_send_ms": 970, "same_deadline_as_setup": True,
    }
    audit = {
        "client_remaining_before_session_ms": 1000,
        "server_execution_limit_ms": 500,
        "session_setup_reserve_ms": 250,
        "server_timeout_response_margin_ms": 250,
    }
    assert harness.session_timing_valid([base, select], audit)
    window = harness.server_first_window([base, select], audit)
    assert window["status"] == "SERVER_BEFORE_CLIENT_WINDOW_OBSERVED"
    assert window["margin_ms"] == 470
    for remaining in (740, 700, 600):
        observed = {**select, "remaining_at_send_ms": remaining}
        assert harness.session_timing_valid([base, observed], audit)
        assert harness.server_first_window([base, observed], audit)["status"] == "CLIENT_MAY_EXPIRE_FIRST"
    assert harness.server_first_window([base])["status"] == "NOT_OBSERVED"
    assert not harness.session_timing_valid([base, {**select, "same_deadline_as_setup": False}], audit)
    assert not harness.session_timing_valid([{**base, "execute_status": "FAILED"}, select], audit)
    assert not harness.session_timing_valid([{**base, "server_execution_limit_ms": 501}, select], audit)


@pytest.mark.parametrize(("statement", "expected"), [
    ("GRANT SELECT ON `performance_schema`.`threads` TO 'monitor'@'%'", True),
    ("GRANT PROCESS ON *.* TO 'monitor'@'%'", True),
    ("GRANT REFERENCES ON *.* TO 'monitor'@'%'", False),
    ("GRANT UPDATE ON *.* TO 'monitor'@'%'", False),
    ("GRANT PROXY ON 'root'@'localhost' TO 'monitor'@'%'", False),
    ("GRANT SELECT ON *.* TO 'monitor'@'%' WITH GRANT OPTION", False),
    ("GRANT `role_name`@`%` TO 'monitor'@'%'", False),
    ("unrecognized grant output", False),
])
def test_verify_allowlisted_readonly_grants(statement: str, expected: bool) -> None:
    assert harness.grant_is_readonly(statement) == expected


def test_history_long_corroborates_observed_event_after_thread_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer, _ = adapter()
    calls: list[tuple[str, tuple[int, ...]]] = []

    def read(_: Any, sql: str, args: tuple[int, ...], until: float) -> list[tuple[Any, ...]]:
        calls.append((sql, args))
        if sql == harness.CURRENT:
            return [(99, 8, None, "SELECT 1"), (55, 9, None, "SELECT 1")]
        if sql == harness.HISTORY:
            return []
        return [(55, 9, 10, "SELECT 1")]

    monkeypatch.setattr(harness, "observer_read", read)
    assert harness.observe(observer, 55, 900, harness.Observation("SELECT 1", 55), {
        "events_statements_current": "YES", "events_statements_history": "YES",
    }, history_long=True)["end_event_id"] == 10
    assert calls == [(harness.CURRENT, (55,)), (harness.HISTORY, (55, 9)), (harness.HISTORY_LONG, (55, 9))]


def test_observer_rejects_sql_outside_allowlist_and_checks_reviewed_statements() -> None:
    target, _ = adapter()
    for sql in (harness.CURRENT, harness.HISTORY, harness.HISTORY_LONG, harness.HISTORY_PROBE,
                harness.THREAD, harness.CONSUMERS, harness.INSTRUMENTS):
        with pytest.raises(harness.GateError, match="bounded socket"):
            harness.observer_read(target, sql)
    with pytest.raises(harness.GateError, match="Unexpected observer statement"):
        harness.observer_read(target, "SELECT 2")


def test_session_then_hint_run_on_fresh_connections_and_keep_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    target, driver = adapter()
    observer, observer_driver = adapter()
    target.registry = observer.registry = harness.stage_registry(tmp_path)
    first = driver.connection
    scripts = {**first.scripts, **{
        target.registry.get(name, DatabaseType.MYSQL).text: name for name in harness.NAMES
    }}
    connections = [first, FakeConnection(scripts), FakeConnection(scripts), observer_driver.connection]
    for conn, cid in zip(connections, (9, 19, 29, 39), strict=True):
        conn.scripts = scripts
        conn.results["preflight"][0].update(
            server_version="8.4.11", monitor_user="lab-observer@localhost", connection_id=cid,
        )
        conn.results["timeout_identity"] = [{
            "connection_id": cid, "version": "8.4.11", "product": "MySQL Community Server - GPL",
            "session_limit_ms": 4000,
        }]
        monkeypatch.setattr(conn, "open", True, raising=False)
        close = conn.close

        def close_connection(conn: Any = conn, close: Any = close) -> None:
            close()
            conn.open = False

        monkeypatch.setattr(conn, "close", close_connection)
    first.failures["timeout_session"] = RuntimeError(3024, "interrupted; password=fake-secret")
    connections[1].failures["timeout_hint"] = RuntimeError(3024, "interrupted")
    observer_clock = [0.0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: observer_clock[0])
    monkeypatch.setattr(SqlDeadline, "start",
                        classmethod(lambda cls, seconds: cls(seconds, observer_clock[0], lambda: observer_clock[0])))
    real_connect = target.connect
    next_connection = iter(connections[:3])

    def connect() -> None:
        driver.connection = next(next_connection)
        real_connect()
        proxy = _DeadlineSocket(SimpleNamespace(sendall=lambda _: None, settimeout=lambda _: None))
        monkeypatch.setattr(target, "_deadline_socket", proxy)

    monkeypatch.setattr(target, "connect", connect)
    monkeypatch.setattr(harness, "observer_preflight", lambda *args: {
        "status": "READY", "target_thread": {"thread_id": 55}, "consumers": {}, "history_long_available": False,
    })
    monkeypatch.setattr(harness, "observe", lambda *args, **kwargs: {"status": "ENDED"})
    execute = FakeCursor.execute

    def wire(cursor: FakeCursor, sql: str, parameters: Any = None) -> None:
        if target._deadline_socket is not None and (
            sql == harness.SESSION_SET or "timeout_session_probe" in sql or "timeout_hint_probe" in sql
        ):
            target._deadline_socket.sendall(b"fake wire")
        execute(cursor, sql, parameters)
        if cursor.script_name == "timeout_identity":
            cursor.rows[0]["session_limit_ms"] = cursor.connection.server_limits[-1]

    monkeypatch.setattr(FakeCursor, "execute", wire)
    with harness.capture_audit() as audit:
        audit.add_redactions(target.instance.credential_alias, "lab-observer@localhost")
        target.connect()
        observer.connect()
        session = harness.exercise(target, observer, "timeout_session", audit, tmp_path)
        hint = harness.exercise(target, observer, "timeout_hint", audit, tmp_path)
    assert harness.verified_case(session), harness.render_json(session)
    assert harness.verified_case(hint), harness.render_json(hint)
    assert session["audit_limit_consistent"] and session["session_timing_valid"]
    assert session["server_error_evidence"]["vendor_code"] == 3024
    assert session["server_error_evidence"]["status"] == "CONFIRMED"
    assert session["server_stop"]["status"] == "ENDED"
    assert session["connection_id"] == 9 and session["recovery"]["connection_id"] == 19
    assert hint["connection_id"] == 19 and hint["recovery"]["connection_id"] == 29
    assert first.closed and connections[1].closed
    assert first.executed.count("timeout_session") == connections[1].executed.count("timeout_hint") == 1
    assert "timeout_session" not in connections[1].executed
    assert "fake-secret" not in "".join(path.read_text(encoding="utf8") for path in tmp_path.glob("timeout_*.*"))


def test_cli_resolver_uses_inventory_credential_when_session_is_unlocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from dba import encrypted_credentials

    target, _ = adapter()
    asset = replace(target.instance, credential_alias="inventory-alias")
    os_credential = Credential("monitor_user", "os-secret")
    session_calls: list[str] = []

    class KeyringStore:
        @property
        def backend_name(self) -> str:
            return "Windows Credential Manager"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> Credential:
            assert alias == asset.credential_alias
            return os_credential

    class EncryptedStore:
        def __init__(self, path: Path | None = None) -> None:
            self.path = path or tmp_path / "default-store"
            self.exists = False

    class Session:
        def status(self) -> dict[str, str]:
            session_calls.append("status")
            return {"status": "UNLOCKED"}

        def get(self, alias: str, store_path: Path) -> Credential | None:
            session_calls.append("get")
            return None

    monkeypatch.setattr(harness, "KeyringCredentialStore", KeyringStore)
    monkeypatch.setattr(harness, "EncryptedFileCredentialStore", EncryptedStore)
    monkeypatch.setattr(encrypted_credentials, "CredentialSessionClient", Session)
    provider = harness.resolve_acceptance_credential(asset, "auto", None)
    assert provider.get_credential(asset.credential_alias) is os_credential
    assert provider.last_backend == "Windows Credential Manager"
    assert session_calls == []


def test_cli_resolver_uses_unlocked_session_bound_to_encrypted_local_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from dba import encrypted_credentials

    target, _ = adapter()
    asset = replace(target.instance, credential_alias="inventory-alias")
    session_credential = Credential("monitor_user", "session-secret")
    requested: list[tuple[str, Path]] = []

    class KeyringStore:
        @property
        def backend_name(self) -> str:
            return "Windows Credential Manager"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> None:
            return None

    class EncryptedStore:
        def __init__(self, path: Path | None = None) -> None:
            self.path = path or tmp_path / "default-store"
            self.exists = False

    class Session:
        def status(self) -> dict[str, str]:
            return {"status": "UNLOCKED"}

        def get(self, alias: str, store_path: Path) -> Credential | None:
            requested.append((alias, store_path))
            return session_credential

    monkeypatch.setattr(harness, "KeyringCredentialStore", KeyringStore)
    monkeypatch.setattr(harness, "EncryptedFileCredentialStore", EncryptedStore)
    monkeypatch.setattr(encrypted_credentials, "CredentialSessionClient", Session)
    provider = harness.resolve_acceptance_credential(asset, "auto", tmp_path / "bound-store")
    assert provider.get_credential(asset.credential_alias) is session_credential
    assert provider.last_backend == "Encrypted Credential Session"
    assert requested == [(asset.credential_alias, tmp_path / "bound-store")]


def test_cli_resolver_reports_safe_failure_without_prompting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from dba import encrypted_credentials

    target, _ = adapter()
    asset = replace(target.instance, credential_alias="inventory-alias")
    prompt_calls: list[tuple[str, str]] = []

    class KeyringStore:
        @property
        def backend_name(self) -> str:
            return "Windows Credential Manager"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> None:
            return None

    class EncryptedStore:
        def __init__(self, path: Path | None = None) -> None:
            self.path = path or tmp_path / "default-store"
            self.exists = False

    class Session:
        def status(self) -> dict[str, str]:
            return {"status": "LOCKED"}

        def get(self, alias: str, store_path: Path) -> None:
            return None

    def forbidden_prompt(database_id: str, alias: str) -> Credential:
        prompt_calls.append((database_id, alias))
        raise harness.ToolkitError(
            "CredentialUnavailable", "No credential was resolved from an existing store."
        )

    monkeypatch.setattr(harness, "KeyringCredentialStore", KeyringStore)
    monkeypatch.setattr(harness, "EncryptedFileCredentialStore", EncryptedStore)
    monkeypatch.setattr(encrypted_credentials, "CredentialSessionClient", Session)
    monkeypatch.setattr(harness, "reject_credential_prompt", forbidden_prompt)
    with pytest.raises(harness.ToolkitError) as caught:
        harness.resolve_acceptance_credential(asset, "auto", None)
    assert caught.value.error_type == "CredentialUnavailable"
    assert "secret" not in str(caught.value).casefold()
    assert prompt_calls == [(asset.id, asset.credential_alias)]


def test_resolved_provider_keeps_inventory_lookup_result_for_both_adapters() -> None:
    credential = Credential("monitor_user", "not-printed")
    provider = harness.ResolvedCredentialProvider(credential, "Windows Credential Manager")
    assert provider.get_credential("inventory-alias") is credential
    assert provider.get_credential("inventory-alias") is credential
