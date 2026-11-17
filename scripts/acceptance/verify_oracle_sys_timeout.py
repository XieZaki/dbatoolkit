"""Bounded SYS-only driver experiments for the local Oracle Free lab."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from dba.database_errors import sanitize_vendor_text
from dba.lab_config import (
    LabDatabaseConfig,
    Secret,
    connect_with_lab_secret,
    read_lab_connection_profile,
)
from scripts.acceptance.oracle_ping_recovery_proxy import PingRecoveryRelay
from scripts.acceptance.verify_oracle_timeout import INVENTORY, load_asset

if TYPE_CHECKING:
    import oracledb

ROOT = Path(__file__).resolve().parents[2]
CALL_MS = 2000
WAIT_SECONDS = 6
IDENTITY = """
SELECT SYS_CONTEXT('USERENV', 'SESSION_USER'), SYS_CONTEXT('USERENV', 'ISDBA'),
       SYS_CONTEXT('USERENV', 'CON_NAME'), SYS_CONTEXT('USERENV', 'SERVICE_NAME'),
       SYS_CONTEXT('USERENV', 'INSTANCE'), SYS_CONTEXT('USERENV', 'SID'),
       SYS_CONTEXT('USERENV', 'SESSIONID'), SYS_CONTEXT('USERENV', 'CON_ID'),
       (SELECT cdb FROM v$database) FROM dual
"""
SAMPLE = """
SELECT s.sid, s.serial#, s.username, s.audsid, s.con_id, s.client_identifier,
       s.sql_id, s.sql_child_number, s.sql_exec_id, s.sql_exec_start, s.status,
       s.event, s.wait_class, s.last_call_et, s.prev_sql_id, s.prev_child_number,
       s.prev_exec_id, s.prev_exec_start,
       (SELECT COUNT(*) FROM v$sql q
        WHERE q.sql_id = s.sql_id AND q.child_number = s.sql_child_number
          AND q.con_id = s.con_id) AS current_candidates,
       (SELECT COUNT(*) FROM v$sql q
        WHERE q.sql_id = s.sql_id AND q.child_number = s.sql_child_number
          AND q.con_id = s.con_id AND q.sql_text = :target_sql) AS current_exact
