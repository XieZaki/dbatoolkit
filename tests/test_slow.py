import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml
from test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider, evaluate_active_request_text

import dba.cli as cli
from dba.analyzers import SlowAnalyzer
from dba.cli import _redact_diagnostic_json, main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, Finding, ReferenceCommand, Severity
from dba.errors import ToolkitError
from dba.incident import build_incident_summary, build_performance_triage, build_priority_candidates
from dba.presentation import render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import (
    DEFAULT_HUMAN_REPORT_POLICY,
    HtmlReportRenderer,
    HumanReportPolicy,
    build_report_model,
)
from dba.settings import SQLSERVER_LONG_TRANSACTION_SECONDS, SQLServerSettings
from dba.slow import blocking_graph, build_slow_report, descendants, diagnose_slow
from dba.slow_reporter import render_console, render_markdown
from dba.sqlserver import SQLServerAdapter

INSTANCE = DatabaseInstance("test", "Test", DatabaseType.SQLSERVER, "test.invalid", 1433, "monitor")


def kpi(page: str, label: str) -> str:
    match = re.search(rf'<span class="metric-label">{re.escape(label)}</span>.*?'
                      r'<strong class="kpi-value[^"]*">([^<]*)</strong>', page, re.S)
    assert match is not None, label
    return match[1]


def sample(name: str, rows: list[dict[str, Any]], *, sampled_at: datetime | None = None, **kwargs: Any) -> CollectorResult:
    now = sampled_at if sampled_at is not None else datetime.now(timezone.utc)
    return CollectorResult(
        "test",
        name,
        now,
        now,
        1,
        tuple(rows),
        len(rows),
        kwargs.get("truncated", False),
        kwargs.get("success", True),
        kwargs.get("error_type"),
    )


def report(
    requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]] | None = None,
    transactions: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
    **settings: Any,
) -> Any:
    return build_slow_report(
        INSTANCE,
        (
            sample("active_requests", requests),
            sample("active_sessions", sessions or []),
            sample("transactions", transactions or []),
            sample("connections", connections or []),
        ),
        SQLServerSettings(**settings),
    )


def edge(child: int, parent: int, **kwargs: Any) -> dict[str, Any]:
    return {"session_id": child, "blocking_session_id": parent, **kwargs}


@pytest.mark.parametrize(
    "edges,roots,count",
    [
        ([(2, 1)], [1], 1),
        ([(2, 1), (3, 2), (4, 2)], [1], 3),
        ([(2, 1), (4, 3)], [1, 3], 1),
        ([], [], 0),
    ],
)
def test_blocking_graph(edges: list[tuple[int, int]], roots: list[int], count: int) -> None:
    graph = blocking_graph([edge(c, p) for c, p in edges])
    assert graph["roots"] == roots
    if roots:
        assert len(descendants(roots[0], graph["children"])) == count


def test_cycles_deep_chain_multiple_parents() -> None:
    graph = blocking_graph([edge(2, 1), edge(1, 2), edge(3, 2)])
    assert graph["roots"] == [] and graph["unresolved"] == [1, 2, 3]
    graph = blocking_graph([edge(n + 1, n) for n in range(1, 2500)])
    assert len(descendants(1, graph["children"])) == 2499
    graph = blocking_graph([edge(3, 1), edge(3, 2)])
    assert graph["roots"] == [1, 2] and graph["anomalies"]


@pytest.mark.parametrize("parent", [-2, -3, -4, -5, -99, 50000, 52])
def test_special_or_invalid_blocker(parent: int) -> None:
    graph = blocking_graph([edge(52, parent)])
    assert not graph["roots"] and graph["anomalies"]


def test_concurrency_and_ranking() -> None:
    value = report(
        [
            edge(52, 51, status="suspended", elapsed_ms=20000, wait_type="LCK_M_X"),
            edge(53, 0, status="running", elapsed_ms=60000),
            edge(53, 0, status="runnable", elapsed_ms=90000),
        ],
        [{"session_id": n} for n in range(51, 60)],
        slow_top_n=2,
        long_running_seconds=60,
        high_concurrency_threshold=3,
    )
    assert value.details["concurrency"] == {
        "connected_sessions": 9,
        "active_sessions": 2,
        "active_requests": 3,
        "running_requests": 1,
        "waiting_requests": 1,
        "blocked_requests": 1,
        "request_status_counts": {"RUNNING": 1, "RUNNABLE": 1, "SUSPENDED": 1, "SLEEPING": 0,
                                  "BACKGROUND": 0, "UNKNOWN": 0},
        "wait_type_counts": {"LOCK": 1, "WAITFOR": 0, "OTHER": 0, "NONE": 2},
        "requests_in_blocking_graph": 1,
        "blocking_graph_complete": True,
        "high_concurrency_explained_by_blocking": False,
    }
    assert [r["elapsed_ms"] for r in value.details["top_requests"]] == [90000, 60000]
    assert len(value.details["long_running"]) == 2
    assert {f.title for f in value.findings} >= {
        "HIGH_CONCURRENCY",
        "LONG_RUNNING_SQL",
        "WAITING_SQL",
        "BLOCKING",
    }


def _waitfor_request(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": 51, "request_id": 0, "blocking_session_id": 0,
        "command": "WAITFOR", "wait_type": "WAITFOR", "status": "suspended",
        "elapsed_ms": 29_000, "wait_ms": 29_000, "cpu_ms": 1, "logical_reads": 2,
        "reads": 0, "writes": 0, "statement_sql_text": "WAITFOR DELAY '00:00:30';",
        **overrides,
    }


@pytest.mark.parametrize("requests", [
    [],
    [edge(51, 0, command="SELECT", status="running", elapsed_ms=2_000)],
    [_waitfor_request()],
])
def test_clear_sqlserver_snapshot_has_no_performance_warning(requests: list[dict[str, Any]]) -> None:
    value = report(requests, [{"session_id": 51, "open_transaction_count": 0}])
    summary = build_incident_summary(value)
    triage = build_performance_triage(value)
    assert value.findings == ()
    assert summary.status == "HEALTHY" and summary.severity == "INFO"
    assert summary.primary_problem == "UNKNOWN" and summary.observed_now == "NOT_DETECTED"
    assert summary.impact["open_transactions"] == 0
    assert summary.immediate_action and summary.immediate_action["priority"] == "INFO"
    assert not summary.recovery_actions
    assert triage["status"] == "UNKNOWN" and triage["root_cause_status"] == "NOT_ESTABLISHED"
    assert triage["assessments"] == []
    assert len(value.details["all_requests"]) == len(requests)
    if requests and requests[0].get("command") == "WAITFOR":
        request = value.details["all_requests"][0]
        assert request["request_kind"] == "ACTIVE_WAIT"
        assert request["wait_type"] == "WAITFOR" and request["wait_ms"] == 29_000
        assert request["statement_sql_text"] == requests[0]["statement_sql_text"]
        assert value.details["concurrency"]["waiting_requests"] == 1
        assert summary.wait is None
        page = HtmlReportRenderer().render(build_report_model(value, summary))
        assert kpi(page, "未结束事务") == "0"
        performance = page.split('id="sql-resource-001"', 1)[1].split('id="blocking-chain"', 1)[0]
        assert "当前 SQL 正在执行 WAITFOR 主动等待" in performance
        assert "持有已核验的未结束事务" not in performance


