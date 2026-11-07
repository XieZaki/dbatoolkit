from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dba import top_sql_integration as integration
from dba.core import DatabaseInstance, DatabaseType
from dba.incident import build_incident_summary
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.model import HumanReportPolicy
from dba.reporting.presenters import render_report_section_text
from dba.top_sql import CollectionStatus, MetricValueStatus


def _identity(engine: DatabaseType) -> dict[str, Any]:
    if engine is DatabaseType.SQLSERVER:
        return {"database_engine": engine.value, "kind": "STATEMENT", "fields": {
            "sql_handle": "0x01", "query_hash": "0x02", "statement_start_offset": 0,
            "statement_end_offset": 20, "database_id": 7,
        }}
    if engine is DatabaseType.POSTGRESQL:
        return {"database_engine": engine.value, "kind": "STATEMENT", "fields": {
            "queryid": 17, "dbid": 1, "userid": 10, "toplevel": 1,
        }}
    if engine is DatabaseType.MYSQL:
        return {"database_engine": engine.value, "kind": "STATEMENT", "fields": {
            "digest": "ab" * 32, "schema_name": "sakila",
        }}
    return {"database_engine": engine.value, "kind": "STATEMENT", "fields": {
        "sql_id": "0sbbcuruzd66f", "child_number": 2, "inst_id": 1, "con_id": 3,
    }}


def _slow_row(engine: DatabaseType, identity: dict[str, Any] | None = None,
              **changes: Any) -> dict[str, Any]:
    row = dict(identity or _identity(engine)["fields"])
    text_key, truncated_key = integration._SQL_TEXT_FIELDS[engine]
    row.update({text_key: "SELECT safe_value", truncated_key: False})
    row.update(changes)
    return row


@pytest.mark.parametrize("engine", [
    DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
    DatabaseType.MYSQL, DatabaseType.ORACLE,
])
def test_exact_native_identity_match_for_all_four_engines(engine: DatabaseType) -> None:
    item: dict[str, Any] = {"rank": 1, "sql_identity": _identity(engine), "plan_identity": None}
    changes: dict[str, Any] = {}
    if engine is DatabaseType.SQLSERVER:
        item["plan_identity"] = {"database_engine": engine.value, "kind": "PLAN", "fields": {
            "plan_handle": "0x03", "query_plan_hash": "0x04", "plan_generation_num": 8,
        }}
        changes = {"plan_handle": "0x03", "query_plan_hash": "0x04", "plan_generation_num": 8}
    associated = integration.associate_slow_sql_text(
        engine, [item], {integration._SLOW_REQUEST_KEYS[engine]: [_slow_row(engine, **changes)]})
    assert associated[0]["sql_text_association"] == {
        "status": "MATCHED", "source": "slow.active_requests", "sql_text": "SELECT safe_value",
        "truncated_by_report_policy": False,
    }


def test_postgresql_query_id_and_database_id_mismatch_remain_unmatched() -> None:
    item = {"sql_identity": _identity(DatabaseType.POSTGRESQL)}
    for change in ({"queryid": 18}, {"dbid": 2}):
        rows = [_slow_row(DatabaseType.POSTGRESQL, **change)]
        result = integration.associate_slow_sql_text(
            DatabaseType.POSTGRESQL, [item], {"active_requests": rows})
        assert result[0]["sql_text_association"]["status"] == "UNMATCHED"
        assert "sql_text" not in result[0]["sql_text_association"]


def test_mysql_schema_and_oracle_container_mismatch_remain_unmatched() -> None:
    cases = (
        (DatabaseType.MYSQL, {"active_requests": [_slow_row(
            DatabaseType.MYSQL, schema_name="other_schema")]}, _identity(DatabaseType.MYSQL)),
        (DatabaseType.ORACLE, {"active_requests": [_slow_row(
            DatabaseType.ORACLE, con_id=4)]}, _identity(DatabaseType.ORACLE)),
    )
    for engine, details, identity in cases:
        result = integration.associate_slow_sql_text(engine, [{"sql_identity": identity}], details)
        assert result[0]["sql_text_association"]["status"] == "UNMATCHED"


