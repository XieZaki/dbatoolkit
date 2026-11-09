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
from dba.top_sql_report import TopSQLDiagnosticReport

_STAMP = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
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


def _top_sql_snapshot(engine: DatabaseType, payload: dict[str, Any]) -> TopSQLDiagnosticReport:
    instance = _slow_snapshot(engine).instance
    return TopSQLDiagnosticReport(instance, _STAMP, "top-sql", (), {
        "version": _ENGINE_CONTRACTS[engine]["version"], "top_sql": payload,
        "incomplete": payload["completeness"] != "COMPLETE",
    })


def _json_report(report: SlowReport | TopSQLDiagnosticReport, summary: IncidentSummary) -> dict[str, Any]:
    output = {
        "success": (report.details["top_sql"]["collection_status"] == "COLLECTED"
                    if report.scenario == "top-sql" else not bool(report.details.get("incomplete", True))),
        "report": asdict(report),
        "incident_summary": asdict(summary),
    }
    return cast(dict[str, Any], json.loads(render_json(_redact_diagnostic_json(output))))


def _top_sql_section(model: ReportModel) -> ReportSection:
    return next(section for section in model.sections if section.section_id == "top-sql")


@pytest.mark.parametrize("engine", _ENGINES, ids=lambda item: item.value)
@pytest.mark.parametrize("state", ("complete_matched", "partial_unmatched", "complete_empty", "failed"))
def test_independent_top_sql_json_html_semantic_parity_for_four_engines(
    engine: DatabaseType, state: str,
) -> None:
    contract = _ENGINE_CONTRACTS[engine]
    result, source = _top_sql_result(engine, state)
    payload = result.to_dict()
    report = _top_sql_snapshot(engine, payload)

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
    if json_top_sql["items"]:
        expected_items = result.to_dict()["items"]
        for index, item in enumerate(json_top_sql["items"]):
            assert item["sql_identity"] == expected_items[index]["sql_identity"]
            assert item["plan_identity"] == expected_items[index]["plan_identity"]
    identity_markers = {
        DatabaseType.SQLSERVER: ("handle-001", "plan-handle-001", "query-hash-001",
                                 "plan-hash-001", "database_id=7"),
        DatabaseType.MYSQL: ("digest-001", "schema_name="),
        DatabaseType.POSTGRESQL: ("queryid=8107", "dbid=16384", "userid=10", "toplevel=1"),
        DatabaseType.ORACLE: ("abc1234567890", "child_number=2", "inst_id=1", "plan_hash_value="),
    }[engine]
    assert all(marker not in html_top_sql for marker in identity_markers)
    assert "采集状态" in html_top_sql
    for label, value in fields.items():
        assert display_label(label) in html_top_sql
        assert display_report_value(label, value) in html_top_sql
    section = _top_sql_section(model)
    assert not section.code_blocks
    assert all("sql_text_association" not in item for item in json_top_sql["items"])
    assert json_document["success"] is (state != "failed")

    # Association remains a separate strict contract for previously captured evidence.
    associated = associate_slow_sql_text(engine, payload["items"], source)
    if state == "complete_matched":
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == "COMPLETE"
        assert associated[0]["sql_text_association"]["status"] == "MATCHED"
        assert associated[0]["sql_text_association"]["sql_text"] == _MATCHED_SQL
        assert section.tables[0].headers[1] == "Database Name"
        assert section.tables[0].rows[0][1:] == (
            "parity_db" if engine in {DatabaseType.SQLSERVER, DatabaseType.MYSQL} else "名称未采集",
            str(json_top_sql["items"][0]["metric_value"]),
        )
        assert "PARITY_SPECIAL_" not in html
    elif state == "partial_unmatched":
        association = associated[0]["sql_text_association"]
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == json_top_sql["data_visibility"] == "PARTIAL"
        assert fields["Completeness"] == "PARTIAL"
        assert "部分可用" in html_top_sql
        if engine is DatabaseType.SQLSERVER:
            assert association["status"] == "IDENTITY_INCOMPLETE"
            assert association["reason"] == "SLOW_REQUEST_IDENTITY_INCOMPLETE"
        else:
            assert association["status"] == "UNMATCHED"
            assert association["reason"] == "NO_EXACT_NATIVE_IDENTITY_MATCH"
        assert "sql_text" not in association
        assert all(reason in fields["Limitations"] for reason in json_top_sql["limitations"])
        assert _UNMATCHED_SQL not in json.dumps(json_document, ensure_ascii=False)
        assert _UNMATCHED_SQL not in html
    elif state == "complete_empty":
        assert json_top_sql["collection_status"] == "COLLECTED"
        assert json_top_sql["completeness"] == "COMPLETE"
        assert json_top_sql["items"] == []
        assert fields["Returned Records"] == "0"
        assert "没有匹配记录" in html_top_sql
        assert "ConnectionFailed" not in html_top_sql
    else:
        assert json_top_sql["collection_status"] == "FAILED"
        assert json_top_sql["completeness"] == "UNKNOWN"
        assert json_top_sql["data_visibility"] == "UNAVAILABLE"
        assert json_top_sql["database_error"]["classification"] == "TIMEOUT"
        assert "FAILED" in fields["Collection Status"]
        assert "COLLECTION_FAILED" in fields["Limitations"]
        assert "失败" in html_top_sql
        assert "ConnectionFailed" not in html_top_sql
        assert not section.tables