@pytest.mark.parametrize(("count", "transactions"), [
    (1, []),
    (0, [{"session_id": 51, "transaction_id": 401, "transaction_type": 1, "transaction_state": 2}]),
])
def test_waitfor_with_open_transaction_evidence_stays_a_warning(
    count: int, transactions: list[dict[str, Any]],
) -> None:
    value = report([_waitfor_request(elapsed_ms=200)], [{"session_id": 51, "open_transaction_count": count}],
                   transactions)
    assert {finding.title for finding in value.findings} == {"WAITING_SQL"}
    assert value.details["all_requests"][0].get("request_kind") != "ACTIVE_WAIT"
    summary = build_incident_summary(value)
    assert (summary.status, summary.severity, summary.primary_problem, summary.observed_now) == (
        "DEGRADED", "WARNING", "WAITING_SQL", "ACTIVE")
    assert summary.immediate_action and summary.immediate_action["priority"] == "WARNING"
    assert summary.root_cause_candidate is None
    assert summary.impact["open_transactions"] == len(transactions)
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    model = build_report_model(value, summary)
    priority = next(section for section in model.sections if section.section_id == "priority-candidates").tables[0]
    assert priority.row_states == ("clear",)
    page = HtmlReportRenderer().render(model)
    assert kpi(page, "未结束事务") == str(len(transactions))
    assert build_performance_triage(value)["root_cause_status"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize(("command", "event"), [
    ("WAITFOR", "LCK_M_X"), ("WAITFOR", "PAGEIOLATCH_SH"), ("WAITFOR", "RESOURCE_SEMAPHORE"),
    ("WAITFOR", "CXPACKET"), ("SELECT", "WAITFOR"),
])
def test_one_waitfor_marker_does_not_exempt_the_request(command: str, event: str) -> None:
    value = report([_waitfor_request(command=command, wait_type=event, elapsed_ms=200)],
                   [{"session_id": 51, "open_transaction_count": 0}])
    assert [f.title for f in value.findings] == ["WAITING_SQL"]
    summary = build_incident_summary(value)
    assert summary.wait and summary.wait["event"] == event
    assert (summary.status, summary.severity, summary.primary_problem, summary.observed_now) == (
        "DEGRADED", "WARNING", "WAITING_SQL", "ACTIVE")
    triage = build_performance_triage(value)
    assert triage["status"] == "UNKNOWN" and triage["root_cause_status"] == "NOT_ESTABLISHED"
    assert triage["assessments"][0]["observed_signals"] == ["WAITING_SQL"]


def test_waitfor_does_not_hide_blocking_chain_or_blocking_anomalies() -> None:
    value = report([
        _waitfor_request(),
        edge(52, 51, elapsed_ms=100, wait_type="LCK_M_X"),
        edge(53, 52, elapsed_ms=200, wait_type="LCK_M_S"),
    ], [{"session_id": sid, "open_transaction_count": 0} for sid in (51, 52, 53)])
    assert {"BLOCKING", "ROOT_BLOCKER", "WAITING_SQL"} <= {f.title for f in value.findings}
    summary = build_incident_summary(value)
    assert summary.primary_problem == "BLOCKING" and summary.observed_now == "ACTIVE"
    assert summary.root_cause_candidate and summary.root_cause_candidate["session"] == 51
    assert summary.root_cause_candidate["blocked_sessions"] == 2
    assert summary.wait == {"category": "LOCK", "event": "LCK_M_S"}
    assert value.details["all_requests"][0].get("request_kind") != "ACTIVE_WAIT"
    for blocker in (52, -2):
        blocked_wait = report([_waitfor_request(blocking_session_id=blocker)],
                              [{"session_id": 51, "open_transaction_count": 0}])
        assert "WAITING_SQL" in {f.title for f in blocked_wait.findings}
        assert blocked_wait.details["all_requests"][0].get("request_kind") != "ACTIVE_WAIT"


def test_waitfor_downstream_blocking_report_uses_shared_transaction_and_graph_evidence() -> None:
    login_time = "2026-10-18T13:00:00"
    sessions = [{"session_id": sid, "login_time": login_time, "open_transaction_count": 1}
                for sid in (51, 52, 53)]
    root_tx = {"session_id": 51, "transaction_id": 401, "transaction_type": 1,
               "transaction_state": 2, "login_time": login_time}
    value = report([
        _waitfor_request(elapsed_ms=40_000, wait_ms=14_000),
        edge(52, 51, request_id=0, elapsed_ms=35_000, wait_type="PAGEIOLATCH_SH", wait_ms=30_000),
        edge(52, 51, request_id=1, elapsed_ms=16_000, wait_type="LCK_M_X", wait_ms=11_000),
        edge(52, 51, request_id=2, elapsed_ms=5_000, wait_type="LCK_M_S", wait_ms=2_000),
    ], sessions, [
        root_tx,
        {**root_tx, "session_id": 53},  # A transaction can have more than one session mapping.
        {**root_tx, "session_id": 52, "transaction_id": 402},
        {**root_tx, "transaction_id": 403, "transaction_state": 6},
        {**root_tx, "transaction_id": 404, "transaction_type": 3},
    ], slow_top_n=1)
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    summary = build_incident_summary(value)
    exported = json.loads(render_json({"report": asdict(value), "incident_summary": asdict(summary)}))
    assert exported["incident_summary"]["impact"]["open_transactions"] == 2
    root = exported["report"]["details"]["roots"][0]
    assert root["longest_blocked_lock_wait_ms"] == 11_000
    assert exported["incident_summary"]["timing"]["longest_block"] == "00:00:11"
    candidate = exported["incident_summary"]["root_cause_candidate"]
    assert candidate["open_transaction"] == "YES"
    for key, expected in {"transaction_observed": True, "transaction_identity_verified": True,
                          "transaction_identity_quality": "CONFIRMED", "transaction_id": 401}.items():
        assert root[key] == candidate[key] == expected
    assert value.details["all_requests"][0].get("request_kind") != "ACTIVE_WAIT"
    model = build_report_model(value, summary)
    priority = next(section for section in model.sections if section.section_id == "priority-candidates").tables[0]
    assert [(row[1], state) for row, state in zip(priority.rows, priority.row_states, strict=True)] == [
        ("51", "blocked"), ("52", "blocked"),
    ]
    page = HtmlReportRenderer().render(model)
    assert kpi(page, "未结束事务") == "2"
    assert "已观测到事务" in page and "事务身份已核验" in page
    assert "已确认参与阻塞" in page
    assert '<div class="kpi-foot">最长受阻锁等待 00:00:11</div>' in page
    blocking = next(section for section in model.sections if section.section_id == "blocking")
    assert len(blocking.tables[0].rows) == 2
    assert not blocking.fields and "Wait Time" not in blocking.tables[0].headers
    assert "11,000 ms（00:00:11）" in page


def test_lock_wait_duration_stays_on_its_own_blocking_edge() -> None:
    value = report([
        edge(52, 51, request_id=1, wait_type="LCK_M_X", wait_ms=11_000),
        edge(52, 53, request_id=2, wait_type="LCK_M_S", wait_ms=19_000),
        edge(52, -2, request_id=3, wait_type="LCK_M_X", wait_ms=45_000),
    ])
    assert {root["session_id"]: root["longest_blocked_lock_wait_ms"] for root in value.details["roots"]} == {
        51: 11_000, 53: 19_000,
    }
    assert build_incident_summary(value).timing["longest_block"] == "00:00:19"


def mixed_waitfor_workbench_fixture(
    snapshot: datetime = datetime(2026, 11, 10, 12, 38, 20, tzinfo=timezone.utc),
    *, root_age: int | None = 20, ambiguous_transaction: bool = False, truncated_transactions: bool = False,
) -> Any:
    login_time = (snapshot - timedelta(hours=2)).isoformat()
    requests = [
        _waitfor_request(database_name="FixtureDB", elapsed_ms=15_000, wait_ms=14_000,
                         start_time=(snapshot - timedelta(seconds=15)).isoformat()),
        edge(55, 51, request_id=0, database_name="FixtureDB", status="suspended", elapsed_ms=12_000,
             start_time=(snapshot - timedelta(seconds=12)).isoformat(),
             wait_type="LCK_M_X", wait_ms=11_000, wait_resource="KEY: synthetic",
             statement_sql_text="UPDATE fixture_rows SET value = 2 WHERE id = 1;"),
    ]
    sessions = [{"session_id": sid, "login_time": login_time, "open_transaction_count": 1,
                 "login_name": "fixture-user", "host_name": "fixture-client", "program_name": "fixture-driver",
                 "client_net_address": "192.0.2.10"} for sid in (51, 55)]
    transactions = [
        {"session_id": sid, "transaction_id": sid * 10, "transaction_type": 1, "transaction_state": 2,
         "login_time": login_time, "transaction_begin_time": (snapshot - timedelta(seconds=age or 0)).isoformat(),
         "transaction_age_seconds": age} for sid, age in ((51, root_age), (55, 15))
    ]
    if ambiguous_transaction:
        transactions.append({**transactions[0], "transaction_id": 511})
    results = tuple(sample(name, rows, sampled_at=snapshot, truncated=name == "transactions" and truncated_transactions)
                    for name, rows in (
        ("active_requests", requests), ("active_sessions", sessions), ("transactions", transactions), ("connections", []),
    ))
    value = replace(build_slow_report(INSTANCE, results, SQLServerSettings()), diagnosed_at=snapshot)
    value.details.update(capture_started_at=snapshot.isoformat(), capture_finished_at=snapshot.isoformat(),
                         triage_completed_at=snapshot.isoformat())
    value.details["performance_triage"] = build_performance_triage(value)
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    return value


def test_slow_workbench_explains_mixed_waitfor_and_keeps_technical_evidence_separate() -> None:
    value = mixed_waitfor_workbench_fixture()
    summary = build_incident_summary(value)
    original = asdict(value), asdict(summary)
    model = build_report_model(value, summary)
    page = HtmlReportRenderer().render(model)
    main, appendix = page.split('<details class="panel-group" id="session-appendix" data-expandable>', 1)
    problems = [section.title for section in model.sections if section.section_id.startswith("diagnosis-problem-")]
    assert problems == ["锁与阻塞"]
    assert "涉及会话 51、55" in main
    assert "根会话当前语句为 WAITFOR，事务内获取锁的具体语句暂无法确认。" in main
    assert "根阻塞关联证据" in main and "发生原因" not in main
    assert "会话 51 已确认处于阻塞链根节点，并关联到已核验的未结束事务 510。" in main
    assert "事务内实际取得锁的具体语句尚无法确认" in main
    blocking_page = main.split('id="page-blocking"', 1)[1].split('id="page-plans"', 1)[0]
    assert "00:00:11" in main and "KEY: synthetic" in blocking_page and "KEY: synthetic" in appendix
    assert blocking_page.count("锁对象（库 / 表 / 索引）") == 1 and "本次未采集锁 DMV。" in blocking_page
    start = datetime.fromisoformat(value.details["transactions"][0]["transaction_begin_time"])
    displayed = start.astimezone().isoformat(sep=" ", timespec="seconds")
    assert displayed in main and displayed in appendix
    assert 'href="#current-request-001"' in main and 'href="#current-request-002"' in main
    assert page.count('<code>UPDATE fixture_rows') == 1
    assert 'id="priority-candidates"' not in page and 'id="root-related-requests"' not in page
    assert "details.blocking_graph" not in main and "details.blocking_graph" in appendix
    assert 'id="collection-quality"' in appendix and 'id="performance-triage"' in appendix
    assert "采集完整性：完整" in main and "顺序采样，非原子快照" in main
    assert "根因确认状态" in appendix and "尚未确定" in appendix
    assert "查看同号会话的 SQL（关联待核验）" in main
    for token in ("UNKNOWN", "N/A", "NOT_ESTABLISHED", "CONFIRMED", "Root Blocker", "Read/Write", "Display summary"):
        assert token not in page
    assert (asdict(value), asdict(summary)) == original


@pytest.mark.parametrize(("waits", "truncated", "values", "judgement"), [
    (("PAGEIOLATCH_SH", "PAGEIOLATCH_EX", "RESOURCE_SEMAPHORE"), False,
     {"CPU 调度等待": "0 个请求", "内存授予等待": "1 个请求", "I/O 等待": "2 个请求"},
     "数据库侧观测到资源竞争迹象：I/O 等待 2 个请求。请结合 Zabbix 或主机 CPU、MEM、IO 监控人工核实；"
     "单次采样不能确认容量瓶颈。"),
    (("PAGEIOLATCH_SH", None, "RESOURCE_SEMAPHORE"), False,
     {"CPU 调度等待": "0 个请求", "内存授予等待": "1 个请求", "I/O 等待": "1 个请求"},
     "数据库侧未观测到足以判断资源瓶颈的竞争证据。"),
    (("PAGEIOLATCH_SH", "PAGEIOLATCH_EX", None), True,
     {"CPU 调度等待": "未采集", "内存授予等待": "未采集", "I/O 等待": "未采集"},
     "活动请求采样不完整，不给出数据库侧资源判断。"),
])
def test_slow_workbench_database_resource_evidence_without_host_metrics(
    waits: tuple[str | None, ...], truncated: bool, values: dict[str, str], judgement: str,
) -> None:
    value = report([edge(51 + index, 0, wait_type=wait, status="suspended" if wait else "running")
                    for index, wait in enumerate(waits)])
    value.details["samples"][0]["truncated"] = truncated
    model = build_report_model(value, build_incident_summary(value))
    resources = next(section for section in model.sections if section.section_id == "wb-resources")
    fields = dict(resources.fields)
    assert {key: fields[key] for key in values} == values and resources.summary == judgement
    assert all(not key.startswith("主机") for key in fields) and "扩容" not in judgement
    page = HtmlReportRenderer().render(model)
    overview = page.split('id="page-overview"', 1)[1].split('id="page-sql"', 1)[0]
    evidence = page.split('id="page-resources"', 1)[1].split('id="page-sessions"', 1)[0]
    assert f'<p class="note">{judgement}</p>' in overview and f'<p class="note">{judgement}</p>' in evidence
    assert "主机 CPU、MEM、IO" in evidence and "扩容" not in page
    assert "主机 CPU</span>" not in page and "高并发无主导慢 SQL 诊断" not in page


@pytest.mark.parametrize("last_sql", [None, ["BEGIN TRAN; UPDATE fixture_rows SET value = 1 WHERE id = 1;"]])
def test_slow_workbench_root_blocker_previous_sql_and_lock_states(last_sql: list[str] | None) -> None:
    value = mixed_waitfor_workbench_fixture()
    if last_sql is not None:
        value.details["roots"][0]["last_sql"] = last_sql
    original = asdict(value)
    model = build_report_model(value, build_incident_summary(value))
    root = next(section for section in model.sections if section.section_id == "wb-root-blocker-001")
    fields = dict(root.fields)
    assert fields["会话"] == "51" and fields["影响会话数"] == "1"
    assert fields["锁对象（库 / 表 / 索引）"] == fields["锁模式"] == "未采集"
    assert fields["锁对象解析"] == "本次未采集锁 DMV。"
    assert root.summary_link == "#current-request-001"
    previous = "上一条 SQL（最近批处理，sys.dm_exec_connections.most_recent_sql_handle）"
    if last_sql is None:
        assert fields[previous if previous in fields else "上一条 SQL"] == "未采集" and not root.code_blocks
    else:
        assert root.code_blocks and root.code_blocks[0].content == last_sql[0]
    victims = next(section for section in model.sections if section.section_id == "wb-blocked-sessions").tables[0]
    assert [row[0] for row in victims.rows] == ["55"] and victims.rows[0][5] == victims.rows[0][6] == "51"
    page = HtmlReportRenderer().render(model)
    blocking = page.split('id="page-blocking"', 1)[1].split('id="page-plans"', 1)[0]
    assert blocking.count("<code>BEGIN TRAN; UPDATE fixture_rows") == (0 if last_sql is None else 1)
    assert blocking.count("BEGIN TRAN; UPDATE fixture_rows") == (0 if last_sql is None else 2)
    assert "Toolkit 不执行 KILL、TERMINATE 或 CANCEL" in blocking
    assert asdict(value) == original


@pytest.mark.parametrize(("age", "ambiguous", "truncated", "status", "explanation"), [
    (SQLSERVER_LONG_TRANSACTION_SECONDS, False, False, "CONFIRMED", "已确认"),
    (SQLSERVER_LONG_TRANSACTION_SECONDS - 1, False, False, "NOT_TRIGGERED", "未触发"),
    (20, False, False, "NOT_TRIGGERED", "未触发"),
    (None, False, False, "UNKNOWN", "事务身份已核验，但缺少有效的事务持续时间"),
    (SQLSERVER_LONG_TRANSACTION_SECONDS, True, False, "UNKNOWN", "会话与未结束事务的关联未核验"),
    (SQLSERVER_LONG_TRANSACTION_SECONDS, False, True, "UNKNOWN", "会话或事务采样不完整"),
])
def test_workbench_long_transaction_rule_matches_json_and_verified_root_evidence(
    age: int | None, ambiguous: bool, truncated: bool, status: str, explanation: str,
) -> None:
    value = mixed_waitfor_workbench_fixture(root_age=age, ambiguous_transaction=ambiguous,
                                           truncated_transactions=truncated)
    original = asdict(value)
    exported = json.loads(render_json({"report": original}))["report"]["details"]
    root = exported["roots"][0]
    assert root["transaction_identity_verified"] is (not ambiguous and not truncated)
    assessments = {item["native_identity"]["session_id"]: item
                   for item in exported["performance_triage"]["assessments"]}
    rule = assessments[51]["classification_evidence"]["LONG_RUNNING_TRANSACTION"]
    assert rule["status"] == status
    assert ("LONG_RUNNING_TRANSACTION" in assessments[51]["classifications"]) is (status == "CONFIRMED")
    assert (not rule["uncertainty_reasons"]) is (status != "UNKNOWN")
    assert rule["evidence_references"][0]["facts"]["transaction_age_seconds"] == age
    assert "LONG_RUNNING_TRANSACTION" not in assessments[55]["classification_evidence"]
    assert assessments[51]["root_cause_status"] == "NOT_ESTABLISHED"
    model = build_report_model(value, build_incident_summary(value))
    transaction_rows = next(section for section in model.sections if section.section_id == "transaction-analysis").tables[0]
    victim = next(row for row in transaction_rows.rows if row[0] == "55")
    assert victim[-2] == "部分确认（已观测，关联身份未核验）"
    page = HtmlReportRenderer().render(model)
    assert "查看同号会话的 SQL（关联待核验）" in page
    appendix = page.split('id="performance-triage"', 1)[1]
    if status == "UNKNOWN":
        assert "TRIAGE-LONG-TX-01, 未知" in appendix and explanation in appendix
    else:
        assert f"TRIAGE-LONG-TX-01, {explanation})（持续" in appendix
        assert f"持续 {age} 秒，阈值 {SQLSERVER_LONG_TRANSACTION_SECONDS} 秒" in appendix
    assert asdict(value) == original


@pytest.mark.parametrize("snapshot", [
    datetime(2026, 11, 10, 12, 38, 20, tzinfo=timezone.utc),
    datetime(2026, 11, 10, 0, 0, 4, tzinfo=timezone.utc),
])
def test_synthetic_workbench_timestamps_reconcile_with_sample_window(snapshot: datetime) -> None:
    value = mixed_waitfor_workbench_fixture(snapshot)
    assert value.diagnosed_at == snapshot
    for row in value.details["samples"]:
        assert datetime.fromisoformat(row["finished_at"]) == snapshot
    for row in value.details["transactions"]:
        started = datetime.fromisoformat(row["transaction_begin_time"])
        assert snapshot - started == timedelta(seconds=row["transaction_age_seconds"])
    for row in value.details["all_requests"]:
        started = datetime.fromisoformat(row["start_time"])
        assert snapshot - started == timedelta(milliseconds=row["elapsed_ms"])
        assert 0 <= row["wait_ms"] <= row["elapsed_ms"]
    root_transaction = value.details["transactions"][0]
    root_transaction["transaction_begin_time"] = "2026-11-09T23:59:59"
    original = asdict(value)
    model = build_report_model(value, build_incident_summary(value))
    transactions = next(section for section in model.sections if section.section_id == "transaction-analysis")
    assert "2026-11-09 23:59:59 (时区未知)" in transactions.tables[0].rows[0]
    assert asdict(value) == original


def test_workbench_resources_and_activity_use_evidence_without_repeating_blocking() -> None:
    value = mixed_waitfor_workbench_fixture()
    model = build_report_model(value, build_incident_summary(value))
    page = HtmlReportRenderer().render(model)
    performance = "".join(page.split(f'id="sql-resource-{index:03d}"', 1)[1].split("</section>", 1)[0]
                          for index in (1, 2))
    sql_list = page.split("当前执行 SQL 列表", 1)[1].split('id="current-requests"', 1)[0]
    assert 'href="#current-request-001"' in sql_list and 'href="#current-request-002"' in sql_list
    assert all(fragment in performance for fragment in ("LCK_M_X", "00:00:11", "当前锁等待占请求运行时间的大部分"))
    assert all(fragment not in performance for fragment in ("资源证据判断", "长运行请求", "--session"))
    resources = page.split('id="evidence"', 1)[1].split('id="page-sessions"', 1)[0]
    assert "LCK_M_X" not in resources and "UPDATE fixture_rows" not in resources
    activity = page.split('id="current-request-001"', 1)[1].split('id="sql-resource-001"', 1)[0]
    primary, detail = activity.split("<summary>连接与请求详情</summary>", 1)
    assert "登录账号" in primary and "已执行时间" in primary and "WAITFOR DELAY" in primary
    assert "客户端 IP" not in primary and "192.0.2.10" in detail and "客户端 IP" in detail
    assert "Windows 用户" not in activity and "客户端接口" not in activity
    assert activity.count("<code>WAITFOR DELAY") == 1
    summary_copy = re.search(r'id="copySummary"[^>]*data-copy="([^"]+)"', page)
    assert summary_copy is not None
    assert "阻塞请求数：1" in unescape(summary_copy[1]) and "blocked_requests" not in summary_copy[1]
    assert "活动请求（active_requests）" in page and "尚未评估" in page


@pytest.mark.parametrize(("event", "expected"), [
    (None, "暂无法判断主要资源消耗"), ("RESOURCE_SEMAPHORE", "当前正在等待查询内存授予"),
])
def test_workbench_distinguishes_missing_memory_and_unverified_blocking(event: str | None, expected: str) -> None:
    value = report([edge(51, 0, wait_type=event)])
    value.details["samples"][0]["truncated"] = True
    model = build_report_model(value, build_incident_summary(value))
    resource = next(section for section in model.sections if section.section_id == "sql-resource-001")
    metrics = dict(resource.fields)
    assert all(metrics[label] == "未采集" for label in (
        "CPU 时间（自请求开始累计）", "逻辑读取（自请求开始累计）", "物理读取（自请求开始累计）",
        "写入计数（自请求开始累计）", "当前等待时长",
    ))
    assert expected in metrics["性能分析"] and "阻塞者" not in metrics["性能分析"]
    quality = next(section for section in model.sections if section.section_id == "collection-quality").tables[0]
    request_sample = next(row for row in quality.rows if row[0] == "活动请求（active_requests）")
    assert request_sample[2] == "已达到采集行数上限"


def test_workbench_reports_observed_blocking_without_claiming_missing_lock_timing() -> None:
    value = report([edge(55, 51, wait_type="LCK_M_X")])
    value.details["samples"][0]["truncated"] = True
    model = build_report_model(value, build_incident_summary(value))
    resource = next(section for section in model.sections if section.section_id == "sql-resource-001")
    metrics = dict(resource.fields)
    assert "等待会话 51" in metrics["性能分析"] and metrics["当前等待时长"] == "未采集"
    assert "已观测到锁等待，当前等待时长未采集" in metrics["性能分析"]
    assert "占请求运行时间的大部分" not in metrics["性能分析"]


def mixed_waitfor_resource_fixture() -> Any:
    snapshot = datetime(2026, 11, 10, 12, 38, 20, tzinfo=timezone.utc)
    login = (snapshot - timedelta(hours=4)).isoformat()
    requests = [
        _waitfor_request(database_name="FixtureDB", elapsed_ms=91_000, wait_ms=None, cpu_ms=2, logical_reads=2,
                         reads=0, writes=0, start_time=(snapshot - timedelta(seconds=91)).isoformat(),
                         statement_sql_text="WAITFOR DELAY '00:03:00';"),
        edge(55, 51, request_id=0, database_name="FixtureDB", command="UPDATE", status="suspended",
             elapsed_ms=9_600_500, wait_ms=9_600_000, wait_type="LCK_M_X", cpu_ms=0, logical_reads=2, reads=0,
             wait_resource="KEY: synthetic",
             start_time=(snapshot - timedelta(milliseconds=9_600_500)).isoformat(),
             statement_sql_text="UPDATE fixture_rows SET value = 2 WHERE id = 1;"),
    ]
    sessions = [{"session_id": sid, "login_time": login, "open_transaction_count": 1,
                 "login_name": f"fixture_user_{sid}"} for sid in (51, 55)]
    transactions = [{"session_id": 51, "transaction_id": 510, "transaction_state": 2, "transaction_type": 1,
                     "login_time": login, "transaction_begin_time": (snapshot - timedelta(seconds=9_600)).isoformat(),
                     "transaction_age_seconds": 9_600}]
    samples = tuple(sample(name, rows, sampled_at=snapshot) for name, rows in (
        ("active_requests", requests), ("active_sessions", sessions), ("transactions", transactions), ("connections", []),
    ))
    value = replace(build_slow_report(INSTANCE, samples, SQLServerSettings()), diagnosed_at=snapshot)
    value.details.update(capture_started_at=snapshot.isoformat(), capture_finished_at=snapshot.isoformat(),
                         performance_triage=build_performance_triage(value))
    return value


def test_sqlserver_resource_cards_show_observed_request_costs_and_keep_transaction_time_separate() -> None:
    value = mixed_waitfor_resource_fixture()
    original = asdict(value)
    exported = json.loads(render_json({"report": original}))["report"]["details"]
    model = build_report_model(value, build_incident_summary(value))
    cards = {section.title.split()[1]: section for section in model.sections
             if section.section_id.startswith("sql-resource-")}
    metrics = {sid: dict(card.fields) for sid, card in cards.items()}
    assert metrics["51"]["CPU 时间（自请求开始累计）"] == "2 ms" and metrics["51"]["当前等待时长"] == "未采集"
    assert all(text in metrics["51"]["性能分析"] for text in (
        "WAITFOR 主动等待", "已确认的根阻塞节点", "已核验的未结束事务", "取得锁的具体语句仍待核查",
    ))
    assert metrics["55"]["当前等待时长"] == "9,600,000 ms（02:40:00）"
    assert "当前锁等待占请求运行时间的大部分" in metrics["55"]["性能分析"]
    assert "等待会话 51" in metrics["55"]["性能分析"]
    assert "截至采集时，当前请求累计 CPU 时间为零。" in metrics["55"]["性能分析"]
    assert "截至采集时，当前请求累计物理读取为零。" in metrics["55"]["性能分析"]
    for row in exported["all_requests"]:
        sid = str(row["session_id"])
        assert metrics[sid]["CPU 时间（自请求开始累计）"] == f"{row['cpu_ms']} ms"
        assert metrics[sid]["逻辑读取（自请求开始累计）"] == f"{row['logical_reads']} 页（按 8 KiB/页折合 16 KiB）"
        assert metrics[sid]["物理读取（自请求开始累计）"] == f"{row['reads']} 页"
        assert metrics[sid]["当前等待事件"].startswith(row["wait_type"])
        assert metrics[sid]["写入计数（自请求开始累计）"] == (
            f"{row['writes']} 次" if row.get("writes") is not None else "未采集")
    assert metrics["51"]["写入计数（自请求开始累计）"] == "0 次"
    assert metrics["55"]["写入计数（自请求开始累计）"] == "未采集"
    assert cards["55"].summary_link == "#current-request-001"
    assert cards["51"].summary_link == "#current-request-002"
    page = HtmlReportRenderer().render(model)
    region = page.split('id="current-requests"', 1)[1].split('id="blocking-chain"', 1)[0]
    activity = "".join(page.split(f'id="current-request-{index:03d}"', 1)[1].split(
        f'id="sql-resource-{index:03d}"', 1)[0] for index in (1, 2))
    blocking = page.split('id="blocking-chain"', 1)[1].split('id="manual-action"', 1)[0]
    transactions = page.split('id="transactions"', 1)[1].split('id="page-plans"', 1)[0]
    performance = "".join(page.split(f'id="sql-resource-{index:03d}"', 1)[1].split("</section>", 1)[0]
                          for index in (1, 2))
    assert region.count('id="sql-resource-') == 2 and region.count('id="current-request-') == 2
    assert "00:01:31" in activity and "请求 ID" in activity and activity.count("复制文本") == 2
    assert all(text not in activity for text in (
        "CPU 时间", "逻辑读取", "物理读取", "写入", "等待类型", "等待时间", "LCK_M_X", "阻塞者",
    ))
    assert "直接阻塞者" in blocking and "层级" in blocking
    assert all(text not in blocking for text in ("LCK_M_X", "等待", "02:40:00", "事务", "CPU 时间"))
    assert all(text in transactions for text in (
        "510", "02:40:00", "读写事务", "活动中", "已确认", 'href="#current-request-002"',
        '<a href="#transaction-start-source">SQL Server 事务 DMV</a>',
    ))
    assert "sys.dm_tran_active_transactions.transaction_begin_time" not in transactions
    assert "CPU 时间" not in transactions and "原始阻塞边" not in blocking
    appendix = page.split('id="session-appendix"', 1)[1]
    assert 'id="blocking-evidence"' in appendix and 'id="transaction-start-source"' in appendix
    assert "sys.dm_tran_active_transactions.transaction_begin_time" in appendix
    sql_list = page.split("当前执行 SQL 列表", 1)[1].split('id="current-requests"', 1)[0]
    assert "自开始累计至采集时刻的值，不是采样区间增量" in sql_list
    headings = re.findall(r'id="(?:current-request|sql-resource)-\d+">.*?</h2>', region, re.DOTALL)
    assert len(headings) == 4 and all('class="badge INFO"' not in heading for heading in headings)
    assert 'class="tag tag-red">高优先级' in page.split('id="root-assessment"', 1)[1].split('id="current-requests"', 1)[0]
    assert "请求采样范围" not in performance and "00:01:31" not in performance and "<code>" not in performance
    assert "0 ms" in performance and "02:40:00" in performance
    assert asdict(value) == original


def test_sqlserver_concurrency_report_matches_json_requests_and_keeps_sql_copy_intact() -> None:
    statements = (
        "SELECT /* Q4 fixture 中文🚀 */ a.key_id, COUNT_BIG(*) FROM lab.a AS a WITH (INDEX(0)) "
        "JOIN lab.b AS b ON CONVERT(varchar(20), a.key_id)=b.key_id "
        "JOIN lab.c AS c ON a.key_id=c.key_id GROUP BY a.key_id ORDER BY a.key_id OPTION (HASH JOIN, MAXDOP 1)",
        "WITH q AS (SELECT key_id FROM lab.a) SELECT q.key_id FROM q JOIN lab.b AS b "
        "ON q.key_id=b.key_id OPTION (HASH JOIN, MAXDOP 1)",
        "SELECT a.key_id FROM lab.a AS a JOIN lab.a AS b "
        "ON a.key_id=b.key_id AND a.key_id<17 OPTION (HASH JOIN, MAXDOP 1)",
    )
    rows = []
    for group, (statement, size) in enumerate(zip(statements, (3, 2, 1), strict=True), 1):
        for member in range(size):
            rows.append(edge(50 + len(rows), 0, request_id=0, database_id=5, database_name="Lab",
                             status="runnable" if member == 0 else "running",
                             cpu_ms=group * 7, logical_reads=group * 17, sql_handle=f"0x{group:02x}",
                             statement_start_offset=0, statement_end_offset=len(statement.encode("utf-16-le")) - 2,
                             statement_sql_text=statement, statement_sql_text_truncated=False))
    value = report(rows, high_concurrency_threshold=2)
    original = asdict(value)
    exported = json.loads(render_json({"report": original}))["report"]["details"]["all_requests"]
    model = build_report_model(value, build_incident_summary(value))
    aggregate = next(section for section in model.sections if section.section_id == "diagnosis-sql-concurrency")
    assert "可运行 3 条，运行中 3 条" in aggregate.summary
    assert "持续 CPU 瓶颈待核验" in dict(aggregate.fields)["调度证据"]
    for index, statement in enumerate(statements):
        matches = [row for row in exported if row["statement_sql_text"] == statement]
        actual = aggregate.tables[0].rows[index]
        assert actual[1:6] == (
            str(len(matches)), f"{sum(row['cpu_ms'] for row in matches)} ms",
            f"{sum(row['logical_reads'] for row in matches)} 页", "1", str(matches[0]["session_id"]),
        )
    assert aggregate.tables[0].rows[0][-1] == "INDEX(0) 访问路径限制、JOIN 条件中的 CONVERT、多表 JOIN、GROUP BY、ORDER BY"
    assert aggregate.tables[0].rows[2][-1] == "—"
    page = HtmlReportRenderer().render(model)
    diagnosis = page.split('id="root-assessment"', 1)[1].split('id="current-requests"', 1)[0]
    assert 'id="diagnosis-sql-concurrency"' in diagnosis and 'href="#current-request-001"' in diagnosis
    activity = page.split('id="current-requests"', 1)[1].split('id="blocking-chain"', 1)[0]
    bodies = [unescape(sql) for sql in re.findall(r"<code>(.*?)</code>", activity, re.DOTALL)]
    copies = [unescape(sql) for sql in re.findall(r'data-copy="([^"]*)"', activity)]
    assert bodies == copies == [row["statement_sql_text"] for row in exported]
    assert asdict(value) == original


def test_sqlserver_concurrency_keeps_inexact_identity_and_missing_metrics_separate() -> None:
    common = dict(database_id=5, sql_handle="0xAB", statement_start_offset=0, statement_end_offset=50,
                  query_hash="0x01", statement_sql_text="SELECT same_prefix")
    variants: list[dict[str, Any]] = [
        {"cpu_ms": 0, "logical_reads": 0, "status": "runnable", "wait_type": "SOS_SCHEDULER_YIELD"},
        {"sql_handle": "0xab", "logical_reads": 5, "status": "running"},
        {"database_id": 6}, {"statement_start_offset": 2}, {"sql_handle": "0xAC"},
        {"sql_handle": None, "statement_sql_text_truncated": True},
        {"sql_handle": None, "statement_sql_text_truncated": True},
    ]
    value = report([edge(51 + i, 0, **{**common, **variant}) for i, variant in enumerate(variants)],
                   high_concurrency_threshold=2)
    value.details["samples"][0]["truncated"] = True
    model = build_report_model(value, build_incident_summary(value))
    section = next(section for section in model.sections if section.section_id == "diagnosis-sql-concurrency")
    assert "采样不完整" in section.summary and "2 条请求缺少可核验的语句身份" in section.summary
    assert "1 条记录了 SOS_SCHEDULER_YIELD" in section.summary
    assert "累计 CPU 最高" not in dict(section.fields)["优先核查"]
    groups = section.tables[0].rows
    assert len(groups) == 6
    assert groups[0][1:5] == ("2", "0 ms（1/2 条已采集）", "5 页", "1")
    assert all(row[1:4] == ("1", "未采集", "未采集") for row in groups[1:])
    assert all("身份未核验" in row[0] for row in groups[-2:])
    bounded = build_report_model(value, build_incident_summary(value),
                                 policy=HumanReportPolicy(max_wait_rows=3, max_related_sessions=2))
    table = next(s for s in bounded.sections if s.section_id == "diagnosis-sql-concurrency").tables[0]
    assert len(table.rows) == 3 and table.cell_links == ((0, 5, "#current-request-001"),)


@pytest.mark.parametrize("end_at_batch_end", [False, True])
@pytest.mark.parametrize("extra_units", [0, 1])
def test_sqlserver_slow_exact_text_limit_and_one_unit_over_preserve_json_html_parity(
    end_at_batch_end: bool, extra_units: int,
) -> None:
    limit = DEFAULT_HUMAN_REPORT_POLICY.max_sql_chars
    literal_start = "SELECT N'中文<&"
    statement = literal_start + "x" * (limit + extra_units - len(literal_start) - 1) + "'"
    prefix = "" if end_at_batch_end else "SELECT N'前😀';\r\n"
    batch = prefix + statement + ("" if end_at_batch_end else ";\r\nSELECT 98765;")
    start_offset = len(prefix.encode("utf-16-le"))
    end_offset = -1 if end_at_batch_end else start_offset + len(statement.encode("utf-16-le")) - 2
    batch_text, batch_truncated, text, unavailable, truncated = evaluate_active_request_text(
        batch, start_offset, end_offset)
    assert (text, unavailable, truncated) == (statement[:limit], None, extra_units)
    batch_bytes = batch.encode("utf-16-le")
    assert (batch_text, batch_truncated) == (
        batch_bytes[:limit * 2].decode("utf-16-le"), int(len(batch_bytes) > limit * 2))

    snapshot = report([edge(51, 0, request_id=0, status="running", command="SELECT",
                            statement_start_offset=start_offset, statement_end_offset=end_offset,
                            sql_text=batch_text, sql_text_truncated=batch_truncated,
                            statement_sql_text=text, statement_sql_text_truncated=truncated)])
    incident = build_incident_summary(snapshot)
    original = asdict(snapshot)
    payload = _redact_diagnostic_json({"report": original, "incident_summary": asdict(incident)})
    exported = json.loads(render_json(payload))["report"]["details"]["all_requests"][0]
    assert exported["statement_sql_text"] == text and bool(exported["statement_sql_text_truncated"]) == bool(extra_units)
    page = HtmlReportRenderer().render(build_report_model(snapshot, incident))
    activity = page.split('id="current-request-001"', 1)[1].split('id="blocking-chain"', 1)[0]
    body = re.search(r"<code>(.*?)</code>", activity, re.DOTALL)
    copy = re.search(r'data-copy="([^"]*)"', activity)
    assert body is not None and copy is not None
    assert unescape(body[1]) == unescape(copy[1]) == exported["statement_sql_text"]
    assert ("当前语句文本已截断" in activity) == bool(extra_units)
    assert "SELECT 98765" not in activity
    assert asdict(snapshot) == original


def test_workbench_keeps_independent_io_and_cpu_evidence_separate_from_lock_chain() -> None:
    mixed = mixed_waitfor_workbench_fixture()
    value = report([
        *mixed.details["all_requests"], edge(61, 0, elapsed_ms=3_000, wait_type="PAGEIOLATCH_SH", wait_ms=2_000),
        edge(62, 0, elapsed_ms=13_000, status="running", cpu_ms=10_000, command="SELECT", plan_handle="fixture-plan"),
    ], mixed.details["all_sessions"], mixed.details["transactions"])
    value = replace(value, findings=(*value.findings, Finding(
        "CPU_PRESSURE", Severity.WARNING, {"cpu_ms": 10_000}, "复核此请求的 CPU 证据。", session="62")))
    model = build_report_model(value, build_incident_summary(value))
    problems = {section.title: dict(section.fields) for section in model.sections
                if section.section_id.startswith("diagnosis-problem-")}
    assert set(problems) == {"锁与阻塞", "需关注的请求等待", "CPU 压力"}
    assert "会话 61" in problems["需关注的请求等待"]["影响范围"]
    assert "PAGEIOLATCH_SH（I/O 等待）" in problems["需关注的请求等待"]["诊断依据"]
    assert "尚不能确定根因" in problems["需关注的请求等待"]["发生原因"]
    assert problems["CPU 压力"]["影响范围"] == "会话 62"
    plans = next(section for section in model.sections if section.section_id == "execution-plan-follow-up")
    assert plans.fields == (("会话 62", "dba diagnose test plan --session 62"),)


@pytest.mark.parametrize("shared_victim", [False, True])
def test_workbench_uses_one_blocking_row_per_session_with_multiple_roots(shared_victim: bool) -> None:
    requests = [edge(52, 51), edge(53, 52), edge(55, 54)]
    if shared_victim:
        requests.append(edge(52, 54))
    value = report(requests)
    model = build_report_model(value, build_incident_summary(value))
    blocking = next(section for section in model.sections if section.section_id == "blocking")
    rows = {row[0].split()[-1]: row for row in blocking.tables[0].rows}
    assert len(rows) == len(blocking.tables[0].rows) == 5
    assert {sid for sid, row in rows.items() if row[1] == "Root Blocker"} == {"51", "54"}
    assert rows["53"][2:4] == ("52", "2") and rows["55"][2] == "54"
    assert rows["52"][2] == ("51、54" if shared_victim else "51")
    assert ("阻塞证据不完整" in blocking.summary) is shared_victim
    appendix = next(section for section in model.sections if section.section_id == "blocking-evidence")
    assert len(appendix.tables[0].rows) == len(requests)


@pytest.mark.parametrize("failed", [False, True])
def test_workbench_distinguishes_complete_empty_samples_from_collection_failure(failed: bool) -> None:
    results = tuple(sample(name, [], success=not (failed and name == "active_requests"))
                    for name in ("active_requests", "active_sessions", "transactions", "connections"))
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    summary = build_incident_summary(value)
    page = HtmlReportRenderer().render(build_report_model(value, summary))
    expected = "未知" if failed else "0"
    assert kpi(page, "活动请求") == expected
    assert kpi(page, "根阻塞会话") == expected
    assert ("活动请求采集失败" if failed else "本次采样没有活动请求") in page


@pytest.mark.parametrize("limitation", ["failed", "truncated", "missing_identity", "missing_state"])
def test_unreliable_transaction_sample_keeps_count_and_root_identity_unknown(limitation: str) -> None:
    login_time = "2026-10-18T13:00:00"
    transaction = {"session_id": 51, "transaction_id": None if limitation == "missing_identity" else 401,
                   "transaction_type": 1, "transaction_state": None if limitation == "missing_state" else 2,
                   "login_time": login_time}
    value = build_slow_report(INSTANCE, (
        sample("active_requests", [_waitfor_request(), edge(52, 51, wait_type="LCK_M_X")]),
        sample("active_sessions", [{"session_id": 51, "login_time": login_time, "open_transaction_count": 1}]),
        sample("transactions", [transaction], success=limitation != "failed", truncated=limitation == "truncated"),
        sample("connections", []),
    ), SQLServerSettings())
    summary = build_incident_summary(value)
    assert "open_transactions" not in summary.impact
    candidate = summary.root_cause_candidate
    assert candidate is not None and candidate["open_transaction"] == "UNKNOWN"
    assert candidate["transaction_id"] is None
    if limitation in {"failed", "missing_state"}:
        assert candidate["transaction_observed"] is None
        assert candidate["transaction_identity_verified"] is None
    else:
        assert candidate["transaction_observed"] is True
        assert candidate["transaction_identity_verified"] is False
    assert not summary.recovery_actions
    page = HtmlReportRenderer().render(build_report_model(value, summary))
    assert kpi(page, "未结束事务") == "未知"
    assert "无法确认未结束事务数量" in page


@pytest.mark.parametrize(("requests_truncated", "expected_badge"), [
    (False, "blocked"), (True, "observed-blocking"),
])
def test_observed_lock_edge_does_not_verify_a_reused_session(
    requests_truncated: bool, expected_badge: str,
) -> None:
    value = build_slow_report(INSTANCE, (
        sample("active_requests", [_waitfor_request(), edge(52, 51, wait_type="LCK_M_X", wait_ms=1_500)],
               truncated=requests_truncated),
        sample("active_sessions", [{"session_id": 51, "login_time": "2026-10-18T13:00:00",
                                    "open_transaction_count": 1, "login_name": "fixture", "host_name": "host",
                                    "program_name": "test"}]),
        sample("transactions", [{"session_id": 51, "login_time": "2026-10-18T14:00:00", "transaction_id": 401,
                                  "transaction_state": 2, "transaction_type": 1}]),
        sample("connections", []),
    ), SQLServerSettings())
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    summary = build_incident_summary(value)
    candidate = summary.root_cause_candidate
    assert candidate and candidate["transaction_identity_quality"] == "RECHECK_REQUIRED"
    assert candidate["transaction_observed"] is True and candidate["transaction_identity_verified"] is False
    assert candidate["open_transaction"] == "UNKNOWN" and candidate["transaction_id"] is None
    assert summary.impact["open_transactions"] == 1  # The transaction sample is complete on its own.
    assert not summary.recovery_actions and not any(finding.reference_commands for finding in value.findings)
    model = build_report_model(value, summary)
    priority = next(section for section in model.sections if section.section_id == "priority-candidates").tables[0]
    assert priority.row_states == (expected_badge, expected_badge)


@pytest.mark.parametrize("elapsed", [9_999, 10_000])
def test_sqlserver_long_running_threshold_is_unchanged(elapsed: int) -> None:
    value = report([edge(51, 0, command="SELECT", status="running", elapsed_ms=elapsed)],
                   long_running_seconds=10)
    assert ("LONG_RUNNING_SQL" in {f.title for f in value.findings}) is (elapsed >= 10_000)


def test_mixed_waitfor_and_real_wait_stay_separate_in_summary_triage_and_html() -> None:
    value = report([
        _waitfor_request(database_name="test-db"),
        edge(52, 0, status="suspended", command="SELECT", elapsed_ms=11_000,
             wait_type="PAGEIOLATCH_SH", statement_sql_text="SELECT long_io"),
    ], [{"session_id": 51, "open_transaction_count": 0, "login_name": "fixture-user",
         "host_name": "fixture-host", "client_net_address": "192.0.2.51", "program_name": "fixture-app"}],
        slow_top_n=1)
    assert [r["session_id"] for r in value.details["top_requests"]] == [51]
    for title in ("WAITING_SQL", "LONG_RUNNING_SQL"):
        finding = next(f for f in value.findings if f.title == title)
        assert [r["session_id"] for r in finding.evidence["requests"]] == [52]
    summary = build_incident_summary(value)
    assert summary.wait == {"category": "IO", "event": "PAGEIOLATCH_SH"}
    assert summary.status == "DEGRADED" and summary.primary_problem == "LONG_RUNNING_SQL"
    triage = build_performance_triage(value)
    assert [a["native_identity"]["session_id"] for a in triage["assessments"]] == [52]
    assert triage["assessments"][0]["observed_signals"] == ["LONG_RUNNING_SQL", "WAITING_SQL"]
    model = build_report_model(value, summary)
    long_section = next(s for s in model.sections if s.section_id == "nonblocked-long-running")
    assert [row[0] for row in long_section.tables[0].rows] == ["52"]
    html = HtmlReportRenderer().render(model)
    for fragment in ("WAITFOR DELAY", "WAITFOR 主动等待", "fixture-user", "fixture-host",
                     "192.0.2.51", "fixture-app", "29000", "PAGEIOLATCH_SH"):
        assert fragment in html


@pytest.mark.parametrize("missing", ["failed", "truncated", "missing_counter", "missing_blocker"])
def test_waitfor_with_incomplete_safety_evidence_remains_unknown(missing: str) -> None:
    request = _waitfor_request()
    if missing == "missing_blocker":
        request.pop("blocking_session_id")
    session = {"session_id": 51, "open_transaction_count": 0} if missing != "missing_counter" else {"session_id": 51}
    value = build_slow_report(INSTANCE, (
        sample("active_requests", [request]), sample("active_sessions", [session]),
        sample("transactions", [], success=missing != "failed", truncated=missing == "truncated"),
        sample("connections", []),
    ), SQLServerSettings())
    assert {f.title for f in value.findings} == {"INCOMPLETE_SNAPSHOT"}
    assert value.details["incomplete"] is True
    summary = build_incident_summary(value)
    assert summary.data_completeness != "COMPLETE"
    assert summary.status == summary.primary_problem == summary.observed_now == "UNKNOWN"
    assert summary.immediate_action is None and not summary.recovery_actions
    assert build_performance_triage(value)["status"] == "UNKNOWN"


def _request_with_empty_normalized_identity(session_id: int, **values: Any) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "status": "running",
        "elapsed_ms": 30000,
        "blocking_session_id": 0,
        "normalized": {
            "session_id": session_id,
            "username": None,
            "client_host": None,
            "client_ip": None,
            "application": None,
            **values,
        },
    }


