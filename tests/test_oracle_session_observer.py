from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.acceptance.oracle_session_observer import (
    COLUMNS,
    MAX_CANDIDATES,
    MAX_FULLTEXT_CHARS,
    SAMPLE_SQL,
    Observation,
    SessionObserver,
    text_comparison,
    text_fingerprint,
)

SEED = {"instance_id": "1", "session_id": "10", "audit_session_id": "42"}
START = datetime(2026, 11, 15, 10, 0)
IDENTITY = {"sid": 10, "serial_number": 9, "audsid": 42, "con_id": 3, "instance_id": 1,
            "service_name": "freepdb1"}
TAG = "dbatoolkit-observe-test"
STATEMENT = "SELECT sensitive_marker\r\nFROM DUAL\r\n"


class Clock:
    now = 0

    def __call__(self) -> int:
        return self.now


def row(*, candidates: list[tuple[Any, ...]] | None = None, **changes: Any) -> list[tuple[Any, ...]]:
    data = {**IDENTITY, "sql_id": "sql_target123", "sql_exec_id": 7, "sql_exec_start": START,
            "sql_child_number": 2, "client_identifier": TAG,
            "status": "ACTIVE", "event": "db file sequential read", "wait_class": "User I/O",
            "state": "WAITING", "last_call_et": 2, **changes}
    if candidates is None:
        candidates = [(data["sql_id"], 2, 3, 100, STATEMENT.strip())]
    return [tuple(data.get(name) for name in COLUMNS) + candidate + ((None,) if len(candidate) == 5 else ())
            for candidate in candidates]


def prepare(obs: Observation) -> None:
    obs.sample(row(sql_id=None, sql_exec_id=None, sql_exec_start=None, status="INACTIVE"), prepare=True)


def test_bind_identity_active_inactive_and_absent() -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    prepare(obs)
    clock.now = 5
    obs.mark("execute_started")
    clock.now = 10
    obs.sample(row())
    clock.now = 15
    obs.mark("execute_returned")
    clock.now = 20
    obs.sample(row(status="INACTIVE", sql_id=None, sql_exec_id=None, sql_exec_start=None))
    clock.now = 30
    obs.sample([])
    report = obs.report()
    assert report["target_identity"] == IDENTITY
    assert report["execution_identity"] == {
        "sql_id": "sql_target123", "sql_exec_id": 7, "sql_exec_start": START.isoformat(),
    }
    assert [x["state"] for x in report["state_transitions"]] == [
        "PREPARED", "ACTIVE_VERIFIED", "TARGET_INACTIVE", "SESSION_ABSENT",
    ]
    assert report["samples"][1]["wait_class"] == "User I/O"
    assert report["first_active_observed"] == report["last_active_observed"] == 10
    assert report["session_disappeared"] is True
    assert report["query_stop_confirmed"] is False


@pytest.mark.parametrize(("change", "reason"), [
    ({"serial_number": 11}, "SESSION_REBOUND"),
    ({"audsid": 43}, "SESSION_REBOUND"),
    ({"con_id": 4}, "SESSION_REBOUND"),
    ({"client_identifier": "different"}, "CLIENT_IDENTIFIER_MISMATCH"),
    ({"sql_exec_id": 8}, "EXECUTION_CHANGED"),
    ({"sql_exec_start": None}, "MISSING_EXECUTION_KEY"),
    ({"sql_id": None}, "MISSING_EXECUTION_KEY"),
])
def test_state_changes_do_not_reassign_execution(change: dict[str, Any], reason: str) -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    prepare(obs)
    clock.now = 1
    obs.mark("execute_started")
    clock.now = 2
    obs.sample(row())
    clock.now = 3
    obs.mark("execute_returned")
    clock.now = 4
    obs.sample(row(**change))
    assert obs.status == "UNKNOWN"
    assert obs.samples[-1]["reason"] == reason
    assert obs.report()["execution_identity"] is None
    assert obs.report()["last_verified_execution_identity"]["sql_exec_id"] == 7
    if reason in {"SESSION_REBOUND", "EXECUTION_CHANGED", "CLIENT_IDENTIFIER_MISMATCH"}:
        obs.sample(row())
        assert obs.status == "UNKNOWN"
    assert obs.report()["query_stop_confirmed"] is False


