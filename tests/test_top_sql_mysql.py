from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from test_mysql import INSTANCE, FakeDriver, MemoryCredentialProvider
from test_mysql import adapter as mysql_adapter
from test_sqlserver import ROOT

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.mysql import MYSQL_TOP_SQL_METRICS, MySQLAdapter
from dba.presentation import render_human, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.top_sql import (
    CollectionScope,
    CollectionStatus,
    Completeness,
    DataVisibility,
    Limitation,
    MetricScope,
    MetricSemantics,
    RankingDirection,
)
from dba.top_sql_collectors import (
    MYSQL_METRICS,
    MYSQL_TIMER_METRICS,
    TOP_LIMIT,
    MySQLTopSQLCollector,
)
from dba.top_sql_profiles import (
    MYSQL_PARSER_CONTRACT_ID,
    MYSQL_SQL_PROFILE_ID,
    MYSQL_SUPPORTED_FAMILIES,
    validate_top_sql_profiles,
)
from dba.top_sql_report import TopSQLDiagnosticReport
from dba.versioning import PROFILE_BINDINGS, PROFILE_RESOLVER, DetectedDatabaseVersion, SupportState


class FakeTopSQLAdapter:
    instance = INSTANCE
    registry = SqlScriptRegistry(ROOT / "sql")

    def __init__(self, *, rows: list[dict[str, Any]] | None = None,
                 source: dict[str, Any] | None = None,
                 info: dict[str, Any] | None = None,
                 source_success: bool = True, source_error: str | None = None,
                 info_success: bool = True, info_error: str | None = None,
                 data_success: bool = True, data_error: str | None = None,
                 data_truncated: bool = False) -> None:
        self.rows = list(rows or [])
        self.source = {**_source(), **(source or {})}
        self.info = {**_info(), **(info or {})}
        self.source_success = source_success
        self.source_error = source_error
        self.info_success = info_success
        self.info_error = info_error
        self.data_success = data_success
        self.data_error = data_error
        self.data_truncated = data_truncated
        self.executed: list[str] = []
        self.parameters: tuple[str, int] | None = None

    def execute_readonly(self, script: Any) -> CollectorResult:
        self.executed.append(script.metadata.name)
        if script.metadata.name == "top_sql_source":
            return _result("top_sql_source", [self.source], success=self.source_success,
                           error=self.source_error)
        if script.metadata.name == "top_sql_info":
            return _result("top_sql_info", [self.info], success=self.info_success,
                           error=self.info_error)
        raise AssertionError(f"Unexpected MySQL Top SQL probe: {script.metadata.name}")

    def execute_top_sql_readonly(self, script: Any, top_n: int, metric: str) -> CollectorResult:
        self.executed.append(script.metadata.name)
        self.parameters = (metric, top_n)
        return _result("top_sql", self.rows, success=self.data_success,
                       error=self.data_error, truncated=self.data_truncated)


def _profile(family: str = "8.4") -> Any:
    raw = {"5.7": "5.7.44", "8.0": "8.0.44", "8.4": "8.4.1"}[family]
    detected = PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, family,
        DetectedDatabaseVersion(raw, family, "8022_plus" if family == "8.0" else None),
        "top-sql",
    )
    return detected


def _source(**changes: Any) -> dict[str, Any]:
    return {
        "performance_schema_enabled": 1,
        "statements_digest_enabled": 1,
        "statement_instrument_count": 3,
        "statement_instrument_disabled_count": 0,
        "statement_instrument_untimed_count": 0,
        "digest_table_available": 1,
        **changes,
    }


def _info(**changes: Any) -> dict[str, Any]:
    return {"digest_lost": "0", "digest_null_bucket_present": 0, **changes}


def _row(value: Any, *, digest: str | None = "ab" * 32,
         schema_name: str | None = "app") -> dict[str, Any]:
    return {"schema_name": schema_name, "digest": digest, "ranking_value": value}


