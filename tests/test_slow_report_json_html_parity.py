from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, cast

import pytest
from test_html_reports import _report

from dba.cli import _redact_diagnostic_json
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.incident import IncidentSummary, build_incident_summary
from dba.logging import redact
from dba.presentation import render_json
from dba.reporting import (
    DEFAULT_HUMAN_REPORT_POLICY,
    HtmlReportRenderer,
    ReportModel,
    ReportSection,
    build_report_model,
    render_bounded,
)
from dba.reporting.normalizers import display_label, display_report_value
from dba.settings import SQLServerSettings
from dba.slow import SlowReport, build_slow_report
from dba.top_sql import (
    CollectionScope,
    CollectionStatus,
    Completeness,
    DataVisibility,
    IdentityKind,
    Limitation,
    MetricScope,
    MetricSemantics,
    NativeSQLIdentity,
    RankingDirection,
    TopSQLItem,
    TopSQLResult,
)
from dba.top_sql_integration import associate_slow_sql_text

_STAMP = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
_SQL_MARKER = "\n[SQL 文本因人工报告长度上限而省略。]"
_MATCHED_SQL = "SELECT 'PARITY_SPECIAL_<tag>&\"quote\"'\nFROM parity_source\nWHERE note = 'two lines';"
_UNMATCHED_SQL = "UNMATCHED_CANDIDATE_MUST_NOT_LEAK_87c1"
_ENGINES = (
    DatabaseType.SQLSERVER,
    DatabaseType.MYSQL,
    DatabaseType.POSTGRESQL,
    DatabaseType.ORACLE,
)
_ENGINE_CONTRACTS: dict[DatabaseType, dict[str, Any]] = {
    DatabaseType.SQLSERVER: {
        "display": "SQL Server", "version": "16.0.4305.5", "metric": "cpu_time",
        "metric_name": "total_cpu_time", "unit": "microseconds",
        "metric_scope": MetricScope.SQL_STATEMENT,
        "collection_scope": CollectionScope.INSTANCE, "source": "sys.dm_exec_query_stats",
        "source_key": "all_requests", "text_key": "sql_text", "truncated_key": "sql_text_truncated",
        "identity_field": "sql_handle",
    },
    DatabaseType.MYSQL: {
        "display": "MySQL", "version": "8.4.11", "metric": "sum_timer_wait",
        "metric_name": "sum_timer_wait", "unit": "milliseconds",
        "metric_scope": MetricScope.DIGEST,
        "collection_scope": CollectionScope.INSTANCE,
        "source": "performance_schema.events_statements_summary_by_digest",
        "source_key": "active_requests", "text_key": "sql_text", "truncated_key": "sql_text_truncated",
        "identity_field": "digest",
    },
    DatabaseType.POSTGRESQL: {
        "display": "PostgreSQL", "version": "16.15", "metric": "total_exec_time",
        "metric_name": "total_exec_time", "unit": "milliseconds",
        "metric_scope": MetricScope.SQL_STATEMENT,
        "collection_scope": CollectionScope.INSTANCE, "source": "pg_stat_statements",
        "source_key": "active_requests", "text_key": "query", "truncated_key": "query_truncated",
        "identity_field": "queryid",
    },
    DatabaseType.ORACLE: {
        "display": "Oracle", "version": "26.0.0.0", "metric": "cpu_time",
        "metric_name": "cpu_time", "unit": "microseconds",
        "metric_scope": MetricScope.CHILD_CURSOR,
        "collection_scope": CollectionScope.DATABASE, "source": "V$SQL child cursor",
        "source_key": "active_requests", "text_key": "sql_text", "truncated_key": "sql_text_truncated",
        "identity_field": "sql_id",
    },
}