def test_preexisting_or_unmatched_cursor_does_not_pin_execution() -> None:
    obs = Observation(SEED, TAG, STATEMENT)
    obs.sample(row(), prepare=True)
    obs.sample(row())
    assert obs.status == "NOT_OBSERVED"
    obs.sample(row(sql_exec_id=8, candidates=[("sql_target123", 2, 3, 100, "OTHER SQL")]))
    assert obs.report()["execution_identity"] is None
    obs.sample(row(sql_exec_id=8))
    assert obs.status == "TARGET_ACTIVE"
    assert obs.report()["query_stop_confirmed"] is False


@pytest.mark.parametrize(("start_at", "end_at", "expected"), [
    (9, 15, False), (10, 15, False), (11, 15, True), (11, 20, False), (11, 21, False),
])
def test_active_requires_the_entire_sample_inside_execute(start_at: int, end_at: int, expected: bool) -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    prepare(obs)
    actions = [(10, lambda: obs.mark("execute_started")), (20, lambda: obs.mark("execute_returned"))]
    stamps: list[dict[str, int]] = []
    actions.extend([(start_at, lambda: stamps.append(obs.event())),
                    (end_at, lambda: obs.sample(row(), start=stamps[0]))])
    for instant, action in sorted(actions, key=lambda item: item[0]):
        clock.now = instant
        action()
    report = obs.report()
    assert report["active_verified"] is expected
    assert report["active_after_driver_error_observed"] == "UNKNOWN"
    assert report["query_stop_confirmed"] is False


@pytest.mark.parametrize(("variant", "verified"), [
    ("exact", True), ("different_child", False), ("missing_child", False),
    ("unchanged", False), ("other_sql", False), ("other_sql_during_fetch", False),
    ("rebound", False), ("failed_target", False), ("ambiguous_prev", False),
])
def test_completed_before_sample_requires_unique_fresh_previous_execution(variant: str, verified: bool) -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    sql_id = "sql_target123"
    previous_id = sql_id if variant == "unchanged" else "baseline"
    obs.sample(row(status="INACTIVE", sql_id=None, prev_sql_id=previous_id, prev_child_number=2,
                   prev_exec_id=7, prev_exec_start=START), prepare=True)
    for now, phase in ((10, "execute_started"), (20, "execute_returned")):
        clock.now = now
        obs.mark(phase)
    if variant == "other_sql_during_fetch":
        clock.now = 25
        obs.sample(row(sql_id="different"))
    clock.now = 30
    obs.mark("target_call_finished")
    obs.target_succeeded = variant != "failed_target"
    clock.now = 40
    candidates = [(sql_id, 2, 3, 100, STATEMENT.strip())]
    if variant == "ambiguous_prev":
        candidates *= 2
    rows = row(status="ACTIVE" if variant == "other_sql" else "INACTIVE",
                   sql_id="other" if variant == "other_sql" else None,
                   serial_number=99 if variant == "rebound" else 9,
                   prev_sql_id=sql_id, prev_exec_id=7, prev_exec_start=START,
                   prev_child_number={"different_child": 1, "missing_child": None}.get(variant, 2),
                   candidates=candidates)
    obs.sample(rows)
    clock.now = 50
    obs.sample(rows)
    report = obs.report()
    assert report["completed_before_sample"] is verified
    assert (report["samples"][-1]["result"] == "COMPLETED_BEFORE_SAMPLE") is verified
    assert report["active_verified"] is False
    assert report["first_active_observed"] is None
    assert report["query_stop_confirmed"] is False


def test_clock_sequence_orders_equal_ticks_and_reports_clock_metadata() -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    obs.mark("observer_thread_started")
    prepare(obs)
    obs.mark("observer_ready")
    report = obs.report()
    start, ready = report["phase_markers"]
    sample = report["samples"][0]
    assert start["perf_counter_ns"] == sample["perf_counter_ns"] == ready["perf_counter_ns"]
    assert start["event_sequence"] < sample["sample_started"]["event_sequence"] < sample["event_sequence"]
    assert sample["event_sequence"] < ready["event_sequence"]
    assert report["clock_source"] == "perf_counter_ns"
    assert report["clock_info"]["perf_counter"]["measured_min_positive_delta_ns"] > 0
    assert report["clock_info"]["monotonic"]["resolution_ns"] > 0