def test_active_request_normalized_identity_uses_matching_session_and_stays_equal() -> None:
    request = _request_with_empty_normalized_identity(51)
    original_normalized = dict(request["normalized"])
    session = {
        "session_id": 51,
        "login_name": "sa",
        "host_name": "ZAKKY",
        "client_net_address": "172.17.0.1",
        "program_name": "Navicat",
    }

    value = report([request], [session], slow_top_n=1)

    expected = {
        "username": "sa",
        "client_host": "ZAKKY",
        "client_ip": "172.17.0.1",
        "application": "Navicat",
    }
    top_request = value.details["top_requests"][0]
    all_request = value.details["all_requests"][0]
    assert {key: top_request["normalized"][key] for key in expected} == expected
    assert top_request["normalized"] == all_request["normalized"]
    assert request["normalized"] == original_normalized
    assert all_request["status"] == "running"
    assert all_request["blocking_session_id"] == 0


def test_active_request_without_matching_session_keeps_normalized_identity_null() -> None:
    request = _request_with_empty_normalized_identity(52)

    value = report([request], [{"session_id": 51, "login_name": "sa"}])

    normalized = value.details["all_requests"][0]["normalized"]
    assert {key: normalized[key] for key in (
        "username", "client_host", "client_ip", "application",
    )} == {"username": None, "client_host": None, "client_ip": None, "application": None}


