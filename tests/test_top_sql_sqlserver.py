from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

import pytest
import yaml

from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.sqlserver import SQLServerAdapter
from dba.top_sql import CollectionScope, CollectionStatus, Completeness, DataVisibility, Limitation
from dba.top_sql_collectors import METRICS, SQLServerTopSQLCollector
from dba.top_sql_profiles import SQL_PROFILE_ID, validate_top_sql_profiles
from dba.top_sql_report import TopSQLDiagnosticReport
from dba.versioning import (
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    ResolvedDiagnosticProfile,
    SupportState,
)
from tests.test_html_reports import _report
from tests.test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider


@pytest.fixture
def driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def adapter(driver: FakeDriver) -> SQLServerAdapter:
    instance = DatabaseInstance("test", "Test", DatabaseType.SQLSERVER, "test.invalid", 1433,
                                "monitor-alias", database="master", version_family="2022")
    return SQLServerAdapter(instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                            driver=cast(Any, driver))


def profile(family: str = "2022") -> ResolvedDiagnosticProfile:
    version = "16.0.4305.5" if family == "2022" else ("17.0.1" if family == "2025" else "15.0.1")
    return PROFILE_RESOLVER.resolve(DatabaseType.SQLSERVER, family,
        DetectedDatabaseVersion(version, family), "top-sql")


def row(**changes: Any) -> dict[str, Any]:
    return {
        "sql_handle": "0x01", "plan_handle": "0x02", "query_hash": "0x03",
        "query_plan_hash": "0x04", "plan_generation_num": 2,
        "statement_start_offset": 0, "statement_end_offset": 42, "database_id": 7,
        "database_name": "appdb", "sql_text_available": 1, "total_cpu_time": 1000, "total_logical_reads": 120,
        "total_physical_reads": 8, "execution_count": 4, "total_elapsed_time": 1600,
        "average_cpu_time": "250.0000", "average_elapsed_time": "400.0000",
        "ranking_value": "1000.0000", **changes,
    }


def test_sqlserver_top_sql_routes_are_explicit_and_2025_unverified() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_top_sql_profiles(registry, PROFILE_BINDINGS) == ()
    routes = {binding.version_family: binding for binding in PROFILE_BINDINGS
              if binding.db_type == DatabaseType.SQLSERVER and binding.feature == "top-sql"}
    assert routes["2008r2"].support_state is SupportState.SUPPORTED
    assert routes["2012"].support_state is SupportState.SUPPORTED
    assert routes["2019"].support_state is SupportState.SUPPORTED
    assert routes["2025"].support_state is SupportState.UNKNOWN
    script = registry.get("top_sql", DatabaseType.SQLSERVER)
    assert script.metadata.max_rows == 101 and script.metadata.timeout_seconds <= 15
    assert set(script.metadata.version_families) == {
        "2008r2", "2012", "2014", "2016", "2017", "2019", "2022"
    }
    assert "sys.dm_exec_query_stats" in script.text
    assert "sys.dm_exec_sql_text" in script.text
    assert "COALESCE(st.dbid, plan_text.dbid) AS database_id" in script.text
    assert "DB_NAME(COALESCE(st.dbid, plan_text.dbid)) END AS database_name" in script.text
    assert "OUTER APPLY sys.dm_exec_sql_text(qs.plan_handle) AS plan_text" in script.text
    assert "query_plan_hash" in script.text and "statement_start_offset" in script.text
    assert "TOP (?)" in script.text and "ORDER BY ranking.ranking_value DESC" in script.text
    assert "sys.dm_exec_query_plan" not in script.text
    assert "plan_generation_num" in script.text