def test_sqlserver_plan_generation_mismatch_is_not_associated() -> None:
    sql_identity = _identity(DatabaseType.SQLSERVER)
    plan_identity = {"database_engine": "sqlserver", "kind": "PLAN", "fields": {
        "plan_handle": "0x03", "query_plan_hash": "0x04", "plan_generation_num": 8,
    }}
    item = {"sql_identity": sql_identity, "plan_identity": plan_identity}
    row = _slow_row(DatabaseType.SQLSERVER, plan_handle="0x03", query_plan_hash="0x04",
                    plan_generation_num=9)
    result = integration.associate_slow_sql_text(
        DatabaseType.SQLSERVER, [item], {"all_requests": [row]})
    assert result[0]["sql_text_association"]["status"] == "UNMATCHED"


def test_identity_must_be_complete_and_source_sql_must_not_be_truncated() -> None:
    item = {"sql_identity": _identity(DatabaseType.MYSQL)}
    incomplete = dict(_identity(DatabaseType.MYSQL)["fields"])
    incomplete.pop("schema_name")
    result = integration.associate_slow_sql_text(
        DatabaseType.MYSQL, [item], {"active_requests": [_slow_row(DatabaseType.MYSQL, incomplete)]})
    assert result[0]["sql_text_association"]["status"] == "UNMATCHED"

    result = integration.associate_slow_sql_text(
        DatabaseType.MYSQL, [item], {"active_requests": [
            _slow_row(DatabaseType.MYSQL, sql_text_truncated=True),
        ]})
    assert result[0]["sql_text_association"] == {
        "status": "UNMATCHED", "source": None, "reason": "SOURCE_SQL_TEXT_TRUNCATED",
    }

    sqlserver_item = {"sql_identity": _identity(DatabaseType.SQLSERVER), "plan_identity": None}
    sqlserver = integration.associate_slow_sql_text(
        DatabaseType.SQLSERVER, [sqlserver_item], {"all_requests": [
            _slow_row(DatabaseType.SQLSERVER),
        ]})
    assert sqlserver[0]["sql_text_association"]["status"] == "UNMATCHED"


def test_ambiguous_native_identity_is_unmatched_and_never_displays_candidate_sql() -> None:
    engine = DatabaseType.POSTGRESQL
    identity = _identity(engine)
    item = {"rank": 1, "sql_identity": identity, "metric_value": 10}
    fuzzy_only = _slow_row(engine, queryid=18, query="SELECT FUZZY_ONLY_SENTINEL")

    no_exact_match = integration.associate_slow_sql_text(
        engine, [item], {"active_requests": [fuzzy_only]})[0]["sql_text_association"]
    assert no_exact_match["status"] == "UNMATCHED"
    assert no_exact_match["reason"] == "NO_EXACT_NATIVE_IDENTITY_MATCH"
    assert "sql_text" not in no_exact_match

    exact_row = _slow_row(engine, query="SELECT EXACT_MATCH_SENTINEL")
    normal_match = integration.associate_slow_sql_text(
        engine, [item], {"active_requests": [exact_row, fuzzy_only]})[0]["sql_text_association"]
    assert normal_match["status"] == "MATCHED"
    assert normal_match["sql_text"] == "SELECT EXACT_MATCH_SENTINEL"

    ambiguous_rows = [
        _slow_row(engine, query="SELECT AMBIGUOUS_SQL_ONE_SENTINEL"),
        _slow_row(engine, query="SELECT AMBIGUOUS_SQL_TWO_SENTINEL"),
        fuzzy_only,
    ]
    ambiguous = integration.associate_slow_sql_text(
        engine, [item], {"active_requests": ambiguous_rows})[0]["sql_text_association"]
    assert ambiguous["status"] == "UNMATCHED"
    assert ambiguous["reason"] == "AMBIGUOUS_NATIVE_IDENTITY_MATCH"
    assert "sql_text" not in ambiguous

    from tests.test_html_reports import _report

    report = _report()
    report.details["top_sql"] = {
        "database_engine": engine.value,
        "database_version": "16.15",
        "collection_status": "COLLECTED",
        "completeness": "COMPLETE",
        "data_visibility": "COMPLETE",
        "ranking_metric": "total_exec_time",
        "metric_unit": "milliseconds",
        "metric_semantics": "CUMULATIVE",
        "collection_scope": "INSTANCE",
        "evidence_source": "pg_stat_statements",
        "items": [{**item, "sql_text_association": ambiguous}],
    }
    model = build_report_model(report, build_incident_summary(report))
    section = next(part for part in model.sections if part.section_id == "top-sql")
    cli_text = render_report_section_text(section)
    html = HtmlReportRenderer().render(model)
    assert "身份未匹配" in cli_text and "身份未匹配" in html
    for candidate in (
        "FUZZY_ONLY_SENTINEL", "EXACT_MATCH_SENTINEL",
        "AMBIGUOUS_SQL_ONE_SENTINEL", "AMBIGUOUS_SQL_TWO_SENTINEL",
    ):
        assert candidate not in cli_text
        assert candidate not in html


