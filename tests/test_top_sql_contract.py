from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from dba.core import DatabaseType
from dba.incident import build_incident_summary
from dba.presentation import render_human, render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.top_sql import (
    CollectionScope,
    CollectionStatus,
    Completeness,
    DataVisibility,
    IdentityKind,
    Limitation,
    MetricScope,
    MetricSemantics,
    MetricValueStatus,
    NativeSQLIdentity,
    RankingDirection,
    TopSQLItem,
    TopSQLResult,
)
from tests.test_html_reports import _report

STAMP = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _identity(engine: DatabaseType) -> NativeSQLIdentity:
    fields: dict[str, str | int] = {
        DatabaseType.SQLSERVER: {"sql_handle": "0x01", "query_hash": "0x03",
                                 "statement_start_offset": 0, "statement_end_offset": 20,
                                 "database_id": 7},
        DatabaseType.POSTGRESQL: {"queryid": 123, "dbid": 1, "userid": 10, "toplevel": 1},
        DatabaseType.MYSQL: {"digest": "abc", "schema_name": "sakila"},
        DatabaseType.ORACLE: {"sql_id": "abc123", "child_number": 2, "inst_id": 1},
    }[engine]
    return NativeSQLIdentity(engine, IdentityKind.STATEMENT, fields)


def _result(engine: DatabaseType = DatabaseType.POSTGRESQL, **changes: object) -> TopSQLResult:
    defaults: dict[str, object] = {
        "database_id": "lab-db", "database_engine": engine, "database_version": "16",
        "profile_id": "pg16", "collection_timestamp": STAMP, "ranking_metric": "total_exec_time",
        "ranking_direction": RankingDirection.DESCENDING, "metric_name": "elapsed_time",
        "metric_unit": "milliseconds", "metric_semantics": MetricSemantics.CUMULATIVE,
        "metric_scope": MetricScope.SQL_STATEMENT, "evidence_source": "pg_stat_statements",
        "data_visibility": DataVisibility.COMPLETE,
        "completeness": Completeness.COMPLETE, "collection_status": CollectionStatus.COLLECTED,
        "items": (TopSQLItem(_identity(engine), 0, rank=1),), "limitations": (),
    }
    defaults.update(changes)
    return TopSQLResult(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize("engine", list(DatabaseType)[:4])
def test_native_identity_fields_preserve_engine_specific_scope(engine: DatabaseType) -> None:
    identity = _identity(engine).to_dict()
    assert identity["database_engine"] == engine.value
    assert identity["fields"]
    assert "sql_text" not in identity["fields"]


def test_mysql_request_identity_is_not_digest_identity() -> None:
    digest = NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT,
                               {"digest": "abc", "schema_name": "sakila"})
    request = NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.REQUEST,
                                {"processlist_id": 41, "thread_id": 91})
    assert digest.kind is IdentityKind.STATEMENT
    assert "thread_id" not in digest.fields
    assert "digest" not in request.fields


def test_mysql_digest_identity_preserves_known_null_schema_only_for_schema_field() -> None:
    identity = NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT,
                                 {"digest": "abc", "schema_name": None})
    assert identity.to_dict()["fields"] == {"digest": "abc", "schema_name": None}
    with pytest.raises(ValueError, match="Null native identity"):
        NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT,
                          {"digest": None, "schema_name": "sakila"})


def test_postgresql_statement_and_backend_identities_are_separate() -> None:
    statement = NativeSQLIdentity(DatabaseType.POSTGRESQL, IdentityKind.STATEMENT,
                                  {"queryid": 4, "dbid": 1, "userid": 2, "toplevel": 1})
    backend = NativeSQLIdentity(DatabaseType.POSTGRESQL, IdentityKind.REQUEST,
                                {"pid": 90, "backend_start": "2026-10-21T11:00:00+00:00"})
    assert statement.kind != backend.kind
    assert "pid" not in statement.fields
    assert statement.fields["toplevel"] == 1


