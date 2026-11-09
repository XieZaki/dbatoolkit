from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from test_postgresql import INSTANCE, FakeDriver, adapter, result
from test_sqlserver import MemoryCredentialProvider

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.postgresql import POSTGRESQL_TOP_SQL_METRICS, PostgreSQLAdapter
from dba.presentation import render_human, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.top_sql import (
    CollectionScope,
    CollectionStatus,
    Completeness,
    DataVisibility,
    IdentityKind,
    Limitation,
    NativeSQLIdentity,
)
from dba.top_sql_collectors import (
    METRICS as SQLSERVER_METRICS,
)
from dba.top_sql_collectors import (
    POSTGRESQL_METRICS,
    PostgreSQLTopSQLCollector,
)
from dba.top_sql_profiles import (
    POSTGRESQL_PARSER_CONTRACT_ID,
    POSTGRESQL_SQL_PROFILE_ID,
    POSTGRESQL_SUPPORTED_FAMILIES,
    validate_top_sql_profiles,
)
from dba.top_sql_report import TopSQLDiagnosticReport
from dba.versioning import (
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    SupportState,
)

ROOT = Path(__file__).resolve().parents[1]


def _profile(family: str = "16") -> Any:
    version = f"{family}.4"
    return PROFILE_RESOLVER.resolve(
        DatabaseType.POSTGRESQL, family, DetectedDatabaseVersion(version, family), "top-sql"
    )


def _source(**changes: Any) -> dict[str, Any]:
    return {
        "extension_installed": True,
        "extension_version": "1.11",
        "schema_name": "public",
        "module_preloaded": True,
        "compute_query_id": "auto",
        "tracking_mode": "top",
        "statements_view_available": True,
        "info_view_available": True,
        **changes,
    }


def _row(value: Any, **changes: Any) -> dict[str, Any]:
    return {
        "dbid": 16384,
        "userid": 16385,
        "queryid": 9223372036854775000,
        "toplevel": True,
        "ranking_value": value,
        **changes,
    }


def _adapter_with_results(
    rows: list[dict[str, Any]] | None = None,
    *,
    source: dict[str, Any] | None = None,
    info_rows: list[dict[str, Any]] | None = None,
    data_success: bool = True,
    data_error: str | None = None,
    data_truncated: bool = False,
    info_success: bool = True,
    info_error: str | None = None,
    info_truncated: bool = False,
) -> tuple[Any, PostgreSQLTopSQLCollector]:
    class StubAdapter:
        instance = INSTANCE
        registry = SqlScriptRegistry(ROOT / "sql")

        def __init__(self) -> None:
            self.parameters: tuple[int, str] | None = None
            self.executed: list[str] = []

        def execute_readonly(self, script: Any) -> CollectorResult:
            self.executed.append(script.metadata.name)
            if script.metadata.name == "top_sql_source":
                return result("top_sql_source", [source or _source()])
            if script.metadata.name == "top_sql_info":
                now = datetime.now(timezone.utc)
                return CollectorResult(
                    INSTANCE.id, "top_sql_info", now, now, 1.0,
                    tuple(info_rows if info_rows is not None else [{
                        "stats_reset": "2026-10-01T00:00:00+00:00", "dealloc": 0,
                    }]),
                    len(info_rows if info_rows is not None else [{"stats_reset": "2026-10-01T00:00:00+00:00",
                                                                  "dealloc": 0}]),
                    info_truncated, info_success, info_error,
                )
            raise AssertionError(f"Unexpected PostgreSQL Top SQL probe: {script.metadata.name}")

        def execute_top_sql_readonly(self, script: Any, top_n: int, metric: str) -> CollectorResult:
            self.parameters = (top_n, metric)
            self.executed.append(script.metadata.name)
            now = datetime.now(timezone.utc)
            selected_rows = rows or []
            return CollectorResult(INSTANCE.id, "top_sql", now, now, 1.0,
                                   tuple(selected_rows), len(selected_rows), data_truncated,
                                   data_success, data_error)

    fake = StubAdapter()
    return fake, PostgreSQLTopSQLCollector(cast(PostgreSQLAdapter, fake), _profile())