class _SectionTextParser(HTMLParser):
    """Collect visible text from one rendered report section, skipping executable text."""

    _VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                            "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self, section_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.section_id = section_id
        self.depth = 0
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.depth == 0 and values.get("id") == self.section_id:
            self.depth = 1
            return
        if self.depth:
            if tag not in self._VOID_TAGS:
                self.depth += 1
            if tag in {"script", "style"}:
                self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
        if tag not in self._VOID_TAGS:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.ignored_depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def _section_text(html: str, section_id: str = "top-sql") -> str:
    parser = _SectionTextParser(section_id)
    parser.feed(html)
    return parser.text()


def _identity_and_source(engine: DatabaseType, *, identifier: str | None = None,
                         sql_text: str = _MATCHED_SQL, matched: bool = True
                         ) -> tuple[NativeSQLIdentity, NativeSQLIdentity | None, dict[str, Any]]:
    if engine is DatabaseType.SQLSERVER:
        fields: dict[str, str | int | None] = {
            "sql_handle": identifier or "handle-001", "query_hash": "query-hash-001",
            "statement_start_offset": 0, "statement_end_offset": 64, "database_id": 7,
        }
        plan = NativeSQLIdentity(engine, IdentityKind.PLAN, {
            "plan_handle": "plan-handle-001", "query_plan_hash": "plan-hash-001",
            "plan_generation_num": 1, "database_id": 7,
        })
        row: dict[str, Any] = {**fields, **plan.fields, "plan_generation_match_count": 1}
    elif engine is DatabaseType.POSTGRESQL:
        fields = {"queryid": identifier or 8107, "dbid": 16384, "userid": 10, "toplevel": 1}
        plan = None
        row = dict(fields)
    elif engine is DatabaseType.MYSQL:
        fields = {"digest": identifier or "digest-001", "schema_name": "parity_db"}
        plan = None
        row = dict(fields)
    else:
        fields = {"sql_id": identifier or "abc1234567890", "child_number": 2, "inst_id": 1}
        plan = None
        row = dict(fields)

    identity = NativeSQLIdentity(engine, IdentityKind.STATEMENT, fields)
    contract = _ENGINE_CONTRACTS[engine]
    if not matched:
        # Missing one required source identity field must leave the SQL unassociated.
        missing_key = {
            DatabaseType.SQLSERVER: "sql_handle",
            DatabaseType.POSTGRESQL: "userid",
            DatabaseType.MYSQL: "digest",
            DatabaseType.ORACLE: "child_number",
        }[engine]
        row.pop(missing_key, None)
    row[contract["text_key"]] = sql_text
    row[contract["truncated_key"]] = False
    return identity, plan, row


