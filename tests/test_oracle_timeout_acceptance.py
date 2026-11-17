"""Exercise Oracle timeout acceptance without a database or credential store."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_oracle import FakeConnection, FakeCursor, fixture

from dba.credentials import Mode
from dba.errors import ToolkitError
from dba.oracle import OracleAdapter
from dba.settings import OracleSettings
from scripts.acceptance import verify_oracle_timeout as harness

SECRETS = ("oracle-acceptance-secret", "oracle-acceptance-user", "oracle-monitor")
ROWS = [{"probe_value": value} for value in range(1, 102)]


class Driver:
    def __init__(self, *connections: FakeConnection) -> None:
        self.connections = iter(connections)
        self.calls: list[tuple[str, str]] = []

    def connect(self, **kwargs: Any) -> FakeConnection:
        self.calls.append((kwargs["user"], kwargs["password"]))
        return next(self.connections)


def make_lab(tmp_path: Path, clock: Any, *, observe_v_session: bool = False) -> tuple[
    OracleAdapter, OracleAdapter, FakeConnection, FakeConnection, Driver,
]:
    asset, _, provider, seed = fixture()
    registry = harness.stage_registry(tmp_path / "registry", observe_v_session=observe_v_session)
    scripts = {script.text: script.metadata.name for script in registry.validate()}

    def connection(sid: str) -> FakeConnection:
        return FakeConnection(scripts, {
            **seed.connection.data,
            "timeout_baseline": [{"baseline_value": 1}],
            "timeout_identity": [{"instance_id": "1", "session_id": sid, "audit_session_id": sid + "8"}],
            "timeout_normal": ROWS,
        })

    old, observer, fresh = (connection(sid) for sid in ("10", "11", "12"))
    fake = Driver(old, observer, fresh)
    driver = harness.TracedDriver(fake, clock)
    settings = OracleSettings(diagnostic_sql_timeout_seconds=2)
    return (OracleAdapter(asset, provider, registry, settings, Mode.DEVELOPMENT, driver=driver),
            OracleAdapter(asset, provider, registry, settings, Mode.DEVELOPMENT, driver=driver),
            old, fresh, fake)


@pytest.mark.parametrize(("scenario", "stages"), [
    ("execute", ["Execute"]),
    ("fetch", ["Fetch"]),
    ("both", ["Execute", "Fetch"]),
])
def test_proxy_scenario_runs_only_requested_timeout_cases(
    tmp_path: Path, sql_deadline_clock: Any, monkeypatch: pytest.MonkeyPatch,
    scenario: str, stages: list[str],
) -> None:
    target, observer, _, _, _ = make_lab(tmp_path, sql_deadline_clock)
    called: list[str] = []

    def run_case(*, stage: str, **_: Any) -> dict[str, Any]:
        called.append(stage)
        return {"status": "PASS", "baseline_retained": True, "recovery": {"status": "PASS"}}

    monkeypatch.setattr(harness, "run_proxy_timeout_case", run_case)
    proxy = SimpleNamespace(LISTEN_HOST="127.0.0.1", listen_port=12345, upstream_host="127.0.0.1",
                            upstream_port=1521)
    with harness.capture_audit(SECRETS) as audit, target, observer:
        summary = harness.exercise_with_tcp_delay_proxy(
            target, observer, tmp_path, audit, SECRETS, target._driver, proxy, scenario=scenario,
        )
    assert called == stages
    for stage in ("Execute", "Fetch"):
        assert summary[f"{stage.lower()}_timeout"]["status"] == (
            "PASS" if stage in stages else "NOT_EXECUTED"
        )
    assert summary["status"] == "PARTIAL"
    assert summary["server_query_stop_confirmed"] is False


def test_registry_declares_bounded_probe_and_rejects_tampering(tmp_path: Path) -> None:
    registry = harness.stage_registry(tmp_path / "registry")
    names = {item.metadata.name for item in registry.validate()}
    assert names == set(OracleAdapter.REQUIRED) | {"timeout_identity", "timeout_baseline", "timeout_normal"}
    script = registry.get("timeout_normal", harness.ENGINE)
    assert script.metadata.max_rows == 102 and script.metadata.timeout_seconds == 2
    registry.authorize(script, harness.ENGINE)
    source = tmp_path / "registry/oracle/timeout_normal.sql"
    source.write_text(script.text.replace("101", "9000"), encoding="utf-8")
    from dba.inventory import ValidationError

    with pytest.raises(ValidationError, match="changed"):
        registry.authorize(script, harness.ENGINE)


def test_normal_probe_has_fetch_timing_and_blocked_live_timeouts(
    tmp_path: Path, sql_deadline_clock: Any,
) -> None:
    target, observer, _, _, _ = make_lab(tmp_path, sql_deadline_clock)
    with harness.capture_audit(SECRETS) as audit, target, observer:
        summary = harness.exercise(target, observer, tmp_path, audit, SECRETS)
    assert summary["normal"] == "PASS"
    assert summary["status"] == "PARTIAL"
    assert summary["execute_timeout"]["status"] == summary["fetch_timeout"]["status"] == "BLOCKED"
    trace = summary["normal_trace"]
    assert trace[0]["phase"] == "Execute" and trace[0]["prefetchrows"] == 100
    assert sum(item.get("rows_returned", 0) for item in trace) == 101
    assert json.loads((tmp_path / "normal.json").read_text(encoding="utf-8"))["row_count"] == 101


def test_optional_observer_blocks_execute_when_observer_connection_fails(
    tmp_path: Path, sql_deadline_clock: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, direct_observer, old, _, _ = make_lab(tmp_path, sql_deadline_clock, observe_v_session=True)
    tagged_steps: list[tuple[str, str]] = []
    real_execute = FakeCursor.execute

    def execute(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
        tag = getattr(cursor.connection, "client_identifier", "")
        if cursor.connection is old and tag:
            tagged_steps.append((old.sql_names[sql], tag))
        real_execute(cursor, sql, parameters)

    def connect() -> Any:
        tagged_steps.append(("observer_connect", getattr(old, "client_identifier", "")))
        raise RuntimeError("sensitive connection detail")

    @contextmanager
    def open_connection() -> Iterator[Any]:
        yield connect()

    monkeypatch.setattr(FakeCursor, "execute", execute)
    with harness.capture_audit(SECRETS) as audit, target, direct_observer:
        data = harness.exercise(target, direct_observer, tmp_path, audit, SECRETS,
                                observation_factory=open_connection)
    evidence = data["session_observation"]["normal"]
    assert data["normal"] == "NOT_EXECUTED"
    assert data["normal_result"]["error_type"] == "ObserverNotReady"
    assert evidence["observer_status"] == "UNKNOWN"
    assert evidence["worker_joined"] is True
    assert evidence["query_stop_confirmed"] is False
    assert "sensitive connection detail" not in json.dumps(evidence)
    tag = evidence["client_identifier"]
    assert tag.startswith("dbatoolkit-observe-") and len(tag.encode("ascii")) <= 64
    assert tagged_steps == [("timeout_identity", tag), ("observer_connect", tag)]
    assert getattr(old, "client_identifier") == ""


@pytest.mark.parametrize(("time_spent", "timeout_ms"), [(1.9996, 1), (1.9976, 3)])
def test_positive_remaining_is_rounded_up_at_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql_deadline_clock: Any,
    time_spent: float, timeout_ms: int,
) -> None:
    target, _observer, _old, _fresh, _driver = make_lab(tmp_path, sql_deadline_clock)
    execute = FakeCursor.execute

    def spend(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
        execute(cursor, sql, parameters)
        if cursor.connection.sql_names[sql] == "timeout_normal":
            sql_deadline_clock.advance(time_spent)

    monkeypatch.setattr(FakeCursor, "execute", spend)
    with harness.capture_audit(SECRETS), target:
        result, connection = harness.traced_query(target)
    assert result.success
    assert connection.trace[0]["call_timeout_ms"] == 2000
    assert connection.trace[1]["call_timeout_ms"] == timeout_ms


@pytest.mark.parametrize(("phase", "expected_calls"), [("Execute", 1), ("Fetch", 3)])
def test_internal_round_trips_exhaust_cumulative_deadline_and_discard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql_deadline_clock: Any,
    phase: str, expected_calls: int,
) -> None:
    target, _observer, old, _fresh, _driver = make_lab(tmp_path, sql_deadline_clock)
    execute = FakeCursor.execute
    fetch = FakeCursor.fetchmany

    def rounds(cursor: FakeCursor, *durations: float) -> None:
        for duration in durations:
            assert duration * 1000 < cursor.connection.call_timeout
            sql_deadline_clock.advance(duration)

    def timed_execute(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
        execute(cursor, sql, parameters)
        if cursor.connection.sql_names[sql] == "timeout_normal":
            if phase == "Execute":
                rounds(cursor, 1.2, 1.2)
            else:
                rounds(cursor, 0.5)

    def timed_fetch(cursor: FakeCursor, size: int) -> list[tuple[Any, ...]]:
        if cursor.rows is ROWS:
            if cursor.offset == 0:
                rounds(cursor, 0.5)
            else:
                rounds(cursor, 0.6, 0.6)
        return fetch(cursor, size)

    monkeypatch.setattr(FakeCursor, "execute", timed_execute)
    monkeypatch.setattr(FakeCursor, "fetchmany", timed_fetch)
    with harness.capture_audit(SECRETS), target:
        result, connection = harness.traced_query(target)
        assert target._connection is None
        with pytest.raises(ToolkitError) as exc:
            harness.query(target, "timeout_baseline")
        assert exc.value.error_type == "PreflightRequired"
    assert result.error_type == f"{phase}Timeout"
    assert result.database_error is not None
    assert result.database_error["timeout"]["cancellation_status"] == "CANCEL_STATUS_UNKNOWN"
    assert result.database_error["timeout"]["connection_status"] == "DISCARDED"
    assert not result.rows and old.closed and connection.closed
    assert len(connection.trace) == expected_calls
    if phase == "Fetch":
        assert [entry["call_timeout_ms"] for entry in connection.trace] == [2000, 1500, 1000]
        assert connection.trace[1]["rows_returned"] == 1


@pytest.mark.parametrize(("code", "mapped", "category", "cancel", "phase"), [
    ("DPY-4024", "DPY-4024", "TIMEOUT", "DRIVER_REPORTED", "Execute"),
    ("DPI-1067", "DPY-4024", "TIMEOUT", "DRIVER_REPORTED", "Fetch"),
    ("DPY-4011", "DPY-4011", "CONNECTION_LOST", "CANCEL_STATUS_UNKNOWN", "Fetch"),
    ("DPI-1080", "DPY-4011", "CONNECTION_LOST", "CANCEL_STATUS_UNKNOWN", "Execute"),
])
def test_single_round_trip_driver_error_cleanup_recovery_and_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql_deadline_clock: Any,
    code: str, mapped: str, category: str, cancel: str, phase: str,
) -> None:
    target, observer, old, fresh, driver = make_lab(tmp_path, sql_deadline_clock)
    driver_errors = pytest.importorskip("oracledb.errors")
    message = (
        f"{code}: call timeout of 2000 ms exceeded with ORA-03156"
        if code.endswith("4024") or code.endswith("1067")
        else f"{code}: the database or network closed the connection"
    )
    native = driver_errors._Error(message=f"{message}; password={SECRETS[0]}; user={SECRETS[1]}")
    assert native.full_code == mapped
    assert native.is_session_dead is (category == "CONNECTION_LOST")
    failure = RuntimeError(native)
    execute = FakeCursor.execute
    fetch = FakeCursor.fetchmany

    def timed_execute(cursor: FakeCursor, sql: str, parameters: dict[str, Any] | None = None) -> None:
        execute(cursor, sql, parameters)
        if cursor.connection is old and cursor.connection.sql_names[sql] == "timeout_normal":
            if phase == "Execute":
                sql_deadline_clock.advance(cursor.connection.call_timeout / 1000)
                raise failure
            sql_deadline_clock.advance(0.25)

    def timed_fetch(cursor: FakeCursor, size: int) -> list[tuple[Any, ...]]:
        if phase == "Fetch" and cursor.connection is old and cursor.rows is ROWS and cursor.offset == 1:
            sql_deadline_clock.advance(cursor.connection.call_timeout / 1000)
            raise failure
        return fetch(cursor, size)

    monkeypatch.setattr(FakeCursor, "execute", timed_execute)
    monkeypatch.setattr(FakeCursor, "fetchmany", timed_fetch)
    with harness.capture_audit(SECRETS) as audit, target, observer:
        summary = harness.exercise(target, observer, tmp_path, audit, SECRETS)
        assert target._connection is not None
    recorded = summary["unexpected_failure"]
    error = recorded["result"]["database_error"]
    assert error["vendor_code"] == mapped and error["error_category"] == category
    assert error["timeout"]["cancellation_status"] == cancel
    assert error["timeout"]["connection_status"] == "DISCARDED"
    assert recorded["result"]["error_type"] == (
        "QueryTimeout" if category == "TIMEOUT" else "QueryConnectionLost"
    )
    assert recorded["phase"] == phase
    assert recorded["attempts"] == 1 and recorded["partial_result_discarded"]
    driver_codes = summary["normal_trace"][-1]["driver_error"]["codes"]
    assert driver_codes[0] == mapped
    if code.startswith("DPI-"):
        assert code in driver_codes
    else:
        assert code == mapped
    assert summary["recovery"]["status"] == "PASS"
    assert summary["recovery"]["refused_before_preflight"] is True
    assert summary["baseline_retained"] and old.closed and fresh.closed
    assert all(recorded["output_parity"].values())
    assert len(driver.calls) == 3 and len(set(driver.calls)) == 1
    assert [name for name, _ in old.calls].count("timeout_normal") == 1
    for artifact in tmp_path.glob("driver_failure.*"):
        text = artifact.read_text(encoding="utf-8")
        assert mapped in text and all(secret not in text for secret in SECRETS)
        assert "[REDACTED]" in text


@pytest.mark.parametrize(("code", "category"), [
    ("DPI-1067", "TIMEOUT"),
    ("DPI-1080", "CONNECTION_LOST"),
])
def test_structured_dpi_code_is_classified_without_message_code(
    code: str, category: str,
) -> None:
    from dba.database_errors import normalize_oracle_error, with_timeout_evidence

    synthetic = Exception(SimpleNamespace(
        full_code=code, code=0, is_session_dead=False, message="driver supplied structured code",
    ))
    detail = normalize_oracle_error(synthetic)
    assert detail.vendor_code == code
    assert detail.error_category == category
    if code == "DPI-1067":
        assert detail.classification == "TIMEOUT"
        cancel_status, mechanism, phase = "DRIVER_REPORTED", "ORACLE_CALL_TIMEOUT", "Execute"
    else:
        assert detail.classification == "CONNECTION_LOST"
        cancel_status, mechanism, phase = "CANCEL_STATUS_UNKNOWN", "ORACLE_CONNECTION_LOST", "Fetch"
    detail = with_timeout_evidence(
        detail,
        execution_phase=phase,
        timeout_seconds=1,
        effective_timeout_seconds=1,
        script_timeout_seconds=1,
        elapsed_ms=1000,
        script_path="oracle/timeout_normal.sql",
    )
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == cancel_status
    assert detail.timeout.mechanism == mechanism
    assert harness.driver_error_evidence(synthetic)["codes"] == [code]


def test_installed_driver_maps_dpi_errors_without_connecting() -> None:
    errors = pytest.importorskip("oracledb.errors")
    usable = errors._Error(message="DPI-1067: call timeout of 500 ms exceeded with ORA-03156")
    dead = errors._Error(message="DPI-1080: connection was closed by ORA-03156")
    assert usable.full_code == "DPY-4024" and usable.is_session_dead is False
    assert dead.full_code == "DPY-4011" and dead.is_session_dead is True


class FakeGate:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.timeout_evidence: list[dict[str, Any]] = []
        self.release_modes: list[tuple[bool, str]] = []

    def wait_quiescent(self) -> dict[str, Any]:
        return {"confirmed": True, "quiet_period_ms": 100, "upstream_readable_at_end": False}

    def arm_gate(
        self,
        stage: str,
        *,
        quiescence: dict[str, Any] | None = None,
        release_after_post_hold_client_bytes: int | None = None,
        minimum_hold_seconds: float = 0.0,
    ) -> None:
        self.records.append({
            "stage": stage,
            "armed": True,
            "quiescence": quiescence,
            "release_after_post_hold_client_bytes": release_after_post_hold_client_bytes,
            "minimum_hold_seconds": minimum_hold_seconds,
        })

    def snapshot(self) -> dict[str, Any]:
        return {"gate": self.records[-1] if self.records else None}

    def mark_timeout_observed(self, evidence: dict[str, Any]) -> None:
        self.timeout_evidence.append(evidence)

    def release_gate(self, *, discard_response: bool = False, reason: str = "harness") -> None:
        if self.records:
            self.records[-1]["armed"] = False
        self.release_modes.append((discard_response, reason))


@pytest.mark.parametrize(("error_at", "expected_order"), [
    (29, "ERROR_OBSERVED_BEFORE_RELEASE"),
    (30, "SAME_CLOCK_TICK"),
    (31, "RELEASE_BEFORE_ERROR_OBSERVED"),
    (None, "UNKNOWN"),
])
def test_gate_evidence_is_independent_of_driver_error_timing(
    error_at: int | None, expected_order: str,
) -> None:
    gate = {
        "request_bytes": 21,
        "response_bytes": 584,
        "held_bytes": 462,
        "forwarded_bytes": 584,
        "forwarded_before_release_bytes": 0,
        "forwarded_after_release_bytes": 584,
        "held_response_forwarded_bytes": 462,
        "pre_gate_quiescence": {"confirmed": True},
        "response_held_monotonic_ns": 10,
        "released_monotonic_ns": 30,
        "release_mode": "forward",
        "release_reason": "post_hold_client_bytes_and_minimum_delay",
        "release_after_post_hold_client_bytes": 1,
        "release_trigger_satisfied": True,
    }
    event = {"driver_error_observed_monotonic_ns": error_at, "estimated_timeout_deadline_monotonic_ns": 25}
    evidence = harness.summarize_proxy_gate(gate, event)
    assert evidence["verified"] is True
    assert evidence["driver_timeout_observed_by_proxy"] is False
    assert evidence["release_vs_driver_error_observation"] == expected_order
    assert evidence["estimated_timeout_deadline_monotonic_ns"] == 25
    assert harness.summarize_proxy_gate({**gate, "forwarded_before_release_bytes": 1}, event)["verified"] is False
    assert harness.summarize_proxy_gate({**gate, "held_response_forwarded_bytes": 461}, event)["verified"] is False
    assert harness.summarize_proxy_gate({**gate, "release_reason": "max_gate_hold_elapsed"}, event)["verified"] is False
    del gate["forwarded_before_release_bytes"]
    assert harness.summarize_proxy_gate(gate, event)["verified"] is False


def make_traced_cursor(
    tmp_path: Path,
    clock: Any,
    fake_gate: FakeGate,
    *,
    gate_phase: str,
) -> tuple[Any, FakeConnection, Any, Any]:
    asset, _, _provider, seed = fixture()
    registry = harness.stage_registry(tmp_path / f"registry-{gate_phase.lower()}")
    scripts = {script.text: script.metadata.name for script in registry.validate()}
    connection = FakeConnection(scripts, {
        **seed.connection.data,
        "timeout_identity": [{"instance_id": "1", "session_id": "10", "audit_session_id": "108"}],
        "timeout_normal": ROWS,
    })
    owner = harness.TracedDriver(
        Driver(connection), clock, script_names=scripts, proxy=fake_gate,
    )
    owner.gate_plan = harness.GatePlan(gate_phase)
    traced = owner.connect(user="offline-only", password="offline-secret")
    traced.record = True
    cursor = traced.cursor()
    cursor.arraysize = 100
    cursor.prefetchrows = 100
    return registry, connection, owner, cursor


def test_stage_control_arms_execute_only_for_the_reviewed_target_script(
    tmp_path: Path, sql_deadline_clock: Any,
) -> None:
    gate = FakeGate()
    registry, _connection, _owner, cursor = make_traced_cursor(
        tmp_path, sql_deadline_clock, gate, gate_phase="Execute",
    )
    cursor.execute(registry.get("timeout_identity", harness.ENGINE).text)
    assert gate.records == []
    cursor.execute(registry.get("timeout_normal", harness.ENGINE).text)
    assert [record["stage"] for record in gate.records] == ["Execute"]
    assert gate.records[0]["quiescence"]["confirmed"] is True


def test_fetch_stage_arms_exactly_before_fetch_call_101_after_100_rows(
    tmp_path: Path, sql_deadline_clock: Any,
) -> None:
    gate = FakeGate()
    registry, _connection, owner, cursor = make_traced_cursor(
        tmp_path, sql_deadline_clock, gate, gate_phase="Fetch",
    )
    cursor.execute(registry.get("timeout_normal", harness.ENGINE).text)
    for _ in range(100):
        assert len(cursor.fetchmany(1)) == 1
    assert gate.records == []
    assert owner.gate_plan is not None
    assert len(cursor.fetchmany(1)) == 1
    assert len(gate.records) == 1
    assert gate.records[0]["stage"] == "Fetch"
    assert owner.gate_preparations[0]["confirmed"] is True
    assert cursor.fetch_call_number == 101
    assert cursor.rows_consumed == 101


@pytest.mark.parametrize("gate_phase", ["Execute", "Fetch"])
def test_controlled_release_policy_is_applied_to_the_selected_stage(
    tmp_path: Path, sql_deadline_clock: Any, gate_phase: str,
) -> None:
    gate = FakeGate()
    registry, _connection, owner, cursor = make_traced_cursor(
        tmp_path, sql_deadline_clock, gate, gate_phase=gate_phase,
    )
    owner.release_after_client_data = True
    owner.release_minimum_hold_seconds = 2.1
    cursor.execute(registry.get("timeout_normal", harness.ENGINE).text)
    if gate_phase == "Fetch":
        for _ in range(100):
            assert len(cursor.fetchmany(1)) == 1
        assert gate.records == []
        assert len(cursor.fetchmany(1)) == 1
    else:
        assert len(gate.records) == 1
    record = gate.records[-1]
    assert record["stage"] == gate_phase
    assert record["release_after_post_hold_client_bytes"] == 1
    assert record["minimum_hold_seconds"] == 2.1


@pytest.mark.parametrize(("code", "timeout_reported"), [
    ("DPY-4024", True),
    ("DPY-4011", False),
])
def test_gated_driver_error_preserves_timeout_vs_connection_loss_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sql_deadline_clock: Any,
    code: str,
    timeout_reported: bool,
) -> None:
    gate = FakeGate()
    registry, _connection, _owner, cursor = make_traced_cursor(
        tmp_path, sql_deadline_clock, gate, gate_phase="Execute",
    )
    errors = pytest.importorskip("oracledb.errors")
    if code == "DPY-4024":
        native = errors._Error(message="DPY-4024: call timeout of 2000 ms exceeded")
    else:
        native = errors._Error(message="DPY-4011: the database or network closed the connection")

    def fail_execute(_cursor: FakeCursor, _sql: str, _parameters: dict[str, Any] | None = None) -> None:
        raise RuntimeError(native)

    monkeypatch.setattr(FakeCursor, "execute", fail_execute)
    with pytest.raises(RuntimeError):
        cursor.execute(registry.get("timeout_normal", harness.ENGINE).text)
    assert bool(gate.timeout_evidence) is timeout_reported
    assert gate.release_modes == [(True, "driver-timeout" if timeout_reported else "driver-operation-error")]
    event = cursor.connection.trace[-1]
    assert code in event["driver_error"]["codes"]
