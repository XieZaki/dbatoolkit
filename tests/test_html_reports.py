from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dba.cli import _diagnostic_report_path
from dba.core import DatabaseInstance, DatabaseType, Finding, ReferenceCommand, Severity
from dba.incident import build_incident_summary
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.presenters import _local_time
from dba.slow import SlowReport
from dba.top_sql_report import TopSQLDiagnosticReport


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
             "elapsed_ms": 62000, "wait_type": "WAITFOR", "cpu_ms": 0,
             "logical_reads": 0, "reads": 0, "writes": 0,
             "sql_text": "WAITFOR DELAY '00:02:00'"},
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
             "elapsed_ms": 62000, "wait_type": "WAITFOR", "cpu_ms": 0,
             "logical_reads": 0, "reads": 0, "writes": 0,
             "sql_text": "WAITFOR DELAY '00:02:00'"},
        ],
        "samples": [{"collector": name, "success": True, "truncated": False,
                     "row_count": 1} for name in
                    ("active_sessions", "active_requests", "blocking", "transactions", "connections")],
    }
    return SlowReport(instance, datetime(2026, 10, 17, tzinfo=timezone.utc), "slow", findings, details)


def _top_sql_payload(database_type: DatabaseType = DatabaseType.MYSQL, *,
                     completeness: str = "PARTIAL", visibility: str = "PARTIAL",
                     association_status: str = "UNMATCHED") -> dict[str, object]:
    engine = database_type.value
    identity_fields: dict[str, object] = ({"digest": "digest-001", "schema_name": "app"}
        if database_type is DatabaseType.MYSQL else
        {"queryid": 8107, "dbid": 16384, "userid": 10, "toplevel": True})
    identity = {"database_engine": engine, "kind": "STATEMENT", "fields": identity_fields}
    return {
        "database_engine": engine,
        "database_version": "16.15 (Debian 16.15-1)" if database_type is DatabaseType.POSTGRESQL else "8.4.11",
        "collection_status": "COLLECTED", "completeness": completeness,
        "data_visibility": visibility, "profile_id": f"{engine}_top_sql",
        "collection_timestamp": "2026-11-04T23:58:13+00:00",
        "ranking_metric": "total_exec_time" if database_type is DatabaseType.POSTGRESQL else "sum_timer_wait",
        "ranking_direction": "DESCENDING", "metric_name": "total_exec_time" if database_type is DatabaseType.POSTGRESQL else "sum_timer_wait",
        "metric_unit": "milliseconds" if database_type is DatabaseType.POSTGRESQL else "picoseconds",
        "metric_semantics": "CUMULATIVE", "metric_scope": "SQL_STATEMENT",
        "collection_scope": "INSTANCE", "evidence_source": "pg_stat_statements" if database_type is DatabaseType.POSTGRESQL else "performance_schema.events_statements_summary_by_digest",
        "limitations": ["COUNTER_EPOCH_UNKNOWN", "SOURCE_COVERAGE_INCOMPLETE"]
        if completeness == "PARTIAL" else [],
        "source_metadata": {"statement_instrument_count": 214,
                            "statement_instrument_disabled_count": 15,
                            "statement_instrument_untimed_count": 15}
        if database_type is DatabaseType.MYSQL else {},
        "items": [{"rank": 1, "metric_value": 9876, "sql_identity": identity,
                   "sql_text_association": {"status": association_status,
                                            "sql_text": "MUST_NOT_BE_SHOWN"}}],
    }


def _top_sql_report(database_type: DatabaseType = DatabaseType.MYSQL, *,
                    completeness: str = "PARTIAL", visibility: str = "PARTIAL") -> TopSQLDiagnosticReport:
    instance = DatabaseInstance("synthetic-top-sql", "Synthetic Top SQL", database_type,
                                "localhost", 1, "synthetic-monitor", environment="test")
    details: dict[str, object] = {"version": "160015" if database_type is DatabaseType.POSTGRESQL else "8.4.11",
                                  "top_sql": _top_sql_payload(database_type,
                                      completeness=completeness, visibility=visibility)}
    return TopSQLDiagnosticReport(instance, datetime(2026, 11, 4, tzinfo=timezone.utc),
                                  "top-sql", (), details)


