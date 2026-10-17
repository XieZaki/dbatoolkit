from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dba.cli import _diagnostic_report_path
from dba.core import DatabaseInstance, DatabaseType, Finding, ReferenceCommand, Severity
from dba.incident import build_incident_summary
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.slow import SlowReport


def _report() -> SlowReport:
    instance = DatabaseInstance("sqlserver2022", "SQL <Server>", DatabaseType.SQLSERVER,
                                "localhost", 1433, "monitor", environment="test")
    root = {"session_id": 57, "login_name": "app", "host_name": "host-a",
            "program_name": "<img src=x onerror=alert(1)>", "status": "sleeping",
            "blocked_count": 2, "open_transaction_count": 1,
            "transaction_age_seconds": 77}
    findings = (Finding("ROOT_BLOCKER", Severity.CRITICAL, root,
                        "Review the owning transaction.",
                        (ReferenceCommand("KILL 57;", "Manual review only."),), session="57"),)
    details = {
        "version": "16.0.4305.5", "incomplete": False,
        "concurrency": {"active_requests": 3, "blocked_requests": 2},
        "roots": [root],
        "blocking_graph": {"roots": [57], "children": {57: [59], 59: [67]}, "anomalies": []},
        "all_sessions": [{"session_id": sid, "login_name": "app", "host_name": "host-a",
                          "program_name": "simulator", "database_name": "AppDb", "status": "sleeping"}
                         for sid in (57, 59, 67, 68)],
        "all_requests": [
            {"session_id": 59, "status": "suspended", "database_name": "AppDb",
             "elapsed_ms": 77135, "wait_type": "LCK_M_X", "blocking_session_id": 57,
             "sql_text": "UPDATE t SET value = '<script>alert(1)</script>'"},
            {"session_id": 67, "status": "suspended", "database_name": "AppDb",
             "elapsed_ms": 50120, "wait_type": "LCK_M_X", "blocking_session_id": 59,
             "sql_text": "UPDATE t SET value = 2"},
            {"session_id": 68, "status": "running", "database_name": "AppDb",
             "elapsed_ms": 62000, "sql_text": "WAITFOR DELAY '00:02:00'"},
        ],
        "transactions": [{"session_id": 57, "transaction_id": "tx-1",
                          "transaction_age_seconds": 77, "used_ublk": 4}],
        "long_running": [
            {"session_id": 59, "status": "suspended", "database_name": "AppDb",
             "elapsed_ms": 77135, "wait_type": "LCK_M_X", "blocking_session_id": 57,
             "sql_text": "UPDATE t SET value = 1"},
            {"session_id": 67, "status": "suspended", "database_name": "AppDb",
             "elapsed_ms": 50120, "wait_type": "LCK_M_X", "blocking_session_id": 59},
            {"session_id": 68, "status": "running", "database_name": "AppDb",
             "elapsed_ms": 62000, "sql_text": "WAITFOR DELAY '00:02:00'"},
        ],
        "samples": [{"collector": name, "success": True, "truncated": False,
                     "row_count": 1} for name in
                    ("active_sessions", "active_requests", "blocking", "transactions", "connections")],
    }
    return SlowReport(instance, datetime(2026, 10, 17, tzinfo=timezone.utc), "slow", findings, details)


def test_html_slow_report_formats_incident_evidence_and_escapes_database_text() -> None:
    report = _report()
    summary = build_incident_summary(report, "reports/sqlserver2022_slow.html")
    page = HtmlReportRenderer().render(build_report_model(report, summary))
    assert page.index("Incident Summary") < page.index("Immediate Recovery")
    assert page.index("Immediate Recovery") < page.index("Root Cause Candidate")
    assert "Blocking Analysis" in page and "Session Identity" in page
    assert "Transaction Analysis" in page and "Request Runtime" in page
    assert "59" in page and "67" in page
    assert "Long Running Requests" in page and "Blocked 2" in page and "Non-Blocked 1" in page
    assert "KILL 57;" in page and "Toolkit Executed" in page and "Rollback Risk" in page
    assert "00:01:17" in page
    assert "<img src=x" not in page
    assert "<script>alert(1)</script>" not in page
    assert "alert(1)" not in page
    assert '<details' in page and "Session 59 SQL" in page
    assert '{"session_id"' not in page and "{'session_id'" not in page
    assert "https://" not in page and "http://" not in page and "<script" not in page


def test_human_report_tables_have_fixed_columns_and_hide_unrelated_session_fields() -> None:
    report = _report()
    report.details["all_sessions"].append({"session_id": 999, "status": "sleeping",
                                           "secret_internal_field": "must not appear"})
    page = HtmlReportRenderer().render(build_report_model(report,
        build_incident_summary(report, "reports/report.html")))
    assert "secret internal field" not in page
    assert "must not appear" not in page
    assert "Session Identity" in page and "Request Runtime" in page
    assert "Open Txn" not in page or "Open Transaction" in page
    assert "Appendix — Other Sessions" in page