def _result(name: str, rows: list[dict[str, Any]], *, success: bool = True,
            error: str | None = None, truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult(INSTANCE.id, name, now, now, 1.0, tuple(rows), len(rows),
                           truncated, success, error)


def _collect(adapter: FakeTopSQLAdapter, metric: str = "sum_timer_wait",
             top_n: int = 10) -> Any:
    collector = MySQLTopSQLCollector(cast(MySQLAdapter, adapter), _profile())
    return collector.collect(INSTANCE, metric, top_n)


def test_mysql_top_sql_profiles_cover_57_80_variants_and_84() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    routes = [binding for binding in PROFILE_BINDINGS
              if binding.db_type == DatabaseType.MYSQL and binding.feature == "top-sql"]
    assert validate_top_sql_profiles(registry, PROFILE_BINDINGS) == ()
    assert {(item.version_family, item.runtime_variant) for item in routes} == {
        ("5.7", "default"), ("8.0", "pre_8022"), ("8.0", "8022_plus"), ("8.4", "default")
    }
    assert {item.version_family for item in routes} == MYSQL_SUPPORTED_FAMILIES == {"5.7", "8.0", "8.4"}
    assert all(item.support_state is SupportState.SUPPORTED for item in routes)
    assert all(item.sql_profile_id == MYSQL_SQL_PROFILE_ID
               and item.parser_contract_id == MYSQL_PARSER_CONTRACT_ID for item in routes)


def test_mysql_80_patch_routes_share_only_common_metric_sql() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    script = registry.get("top_sql", DatabaseType.MYSQL)
    profiles = [PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, "8.0", DetectedDatabaseVersion(version, "8.0", variant), "top-sql",
    ) for version, variant in (("8.0.27", "pre_8022"), ("8.0.28", "8022_plus"),
                               ("8.0.31", "8022_plus"))]
    assert all(profile.sql_profile_id == MYSQL_SQL_PROFILE_ID for profile in profiles)
    assert all(profile.support_state is SupportState.SUPPORTED for profile in profiles)
    assert "SUM_CPU_TIME" not in script.text and "SUM_MEMORY" not in script.text
    assert set(MYSQL_METRICS) == MYSQL_TOP_SQL_METRICS


def test_mysql_top_sql_registry_is_bounded_shared_and_57_compatible() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    scripts = {name: registry.get(name, DatabaseType.MYSQL)
               for name in ("top_sql_source", "top_sql_info", "top_sql")}
    assert all(script.metadata.feature == "top-sql" and script.metadata.reviewed
               and script.metadata.risk == "READ_ONLY" for script in scripts.values())
    assert scripts["top_sql"].metadata.max_rows == TOP_LIMIT + 1 == 101
    assert scripts["top_sql"].metadata.version_families == ("5.7", "8.0", "8.4")
    assert set(MYSQL_TOP_SQL_METRICS) == set(MYSQL_METRICS)
    sql = scripts["top_sql"].text
    assert sql.count("%s") == 2 and "LIMIT %s" in sql
    assert "DIGEST IS NOT NULL" in sql
    assert "ORDER BY ranked.ranking_value DESC" in sql
    assert all(field in sql for field in (
        "SUM_TIMER_WAIT", "AVG_TIMER_WAIT", "COUNT_STAR", "SUM_ROWS_EXAMINED",
        "SUM_ROWS_SENT", "SUM_CREATED_TMP_DISK_TABLES", "SUM_NO_INDEX_USED",
    ))
    assert "SUM_CPU_TIME" not in sql and "MEMORY" not in sql and "DIGEST_TEXT" not in sql
    assert "WITH " not in sql.upper() and "ROW_NUMBER" not in sql.upper()
    assert "statements_digest" in scripts["top_sql_source"].text
    assert "setup_instruments" in scripts["top_sql_source"].text
    assert "Performance_schema_digest_lost" in scripts["top_sql_info"].text
    assert "DIGEST IS NULL" in scripts["top_sql_info"].text
    assert not {"top_sql", "top_sql_source", "top_sql_info"}.intersection(MySQLAdapter.REQUIRED)