class _HTMLReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        identifier = values.get("id")
        if identifier:
            self.ids.append(identifier)
        href = values.get("href")
        if href and href.startswith("#"):
            self.fragments.append(href[1:])


def test_html_slow_report_formats_incident_evidence_and_escapes_database_text() -> None:
    report = _report()
    summary = build_incident_summary(report, "reports/sqlserver2022_slow.html")
    page = HtmlReportRenderer().render(build_report_model(report, summary))
    assert '<html lang="zh-CN">' in page
    assert page.index('id="summary"') < page.index('id="root-assessment"')
    assert page.index('id="root-assessment"') < page.index('id="blocking-chain"')
    assert page.index('id="blocking-chain"') < page.index('id="manual-action"')
    assert "锁与阻塞链" in page and "会话身份与来源" in page
    assert "长期未提交事务" in page and "请求运行信息" in page
    assert "59" in page and "67" in page
    assert "长运行请求 3 条" in page and "阻塞相关 2 条" in page and "非阻塞 1 条" in page
    assert 'class="report-section manual-action"' in page
    assert "Toolkit 不执行 KILL" in page
    assert "KILL 57;" in page and "Toolkit 是否执行" in page and "回滚风险" in page
    assert "长时间主动等待（WAITFOR）" in page and "LCK_M_X" in page
    assert "直接阻塞会话" in page and "间接受影响会话" in page
    assert "共" in page and "省略" in page
    command_index = page.index("KILL 57;")
    assert "<details" in page[:command_index] and "执行前必须重新核对会话身份" in page
    assert "00:01:17" in page
    assert "<img src=x" not in page
    assert "<script>alert(1)</script>" not in page
    assert 'data-copy="UPDATE t SET value = &#x27;&lt;script&gt;alert(1)' in page
    assert '<details' in page and "会话 59 SQL" in page
    assert '{"session_id"' not in page and "{'session_id'" not in page
    assert "https://" not in page and "http://" not in page
    assert '<script src=' not in page and "<script>alert(1)</script>" not in page
    assert "<script>" in page  # Inline interaction code is required by the approved offline template.


def test_slow_template_is_model_driven_and_contains_no_approved_template_demo_values() -> None:
    report = _report()
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/report.html")))

    assert 'id="summary"' in page and 'id="root-assessment"' in page
    assert 'id="blocking-chain"' in page and 'id="manual-action"' in page
    assert 'id="collection-quality"' in page and 'id="session-appendix"' in page
    assert "SQL &lt;Server&gt;" in page and "16.0.4305.5" in page
    assert "mysql-test-01" not in page and "APP-SRV-01" not in page
    assert "TRX-88421" not in page and "2026-11-01 20:31:02" not in page
    assert "10.0.1.25" not in page and "OrderService" not in page
    assert '<link rel="stylesheet" href="http' not in page
    assert '<script src="http' not in page


def test_slow_report_colors_sessions_from_blocking_evidence_not_runtime_guessing() -> None:
    report = _report()
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/report.html")))

    assert 'data-diagnostic-state="blocked"' in page
    assert 'data-diagnostic-state="clear"' in page
    assert "已确认参与阻塞" in page
    assert "完整采样确认未参与阻塞" in page
    assert 'data-session-search="true"' in page
    assert "MySQL PROCESSLIST_ID" not in page

    partial = _report()
    partial.details["blocking_graph"] = {"roots": [], "children": {}, "anomalies": []}
    partial.details["samples"] = [
        {**sample, "success": False} if sample["collector"] == "active_requests" else sample
        for sample in partial.details["samples"]]
    partial_page = HtmlReportRenderer().render(build_report_model(
        partial, build_incident_summary(partial, "reports/report.html")))
    assert 'data-diagnostic-state="partial"' in partial_page
    assert "阻塞状态未能确认" in partial_page