def test_report_policy_bounds_high_concurrency_and_discloses_omissions() -> None:
    from dba.reporting import HumanReportPolicy, render_bounded

    report = _report()
    sessions = report.details["all_sessions"]
    requests = report.details["all_requests"]
    for sid in range(1000, 2000):
        sessions.append({"session_id": sid, "status": "sleeping", "login_name": "app"})
    for sid in range(2000, 2300):
        requests.append({"session_id": sid, "status": "suspended", "wait_type": "LCK_M_X",
                         "blocking_session_id": 57, "elapsed_ms": sid})
    report.details["blocking_graph"]["children"][57].extend(range(2000, 2300))
    report = replace(report, findings=(*report.findings, Finding(
        "LONG_RUNNING_SQL", Severity.WARNING, {"threshold_seconds": 1, "count": 303},
        "Review request runtime.")))
    policy = HumanReportPolicy(max_root_blockers=5, max_blocking_nodes_per_root=10,
                               max_related_sessions=20, max_blocked_requests=5,
                               max_nonblocked_long_running=3, max_transactions=4,
                               max_wait_rows=5, max_appendix_sessions=7,
                               max_sql_chars=256, max_plan_operators=4,
                               soft_report_bytes=2 * 1024 * 1024,
                               hard_report_bytes=8 * 1024 * 1024)
    summary = build_incident_summary(report, "reports/report.html")
    page = render_bounded(build_report_model(report, summary, policy=policy),
                          HtmlReportRenderer().render, policy)
    assert "omitted from the human report" in page.lower()
    assert "Immediate Recovery" in page and "Root Cause Candidate" in page
    assert "Long Running Requests 303 · Blocked 302 · Non-Blocked 1" in page
    assert len(page.encode("utf-8")) < policy.hard_report_bytes
    assert page.count("<tr>") < 100


def test_hard_report_size_fallback_keeps_incident_and_recovery() -> None:
    from dba.reporting import HumanReportPolicy, render_bounded

    report = _report()
    report.details["all_sessions"] *= 100
    summary = build_incident_summary(report, "reports/report.html")
    policy = HumanReportPolicy(soft_report_bytes=4096, hard_report_bytes=16 * 1024)
    page = render_bounded(build_report_model(report, summary, policy=policy),
                          HtmlReportRenderer().render, policy)
    assert "Incident Summary" in page
    assert "Immediate Recovery" in page
    assert "Root Cause Candidate" in page
    assert "Collection Quality" in page
    assert "Report evidence was reduced" in page
    assert len(page.encode("utf-8")) <= policy.hard_report_bytes


def test_transaction_enums_are_database_specific_and_unknown_values_remain_visible() -> None:
    report = _report()
    report.details["transactions"] = [{"session_id": 57, "transaction_id": 775180,
        "transaction_begin_time": "2026-10-17T00:59:54+00:00", "transaction_age_seconds": 2563,
        "transaction_type": 1, "transaction_state": 2}]
    report.details["transactions"].append({"session_id": 58, "transaction_id": 775181,
        "transaction_age_seconds": 4, "transaction_type": 99, "transaction_state": 99})
    summary = build_incident_summary(report, "reports/report.html")
    page = HtmlReportRenderer().render(build_report_model(report, summary))
    assert "Read/Write" in page and "Active" in page
    assert "Unknown (99)" in page


def test_displayed_sql_obeys_report_character_policy() -> None:
    from dba.reporting import HumanReportPolicy

    report = _report()
    report.details["long_running"][0]["sql_text"] = "SELECT " + "x" * 500
    report.details["long_running"][0]["sql_text_truncated"] = False
    policy = HumanReportPolicy(max_sql_chars=32)
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/report.html"), policy=policy))
    assert "SQL text omitted by human report limit" in page
    assert "x" * 100 not in page


@pytest.mark.parametrize("session_count", [20, 50, 100, 500, 1000, 5000])
def test_default_human_report_budget_for_high_session_counts(session_count: int) -> None:
    from dba.reporting import DEFAULT_HUMAN_REPORT_POLICY, render_bounded

    report = _report()
    report.details["all_sessions"] = [
        {"session_id": sid, "login_name": "app", "host_name": "worker",
         "program_name": "synthetic", "database_name": "AppDb", "status": "sleeping"}
        for sid in range(session_count)]
    blocked_count = min(300, max(0, session_count - 1))
    report.details["all_requests"] = [
        {"session_id": sid, "status": "suspended", "database_name": "AppDb",
         "elapsed_ms": sid * 1000, "wait_type": "LCK_M_X", "blocking_session_id": 57}
        for sid in range(1, blocked_count + 1)]
    report.details["blocking_graph"] = {
        "roots": [57], "children": {57: list(range(1, blocked_count + 1))}, "anomalies": []}
    summary = build_incident_summary(report, "reports/synthetic.html")
    page = render_bounded(build_report_model(report, summary), HtmlReportRenderer().render)
    assert "Immediate Recovery" in page and "Root Cause Candidate" in page
    assert "Omitted" in page or "omitted" in page
    assert len(page.encode("utf-8")) <= DEFAULT_HUMAN_REPORT_POLICY.hard_report_bytes
    assert len(page.encode("utf-8")) < 5 * 1024 * 1024


def test_report_filename_timestamp_and_collision(monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    frozen = datetime(2026, 10, 17, 0, 1, 11, tzinfo=datetime.now().astimezone().tzinfo)

    class FrozenDateTime:
        @staticmethod
        def now() -> datetime:
            return frozen

    from dba import cli

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    first = _diagnostic_report_path("sqlserver2022", "slow", None)
    assert first == Path("reports/sqlserver2022_slow_20261017_000111.html")
    first.parent.mkdir(parents=True)
    first.touch()
    second = _diagnostic_report_path("sqlserver2022", "slow", None)
    assert second.name == "sqlserver2022_slow_20261017_000111_02.html"
    assert not re.search(r"[0-9a-f]{32}", first.name)
    assert _diagnostic_report_path("sqlserver2022", "slow", Path("reports/custom.md")) == Path(
        "reports/custom.html"
    )