def _top_sql_result(engine: DatabaseType, state: str, *, identifier: str | None = None,
                    sql_text: str = _MATCHED_SQL) -> tuple[TopSQLResult, dict[str, Any]]:
    contract = _ENGINE_CONTRACTS[engine]
    items: tuple[TopSQLItem, ...] = ()
    rows: list[dict[str, Any]] = []
    if state in {"complete_matched", "partial_unmatched"}:
        identity, plan, source_row = _identity_and_source(
            engine, identifier=identifier, sql_text=sql_text, matched=state == "complete_matched")
        metric_value = 42_000_000 if engine is DatabaseType.SQLSERVER else 42
        items = (TopSQLItem(identity, metric_value, rank=1, plan_identity=plan,
                            database_name="parity_db" if engine is DatabaseType.SQLSERVER else None),)
        rows = [source_row]

    completeness = (Completeness.PARTIAL if state == "partial_unmatched" else
                    Completeness.UNKNOWN if state == "failed" else Completeness.COMPLETE)
    visibility = (DataVisibility.PARTIAL if state == "partial_unmatched" else
                  DataVisibility.UNAVAILABLE if state == "failed" else DataVisibility.COMPLETE)
    collection_status = CollectionStatus.FAILED if state == "failed" else CollectionStatus.COLLECTED
    limitations = ((Limitation.SOURCE_COVERAGE_INCOMPLETE, Limitation.TRUNCATED)
                   if state == "partial_unmatched" else
                   (Limitation.COLLECTION_FAILED,) if state == "failed" else ())
    source_metadata: dict[str, str | int | float | bool | None] = {}
    if engine is DatabaseType.POSTGRESQL:
        source_metadata = {"stats_reset": "2026-11-01T00:00:00+00:00",
                           "planning_time_included": False,
                           "queryid_stability": "major-version scoped"}
    elif engine is DatabaseType.MYSQL:
        source_metadata = {"statement_instrument_count": 214,
                           "statement_instrument_disabled_count": 15,
                           "statement_instrument_untimed_count": 15}
    elif engine is DatabaseType.ORACLE:
        source_metadata = {"instance_id": 1, "container_id": 0, "container_name": "CDB$ROOT"}

    result = TopSQLResult(
        database_id=f"parity-{engine.value}", database_engine=engine,
        database_version=contract["version"], profile_id=f"{engine.value}-top-sql-profile",
        collection_timestamp=_STAMP, ranking_metric=contract["metric"],
        ranking_direction=RankingDirection.DESCENDING, metric_name=contract["metric_name"],
        metric_unit=contract["unit"], metric_semantics=MetricSemantics.CUMULATIVE,
        metric_scope=contract["metric_scope"], evidence_source=contract["source"],
        data_visibility=visibility, completeness=completeness,
        collection_status=collection_status, items=items, limitations=limitations,
        collection_scope=contract["collection_scope"], source_metadata=source_metadata,
        database_error=({"vendor_code": "SYNTHETIC_TIMEOUT", "classification": "TIMEOUT",
                         "message": "Synthetic Top SQL collector timeout"} if state == "failed" else None),
    )
    return result, {contract["source_key"]: rows}


def _slow_snapshot(engine: DatabaseType) -> SlowReport:
    base = _report()
    contract = _ENGINE_CONTRACTS[engine]
    instance = replace(
        base.instance, id=f"parity-{engine.value}", name=f"{contract['display']} parity <&>",
        db_type=engine, credential_alias="synthetic-monitor-alias",
    )
    return replace(base, instance=instance, details={**base.details, "version": contract["version"],
                                                     "incomplete": False})


def _attach_top_sql(report: SlowReport, result: TopSQLResult, source: dict[str, Any]) -> None:
    payload = result.to_dict()
    source_key = _ENGINE_CONTRACTS[result.database_engine]["source_key"]
    payload["items"] = associate_slow_sql_text(
        result.database_engine, payload["items"], {source_key: source.get(source_key, [])})
    report.details["top_sql"] = payload
    report.details["top_sql_integration"] = {
        "status": result.collection_status.value,
        "database_engine": result.database_engine.value,
        "database_version": result.database_version,
        "ranking_metric": result.ranking_metric,
        "top_sql": payload,
    }


def _json_report(report: SlowReport, summary: IncidentSummary) -> dict[str, Any]:
    output = {
        "success": not bool(report.details.get("incomplete", True)),
        "report": asdict(report),
        "incident_summary": asdict(summary),
    }
    return cast(dict[str, Any], json.loads(render_json(_redact_diagnostic_json(output))))


def _top_sql_section(model: ReportModel) -> ReportSection:
    return next(section for section in model.sections if section.section_id == "top-sql")