def test_slow_template_is_self_contained_responsive_and_keyboard_operable() -> None:
    template_path = Path(__file__).parents[1] / "dba" / "reporting" / "templates" / "slow_report.html"
    template = template_path.read_text(encoding="utf-8")
    root_tokens = re.search(r":root\s*\{[^}]*\}", template, re.DOTALL)
    assert root_tokens is not None
    remaining_css = template.replace(root_tokens.group(0), "", 1)

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", remaining_css)
    assert "overflow-x:clip" in template
    assert "100vw" not in template
    assert "max-width:1120px" in template
    assert "max-width:850px" in template
    assert "max-width:560px" in template
    assert ":focus-visible" in template and ":active" in template and ":disabled" in template
    assert "aria-busy" in template and ".toast.success" in template and ".toast.error" in template
    assert "prefers-reduced-motion:reduce" in template
    assert not re.search(r"<(?:script|link|img)\b[^>]+(?:src|href)=\"https?://", template, re.IGNORECASE)


def test_slow_hero_status_is_green_only_for_complete_healthy_evidence() -> None:
    report = _report()
    base = build_report_model(report, build_incident_summary(report, "reports/report.html"))
    renderer = HtmlReportRenderer()

    healthy = replace(base, incident_summary=replace(
        base.incident_summary, status="HEALTHY", severity="INFO", data_completeness="COMPLETE"))
    assert 'status-pill clear' in renderer.render(healthy)

    unknown = replace(base, incident_summary=replace(
        base.incident_summary, status="UNKNOWN", severity="INFO", data_completeness="COMPLETE"))
    assert 'status-pill unknown' in renderer.render(unknown)

    incomplete = replace(base, incident_summary=replace(
        base.incident_summary, status="HEALTHY", severity="INFO", data_completeness="PARTIAL"))
    assert 'status-pill caution' in renderer.render(incomplete)


def test_slow_report_renders_multiple_roots_and_unbounded_depth_as_direct_edges() -> None:
    report = _report()
    report.details["blocking_graph"] = {
        "roots": [57, 70],
        "children": {57: [59], 59: [67], 67: [71], 71: [72], 70: [73]},
        "anomalies": [],
    }
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/report.html")))

    assert "70" in page and "72" in page and "73" in page
    assert "Direct Blocker" not in page
    assert "直接阻塞者" in page
    assert "71" in page and "72" in page


@pytest.mark.parametrize("database_type,session,extra_headers,extra_values", [
    (DatabaseType.MYSQL, {"processlist_id": 36, "thread_id": 8036},
     ("MySQL PROCESSLIST_ID", "Performance Schema THREAD_ID"), ("36", "8036")),
    (DatabaseType.POSTGRESQL, {"pid": 236}, ("PostgreSQL PID",), ("236",)),
    (DatabaseType.SQLSERVER, {"session_id": 57}, ("会话",), ("57",)),
    (DatabaseType.ORACLE, {"inst_id": 2, "sid": 57, "serial_number": 901},
     ("Oracle INST_ID", "Oracle SID", "Oracle SERIAL#"), ("2", "57", "901")),
])
def test_slow_html_preserves_native_session_identity_by_database(
        database_type: DatabaseType, session: dict[str, int],
        extra_headers: tuple[str, ...], extra_values: tuple[str, ...]) -> None:
    report = _report()
    report = replace(report, instance=DatabaseInstance(
        "synthetic", "Synthetic", database_type, "localhost", 1, "monitor", environment="test"))
    identity = {**session, "login_name": "monitor-user", "host_name": "workload-host",
                "database_name": "TestDb", "status": "sleeping"}
    request = {**session, "status": "suspended", "sql_text": "SELECT synthetic_current_sql",
               "blocking_session_id": None}
    sid = f"{session['inst_id']}:{session['sid']}" if database_type == DatabaseType.ORACLE else str(
        next(iter(session.values())))
    child = f"{session['inst_id']}:58" if database_type == DatabaseType.ORACLE else str(int(sid) + 1)
    report.details.update({
        "all_sessions": [identity, {**session, **({"sid": 58} if database_type == DatabaseType.ORACLE else
                                                  {"pid": int(sid) + 1} if database_type == DatabaseType.POSTGRESQL else
                                                  {"processlist_id": int(sid) + 1, "thread_id": 8037}
                                                  if database_type == DatabaseType.MYSQL else
                                                  {"session_id": int(sid) + 1})}],
        "all_requests": [request],
        "blocking_graph": {"roots": [sid], "children": {sid: [child]}, "anomalies": []},
        "samples": [{"collector": name, "success": True, "truncated": False, "row_count": 1}
                    for name in ("active_sessions", "active_requests", "blocking", "transactions", "connections")],
    })
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/report.html")))
    for value in (*extra_headers, *extra_values):
        assert value in page
    assert "synthetic_current_sql" in page