@pytest.mark.parametrize("metric,expected_unit", [
    ("sum_timer_wait", "milliseconds"),
    ("avg_timer_wait", "milliseconds/execution"),
    ("count_star", "executions"),
    ("sum_rows_examined", "rows"),
    ("sum_rows_sent", "rows"),
    ("sum_created_tmp_disk_tables", "tables"),
    ("sum_no_index_used", "executions"),
])
def test_mysql_top_sql_metric_contract(metric: str, expected_unit: str) -> None:
    adapter = FakeTopSQLAdapter(rows=[_row("2000000000" if metric in MYSQL_TIMER_METRICS else 2)])
    result = _collect(adapter, metric, top_n=7)
    expected_value = 2 if metric in MYSQL_TIMER_METRICS else 2
    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.metric_name == metric and result.metric_unit == expected_unit
    assert result.metric_semantics is MetricSemantics.CUMULATIVE
    assert result.metric_scope is MetricScope.DIGEST
    assert result.collection_scope is CollectionScope.INSTANCE
    assert result.ranking_direction is RankingDirection.DESCENDING
    assert result.items[0].metric_value == expected_value
    assert adapter.parameters == (metric, 7)


@pytest.mark.parametrize("metric,raw,expected", [
    ("sum_timer_wait", Decimal("1500000000"), 1.5),
    ("avg_timer_wait", "500000000", 0.5),
    ("count_star", 12, 12),
    ("sum_rows_examined", "42", 42),
])
def test_mysql_top_sql_converts_picoseconds_only_for_timer_metrics(
    metric: str, raw: Any, expected: int | float,
) -> None:
    result = _collect(FakeTopSQLAdapter(rows=[_row(raw)]), metric)
    assert result.items[0].metric_value == expected
    assert (result.metric_unit.startswith("milliseconds")) is (metric in MYSQL_TIMER_METRICS)


def test_mysql_top_sql_keeps_equal_digest_rows_separate_by_schema_and_allows_null_schema() -> None:
    adapter = FakeTopSQLAdapter(rows=[
        _row(30, digest="same", schema_name="app_a"),
        _row(20, digest="same", schema_name="app_b"),
        _row(10, digest="null_schema", schema_name=None),
    ])
    result = _collect(adapter, "count_star")
    assert [item.sql_identity.fields for item in result.items if item.sql_identity] == [
        {"schema_name": "app_a", "digest": "same"},
        {"schema_name": "app_b", "digest": "same"},
        {"digest": "null_schema", "schema_name": None},
    ]
    assert result.completeness is Completeness.COMPLETE


def test_mysql_top_sql_digest_null_bucket_is_excluded_and_marks_coverage_partial() -> None:
    result = _collect(FakeTopSQLAdapter(
        rows=[_row(10, digest=None)], info={"digest_lost": "2", "digest_null_bucket_present": 1},
    ))
    assert result.items == ()
    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.completeness is Completeness.PARTIAL
    assert result.source_metadata["digest_lost"] == 2
    assert result.source_metadata["digest_null_bucket_present"] is True
    assert Limitation.IDENTITY_AMBIGUOUS in result.limitations
    assert Limitation.SOURCE_COVERAGE_INCOMPLETE in result.limitations
    assert Limitation.CACHE_EVICTED not in result.limitations


@pytest.mark.parametrize("digest_lost,expected_partial", [("0", False), ("5", True)])
def test_mysql_digest_lost_counter_controls_coverage_without_cache_eviction(
    digest_lost: str, expected_partial: bool,
) -> None:
    result = _collect(FakeTopSQLAdapter(rows=[_row(5)], info={"digest_lost": digest_lost}))
    assert result.source_metadata["digest_lost"] == int(digest_lost)
    assert (result.completeness is Completeness.PARTIAL) is expected_partial
    assert (Limitation.SOURCE_COVERAGE_INCOMPLETE in result.limitations) is expected_partial
    assert Limitation.CACHE_EVICTED not in result.limitations


def test_mysql_missing_digest_loss_metadata_degrades_without_fabricating_zero() -> None:
    result = _collect(FakeTopSQLAdapter(rows=[], info={"digest_lost": None}))
    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.items == ()
    assert result.completeness is Completeness.PARTIAL
    assert result.source_metadata["digest_lost"] is None
    assert Limitation.SOURCE_UNAVAILABLE in result.limitations
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result.limitations