def test_postgresql_top_sql_profiles_cover_only_14_to_18_and_do_not_change_sqlserver() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_top_sql_profiles(registry, PROFILE_BINDINGS) == ()
    routes = {binding.version_family: binding for binding in PROFILE_BINDINGS
              if binding.db_type == DatabaseType.POSTGRESQL and binding.feature == "top-sql"}
    assert set(routes) == POSTGRESQL_SUPPORTED_FAMILIES == {"14", "15", "16", "17", "18"}
    assert all(item.support_state is SupportState.SUPPORTED for item in routes.values())
    assert "13" not in routes
    assert SQLSERVER_METRICS["cpu_time"] == ("total_cpu_time", "microseconds")
    assert PostgreSQLAdapter.REQUIRED == (
        "preflight", "server_info", "database_status", "active_sessions", "long_running",
        "transactions", "blocking",
    )
    assert not {"top_sql", "top_sql_source", "top_sql_info"}.intersection(PostgreSQLAdapter.REQUIRED)
    assert _profile().sql_profile_id == POSTGRESQL_SQL_PROFILE_ID
    assert _profile().parser_contract_id == POSTGRESQL_PARSER_CONTRACT_ID


def test_postgresql_top_sql_registry_is_bounded_parameterized_and_does_not_capture_query_text() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    scripts = {name: registry.get(name, DatabaseType.POSTGRESQL)
               for name in ("top_sql_source", "top_sql_info", "top_sql")}
    assert scripts["top_sql"].metadata.max_rows == 101
    assert scripts["top_sql"].metadata.version_families == ("14", "15", "16", "17", "18")
    assert all(script.metadata.risk == "READ_ONLY" and script.metadata.reviewed for script in scripts.values())
    sql = scripts["top_sql"].text
    assert sql.count("%s") == 2
    assert "LIMIT (SELECT top_n FROM requested)" in sql
    assert "ORDER BY ranking_value DESC NULLS LAST" in sql
    assert all(field in sql for field in (
        "s.dbid", "s.userid", "s.queryid", "s.toplevel", "s.total_exec_time",
        "s.mean_exec_time", "s.calls", "s.shared_blks_read", "s.shared_blks_hit",
        "s.temp_blks_written", "s.wal_bytes",
    ))
    assert "s.query," not in sql and "s.query " not in sql and "pg_stat_statements_reset" not in sql
    assert "public.pg_stat_statements" in sql
    assert "schema_name" not in sql
    assert "stats_reset" in scripts["top_sql_info"].text and "dealloc" in scripts["top_sql_info"].text


@pytest.mark.parametrize(("metric", "raw", "expected", "unit"), [
    ("total_exec_time", "182.375", 182.375, "milliseconds"),
    ("mean_exec_time", "2.125", 2.125, "milliseconds/execution"),
    ("calls", "123", 123, "executions"),
    ("shared_blks_read", "23", 23, "blocks"),
    ("shared_blks_hit", "90", 90, "blocks"),
    ("temp_blks_written", "4", 4, "blocks"),
    ("wal_bytes", "90071992547409931234567891", 90071992547409931234567891, "bytes"),
])
def test_postgresql_top_sql_metrics_keep_units_and_exact_values(
    metric: str, raw: str, expected: int | float, unit: str,
) -> None:
    assert set(POSTGRESQL_METRICS) == POSTGRESQL_TOP_SQL_METRICS
    fake, collector = _adapter_with_results([_row(raw)])
    result_value = collector.collect(INSTANCE, metric, 1)
    assert fake.parameters == (1, metric)
    assert result_value.ranking_metric == metric
    assert result_value.metric_name == metric
    assert result_value.metric_unit == unit
    assert result_value.metric_semantics.value == "CUMULATIVE"
    assert result_value.items[0].metric_value == expected
    if metric == "wal_bytes":
        assert type(result_value.items[0].metric_value) is int
    if metric == "shared_blks_read":
        assert Limitation.NOT_DEVICE_IO in result_value.limitations


def test_default_metric_is_total_exec_time_and_top_n_is_bound_separately() -> None:
    fake, collector = _adapter_with_results([_row("12.5")])
    result_value = collector.collect(INSTANCE)
    assert fake.parameters == (10, "total_exec_time")
    assert result_value.ranking_metric == "total_exec_time"
    assert result_value.collection_scope is CollectionScope.INSTANCE
    assert result_value.metric_scope.value == "SQL_STATEMENT"
    assert result_value.source_metadata["queryid_stability"] == "NOT_GUARANTEED_ACROSS_MAJOR_VERSIONS"


def test_top_n_maximum_one_hundred_is_bound_without_string_interpolation() -> None:
    fake, collector = _adapter_with_results([_row("1")])
    result_value = collector.collect(INSTANCE, top_n=100)
    assert fake.parameters == (100, "total_exec_time")
    assert len(result_value.items) == 1
    assert "%s" in fake.registry.get("top_sql", DatabaseType.POSTGRESQL).text