def test_active_request_missing_session_source_fields_remain_null() -> None:
    request = _request_with_empty_normalized_identity(51)
    session = {
        "session_id": 51,
        "host_name": "ZAKKY",
        "program_name": "Navicat",
    }

    value = report([request], [session])

    normalized = value.details["all_requests"][0]["normalized"]
    assert normalized["username"] is None
    assert normalized["client_host"] == "ZAKKY"
    assert normalized["client_ip"] is None
    assert normalized["application"] == "Navicat"


def test_active_request_normalized_identity_does_not_overwrite_existing_values() -> None:
    request = _request_with_empty_normalized_identity(
        51, username="request-user", client_ip="192.0.2.4",
    )
    session = {
        "session_id": 51,
        "login_name": "session-user",
        "host_name": "ZAKKY",
        "client_net_address": "172.17.0.1",
        "program_name": "Navicat",
    }

    value = report([request], [session])

    normalized = value.details["all_requests"][0]["normalized"]
    assert normalized["username"] == "request-user"
    assert normalized["client_host"] == "ZAKKY"
    assert normalized["client_ip"] == "192.0.2.4"
    assert normalized["application"] == "Navicat"


def test_active_request_sources_match_each_session_and_preserve_request_values() -> None:
    requests = [
        {"session_id": 51, "request_id": 1, "elapsed_ms": 2000,
         "login_name": "request-user", "normalized": {"username": "request-user"}},
        {"session_id": 52, "request_id": 1, "elapsed_ms": 1000,
         "normalized": {"username": None}},
    ]
    sessions = [
        {"session_id": 51, "login_name": "session-user-a", "original_login_name": "original-a",
         "nt_user_name": "DOMAIN\\user-a", "host_name": "same-host",
         "client_net_address": "192.0.2.51", "program_name": "client-a",
         "client_interface_name": "ODBC", "host_process_id": 5100},
        {"session_id": 52, "login_name": "session-user-b", "original_login_name": "original-b",
         "nt_user_name": "DOMAIN\\user-b", "host_name": "same-host",
         "client_net_address": "192.0.2.52", "program_name": "client-b",
         "client_interface_name": ".Net SqlClient", "host_process_id": 5200},
    ]

    value = report(requests, sessions)
    by_request = {row["session_id"]: row for row in value.details["all_requests"]}

    assert by_request[51]["login_name"] == "request-user"
    assert by_request[51]["original_login_name"] == "original-a"
    assert by_request[51]["nt_user_name"] == "DOMAIN\\user-a"
    assert by_request[51]["client_net_address"] == "192.0.2.51"
    assert by_request[51]["client_interface_name"] == "ODBC"
    assert by_request[51]["host_process_id"] == 5100
    assert by_request[51]["normalized"]["username"] == "request-user"
    assert by_request[52]["login_name"] == "session-user-b"
    assert by_request[52]["client_net_address"] == "192.0.2.52"
    assert by_request[52]["program_name"] == "client-b"