@pytest.mark.parametrize("engine", _ENGINES, ids=lambda item: item.value)
@pytest.mark.parametrize("state", ("complete_matched", "partial_unmatched", "complete_empty", "failed"))
def test_slow_report_json_html_semantic_parity_for_four_engines(
    engine: DatabaseType, state: str,
) -> None:
    contract = _ENGINE_CONTRACTS[engine]
    report = _slow_snapshot(engine)
    baseline = _slow_snapshot(engine)
    baseline_summary = build_incident_summary(baseline, "reports/parity.html")
    result, source = _top_sql_result(engine, state)
    _attach_top_sql(report, result, source)

    summary = build_incident_summary(report, "reports/parity.html")
    model = build_report_model(report, summary)
    html = render_bounded(model, HtmlReportRenderer().render)
    html_top_sql = _section_text(html)
    json_document = _json_report(report, summary)
    json_report = json_document["report"]
    json_top_sql = json_report["details"]["top_sql"]
    fields = dict(_top_sql_section(model).fields)

    assert json_report["instance"]["db_type"] == engine.value
    assert json_report["instance"]["credential_alias"] == "[REDACTED]"
    assert report.instance.credential_alias == "synthetic-monitor-alias"
    assert fields["Database Engine"] == contract["display"]
    assert fields["Database Version"] == contract["version"]
    assert fields["Collection Status"] == json_top_sql["collection_status"]
    assert fields["Completeness"] == {
        "COMPLETE": "完整", "PARTIAL": "PARTIAL", "UNKNOWN": "未知",
    }[json_top_sql["completeness"]]
    assert fields["Data Visibility"] == {
        "COMPLETE": "完整", "PARTIAL": "PARTIAL", "UNAVAILABLE": "UNAVAILABLE",
    }[json_top_sql["data_visibility"]]
    assert fields["Returned Records"] == str(len(json_top_sql["items"]))
    assert fields["Ranking Metric"] == json_top_sql["ranking_metric"]
    assert fields["Metric Name"] == json_top_sql["metric_name"]
    assert fields["Metric Unit"] == json_top_sql["metric_unit"]
    assert fields["Metric Semantics"] == json_top_sql["metric_semantics"]
    assert fields["Metric Scope"] == json_top_sql["metric_scope"]
    assert fields["Collection Scope"] == json_top_sql["collection_scope"]
    if engine is DatabaseType.SQLSERVER:
        assert "数据库名称" in html_top_sql
        if state in {"complete_matched", "partial_unmatched"}:
            assert "parity_db" in html_top_sql and "42.00 秒" in html_top_sql
        assert "采集状态" not in html_top_sql
        assert "handle-001" not in html_top_sql
        assert "plan-handle-001" not in html_top_sql
        assert "query-hash-001" not in html_top_sql
        for label in ("Ranking Metric", "Ranking Direction", "Collection Scope", "Returned Records"):
            assert display_label(label) in html_top_sql
        assert '<section class="report-section" id="top-sql"' in html
    else:
        assert "采集状态" in html_top_sql
        assert display_label(json_top_sql["collection_status"]) in html_top_sql
        for label, value in fields.items():
            assert display_label(label) in html_top_sql
            display_value = display_report_value(label, value)
            assert display_value in html_top_sql
    assert json_document["success"] is True
    assert summary.status == baseline_summary.status
    assert summary.severity == baseline_summary.severity
    assert summary.primary_problem == baseline_summary.primary_problem
    assert summary.root_cause_candidate == baseline_summary.root_cause_candidate
    assert summary.data_completeness == baseline_summary.data_completeness
    quality = next(section for section in model.sections if section.section_id == "collection-quality")
    quality_row = next(row for table in quality.tables for row in table.rows
                       if row[0] == f"{engine.value}_top_sql")
    assert quality_row[1] == {
        "complete_matched": "完整", "partial_unmatched": "PARTIAL",
        "complete_empty": "完整", "failed": "FAILED",
    }[state]

    if state == "complete_matched":
        association = json_top_sql["items"][0]["sql_text_association"]
        section = _top_sql_section(model)
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == "COMPLETE"
        assert association["status"] == "MATCHED"
        assert section.tables[0].rows[0][-2 if engine is DatabaseType.SQLSERVER else -1] == "MATCHED"
        assert len(section.code_blocks) == 1
        assert section.code_blocks[0].content == association["sql_text"]
        assert " ".join(_MATCHED_SQL.split()) in html_top_sql
        assert "PARITY_SPECIAL_&lt;tag&gt;" in html
        assert "PARITY_SPECIAL_<tag>" not in html
    elif state == "partial_unmatched":
        association = json_top_sql["items"][0]["sql_text_association"]
        section = _top_sql_section(model)
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == "PARTIAL"
        assert json_top_sql["data_visibility"] == "PARTIAL"
        assert fields["Completeness"] != "完整"
        assert fields["Completeness"] == "PARTIAL"
        assert ("证据完整性 部分可用" in html if engine is DatabaseType.SQLSERVER
                else "证据完整性 部分可用" in html_top_sql)
        if engine is DatabaseType.SQLSERVER:
            assert association["status"] == "IDENTITY_INCOMPLETE"
            assert association["reason"] == "SLOW_REQUEST_IDENTITY_INCOMPLETE"
            assert "活动请求身份字段不完整" in html_top_sql
            assert section.tables[0].rows[0][-2] == "身份信息不完整"
        else:
            assert association["status"] == "UNMATCHED"
            assert association["reason"] == "NO_EXACT_NATIVE_IDENTITY_MATCH"
            assert section.tables[0].rows[0][-1] == "UNMATCHED"
        assert "sql_text" not in association
        assert all(reason in fields["Limitations"] for reason in
                   json_top_sql["limitations"])
        assert _UNMATCHED_SQL not in json.dumps(json_document, ensure_ascii=False)
        assert _UNMATCHED_SQL not in html
    elif state == "complete_empty":
        section = _top_sql_section(model)
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == "COMPLETE"
        assert json_top_sql["items"] == []
        assert fields["Returned Records"] == "0"
        assert "没有匹配记录" in section.summary
        assert "没有匹配记录" in html_top_sql
        assert "ConnectionFailed" not in html_top_sql
    else:
        section = _top_sql_section(model)
        assert json_top_sql["collection_status"] == "FAILED"
        assert json_top_sql["completeness"] == "UNKNOWN"
        assert json_top_sql["data_visibility"] == "UNAVAILABLE"
        assert json_top_sql["database_error"]["classification"] == "TIMEOUT"
        assert "FAILED" in fields["Collection Status"]
        assert "COLLECTION_FAILED" in fields["Limitations"]
        assert ("失败" in html if engine is DatabaseType.SQLSERVER else "失败" in html_top_sql)
        assert "ConnectionFailed" not in html_top_sql
        assert not section.tables
        assert json_document["success"] is True