def test_long_sql_is_bounded_and_redacted_using_human_report_policy() -> None:
    item = {"sql_identity": _identity(DatabaseType.POSTGRESQL)}
    sql = "SELECT 'password=secret-value' " + ("x" * 80)
    policy = HumanReportPolicy(max_sql_chars=32)
    result = integration.associate_slow_sql_text(
        DatabaseType.POSTGRESQL, [item], {"active_requests": [
            _slow_row(DatabaseType.POSTGRESQL, query=sql),
        ]}, policy)
    detail = result[0]["sql_text_association"]
    assert detail["status"] == "MATCHED"
    assert detail["truncated_by_report_policy"] is True
    assert len(detail["sql_text"]) < 100
    assert "secret-value" not in detail["sql_text"]
    assert "password=[REDACTED]" in detail["sql_text"]


def _adapter(engine: DatabaseType) -> Any:
    adapter_type = {
        DatabaseType.SQLSERVER: integration.SQLServerAdapter,
        DatabaseType.POSTGRESQL: integration.PostgreSQLAdapter,
        DatabaseType.MYSQL: integration.MySQLAdapter,
        DatabaseType.ORACLE: integration.OracleAdapter,
    }[engine]
    adapter = object.__new__(adapter_type)
    adapter.instance = DatabaseInstance("test", "Test", engine, "test.invalid", 1,
                                        "monitor", environment="development")
    return adapter


@pytest.mark.parametrize("engine", [
    DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
    DatabaseType.MYSQL, DatabaseType.ORACLE,
])
def test_opt_in_routes_once_to_existing_collector(engine: DatabaseType,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, DatabaseType, str, int]] = []

    class FakeResult:
        collection_status = CollectionStatus.COLLECTED

        def to_dict(self) -> dict[str, Any]:
            return {"database_engine": engine.value, "database_version": "test",
                    "collection_status": "COLLECTED", "completeness": "COMPLETE",
                    "data_visibility": "COMPLETE", "items": []}

    def fake_collector(name: str) -> type[Any]:
        class FakeCollector:
            def __init__(self, adapter: Any, profile: Any) -> None:
                self.adapter = adapter

            def collect(self, instance: DatabaseInstance, metric: str, top_n: int) -> FakeResult:
                assert instance is self.adapter.instance
                calls.append((name, engine, metric, top_n))
                return FakeResult()

        return FakeCollector

    route_names = {
        DatabaseType.SQLSERVER: "SQLServerTopSQLCollector",
        DatabaseType.POSTGRESQL: "PostgreSQLTopSQLCollector",
        DatabaseType.MYSQL: "MySQLTopSQLCollector",
        DatabaseType.ORACLE: "OracleTopSQLCollector",
    }
    for name in route_names.values():
        monkeypatch.setattr(integration, name, fake_collector(name))
    profile = SimpleNamespace(version_status=integration.VersionStatus.VERIFIED,
                               support_state=integration.SupportState.SUPPORTED)
    monkeypatch.setattr(integration, "resolve_profile_for_adapter", lambda *_: (profile, None))

    result = integration.collect_slow_top_sql(_adapter(engine), {}, enabled=True,
                                              core_complete=True, top_n=7)
    assert result is not None and result["status"] == "COLLECTED"
    assert calls == [(route_names[engine], engine, integration._DEFAULT_METRICS[engine], 7)]