@pytest.mark.parametrize("engine", _ENGINES, ids=lambda item: item.value)
def test_report_boundary_values_stay_bounded_escaped_and_structured(engine: DatabaseType) -> None:
    identifier = "A" * 20_000 + "B" * 20_000
    sql_text = ("SELECT 'PARITY_BOUNDARY_<script>&\"quote\"';\n"
                "-- multiline synthetic query\n" + "Q" * (DEFAULT_HUMAN_REPORT_POLICY.max_sql_chars + 500)
                + "\nTAIL_MARKER_NOT_RENDERED")
    result, source = _top_sql_result(engine, "complete_matched", identifier=identifier, sql_text=sql_text)
    report = _slow_snapshot(engine)
    source_key = _ENGINE_CONTRACTS[engine]["source_key"]
    request = {**source[source_key][0], "session_id": 123}
    if engine is DatabaseType.SQLSERVER:
        request["statement_sql_text"] = sql_text
    report.details.update({"all_requests": [request], source_key: [request]})
    summary = build_incident_summary(report)
    model = build_report_model(report, summary)
    html = render_bounded(model, HtmlReportRenderer().render)
    card = _section_text(html, "current-request-001")
    json_document = _json_report(report, summary)
    raw_request = json_document["report"]["details"][source_key][0]
    identity_key = _ENGINE_CONTRACTS[engine]["identity_field"]
    assert raw_request[identity_key] == identifier
    assert raw_request[_ENGINE_CONTRACTS[engine]["text_key"]] == sql_text
    assert "top_sql" not in json_document["report"]["details"]

    # The standalone report also keeps the full identity in JSON and hides it in HTML.
    payload = result.to_dict()
    top_report = _top_sql_snapshot(engine, payload)
    top_summary = build_incident_summary(top_report)
    top_html = HtmlReportRenderer().render(build_report_model(top_report, top_summary))
    top_json = _json_report(top_report, top_summary)["report"]["details"]["top_sql"]
    assert top_json["items"][0]["sql_identity"]["fields"][identity_key] == identifier
    assert identifier not in top_html

    if engine is not DatabaseType.ORACLE:
        assert "A" * 100 not in card
    assert len(html) < DEFAULT_HUMAN_REPORT_POLICY.hard_report_bytes
    assert "PARITY_BOUNDARY_&lt;script&gt;&amp;&quot;quote&quot;" in html
    assert "PARITY_BOUNDARY_<script>" not in html
    assert "TAIL_MARKER_NOT_RENDERED" not in card
    assert "SQL 文本因人工报告长度上限而省略。" in card
    assert 'PARITY_BOUNDARY_<script>&"quote"' in card


@pytest.mark.parametrize(("state", "expected_status"), [
    ("no_active_requests", "NO_ACTIVE_REQUESTS"),
    ("incomplete_top_identity", "IDENTITY_INCOMPLETE"),
])
def test_sqlserver_legacy_association_state_stays_in_json_without_slow_html(
    state: str, expected_status: str,
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

    assert json_association["status"] == expected_status
    assert json_association["reason"] == (
        "NO_ACTIVE_REQUESTS" if state == "no_active_requests" else "TOP_SQL_IDENTITY_INCOMPLETE")
    assert all(section.section_id != "top-sql" for section in model.sections)
    assert 'id="top-sql"' not in html
    assert _UNMATCHED_SQL not in json.dumps(json_document, ensure_ascii=False)
    assert _UNMATCHED_SQL not in html




def test_sqlserver_independent_top_sql_hides_native_ids_and_labels_missing_database_name() -> None:
    result, _ = _top_sql_result(DatabaseType.SQLSERVER, "complete_matched")
    payload = result.to_dict()
    payload["items"][0].pop("database_name")
    report = _top_sql_snapshot(DatabaseType.SQLSERVER, payload)

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
    report = _top_sql_snapshot(engine, payload)
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
    top_sql_text = _section_text(html)
    assert "没有匹配记录" in top_sql_text
    assert "ConnectionFailed" not in top_sql_text


def test_uri_redaction_handles_long_non_uri_identifiers_and_keeps_credentials_hidden() -> None:
    prefix = "A" * 40_000
    value = (f"{prefix} postgresql://monitor:test-secret@db.example.test/parity; "
             "mysql://monitor:test-token@db.example.test/parity; "
             "postgresql://user-without-password@db.example.test/parity")
    expected = (f"{prefix} postgresql://[REDACTED]@db.example.test/parity; "
                "mysql://[REDACTED]@db.example.test/parity; "
                "postgresql://user-without-password@db.example.test/parity")

    assert redact(value) == expected