def test_fingerprints_redact_non_whitespace_and_keep_exact_byte_differences() -> None:
    tail = text_fingerprint(STATEMENT, STATEMENT.strip())
    interior = text_fingerprint(STATEMENT.strip().replace("\r\n", "\n"), STATEMENT.strip())
    assert tail["first_difference_byte_offset"] == len(STATEMENT.strip().encode())
    assert r"\r\n" in tail["difference_context"]["actual"]
    assert interior["first_difference_byte_offset"] == STATEMENT.index("\r")
    assert "sensitive_marker" not in json.dumps([tail, interior])


def test_truncated_sql_text_requires_complete_matching_clob() -> None:
    prefix = "A" * 1000
    expected = prefix + "tail"
    assert text_comparison(prefix, None, prefix)["sql_text_matches"] is False
    assert text_comparison(prefix, None, expected)["sql_text_matches"] is False
    assert text_comparison(prefix, prefix + "else", expected)["sql_text_matches"] is False
    complete = text_comparison(prefix, expected, expected)
    assert complete["sql_text_matches"] is True
    assert complete["sql_fulltext_fingerprint"]["utf8_bytes"] == len(expected)


def test_clob_capture_is_bounded_and_large_values_cannot_match() -> None:
    prefix = "X" * 1000
    calls = []

    def read(offset: int, amount: int) -> str:
        calls.append((offset, amount))
        return "X" * amount

    @contextmanager
    def unused_connection() -> Iterator[Any]:
        yield None

    lob = SimpleNamespace(size=lambda: MAX_FULLTEXT_CHARS + 1, read=read)
    observer = SessionObserver(unused_connection, SEED, prefix, TAG)
    candidate = ("sql_target123", 2, 3, 100, prefix, lob)
    observed = observer._read_fulltext(row(candidates=[candidate]))
    assert not calls
    assert observed[0][-1] == {"status": "OVER_LIMIT"}
    observer.observation.sample(observed)
    assert observer.observation.samples[0]["cursor_candidates"][0]["sql_text_matches"] is False


def test_child_container_and_exact_text_are_checked_independently() -> None:
    obs = Observation(SEED, TAG, STATEMENT)
    prepare(obs)
    sql_id = "sql_target123"
    candidates = [
        (sql_id, 1, 3, 101, STATEMENT.strip()),
        (sql_id, 2, 0, 102, STATEMENT.strip()),
        (sql_id, 2, 3, 103, STATEMENT.strip().replace("\r\n", "\n")),
    ]
    obs.sample(row(candidates=candidates))
    sample = obs.samples[-1]
    assert sample["reason"] == "SQL_TEXT_MISMATCH"
    assert [(v["child_number_matches"], v["con_id_matches"], v["sql_text_matches"])
            for v in sample["cursor_candidates"]] == [
        (False, True, True), (True, False, True), (True, True, False),
    ]
    assert obs.report()["execution_identity"] is None

    exact = (sql_id, 2, 3, 104, STATEMENT.strip())
    obs.sample(row(candidates=[*candidates, exact]))
    assert obs.status == "UNKNOWN"
    assert obs.samples[-1]["reason"] == "AMBIGUOUS_CHILD_CURSOR"
    obs.sample(row(candidates=[*candidates[:2], exact]))
    assert obs.status == "TARGET_ACTIVE"
    assert obs.samples[-1]["exact_cursor_match"] == 1
    assert obs.samples[-1]["sql_child_number"] == 2
    assert "sensitive_marker" not in json.dumps(obs.report())


def test_duplicate_tag_cannot_prepare_a_session() -> None:
    obs = Observation(SEED, TAG, STATEMENT)
    obs.sample([*row(), *row(sid=11)], prepare=True)
    assert obs.status == "UNKNOWN"
    assert obs.samples[-1]["reason"] == "AMBIGUOUS_SESSION_SAMPLE"
    assert obs.identity is None


def test_candidate_overflow_refuses_partial_match() -> None:
    obs = Observation(SEED, TAG, STATEMENT)
    prepare(obs)
    obs.sample(row() * (MAX_CANDIDATES + 1))
    assert obs.status == "UNKNOWN"
    assert obs.samples[-1]["reason"] == "CANDIDATE_LIMIT_EXCEEDED"
    assert obs.report()["execution_identity"] is None