def test_duplicate_session_ids_do_not_supply_ambiguous_request_identity() -> None:
    request = _request_with_empty_normalized_identity(51)
    value = report([request], [
        {"session_id": 51, "login_name": "user-a"},
        {"session_id": 51, "login_name": "user-b"},
    ])

    row = value.details["all_requests"][0]
    assert row["normalized"]["username"] is None
    assert "login_name" not in row


def test_idle_transaction_evidence_source_age_and_last_sql() -> None:
    value = report(
        [edge(52, 51, wait_type="LCK_M_X")],
        [
            {
                "session_id": 51,
                "status": "sleeping",
                "open_transaction_count": 1,
                "login_time": "2026-10-13T09:00:00",
                "host_name": "app",
                "login_name": "business",
                "program_name": "client",
            }
        ],
        [
            {
                "session_id": 51,
                "transaction_id": 7,
                "login_time": "2026-10-13T09:00:00",
                "transaction_state": 2,
                "transaction_type": 1,
                "transaction_age_seconds": 5862,
                "transaction_begin_time": "2026-10-13T10:00:00",
            }
        ],
        [{"session_id": 51, "client_net_address": "10.0.0.1", "last_sql": "UPDATE fixture"}],
    )
    root = value.details["roots"][0]
    assert root["transaction_age_seconds"] == 5862 and root["client_net_address"] == "10.0.0.1"
    assert root["transaction_id"] == 7 and root["transaction_identity_quality"] == "CONFIRMED"
    assert root["last_sql"] == ["UPDATE fixture"] and root["host_name"] == "app"
    finding = next(f for f in value.findings if f.title == "IDLE_TRANSACTION_BLOCKER")
    assert finding.severity.value == "CRITICAL" and finding.evidence["transactions"]
    commands = [c for f in value.findings for c in f.reference_commands]
    assert commands[0].text == "KILL 51;" and commands[0].manual_only
    assert not hasattr(commands[0], "execute") and not callable(commands[0])