FROM v$session s WHERE s.sid = :target_sid AND s.con_id = :target_con
"""
COLUMNS = (
    "sid", "serial", "username", "audsid", "con_id", "client_identifier", "sql_id", "child",
    "sql_exec_id", "sql_exec_start", "status", "event", "wait_class", "last_call_et",
    "prev_sql_id", "prev_child", "prev_exec_id", "prev_exec_start", "current_candidates", "current_exact",
)


def error_code(exc: Exception) -> str:
    detail = exc.args[0] if exc.args else None
    code = getattr(detail, "full_code", None)
    return code if isinstance(code, str) and re.fullmatch(r"(?:ORA|DPY|DPI)-\d{4,5}", code) else type(exc).__name__


def connect(config: LabDatabaseConfig, password: str, *, relay_port: int | None = None) -> oracledb.Connection:
    import oracledb

    return oracledb.connect(
        host="127.0.0.1", port=config.port if relay_port is None else relay_port,
        service_name=config.database, user=config.user,
        password=password, mode=oracledb.AuthMode.SYSDBA, tcp_connect_timeout=3, retry_count=0,
        disable_oob=False,
    )


def read_identity(connection: oracledb.Connection) -> tuple[Any, ...]:
    import oracledb

    connection.call_timeout = CALL_MS
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY)
        identity = cursor.fetchone()
    if (identity is None or identity[:3] != ("SYS", "TRUE", "FREEPDB1")
            or str(identity[3]).casefold() != "freepdb1" or identity[8] != "YES"
            or not str(connection.version).startswith("23.") or not oracledb.is_thin_mode()):
        raise RuntimeError("Oracle lab identity verification failed")
    return tuple(identity)


def collect(observer: oracledb.Connection, identity: tuple[Any, ...], tag: str, statement: str,
            samples: list[dict[str, Any]], ready: threading.Event, stop: threading.Event) -> None:
    try:
        observer.call_timeout = 750
        deadline = time.monotonic() + 12
        with observer.cursor() as cursor:
            while not stop.is_set() and time.monotonic() < deadline:
                started = time.perf_counter_ns()
                cursor.execute(SAMPLE, target_sql=statement, target_sid=int(identity[5]), target_con=int(identity[7]))
                rows = cursor.fetchmany(2)
                sample = dict(zip(COLUMNS, rows[0], strict=True)) if len(rows) == 1 else {}
                matches = (sample.get("username") == "SYS" and sample.get("client_identifier") == tag
                           and str(sample.get("audsid")) == str(identity[6]))
                samples.append({"started_ns": started, "ended_ns": time.perf_counter_ns(),
                                "row_count": len(rows), "identity_matches": matches,
                                "session": sample if matches else None})
                if len(samples) == 1:
                    ready.set()
                if stop.wait(0.075):
                    break
    except Exception as exc:
        samples.append({"observer_error": error_code(exc)})
    finally:
        ready.set()


def execution_evidence(samples: list[dict[str, Any]], start: int, reported: int) -> dict[str, Any]:
    current = ("sql_id", "child", "sql_exec_id", "sql_exec_start")
    previous = ("prev_sql_id", "prev_child", "prev_exec_id", "prev_exec_start")
    result: dict[str, Any] = {"active_after_threshold": False, "active_after_driver_return": False,
                              "SERVER_QUERY_STOP_CONFIRMED": False, "stop_sample_ns": None}
    if not samples or not samples[0].get("identity_matches"):
        return result
    serial = samples[0]["session"]["serial"]
    execution = None
    idle_samples = 0
    for sample in samples[1:]:
        row = sample.get("session")
        if row is None or row["serial"] != serial:
            break
        if sample["started_ns"] <= start:
            continue
        key = tuple(row[field] for field in current)
        if row["status"] == "ACTIVE" and row["current_candidates"] == row["current_exact"] == 1 and None not in key:
            if execution is not None and key != execution:
                break
            execution = key
            idle_samples = 0
            result["active_after_threshold"] |= sample["started_ns"] > start + CALL_MS * 1_000_000
            result["active_after_driver_return"] |= sample["started_ns"] > reported
        elif execution is not None:
            if row["status"] != "INACTIVE" or tuple(row[field] for field in previous) != execution:
                break
            idle_samples += 1
            if idle_samples == 1:
                result["stop_sample_ns"] = sample["ended_ns"]
            if idle_samples == 2:
                result["SERVER_QUERY_STOP_CONFIRMED"] = True
                return result
    result["stop_sample_ns"] = None
    return result


def workload(phase: str, token: str) -> str:
    if phase == "execute":
        return f"BEGIN /* {token} */ SYS.DBMS_LOCK.SLEEP({WAIT_SECONDS}); END;"
    return (
        "WITH FUNCTION lab_row(n NUMBER) RETURN NUMBER IS BEGIN "
        f"IF n = 2 THEN SYS.DBMS_LOCK.SLEEP({WAIT_SECONDS}); END IF; RETURN n; END; "
        f"SELECT /* {token} */ lab_row(LEVEL) FROM dual CONNECT BY LEVEL <= 2"
    )


def attempt(cursor: oracledb.Cursor, phase: str, statement: str) -> dict[str, Any]:
    operations: list[tuple[str, Callable[[], Any]]] = [("execute", lambda: cursor.execute(statement))]
    if phase == "fetch":
        operations.extend((("first_fetch", cursor.fetchone), ("next_fetch", cursor.fetchone)))
    result: dict[str, Any] = {"calls": []}
    for label, operation in operations:
        started = time.perf_counter_ns()
        try:
            value = operation()
        except Exception as exc:
            result["calls"].append({"phase": label, "started_ns": started, "ended_ns": time.perf_counter_ns(),
                                    "driver_error": error_code(exc)})
            break
        result["calls"].append({"phase": label, "started_ns": started, "ended_ns": time.perf_counter_ns(),
                                "value": value if label != "execute" else None})
    return result


def run_case(config: LabDatabaseConfig, secret: Secret, phase: str) -> dict[str, Any]:
    tag = f"DBATK_{phase}_{uuid4().hex}"
    statement = workload(phase, tag)
    samples: list[dict[str, Any]] = []
    result: dict[str, Any] = {"scenario": phase, "call_timeout_ms": CALL_MS, "wait_seconds": WAIT_SECONDS,
                              "samples": samples, "SERVER_QUERY_STOP_CONFIRMED": False}
    with connect_with_lab_secret(secret, connect, config) as observer:
        observe_identity = read_identity(observer)
        with connect_with_lab_secret(secret, connect, config) as target:
            target.client_identifier = tag
            identity = read_identity(target)
            if observe_identity[4] != identity[4] or observe_identity[7] != identity[7]:
                raise RuntimeError("Observer instance or container differs")
            ready, stop = threading.Event(), threading.Event()
            worker = threading.Thread(target=collect, args=(observer, identity, tag, statement, samples, ready, stop))
            worker.start()
            try:
                if not ready.wait(3) or not samples or not samples[0].get("identity_matches"):
                    result["status"] = "OBSERVER_NOT_READY"
                else:
                    with target.cursor() as cursor:
                        cursor.arraysize = 1
                        cursor.prefetchrows = 0
                        result.update(attempt(cursor, phase, statement))
                        last = result["calls"][-1]
                        result["status"] = "DRIVER_ERROR" if last.get("driver_error") else "NO_DRIVER_ERROR"
                        # Keep the target untouched while the independent connection observes the wait.
                        worker.join(max(0, 8 - (time.perf_counter_ns() - last["started_ns"]) / 1e9))
                        stop.set()
                        worker.join()
                        result.update(execution_evidence(samples, last["started_ns"], last["ended_ns"]))
                        if result["SERVER_QUERY_STOP_CONFIRMED"]:
                            try:
                                cursor.execute("SELECT 1 FROM dual")
                                result["same_connection_select"] = cursor.fetchone() == (1,)
                            except Exception as exc:
                                result["same_connection_error"] = error_code(exc)
            finally:
                stop.set()
                worker.join()
                result["observer_joined"] = True
        result["old_connection_closed"] = True
    with connect_with_lab_secret(secret, connect, config) as fresh:
        new = read_identity(fresh)
        with fresh.cursor() as cursor:
            cursor.execute("SELECT serial# FROM v$session WHERE sid = :sid AND con_id = :con",
                           sid=int(new[5]), con=int(new[7]))
            fresh_serial = cursor.fetchone()
            old_sample = samples[0].get("session") if samples else None
            result["fresh_identity_changed"] = (
                (new[5], fresh_serial[0]) != (identity[5], old_sample["serial"])
                if fresh_serial and old_sample else None
            )
            cursor.execute("SELECT 1 FROM dual")
            result["fresh_select"] = cursor.fetchone() == (1,)
    return result


def ping_error(exc: Exception) -> dict[str, Any]:
    detail = exc.args[0] if exc.args else None
    result: dict[str, Any] = {"exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}"}
    for key in ("full_code", "code", "message", "context", "offset", "isrecoverable", "is_session_dead"):
        value = getattr(detail, key, None)
        result[key] = sanitize_vendor_text(value) if isinstance(value, str) else (
            value if type(value) in {bool, int} else None
        )
    chain: list[dict[str, Any]] = []
    seen = {id(exc)}
    cause = exc.__cause__ or exc.__context__
    while cause is not None and id(cause) not in seen and len(chain) < 4:
        seen.add(id(cause))
        chain.append({"exception_type": f"{type(cause).__module__}.{type(cause).__qualname__}",
                      "errno": getattr(cause, "errno", None), "winerror": getattr(cause, "winerror", None)})
        cause = cause.__cause__ or cause.__context__
    result.update(errno=getattr(exc, "errno", None), winerror=getattr(exc, "winerror", None), chain=chain)
    return result


class PingClock:
    """One clock and sequence for the harness and its relay event sink."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0

    def sample(self) -> tuple[int, int]:
        with self._lock:
            timestamp = time.perf_counter_ns()
            self._sequence += 1
            return timestamp, self._sequence

    def mark(self, fields: dict[str, Any], name: str) -> None:
        fields[f"{name}_ns"], fields[f"{name}_sequence"] = self.sample()