def test_disabled_or_incomplete_slow_does_not_call_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_: Any, **__: Any) -> Any:
        raise AssertionError("Top SQL Collector must not be called")

    monkeypatch.setattr(integration, "resolve_profile_for_adapter", unexpected)
    assert integration.collect_slow_top_sql(_adapter(DatabaseType.POSTGRESQL), {},
                                            enabled=False, core_complete=True) is None
    result = integration.collect_slow_top_sql(_adapter(DatabaseType.POSTGRESQL), {},
                                              enabled=True, core_complete=False)
    assert result is not None
    assert result["status"] == MetricValueStatus.NOT_COLLECTED.value
    assert result["reason"] == "CORE_EVIDENCE_INCOMPLETE"


def test_unsupported_permission_empty_partial_and_exception_statuses_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(DatabaseType.POSTGRESQL)
    unknown = SimpleNamespace(version_status=integration.VersionStatus.UNVERIFIED,
                              support_state=integration.SupportState.UNKNOWN)
    monkeypatch.setattr(integration, "resolve_profile_for_adapter", lambda *_: (unknown, None))
    assert integration.collect_slow_top_sql(adapter, {}, enabled=True,
                                            core_complete=True)["status"] == "UNSUPPORTED"

    profile = SimpleNamespace(version_status=integration.VersionStatus.VERIFIED,
                              support_state=integration.SupportState.SUPPORTED)
    monkeypatch.setattr(integration, "resolve_profile_for_adapter", lambda *_: (profile, None))

    class FakeResult:
        def __init__(self, status: CollectionStatus, items: list[dict[str, Any]],
                     completeness: str = "COMPLETE") -> None:
            self.collection_status = status
            self.items = items
            self.completeness = completeness

        def to_dict(self) -> dict[str, Any]:
            return {"database_engine": "postgresql", "database_version": "16",
                    "collection_status": self.collection_status.value,
                    "completeness": self.completeness, "data_visibility": "PARTIAL"
                    if self.completeness == "PARTIAL" else "COMPLETE", "items": self.items}

    class FakeCollector:
        outcome = FakeResult(CollectionStatus.PERMISSION_DENIED, [])

        def __init__(self, *_: Any) -> None:
            pass

        def collect(self, *_: Any) -> FakeResult:
            return self.outcome

    monkeypatch.setattr(integration, "PostgreSQLTopSQLCollector", FakeCollector)
    denied = integration.collect_slow_top_sql(adapter, {}, enabled=True, core_complete=True)
    assert denied["status"] == "PERMISSION_DENIED"
    assert denied["top_sql"]["collection_status"] == "PERMISSION_DENIED"

    FakeCollector.outcome = FakeResult(CollectionStatus.COLLECTED, [])
    empty = integration.collect_slow_top_sql(adapter, {}, enabled=True, core_complete=True)
    assert empty["status"] == "COLLECTED" and empty["top_sql"]["items"] == []

    FakeCollector.outcome = FakeResult(CollectionStatus.COLLECTED, [], completeness="PARTIAL")
    partial = integration.collect_slow_top_sql(adapter, {}, enabled=True, core_complete=True)
    assert partial["top_sql"]["completeness"] == "PARTIAL"

    def raise_error(*_: Any, **__: Any) -> Any:
        raise RuntimeError("untrusted vendor detail must not be copied")

    monkeypatch.setattr(FakeCollector, "collect", raise_error)
    failed = integration.collect_slow_top_sql(adapter, {}, enabled=True, core_complete=True)
    assert failed["status"] == "FAILED"
    assert failed["error_type"] == "RuntimeError"
    assert "untrusted vendor detail" not in str(failed)