def test_sqlserver_top_sql_registry_has_exact_metric_sources_and_server_ordering() -> None:
    script = SqlScriptRegistry(ROOT / "sql").get("top_sql", DatabaseType.SQLSERVER)
    normalized = " ".join(script.text.split())

    expected_sources = {
        "cpu_time": "CONVERT(decimal(38, 4), qs.total_worker_time)",
        "logical_reads": "CONVERT(decimal(38, 4), qs.total_logical_reads)",
        "physical_reads": "CONVERT(decimal(38, 4), qs.total_physical_reads)",
        "execution_count": "CONVERT(decimal(38, 4), qs.execution_count)",
        "elapsed_time": "CONVERT(decimal(38, 4), qs.total_elapsed_time)",
    }
    for metric, expression in expected_sources.items():
        assert f"WHEN N'{metric}' THEN {expression}" in normalized

    cpu_average = (
        "CONVERT(decimal(38, 4), qs.total_worker_time) "
        "/ NULLIF(qs.execution_count, 0)"
    )
    elapsed_average = (
        "CONVERT(decimal(38, 4), qs.total_elapsed_time) "
        "/ NULLIF(qs.execution_count, 0)"
    )
    assert f"WHEN N'average_cpu_time' THEN {cpu_average}" in normalized
    assert f"WHEN N'average_elapsed_time' THEN {elapsed_average}" in normalized
    assert "AS average_cpu_time" in normalized and "AS average_elapsed_time" in normalized
    assert normalized.startswith("SELECT TOP (?)")
    assert (
        "ORDER BY ranking.ranking_value DESC, qs.sql_handle ASC, "
        "qs.statement_start_offset ASC, qs.statement_end_offset ASC, "
        "qs.plan_handle ASC, qs.plan_generation_num DESC"
    ) in normalized


def test_collector_parameterizes_top_and_returns_all_supported_cumulative_metric_routes(
    adapter: Any, driver: Any,
) -> None:
    adapter.connect()
    driver.connection.results["top_sql"] = [row()]
    for metric, (metric_name, unit) in METRICS.items():
        if metric == "logical_reads":
            driver.connection.results["top_sql"] = [row(ranking_value=120)]
            expected = 120
        elif metric == "physical_reads":
            driver.connection.results["top_sql"] = [row(ranking_value=8)]
            expected = 8
        elif metric == "execution_count":
            driver.connection.results["top_sql"] = [row(ranking_value=4)]
            expected = 4
        elif metric == "elapsed_time":
            driver.connection.results["top_sql"] = [row(ranking_value=1600)]
            expected = 1600
        elif metric == "average_cpu_time":
            driver.connection.results["top_sql"] = [row(ranking_value="250.0000")]
            expected = 250
        elif metric == "average_elapsed_time":
            driver.connection.results["top_sql"] = [row(ranking_value="400.0000")]
            expected = 400
        else:
            expected = 1000
        result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance, metric, 5)
        assert result.collection_status is CollectionStatus.COLLECTED
        assert result.completeness is Completeness.COMPLETE
        assert result.data_visibility is DataVisibility.COMPLETE
        assert result.ranking_metric == metric and result.metric_name == metric_name
        assert result.metric_unit == unit and result.metric_semantics.value == "CUMULATIVE"
        assert result.items[0].metric_value == expected and result.items[0].rank == 1
        assert result.items[0].sql_identity is not None
        assert result.items[0].sql_identity.fields["sql_handle"] == "0x01"
        assert result.items[0].sql_identity.fields["query_hash"] == "0x03"
        assert result.items[0].sql_identity.fields["database_id"] == 7
        assert result.items[0].database_name == "appdb"
        assert result.items[0].sql_identity.fields["statement_start_offset"] == 0
        assert result.items[0].sql_identity.fields["statement_end_offset"] == 42
        assert result.items[0].plan_identity is not None
        assert result.items[0].plan_identity.fields["plan_handle"] == "0x02"
        assert result.items[0].plan_identity.fields["query_plan_hash"] == "0x04"
        assert result.items[0].plan_identity.fields["plan_generation_num"] == 2
        if metric in {"cpu_time", "elapsed_time", "average_cpu_time", "average_elapsed_time"}:
            assert Limitation.METRIC_RESOLUTION_LIMITED in result.limitations
        if metric == "physical_reads":
            assert Limitation.NOT_DEVICE_IO in result.limitations
        assert driver.connection.bound_parameters[-1] == (5, metric)
    # The shared byte-bounded fetcher requests one row at a time.
    assert driver.connection.fetch_sizes[-2:] == [1, 1]
    assert driver.connection.timeouts[-1] <= 15
    adapter.disconnect()