def test_mysql_invalid_digest_loss_metadata_is_not_coerced_to_zero() -> None:
    result = _collect(FakeTopSQLAdapter(rows=[], info={"digest_lost": "not-a-counter"}))
    assert result.source_metadata["digest_lost"] is None
    assert result.completeness is Completeness.PARTIAL
    assert Limitation.SOURCE_UNAVAILABLE in result.limitations


def test_mysql_source_permission_failure_is_not_a_successful_empty_result() -> None:
    adapter = FakeTopSQLAdapter(source_success=False, source_error="QueryDenied")
    result = _collect(adapter)
    assert result.collection_status is CollectionStatus.PERMISSION_DENIED
    assert result.completeness is Completeness.UNKNOWN
    assert "top_sql" not in adapter.executed


def test_mysql_info_permission_failure_keeps_data_partial_and_reports_permission_gap() -> None:
    result = _collect(FakeTopSQLAdapter(
        rows=[_row(8)], info_success=False, info_error="QueryDenied",
    ))
    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.completeness is Completeness.PARTIAL
    assert Limitation.PERMISSION_DENIED in result.limitations


@pytest.mark.parametrize("source_change,expected", [
    ({"performance_schema_enabled": 0}, CollectionStatus.SOURCE_NOT_CONFIGURED),
    ({"statements_digest_enabled": 0}, CollectionStatus.SOURCE_NOT_CONFIGURED),
    ({"digest_table_available": 0}, CollectionStatus.SOURCE_UNAVAILABLE),
    ({"statements_digest_enabled": None}, CollectionStatus.SOURCE_UNAVAILABLE),
])
def test_mysql_unconfigured_or_unavailable_source_never_becomes_complete_empty(
    source_change: dict[str, Any], expected: CollectionStatus,
) -> None:
    adapter = FakeTopSQLAdapter(source=source_change, rows=[])
    result = _collect(adapter)
    assert result.collection_status is expected
    assert result.data_visibility in {DataVisibility.NOT_CONFIGURED, DataVisibility.UNAVAILABLE}
    assert result.completeness is not Completeness.COMPLETE
    assert "top_sql" not in adapter.executed


def test_mysql_statement_instrument_coverage_is_metric_aware() -> None:
    timer = _collect(FakeTopSQLAdapter(rows=[_row(10)], source={"statement_instrument_untimed_count": 1}))
    counter = _collect(FakeTopSQLAdapter(rows=[_row(10)], source={"statement_instrument_untimed_count": 1}),
                       "count_star")
    disabled = _collect(FakeTopSQLAdapter(rows=[_row(10)], source={"statement_instrument_disabled_count": 1}))
    all_disabled = _collect(FakeTopSQLAdapter(
        rows=[], source={"statement_instrument_count": 3, "statement_instrument_disabled_count": 3},
    ))
    assert timer.completeness is Completeness.PARTIAL
    assert Limitation.SOURCE_COVERAGE_INCOMPLETE in timer.limitations
    assert counter.completeness is Completeness.COMPLETE
    assert disabled.completeness is Completeness.PARTIAL
    assert all_disabled.collection_status is CollectionStatus.SOURCE_NOT_CONFIGURED


def test_mysql_confirmed_empty_digest_source_is_complete_but_epoch_is_unknown() -> None:
    result = _collect(FakeTopSQLAdapter(rows=[]))
    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.completeness is Completeness.COMPLETE
    assert result.data_visibility is DataVisibility.COMPLETE
    assert result.items == ()
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result.limitations


def test_mysql_top_sql_limit_and_order_are_enforced() -> None:
    adapter = FakeTopSQLAdapter(rows=[_row("20"), _row("10", digest="cd" * 32)])
    result = _collect(adapter, "count_star", top_n=TOP_LIMIT)
    assert len(result.items) == 2 and adapter.parameters == ("count_star", TOP_LIMIT)
    with pytest.raises(ToolkitError, match="between 1 and"):
        _collect(FakeTopSQLAdapter(), top_n=TOP_LIMIT + 1)
    with pytest.raises(ToolkitError, match="rank order"):
        _collect(FakeTopSQLAdapter(rows=[_row("10"), _row("20", digest="cd" * 32)]), "count_star")