def test_identity_preserves_database_user_queryid_and_toplevel_as_integer() -> None:
    fake, collector = _adapter_with_results([
        _row("12", queryid=901, toplevel=True),
        _row("12", dbid=16385, queryid=901, toplevel=False),
    ])
    result_value = collector.collect(INSTANCE, top_n=2)
    identities = [item.sql_identity.fields for item in result_value.items if item.sql_identity]
    assert identities == [
        {"dbid": 16384, "userid": 16385, "queryid": 901, "toplevel": 1},
        {"dbid": 16385, "userid": 16385, "queryid": 901, "toplevel": 0},
    ]
    assert fake.parameters == (2, "total_exec_time")
    assert NativeSQLIdentity(DatabaseType.POSTGRESQL, IdentityKind.STATEMENT, identities[0])
    with pytest.raises(ValueError, match="Native identity values"):
        NativeSQLIdentity(DatabaseType.POSTGRESQL, IdentityKind.STATEMENT,
                          {"queryid": 901, "dbid": 16384, "userid": 16385, "toplevel": True})


def test_missing_query_identity_is_reported_partial_and_not_fabricated() -> None:
    _, collector = _adapter_with_results([_row("12", queryid=None), _row("11", queryid=901)])
    result_value = collector.collect(INSTANCE, top_n=2)
    assert result_value.collection_status is CollectionStatus.COLLECTED
    assert result_value.completeness is Completeness.PARTIAL
    assert result_value.data_visibility is DataVisibility.PARTIAL
    assert Limitation.IDENTITY_AMBIGUOUS in result_value.limitations
    assert result_value.source_metadata["identity_unavailable_rows"] == 1
    assert len(result_value.items) == 1
    identity = result_value.items[0].sql_identity
    assert identity is not None
    assert identity.fields["queryid"] == 901


def test_empty_configured_source_is_a_complete_collected_empty_result() -> None:
    _, collector = _adapter_with_results([])
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.COLLECTED
    assert result_value.completeness is Completeness.COMPLETE
    assert result_value.data_visibility is DataVisibility.COMPLETE
    assert result_value.items == ()


@pytest.mark.parametrize("source", [
    _source(extension_installed=False, extension_version=None, schema_name=None),
    _source(module_preloaded=False),
    _source(tracking_mode="none"),
])
def test_missing_extension_or_preload_returns_source_not_configured(source: dict[str, Any]) -> None:
    fake, collector = _adapter_with_results([_row("1")], source=source)
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.SOURCE_NOT_CONFIGURED
    assert result_value.data_visibility is DataVisibility.NOT_CONFIGURED
    assert result_value.completeness is Completeness.UNKNOWN
    assert fake.executed == ["top_sql_source"]


def test_non_public_extension_schema_fails_closed_without_dynamic_identifier_sql() -> None:
    fake, collector = _adapter_with_results([_row("1")], source=_source(schema_name="custom; DROP SCHEMA public"))
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.SOURCE_UNAVAILABLE
    assert result_value.data_visibility is DataVisibility.UNAVAILABLE
    assert fake.executed == ["top_sql_source"]


def test_missing_info_view_or_info_query_failure_keeps_rows_partial() -> None:
    fake, collector = _adapter_with_results([_row("5")], source=_source(info_view_available=False))
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.COLLECTED
    assert result_value.completeness is Completeness.PARTIAL
    assert result_value.source_metadata["stats_reset"] is None
    assert result_value.source_metadata["dealloc"] is None
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result_value.limitations
    assert Limitation.CACHE_EVICTED not in result_value.limitations
    assert "top_sql_info" not in fake.executed

    fake, collector = _adapter_with_results([_row("5")], info_success=False, info_error="QueryFailed")
    result_value = collector.collect(INSTANCE)
    assert result_value.completeness is Completeness.PARTIAL
    assert Limitation.SOURCE_UNAVAILABLE in result_value.limitations
    assert "top_sql_info" in fake.executed

    _, collector = _adapter_with_results([_row("5")], info_success=False, info_error="QueryDenied")
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.COLLECTED
    assert Limitation.PERMISSION_DENIED in result_value.limitations


@pytest.mark.parametrize(("dealloc", "cache_evicted"), [(0, False), (2, True)])
def test_cache_evicted_limitation_reflects_valid_dealloc_value(dealloc: int, cache_evicted: bool) -> None:
    _, collector = _adapter_with_results(
        [_row("5")], info_rows=[{"stats_reset": "2026-10-01T00:00:00+00:00", "dealloc": dealloc}],
    )
    result_value = collector.collect(INSTANCE)
    assert result_value.source_metadata["dealloc"] == dealloc
    assert (Limitation.CACHE_EVICTED in result_value.limitations) is cache_evicted


