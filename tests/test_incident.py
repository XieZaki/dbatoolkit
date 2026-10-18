from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pytest

from dba.core import DatabaseInstance, DatabaseType, Finding, ReferenceCommand, Severity
from dba.incident import IncidentSummary, build_incident_summary, manual_action_decisions
from dba.presentation import render_emergency_triage, render_incident_summary, render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.slow import SlowReport
from dba.slow_reporter import render_markdown

INSTANCE = DatabaseInstance("sql", "SQL", DatabaseType.SQLSERVER, "db", 1433, "monitor")
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def report(*, block: bool = False, incomplete: bool = False,
           long_running: bool = False) -> SlowReport:
    details: dict[str, Any] = {
        "incomplete": incomplete,
        "version": "16.0",
        "compatibility": "test",
        "quality": [],
        "concurrency": {"active_requests": 2 if block or long_running else 0,
                        "blocked_requests": 1 if block else 0},
        "blocking_graph": {"anomalies": [], "roots": [11] if block else [],
                            "children": {"11": [12]} if block else {}, "unresolved": []},
        "top_requests": ([{"session_id": 12, "elapsed_ms": 120000,
                           "blocking_session_id": 11, "wait_time_ms": 45000,
                           "wait_type": "LCK_M_X"}] if block else
                         [{"session_id": 12, "elapsed_ms": 120000}] if long_running else []),
        "all_sessions": ([{"session_id": 11, "status": "sleeping"}] if block else []),
        "all_requests": ([{"session_id": 12, "sql_text": "UPDATE fixture"}] if block else []),
        "long_running": ([{"session_id": 12, "elapsed_ms": 120000}] if long_running else []),
        "roots": ([{"session_id": 11, "status": "sleeping", "login_name": "app",
                    "host_name": "APP01", "client_net_address": "10.0.0.4",
                    "program_name": "MES", "blocked_session_count": 1,
                    "transaction_age_seconds": 3600,
                    "unfinished_transaction_confirmed": True}] if block else []),
        "sources": [],
        "transactions": ([{"session_id": 11, "transaction_age_seconds": 3600}] if block else []),
        "samples": [{"collector": name, "row_count": 1, "success": True, "truncated": False}
                    for name in ("active_requests", "active_sessions", "transactions", "connections")],
    }
    if incomplete:
        details["samples"][0]["truncated"] = True
    findings: tuple[Finding, ...] = ()
    if block:
        findings = (
            Finding("BLOCKING", Severity.CRITICAL, {}, "review"),
            Finding("ROOT_BLOCKER", Severity.WARNING, details["roots"][0], "review",
                    (ReferenceCommand("KILL 11;", "manual review"),), session="11"),
        )
    elif long_running:
        findings = (Finding("LONG_RUNNING_SQL", Severity.WARNING, {}, "review"),)
    return SlowReport(INSTANCE, NOW, "slow", findings, details)


def test_blocking_incident_summary_has_impact_timing_source_and_safe_action() -> None:
    value = build_incident_summary(report(block=True), "reports/slow.md")
    assert value.data_completeness == "COMPLETE"
    assert value.status == "DEGRADED" and value.severity == "HIGH"
    assert value.primary_problem == "BLOCKING" and value.observed_now == "ACTIVE"
    assert value.impact["blocked_requests"] == 1
    assert value.timing == {"oldest_transaction": "01:00:00", "longest_request": "00:02:00",
                            "longest_block": "00:00:45"}
    assert value.wait == {"category": "LOCK", "event": "LCK_M_X"}
    assert value.root_cause_candidate and "open transaction" in value.root_cause_candidate["reason"]
    assert value.source == {"login": "app", "host": "APP01", "client_ip": "10.0.0.4",
                            "program": "MES"}
    assert len(value.recovery_actions) == 1
    action = value.recovery_actions[0]
    assert action.action_type == "TERMINATE_SESSION"
    assert action.reference_command == "KILL 11;"
    assert action.rollback_risk == "UNKNOWN" and action.rollback_duration == "UNKNOWN"
    assert action.approval_required and not action.toolkit_executed
    evidence = render_markdown(report(block=True))
    assert "KILL 11;" in evidence and '"session_id": 11' in evidence
    assert "Root Blocker Details" in evidence and "Toolkit 未执行任何 KILL 操作" in evidence
    assert "## All Sessions" in evidence and "## All Active Requests" in evidence
    assert "UPDATE fixture" in evidence


def test_incomplete_evidence_never_surfaces_termination_command() -> None:
    value = build_incident_summary(report(block=True, incomplete=True))
    assert value.data_completeness == "PARTIAL"
    assert value.observed_now == "ACTIVE"
    assert value.missing_evidence
    assert value.recovery_actions == ()
    human = render_incident_summary(value)
    assert "Reason: evidence incomplete" in human
    assert "KILL 11" not in human
    unavailable = build_incident_summary(report(incomplete=True))
    assert unavailable.observed_now == "UNKNOWN"


def test_reduced_optional_visibility_is_partial_without_failing_core_evidence() -> None:
    sample = report()
    sample.details["data_visibility"] = "PARTIAL"
    sample.details["full_profile_evidence_status"] = "PARTIAL"
    value = build_incident_summary(sample)
    assert value.data_completeness == "PARTIAL"
    assert value.missing_evidence == ()