class PingRelayEvents(list[dict[str, int | str]]):
    """Stamp events when the unmodified relay publishes them."""

    def __init__(self, clock: PingClock) -> None:
        super().__init__()
        self._clock = clock

    def append(self, event: dict[str, int | str], /) -> None:
        timestamp, sequence = self._clock.sample()
        super().append({**event, "relay_monotonic_ns": event["ns"], "ns": timestamp, "sequence": sequence})


def timed_ping(connection: oracledb.Connection, clock: PingClock | None = None) -> dict[str, Any]:
    clock = clock if clock is not None else PingClock()
    report: dict[str, Any] = {"configured_ms": CALL_MS, "readback_ms": connection.call_timeout, "exception": None}
    clock.mark(report, "started")
    try:
        connection.ping()
    except Exception as exc:
        clock.mark(report, "exception")
        report["exception"] = ping_error(exc)
    finally:
        clock.mark(report, "ended")
    try:
        report["healthy"] = connection.is_healthy()
    except Exception as exc:
        report.update(healthy=None, health_error=error_code(exc))
    return report


def _ping_position(fields: dict[str, Any], prefix: str = "") -> tuple[int, int]:
    return int(fields.get(f"{prefix}ns", -1)), int(fields.get(f"{prefix}sequence", 0))