def test_missing_dealloc_marks_epoch_partial_without_claiming_cache_eviction() -> None:
    _, collector = _adapter_with_results(
        [_row("5")], info_rows=[{"stats_reset": "2026-10-01T00:00:00+00:00"}],
    )
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is CollectionStatus.COLLECTED
    assert result_value.completeness is Completeness.PARTIAL
    assert result_value.source_metadata["dealloc"] is None
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result_value.limitations
    assert Limitation.CACHE_EVICTED not in result_value.limitations


@pytest.mark.parametrize(("error", "status", "limitation"), [
    ("QueryDenied", CollectionStatus.PERMISSION_DENIED, Limitation.PERMISSION_DENIED),
    ("QueryTimeout", CollectionStatus.FAILED, Limitation.COLLECTOR_TIMEOUT),
    ("QueryFailed", CollectionStatus.FAILED, Limitation.COLLECTION_FAILED),
])
def test_data_query_failures_preserve_access_timeout_and_failure_states(
    error: str, status: CollectionStatus, limitation: Limitation,
) -> None:
    _, collector = _adapter_with_results(data_success=False, data_error=error)
    result_value = collector.collect(INSTANCE)
    assert result_value.collection_status is status
    assert limitation in result_value.limitations


def test_data_truncation_and_unavailable_reset_epoch_are_partial() -> None:
    _, collector = _adapter_with_results([_row("3")], data_truncated=True)
    result_value = collector.collect(INSTANCE)
    assert result_value.completeness is Completeness.PARTIAL
    assert Limitation.TRUNCATED in result_value.limitations

    _, collector = _adapter_with_results([_row("3")], info_rows=[{"stats_reset": None, "dealloc": 0}])
    result_value = collector.collect(INSTANCE)
    assert result_value.completeness is Completeness.PARTIAL
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result_value.limitations


@pytest.mark.parametrize("rows", [
    [_row("2"), _row("3", queryid=901)],
    [_row("2", dbid=16385, userid=16385, queryid=901), _row("2", queryid=900)],
])
def test_invalid_server_ranking_order_is_rejected(rows: list[dict[str, Any]]) -> None:
    _, collector = _adapter_with_results(rows)
    with pytest.raises(ToolkitError, match="rank order|tie order"):
        collector.collect(INSTANCE, top_n=2)


@pytest.mark.parametrize(("metric", "value"), [
    ("calls", "1.5"),
    ("wal_bytes", "1.25"),
    ("total_exec_time", "NaN"),
    ("shared_blks_hit", "-1"),
])
def test_invalid_numeric_values_are_rejected(metric: str, value: str) -> None:
    _, collector = _adapter_with_results([_row(value)])
    with pytest.raises(ToolkitError, match="ranking value|count or byte total"):
        collector.collect(INSTANCE, metric)


@pytest.mark.parametrize("top_n", [0, 101, True, 1.5])
def test_top_n_is_bounded_to_one_through_one_hundred(top_n: Any) -> None:
    _, collector = _adapter_with_results([_row("1")])
    with pytest.raises(ToolkitError, match="Top N"):
        collector.collect(INSTANCE, top_n=top_n)


def test_collector_rejects_engine_specific_metric_from_other_database() -> None:
    _, collector = _adapter_with_results([_row("1")])
    with pytest.raises(ToolkitError, match="Unsupported PostgreSQL"):
        collector.collect(INSTANCE, "cpu_time")


def test_postgresql_adapter_uses_only_top_sql_bound_parameter_interface() -> None:
    target, driver = adapter()
    target.connect()
    script = target.registry.get("top_sql", DatabaseType.POSTGRESQL)
    target.execute_top_sql_readonly(script, 7, "wal_bytes")
    assert driver.connection.bound_parameters[-1] == ("top_sql", (7, "wal_bytes"))
    with pytest.raises(ToolkitError, match="validated parameter binding"):
        target.execute_readonly(script)
    before = len(driver.connection.bound_parameters)
    with pytest.raises(ToolkitError) as error:
        target.execute_top_sql_readonly(script, 101, "wal_bytes")
    assert error.value.error_type == "InvalidParameters"
    with pytest.raises(ToolkitError) as error:
        target.execute_top_sql_readonly(script, 10, "total_exec_time; DROP TABLE x")
    assert error.value.error_type == "InvalidParameters"
    assert len(driver.connection.bound_parameters) == before
    target.disconnect()