class FakeCursor:
    def __init__(self, code: str | None, fail_at: int, clock: Clock) -> None:
        self.code = code
        self.fail_at = fail_at
        self.clock = clock
        self.bound_calls = 0
        self.sampled = threading.Event()

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, **binds: Any) -> None:
        if sql != SAMPLE_SQL:
            assert sql.startswith("SELECT 1 FROM SYS.")
            if "V_$SESSION" in sql and self.code == "ORA-00942":
                raise RuntimeError(SimpleNamespace(full_code=self.code, message="secret"))
            return
        assert binds == {"client_identifier": TAG, "include_pinned": int(bool(self.bound_calls)), "target_sid": 10}
        self.bound_calls += 1
        if self.bound_calls == self.fail_at:
            raise RuntimeError(SimpleNamespace(full_code=self.code, message="secret"))

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        assert size == MAX_CANDIDATES + 1
        if self.bound_calls == 1:
            return row(sql_id=None, sql_exec_id=None, sql_exec_start=None)
        self.clock.now += 8_000_000_000
        self.sampled.set()
        return row()


def test_worker_first_poll_can_start_after_short_target_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.acceptance.oracle_session_observer.FIRST_POLL_GRACE_SECONDS", 1)
    clock = Clock()
    cursor = FakeCursor(None, 0, clock)

    @contextmanager
    def connect() -> Iterator[Any]:
        yield SimpleNamespace(cursor=lambda: cursor)

    observer = SessionObserver(connect, SEED, STATEMENT, TAG, clock=clock)
    try:
        assert observer.prepare()
        assert cursor.bound_calls == 1
        for clock.now, phase in ((10, "execute_started"), (20, "execute_returned"), (30, "target_call_finished")):
            observer.mark(phase)
        assert cursor.sampled.wait(2)
    finally:
        report = observer.finish(target_succeeded=True)
    markers = {entry["phase"]: entry["event_sequence"] for entry in report["phase_markers"]}
    assert markers["observer_ready"] < markers["execute_started"]
    assert markers["target_call_finished"] < markers["first_poll_target_finished"]
    assert markers["first_poll_target_finished"] < report["last_sample_window"]["start"]["event_sequence"]


@pytest.mark.parametrize(("code", "fail_at", "state"), [
    ("ORA-00942", 1, "PERMISSION_DENIED"),
    ("ORA-00904", 1, "UNSUPPORTED"),
    ("DPY-4011", 2, "UNKNOWN"),
    (None, 0, "UNKNOWN"),
])
def test_worker_deadline_exit_and_error_redaction(code: str | None, fail_at: int, state: str) -> None:
    clock = Clock()
    cursor = FakeCursor(code, fail_at, clock)
    conn = SimpleNamespace(cursor=lambda: cursor)
    closed = threading.Event()

    @contextmanager
    def connect() -> Iterator[Any]:
        try:
            yield conn
        finally:
            closed.set()

    observer = SessionObserver(connect, SEED, STATEMENT, TAG, clock=clock)
    assert observer.prepare() is (fail_at != 1)
    if fail_at != 1:
        assert cursor.bound_calls == 1
        observer.mark("execute_started")
    assert closed.wait(2)
    report = observer.finish()
    assert conn.call_timeout == 500
    assert report["worker_joined"] is True
    assert report["observer_ready"] is (fail_at != 1)
    if report["observer_ready"]:
        ready = next(m for m in report["phase_markers"] if m["phase"] == "observer_ready")
        assert report["first_sample_window"]["end"]["event_sequence"] < ready["event_sequence"]
        phases = [m["phase"] for m in report["phase_markers"]]
        assert phases.index("observer_ready") < phases.index("execute_started") < phases.index(
            "first_poll_grace_elapsed"
        )
    assert report["observer_status"] == state
    assert report["query_stop_confirmed"] is False
    assert report["sampling_deadline_reached"] is (code is None)
    if code:
        assert report["observation_error"]["code"] == code
    if code == "ORA-00942":
        assert report["access_probes"]["SYS.V_$SESSION"]["status"] == "UNAVAILABLE"
        assert report["access_probes"]["SYS.V_$SQL"]["status"] == "READABLE"
    rendered = json.dumps(report)
    assert "secret" not in rendered
    assert "sensitive_marker" not in rendered