def ping_verdict(scenario: str, call: dict[str, Any], events: list[dict[str, int | str]],
                 followup: dict[str, Any] | None) -> str:
    if any(e["event"] == "relay_failed" for e in events) or call["readback_ms"] != CALL_MS:
        return "INCONCLUSIVE"
    names = [e["event"] for e in events]
    if scenario in {"direct", "transparent"}:
        if call["exception"] is None and call["healthy"] is True and (
            scenario == "direct" or ("response_forwarded" in names and "marker_seen" not in names)
        ):
            return "PING_OK"
        return "INCONCLUSIVE"
    error = call["exception"] or {}
    interrupts = [e for e in events if e["event"] == "interrupt_forwarded"]
    if len(interrupts) != 1 or not (
        _ping_position(call, "started_") <= _ping_position(interrupts[0]) <= _ping_position(call, "exception_")
    ) or any(
        e["event"] == "eof" and e.get("side") == "upstream"
        and _ping_position(e) <= _ping_position(call, "ended_") for e in events
    ):
        return "INCONCLUSIVE"
    if scenario == "release" and error.get("full_code") == "DPY-4024" and any(
        e["event"] == "response_released"
        and _ping_position(interrupts[0]) <= _ping_position(e) <= _ping_position(call, "exception_")
        for e in events
    ):
        if followup and followup["exception"] is None and followup["healthy"] is True:
            return "RECOVERY_RECEIVE_COMPLETED_CONNECTION_USABLE"
        return "RECOVERY_RECEIVE_COMPLETED_CONNECTION_UNVERIFIED"
    if scenario == "hold" and error.get("full_code") == "DPY-4011" and call["healthy"] is False and not any(
        e["event"] in {"response_forwarded", "response_released"} for e in events
    ) and any(
        e["event"] == "eof" and e.get("side") == "client" and _ping_position(e) >= _ping_position(interrupts[0])
        for e in events
    ) and any(
        reason in (error.get("message") or "").casefold()
        for reason in ("socket timed out while recovering from previous socket timeout",
                       "socket timed out while awaiting break response from server")
    ) and "transport_ended_before_cleanup" in names:
        return "RECOVERY_WAIT_TIMEOUT"
    return "INCONCLUSIVE"