@pytest.mark.parametrize("engine", _ENGINES, ids=lambda item: item.value)
def test_report_boundary_values_stay_bounded_escaped_and_structured(engine: DatabaseType) -> None:
    identifier = "A" * 20_000 + "B" * 20_000
    sql_text = ("SELECT 'PARITY_BOUNDARY_<script>&\"quote\"';\n"
                "-- multiline synthetic query\n" + "Q" * (DEFAULT_HUMAN_REPORT_POLICY.max_sql_chars + 500)
                + "\nTAIL_MARKER_NOT_RENDERED")
    result, source = _top_sql_result(engine, "complete_matched", identifier=identifier, sql_text=sql_text)
    report = _slow_snapshot(engine)
    _attach_top_sql(report, result, source)
    summary = build_incident_summary(report, "reports/parity.html")
    model = build_report_model(report, summary)
    html = render_bounded(model, HtmlReportRenderer().render)
    html_top_sql = _section_text(html)
    json_document = _json_report(report, summary)
    payload = json_document["report"]["details"]["top_sql"]
    identity_key = str(_ENGINE_CONTRACTS[engine]["identity_field"])
    association = payload["items"][0]["sql_text_association"]
    identity_value = payload["items"][0]["sql_identity"]["fields"][identity_key]

    assert identity_value == identifier
    assert len(association["sql_text"]) == DEFAULT_HUMAN_REPORT_POLICY.max_sql_chars + len(_SQL_MARKER)
    assert association["sql_text"].endswith(_SQL_MARKER)
    assert association["truncated_by_report_policy"] is True
    assert "\n-- multiline synthetic query\n" in association["sql_text"]
    assert _UNMATCHED_SQL not in html


    section = _top_sql_section(model)
    identity_text = section.tables[0].rows[0][1]
    assert section.code_blocks[0].content == association["sql_text"]
    if engine is DatabaseType.SQLSERVER:
        assert identity_text == "parity_db"
        assert "A" * 100 not in html_top_sql
        assert identifier not in html
    else:
        assert "A" * 100 in identity_text
        assert "A" * 101 not in identity_text
        assert "B" not in identity_text
    assert len(html) < DEFAULT_HUMAN_REPORT_POLICY.hard_report_bytes
    assert "PARITY_BOUNDARY_&lt;script&gt;&amp;&quot;quote&quot;" in html
    assert "PARITY_BOUNDARY_<script>" not in html
    assert "TAIL_MARKER_NOT_RENDERED" not in html_top_sql
    assert "SQL 文本因人工报告长度上限而省略。" in html_top_sql
    assert "PARITY_BOUNDARY_<script>&\"quote\"" in html_top_sql