def test_mysql_top_sql_equal_values_follow_schema_then_digest_stable_order() -> None:
    rows = [
        _row(10, digest="bb", schema_name="z_schema"),
        _row(10, digest="aa", schema_name="a_schema"),
    ]
    with pytest.raises(ToolkitError, match="tie order"):
        _collect(FakeTopSQLAdapter(rows=rows), "count_star")
    result = _collect(FakeTopSQLAdapter(rows=list(reversed(rows))), "count_star")
    assert [item.sql_identity.fields for item in result.items if item.sql_identity] == [
        {"schema_name": "a_schema", "digest": "aa"},
        {"schema_name": "z_schema", "digest": "bb"},
    ]


def test_mysql_top_sql_truncated_fetch_is_partial() -> None:
    result = _collect(FakeTopSQLAdapter(rows=[_row(10)], data_truncated=True))
    assert result.completeness is Completeness.PARTIAL
    assert Limitation.TRUNCATED in result.limitations


def test_mysql_top_sql_same_result_drives_json_human_and_offline_html() -> None:
    adapter = FakeTopSQLAdapter(rows=[_row(2500000000, digest="a1" * 32)])
    top_result = _collect(adapter)
    payload = top_result.to_dict()
    json_payload = json.loads(render_json(payload))
    human = render_human("top-sql", payload)
    report = TopSQLDiagnosticReport(INSTANCE, top_result.collection_timestamp, "top-sql", (), {
        "version": top_result.database_version,
        "top_sql": payload,
    })
    html = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    assert json_payload["items"] == payload["items"]
    assert "digest=a1" in human and "2.5" in human and "累计统计" in human
    assert "digest=a1" in html and "2.5" in html and "sum_timer_wait" in html
    assert "DIGEST_TEXT" not in html and "SELECT " not in html


def test_mysql_top_sql_cli_default_and_json_html_route(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "mysql84", "name": "MySQL Test", "db_type": "mysql", "host": "mysql.invalid",
        "port": 3306, "database": "test", "credential_alias": "monitor",
        "environment": "development", "version_family": "8.4",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["preflight"] = [{**driver.connection.results["preflight"][0],
                                                 "server_version": "8.4.1"}]
    driver.connection.results["version_detect"] = [{"raw_version": "8.4.1"}]
    driver.connection.results["top_sql_source"] = [_source()]
    driver.connection.results["top_sql_info"] = [_info()]
    driver.connection.results["top_sql"] = [_row(1000000000, digest="de" * 32)]
    report = tmp_path / "top-sql.html"
    assert main([
        "--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
        "diagnose", "mysql84", "top-sql", "--top", "4", "--report", str(report), "--json",
    ], provider=MemoryCredentialProvider(), driver=driver) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["top_sql"]["ranking_metric"] == "sum_timer_wait"
    assert payload["top_sql"]["collection_status"] == "COLLECTED"
    assert payload["top_sql"]["items"][0]["sql_identity"]["fields"]["digest"] == "de" * 32
    assert driver.connection.executed_parameters[-1] == ("sum_timer_wait", 4)
    assert "sum_timer_wait" in report.read_text(encoding="utf-8")


def test_mysql_adapter_uses_only_dedicated_top_sql_binding_and_preserves_recent_binding() -> None:
    target, driver = mysql_adapter()
    target.connect()
    script = target.registry.get("top_sql", DatabaseType.MYSQL)
    driver.connection.results["top_sql"] = [_row(9)]
    result = target.execute_top_sql_readonly(script, 7, "count_star")
    assert result.success
    assert driver.connection.executed_parameters[-1] == ("count_star", 7)
    before = len(driver.connection.executed)
    with pytest.raises(ToolkitError, match="validated metric and limit"):
        target.execute_readonly(script)
    with pytest.raises(ToolkitError) as caught:
        target.execute_top_sql_readonly(script, TOP_LIMIT + 1, "count_star")
    assert caught.value.error_type == "InvalidParameters"
    with pytest.raises(ToolkitError) as caught:
        target.execute_top_sql_readonly(script, 5, "count_star; DROP TABLE x")
    assert caught.value.error_type == "InvalidParameters"
    assert len(driver.connection.executed) == before
    target.disconnect()