def test_oracle_identity_retains_child_and_instance_and_sqlserver_offsets() -> None:
    oracle = _identity(DatabaseType.ORACLE).fields
    sqlserver = _identity(DatabaseType.SQLSERVER).fields
    assert oracle["child_number"] == 2 and oracle["inst_id"] == 1
    sqlserver_plan = NativeSQLIdentity(DatabaseType.SQLSERVER, IdentityKind.PLAN,
                                       {"plan_handle": "0x02", "query_plan_hash": "0x04", "database_id": 7})
    oracle_plan = NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.PLAN,
                                    {"sql_id": "abc123", "child_number": 2,
                                     "plan_hash_value": 90, "inst_id": 1})
    assert sqlserver_plan.fields["plan_handle"] and oracle_plan.fields["plan_hash_value"] == 90
    assert sqlserver["statement_start_offset"] == 0
    assert sqlserver["statement_end_offset"] == 20


@pytest.mark.parametrize("semantics", list(MetricSemantics))
def test_metric_semantics_are_explicit_and_serializable(semantics: MetricSemantics) -> None:
    result = _result(metric_semantics=semantics)
    assert json.loads(render_json(result))["metric_semantics"] == semantics.value


@pytest.mark.parametrize(("metric", "unit"), [
    ("cpu_time", "microseconds"), ("elapsed_time", "milliseconds"),
    ("logical_reads", "pages"), ("physical_reads", "reads"),
    ("buffer_gets", "blocks"), ("rows_examined", "rows"),
    ("execution_count", "executions"), ("execution_rate", "executions/second"),
])
def test_metric_name_and_native_unit_are_preserved_without_conversion(metric: str, unit: str) -> None:
    result = _result(metric_name=metric, metric_unit=unit, ranking_metric=metric)
    payload = result.to_dict()
    assert payload["metric_name"] == metric
    assert payload["metric_unit"] == unit


def test_zero_is_observed_but_unavailable_is_null_not_zero() -> None:
    assert _result().items[0].metric_value == 0
    missing = TopSQLItem(_identity(DatabaseType.POSTGRESQL), None,
                         MetricValueStatus.PERMISSION_DENIED)
    result = _result(collection_status=CollectionStatus.PERMISSION_DENIED,
                     completeness=Completeness.PARTIAL, data_visibility=DataVisibility.PARTIAL,
                     items=(), limitations=(Limitation.PERMISSION_DENIED,))
    assert missing.metric_value is None and missing.rank is None
    assert result.to_dict()["items"] == []
    with pytest.raises(ValueError):
        TopSQLItem(_identity(DatabaseType.POSTGRESQL), 0, MetricValueStatus.NOT_COLLECTED)


def test_unavailable_and_empty_collection_states_are_distinct() -> None:
    empty = _result(items=())
    unconfigured = _result(collection_status=CollectionStatus.SOURCE_NOT_CONFIGURED,
                           completeness=Completeness.UNKNOWN, data_visibility=DataVisibility.NOT_CONFIGURED,
                           items=(),
                           limitations=(Limitation.SOURCE_NOT_CONFIGURED,))
    assert empty.collection_status is CollectionStatus.COLLECTED
    assert unconfigured.collection_status is CollectionStatus.SOURCE_NOT_CONFIGURED


def test_comparability_requires_same_database_metric_unit_semantics_and_scope() -> None:
    base = _result()
    assert base.is_comparable_to(_result())
    assert not base.is_comparable_to(_result(metric_unit="microseconds"))
    assert not base.is_comparable_to(_result(metric_semantics=MetricSemantics.INTERVAL_DELTA))
    assert not base.is_comparable_to(_result(metric_scope=MetricScope.DATABASE))
    assert not base.is_comparable_to(_result(collection_scope=CollectionScope.INSTANCE))
    assert not base.is_comparable_to(_result(database_engine=DatabaseType.MYSQL,
                                              items=(TopSQLItem(_identity(DatabaseType.MYSQL), 0, rank=1),)))