def test_mysql_recent_statement_evidence_is_escaped_and_collapsed() -> None:
    report = _report()
    report = replace(report, instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "monitor", environment="test"))
    report.details["recent_statements"] = [{
        "session_id": 36, "evidence_status": "AVAILABLE", "statements": [{
            "session_id": 36, "event_id": 105, "duration_ms": 4,
            "source": "performance_schema.events_statements_history",
            "evidence_status": "AVAILABLE", "sql_text_redacted": "SELECT '&lt;script&gt;'<script>alert(1)</script>",
            "truncated": False,
        }],
    }]
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/mysql.html")))
    assert "最近执行 SQL" in page
    assert "不保证属于当前事务" in page and "不保证是持锁 SQL" in page
    assert "performance_schema.events_statements_history" in page
    assert "<details" in page and "连接 36 事件 105" in page
    assert "<script>alert(1)</script>" not in page


def test_mysql_recent_statement_html_keeps_1000_character_limit_independent_of_cli() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "monitor", environment="test"))
    sql = "SELECT " + "x" * 1200
    report.details["recent_statements"] = [{
        "session_id": 36, "evidence_status": "AVAILABLE", "statements": [{
            "session_id": 36, "event_id": 105, "duration_ms": 4,
            "source": "performance_schema.events_statements_history",
            "evidence_status": "AVAILABLE", "sql_text_redacted": sql,
            "truncated": False,
        }],
    }]
    report.details["recent_statements_elapsed_ms"] = 0.02
    model = build_report_model(report, build_incident_summary(report, "reports/mysql.html"))
    section = next(item for item in model.sections if item.section_id == "mysql-recent-statements")
    assert len(section.code_blocks[0].content) > 1000
    assert section.code_blocks[0].content.startswith(sql[:1000])
    assert "omitted by 1000 character report limit" in section.code_blocks[0].content
    assert ("采集耗时（毫秒）", "<0.1") in section.fields
    page = HtmlReportRenderer().render(model)
    assert "SQL 文本截断" in page and "<details" in page


def test_human_report_tables_have_fixed_columns_and_hide_unrelated_session_fields() -> None:
    report = _report()
    report.details["all_sessions"].append({"session_id": 999, "status": "sleeping",
                                           "secret_internal_field": "must not appear"})
    page = HtmlReportRenderer().render(build_report_model(report,
        build_incident_summary(report, "reports/report.html")))
    assert "secret internal field" not in page
    assert "must not appear" not in page
    assert "会话身份与来源" in page and "请求运行信息" in page
    assert "Open Txn" not in page or "Open Transaction" in page
    assert "其他会话（附录）" in page


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
    assert "省略" in page
    assert "人工处置参考" in page and "根因候选" in page
    assert "长运行请求 303 条 · 阻塞相关 302 条 · 非阻塞 1 条" in page
    assert len(page.encode("utf-8")) < policy.hard_report_bytes
    assert page.count("<tr>") < 100


def test_sql_server_naive_times_remain_unzoned_and_aware_times_convert() -> None:
    naive = datetime(2026, 10, 18, 13, 29, 56)
    assert _local_time(naive).endswith("(时区未知)")
    assert "13:29:56" in _local_time("2026-10-18T13:29:56")

    utc_value = datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)
    assert _local_time(utc_value) == utc_value.astimezone().isoformat(sep=" ", timespec="seconds")
    assert utc_value.astimezone(ZoneInfo("Asia/Shanghai")).day == 19

    dst_value = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)
    assert _local_time(dst_value) == dst_value.astimezone().isoformat(sep=" ", timespec="seconds")