@pytest.mark.parametrize(("state", "expected_status", "expected_reason_labels"), [
    ("no_active_requests", "NO_ACTIVE_REQUESTS", ("无活动请求",)),
    ("incomplete_top_identity", "IDENTITY_INCOMPLETE",
     ("Top SQL 身份字段不完整", "无活动请求")),
])
def test_sqlserver_identity_state_has_json_html_parity(
    state: str, expected_status: str, expected_reason_labels: tuple[str, ...],
) -> None:
    report = _slow_snapshot(DatabaseType.SQLSERVER)
    result, _ = _top_sql_result(DatabaseType.SQLSERVER, "complete_matched")
    payload = result.to_dict()
    item = payload["items"][0]
    if state == "incomplete_top_identity":
        item["sql_identity"]["fields"].pop("database_id")
    payload["items"] = associate_slow_sql_text(
        DatabaseType.SQLSERVER, payload["items"], {"all_requests": []})
    report.details.update({
        "all_requests": [],
        "top_sql": payload,
        "top_sql_integration": {"status": "COLLECTED", "database_engine": "sqlserver",
                                "top_sql": payload},
    })

    summary = build_incident_summary(report, "reports/sqlserver-identity-parity.html")
    model = build_report_model(report, summary)
    html = render_bounded(model, HtmlReportRenderer().render)
    json_document = _json_report(report, summary)
    json_association = json_document["report"]["details"]["top_sql"]["items"][0]["sql_text_association"]
    section = _top_sql_section(model)

    assert json_association["status"] == expected_status
    assert json_association["reason"] == (
        "NO_ACTIVE_REQUESTS" if state == "no_active_requests" else "TOP_SQL_IDENTITY_INCOMPLETE")
    assert section.tables is not None
    assert section.tables[0].headers[-1] == "Association reason"
    for label in expected_reason_labels:
        assert label in html
    assert display_label(expected_status) in html
    assert _UNMATCHED_SQL not in json.dumps(json_document, ensure_ascii=False)
    assert _UNMATCHED_SQL not in html




def test_sqlserver_slow_top_sql_hides_native_ids_and_labels_missing_database_name() -> None:
    report = _slow_snapshot(DatabaseType.SQLSERVER)
    result, source = _top_sql_result(DatabaseType.SQLSERVER, "complete_matched")
    _attach_top_sql(report, result, source)
    report.details["top_sql"]["items"][0].pop("database_name", None)

    summary = build_incident_summary(report, "reports/sqlserver-readable-top-sql.html")
    model = build_report_model(report, summary)
    html = render_bounded(model, HtmlReportRenderer().render)
    html_top_sql = _section_text(html)
    section = _top_sql_section(model)
    json_document = _json_report(report, summary)
    item = json_document["report"]["details"]["top_sql"]["items"][0]

    assert section.tables[0].headers[1] == "Database Name"
    assert section.tables[0].rows[0][1] == "名称未采集"
    assert "名称未采集" in html_top_sql
    assert "handle-001" not in html_top_sql
    assert "plan-handle-001" not in html_top_sql
    assert "query-hash-001" not in html_top_sql
    assert "database_id=7" not in html_top_sql
    assert item["sql_identity"]["fields"]["sql_handle"] == "handle-001"
    assert item["sql_identity"]["fields"]["database_id"] == 7
    assert item["plan_identity"]["fields"]["plan_handle"] == "plan-handle-001"