def test_fulltext_is_authoritative_when_sql_text_has_line_ending_difference() -> None:
    expected = STATEMENT.strip()
    result = text_comparison(expected.replace("\r\n", "\r "), expected, expected)
    assert result["sql_text_matches"] is True
    assert result["sql_text_match_status"] == "EXACT_MATCH"
    assert result["sql_text_fingerprint"]["sha256"] != result["sql_fulltext_fingerprint"]["sha256"]


@pytest.mark.parametrize(("text", "fulltext", "expected_status"), [
    (STATEMENT.strip(), None, "EXACT_MATCH"),
    (STATEMENT.strip(), STATEMENT.strip() + " /* different */", "MISMATCH"),
    ("Q" * 1000, None, "UNVERIFIED"),
    ("Q" * 1000, {"status": "OVER_LIMIT"}, "UNVERIFIED"),
    (STATEMENT.strip(), {"status": "UNAVAILABLE"}, "UNVERIFIED"),
])
def test_fulltext_comparison_distinguishes_mismatch_from_missing_evidence(
    text: str, fulltext: Any, expected_status: str,
) -> None:
    result = text_comparison(text, fulltext, STATEMENT.strip())
    assert result["sql_text_match_status"] == expected_status
    assert result["sql_text_matches"] is (expected_status == "EXACT_MATCH")


@pytest.mark.parametrize(("mode", "verdict"), [
    ("exact", "EXACT_MATCH"), ("different", "MISMATCH"),
    ("short_read", "UNVERIFIED"), ("read_error", "UNVERIFIED"),
])
def test_scoped_child_reads_fulltext_even_when_sql_text_matches(mode: str, verdict: str) -> None:
    expected = STATEMENT.strip()
    fulltext = expected + " additions" if mode == "different" else expected
    calls = []

    def read(offset: int, amount: int) -> str:
        calls.append((offset, amount))
        if mode == "read_error":
            raise RuntimeError("sensitive data")
        return fulltext[:-1] if mode == "short_read" else fulltext

    @contextmanager
    def unused_connection() -> Iterator[Any]:
        yield None

    lob = SimpleNamespace(size=lambda: len(fulltext), read=read)
    observer = SessionObserver(unused_connection, SEED, STATEMENT, TAG)
    prepare(observer.observation)
    rows = row(candidates=[("sql_target123", 2, 3, 100, expected, lob)])
    observer.observation.sample(observer._read_fulltext(rows))
    sample = observer.observation.samples[-1]
    assert calls == [(1, len(fulltext))]
    assert sample["cursor_candidates"][0]["sql_text_match_status"] == verdict
    if verdict == "UNVERIFIED":
        assert sample["result"] == "UNKNOWN"
        assert sample["reason"] == "SQL_TEXT_UNVERIFIED"
    assert "sensitive data" not in json.dumps(sample)


@pytest.mark.parametrize(("window_start", "window_end", "crosses"), [
    (3, 8, False), (11, 19, False), (21, 29, False), (35, 37, False),
    (9, 11, True), (19, 21, True), (29, 31, True),
    (10, 11, True), (11, 20, True), (20, 21, True), (29, 30, True), (30, 31, True),
])
def test_crossing_samples_cannot_prove_activity_or_veto_independent_completion(
    window_start: int, window_end: int, crosses: bool,
) -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    obs.sample(row(sql_id=None, status="INACTIVE",
                   prev_sql_id="baseline", prev_child_number=2, prev_exec_id=1, prev_exec_start=START),
               prepare=True)
    stamps: list[dict[str, int]] = []

    def sample() -> None:
        if window_start > 30:
            obs.sample(row(sql_id=None, status="INACTIVE", prev_sql_id="sql_target123",
                           prev_child_number=2, prev_exec_id=7, prev_exec_start=START,
                           candidates=[("sql_target123", 2, 3, 100, STATEMENT.strip())]), start=stamps[0])
        else:
            obs.sample(row(), start=stamps[0])

    actions = [(10, lambda: obs.mark("execute_started")), (20, lambda: obs.mark("execute_returned")),
               (30, lambda: obs.mark("target_call_finished")),
               (window_start, lambda: stamps.append(obs.event())), (window_end, sample)]
    for instant, action in sorted(actions, key=lambda item: item[0]):
        clock.now = instant
        action()
    obs.target_succeeded = True
    for clock.now in (40, 50):
        obs.sample(row(sql_id=None, status="INACTIVE", prev_sql_id="sql_target123",
                       prev_child_number=2, prev_exec_id=7, prev_exec_start=START,
                       candidates=[("sql_target123", 2, 3, 100, STATEMENT.strip())]))
    report = obs.report()
    assert report["completed_before_sample"] is True
    assert report["active_verified"] is ((window_start, window_end) == (11, 19))
    assert report["query_stop_confirmed"] is False
    if crosses:
        sample = report["samples"][1]
        assert sample["result"] == "UNKNOWN"
        assert sample["attribution_excluded_reason"] == "AMBIGUOUS_EXECUTION_WINDOW"