def test_hard_report_size_fallback_keeps_incident_and_recovery() -> None:
    from dba.reporting import HumanReportPolicy, render_bounded

    report = _report()
    report.details["all_sessions"] *= 100
    summary = build_incident_summary(report, "reports/report.html")
    policy = HumanReportPolicy(soft_report_bytes=4096, hard_report_bytes=16 * 1024)
    page = render_bounded(build_report_model(report, summary, policy=policy),
                          HtmlReportRenderer().render, policy)
    assert "故障概况" in page
    assert "人工处置参考" in page
    assert "根因候选" in page
    assert "证据完整性" in page
    assert "报告达到大小上限，已收敛部分证据内容" in page
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
    assert "SQL 文本因人工报告长度上限而省略" in page
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
    assert "人工处置参考" in page and "根因候选" in page
    assert "省略" in page
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


def test_mysql_top_sql_quality_distinguishes_collection_from_partial_completeness() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "synthetic-monitor", environment="test"))
    report.details["top_sql"] = _top_sql_payload()
    model = build_report_model(report, build_incident_summary(report))
    quality = next(section for section in model.sections if section.section_id == "collection-quality")
    rows = {row[0]: row for row in quality.tables[0].rows}

    assert rows["mysql_top_sql"][1] == "PARTIAL"
    assert "采集状态=COLLECTED" in rows["mysql_top_sql"][2]
    assert "证据完整性=PARTIAL" in rows["mysql_top_sql"][2]
    assert "COUNTER_EPOCH_UNKNOWN" in rows["mysql_top_sql"][2]
    assert "SOURCE_COVERAGE_INCOMPLETE" in rows["mysql_top_sql"][2]
    page = HtmlReportRenderer().render(model)
    assert "采集状态=已采集" in page and "证据完整性=部分可用" in page
    top_sql = next(section for section in model.sections if section.section_id == "top-sql")
    top_sql_fields = dict(top_sql.fields)
    assert top_sql_fields["Database Engine"] == "MySQL"
    source_metadata = top_sql_fields["Source Metadata"]
    assert "Statement Instrument Count: 214" in source_metadata
    assert "Statement Instrument Disabled Count: 15" in source_metadata
    assert "Statement Instrument Untimed Count: 15" in source_metadata
    assert model.incident_summary.data_completeness == "COMPLETE"


@pytest.mark.parametrize(("completeness", "visibility", "expected"), [
    ("COMPLETE", "COMPLETE", "完整"),
    ("UNKNOWN", "UNKNOWN", "未知"),
    ("COMPLETE", "UNKNOWN", "未知"),
])
def test_top_sql_quality_never_promotes_unknown_to_complete(
        completeness: str, visibility: str, expected: str) -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "synthetic-monitor", environment="test"))
    report.details["top_sql"] = _top_sql_payload(
        completeness=completeness, visibility=visibility)
    model = build_report_model(report, build_incident_summary(report))
    quality = next(section for section in model.sections if section.section_id == "collection-quality")
    row = next(row for row in quality.tables[0].rows if row[0] == "mysql_top_sql")
    assert row[1] == expected


def test_top_sql_completeness_does_not_raise_incomplete_slow_core() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "synthetic-monitor", environment="test"))
    report.details["incomplete"] = True
    report.details["samples"] = [
        {**sample, "success": False} if sample["collector"] == "active_requests" else sample
        for sample in report.details["samples"]]
    report.details["top_sql"] = _top_sql_payload(completeness="COMPLETE", visibility="COMPLETE")
    model = build_report_model(report, build_incident_summary(report))
    quality = next(section for section in model.sections if section.section_id == "collection-quality")
    row = next(row for row in quality.tables[0].rows if row[0] == "mysql_top_sql")
    assert row[1] == "完整"
    assert model.incident_summary.data_completeness != "COMPLETE"