def test_default_metric_semantics_remain_cumulative_and_collection_is_single_snapshot() -> None:
    assert set(integration._DEFAULT_METRICS.values()) == {
        "cpu_time", "total_exec_time", "sum_timer_wait",
    }
    assert all("delta" not in metric for metric in integration._DEFAULT_METRICS.values())


def test_cli_and_html_use_the_same_bounded_top_sql_report_section() -> None:
    from tests.test_html_reports import _report

    report = _report()
    payload = {
        "database_engine": "postgresql", "database_version": "16.4",
        "collection_status": "COLLECTED", "completeness": "COMPLETE",
        "data_visibility": "COMPLETE", "ranking_metric": "total_exec_time",
        "metric_name": "elapsed_time", "metric_unit": "milliseconds",
        "metric_semantics": "CUMULATIVE", "metric_scope": "SQL_STATEMENT",
        "collection_scope": "INSTANCE", "evidence_source": "pg_stat_statements",
        "items": [{"rank": 1, "sql_identity": _identity(DatabaseType.POSTGRESQL),
                   "metric_value": 42, "sql_text_association": {
                       "status": "MATCHED", "sql_text": "SELECT '&lt;safe&gt;'",}}],
    }
    report.details.update({"top_sql": payload, "top_sql_integration": {"status": "COLLECTED"}})
    model = build_report_model(report, build_incident_summary(report))
    section = next(item for item in model.sections if item.section_id == "top-sql")
    cli_text = render_report_section_text(section)
    html = HtmlReportRenderer().render(model)
    for evidence in ("total_exec_time", "累计统计", "queryid=17", "身份已匹配"):
        assert evidence in cli_text and evidence in html
    assert "Top SQL 排名反映数据库统计视图中的累计表现，不代表当前故障根因。" in cli_text
    assert "SELECT" in cli_text and "SELECT" in html


def test_unmatched_top_sql_never_displays_sql_details() -> None:
    from tests.test_html_reports import _report

    report = _report()
    payload = {
        "database_engine": "postgresql", "database_version": "16.4",
        "collection_status": "COLLECTED", "completeness": "COMPLETE",
        "data_visibility": "COMPLETE", "ranking_metric": "total_exec_time",
        "metric_name": "elapsed_time", "metric_unit": "milliseconds",
        "metric_semantics": "CUMULATIVE", "metric_scope": "SQL_STATEMENT",
        "collection_scope": "INSTANCE", "evidence_source": "pg_stat_statements",
        "items": [{"rank": 1, "sql_identity": _identity(DatabaseType.POSTGRESQL),
                   "metric_value": 42, "sql_text_association": {
                       "status": "UNMATCHED", "sql_text": "MUST_NOT_BE_SHOWN"}}],
    }
    report.details["top_sql"] = payload
    model = build_report_model(report, build_incident_summary(report))
    section = next(item for item in model.sections if item.section_id == "top-sql")
    html = HtmlReportRenderer().render(model)
    assert "身份未匹配" in render_report_section_text(section)
    assert "MUST_NOT_BE_SHOWN" not in render_report_section_text(section)
    assert "身份未匹配" in html and "MUST_NOT_BE_SHOWN" not in html


def test_status_only_top_sql_is_presented_without_marking_slow_incomplete() -> None:
    from tests.test_html_reports import _report

    report = _report()
    report.details["top_sql_integration"] = {
        "status": MetricValueStatus.NOT_COLLECTED.value,
        "reason": "CORE_EVIDENCE_INCOMPLETE",
    }
    model = build_report_model(report, build_incident_summary(report))
    section = next(item for item in model.sections if item.section_id == "top-sql")
    assert "未采集" in render_report_section_text(section)
    assert report.details.get("incomplete") is False