def test_postgresql_cli_default_routes_to_shared_json_and_html_top_sql(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg-lab", "name": "PG Lab", "db_type": "postgresql", "host": "pg.invalid",
        "port": 5432, "version_family": "16", "credential_alias": "monitor", "database": "postgres",
        "environment": "development",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["top_sql_source"] = [_source()]
    driver.connection.results["top_sql_info"] = [{"stats_reset": "2026-10-01T00:00:00+00:00", "dealloc": 2}]
    driver.connection.results["top_sql"] = [_row("10.25", queryid=77)]
    report_path = tmp_path / "pg-top-sql.html"
    code = main([
        "--inventory", str(inventory), "--sql-root", str(ROOT / "sql"), "diagnose", "pg-lab", "top-sql",
        "--top", "5", "--report", str(report_path), "--json",
    ], provider=MemoryCredentialProvider(), driver=driver)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["top_sql"]["ranking_metric"] == "total_exec_time"
    assert payload["top_sql"]["collection_status"] == "COLLECTED"
    assert payload["top_sql"]["collection_scope"] == "INSTANCE"
    assert payload["top_sql"]["items"][0]["sql_identity"]["fields"]["toplevel"] == 1
    assert driver.connection.bound_parameters[-1] == ("top_sql", (5, "total_exec_time"))
    html = report_path.read_text(encoding="utf-8")
    assert "Top SQL" in html and "total_exec_time" in html and "Dealloc: 2" in html
    assert "SELECT " not in html


def test_slow_cli_collects_only_current_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg-lab", "name": "PG Lab", "db_type": "postgresql", "host": "pg.invalid",
        "port": 5432, "version_family": "16", "credential_alias": "monitor", "database": "postgres",
        "environment": "development",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["top_sql_source"] = [_source()]
    driver.connection.results["top_sql_info"] = [{"stats_reset": "2026-10-01T00:00:00+00:00", "dealloc": 0}]
    driver.connection.results["top_sql"] = [_row("10.25", queryid=77)]
    common = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql")]

    default_report = tmp_path / "slow-default.html"
    assert main([*common, "diagnose", "pg-lab", "slow", "--format", "json",
                 "--report", str(default_report)],
                provider=MemoryCredentialProvider(), driver=driver) == 0
    default_payload = json.loads(capsys.readouterr().out)
    details = default_payload["report"]["details"]
    assert "top_sql" not in details and "top_sql_integration" not in details
    assert default_payload["success"] is True
    assert {"active_sessions", "blocking", "transactions", "long_running"} <= set(driver.connection.executed)
    assert not any(name.startswith("top_sql") for name in driver.connection.executed)
    assert driver.connection.executed.count("version_detect") == 1
    assert 'id="top-sql"' not in default_report.read_text(encoding="utf-8")


@pytest.mark.parametrize(("options", "rejected_flag"), [
    (["--include-top-sql", "--metric", "total_exec_time", "--top", "5"], "--include-top-sql"),
    (["--metric", "total_exec_time"], "--metric"),
    (["--top", "10"], "--top"),
])
def test_slow_rejects_retired_top_sql_options_before_inventory(
    options: list[str], rejected_flag: str, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--inventory", str(tmp_path / "missing.yaml"),
                 "diagnose", "unused", "slow", *options, "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_type"] == "InvalidArgument"
    assert rejected_flag in output["error_message"]
    assert "dba diagnose <database_id> top-sql" in output["guidance"]

    assert main(["--inventory", str(tmp_path / "missing.yaml"),
                 "diagnose", "unused", "slow", *options]) == 1
    assert "dba diagnose <database_id> top-sql" in capsys.readouterr().out


def test_diagnose_help_explains_ranking_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["diagnose", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--include-top-sql" not in help_text
    assert "--metric" in help_text and "仅用于 top-sql" in help_text
    assert "--top" in help_text and "top-sql 或 active-sql" in help_text


def test_pg_top_sql_one_result_drives_json_cli_and_offline_html() -> None:
    fake, collector = _adapter_with_results([_row("55.5", queryid=89)])
    top_result = collector.collect(INSTANCE)
    payload = top_result.to_dict()
    json_payload = json.loads(render_json(payload))
    cli = render_human("top-sql", payload)
    report = TopSQLDiagnosticReport(INSTANCE, top_result.collection_timestamp, "top-sql", (), {
        "version": top_result.database_version, "top_sql": payload,
    })
    incident = build_incident_summary(report)
    html = HtmlReportRenderer().render(build_report_model(report, incident))
    assert json_payload["items"] == payload["items"]
    assert "queryid=89" in cli and "55.5" in cli and "planning_time_included=False" in cli
    assert json_payload["items"][0]["sql_identity"]["fields"]["queryid"] == 89
    assert "queryid=89" not in html
    assert "<td>55.5</td>" in html and "是否包含规划耗时" in html and "否" in html
    assert fake.parameters == (10, "total_exec_time")
