"""SQL Server Slow request-status tallies and blocking/high-concurrency finding separation."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from html import unescape
from typing import Any

import pytest
from test_slow import INSTANCE, edge, kpi, report, sample

from dba.incident import build_incident_summary, build_performance_triage, build_priority_candidates
from dba.presentation import render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.normalizers import display_label
from dba.settings import SQLServerSettings
from dba.slow import build_slow_report, sqlserver_request_status


def _scenario_a_pure_concurrency() -> Any:
    """Report 221142 shape: 85 requests (69 runnable, 16 running), 101 connected sessions, no blocking."""
    requests = [edge(100 + index, 0, request_id=0, status="runnable" if index < 69 else "running",
                     elapsed_ms=5_000 + index, cpu_ms=10, logical_reads=20, command="SELECT",
                     statement_sql_text="SELECT COUNT(*) FROM lab.a;") for index in range(85)]
    sessions = [{"session_id": 100 + index, "open_transaction_count": 0} for index in range(101)]
    return report(requests, sessions)


def _scenario_b_blocking_pile_up() -> Any:
    """Report 221745 shape: root 55 runs WAITFOR inside a transaction, 99 sessions wait on LCK_M_X."""
    login = "2026-11-12T22:00:00"
    root = {"session_id": 55, "request_id": 0, "blocking_session_id": 0, "command": "WAITFOR",
            "wait_type": "WAITFOR", "status": "suspended", "elapsed_ms": 300_000, "wait_ms": 299_000,
            "statement_sql_text": "WAITFOR DELAY '00:10:00';"}
    victims = [edge(56 + index, 55, request_id=0, status="suspended", command="UPDATE", wait_type="LCK_M_X",
                    wait_ms=60_000 - index, elapsed_ms=60_000, wait_resource="KEY: synthetic",
                    statement_sql_text="UPDATE lab.a SET v = 1 WHERE id = 1;") for index in range(99)]
    sessions = [{"session_id": sid, "login_time": login, "open_transaction_count": 1, "login_name": "fixture",
                 "host_name": "fixture-host", "program_name": "fixture-app"} for sid in range(55, 155)]
    transactions = [{"session_id": sid, "transaction_id": 581791 if sid == 55 else 600000 + sid,
                     "transaction_type": 1, "transaction_state": 2, "login_time": login,
                     "transaction_age_seconds": 300} for sid in range(55, 155)]
    return report([root, *victims], sessions, transactions)


def _prepare(value: Any) -> Any:
    value.details["performance_triage"] = build_performance_triage(value)
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    return value


def _titles(value: Any) -> set[str]:
    return {finding.title for finding in value.findings}


def _status_text(page: str) -> str:
    match = re.search(r"请求状态：[^<]*?。等待类型（与请求状态分别统计）：[^<]*?。", page)
    assert match is not None
    return unescape(match[0])


def test_scenario_a_pure_high_concurrency_keeps_finding_and_cpu_scheduling_evidence() -> None:
    value = _prepare(_scenario_a_pure_concurrency())
    concurrency = value.details["concurrency"]
    assert concurrency["connected_sessions"] == 101 and concurrency["active_requests"] == 85
    assert concurrency["request_status_counts"] == {
        "RUNNING": 16, "RUNNABLE": 69, "SUSPENDED": 0, "SLEEPING": 0, "BACKGROUND": 0, "UNKNOWN": 0}
    assert concurrency["blocked_requests"] == 0 and value.details["roots"] == []
    assert concurrency["high_concurrency_explained_by_blocking"] is False
    titles = _titles(value)
    assert "HIGH_CONCURRENCY" in titles and not titles & {"BLOCKING", "ROOT_BLOCKER"}
    high = next(finding for finding in value.findings if finding.title == "HIGH_CONCURRENCY")
    assert high.evidence == {"active_requests": 85, "threshold": 50}
    summary = build_incident_summary(value)
    assert summary.primary_problem == "HIGH_CONCURRENCY"
    assert summary.impact["active_requests"] == 85 and summary.impact["blocked_requests"] == 0
    model = build_report_model(value, summary)
    resources = dict(next(s for s in model.sections if s.section_id == "wb-resources").fields)
    assert resources["CPU 调度等待"] == "69 个请求"
    concurrency_section = next(s for s in model.sections if s.section_id == "diagnosis-sql-concurrency")
    assert "可运行 69 条，运行中 16 条，挂起 0 条，状态未知 0 条" in concurrency_section.summary
    page = HtmlReportRenderer().render(model)
    assert kpi(page, "活动请求") == "85" and kpi(page, "阻塞请求") == "0"
    assert "101 个活动请求" not in page and "85 个活动请求" in page


def test_scenario_b_blocking_pile_up_is_primary_without_duplicate_high_concurrency() -> None:
    value = _prepare(_scenario_b_blocking_pile_up())
    concurrency = value.details["concurrency"]
    assert concurrency["active_requests"] == 100 and concurrency["blocked_requests"] == 99
    assert concurrency["request_status_counts"]["SUSPENDED"] == 100
    assert concurrency["request_status_counts"]["UNKNOWN"] == 0
    assert sum(concurrency["request_status_counts"].values()) == 100
    assert concurrency["wait_type_counts"] == {"LOCK": 99, "WAITFOR": 1, "OTHER": 0, "NONE": 0}
    assert concurrency["requests_in_blocking_graph"] == 100 and concurrency["blocking_graph_complete"] is True
    assert concurrency["high_concurrency_explained_by_blocking"] is True
    assert [root["session_id"] for root in value.details["roots"]] == [55]
    assert value.details["roots"][0]["blocked_session_count"] == 99
    titles = _titles(value)
    assert {"BLOCKING", "ROOT_BLOCKER"} <= titles and "HIGH_CONCURRENCY" not in titles
    blocking = next(finding for finding in value.findings if finding.title == "BLOCKING")
    assert blocking.severity.value == "CRITICAL"
    assert blocking.evidence["blocked_requests"] == 99 and blocking.evidence["active_requests"] == 100
    assert blocking.evidence["requests_in_blocking_graph"] == 100
    summary = build_incident_summary(value)
    assert summary.primary_problem == "BLOCKING"
    assert summary.root_cause_candidate and summary.root_cause_candidate["session"] == 55
    assert (summary.impact["active_requests"], summary.impact["blocked_requests"], summary.impact["root_blockers"]) \
        == (100, 99, 1)
    model = build_report_model(value, summary)
    problems = [s.title for s in model.sections if s.section_id.startswith("diagnosis-problem-")]
    assert "高并发" not in problems and "锁与阻塞" in problems
    page = HtmlReportRenderer().render(model)
    status = _status_text(page)
    assert "可运行 0 条，运行中 0 条，挂起 100 条，状态未知 0 条" in status
    assert "锁等待（LCK_M_*） 99 条，WAITFOR 1 条" in status and "其他或状态未采集" not in page
    assert "全部活动请求均位于已确认的阻塞图中" in page
    assert kpi(page, "活动请求") == "100" and kpi(page, "阻塞请求") == "99" and kpi(page, "根阻塞会话") == "1"


@pytest.mark.parametrize("variant", ["independent_runnable", "truncated_requests", "graph_anomaly"])
def test_scenario_c_blocking_does_not_unconditionally_suppress_high_concurrency(variant: str) -> None:
    chain = [edge(51, 0, status="suspended", command="WAITFOR", wait_type="WAITFOR"),
             *(edge(sid, 51, status="suspended", wait_type="LCK_M_X") for sid in range(52, 102))]
    if variant == "independent_runnable":
        requests = [*chain, *(edge(sid, 0, status="runnable", command="SELECT") for sid in range(200, 260))]
    elif variant == "graph_anomaly":
        requests = [*chain, edge(300, -2, status="suspended", wait_type="LCK_M_X")]
    else:
        requests = chain
    results = (sample("active_requests", requests, truncated=variant == "truncated_requests"),
               sample("active_sessions", []), sample("transactions", []), sample("connections", []))
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    titles = _titles(value)
    assert "BLOCKING" in titles and "HIGH_CONCURRENCY" in titles
    assert value.details["concurrency"]["high_concurrency_explained_by_blocking"] is False
    high = next(finding for finding in value.findings if finding.title == "HIGH_CONCURRENCY")
    assert high.evidence["active_requests"] == len(requests)
    assert high.evidence["requests_in_blocking_graph"] + high.evidence["requests_outside_blocking_graph"] \
        == len(requests)
    if variant == "independent_runnable":
        assert high.evidence["requests_outside_blocking_graph"] == 60
        assert value.details["concurrency"]["request_status_counts"]["RUNNABLE"] == 60
        assert high.evidence["blocking_graph_complete"] is True
    else:
        assert high.evidence["blocking_graph_complete"] is False
    blocking = next(finding for finding in value.findings if finding.title == "BLOCKING")
    assert "active_requests" not in blocking.evidence


@pytest.mark.parametrize(("raw", "expected"), [
    ("running", "RUNNING"), ("RUNNABLE", "RUNNABLE"), (" Suspended ", "SUSPENDED"), ("sleeping", "SLEEPING"),
    ("background", "BACKGROUND"), (None, "UNKNOWN"), ("", "UNKNOWN"), ("waiting", "UNKNOWN"), (3, "UNKNOWN"),
])
def test_sqlserver_request_status_normalisation(raw: object, expected: str) -> None:
    assert sqlserver_request_status(raw) == expected


@pytest.mark.parametrize("truncated", [False, True])
def test_scenario_d_missing_status_stays_unknown_without_wait_type_inference(truncated: bool) -> None:
    requests = [edge(51, 0, status=None, wait_type="LCK_M_X"), edge(52, 0, wait_type="WAITFOR"),
                edge(53, 0, status="pending", wait_type="PAGEIOLATCH_SH"), edge(54, 0, status="suspended")]
    results = (sample("active_requests", requests, truncated=truncated),
               sample("active_sessions", []), sample("transactions", []), sample("connections", []))
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    counts = value.details["concurrency"]["request_status_counts"]
    assert counts["UNKNOWN"] == 3 and counts["SUSPENDED"] == 1 and sum(counts.values()) == 4
    assert [row.get("status") for row in value.details["all_requests"]] == [None, None, "pending", "suspended"]
    summary = build_incident_summary(value)
    assert (summary.data_completeness == "COMPLETE") is not truncated
    page = HtmlReportRenderer().render(build_report_model(value, summary))
    status = _status_text(page)
    assert "挂起 1 条，状态未知 3 条" in status
    assert ("活动请求采样不完整，以上计数仅覆盖已采集的请求。" in page) is truncated
    assert "UNKNOWN" not in page


@pytest.mark.parametrize("scenario", [_scenario_a_pure_concurrency, _scenario_b_blocking_pile_up])
def test_scenario_e_html_and_json_report_the_same_counts_and_conclusion(scenario: Any) -> None:
    value = _prepare(scenario())
    summary = build_incident_summary(value)
    exported = json.loads(render_json({"report": asdict(value), "incident_summary": asdict(summary)}))
    details, incident = exported["report"]["details"], exported["incident_summary"]
    page = HtmlReportRenderer().render(build_report_model(value, summary))
    concurrency = details["concurrency"]
    assert int(kpi(page, "活动请求")) == concurrency["active_requests"] == incident["impact"]["active_requests"]
    assert int(kpi(page, "阻塞请求")) == concurrency["blocked_requests"] == incident["impact"]["blocked_requests"]
    assert int(kpi(page, "根阻塞会话")) == len(details["roots"]) == incident["impact"]["root_blockers"]
    status = _status_text(page)
    statuses, waits = concurrency["request_status_counts"], concurrency["wait_type_counts"]
    for label, key in (("可运行", "RUNNABLE"), ("运行中", "RUNNING"), ("挂起", "SUSPENDED"), ("状态未知", "UNKNOWN")):
        assert f"{label} {statuses[key]} 条" in status
    for label, key in (("锁等待（LCK_M_*）", "LOCK"), ("WAITFOR", "WAITFOR"), ("其他等待", "OTHER"),
                       ("无等待类型", "NONE")):
        assert f"{label} {waits[key]} 条" in status
    assert sum(statuses.values()) == sum(waits.values()) == concurrency["active_requests"]
    titles = [finding["title"] for finding in exported["report"]["findings"]]
    severities = {finding["title"]: finding["severity"] for finding in exported["report"]["findings"]}
    assert ("HIGH_CONCURRENCY" in titles) is ('<h2 class="problem-title">高并发' in page or
                                              re.search(r'id="diagnosis-problem-\d+">.{0,400}?高并发', page, re.S)
                                              is not None)
    assert ("BLOCKING" in titles) is (re.search(r'id="diagnosis-problem-\d+">.{0,400}?锁与阻塞', page, re.S)
                                      is not None)
    assert severities.get("BLOCKING", "CRITICAL") == "CRITICAL" and severities.get("HIGH_CONCURRENCY",
                                                                                     "WARNING") == "WARNING"
    pill = re.search(r'class="status-pill [^"]+">.*?</i>(.*?)</span>', page, re.S)
    assert pill and unescape(pill[1]) == " · ".join(display_label(incident[key]) for key in ("status", "severity"))
    hero = re.search(r'class="hero-description">(.*?)</p>', page)
    assert hero and unescape(hero[1]).startswith(f"主要问题：{display_label(incident['primary_problem'])} · ")
    assert f"采集完整性：{'完整' if incident['data_completeness'] == 'COMPLETE' else ''}" in unescape(hero[1])