@pytest.mark.parametrize(("variant", "reason"), [
    ("stable", None),
    ("other_sql", "OTHER_SQL_IN_TARGET_WINDOW"),
    ("previous_other_sql", "OTHER_SQL_IN_TARGET_WINDOW"),
    ("ambiguous_child", "AMBIGUOUS_EXECUTION_WINDOW"),
    ("conflicting_cursor_text", "TARGET_CURSOR_UNVERIFIED"),
    ("changed_key", "PREV_EXECUTION_CHANGED"),
    ("rebound", "TARGET_OR_IDENTITY_UNVERIFIED"),
    ("one_post_sample", "INSUFFICIENT_STABLE_POST_COMPLETION_SAMPLES"),
])
def test_short_execute_replays_crossing_sample_and_three_fulltext_matches(variant: str, reason: str | None) -> None:
    clock = Clock()
    obs = Observation(SEED, TAG, STATEMENT, clock)
    inactive = dict(status="INACTIVE", sql_id=None, sql_exec_id=None, sql_exec_start=None, sql_child_number=None)
    baseline = dict(prev_sql_id="baseline", prev_child_number=0, prev_exec_id=27, prev_exec_start=START)
    clock.now = 197_305_900
    obs.sample(row(**inactive, **baseline), prepare=True)
    clock.now = 197_364_400
    obs.mark("observer_ready")
    clock.now = 197_376_100
    crossing_started = obs.event()
    for offset, marker in ((0, "execute_started"), (904_800, "execute_returned"),
                           (2_366_700, "target_call_finished")):
        clock.now = 200_000_000 + offset
        obs.mark(marker)
    clock.now = 225_178_100
    crossing = {**inactive, **baseline}
    candidates = None
    if variant == "other_sql":
        crossing.update(sql_id="different", status="ACTIVE")
    elif variant == "previous_other_sql":
        crossing.update(prev_sql_id="different", prev_exec_id=33)
    elif variant == "rebound":
        crossing.update(serial_number=99)
    elif variant == "ambiguous_child":
        candidates = [("baseline", 0, 3, 100, STATEMENT.strip())] * 2
    elif variant == "conflicting_cursor_text":
        crossing.update(sql_id="sql_target123", sql_child_number=0)
        candidates = [("sql_target123", 0, 3, 100, "SELECT OTHER FROM DUAL")]
    obs.sample(row(candidates=candidates, **crossing), start=crossing_started)

    windows = [(389_310_800, 420_492_600), (574_164_000, 602_003_400), (760_555_100, 790_700_800)]
    if variant == "one_post_sample":
        windows = windows[:1]
    for index, (start, end) in enumerate(windows):
        clock.now = start
        sample_started = obs.event()
        clock.now = end
        obs.sample(row(**inactive, prev_sql_id="sql_target123", prev_child_number=0,
                       prev_exec_id=20 if variant == "changed_key" and index == 1 else 19,
                       prev_exec_start=START,
                       candidates=[("sql_target123", 0, 3, 100, "SELECT", STATEMENT.strip())]),
                   start=sample_started)
    obs.target_succeeded = True
    report = obs.report()
    assert report["samples"][1]["result"] == "UNKNOWN"
    assert report["completed_before_sample"] is (reason is None)
    assert report["completion_unverified_reason"] == reason
    assert report["active_verified"] is False
    assert report["query_stop_confirmed"] is False
    if reason is None:
        assert [s["result"] for s in report["samples"][2:]] == ["COMPLETED_BEFORE_SAMPLE"] * 3