def test_mysql_recent_sql_empty_and_failed_states_are_distinct() -> None:
    empty = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "synthetic-monitor", environment="test"))
    empty.details["recent_statements"] = [{
        "status": "NOT_APPLICABLE",
        "reason": "No confirmed root blocker or idle transaction candidate.",
    }]
    empty_model = build_report_model(empty, build_incident_summary(empty))
    empty_section = next(section for section in empty_model.sections
                         if section.section_id == "mysql-recent-statements")
    empty_text = " ".join(value for _, value in empty_section.fields)
    assert "当前快照未发现可展示的最近执行 SQL" in empty_text
    assert "连接 不可用 状态" not in empty_text
    assert "连接不可用" not in empty_text

    empty_result = replace(_report(), instance=empty.instance)
    empty_result.details["samples"] = [*empty_result.details["samples"], {
        "collector": "recent_statements", "success": True, "truncated": False, "row_count": 0,
    }]
    empty_result.details["recent_statements"] = []
    empty_result_model = build_report_model(empty_result, build_incident_summary(empty_result))
    empty_result_section = next(section for section in empty_result_model.sections
                                if section.section_id == "mysql-recent-statements")
    assert dict(empty_result_section.fields)["采集状态"] == "COLLECTED"
    assert "当前快照未发现可展示的最近执行 SQL" in dict(empty_result_section.fields)["最近执行 SQL"]

    failed = replace(_report(), instance=empty.instance)
    failed.details["samples"] = [*failed.details["samples"], {
        "collector": "recent_statements", "success": False,
        "error_type": "QueryFailed", "error_message": "statement history collection failed",
    }]
    failed.details["recent_statements"] = [{
        "session_id": 36, "evidence_status": "QUERY_FAILED", "statements": [],
        "reason": "Recent statement history query failed; see sanitized collection evidence.",
    }]
    failed_model = build_report_model(failed, build_incident_summary(failed))
    failed_section = next(section for section in failed_model.sections
                          if section.section_id == "mysql-recent-statements")
    failed_fields = dict(failed_section.fields)
    assert failed_fields["采集状态"] == "FAILED"
    assert failed_fields["连接 36 最近 SQL 状态"] == "QUERY_FAILED"
    assert "当前快照未发现可展示的最近执行 SQL" not in " ".join(failed_fields.values())


@pytest.mark.parametrize(("value", "expected"), [
    (160015, "16.15"), ("160015", "16.15"), (100000, "10.0"),
    (100001, "10.1"), (90624, "9.6.24"), ("90624", "9.6.24"),
    ("9.6.24", "9.6.24"), ("16.15 (Debian 16.15-1)", "16.15"),
])
def test_postgresql_version_display_supports_legacy_and_modern_formats(
        value: object, expected: str) -> None:
    from dba.reporting.normalizers import postgresql_version_label

    assert postgresql_version_label(value) == expected


def test_postgresql_slow_version_is_readable_and_retains_raw_server_version_num() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "pg-test", "PostgreSQL", DatabaseType.POSTGRESQL, "localhost", 5432,
        "synthetic-monitor", environment="test"))
    report.details.update(version="160015", server_version_num=160015)
    model = build_report_model(report, build_incident_summary(report))
    metadata = dict(model.metadata)
    assert metadata["Version"] == "16.15"
    assert metadata["server_version_num (raw)"] == "160015"
    page = HtmlReportRenderer().render(model)
    assert "版本" in page and "16.15" in page and "160015" in page


@pytest.mark.parametrize("database_type", [DatabaseType.MYSQL, DatabaseType.POSTGRESQL])
def test_slow_html_ids_are_unique_and_navigation_fragments_resolve(database_type: DatabaseType) -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "synthetic", "Synthetic", database_type, "localhost", 1,
        "synthetic-monitor", environment="test"))
    page = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/synthetic.html")))
    parser = _HTMLReferences()
    parser.feed(page)

    assert len(parser.ids) == len(set(parser.ids))
    assert parser.ids.count("collection-quality") == 1
    assert "collection-quality-panel" in parser.ids
    assert set(parser.fragments).issubset(set(parser.ids))
    assert 'href="#collection-quality"' in page


