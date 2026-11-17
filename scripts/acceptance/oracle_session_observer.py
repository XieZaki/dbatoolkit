"""Acceptance-only Oracle session sampling. No cancellation inference."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from dba.core import DatabaseType
from dba.security import check_sql

# Read every child of the observed SQL_ID up to the safety limit. Compare in
# Python so session collation cannot relax the exact statement check.
MAX_CANDIDATES = 128
MAX_FULLTEXT_CHARS = 4096
FIRST_POLL_GRACE_SECONDS = 0.010
MIN_POST_COMPLETION_SAMPLES = 2
SAMPLE_SQL = f"""
SELECT s.sid, s.serial#, s.audsid, s.con_id,
       TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')), s.service_name,
       s.client_identifier, s.status, s.sql_id, s.sql_child_number, s.sql_exec_id, s.sql_exec_start,
       s.prev_sql_id, s.prev_child_number, s.prev_exec_id, s.prev_exec_start,
       s.event, s.wait_class, s.state, s.last_call_et,
       q.sql_id AS candidate_sql_id, q.child_number, q.con_id AS candidate_con_id,
       q.plan_hash_value, q.sql_text, q.sql_fulltext
FROM v$session s
LEFT JOIN v$sql q ON (q.sql_id = s.sql_id OR q.sql_id = s.prev_sql_id)
  AND s.client_identifier = :client_identifier
WHERE (s.client_identifier = :client_identifier OR (:include_pinned = 1 AND s.sid = :target_sid))
  AND s.con_id = TO_NUMBER(SYS_CONTEXT('USERENV', 'CON_ID'))