def test_sqlserver_session_identity_is_consistent_in_json_and_html() -> None:
    now = _STAMP
    instance = DatabaseInstance(
        "synthetic-sqlserver", "Synthetic SQL Server", DatabaseType.SQLSERVER,
        "localhost", 1433, "synthetic-monitor", environment="test",
    )
    sessions = [{
        "session_id": 51,
        "login_name": "sa",
        "host_name": "ZAKKY",
        "client_net_address": "172.17.0.1",
        "program_name": "Navicat",
        "status": "running",
    }]
    request = {
        "session_id": 51,
        "status": "running",
        "elapsed_ms": 30000,
        "blocking_session_id": 0,
        "normalized": {
            "session_id": 51,
            "username": None,
            "client_host": None,
            "client_ip": None,
            "application": None,
        },
    }
    results = tuple(
        CollectorResult("synthetic-sqlserver", name, now, now, 0.0, tuple(rows),
                        len(rows), False, True)
        for name, rows in (
            ("active_sessions", sessions),
            ("active_requests", [request]),
            ("transactions", []),
            ("connections", []),
        )
    )
    report = build_slow_report(
        instance, results, SQLServerSettings(long_running_seconds=1, slow_top_n=10),
    )
    json_document = json.loads(render_json(_redact_diagnostic_json({"report": asdict(report)})))
    details = json_document["report"]["details"]
    normalized = details["all_requests"][0]["normalized"]
    assert normalized == details["top_requests"][0]["normalized"]

    identity_values = {
        "username": "sa",
        "client_host": "ZAKKY",
        "client_ip": "172.17.0.1",
        "application": "Navicat",
    }
    assert {key: normalized[key] for key in identity_values} == identity_values

    summary = build_incident_summary(report, "reports/session-identity.html")
    html = render_bounded(
        build_report_model(report, summary), HtmlReportRenderer().render,
    )
    html_identity = _section_text(html, "session-sources")
    for value in identity_values.values():
        assert value in html_identity


@pytest.mark.parametrize("engine", _ENGINES, ids=lambda item: item.value)
def test_sparse_top_sql_snapshot_with_missing_optional_fields_is_renderable(engine: DatabaseType) -> None:
    result, _ = _top_sql_result(engine, "complete_empty")
    payload = result.to_dict()
    payload.pop("source_metadata")
    payload.pop("database_error")
    report = _slow_snapshot(engine)
    report.details["top_sql"] = payload
    summary = build_incident_summary(report, "reports/parity.html")
    model = build_report_model(report, summary)

    html = render_bounded(model, HtmlReportRenderer().render)
    json_document = _json_report(report, summary)
    json_top_sql = json_document["report"]["details"]["top_sql"]
    fields = dict(_top_sql_section(model).fields)

    assert "source_metadata" not in json_top_sql and "database_error" not in json_top_sql
    assert json_top_sql["collection_status"] == fields["Collection Status"] == "COLLECTED"
    assert json_top_sql["items"] == []
    assert fields["Returned Records"] == "0"
    assert "已采集" in _section_text(html)


def test_uri_redaction_handles_long_non_uri_identifiers_and_keeps_credentials_hidden() -> None:
    prefix = "A" * 40_000
    value = (f"{prefix} postgresql://monitor:test-secret@db.example.test/parity; "
             "mysql://monitor:test-token@db.example.test/parity; "
             "postgresql://user-without-password@db.example.test/parity")
    expected = (f"{prefix} postgresql://[REDACTED]@db.example.test/parity; "
                "mysql://[REDACTED]@db.example.test/parity; "
                "postgresql://user-without-password@db.example.test/parity")

    assert redact(value) == expected