@pytest.mark.parametrize("state", [None, 0, 1, 3, 6, 7, 8])
def test_idle_requires_unfinished_dmv_transaction(state: int | None) -> None:
    value = report(
        [edge(52, 51)],
        [{"session_id": 51, "status": "sleeping", "open_transaction_count": 1}],
        [{"session_id": 51, "transaction_state": state, "transaction_type": 1}],
    )
    assert "IDLE_TRANSACTION_BLOCKER" not in [f.title for f in value.findings]


def test_root_transaction_id_requires_unique_identity_and_same_login_time() -> None:
    session = {"session_id": 51, "status": "sleeping", "login_time": "2026-10-13T09:00:00",
               "open_transaction_count": 1, "login_name": "monitor", "host_name": "app",
               "program_name": "client"}
    tx = {"session_id": 51, "login_time": "2026-10-13T09:00:00", "transaction_id": 376008,
          "transaction_state": 2, "transaction_type": 1, "transaction_age_seconds": 174}
    root_report = report([edge(56, 51)], [session], [tx])
    root = root_report.details["roots"][0]
    assert root["transaction_id"] == 376008
    assert root["status"] == "sleeping" and root["transaction_identity_quality"] == "CONFIRMED"

    ambiguous = report([edge(56, 51)], [session], [tx, {**tx, "transaction_id": 376009}])
    assert ambiguous.details["roots"][0]["transaction_id"] is None
    assert ambiguous.details["roots"][0]["transaction_identity_quality"] == "AMBIGUOUS"
    assert ambiguous.details["roots"][0]["transaction_observed"] is True
    assert ambiguous.details["roots"][0]["transaction_identity_verified"] is False
    assert not any(f.reference_commands for f in ambiguous.findings)

    reused = report([edge(56, 51)], [session], [{**tx, "login_time": "2026-10-13T10:00:00"}])
    assert reused.details["roots"][0]["transaction_id"] is None
    assert reused.details["roots"][0]["transaction_identity_quality"] == "RECHECK_REQUIRED"
    assert reused.details["roots"][0]["transaction_observed"] is True
    assert reused.details["roots"][0]["transaction_identity_verified"] is False
    assert not any(f.reference_commands for f in reused.findings)