FETCH FIRST {MAX_CANDIDATES + 1} ROWS ONLY
""".strip()

IDENTITY = ("sid", "serial_number", "audsid", "con_id", "instance_id", "service_name")
CURRENT = ("sql_id", "sql_exec_id", "sql_exec_start")
PREVIOUS = ("prev_sql_id", "prev_exec_id", "prev_exec_start")
COLUMNS = (*IDENTITY, "client_identifier", "status", "sql_id", "sql_child_number",
           "sql_exec_id", "sql_exec_start", "prev_sql_id", "prev_child_number", "prev_exec_id", "prev_exec_start",
           "event", "wait_class", "state", "last_call_et")
CANDIDATE_COLUMNS = ("sql_id", "child_number", "con_id", "plan_hash_value", "sql_text", "sql_fulltext")
ACCESS_PROBES = {
    "SYS.V_$SESSION": "SELECT 1 FROM SYS.V_$SESSION WHERE 1 = 0",
    "SYS.V_$SQL": "SELECT 1 FROM SYS.V_$SQL WHERE 1 = 0",
}


def text_fingerprint(text: str, expected: str) -> dict[str, Any]:
    raw, reference = text.encode("utf-8"), expected.encode("utf-8")
    offset = next((i for i, (a, b) in enumerate(zip(raw, reference, strict=False)) if a != b), None)
    if offset is None and len(raw) != len(reference):
        offset = min(len(raw), len(reference))

    def nearby(data: bytes, at: int) -> str:
        whitespace = {9: r"\t", 10: r"\n", 13: r"\r", 32: r"\x20"}
        part = "".join(whitespace.get(value, "*") for value in data[max(0, at - 2):at + 3])
        return part + ("<EOF>" if at >= len(data) else "")

    return {
        "characters": len(text), "utf8_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        "first_difference_byte_offset": offset,
        "difference_context": (
            {"actual": nearby(raw, offset), "expected": nearby(reference, offset)}
            if offset is not None else None
        ),
    }


def text_comparison(text: Any, fulltext: Any, expected: str) -> dict[str, Any]:
    # SQL_TEXT can differ from the complete statement Oracle exposes in the
    # same child's SQL_FULLTEXT. Compare the complete value without normalizing it.
    prefix = isinstance(text, str) and (
        len(text.encode("utf-8")) >= 997 or len(text) >= 1000
        or (len(expected.encode("utf-8")) >= 1000 and expected.startswith(text))
    )
    if isinstance(fulltext, str):
        match_status = "EXACT_MATCH" if fulltext == expected else "MISMATCH"
    elif fulltext is not None or not isinstance(text, str) or prefix:
        match_status = "UNVERIFIED"
    else:
        match_status = "EXACT_MATCH" if text == expected else "MISMATCH"
    return {
        "sql_text_fingerprint": text_fingerprint(text, expected) if isinstance(text, str) else None,
        "sql_fulltext_fingerprint": text_fingerprint(fulltext, expected) if isinstance(fulltext, str) else None,
        "sql_text_may_be_truncated": prefix,
        "sql_fulltext_status": "CAPTURED" if isinstance(fulltext, str) else fulltext,
        "sql_text_match_status": match_status,
        "sql_text_matches": match_status == "EXACT_MATCH",
    }


def read_cursor_fulltext(lob: Any) -> str | dict[str, Any]:
    try:
        size = len(lob.encode("utf-16-le")) // 2 if isinstance(lob, str) else lob.size()
        if type(size) is not int or size < 0:
            return {"status": "UNKNOWN_SIZE"}
        if size > MAX_FULLTEXT_CHARS:
            return {"status": "OVER_LIMIT"}
        value = lob if isinstance(lob, str) else lob.read(1, size) if size else ""
        # Oracle uses UCS-2 code points for CLOB lengths and offsets.
        if not isinstance(value, str) or len(value.encode("utf-16-le")) // 2 != size:
            return {"status": "INCOMPLETE"}
        return value
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error": error_evidence(exc)}


def clock_evidence() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("monotonic", "perf_counter"):
        info = time.get_clock_info(name)
        read = time.monotonic_ns if name == "monotonic" else time.perf_counter_ns
        previous = read()
        deltas = []
        until = time.perf_counter_ns() + 20_000_000
        while time.perf_counter_ns() < until:
            now = read()
            if now > previous:
                deltas.append(now - previous)
            previous = now
        result[name] = {**vars(info), "resolution_ns": info.resolution * 1e9,
                        "measured_min_positive_delta_ns": min(deltas, default=None)}
    return result


def error_evidence(exc: Exception) -> dict[str, str | None]:
    detail = exc.args[0] if exc.args else None
    code = getattr(detail, "full_code", None)
    native = getattr(detail, "code", None)
    if not isinstance(code, str) or not re.fullmatch(r"(?:ORA|DPY|DPI)-\d{4,5}", code):
        code = f"ORA-{native:05d}" if type(native) is int and 0 < native <= 99999 else None
    return {"type": type(exc).__name__, "code": code}


class Observation:
    def __init__(self, seed: dict[str, str], client_identifier: str, statement: str,
                 clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self.seed = seed
        self.client_identifier = client_identifier
        # python-oracledb 3.4.2 Cursor._normalize_statement uses str.strip().
        self.expected_statement = statement.strip()
        self.text_fingerprints = {"registry": text_fingerprint(statement, self.expected_statement),
                                  "driver_strip": text_fingerprint(self.expected_statement, self.expected_statement)}
        self.clock = clock
        self.event_sequence = 0
        self.target_succeeded = False
        self.identity: dict[str, Any] | None = None
        self.execution: tuple[Any, ...] | None = None
        self.execution_child: int | None = None
        self.baseline_executions: set[tuple[Any, ...]] = set()
        self.samples: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []
        self.markers: list[dict[str, Any]] = []
        self.error: dict[str, str | None] | None = None
        self.status = "UNKNOWN"
        self.pinned_identity_lost = False
        self.execution_changed = False
        self.access_probes: dict[str, Any] = {}

    def event(self) -> dict[str, int]:
        self.event_sequence += 1
        return {"event_sequence": self.event_sequence, "perf_counter_ns": self.clock()}

    def transition(self, state: str, at: int) -> None:
        self.status = state
        if not self.transitions or self.transitions[-1]["state"] != state:
            self.transitions.append({"state": state, "perf_counter_ns": at,
                                     "sample_sequence": len(self.samples)})

    def sample(self, rows: list[tuple[Any, ...]], *, prepare: bool = False,
               start: dict[str, int] | None = None, end: dict[str, int] | None = None) -> None:
        start = start if start is not None else self.event()
        end = end if end is not None else self.event()
        row, reason, candidates = self._decode(rows)
        state, reason = self._classify(row, len(rows), prepare, reason)
        self.samples.append({"sequence": len(self.samples) + 1, **end, "sample_started": start,
                             "row_count": len(rows), "result": state, "reason": reason,
                             "cursor_candidates": candidates, **row})
        self.transition(state, end["perf_counter_ns"])

    def _decode(self, rows: list[tuple[Any, ...]]) -> tuple[dict[str, Any], str | None, list[dict[str, Any]]]:
        if not rows:
            return {}, None, []
        if len(rows) > MAX_CANDIDATES:
            return {}, "CANDIDATE_LIMIT_EXCEEDED", []
        width = len(COLUMNS)
        if any(len(raw) != width + len(CANDIDATE_COLUMNS) for raw in rows):
            return {}, "INVALID_SAMPLE_SHAPE", []
        session = rows[0][:width]
        if any(raw[:width] != session for raw in rows):
            return {}, "AMBIGUOUS_SESSION_SAMPLE", []
        row = {key: value.isoformat() if isinstance(value, datetime) else value
               for key, value in zip(COLUMNS, session, strict=True)}
        row["client_identifier_matches"] = row.pop("client_identifier") == self.client_identifier
        row["exact_cursor_match"] = 0
        candidates = []
        for raw in rows:
            sql_id, child, container, plan, text, fulltext = raw[width:]
            if sql_id is None:
                continue
            candidates.append({
                "sql_id": sql_id, "child_number": child, "con_id": container, "plan_hash_value": plan,
                "sql_id_matches": sql_id == row["sql_id"],
                "child_number_matches": child is not None and child == row["sql_child_number"],
                "con_id_matches": container is not None and container == row["con_id"],
                **text_comparison(text, fulltext, self.expected_statement),
            })
        scoped = [c for c in candidates if c["sql_id_matches"] and c["child_number_matches"] and c["con_id_matches"]]
        if len(scoped) > 1:
            return row, "AMBIGUOUS_CHILD_CURSOR", candidates
        if scoped and scoped[0]["sql_text_matches"]:
            row["exact_cursor_match"] = 1
            return row, None, candidates
        if not candidates:
            reason = "NO_SQL_ID_CANDIDATES"
        elif row["sql_child_number"] is None:
            reason = "MISSING_SQL_CHILD_NUMBER"
        elif scoped:
            reason = ("SQL_TEXT_UNVERIFIED" if scoped[0]["sql_text_match_status"] == "UNVERIFIED"
                      else "SQL_TEXT_MISMATCH")
        else:
            reason = "CHILD_OR_CONTAINER_MISMATCH"
        return row, reason, candidates

    def _classify(self, row: dict[str, Any], count: int, prepare: bool,
                  reason: str | None) -> tuple[str, str | None]:
        if not count:
            if self.identity is not None:
                self.pinned_identity_lost = True
                return "SESSION_ABSENT", "SESSION_NOT_VISIBLE"
            return "UNKNOWN", "CLIENT_IDENTIFIER_NOT_FOUND"
        if not row or any(row[k] is None for k in IDENTITY):
            if self.identity is not None and reason == "AMBIGUOUS_SESSION_SAMPLE":
                self.pinned_identity_lost = True
            return "UNKNOWN", reason or "MISSING_SESSION_IDENTITY"
        identity = {k: row[k] for k in IDENTITY}
        if (str(row["sid"]), str(row["audsid"]), str(row["instance_id"])) != (
            self.seed["session_id"], self.seed["audit_session_id"], self.seed["instance_id"],
        ) or (self.identity is not None and identity != self.identity):
            self.pinned_identity_lost = True
            return "UNKNOWN", "SESSION_REBOUND"
        if not row["client_identifier_matches"]:
            self.pinned_identity_lost = True
            return "UNKNOWN", "CLIENT_IDENTIFIER_MISMATCH"
        current = tuple(row[k] for k in CURRENT)
        if prepare:
            self.identity = identity
            previous = tuple(row[k] for k in PREVIOUS)
            self.baseline_executions = {current, previous}
            return "PREPARED", None
        if self.pinned_identity_lost or self.identity is None:
            return "UNKNOWN", "SESSION_IDENTITY_UNAVAILABLE"
        if reason == "AMBIGUOUS_CHILD_CURSOR":
            return "UNKNOWN", reason
        if not all(x is not None for x in current):
            if (self.execution and not self.execution_changed
                    and row["status"] == "INACTIVE" and row["sql_id"] is None):
                return "TARGET_INACTIVE", None
            return "UNKNOWN", "MISSING_EXECUTION_KEY"
        child = row["sql_child_number"]
        if self.execution is not None and (
            current != self.execution or (child is not None and child != self.execution_child)
        ):
            self.execution_changed = True
            return "UNKNOWN", "EXECUTION_CHANGED"
        if self.execution_changed:
            return "UNKNOWN", "EXECUTION_CHANGED"
        if reason == "SQL_TEXT_UNVERIFIED":
            return "UNKNOWN", reason
        if not row["exact_cursor_match"]:
            return ("NOT_OBSERVED" if self.execution is None else "UNKNOWN"), reason
        if any(
            current == prior or (current[0] == prior[0] and None in prior)
            for prior in self.baseline_executions
        ):
            return ("NOT_OBSERVED" if self.execution is None else "UNKNOWN"), "PREEXISTING_EXECUTION"
        if row["status"] == "ACTIVE":
            if self.execution is None:
                self.execution = current
                self.execution_child = child
            return "TARGET_ACTIVE", None
        if row["status"] == "INACTIVE" and self.execution is not None:
            return "TARGET_INACTIVE", None
        return "NOT_OBSERVED", "TARGET_NOT_ACTIVE"

    def fail(self, exc: Exception) -> None:
        self.error = error_evidence(exc)
        code = self.error["code"]
        state = "UNKNOWN"
        if code in {"ORA-00942", "ORA-01031"}:
            state = "PERMISSION_DENIED"
        elif code == "ORA-00904":
            state = "UNSUPPORTED"
        stamp = self.event()
        self.samples.append({"sequence": len(self.samples) + 1, **stamp,
                             "result": state, "error": self.error})
        self.transition(state, stamp["perf_counter_ns"])

    def mark(self, phase: str) -> None:
        self.markers.append({"phase": phase, **self.event()})

    @staticmethod
    def _before(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return bool(a["event_sequence"] < b["event_sequence"] and a["perf_counter_ns"] < b["perf_counter_ns"])

    def _sample_window(self, sample: dict[str, Any], started: dict[str, Any],
                       returned: dict[str, Any], finished: dict[str, Any]) -> str:
        first = sample["sample_started"]
        if self._before(sample, started):
            return "BEFORE_TARGET"
        if self._before(finished, first):
            return "AFTER_TARGET"
        if self._before(started, first) and self._before(sample, returned):
            return "EXECUTE"
        if self._before(returned, first) and self._before(sample, finished):
            return "AFTER_EXECUTE_BEFORE_FINISH"
        return "BOUNDARY_AMBIGUOUS"

    @staticmethod
    def _consistent_key(observed: tuple[Any, ...], expected: tuple[Any, ...]) -> bool:
        # Missing components cannot certify an execution, but known
        # conflicting components rule it out even on a boundary sample.
        return all(value is None or value == wanted for value, wanted in zip(observed, expected, strict=True))

    def _completed(self, samples: list[dict[str, Any]],
                   active: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
        if not self.target_succeeded or self.pinned_identity_lost or self.execution_changed or self.error:
            return [], "TARGET_OR_IDENTITY_UNVERIFIED"
        baseline = next((s for s in samples if s["result"] == "PREPARED"), {})
        baseline_key = tuple(baseline.get(field) for field in PREVIOUS)
        if any(value is None for value in (*baseline_key, baseline.get("prev_child_number"))):
            return [], "INCOMPLETE_PREV_BASELINE"
        post = [s for s in samples if s.get("attribution_window") == "AFTER_TARGET"]
        if not post:
            return [], "NO_POST_COMPLETION_SAMPLE"
        eligible = []
        observed: set[tuple[Any, ...]] = set()
        for s in post:
            if (not s.get("client_identifier_matches") or self.identity is None
                    or any(s.get(k) != self.identity[k] for k in IDENTITY)):
                return [], "SESSION_IDENTITY_UNAVAILABLE"
            # Another current SQL or a new previous key can overwrite the
            # correlation. Never select a later apparently matching row.
            if s.get("sql_id") is not None or s.get("status") != "INACTIVE":
                return [], "OTHER_SQL_OR_NON_IDLE_AFTER_COMPLETION"
            prev = tuple(s.get(field) for field in PREVIOUS)
            child = s.get("prev_child_number")
            if any(value is None for value in (*prev, child)):
                return [], "INCOMPLETE_PREV_EXECUTION_KEY"
            observed.add((*prev, child))
            scoped = [c for c in s["cursor_candidates"]
                      if c["sql_id"] == prev[0] and c["child_number"] == child and c["con_id_matches"]]
            if len(scoped) != 1 or not scoped[0]["sql_text_matches"]:
                return [], "PREV_CURSOR_UNVERIFIED"
            if prev in self.baseline_executions or prev == baseline_key:
                return [], "PREEXISTING_PREV_EXECUTION"
            if any(tuple(a[field] for field in CURRENT) != prev or a["sql_child_number"] != child for a in active):
                return [], "PREV_DIFFERS_FROM_VERIFIED_ACTIVE"
            eligible.append(s)
        if len(observed) != 1:
            return [], "PREV_EXECUTION_CHANGED"
        completed_key = next(iter(observed))
        for s in samples:
            if s.get("attribution_window") not in {"EXECUTE", "AFTER_EXECUTE_BEFORE_FINISH", "BOUNDARY_AMBIGUOUS"}:
                continue
            if s.get("error") or s.get("reason") in {
                "AMBIGUOUS_SESSION_SAMPLE", "AMBIGUOUS_CHILD_CURSOR", "CANDIDATE_LIMIT_EXCEEDED",
                "SESSION_REBOUND", "SESSION_NOT_VISIBLE", "CLIENT_IDENTIFIER_MISMATCH",
            }:
                return [], "AMBIGUOUS_EXECUTION_WINDOW"
            if (self.identity is None or not s.get("client_identifier_matches")
                    or any(s.get(k) != self.identity[k] for k in IDENTITY)):
                return [], "SESSION_IDENTITY_UNAVAILABLE"
            current = tuple(s.get(field) for field in (*CURRENT, "sql_child_number"))
            previous = tuple(s.get(field) for field in (*PREVIOUS, "prev_child_number"))
            if not self._consistent_key(current, completed_key):
                return [], "OTHER_SQL_IN_TARGET_WINDOW"
            if not any(self._consistent_key(previous, key) for key in
                       (completed_key, (*baseline_key, baseline["prev_child_number"]))):
                return [], "OTHER_SQL_IN_TARGET_WINDOW"
            if any(c["sql_id_matches"] and c["child_number_matches"] and c["con_id_matches"]
                   and not c["sql_text_matches"] for c in s["cursor_candidates"]):
                return [], "TARGET_CURSOR_UNVERIFIED"
            previous_cursors = [
                c for c in s["cursor_candidates"]
                if previous[0] is not None and previous[-1] is not None
                and c["sql_id"] == previous[0] and c["child_number"] == previous[-1] and c["con_id_matches"]
            ]
            if len(previous_cursors) > 1:
                return [], "AMBIGUOUS_EXECUTION_WINDOW"
            if (previous == completed_key and previous_cursors
                    and not previous_cursors[0]["sql_text_matches"]):
                return [], "TARGET_CURSOR_UNVERIFIED"
        if len(eligible) < MIN_POST_COMPLETION_SAMPLES or any(
            not self._before(a, b["sample_started"]) for a, b in zip(eligible, eligible[1:], strict=False)
        ):
            return [], "INSUFFICIENT_STABLE_POST_COMPLETION_SAMPLES"
        return eligible, None

    def report(self) -> dict[str, Any]:
        marks = {phase: [m for m in self.markers if m["phase"] == phase]
                 for phase in ("execute_started", "execute_returned", "target_call_finished", "driver_error")}
        start = marks["execute_started"]
        end = marks["execute_returned"]
        finished = marks["target_call_finished"]
        samples = [dict(s) for s in self.samples]
        complete_window = (len(start) == len(end) == len(finished) == 1
                           and self._before(start[0], end[0]) and self._before(end[0], finished[0]))
        active = []
        for sample in samples:
            if complete_window and sample.get("sample_started"):
                window = self._sample_window(sample, start[0], end[0], finished[0])
                sample["attribution_window"] = window
                if window == "BOUNDARY_AMBIGUOUS":
                    sample["result"] = "UNKNOWN"
                    sample["attribution_excluded_reason"] = "AMBIGUOUS_EXECUTION_WINDOW"
            if sample["result"] != "TARGET_ACTIVE":
                continue
            if (len(start) == len(end) == 1 and self._before(start[0], sample["sample_started"])
                    and self._before(sample, end[0])):
                sample["result"] = "ACTIVE_VERIFIED"
                active.append(sample)
            else:
                sample.update(result="UNKNOWN", reason="ACTIVE_OUTSIDE_EXECUTE_WINDOW")
        completed: list[dict[str, Any]] = []
        completion_reason: str | None = "TARGET_WINDOW_UNVERIFIED"
        if complete_window and not marks["driver_error"]:
            completed, completion_reason = self._completed(samples, active)
        for sample in completed:
            sample.update(result="COMPLETED_BEFORE_SAMPLE", reason=None)
        if not active:
            for sample in samples:
                if sample["result"] == "TARGET_INACTIVE":
                    sample.update(result="UNKNOWN", reason="NO_VERIFIED_ACTIVE_EXECUTION")
        transitions: list[dict[str, Any]] = []
        for sample in samples:
            if not transitions or transitions[-1]["state"] != sample["result"]:
                transitions.append({"state": sample["result"], "perf_counter_ns": sample["perf_counter_ns"],
                                    "sample_sequence": sample["sequence"], "event_sequence": sample["event_sequence"]})
        states = {t["state"] for t in self.transitions}
        reasons = {s.get("reason") for s in samples}
        pinned = {k: active[-1][k] for k in CURRENT} if active else None
        completed_identity = {k: completed[0][k] for k in (*PREVIOUS, "prev_child_number")} if completed else None
        status = samples[-1]["result"] if samples else self.status
        if self.status in {"PERMISSION_DENIED", "UNSUPPORTED"} or (self.error and self.status == "UNKNOWN"):
            status = self.status
        windows = [{"start": s["sample_started"], "end": {
            "event_sequence": s["event_sequence"], "perf_counter_ns": s["perf_counter_ns"],
        }} for s in samples if "sample_started" in s]
        return {
            "observer_enabled": True,
            "observer_status": status,
            "target_identity": self.identity,
            "client_identifier": self.client_identifier,
            "execution_identity": pinned if status != "UNKNOWN" else None,
            "last_verified_execution_identity": pinned,
            "last_verified_sql_child_number": active[-1]["sql_child_number"] if active else None,
            "active_verified": bool(active),
            "completed_before_sample": bool(completed),
            "completed_execution_identity": completed_identity,
            "completion_unverified_reason": completion_reason,
            "text_fingerprints": self.text_fingerprints,
            "clock_source": "perf_counter_ns",
            "clock_info": clock_evidence(),
            "first_sample_window": windows[0] if windows else None,
            "last_sample_window": windows[-1] if windows else None,
            "samples": samples,
            "state_transitions": transitions,
            "phase_markers": list(self.markers),
            "first_active_observed": active[0]["perf_counter_ns"] if active else None,
            "last_active_observed": active[-1]["perf_counter_ns"] if active else None,
            "active_after_driver_error_observed": "UNKNOWN",
            "session_disappeared": True if "SESSION_ABSENT" in states else "UNKNOWN",
            "identity_rebound": True if "SESSION_REBOUND" in reasons else "UNKNOWN",
            "observation_error": self.error,
            "access_probes": dict(self.access_probes),
            "query_stop_confirmed": False,
        }


class SessionObserver:
    """Worker owns its independent connection, including its closure."""

    def __init__(self, open_connection: Callable[[], AbstractContextManager[Any]],
                 seed: dict[str, str], statement: str, client_identifier: str,
                 *, clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self.observation = Observation(seed, client_identifier, statement, clock)
        self._open = open_connection
        self._setup_finished = threading.Event()
        self._execute_started = threading.Event()
        self._target_finished = threading.Event()
        self._ready = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._deadline_reached = False
        self._thread = threading.Thread(target=self._run, name="oracle-session-observer", daemon=True)

    def prepare(self) -> bool:
        self._thread.start()
        if not self._setup_finished.wait(5):
            self._stop.set()
            with self._lock:
                self.observation.error = {"type": "ObserverPreparationTimeout", "code": None}
                self.observation.transition("UNKNOWN", self.observation.clock())
            return False
        return self._ready

    def mark(self, phase: str) -> None:
        with self._lock:
            self.observation.mark(phase)
        if phase == "execute_started":
            self._execute_started.set()
        elif phase == "target_call_finished":
            self._target_finished.set()

    def _wait_for_first_poll(self) -> bool:
        # Set up before Execute, then avoid competing for the first few
        # milliseconds. A completed short probe can be sampled immediately.
        if not self._execute_started.wait(5) or self._stop.is_set():
            return False
        finished = self._target_finished.wait(FIRST_POLL_GRACE_SECONDS)
        if self._stop.is_set():
            return False
        self.mark("first_poll_target_finished" if finished else "first_poll_grace_elapsed")
        return True

    def _read(self, cursor: Any, *, prepare: bool = False) -> None:
        with self._lock:
            start = self.observation.event()
        cursor.execute(SAMPLE_SQL, client_identifier=self.observation.client_identifier,
                       include_pinned=int(not prepare), target_sid=int(self.observation.seed["session_id"]))
        rows = cursor.fetchmany(MAX_CANDIDATES + 1)
        rows = self._read_fulltext(rows)
        with self._lock:
            if not self._stop.is_set():
                self.observation.sample(rows, prepare=prepare, start=start, end=self.observation.event())

    def _read_fulltext(self, rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        # Keep the fetch bounded, and only dereference a LOB on a candidate
        # with the observed current or previous child and container.
        if len(rows) > MAX_CANDIDATES:
            return rows
        result = []
        width = len(COLUMNS)
        lob_reads = 0
        for raw in rows:
            if len(raw) != width + len(CANDIDATE_COLUMNS):
                return rows
            sql_id, child, con_id, _, _, lob = raw[width:]
            s = dict(zip(COLUMNS, raw[:width], strict=True))
            relevant = (sql_id is not None and child is not None and con_id == s["con_id"]
                        and (sql_id, child) in {(s["sql_id"], s["sql_child_number"]),
                                                (s["prev_sql_id"], s["prev_child_number"])})
            fulltext: str | dict[str, Any] | None = None
            if relevant and lob is not None:
                if lob_reads >= 2:
                    fulltext = {"status": "READ_LIMIT"}
                else:
                    lob_reads += 1
                    fulltext = read_cursor_fulltext(lob)
            result.append((*raw[:-1], fulltext))
        return result

    def _probe_access(self, cursor: Any) -> None:
        for name, sql in ACCESS_PROBES.items():
            if self._stop.is_set():
                break
            try:
                check_sql(sql, DatabaseType.ORACLE)
                cursor.execute(sql)
            except Exception as exc:
                result = {"status": "UNAVAILABLE", "error": error_evidence(exc)}
            else:
                result = {"status": "READABLE"}
            with self._lock:
                self.observation.access_probes[name] = result

    def _run(self) -> None:
        self.mark("observer_thread_started")
        try:
            check_sql(SAMPLE_SQL, DatabaseType.ORACLE)
            with self._open() as connection:
                if self._stop.is_set():
                    return
                connection.call_timeout = 500
                with connection.cursor() as cursor:
                    cursor.arraysize = cursor.prefetchrows = MAX_CANDIDATES + 1
                    try:
                        self._read(cursor, prepare=True)
                        if self.observation.identity is None or self._stop.is_set():
                            return
                        self.mark("observer_ready")
                        self._ready = True
                        self._setup_finished.set()
                        if not self._wait_for_first_poll():
                            return
                        deadline = self.observation.clock() + 7_000_000_000
                        while not self._stop.is_set() and self.observation.clock() < deadline:
                            self._read(cursor)
                            if self._stop.wait(0.15):
                                break
                        with self._lock:
                            self._deadline_reached = self.observation.clock() >= deadline
                            if self.observation.status == "PREPARED":
                                self.observation.transition("NOT_OBSERVED", self.observation.clock())
                    except Exception as exc:
                        with self._lock:
                            self.observation.fail(exc)
                        if error_evidence(exc)["code"] in {"ORA-00942", "ORA-01031"}:
                            self._probe_access(cursor)
        except Exception as exc:
            with self._lock:
                self.observation.fail(exc)
        finally:
            self._setup_finished.set()

    def finish(self, *, target_succeeded: bool = False) -> dict[str, Any]:
        # Allow post-call sampling without delaying or changing Adapter disposal.
        if self._ready:
            self._thread.join(0.6)
        self._stop.set()
        self._execute_started.set()
        self._target_finished.set()
        self._thread.join(2)
        with self._lock:
            self.observation.target_succeeded = target_succeeded
            report = self.observation.report()
            report["observer_ready"] = self._ready
            report["sampling_deadline_reached"] = self._deadline_reached
        report["worker_joined"] = not self._thread.is_alive()
        if not report["worker_joined"]:
            report["observer_status"] = "UNKNOWN"
            report["observation_error"] = {"type": "ObserverCleanupTimeout", "code": None}
        return report