def test_collector_preserves_distinct_cached_plans_for_the_same_statement(
    adapter: Any, driver: Any,
) -> None:
    driver.connection.results["top_sql"] = [
        row(plan_handle="0x11", query_plan_hash="0x21", plan_generation_num=2,
            ranking_value=100),
        row(plan_handle="0x12", query_plan_hash="0x22", plan_generation_num=3,
            ranking_value=80),
    ]
    adapter.connect()
    result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance, "cpu_time", 2)

    assert len(result.items) == 2
    first_sql = result.items[0].sql_identity
    second_sql = result.items[1].sql_identity
    assert first_sql is not None and second_sql is not None
    assert first_sql.fields == second_sql.fields
    first_plan = result.items[0].plan_identity
    second_plan = result.items[1].plan_identity
    assert first_plan is not None and second_plan is not None
    assert [first_plan.fields["plan_handle"], second_plan.fields["plan_handle"]] == ["0x11", "0x12"]
    assert [first_plan.fields["query_plan_hash"], second_plan.fields["query_plan_hash"]] == ["0x21", "0x22"]
    assert [first_plan.fields["plan_generation_num"], second_plan.fields["plan_generation_num"]] == [2, 3]
    assert [item.metric_value for item in result.items] == [100, 80]
    assert driver.connection.bound_parameters[-1] == (2, "cpu_time")
    adapter.disconnect()


def test_collector_rejects_rows_that_violate_descending_server_rank_order(
    adapter: Any, driver: Any,
) -> None:
    driver.connection.results["top_sql"] = [
        row(ranking_value=80), row(sql_handle="0x03", ranking_value=100),
    ]
    adapter.connect()
    with pytest.raises(ToolkitError, match="not in declared rank order"):
        SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance, "cpu_time", 2)
    adapter.disconnect()


@pytest.mark.parametrize("database_id", [None, "7", 7.0, True])
def test_missing_or_wrong_type_database_id_is_preserved_as_incomplete_identity(
    adapter: Any, driver: Any, database_id: Any,
) -> None:
    driver.connection.results["top_sql"] = [row(database_id=database_id)]
    adapter.connect()

    result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance)

    assert result.collection_status is CollectionStatus.COLLECTED
    assert result.completeness is Completeness.PARTIAL
    assert result.data_visibility is DataVisibility.PARTIAL
    assert Limitation.IDENTITY_AMBIGUOUS in result.limitations
    assert len(result.items) == 1
    assert result.items[0].sql_identity is not None
    assert "database_id" not in result.items[0].sql_identity.fields
    adapter.disconnect()


@pytest.mark.parametrize("top_n", [0, -1, 101, True])
def test_collector_rejects_unbounded_top_n(adapter: Any, top_n: Any) -> None:
    with pytest.raises(ToolkitError, match="between 1 and 100"):
        SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance, "cpu_time", top_n)


def test_adapter_requires_registered_top_sql_script_and_bounded_parameters(adapter: Any) -> None:
    adapter.connect()
    script = adapter.registry.get("top_sql")
    with pytest.raises(ToolkitError, match="controlled parameters"):
        adapter.execute_readonly(script)
    with pytest.raises(ToolkitError, match="limit or ranking metric"):
        adapter.execute_readonly(script, top_sql_parameters=(1000, "cpu_time"))
    with pytest.raises(ToolkitError, match="limit or ranking metric"):
        adapter.execute_readonly(script, top_sql_parameters=(10, "sql injection"))
    adapter.disconnect()


def test_empty_and_partial_cache_results_are_not_fabricated(adapter: Any, driver: Any) -> None:
    adapter.connect()
    driver.connection.results["top_sql"] = []
    empty = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance)
    assert empty.collection_status is CollectionStatus.COLLECTED
    assert empty.completeness is Completeness.COMPLETE and not empty.items

    driver.connection.results["top_sql"] = [row(sql_text_available=0)]
    driver.connection.fetch_sizes[-1:] = []
    collector = SQLServerTopSQLCollector(adapter, profile())
    partial = collector.collect(adapter.instance)
    assert partial.completeness is Completeness.PARTIAL
    assert partial.data_visibility is DataVisibility.PARTIAL
    assert Limitation.SOURCE_UNAVAILABLE in partial.limitations

    driver.connection.results["top_sql"] = [row() for _ in range(101)]
    truncated = collector.collect(adapter.instance)
    assert truncated.completeness is Completeness.PARTIAL
    assert Limitation.TRUNCATED in truncated.limitations
    adapter.disconnect()


def test_missing_native_identity_downgrades_result(adapter: Any, driver: Any) -> None:
    adapter.connect()
    driver.connection.results["top_sql"] = [row(sql_handle=None)]
    result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance)
    assert result.completeness is Completeness.PARTIAL
    assert result.items == ()
    assert Limitation.IDENTITY_AMBIGUOUS in result.limitations
    adapter.disconnect()