def test_independent_top_sql_report_uses_statistics_summary_and_keeps_native_identity() -> None:
    report = _top_sql_report(DatabaseType.POSTGRESQL, completeness="PARTIAL", visibility="PARTIAL")
    page = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))

    for expected in ("数据库类型", "PostgreSQL", "数据库版本", "16.15", "采集状态",
                     "证据完整性", "部分可用", "返回记录数", "排序指标", "指标单位",
                     "累计统计", "统计范围", "数据来源", "采集时间", "queryid=8107"):
        assert expected in page
    for inappropriate in ("严重程度", "主要问题", "当前观测", "采集开始", "初报完成", "N/A"):
        assert inappropriate not in page
    assert "MUST_NOT_BE_SHOWN" not in page
    assert "http://" not in page and "https://" not in page

    complete_report = _top_sql_report(DatabaseType.MYSQL, completeness="COMPLETE", visibility="COMPLETE")
    complete_page = HtmlReportRenderer().render(build_report_model(
        complete_report, build_incident_summary(complete_report)))
    assert "证据完整性" in complete_page and "完整" in complete_page
    assert "部分可用" not in complete_page


def test_mysql_wait_detail_unavailable_does_not_mark_active_requests_failed() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "mysql-test", "MySQL", DatabaseType.MYSQL, "localhost", 3306,
        "synthetic-monitor", environment="test"))
    report.details["wait_detail_status"] = "UNAVAILABLE"
    report.details["wait_detail_reason"] = (
        "events_waits_current consumer is disabled; fallback intentionally projects NULL.")
    model = build_report_model(report, build_incident_summary(report))
    quality = next(section for section in model.sections if section.section_id == "collection-quality")
    rows = {row[0]: row for row in quality.tables[0].rows}
    assert rows["active_requests"][1] == "完整"
    assert rows["current_wait_detail"][1] == "UNAVAILABLE"
    assert "intentionally projects NULL" in rows["current_wait_detail"][2]


def test_postgresql_healthy_slow_html_describes_only_this_snapshot() -> None:
    report = replace(_report(), instance=DatabaseInstance(
        "pg-test", "PostgreSQL", DatabaseType.POSTGRESQL, "localhost", 5432,
        "synthetic-monitor", environment="test"), findings=())
    report.details.update(version="160015", server_version_num=160015,
                          concurrency={"active_requests": 0, "blocked_requests": 0},
                          roots=[], blocking_graph={"roots": [], "children": {}, "anomalies": []},
                          all_requests=[], long_running=[], transactions=[])
    summary = build_incident_summary(report)
    assert summary.status == "HEALTHY"
    page = HtmlReportRenderer().render(build_report_model(report, summary))
    assert "本次采样未发现异常" in page
    assert "当前未观测到阻塞" in page
    assert "健康 · 提示" not in page

    abnormal = replace(report, findings=_report().findings)
    abnormal_summary = build_incident_summary(abnormal)
    abnormal_page = HtmlReportRenderer().render(build_report_model(abnormal, abnormal_summary))
    assert abnormal_summary.status == "DEGRADED"
    assert "本次采样未发现异常" not in abnormal_page
    assert "当前未观测到阻塞" not in abnormal_page


@pytest.mark.parametrize(("database_type", "scenario"), [
    (DatabaseType.MYSQL, "slow"), (DatabaseType.MYSQL, "top-sql"),
    (DatabaseType.POSTGRESQL, "slow"), (DatabaseType.POSTGRESQL, "top-sql"),
])
def test_four_mysql_postgresql_report_variants_generate_self_contained_html(
        database_type: DatabaseType, scenario: str) -> None:
    report = (_top_sql_report(database_type) if scenario == "top-sql" else
              replace(_report(), instance=DatabaseInstance(
                  f"{database_type.value}-test", database_type.value, database_type,
                  "localhost", 1, "synthetic-monitor", environment="test")))
    model = build_report_model(report, build_incident_summary(report))
    page = HtmlReportRenderer().render(model)
    assert "<html lang=\"zh-CN\">" in page
    assert "<script src=\"http" not in page
    assert '<link rel="stylesheet" href="http' not in page