def run_ping(config: LabDatabaseConfig, secret: Secret, scenario: str, result: dict[str, Any]) -> None:
    clock = PingClock()
    relay = None if scenario == "direct" else PingRecoveryRelay(config.port, scenario)
    if relay is not None:
        relay.events = PingRelayEvents(clock)
    clock_info = time.get_clock_info("perf_counter")
    result.update(disable_oob=False, supports_oob_source_inferred=False, supports_oob_runtime=None,
                  SERVER_QUERY_STOP_CONFIRMED=False, lifecycle={},
                  clock={"name": "perf_counter_ns", "implementation": clock_info.implementation,
                         "resolution_seconds": clock_info.resolution})
    lifecycle = result["lifecycle"]
    try:
        with relay if relay is not None else nullcontext():
            clock.mark(lifecycle, "connect_started")
            connection = connect_with_lab_secret(secret, connect, config,
                                                 relay_port=relay.local_port if relay else None)
            clock.mark(lifecycle, "connected")
            try:
                identity = read_identity(connection)
                clock.mark(lifecycle, "identity_cursor_closed")
                result.update(version=connection.version, container=identity[2], service=identity[3],
                              sysdba=True, mode="Thin", sid=connection.session_id,
                              serial=connection.serial_num, evidence="WINDOWS_LOCAL_REAL_E2E")
                if relay:
                    relay.arm()
                call = timed_ping(connection, clock)
                result["ping"] = call
                error = call["exception"] or {}
                if scenario == "hold" and error.get("full_code") == "DPY-4011" and relay:
                    # Observe transport EOF before harness cleanup can produce it.
                    if relay.finished.wait(0.25):
                        relay.record("transport_ended_before_cleanup")
                if (scenario == "release" and error.get("full_code") == "DPY-4024"
                        and error.get("is_session_dead") is not True and call["healthy"] is True
                        and relay and relay.released):
                    result["followup"] = timed_ping(connection, clock)
            finally:
                clock.mark(lifecycle, "connection_close_started")
                connection.close()
                clock.mark(lifecycle, "connection_closed")
    finally:
        events = relay.events if relay else []
        result["relay_events"] = events
        result["interrupt_observed"] = any(e["event"] == "interrupt_forwarded" for e in events)
        result["relay_joined"] = not relay.worker.is_alive() if relay else None
    result["status"] = ping_verdict(scenario, result["ping"], result["relay_events"], result.get("followup"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("preflight", "execute", "fetch", "ping"), required=True)
    parser.add_argument("--ping-scenario", choices=("direct", "transparent", "release", "hold"), default="direct")
    parser.add_argument("--approved-ping-relay", action="store_true",
                        help="Use only after explicit host approval for the A2 experiment.")
    args = parser.parse_args()
    if args.ping_scenario != "direct" and (args.phase != "ping" or not args.approved_ping_relay):
        parser.error("The PING relay requires the ping phase and explicit host approval.")
    if sys.platform != "win32" or Path(sys.executable).resolve() != ROOT / ".venv/Scripts/python.exe":
        parser.error("Use the project Windows virtual environment from authorized Windows PowerShell Desktop.")
    args.output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"scenario": args.phase, "status": "INCOMPLETE",
                              "SERVER_QUERY_STOP_CONFIRMED": False}
    try:
        import oracledb

        report["driver_version"] = oracledb.__version__
        asset = load_asset(INVENTORY)
        config, secret = read_lab_connection_profile(ROOT / "DBAToolkit-LoadTest/local_config.py", "oracle")
        if (config.host.casefold() not in {"127.0.0.1", "localhost", "::1"} or config.port != asset.port
                or (asset.service_name or "").casefold() != "freepdb1"
                or config.database.casefold() != "freepdb1" or config.user.casefold() != "sys"):
            raise RuntimeError("Lab configuration does not match the approved Oracle Free target")
        if args.phase == "ping":
            if oracledb.__version__ != "3.4.2" or not oracledb.is_thin_mode():
                raise RuntimeError("The PING experiment requires python-oracledb 3.4.2 Thin")
            report["ping_scenario"] = args.ping_scenario
            run_ping(config, secret, args.ping_scenario, report)
            return int(report["status"] in {"INCONCLUSIVE", "RECOVERY_RECEIVE_COMPLETED_CONNECTION_UNVERIFIED"})
        with connect_with_lab_secret(secret, connect, config) as conn:
            identity = read_identity(conn)
            report.update(version=conn.version, container=identity[2], service=identity[3],
                          sysdba=True, mode="Thin", cdb=True, evidence="WINDOWS_LOCAL_REAL_E2E")
            with conn.cursor() as cursor:
                cursor.execute("SELECT status FROM all_objects WHERE owner='SYS' "
                               "AND object_name='DBMS_LOCK' AND object_type='PACKAGE'")
                row = cursor.fetchone()
                report["dbms_lock_package"] = row[0] if row else None
                if row != ("VALID",):
                    raise RuntimeError("DBMS_LOCK package is unavailable")
                cursor.execute("BEGIN SYS.DBMS_LOCK.SLEEP(0.01); END;")
                report["dbms_lock_sleep_callable"] = True
        if args.phase != "preflight":
            report.update(run_case(config, secret, args.phase))
        else:
            report["status"] = "PREFLIGHT_PASSED"
    except Exception as exc:
        report["error"] = error_code(exc)
    finally:
        (args.output / "summary.json").write_text(json.dumps(report, default=str, indent=2), encoding="utf-8")
    return int("error" in report or report["status"] == "OBSERVER_NOT_READY")


if __name__ == "__main__":
    raise SystemExit(main())