def test_root_reference_does_not_depend_on_optional_connection_address_collector() -> None:
    login_time = "2026-10-18T10:00:00"
    value = build_slow_report(INSTANCE, (
        sample("active_requests", [edge(56, 58, status="suspended")]),
        sample("active_sessions", [
            {"session_id": 58, "status": "sleeping", "login_time": login_time,
             "login_name": "app", "host_name": "worker", "program_name": "orders",
             "open_transaction_count": 1},
            {"session_id": 56, "status": "suspended", "login_time": login_time,
             "login_name": "app", "host_name": "worker", "program_name": "orders"},
        ]),
        sample("transactions", [{"session_id": 58, "login_time": login_time,
                                  "transaction_id": 376008, "transaction_state": 2,
                                  "transaction_type": 1, "transaction_age_seconds": 174}]),
    ), SQLServerSettings())
    root = next(finding for finding in value.findings if finding.title == "ROOT_BLOCKER")
    assert [command.text for command in root.reference_commands] == ["KILL 58;"]
    assert root.evidence["manual_action"]["status"] == "AVAILABLE"


def test_sqlserver_blocking_chain_keeps_root_transaction_separate_from_victims() -> None:
    sessions = [{"session_id": sid, "status": "sleeping" if sid == 58 else "suspended",
                 "login_time": f"2026-10-18T13:{sid}:00", "login_name": f"app{sid}",
                 "host_name": "simulator", "program_name": "incident-test",
                 "open_transaction_count": 2 if sid != 58 else 1}
                for sid in (58, 56, 59)]
    transactions = [{"session_id": sid, "login_time": f"2026-10-18T13:{sid}:00",
                     "transaction_id": txid, "transaction_state": 2, "transaction_type": 1,
                     "transaction_age_seconds": 174}
                    for sid, txid in ((58, 376008), (56, 376018), (59, 376027))]
    value = report([edge(56, 58, wait_type="LCK_M_X", wait_time_ms=173000),
                    edge(59, 56, wait_type="LCK_M_X", wait_time_ms=173000)],
                   sessions, transactions)
    root = value.details["roots"][0]
    assert root["session_id"] == 58
    assert root["transaction_id"] == 376008
    assert root["transaction_identity_quality"] == "CONFIRMED"
    children = value.details["blocking_graph"]["children"]
    assert root["blocked_session_count"] == 2 and len(children.get("58", children.get(58, []))) == 1
    assert {row["transaction_id"] for row in value.details["transactions"]} == {
        376008, 376018, 376027}


def test_missing_root_ip_sql_and_legacy_fields() -> None:
    value = report([edge(52, 51)])
    assert value.details["roots"][0]["status"] is None
    assert not any(f.reference_commands for f in value.findings)
    value = report([edge(52, 51)], [{"session_id": 51, "status": "sleeping"}])
    assert value.details["roots"][0]["last_sql"] == []
    assert value.details["roots"][0]["client_net_address"] is None
    assert "unavailable" in render_console(value)
    assert not any(f.title == "IDLE_TRANSACTION_BLOCKER" for f in value.findings)


def test_partial_snapshot_and_sensitive_report() -> None:
    results = (
        sample("active_requests", [edge(52, 51)], truncated=True),
        sample("active_sessions", [{"session_id": 51, "host_name": "password=private"}]),
        sample("transactions", [], success=False, error_type="PermissionDenied"),
    )
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    assert value.details["incomplete"]
    assert not any(f.reference_commands for f in value.findings)
    markdown = render_markdown(value)
    assert "private" not in markdown and "truncated" in markdown
    for title in (
        "Summary",
        "Concurrency",
        "Long Running SQL",
        "Blocking Tree",
        "Root Blocker Details",
        "Transaction Details",
        "Session Source",
        "Findings",
        "Recommendations",
        "Reference Commands",
    ):
        assert f"## {title}" in markdown
    assert "Toolkit 未执行任何 KILL 操作" in markdown