def test_query_timeout_is_explicit_and_does_not_create_ranked_rows(adapter: Any, driver: Any) -> None:
    adapter.connect()
    driver.connection.failures["top_sql"] = RuntimeError("HYT00", "query timeout")
    result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance)
    assert result.collection_status is CollectionStatus.FAILED
    assert result.completeness is Completeness.UNKNOWN
    assert result.items == () and Limitation.COLLECTOR_TIMEOUT in result.limitations
    assert result.collection_scope is CollectionScope.INSTANCE


def test_sqlserver_top_sql_collection_scope_is_instance_and_item_scope_is_statement(
    adapter: Any, driver: Any,
) -> None:
    driver.connection.results["top_sql"] = [row()]
    adapter.connect()
    result = SQLServerTopSQLCollector(adapter, profile()).collect(adapter.instance)
    payload = result.to_dict()
    assert payload["collection_scope"] == "INSTANCE"
    assert payload["metric_scope"] == "SQL_STATEMENT"
    assert payload["items"][0]["sql_identity"]["fields"]["database_id"] == 7
    assert payload["items"][0]["database_name"] == "appdb"


def test_sqlserver_top_sql_cli_json_and_html_share_the_contract() -> None:
    instance_report = _report()
    result = {
        "database_id": "lab", "database_engine": "sqlserver", "database_version": "16.0.4305.5",
        "profile_id": SQL_PROFILE_ID, "collection_timestamp": datetime.now(timezone.utc).isoformat(),
        "ranking_metric": "cpu_time", "ranking_direction": "DESCENDING", "metric_name": "total_cpu_time",
        "metric_unit": "microseconds", "metric_semantics": "CUMULATIVE", "metric_scope": "SQL_STATEMENT",
        "collection_scope": "INSTANCE",
        "evidence_source": "sys.dm_exec_query_stats",
        "data_visibility": "COMPLETE", "completeness": "COMPLETE", "collection_status": "COLLECTED",
        "limitations": ["RESET_SUSPECTED", "CACHE_EVICTED"],
        "items": [{"rank": 1, "sql_identity": {"database_engine": "sqlserver", "kind": "STATEMENT",
            "fields": {"sql_handle": "0x01", "statement_start_offset": 0, "statement_end_offset": 42}},
            "plan_identity": None, "request_identity": None, "metric_value": 1000,
            "database_name": "appdb", "value_status": "OBSERVED"}],
    }
    report = TopSQLDiagnosticReport(instance_report.instance, datetime.now(timezone.utc), "top-sql", (),
                                    {"version": "16.0.4305.5", "top_sql": result})
    from dba.incident import build_incident_summary
    incident = build_incident_summary(report)
    assert incident.status == "UNKNOWN" and incident.observed_now == "NOT_ASSESSED"
    model = build_report_model(report, incident)
    html = HtmlReportRenderer().render(model)
    assert "Top SQL" in html and "appdb" in html and "累计统计" in html
    assert "原生身份与执行计划标识" in html and "0x01" in html
    assert "采集范围" in html and "实例级" in html
    assert "SELECT " not in html
    from dba.presentation import render_human
    cli = render_human("top-sql", result)
    assert "total_cpu_time" in cli
    assert "采集范围" in cli and "实例级" in cli
    assert "sqlserver:sql_handle=0x01" in cli
    assert "0x01" in cli and "1000" in cli


def test_top_sql_diagnose_cli_executes_registered_route_and_writes_offline_html(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], driver: FakeDriver,
) -> None:
    from dba.cli import main

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "test", "name": "Test", "db_type": "sqlserver", "host": "test.invalid",
        "port": 1433, "version_family": "2022", "credential_alias": "monitor",
    }]}), encoding="utf-8")
    driver.connection.results["top_sql"] = [row()]
    report_path = tmp_path / "top-sql.html"
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "test", "top-sql", "--metric", "cpu_time", "--top", "7",
            "--report", str(report_path), "--json"]
    from tests.test_sqlserver import MemoryCredentialProvider
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["success"] is True
    assert output["top_sql"]["items"][0]["sql_identity"]["fields"]["sql_handle"] == "0x01"
    assert output["top_sql"]["items"][0]["database_name"] == "appdb"
    assert output["top_sql"]["ranking_metric"] == "cpu_time"
    assert driver.connection.bound_parameters[-1] == (7, "cpu_time")
    html = report_path.read_text(encoding="utf-8")
    assert "Top SQL" in html and "appdb" in html and "microseconds" in html
    assert "原生身份与执行计划标识" in html and "0x01" in html
    assert "SELECT " not in html
    assert driver.connection.closed
