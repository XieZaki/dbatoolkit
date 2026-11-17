"""Read-only Oracle timeout acceptance on the authorized Windows host."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from dba.cli import _effective_settings
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError, database_error_log_fields
from dba.incident import build_incident_summary
from dba.inventory import Inventory
from dba.oracle import OracleAdapter, load_driver
from dba.oracle_slow import build_slow_report
from dba.presentation import render_human, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.settings import OracleSettings
from scripts.acceptance.oracle_session_observer import (
    MAX_CANDIDATES,
    SAMPLE_SQL,
    Observation,
    SessionObserver,
)
from scripts.acceptance.oracle_tcp_delay_proxy import (
    OracleTcpDelayProxy,
    ProxyEndpointDriver,
    ProxyState,
    ProxyStateError,
)
from scripts.acceptance.verify_postgresql_timeout import (
    ROOT,
    Audit,
    capture_audit,
    clean,
    require_host,
    resolve_provider,
    visible_error_fields,
)

ENGINE = DatabaseType.ORACLE
INVENTORY = ROOT / "config/inventory/oracle/oracle.test.yaml"
FIXTURES = Path(__file__).with_name("fixtures") / "oracle_timeout" / "oracle"
DIGESTS = {
    "timeout_baseline.sql": "a03eda1b43d74801e4e3cb5298348773160c54b3d4424c468814bc137d6a953b",
    "timeout_baseline.yaml": "56fec96d29911798f8ab877ed9a140d14513663a2a7952a8a4875f2fb3254948",
    "timeout_identity.sql": "bd6b1b58ffea057ce7e176ca4a7204f75464cfb3462ab49ac047e2159ebb8356",
    "timeout_identity.yaml": "801ce474c599c87a9e5fa791fb696ca8c2c535377f67b24514021d35ce70830e",
    "timeout_normal.sql": "34140a27cb2023d8baf3258af1c87c0e120fd5618b7043cc369525d90ac79c3c",
    "timeout_normal.yaml": "9cffb531477ce164b3677fbb300742d076b6e1669f01e5763418539f5737bcdd",
}
PREFLIGHT_SCRIPTS = frozenset({
    "session_identity", "preflight", "preflight_privileges", "preflight_object_grants",
    "preflight_owned_objects", "connect",
})
BLOCKED_REASON = ("No reviewed workload can reliably delay an Oracle round-trip with "
                  "the existing read-only grants and a bounded resource cost.")


def load_asset(path: Path) -> DatabaseInstance:
    if path.resolve() != INVENTORY.resolve():
        raise ToolkitError("InventoryRejected", "Only the designated Oracle test Inventory is allowed.")
    assets = Inventory.load(path).instances
    if len(assets) != 1:
        raise ToolkitError("InventoryRejected", "Exactly one Oracle test asset is required.")
    asset = assets[0]
    if (asset.id, asset.db_type, asset.host, asset.port, asset.environment, asset.enabled) != (
        "oraclefree", ENGINE, "127.0.0.1", 1521, "test", True,
    ) or bool(asset.service_name) == bool(asset.sid):
        raise ToolkitError("InventoryRejected", "The Oracle test asset or connection target did not match.")
    return asset


def stage_registry(root: Path, *, observe_v_session: bool = False) -> SqlScriptRegistry:
    dest = root / "oracle"
    dest.mkdir(parents=True, exist_ok=False)
    for name in OracleAdapter.REQUIRED:
        for suffix in ("sql", "yaml"):
            filename = f"{name}.{suffix}"
            shutil.copyfile(ROOT / "sql" / "oracle" / filename, dest / filename)
    for filename, digest in DIGESTS.items():
        body = (FIXTURES / filename).read_text(encoding="utf-8")
        if hashlib.sha256(body.encode()).hexdigest() != digest:
            raise ToolkitError("FixtureRejected", "A reviewed Oracle fixture has changed.")
        (dest / filename).write_text(body, encoding="utf-8")
    if observe_v_session:
        (dest / "timeout_session_observe.sql").write_text(SAMPLE_SQL, encoding="utf-8")
        (dest / "timeout_session_observe.yaml").write_text(
            "name: timeout_session_observe\ndatabase_type: oracle\nrisk: READ_ONLY\n"
            f"timeout_seconds: 1\nmax_rows: {MAX_CANDIDATES + 1}\nmax_result_bytes: 1048576\n"
            "reviewed: true\ndescription: Read the pinned Oracle test session only.\n",
            encoding="utf-8",
        )
    registry = SqlScriptRegistry(root)
    for script in registry.validate():
        registry.authorize(script, ENGINE)
    return registry


def query(adapter: OracleAdapter, name: str) -> CollectorResult:
    return adapter.execute_readonly(adapter.registry.get(name, ENGINE))


def identity(adapter: OracleAdapter) -> dict[str, str]:
    result = query(adapter, "timeout_identity")
    if not result.success or result.truncated or result.row_count != 1:
        raise ToolkitError("IdentityUnavailable", "Oracle session identity could not be verified.",
                           database_error=result.database_error)
    row = result.rows[0]
    keys = ("instance_id", "session_id", "audit_session_id")
    if not all(re.fullmatch(r"[0-9]+", str(row.get(key))) for key in keys):
        raise ToolkitError("IdentityUnavailable", "Oracle session identity was incomplete.")
    return {key: str(row[key]) for key in keys}


def driver_error_evidence(exc: Exception) -> dict[str, Any]:
    value = exc.args[0] if exc.args else None
    message = getattr(value, "message", "") or str(exc)
    codes = re.findall(r"\b(?:DPY|DPI|ORA)-[0-9]{4,5}\b", str(message))
    full = getattr(value, "full_code", None)
    if isinstance(full, str) and re.fullmatch(r"(?:DPY|DPI|ORA)-[0-9]{4,5}", full):
        codes.insert(0, full)
    return {
        "exception_type": type(exc).__name__,
        "codes": list(dict.fromkeys(codes)),
        "full_code": full if isinstance(full, str) else None,
        "driver_code": getattr(value, "code", None),
        "driver_session_dead": getattr(value, "is_session_dead", None),
        "driver_recoverable": getattr(value, "isrecoverable", None),
        "message": str(message),
    }


@dataclass(frozen=True)
class GatePlan:
    phase: str
    script_name: str = "timeout_normal"
    fetch_rows_before_gate: int = 100


class TracedCursor:
    def __init__(self, cursor: Any, connection: TracedConnection, owner: TracedDriver) -> None:
        self.raw = cursor
        self.connection = connection
        self.owner = owner
        self.script_name: str | None = None
        self.fetch_call_number = 0
        self.rows_consumed = 0

    @property
    def description(self) -> Any:
        return self.raw.description

    @property
    def arraysize(self) -> int:
        return cast(int, self.raw.arraysize)

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        self.raw.arraysize = value

    @property
    def prefetchrows(self) -> int:
        return cast(int, self.raw.prefetchrows)

    @prefetchrows.setter
    def prefetchrows(self, value: int) -> None:
        self.raw.prefetchrows = value

    def _call(self, phase: str, operation: Callable[[], Any], *,
              fetch_call_number: int | None = None) -> Any:
        conn = self.connection
        begin = conn.clock()
        event: dict[str, Any] = {
            "phase": phase, "started_monotonic": begin, "call_timeout_ms": conn.call_timeout,
            "arraysize": self.arraysize, "prefetchrows": self.prefetchrows,
            "script_name": self.script_name,
        }
        if fetch_call_number is not None:
            event["fetch_call_number"] = fetch_call_number
            event["rows_consumed_before"] = self.rows_consumed
        if conn.timeout_updated_at is not None:
            # Adapter rounds up and the setter takes time. This is only an estimate.
            estimate = conn.timeout_updated_at + conn.call_timeout / 1000
            event["estimated_deadline_monotonic"] = estimate
            event["estimated_remaining_before_ms"] = max(0, estimate - begin) * 1000
        gate_armed = False
        try:
            gate_armed = self.owner.before_operation(
                phase, self.script_name, self.rows_consumed, fetch_call_number,
            )
            event["proxy_gate_armed"] = gate_armed
            if conn.session_observer is not None:
                if gate_armed:
                    conn.session_observer.mark(f"{phase.lower()}_gate_armed")
            if gate_armed:
                call_started = time.monotonic_ns()
                event["driver_call_started_monotonic_ns"] = call_started
                event["estimated_timeout_deadline_monotonic_ns"] = (
                    call_started + conn.call_timeout * 1_000_000 if conn.call_timeout > 0 else None
                )
            observer = conn.session_observer
            if observer is not None and phase == "Execute":
                observer.mark("execute_started")
                try:
                    result = operation()
                finally:
                    observer.mark("execute_returned")
            else:
                result = operation()
            event["call_completed"] = True
        except Exception as exc:
            if gate_armed:
                event["driver_error_observed_monotonic_ns"] = time.monotonic_ns()
            event["call_completed"] = False
            if isinstance(exc, ProxyStateError):
                event["proxy_error"] = {"exception_type": type(exc).__name__, "message": str(exc)}
            else:
                if conn.session_observer is not None:
                    conn.session_observer.mark("driver_error")
                evidence = driver_error_evidence(exc)
                event["driver_error"] = evidence
                if gate_armed:
                    self.owner.operation_failed(evidence)
            raise
        else:
            if phase == "Fetch":
                event["rows_returned"] = len(result)
                self.rows_consumed += len(result)
            return result
        finally:
            finished = conn.clock()
            event["elapsed_ms"] = (finished - begin) * 1000
            if "estimated_deadline_monotonic" in event:
                event["estimated_remaining_after_ms"] = max(
                    0, event["estimated_deadline_monotonic"] - finished,
                ) * 1000
            conn.trace.append(event)

    def execute(self, sql: str, parameters: dict[str, Any] | None = None) -> Any:
        self.script_name = self.owner.script_names.get(sql)
        self.fetch_call_number = 0
        self.rows_consumed = 0
        return self._call("Execute", lambda: self.raw.execute(sql, parameters)
                          if parameters is not None else self.raw.execute(sql))

    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]:
        self.fetch_call_number += 1
        return cast(Sequence[Sequence[Any]], self._call(
            "Fetch", lambda: self.raw.fetchmany(size), fetch_call_number=self.fetch_call_number,
        ))

    def close(self) -> None:
        self.raw.close()


class TracedConnection:
    def __init__(self, connection: Any, clock: Callable[[], float], owner: TracedDriver) -> None:
        self.raw = connection
        self.clock = clock
        self.owner = owner
        self.trace: list[dict[str, Any]] = []
        self.record = False
        self.closed = False
        self.timeout_updated_at: float | None = None
        self.session_observer: SessionObserver | None = None

    @property
    def autocommit(self) -> bool:
        return cast(bool, self.raw.autocommit)

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self.raw.autocommit = value

    @property
    def call_timeout(self) -> int:
        return cast(int, self.raw.call_timeout)

    @call_timeout.setter
    def call_timeout(self, value: int) -> None:
        self.raw.call_timeout = value
        if self.record:
            self.timeout_updated_at = self.clock()

    def cursor(self) -> Any:
        cursor = self.raw.cursor()
        return TracedCursor(cursor, self, self.owner) if self.record else cursor

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        observation = self.session_observer
        if observation is not None:
            observation.mark("target_close_started")
        try:
            self.raw.close()
            self.closed = True
        finally:
            if observation is not None:
                observation.mark("target_close_finished" if self.closed else "target_close_error")


class TracedDriver:
    def __init__(
        self,
        driver: Any,
        clock: Callable[[], float] = time.monotonic,
        *,
        script_names: dict[str, str] | None = None,
        proxy: OracleTcpDelayProxy | None = None,
        release_after_client_data: bool = False,
        release_minimum_hold_ms: int = 0,
    ) -> None:
        self.driver = driver
        self.clock = clock
        self.script_names = script_names or {}
        self.proxy = proxy
        self.release_after_client_data = release_after_client_data
        self.release_minimum_hold_seconds = release_minimum_hold_ms / 1000
        self.gate_plan: GatePlan | None = None
        self.gate_preparations: list[dict[str, Any]] = []

    def before_operation(
        self, phase: str, script_name: str | None, rows_consumed: int,
        fetch_call_number: int | None,
    ) -> bool:
        plan = self.gate_plan
        if self.proxy is None or plan is None or script_name != plan.script_name or phase != plan.phase:
            return False
        if phase == "Fetch" and (
            rows_consumed != plan.fetch_rows_before_gate
            or fetch_call_number != plan.fetch_rows_before_gate + 1
        ):
            return False
        quiescence = self.proxy.wait_quiescent()
        self.gate_preparations.append({"stage": phase, **quiescence})
        if not quiescence.get("confirmed"):
            raise ProxyStateError("Proxy could not confirm a quiet response path before arming.")
        if self.release_after_client_data:
            self.proxy.arm_gate(
                phase,
                quiescence=quiescence,
                release_after_post_hold_client_bytes=1,
                minimum_hold_seconds=self.release_minimum_hold_seconds,
            )
        else:
            self.proxy.arm_gate(phase, quiescence=quiescence)
        return True

    def operation_failed(self, evidence: dict[str, Any]) -> None:
        if self.proxy is None:
            return
        snapshot = self.proxy.snapshot()
        gate = snapshot.get("gate")
        if not isinstance(gate, dict) and snapshot.get("gates"):
            gate = snapshot["gates"][-1]
        if not isinstance(gate, dict):
            return
        codes = evidence.get("codes", [])
        timeout_reported = any(code in {"DPY-4024", "DPI-1067"} for code in codes)
        try:
            if timeout_reported:
                safe_evidence = {
                    "exception_type": evidence.get("exception_type"),
                    "codes": codes,
                    "full_code": evidence.get("full_code"),
                    "driver_session_dead": evidence.get("driver_session_dead"),
                }
                self.proxy.mark_timeout_observed(safe_evidence)
            active_gate = snapshot.get("gate")
            if isinstance(active_gate, dict) and active_gate.get("armed"):
                self.proxy.release_gate(discard_response=True, reason=(
                    "driver-timeout" if timeout_reported else "driver-operation-error"
                ))
        except ProxyStateError:
            # The original driver error is the evidence of record; a proxy race
            # is separately visible through the trace and proxy state.
            return

    def connect(self, **kwargs: Any) -> TracedConnection:
        return TracedConnection(self.driver.connect(**kwargs), self.clock, self)


def traced_query(adapter: OracleAdapter) -> tuple[CollectorResult, TracedConnection]:
    connection = adapter._connection
    if not isinstance(connection, TracedConnection):
        raise ToolkitError("TraceUnavailable", "The Oracle call tracer is unavailable.")
    connection.trace.clear()
    connection.timeout_updated_at = None
    connection.record = True
    try:
        return query(adapter, "timeout_normal"), connection
    finally:
        connection.record = False


ObserverFactory = Callable[[], AbstractContextManager[Any]]


def observer_not_ready(target: OracleAdapter, connection: TracedConnection) -> tuple[CollectorResult, TracedConnection]:
    now = datetime.now(UTC)
    connection.trace.clear()
    return CollectorResult(
        target.instance.id, "timeout_normal", now, now, 0, (), 0, False, False,
        error_type="ObserverNotReady", error_message="Target SQL was not executed because the observer was not ready.",
    ), connection


def observed_probe(
    target: OracleAdapter, seed: dict[str, str], factory: ObserverFactory, observations: dict[str, Any],
    case: str,
) -> tuple[CollectorResult, TracedConnection]:
    connection = target._connection
    if not isinstance(connection, TracedConnection):
        raise ToolkitError("TraceUnavailable", "The Oracle call tracer is unavailable.")
    registry = target.registry
    script = registry.get("timeout_session_observe", ENGINE)
    registry.authorize(script, ENGINE)
    if script.text.replace("\r\n", "\n").strip() != SAMPLE_SQL:
        raise ToolkitError("FixtureRejected", "The Oracle session observer query changed.")
    statement = registry.get("timeout_normal", ENGINE).text
    client_identifier = f"dbatoolkit-observe-{uuid.uuid4().hex}"
    try:
        connection.raw.client_identifier = client_identifier
    except Exception as exc:
        unavailable = Observation(seed, client_identifier, statement)
        unavailable.fail(exc)
        observations[case] = unavailable.report()
        return observer_not_ready(target, connection)
    try:
        # Thin stages the attribute until its next round trip. Reuse the
        # reviewed identity query to publish it before the observer binds.
        if identity(target) != seed:
            raise ToolkitError("IdentityChanged", "The tagged Oracle target identity changed.")
        obs = SessionObserver(factory, seed, statement, client_identifier)
        if not obs.prepare():
            observations[case] = obs.finish()
            return observer_not_ready(target, connection)
        connection.session_observer = obs
        succeeded = False
        try:
            result, traced = traced_query(target)
            succeeded = normal_passed(result)
            return result, traced
        finally:
            obs.mark("target_call_finished")
            connection.session_observer = None
            observations[case] = obs.finish(target_succeeded=succeeded)
    finally:
        if target._connection is connection and not connection.closed:
            connection.raw.client_identifier = ""


def normal_passed(result: CollectorResult) -> bool:
    return (result.success and not result.truncated and result.row_count == 101
            and [row.get("probe_value") for row in result.rows] == list(range(1, 102)))


def render_failure(output: Path, adapter: OracleAdapter, version: str, baseline: CollectorResult | None,
                   failed: CollectorResult, audit: Audit, secrets: tuple[str, ...], *,
                   prefix: str = "driver_failure", audit_start: int = 0) -> dict[str, bool]:
    results = tuple(
        replace(result, rows=tuple(clean(result.rows, secrets)),
                error_message=clean(result.error_message, secrets),
                database_error=clean(result.database_error, secrets))
        for result in (baseline, failed) if result is not None
    )
    failure = results[-1]
    error = failure.database_error or {}
    report = build_slow_report(
        replace(adapter.instance, credential_alias="[REDACTED]", connection={}),
        results, adapter.settings, version, required_collectors=("timeout_normal",),
    )
    payload = {"success": False, "collector_name": failure.collector_name, "error": error,
               "error_type": failure.error_type, "error_message": failure.error_message}
    structured = render_json({**payload, "result": asdict(failure),
                              "baseline": asdict(results[0]) if baseline is not None else None})
    cli = render_human("diagnose", payload)
    markup = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    audit_rows = [row for row in audit.events[audit_start:] if row["sql_script"] == failed.collector_name
                  and row["result"] == "failed"]
    elapsed = (error.get("timeout") or {}).get("elapsed_ms")
    jsonl = "".join(json.dumps({**row, "collector_elapsed_ms": elapsed}, ensure_ascii=False) + "\n"
                    for row in audit_rows)
    for suffix, text in {"json": structured, "txt": cli, "html": markup, "jsonl": jsonl}.items():
        with (output / f"{prefix}.{suffix}").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    projection = database_error_log_fields(error)
    checks = visible_error_fields(markup, cli, {**projection, "elapsed_ms": elapsed})
    return {
        "json": bool(error) and json.loads(structured)["error"] == error,
        "jsonl": bool(error) and len(audit_rows) == 1
        and all(audit_rows[0].get(key) == value for key, value in projection.items())
        and json.loads(jsonl)["collector_elapsed_ms"] == elapsed,
        "html": bool(error) and checks["html"],
        "cli": bool(error) and checks["cli"],
    }


def recover(adapter: OracleAdapter, previous: TracedConnection,
            before: dict[str, str], audit: Audit) -> dict[str, Any]:
    detached = adapter._connection is None
    refused = False
    if detached:
        try:
            query(adapter, "timeout_identity")
        except ToolkitError as exc:
            refused = exc.error_type == "PreflightRequired"
    summary: dict[str, Any] = {"status": "BLOCKED", "adapter_detached": detached,
                               "driver_close_returned": previous.closed,
                               "refused_before_preflight": refused}
    if not (detached and refused and previous.closed):
        return summary
    start = len(audit.events)
    try:
        adapter.preflight_checks.clear()
        adapter.connect()
        after = identity(adapter)
        successful = {event["sql_script"] for event in audit.events[start:] if event["result"] == "ok"}
        full_preflight = PREFLIGHT_SCRIPTS <= successful and adapter.preflight_checks.get("least_privilege") is True
        independent = normal_passed(query(adapter, "timeout_normal"))
        new = adapter._connection is not previous and after != before
        summary.update(status="PASS" if full_preflight and independent and new else "FAIL",
                       full_preflight=full_preflight, independent_select=independent, new_session=new,
                       identity=after)
    except ToolkitError as exc:
        summary.update(status="FAIL", error_type=exc.error_type, error=exc.database_error_payload())
    return summary


def exercise(target: OracleAdapter, observer: OracleAdapter, output: Path,
             audit: Audit, secrets: tuple[str, ...], *,
             observation_factory: ObserverFactory | None = None) -> dict[str, Any]:
    before = identity(target)
    observer_identity = identity(observer)
    if before == observer_identity:
        raise ToolkitError("ObserverRejected", "The observer did not get an independent session.")
    version = target.get_server_version()
    baseline = query(target, "timeout_baseline")
    if not baseline.success or baseline.truncated or baseline.rows != ({"baseline_value": 1},):
        raise ToolkitError("BaselineFailed", "Oracle baseline did not return the expected row.",
                           database_error=baseline.database_error)
    baseline_snapshot = render_json(clean(asdict(baseline), secrets))
    (output / "baseline.json").write_text(baseline_snapshot, encoding="utf-8")
    observations: dict[str, Any] = {}
    result, connection = (
        observed_probe(target, before, observation_factory, observations, "normal")
        if observation_factory else traced_query(target)
    )
    trace = connection.trace[:]
    summary: dict[str, Any] = {
        "status": "PARTIAL",
        "normal": "PASS" if normal_passed(result) else "FAIL",
        "effective_client_deadline_seconds": target.settings.effective_sql_timeout(
            target.registry.get("timeout_normal", ENGINE).metadata.timeout_seconds,
        ),
        "normal_trace": trace, "target_identity": before,
        "observer": {"identity": observer_identity, "query_stop_confirmed": False,
                     "lifecycle_status": "NOT_EXECUTED"},
        "execute_timeout": {"status": "BLOCKED", "reason": BLOCKED_REASON},
        "fetch_timeout": {"status": "BLOCKED", "reason": BLOCKED_REASON},
        "internal_round_trip_deadline": "ISOLATED_OFFLINE_TEST",
        "recovery": {"status": "NOT_EXECUTED"},
    }
    if observation_factory:
        summary["session_observation"] = observations
    if result.error_type == "ObserverNotReady":
        summary.update(status="INCOMPLETE", normal="NOT_EXECUTED", normal_result=asdict(result))
        return summary
    if result.success:
        (output / "normal.json").write_text(render_json(clean(asdict(result), secrets)), encoding="utf-8")
        return summary

    phase = (result.database_error or {}).get("timeout", {}).get("execution_phase")
    summary["unexpected_failure"] = {
        "result": asdict(result),
        "phase": phase, "attempts": sum(item["phase"] == "Execute" for item in trace),
        "partial_result_discarded": result.row_count == 0 and not result.rows,
        "output_parity": render_failure(output, target, version, baseline, result, audit, secrets),
    }
    summary["recovery"] = recover(target, connection, before, audit)
    summary["baseline_retained"] = render_json(clean(asdict(baseline), secrets)) == baseline_snapshot
    return summary


def proxy_gate_evidence(proxy: OracleTcpDelayProxy, stage: str) -> dict[str, Any] | None:
    gates = proxy.snapshot().get("gates", [])
    if not gates or not isinstance(gates[-1], dict) or gates[-1].get("stage") != stage:
        return None
    return cast(dict[str, Any], gates[-1])


def summarize_proxy_gate(
    gate: dict[str, Any] | None, driver_event: dict[str, Any] | None,
) -> dict[str, Any]:
    record = gate or {}
    event = driver_event or {}
    release_at = record.get("released_monotonic_ns")
    error_at = event.get("driver_error_observed_monotonic_ns")
    order = "UNKNOWN"
    if isinstance(release_at, int) and isinstance(error_at, int):
        if release_at < error_at:
            order = "RELEASE_BEFORE_ERROR_OBSERVED"
        elif release_at > error_at:
            order = "ERROR_OBSERVED_BEFORE_RELEASE"
        else:
            order = "SAME_CLOCK_TICK"
    quiet = record.get("pre_gate_quiescence")
    controlled = record.get("release_after_post_hold_client_bytes") is not None
    release_policy_met = not controlled or (
        record.get("release_mode") == "forward"
        and record.get("release_trigger_satisfied") is True
        and record.get("release_reason") == "post_hold_client_bytes_and_minimum_delay"
        and record.get("held_response_forwarded_bytes") == record.get("held_bytes")
    )
    evidence = {
        "request_bytes_observed": record.get("request_bytes", 0) > 0,
        "server_response_bytes_observed": record.get("response_bytes", 0) > 0,
        "server_response_held": record.get("held_bytes", 0) > 0,
        "response_forwarded_after_arm": record.get("forwarded_bytes"),
        "response_forwarded_before_release": record.get("forwarded_before_release_bytes"),
        "response_forwarded_after_release": record.get("forwarded_after_release_bytes"),
        "held_response_forwarded_bytes": record.get("held_response_forwarded_bytes"),
        "buffer_limit_exceeded": bool(record.get("buffer_limit_exceeded")),
        "driver_timeout_observed_by_proxy": bool(record.get("timeout_observed")),
        "pre_gate_quiescence_confirmed": isinstance(quiet, dict) and quiet.get("confirmed") is True,
        "release_policy_met": release_policy_met,
        "response_held_monotonic_ns": record.get("response_held_monotonic_ns"),
        "released_monotonic_ns": release_at,
        "driver_error_observed_monotonic_ns": error_at,
        "driver_call_started_monotonic_ns": event.get("driver_call_started_monotonic_ns"),
        "estimated_timeout_deadline_monotonic_ns": event.get("estimated_timeout_deadline_monotonic_ns"),
        "release_vs_driver_error_observation": order,
    }
    evidence["verified"] = (
        evidence["request_bytes_observed"]
        and evidence["server_response_bytes_observed"]
        and evidence["server_response_held"]
        and evidence["response_forwarded_before_release"] == 0
        and evidence["pre_gate_quiescence_confirmed"]
        and not evidence["buffer_limit_exceeded"]
        and release_policy_met
    )
    return evidence


def run_proxy_timeout_case(
    *,
    stage: str,
    target: OracleAdapter,
    version: str,
    baseline: CollectorResult,
    baseline_snapshot: str,
    output: Path,
    audit: Audit,
    secrets: tuple[str, ...],
    traced_driver: TracedDriver,
    proxy: OracleTcpDelayProxy,
    observation_factory: ObserverFactory | None = None,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_prefix = "execute_timeout" if stage == "Execute" else "fetch_timeout"
    before_identity = identity(target)
    old_connection = target._connection
    if not isinstance(old_connection, TracedConnection):
        raise ToolkitError("TraceUnavailable", "The Oracle proxy target connection is not traced.")
    audit_start = len(audit.events)
    traced_driver.gate_plan = GatePlan(stage)
    try:
        result, connection = (
            observed_probe(target, before_identity, observation_factory, observations, case_prefix)
            if observation_factory and observations is not None else traced_query(target)
        )
    finally:
        traced_driver.gate_plan = None

    trace = list(connection.trace)
    database_error = result.database_error or {}
    timeout = database_error.get("timeout", {}) if isinstance(database_error, dict) else {}
    driver_event = next((item for item in reversed(trace) if "driver_error" in item), None)
    driver_error = driver_event.get("driver_error") if driver_event else None
    driver_codes = driver_error.get("codes", []) if isinstance(driver_error, dict) else []
    driver_timeout = any(code in {"DPY-4024", "DPI-1067"} for code in driver_codes)
    error_category = database_error.get("error_category") if isinstance(database_error, dict) else None
    phase_matches = timeout.get("execution_phase") == stage
    partial_result_discarded = not result.success and result.row_count == 0 and not result.rows
    execute_events = [item for item in trace if item.get("phase") == "Execute"]
    attempt_count = len(execute_events)
    connection_detached = target._connection is None
    driver_close_returned = connection.closed
    proxy_connection_closed = False
    recovery: dict[str, Any] = {"status": "NOT_EXECUTED", "reason": "timeout_discard_not_confirmed"}
    if not result.success and connection_detached and driver_close_returned:
        proxy_connection_closed = wait_for_proxy_listener(proxy)
        recovery = recover(target, connection, before_identity, audit)

    gate = proxy_gate_evidence(proxy, stage)
    if stage == "Fetch":
        successful_fetches = [
            item for item in trace
            if item.get("phase") == "Fetch" and item.get("fetch_call_number", 0) <= 100
            and item.get("call_completed") is True and item.get("rows_returned") == 1
        ]
        gate_fetch = next((
            item for item in trace
            if item.get("phase") == "Fetch" and item.get("fetch_call_number") == 101
            and item.get("proxy_gate_armed") is True
        ), None)
        fetch_evidence = {
            "execute_succeeded": len(execute_events) == 1 and execute_events[0].get("call_completed") is True,
            "rows_consumed_before_gate": sum(item.get("rows_returned", 0) for item in successful_fetches),
            "successful_fetch_calls_before_gate": len(successful_fetches),
            "gate_on_fetch_call_101": gate_fetch is not None,
            "pre_gate_quiescence_confirmed": bool(
                isinstance(gate, dict)
                and isinstance(gate.get("pre_gate_quiescence"), dict)
                and gate["pre_gate_quiescence"].get("confirmed") is True
            ),
            "quiescence_scope": (
                gate.get("pre_gate_quiescence", {}).get("scope")
                if isinstance(gate, dict)
                and isinstance(gate.get("pre_gate_quiescence"), dict)
                else None
            ),
        }
        fetch_evidence["verified"] = (
            fetch_evidence["execute_succeeded"] is True
            and fetch_evidence["rows_consumed_before_gate"] == 100
            and fetch_evidence["successful_fetch_calls_before_gate"] == 100
            and fetch_evidence["gate_on_fetch_call_101"] is True
            and fetch_evidence["pre_gate_quiescence_confirmed"] is True
        )
    else:
        fetch_evidence = {"verified": True, "not_applicable": True}

    gate_evidence = summarize_proxy_gate(gate, driver_event)

    parity = render_failure(
        output, target, version, baseline, result, audit, secrets,
        prefix=f"{case_prefix}_report", audit_start=audit_start,
    ) if not result.success else {"json": False, "jsonl": False, "html": False, "cli": False}
    baseline_retained = (
        render_json(clean(asdict(baseline), secrets)) == baseline_snapshot
    )
    discard_confirmed = (
        connection_detached and driver_close_returned and proxy_connection_closed
    )
    driver_timeout_verified = not result.success and driver_timeout and error_category == "TIMEOUT" and phase_matches
    connection_recovery_verified = (
        discard_confirmed
        and recovery.get("status") == "PASS"
        and partial_result_discarded
        and attempt_count == 1
        and baseline_retained
        and all(parity.values())
    )
    timeout_case_pass = (
        driver_timeout_verified
        and connection_recovery_verified
        and gate_evidence["verified"] is True
        and fetch_evidence["verified"] is True
    )
    return {
        "status": "PASS" if timeout_case_pass else "PARTIAL",
        "phase": stage,
        "result_success": result.success,
        "error_type": result.error_type,
        "database_error": database_error,
        "driver_exception": driver_error,
        "execution_phase_matches": phase_matches,
        "single_execution_attempt": attempt_count == 1,
        "partial_result_discarded": partial_result_discarded,
        "DRIVER_TIMEOUT_VERIFIED": driver_timeout_verified,
        "PROXY_GATE_VERIFIED": gate_evidence["verified"],
        "CONNECTION_RECOVERY_VERIFIED": connection_recovery_verified,
        "DRIVER_REPORTED": {
            "observed": driver_event is not None,
            "call_timeout_confirmed": driver_timeout,
            "error_category": error_category,
            "native_error": driver_error,
        },
        "CONNECTION_DISCARDED": {
            "adapter_detached": connection_detached,
            "driver_close_returned": driver_close_returned,
            "proxy_connection_closed": proxy_connection_closed,
            "confirmed": discard_confirmed,
        },
        "SERVER_QUERY_STOP_CONFIRMED": False,
        "proxy_gate": gate,
        "proxy_gate_evidence": gate_evidence,
        "fetch_evidence": fetch_evidence,
        "output_parity": parity,
        "baseline_retained": baseline_retained,
        "recovery": recovery,
    }


def wait_for_proxy_listener(proxy: OracleTcpDelayProxy, timeout_seconds: float = 3.0) -> bool:
    end = time.monotonic() + timeout_seconds
    while time.monotonic() < end:
        if proxy.state == ProxyState.LISTENING:
            return True
        if proxy.state in {ProxyState.FAILED, ProxyState.CLOSED}:
            return False
        time.sleep(0.01)
    return proxy.state == ProxyState.LISTENING


def exercise_with_tcp_delay_proxy(
    target: OracleAdapter,
    observer: OracleAdapter,
    output: Path,
    audit: Audit,
    secrets: tuple[str, ...],
    traced_driver: TracedDriver,
    proxy: OracleTcpDelayProxy,
    *,
    scenario: str = "both",
    observation_factory: ObserverFactory | None = None,
) -> dict[str, Any]:
    before = identity(target)
    observer_identity = identity(observer)
    if before == observer_identity:
        raise ToolkitError("ObserverRejected", "The observer did not get an independent session.")
    version = target.get_server_version()
    baseline = query(target, "timeout_baseline")
    if not baseline.success or baseline.truncated or baseline.rows != ({"baseline_value": 1},):
        raise ToolkitError("BaselineFailed", "Oracle baseline did not return the expected row.",
                           database_error=baseline.database_error)
    baseline_snapshot = render_json(clean(asdict(baseline), secrets))
    with (output / "baseline.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(baseline_snapshot)

    observations: dict[str, Any] = {}
    normal, _normal_connection = (
        observed_probe(target, before, observation_factory, observations, "normal")
        if observation_factory else traced_query(target)
    )
    normal_ok = normal_passed(normal)
    if normal_ok:
        with (output / "normal.json").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(render_json(clean(asdict(normal), secrets)))

    summary: dict[str, Any] = {
        "status": "PARTIAL",
        "mode": "LOCAL_TCP_DELAY_PROXY",
        "requested_timeout_scenario": scenario,
        "normal": "PASS" if normal_ok else "FAIL",
        "target_identity": before,
        "observer": {
            "identity": observer_identity,
            "transport": "DIRECT_TO_INVENTORY_TARGET",
            "query_stop_confirmed": False,
        },
        "proxy_endpoint": {
            "listen_host": proxy.LISTEN_HOST,
            "listen_port": proxy.listen_port,
            "upstream_host": proxy.upstream_host,
            "upstream_port": proxy.upstream_port,
            "payload_logged": False,
        },
        "baseline_retained": True,
        "execute_timeout": {"status": "NOT_EXECUTED"},
        "fetch_timeout": {"status": "NOT_EXECUTED"},
        "server_query_stop_confirmed": False,
    }
    if observation_factory:
        summary["session_observation"] = observations
    if not normal_ok:
        summary["status"] = "INCOMPLETE"
        if normal.error_type == "ObserverNotReady":
            summary["normal"] = "NOT_EXECUTED"
        summary["normal_result"] = asdict(normal)
        return summary

    stages = ("Execute", "Fetch") if scenario == "both" else (scenario.capitalize(),)
    for stage in stages:
        key = f"{stage.lower()}_timeout"
        if stage == "Fetch" and scenario == "both" and (
            summary["execute_timeout"]["recovery"].get("status") != "PASS"
        ):
            summary[key]["reason"] = "Execute case did not establish a fresh Preflight-ready connection."
            break
        case = run_proxy_timeout_case(
            stage=stage, target=target, version=version, baseline=baseline,
            baseline_snapshot=baseline_snapshot, output=output, audit=audit, secrets=secrets,
            traced_driver=traced_driver, proxy=proxy,
            observation_factory=observation_factory, observations=observations,
        )
        summary[key] = case
        summary["baseline_retained"] = summary["baseline_retained"] and case["baseline_retained"]
    summary["status"] = (
        "PARTIAL"
        if summary["server_query_stop_confirmed"] is False
        else "INCOMPLETE"
    )
    return summary


def output_path(path: Path) -> Path:
    path = path.resolve()
    if path.parent != ROOT / "reports" or not path.name.startswith("oracle-timeout-") or path.exists():
        raise ToolkitError("OutputRejected", "Use a new oracle-timeout directory under reports.")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--settings", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--credential-backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    parser.add_argument("--credential-store-path", type=Path)
    parser.add_argument(
        "--tcp-delay-proxy", action="store_true",
        help="Run bounded Execute and Fetch call-timeout checks through a loopback TCP relay.",
    )
    parser.add_argument(
        "--proxy-release-after-client-data", action="store_true",
        help=("After a held response, release it after post-hold client bytes are observed "
              "and the configured minimum hold has elapsed; payload is never inspected."),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observe-v-session", action="store_true",
                        help="Sample the target from a separate direct Oracle connection.")
    parser.add_argument(
        "--proxy-scenario", choices=("both", "execute", "fetch"),
        help="Select the timeout case to run through the proxy (default: both).",
    )
    args = parser.parse_args()
    if args.proxy_release_after_client_data and not args.tcp_delay_proxy:
        parser.error("--proxy-release-after-client-data requires --tcp-delay-proxy")
    if args.proxy_scenario is not None and not args.tcp_delay_proxy:
        parser.error("--proxy-scenario requires --tcp-delay-proxy")
    output = None
    summary: dict[str, Any] = {"status": "INCOMPLETE", "evidence_type": "NOT_EXECUTED"}
    secrets: tuple[str, ...] = ()
    proxy: OracleTcpDelayProxy | None = None
    stage = "host"
    try:
        require_host()
        stage = "inventory"
        asset = load_asset(args.inventory)
        dest = output_path(args.output)
        settings = cast(OracleSettings, _effective_settings(
            asset, args.settings if args.settings.is_file() else None,
        ))
        settings = replace(settings, diagnostic_sql_timeout_seconds=2,
                           connect_timeout=min(settings.connect_timeout, 5))
        stage = "driver"
        driver = cast(Any, load_driver())
        thin = driver.is_thin_mode()
        summary.update(driver_version=driver.__version__, driver_mode="Thin" if thin else "Thick",
                       oracle_client_version=None if thin else driver.clientversion())
        dest.mkdir(parents=True, exist_ok=False)
        output = dest
        if not thin:
            raise ToolkitError("DriverModeRejected", "The project Oracle acceptance path requires Thin mode.")
        stage = "credentials"
        provider, source = resolve_provider(asset, args.credential_backend, args.credential_store_path)
        secrets = (provider.credential.password, provider.credential.username, asset.credential_alias)
        summary.update(asset=asset.id, credential_source=source)
        stage = "preflight"
        with TemporaryDirectory(prefix="oracle-timeout-registry-") as temp, capture_audit(secrets) as audit:
            registry = stage_registry(Path(temp), observe_v_session=args.observe_v_session)
            scripts = registry.validate()
            script_names = {script.text: script.metadata.name for script in scripts}
            if args.tcp_delay_proxy:
                proxy = OracleTcpDelayProxy(
                    asset.host,
                    asset.port,
                    run_id=dest.name,
                    max_gate_hold_seconds=3.5 if args.proxy_release_after_client_data else 6.0,
                )
                proxy.start()
                target_driver = TracedDriver(
                    ProxyEndpointDriver(driver, proxy),
                    script_names=script_names,
                    proxy=proxy,
                    release_after_client_data=args.proxy_release_after_client_data,
                    release_minimum_hold_ms=(
                        round(settings.diagnostic_sql_timeout_seconds * 1000) + 100
                        if args.proxy_release_after_client_data else 0
                    ),
                )
            else:
                target_driver = TracedDriver(driver, script_names=script_names)
            observer_driver = TracedDriver(driver, script_names=script_names)

            @contextmanager
            def open_observer() -> Iterator[Any]:
                with OracleAdapter(asset, provider, registry, settings, driver=observer_driver) as adapter:
                    connection = cast(TracedConnection, adapter._connection)
                    yield connection.raw

            factory = open_observer if args.observe_v_session else None
            with OracleAdapter(asset, provider, registry, settings, driver=target_driver) as target:
                connection = cast(TracedConnection, target._connection)
                if connection.raw.thin is not True:
                    raise ToolkitError("DriverModeRejected", "Connected Oracle driver mode did not match Thin.")
                summary.update(evidence_type="WINDOWS_LOCAL_REAL_E2E", server_version=connection.raw.version,
                               preflight_server_version=target.get_server_version(),
                               preflight=dict(target.preflight_checks),
                               account_classification="NON_PRIVILEGED")
                with OracleAdapter(asset, provider, registry, settings, driver=observer_driver) as observer:
                    stage = "probe"
                    if proxy is None:
                        summary.update(exercise(target, observer, output, audit, secrets,
                                                observation_factory=factory))
                    else:
                        summary.update(exercise_with_tcp_delay_proxy(
                            target, observer, output, audit, secrets, target_driver, proxy,
                            scenario=args.proxy_scenario or "both",
                            observation_factory=factory,
                        ))
                        summary["proxy_release_policy"] = (
                            {
                                "trigger": "at_least_one_client_byte_after_server_response_held",
                                "minimum_hold_ms": target_driver.release_minimum_hold_seconds * 1000,
                                "maximum_hold_seconds": proxy.max_gate_hold_seconds,
                                "payload_inspected": False,
                            }
                            if args.proxy_release_after_client_data else {
                                "trigger": "driver_operation_outcome",
                                "maximum_hold_seconds": proxy.max_gate_hold_seconds,
                                "payload_inspected": False,
                            }
                        )
    except Exception as exc:
        summary.update(status="INCOMPLETE", stage=stage,
                       error_type=exc.error_type if isinstance(exc, ToolkitError) else type(exc).__name__)
        if isinstance(exc, ToolkitError) and exc.database_error_payload() is not None:
            summary["error"] = exc.database_error_payload()
    finally:
        if proxy is not None:
            cleanup_ok = proxy.close()
            summary["proxy_cleanup"] = {"success": cleanup_ok, "state": proxy.state}
            if not cleanup_ok:
                summary["status"] = "INCOMPLETE"
                summary["proxy_cleanup_error"] = proxy.snapshot().get("failure")
            if output is not None:
                try:
                    proxy.write_trace(output / "oracle_proxy_trace.json")
                except Exception as exc:
                    summary["status"] = "INCOMPLETE"
                    summary["proxy_trace_error"] = type(exc).__name__
    summary = clean(summary, secrets)
    if output is not None:
        with (output / "summary.json").open("x", encoding="utf-8") as stream:
            stream.write(render_json(summary) + "\n")
    print(json.dumps({"status": summary["status"], "stage": stage,
                      "error_type": summary.get("error_type"), "output": str(output) if output else None}))
    return 1 if summary["status"] == "PARTIAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