def test_analyzer_dispatch_and_unsupported() -> None:
    assert SlowAnalyzer().analyze(INSTANCE, ())
    with pytest.raises(ToolkitError, match="Not implemented"):
        SlowAnalyzer().analyze(replace(INSTANCE, db_type=DatabaseType.MYSQL), ())
    assert not hasattr(ReferenceCommand("KILL 1;", "manual"), "execute")


def test_optional_failure_keeps_snapshot_and_closes() -> None:
    driver = FakeDriver()
    driver.connection.failures["transactions"] = RuntimeError("42000", "password=private")
    with SQLServerAdapter(
        INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"), driver=driver
    ) as adapter:
        value = diagnose_slow(adapter)
    assert value.details["incomplete"] and driver.connection.closed
    assert "connections" not in driver.connection.executed
    assert "private" not in render_console(value)


def test_active_root_does_not_claim_idle_transaction() -> None:
    value = report(
        [edge(52, 51), edge(51, 0, sql_text="SELECT 1")],
        [{"session_id": 51, "status": "running", "open_transaction_count": 1}],
        [{"session_id": 51, "transaction_state": 2, "transaction_type": 1}],
    )
    assert any(f.title == "ROOT_BLOCKER" for f in value.findings)
    assert not any(f.title == "IDLE_TRANSACTION_BLOCKER" for f in value.findings)


def test_truncated_requests_do_not_claim_idle_or_offer_kill() -> None:
    results = (
        sample("active_requests", [edge(52, 51)], truncated=True),
        sample(
            "active_sessions",
            [{"session_id": 51, "status": "sleeping", "open_transaction_count": 1}],
        ),
        sample("transactions", [{"session_id": 51, "transaction_state": 2, "transaction_type": 1}]),
        sample("connections", []),
    )
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    assert not any(
        f.title == "IDLE_TRANSACTION_BLOCKER" or f.reference_commands for f in value.findings
    )


@pytest.mark.parametrize(("raw", "shown"), [
    ("runnable", "可运行"), ("running", "运行中"), ("suspended", "挂起"), ("sleeping", "空闲"),
    ("RUNNABLE", "可运行"), ("Database", "Database"), ("future<state>", "future<state>"),
])
def test_sqlserver_request_status_is_localized_only_in_html(raw: str, shown: str) -> None:
    value = report([edge(55, 51, request_id=0, status=raw, elapsed_ms=100, sql_text="SELECT 1")],
                   [dict(session_id=51, status=raw)])
    before = render_json(asdict(value))
    page = HtmlReportRenderer().render(build_report_model(value, build_incident_summary(value, "slow.html")))
    assert f"<dt>执行状态</dt><dd>{escape(shown)}</dd>" in page
    details = json.loads(before)["details"]
    assert details["all_requests"][0]["status"] == details["top_requests"][0]["status"] == raw
    assert render_json(asdict(value)) == before


@pytest.mark.parametrize(("timings", "shown"), [
    ({"report_write_ms": None, "report_write_duration_ms": 0}, "0 ms"),
    ({"report_write_ms": 8}, "8 ms"),
    ({"report_write_ms": None}, "未测量"),
    ({"report_write_ms": 8, "report_write_duration_ms": None}, "未测量"),
])
def test_historical_report_timing_values_are_read_without_inventing_a_duration(
    timings: dict[str, int | None], shown: str,
) -> None:
    value = report([])
    value.details["diagnostic_timings_ms"] = timings
    page = HtmlReportRenderer().render(build_report_model(value, build_incident_summary(value, "slow.html")))
    assert f"<dt>HTML 文件写入耗时</dt><dd>{shown}</dd>" in page
    assert "<dt>诊断至 HTML 写入总耗时</dt><dd>未测量</dd>" in page


def test_diagnostic_collection_timing_separates_setup_and_assembly_from_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "perf_counter", lambda: 2.0)
    timings = cli._diagnostic_collection_timings(0.0, 1.0, 1.5, 1.2)
    assert timings == {"time_to_first_summary_ms": 1200, "collection_ms": 500, "full_collection_ms": 2000,
                       "report_write_ms": None, "report_write_duration_ms": None, "total_report_ms": None}


def test_timed_diagnostic_html_reports_measured_submillisecond_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "report.html"
    placeholders = {"report_write_duration_ms": "WRITE_MS_PLACEHOLDER", "total_report_ms": "TOTAL_MS_PLACEHOLDER"}
    ticks = iter((2.0, 2.0004))
    monkeypatch.setattr(cli, "perf_counter", lambda: next(ticks))
    values = cli._write_timed_diagnostic_html(path, "诊断 <dd>WRITE_MS_PLACEHOLDER</dd><dd>TOTAL_MS_PLACEHOLDER</dd>",
                                              0.0, placeholders)
    assert values == {"report_write_ms": 1, "report_write_duration_ms": 1, "total_report_ms": 2000}
    assert re.fullmatch(r"诊断 <dd>1 ms +</dd><dd>2000 ms +</dd>", path.read_text(encoding="utf-8"))


def test_timed_diagnostic_html_flush_failure_removes_incomplete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "report.html"
    original_open = Path.open

    @contextmanager
    def fail_flush(target: Path, mode: str) -> Iterator[Mock]:
        assert target == path and mode == "xb"
        with original_open(target, mode) as stream:
            writer = Mock(wraps=stream)
            writer.flush.side_effect = OSError("synthetic write failure")
            yield writer

    monkeypatch.setattr(Path, "open", fail_flush)
    with pytest.raises(OSError, match="synthetic write failure"):
        cli._write_timed_diagnostic_html(path, "diagnostic report", 0.0, {})
    assert not path.exists()


@pytest.mark.parametrize("active_wait", [False, True])
def test_cli_diagnose(tmp_path: Path, capsys: pytest.CaptureFixture[str], active_wait: bool) -> None:
    driver = FakeDriver()
    driver.connection.results.update(transactions=[], connections=[])
    if active_wait:
        driver.connection.results.update(
            active_requests=[_waitfor_request()],
            active_sessions=[{"session_id": 51, "open_transaction_count": 0, "login_name": "fixture-user"}],
        )
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {
                        "id": "test",
                        "name": "Test",
                        "db_type": "sqlserver",
                        "host": "test.invalid",
                        "port": 1433,
                        "credential_alias": "monitor",
                        "version_family": "2022",
                    }
                ]
            }
        )
    )
    output = tmp_path / "slow.html"
    args = [
        "--inventory",
        str(inventory),
        "--sql-root",
        str(ROOT / "sql"),
        "diagnose",
        "test",
        "slow",
        "--report",
        str(output),
        "--format",
        "json",
    ]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] and output.exists() and driver.connection.closed
    assert payload["incident_summary"]["data_completeness"] == "COMPLETE"
    assert payload["incident_summary"]["report_path"] == str(output)
    html = output.read_text(encoding="utf-8")
    assert "<span>诊断总览</span>" in html and "<span>阻塞分析</span>" in html and "阻塞关系图" in html
    timings = payload["diagnostic_timings_ms"]
    assert payload["report"]["details"]["diagnostic_timings_ms"] == timings
    assert timings["time_to_first_summary_ms"] is None
    assert timings["collection_ms"] <= timings["full_collection_ms"] <= timings["total_report_ms"]
    assert timings["report_write_duration_ms"] == timings["report_write_ms"] >= 1
    assert timings["report_write_duration_ms"] <= timings["total_report_ms"] - timings["full_collection_ms"] + 1
    for label, key in (("采集阶段耗时", "collection_ms"), ("初始化至诊断组装耗时", "full_collection_ms"),
                       ("HTML 文件写入耗时", "report_write_duration_ms"),
                       ("诊断至 HTML 写入总耗时", "total_report_ms")):
        assert re.search(rf"<dt>{label}</dt><dd>{timings[key]} ms\s*</dd>", html)
    assert "<dt>首次终端摘要耗时</dt><dd>未测量</dd>" in html
    if active_wait:
        assert payload["report"]["findings"] == []
        assert payload["report"]["details"]["performance_triage"]["assessments"] == []
        assert payload["incident_summary"]["status"] == "HEALTHY"
        assert payload["incident_summary"]["severity"] == "INFO"
        assert "WAITFOR 主动等待" in html and "fixture-user" in html
    assert set(driver.connection.executed) <= {
        "monitor_capabilities",
        "monitor_permissions",
        "version_detect",
        "server_info",
        "database_status",
        "active_sessions",
        "active_requests",
        "transactions",
        "connections",
        "lock_resources",
    }
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    assert output.read_text(encoding="utf-8") == html
    failure = json.loads(capsys.readouterr().out)
    assert failure["error_message"] == "File could not be read"
    assert "diagnostic_timings_ms" not in failure and "report_written_at" not in failure
    console_path = tmp_path / "slow-console.html"
    human_args = args[:-2]
    human_args[human_args.index(str(output))] = str(console_path)
    console_driver = FakeDriver()
    console_driver.connection.results.update(transactions=[], connections=[])
    assert main(human_args, provider=MemoryCredentialProvider(), driver=console_driver) == 0
    human_output = capsys.readouterr().out
    assert "数据库性能应急诊断" in human_output and "详细中文报告已写入" in human_output
    assert "Current SQL" not in human_output and "Blocking Tree" not in human_output
    for option in ("--execute", "--force"):
        with pytest.raises(SystemExit) as exc:
            main([*args, option])
        assert exc.value.code == 2


def test_cli_unsupported_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {
                        "id": "other",
                        "name": "Other",
                        "db_type": "redis",
                        "host": "test.invalid",
                        "port": 3306,
                        "credential_alias": "monitor",
                    }
                ]
            }
        )
    )
    result = main(["--inventory", str(inventory), "diagnose", "other", "slow", "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["error_type"] == "NotImplemented" and "尚未实现" in payload["error_message"]
    )


@pytest.mark.parametrize("key", ["slow_top_n", "high_concurrency_threshold"])
@pytest.mark.parametrize("value", [0, -1, True, "10"])
def test_threshold_validation(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        SQLServerSettings(**{key: value})