def test_ranking_direction_and_native_identity_are_validated() -> None:
    with pytest.raises(ValueError, match="sorted"):
        _result(items=(TopSQLItem(_identity(DatabaseType.POSTGRESQL), 1, rank=1),
                       TopSQLItem(_identity(DatabaseType.POSTGRESQL), 9, rank=2)))
    with pytest.raises(ValueError, match="native identity"):
        NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT,
                          {"digest": "abc", "thread_id": 4})
    with pytest.raises(ValueError):
        TopSQLItem(_identity(DatabaseType.POSTGRESQL), float("nan"))
    with pytest.raises(ValueError, match="incomplete"):
        NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT, {"schema_name": "sakila"})
    with pytest.raises(ValueError, match="four relational"):
        _result(database_engine=DatabaseType.MONGODB, items=())


def test_cli_json_and_html_show_contract_status_without_sql_text() -> None:
    result = _result(collection_scope=CollectionScope.INSTANCE)
    payload = json.loads(render_json(result))
    cli = render_human("top-sql", result)
    assert payload["metric_unit"] == "milliseconds"
    assert payload["metric_scope"] == "SQL_STATEMENT"
    assert payload["collection_scope"] == "INSTANCE"
    assert "Collection Scope" in cli and "INSTANCE" in cli
    assert "CUMULATIVE" in cli and "pg_stat_statements" in cli and "0" in cli

    report = _report()
    report_with_top = replace(report, details={**report.details, "top_sql": result.to_dict()})
    summary = build_incident_summary(report_with_top, "reports/slow.html")
    page = HtmlReportRenderer().render(build_report_model(report_with_top, summary))
    assert "Top SQL 资源排序" in page and "pg_stat_statements" in page
    assert "queryid=123" in page
    assert "elapsed_time" in page
    assert "Collection Scope" in page and "INSTANCE" in page


def test_legacy_top_sql_report_without_collection_scope_remains_renderable() -> None:
    legacy_payload = _result().to_dict()
    legacy_payload.pop("collection_scope")
    report = _report()
    report_with_top = replace(report, details={**report.details, "top_sql": legacy_payload})
    page = HtmlReportRenderer().render(build_report_model(
        report_with_top, build_incident_summary(report_with_top, "reports/slow.html")))
    cli = render_human("top-sql", legacy_payload)
    assert "Top SQL 资源排序" in page
    assert "Top SQL" in cli and "Collection Scope" in cli and "UNKNOWN" in cli


def test_html_omits_top_sql_section_when_contract_absent_and_never_renders_sql_text() -> None:
    report = _report()
    summary = build_incident_summary(report, "reports/slow.html")
    page = HtmlReportRenderer().render(build_report_model(report, summary))
    assert "Top SQL 资源排序" not in page

    poisoned = _result(items=(TopSQLItem(_identity(DatabaseType.POSTGRESQL), 3, rank=1),))
    details = poisoned.to_dict()
    details["items"][0]["sql_text"] = "SECRET SQL MUST NOT APPEAR"
    report_with_top = replace(report, details={**report.details, "top_sql": details})
    page = HtmlReportRenderer().render(build_report_model(
        report_with_top, build_incident_summary(report_with_top, "reports/slow.html")))
    assert "SECRET SQL MUST NOT APPEAR" not in page


def test_collection_limitations_and_reset_epoch_are_preserved() -> None:
    result = _result(completeness=Completeness.PARTIAL,
                     data_visibility=DataVisibility.COMPLETE,
                     limitations=(Limitation.TRUNCATED, Limitation.RESET_SUSPECTED,
                                  Limitation.CACHE_EVICTED))
    payload = result.to_dict()
    assert payload["completeness"] == "PARTIAL"
    assert payload["data_visibility"] == "COMPLETE"
    assert payload["limitations"] == ["TRUNCATED", "RESET_SUSPECTED", "CACHE_EVICTED"]
