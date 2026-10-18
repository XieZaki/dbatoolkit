from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, Finding, Severity
from dba.incident import build_priority_candidates
from dba.presentation import render_emergency_triage
from dba.settings import SQLServerSettings, apply_diagnostic_threshold
from dba.slow import SlowReport, collect_slow_phase

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
INSTANCE = DatabaseInstance("db1", "DB1", DatabaseType.SQLSERVER, "localhost", 1433, "monitor")


def _slow_report() -> SlowReport:
    return SlowReport(INSTANCE, NOW, "slow", (
        Finding("LONG_RUNNING_SQL", Severity.WARNING, {"threshold_seconds": 10}, "review"),
    ), {
        "version": "16.0", "emergency_evidence_status": "COMPLETE",
        "samples": [{"finished_at": NOW.isoformat()}],
        "all_sessions": [{"session_id": 10, "status": "sleeping", "login_name": "app",
                          "login_time": "2026-10-18T10:00:00Z", "host_name": "worker",
                          "program_name": "orders"}],
        "roots": [{"session_id": 10, "blocked_count": 7, "transaction_age_seconds": 60,
                    "transaction_id": "tx10", "login_name": "app", "host_name": "worker",
                    "program_name": "orders", "status": "sleeping"}],
        "idle_transactions": [{"session_id": 20, "transaction_age_seconds": 90,
                               "transaction_id": "tx20", "login_name": "app"}],
        "all_requests": [
            {"session_id": 30, "blocking_session_id": 10, "elapsed_ms": 50000},
            {"session_id": 40, "elapsed_ms": 90000, "sql_handle": "sql40"},
        ],
    })


def test_priority_candidates_have_deterministic_categories_and_identity() -> None:
    candidates = build_priority_candidates(_slow_report())
    assert [item["session_id"] for item in candidates] == [10, 20, 40]
    assert [item["category"] for item in candidates] == [
        "ROOT_BLOCKER", "IDLE_OPEN_TRANSACTION", "LONG_RUNNING_SQL"
    ]
    assert candidates[0]["blocked_session_count"] == 7
    assert candidates[0]["transaction_id"] == "tx10"
    assert candidates[0]["evidence_timestamp"] == NOW.isoformat()
    assert candidates[2]["identity_recheck"] == "REQUIRED_BEFORE_MANUAL_ACTION"
    assert candidates[2]["sql_id_type"] == "sql_handle"


def test_candidates_are_bounded_to_five_and_root_order_uses_impact() -> None:
    report = _slow_report()
    report.details["roots"] = [
        {"session_id": 2, "blocked_count": 2}, {"session_id": 1, "blocked_count": 8},
        {"session_id": 3, "blocked_count": 4}, {"session_id": 4, "blocked_count": 1},
        {"session_id": 5, "blocked_count": 3}, {"session_id": 6, "blocked_count": 5},
    ]
    candidates = build_priority_candidates(report)
    assert len(candidates) == 5
    assert [item["session_id"] for item in candidates] == [1, 6, 3, 5, 2]


def test_distinct_instance_session_keys_are_not_merged_and_reuse_is_flagged() -> None:
    report = _slow_report()
    report.details["roots"] = [
        {"session_id": 10, "inst_id": 1, "blocked_count": 2},
        {"session_id": 10, "inst_id": 2, "blocked_count": 3},
    ]
    candidates = build_priority_candidates(report)
    assert [item["session_key"] for item in candidates[:2]] == ["2:10", "1:10"]
    assert all(item["identity_quality"] == "IDENTITY_INCOMPLETE" for item in candidates[:2])
    assert all("可能已复用" in item["identity_reuse_note"] for item in candidates[:2])


def test_incomplete_emergency_summary_does_not_claim_healthy() -> None:
    report = _slow_report()
    report.details["emergency_evidence_complete"] = False
    text = render_emergency_triage(report, capture_started_at=NOW.isoformat())
    assert "当前结论未知" in text
    assert "不能据此判断无阻塞或数据库健康" in text


def test_console_candidate_categories_are_chinese() -> None:
    text = render_emergency_triage(_slow_report(), build_priority_candidates(_slow_report()),
                                  capture_started_at=NOW.isoformat())
    assert "根阻塞" in text
    assert "被阻塞会话" not in text
    assert "ROOT_BLOCKER" not in text


def test_emergency_summary_reports_supplemental_stage_accurately() -> None:
    report = _slow_report()
    report.details.update({"supplemental_pending": True, "supplemental_not_collected": ["connections"]})
    pending = render_emergency_triage(report, capture_started_at=NOW.isoformat())
    assert "补充证据：待采集" in pending and "正在采集补充证据" in pending
    report.details["supplemental_pending"] = False
    complete = render_emergency_triage(report, capture_started_at=NOW.isoformat())
    assert "补充证据：无额外查询" in complete and "无额外补充查询" in complete


@pytest.mark.parametrize("value", [True, False, 0, 86401, "5", 1.5])
def test_shared_slow_threshold_overrides_runtime_and_rejects_invalid_types(
    tmp_path: Path, value: object,
) -> None:
    settings_file = tmp_path / "runtime.yaml"
    settings_file.write_text("sqlserver: {}\ndiagnostics:\n  slow:\n    threshold_seconds: 17\n",
                             encoding="utf-8")
    settings = apply_diagnostic_threshold(SQLServerSettings(), settings_file)
    assert isinstance(settings, SQLServerSettings)
    assert settings.long_running_seconds == 17
    assert SQLServerSettings().long_running_seconds == 10
    settings_file.write_text(f"sqlserver: {{}}\ndiagnostics:\n  slow:\n    threshold_seconds: {value!r}\n",
                             encoding="utf-8")
    with pytest.raises(ValueError, match="integer in 1..86400"):
        apply_diagnostic_threshold(SQLServerSettings(), settings_file)


def test_staged_collector_reuses_existing_result_and_only_runs_requested_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import slow

    calls: list[str] = []

    class FakeCollector:
        def __init__(self, adapter: Any) -> None:
            self.adapter = adapter

        def collect(self, instance: DatabaseInstance) -> CollectorResult:
            calls.append("active_sessions")
            return CollectorResult(instance.id, "active_sessions", NOW, NOW, 1.0, (), 0, False, True)

    monkeypatch.setitem(slow._STAGED_COLLECTORS, "active_sessions", FakeCollector)
    adapter = type("Adapter", (), {"instance": INSTANCE})()
    prior = CollectorResult(INSTANCE.id, "active_sessions", NOW, NOW, 1.0, (), 0, False, True)
    results = collect_slow_phase(adapter, ("active_sessions",), (prior,))  # type: ignore[arg-type]
    assert results == (prior,)
    assert calls == []