def test_manual_reference_survives_partial_full_profile_and_is_shared_by_presenters() -> None:
    sample = report(block=True)
    sample.details.update({"emergency_evidence_complete": True,
                           "full_profile_evidence_status": "PARTIAL",
                           "data_visibility": "PARTIAL"})
    decisions = manual_action_decisions(sample)
    sample.details["manual_action_decisions"] = decisions
    summary = build_incident_summary(sample)
    cli = render_emergency_triage(sample, capture_started_at=NOW.isoformat())
    html = HtmlReportRenderer().render(build_report_model(sample, summary))
    payload = render_json({"report": asdict(sample), "incident_summary": asdict(summary),
                           "manual_action_decisions": decisions})
    assert summary.data_completeness == "PARTIAL"
    assert summary.recovery_actions[0].reference_command == "KILL 11;"
    assert decisions[0]["status"] == "AVAILABLE"
    assert "KILL 11;" in cli
    assert "KILL 11;" in html and "<details" in html
    assert "KILL 11;" in payload and '"status": "AVAILABLE"' in payload
    assert "Toolkit 未执行" in cli


def test_long_running_without_blocking_does_not_recommend_termination() -> None:
    value = build_incident_summary(report(long_running=True))
    assert value.primary_problem == "LONG_RUNNING_SQL"
    assert value.recovery_actions == ()
    text = render_incident_summary(value)
    assert "No session termination recommended" in text
    assert "KILL" not in text


def test_healthy_and_verbose_presentations_share_summary_domain() -> None:
    value = build_incident_summary(report(), "reports/healthy.md")
    assert value.status == "HEALTHY" and value.primary_problem == "UNKNOWN"
    assert value.observed_now == "NOT_DETECTED"
    normal = render_incident_summary(value)
    verbose = render_incident_summary(value, verbose=True)
    assert "Top SQL" not in normal and "Session Source" not in normal
    assert "Collector Summary" in verbose
    assert "Detailed Report" in normal
    payload = render_json(value)
    assert '"status": "HEALTHY"' in payload
    assert asdict(value)["report_path"] == "reports/healthy.md"


def test_plan_diagnosis_summary_keeps_plan_details_out_of_terminal() -> None:
    plan = SlowReport(INSTANCE, NOW, "plan", (), {
        "plan_available": True, "metrics_scope": "current_request", "session_id": 11,
        "request": {"elapsed_ms": 30000, "wait_type": "LCK_M_X", "sql_text": "SELECT private"},
        "top_operators": [{"physical_op": "Index Scan"}],
    })
    value = build_incident_summary(plan, "reports/plan/plan_report.md")
    assert value.observed_now == "ACTIVE" and value.primary_problem == "UNKNOWN"
    assert value.recovery_actions == ()
    text = render_incident_summary(value)
    assert "00:00:30" in text and "SELECT private" not in text and "Index Scan" not in text


def test_disk_growth_findings_map_to_summary_problem_without_kill_action() -> None:
    disk = SlowReport(INSTANCE, NOW, "disk-growth",
                      (Finding("SQLSERVER_LOG_HIGH_USAGE", Severity.WARNING,
                               {"log_used_percent": 90}, "review"),),
                      {"incomplete": False, "samples": [], "log": {"log_used_percent": 90}})
    value = build_incident_summary(disk, "reports/disk.md")
    assert value.primary_problem == "LOG_PRESSURE"
    assert value.status == "DEGRADED" and value.observed_now == "ACTIVE"
    assert not value.recovery_actions


def test_recovery_action_is_text_only_and_json_is_redacted() -> None:
    value = build_incident_summary(report(block=True))
    action = value.recovery_actions[0]
    assert action.destructive and action.approval_required
    assert not hasattr(action, "execute")
    data = render_json(IncidentSummary(**{**asdict(value), "source": {"login": "password=secret"}}))
    assert "secret" not in data


@pytest.mark.parametrize(("db_type", "root", "finding_session", "command"), [
    (DatabaseType.SQLSERVER,
     {"session_id": 11, "blocked_session_count": 2, "unfinished_transaction_confirmed": True},
     "11", "KILL 11;"),
    (DatabaseType.POSTGRESQL,
     {"pid": 11, "blocked_count": 2, "transaction_age_seconds": 90},
     "11", "SELECT pg_cancel_backend(11);"),
    (DatabaseType.MYSQL,
     {"processlist_id": 11, "blocked_count": 2, "trx_id": "abc"},
     "11", "KILL QUERY 11;"),
    (DatabaseType.ORACLE,
     {"sid": 11, "serial_number": 99, "inst_id": 1, "blocked_count": 2,
      "transaction_age_seconds": 90},
     "1:11", "ALTER SYSTEM KILL SESSION '11,99' IMMEDIATE;"),
])
def test_database_specific_reference_commands_flow_through_shared_domain(
    db_type: DatabaseType, root: dict[str, Any], finding_session: str, command: str,
) -> None:
    instance = DatabaseInstance("db", "DB", db_type, "host", 1, "monitor")
    evidence = {"concurrency": {"active_requests": 3, "blocked_requests": 2},
                "blocking_graph": {"anomalies": []}, "incomplete": False,
                "roots": [root], "top_queries": [], "long_running": [],
                "samples": [{"collector": name, "success": True, "truncated": False}
                            for name in ("active_sessions", "active_requests", "transactions", "blocking")]}
    finding = Finding("ROOT_BLOCKER", Severity.WARNING, root, "review",
                      (ReferenceCommand(command, "manual only"),), session=finding_session)
    value = SlowReport(instance, NOW, "slow", (finding,), evidence)
    summary = build_incident_summary(value)
    assert len(summary.recovery_actions) == 1
    assert summary.recovery_actions[0].reference_command == command
    assert summary.recovery_actions[0].toolkit_executed is False
