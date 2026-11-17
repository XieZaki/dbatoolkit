from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scripts.acceptance.verify_oracle_sys_timeout import (
    CALL_MS,
    PingClock,
    PingRelayEvents,
    attempt,
    execution_evidence,
    ping_verdict,
    timed_ping,
    workload,
)

START = 1_000_000
THRESHOLD = START + CALL_MS * 1_000_000
RETURN = THRESHOLD + 10


def sample(at: int, state: str, **changes: Any) -> dict[str, Any]:
    key = {"sql_id": "expected", "child": 0, "sql_exec_id": 10, "sql_exec_start": "2026-11-16"}
    return {
        "started_ns": at, "ended_ns": at + 1, "identity_matches": True,
        "session": {"serial": 7, "status": state, **key, "current_candidates": 1, "current_exact": 1,
                    **{f"prev_{field.removeprefix('sql_')}": value for field, value in key.items()},
                    **changes, "prev_sql_id": changes.get("prev_sql_id", "expected")},
    }


@pytest.mark.parametrize("variant", ["verified", "absent", "one_idle", "rebound", "other_sql", "ambiguous"])
def test_stop_requires_exact_active_and_two_stable_idle_samples(variant: str) -> None:
    baseline = sample(0, "INACTIVE")
    active = sample(RETURN + 10, "ACTIVE")
    idle = sample(RETURN + 20, "INACTIVE")
    final = sample(RETURN + 30, "INACTIVE")
    if variant == "absent":
        final["session"] = None
    elif variant == "rebound":
        final["session"]["serial"] = 8
    elif variant == "other_sql":
        final["session"]["prev_sql_id"] = "different"
    elif variant == "ambiguous":
        active["session"]["current_candidates"] = 2
    samples = [baseline, active, idle, final]
    if variant == "one_idle":
        samples.pop()
    result = execution_evidence(samples, START, RETURN)
    assert result["active_after_driver_return"] is (variant != "ambiguous")
    assert result["SERVER_QUERY_STOP_CONFIRMED"] is (variant == "verified")
    assert result["stop_sample_ns"] == (RETURN + 21 if variant == "verified" else None)


def test_fetch_records_successful_execute_and_first_row_before_driver_error() -> None:
    class Cursor:
        calls = 0

        def execute(self, sql: str) -> None:
            assert sql == workload("fetch", "marker")

        def fetchone(self) -> tuple[int]:
            self.calls += 1
            if self.calls == 1:
                return (1,)
            raise RuntimeError(SimpleNamespace(full_code="DPY-4024", message="synthetic secret"))

    result = attempt(Cursor(), "fetch", workload("fetch", "marker"))  # type: ignore[arg-type]
    assert [call["phase"] for call in result["calls"]] == ["execute", "first_fetch", "next_fetch"]
    assert result["calls"][1]["value"] == (1,)
    assert result["calls"][2]["driver_error"] == "DPY-4024"
    assert "synthetic secret" not in str(result)


def test_ping_timing_records_each_event_even_when_the_clock_ticks_are_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples: list[int] = []

    def tick() -> int:
        samples.append(100)
        return 100

    monkeypatch.setattr("scripts.acceptance.verify_oracle_sys_timeout.time.perf_counter_ns", tick)
    clock = PingClock()
    relay = PingRelayEvents(clock)
    lifecycle: dict[str, Any] = {}

    def first_ping() -> None:
        relay.append({"event": "interrupt_forwarded", "ns": 60})
        relay.append({"event": "response_released", "ns": 60})
        raise RuntimeError(SimpleNamespace(full_code="DPY-4024"))

    def followup_ping() -> None:
        raise RuntimeError(SimpleNamespace(full_code="ORA-01013"))

    clock.mark(lifecycle, "connected")
    target = SimpleNamespace(call_timeout=CALL_MS, ping=first_ping, is_healthy=lambda: True)
    call = timed_ping(target, clock)
    target.ping = followup_ping
    followup = timed_ping(target, clock)
    clock.mark(lifecycle, "connection_close_started")
    assert samples == [100] * 10
    assert [
        lifecycle["connected_sequence"], call["started_sequence"], relay[0]["sequence"],
        relay[1]["sequence"], call["exception_sequence"], call["ended_sequence"],
        followup["started_sequence"], followup["exception_sequence"], followup["ended_sequence"],
        lifecycle["connection_close_started_sequence"],
    ] == list(range(1, 11))
    assert relay[0]["ns"] == call["exception_ns"] == followup["started_ns"] == 100
    assert relay[0]["relay_monotonic_ns"] == 60
    assert ping_verdict("release", call, relay, followup) == "RECOVERY_RECEIVE_COMPLETED_CONNECTION_UNVERIFIED"
    late_release = {**relay[1], "sequence": call["ended_sequence"]}
    assert ping_verdict("release", call, [relay[0], late_release], followup) == "INCONCLUSIVE"
