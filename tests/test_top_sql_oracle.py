from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import MockCredentialProvider, Mode
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.oracle import ORACLE_TOP_SQL_METRICS, OracleAdapter
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
    MetricScope,
    NativeSQLIdentity,
)
from dba.top_sql_collectors import ORACLE_METRICS, OracleTopSQLCollector
from dba.top_sql_profiles import (
    ORACLE_PARSER_CONTRACT_ID,
    ORACLE_SQL_PROFILE_ID,
    ORACLE_SUPPORTED_FAMILIES,
    validate_top_sql_profiles,
)
from dba.top_sql_report import TopSQLDiagnosticReport
from dba.versioning import (
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
)

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = DatabaseInstance(
    "oracle-lab", "Oracle Lab", DatabaseType.ORACLE, "example.invalid", 1521,
    "oracle-monitor", service_name="FREEPDB1", version_family="19c",
)
REGISTRY = SqlScriptRegistry(ROOT / "sql")


def _profile(family: str = "19c", variant: str | None = None) -> Any:
    raw = {
        "11gr2": "11.2.0.4.0", "12c": "12.2.0.1.0", "18c": "18.0.0.0.0",
        "19c": "19.0.0.0.0", "21c": "21.0.0.0.0", "23ai": "23.0.0.0.0",
        "26ai": "23.26.0.0.0",
    }[family]
    runtime_variant = variant
    if family == "12c" and runtime_variant is None:
        runtime_variant = "12_2_plus"
    return PROFILE_RESOLVER.resolve(
        DatabaseType.ORACLE, family,
        DetectedDatabaseVersion(raw, family, runtime_variant), "top-sql",
    )


def _row(sql_id: str, child: int, value: int, *, plan_hash: int | None = 44,
         con_id: int | None = 3) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sql_id": sql_id, "child_number": child, "plan_hash_value": plan_hash,
        "ranking_value": value,
    }
    if con_id is not None:
        row["con_id"] = con_id
    return row


def _result(name: str, rows: list[dict[str, Any]], *, success: bool = True,
            error: str | None = None, truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult(INSTANCE.id, name, now, now, 1.0, tuple(rows), len(rows),
                           truncated, success, error)


class FakeOracleAdapter:
    instance = INSTANCE
    registry = REGISTRY
    instance_id: int | None = 7

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, family: str = "19c",
                 context: list[dict[str, Any]] | None = None, context_success: bool = True,
                 data_success: bool = True, data_error: str | None = None,
                 truncated: bool = False, instance_id: int | None = 7) -> None:
        self.rows = rows or []
        self.family = family
        self.context = context if context is not None else [{"con_id": 3, "con_name": "FREEPDB1"}]
        self.context_success = context_success
        self.data_success = data_success
        self.data_error = data_error
        self.truncated = truncated
        self.instance_id = instance_id
        self.calls: list[tuple[str, int, str, int | None]] = []

    def execute_readonly(self, script: Any) -> CollectorResult:
        assert script.metadata.name == "top_sql_container"
        return _result(script.metadata.name, self.context, success=self.context_success,
                       error=None if self.context_success else "PermissionDenied")

    def execute_top_sql_readonly(self, script: Any, top_n: int, metric: str,
                                 *, con_id: int | None = None) -> CollectorResult:
        self.calls.append((script.metadata.name, top_n, metric, con_id))
        return _result(script.metadata.name, self.rows, success=self.data_success,
                       error=self.data_error, truncated=self.truncated)


def _collect(adapter: FakeOracleAdapter, metric: str = "cpu_time", top_n: int = 10,
             family: str = "19c", variant: str | None = None) -> Any:
    return OracleTopSQLCollector(cast(OracleAdapter, adapter),
                                 _profile(family, variant)).collect(INSTANCE, metric, top_n)


def test_oracle_top_sql_routes_and_version_specific_registry_are_bounded() -> None:
    assert set(ORACLE_METRICS) == ORACLE_TOP_SQL_METRICS
    assert validate_top_sql_profiles(REGISTRY, PROFILE_BINDINGS) == ()
    routes = [(item.version_family, item.runtime_variant) for item in PROFILE_BINDINGS
              if item.db_type == DatabaseType.ORACLE and item.feature == "top-sql"]
    assert set(routes) == {
        ("11gr2", "default"), ("12c", "12_1"), ("12c", "12_2_plus"),
        *((family, "default") for family in ("18c", "19c", "21c", "23ai", "26ai")),
    }
    assert {family for family, _ in routes} == ORACLE_SUPPORTED_FAMILIES
    assert ORACLE_SQL_PROFILE_ID == "oracle_v_sql_child_top_sql"
    assert ORACLE_PARSER_CONTRACT_ID == "oracle_v_sql_child_top_sql_v1"
    legacy = REGISTRY.get("top_sql_11g", DatabaseType.ORACLE)
    modern = REGISTRY.get("top_sql_modern", DatabaseType.ORACLE)
    assert legacy.metadata.max_rows == modern.metadata.max_rows == 101
    assert "CON_ID" not in legacy.text.upper()
    assert "FETCH FIRST" not in legacy.text.upper()
    assert "ROWNUM <= :TOP_N" in legacy.text.upper()
    assert legacy.text.upper().index("ORDER BY RANKING_VALUE") < legacy.text.upper().index("ROWNUM <=")
    assert "S.CON_ID = :CON_ID" in modern.text.upper()
    assert "GV$SQL" not in modern.text.upper()


@pytest.mark.parametrize(("metric", "metric_name", "unit"), [
    ("cpu_time", "cpu_time", "microseconds"),
    ("elapsed_time", "elapsed_time", "microseconds"),
    ("buffer_gets", "buffer_gets", "gets"),
    ("disk_reads", "disk_reads", "reads"),
    ("executions", "executions", "executions"),
])
def test_oracle_top_sql_metric_contract_and_bound_parameters(metric: str, metric_name: str,
                                                              unit: str) -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 0, 90)])
    result = _collect(adapter, metric, top_n=3)
    assert result.metric_name == metric_name
    assert result.metric_unit == unit
    assert result.metric_scope == MetricScope.CHILD_CURSOR
    assert result.collection_scope == CollectionScope.DATABASE
    assert result.metric_semantics.value == "CUMULATIVE"
    assert result.collection_status == CollectionStatus.COLLECTED
    if metric == "elapsed_time":
        assert Limitation.CUMULATIVE_ELAPSED_NOT_SINGLE_EXECUTION_WALL_TIME in result.limitations
    assert result.source_metadata == {
        "instance_id": 7, "container_id": 3, "container_name": "FREEPDB1",
    }
    assert adapter.calls == [("top_sql_modern", 3, metric, 3)]


def test_oracle_same_sql_id_and_same_plan_hash_keep_distinct_child_cursors() -> None:
    adapter = FakeOracleAdapter([
        _row("sql_a", 0, 100, plan_hash=44),
        _row("sql_a", 1, 90, plan_hash=44),
        _row("sql_a", 2, 80, plan_hash=99),
    ])
    result = _collect(adapter, top_n=3)
    assert [item.rank for item in result.items] == [1, 2, 3]
    assert [item.sql_identity.fields["child_number"] for item in result.items] == [0, 1, 2]
    assert [item.plan_identity.fields["plan_hash_value"] for item in result.items if item.plan_identity] == [44, 44, 99]
    assert result.items[0].plan_identity.fields["child_number"] != result.items[1].plan_identity.fields["child_number"]
    assert all(item.sql_identity.fields["inst_id"] == 7 for item in result.items)
    assert all(item.sql_identity.fields["con_id"] == 3 for item in result.items)


@pytest.mark.parametrize("plan_hash", [None, 0])
def test_oracle_missing_or_zero_plan_hash_does_not_fabricate_plan_identity(plan_hash: int | None) -> None:
    result = _collect(FakeOracleAdapter([_row("sql_a", 0, 1, plan_hash=plan_hash)]))
    assert result.items[0].plan_identity is None


def test_oracle_11g_uses_legacy_sql_without_container_query_and_keeps_instance_scope() -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 4, 5, con_id=None)], family="11gr2")
    result = _collect(adapter, family="11gr2")
    assert result.collection_scope == CollectionScope.INSTANCE
    assert "con_id" not in result.items[0].sql_identity.fields
    assert result.items[0].sql_identity.fields == {"sql_id": "sql_a", "child_number": 4, "inst_id": 7}
    assert adapter.calls == [("top_sql_11g", 10, "cpu_time", None)]


@pytest.mark.parametrize("top_n", [1, 100])
def test_oracle_top_sql_accepts_shared_top_limit_boundaries(top_n: int) -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 0, 1)])
    result = _collect(adapter, top_n=top_n)
    assert result.collection_status == CollectionStatus.COLLECTED
    assert adapter.calls == [("top_sql_modern", top_n, "cpu_time", 3)]


def test_oracle_12c_routes_both_variants_to_container_scoped_sql() -> None:
    for variant in ("12_1", "12_2_plus"):
        adapter = FakeOracleAdapter([_row("sql_a", 0, 5)], family="12c")
        result = _collect(adapter, family="12c", variant=variant)
        assert result.collection_scope == CollectionScope.DATABASE
        assert adapter.calls == [("top_sql_modern", 10, "cpu_time", 3)]


def test_oracle_modern_route_preserves_native_zero_container_id() -> None:
    adapter = FakeOracleAdapter(
        [_row("sql_a", 0, 5, con_id=0)],
        context=[{"con_id": 0, "con_name": None}],
    )
    result = _collect(adapter)
    assert result.collection_status == CollectionStatus.COLLECTED
    assert result.collection_scope == CollectionScope.DATABASE
    assert result.source_metadata["container_id"] == 0
    assert result.source_metadata["container_name"] is None
    assert result.items[0].sql_identity.fields["con_id"] == 0
    assert adapter.calls == [("top_sql_modern", 10, "cpu_time", 0)]


def test_oracle_container_identity_conflict_degrades_without_cross_container_merge() -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 0, 10, con_id=4)])
    result = _collect(adapter)
    assert result.collection_status == CollectionStatus.COLLECTED
    assert result.completeness == Completeness.PARTIAL
    assert result.data_visibility == DataVisibility.PARTIAL
    assert result.items == ()
    assert Limitation.IDENTITY_AMBIGUOUS in result.limitations


def test_oracle_current_instance_identity_prevents_rac_child_cursor_merge() -> None:
    first = _collect(FakeOracleAdapter([_row("sql_a", 0, 20)], instance_id=7))
    second = _collect(FakeOracleAdapter([_row("sql_a", 0, 20)], instance_id=8))
    assert first.items[0].sql_identity.fields["inst_id"] == 7
    assert second.items[0].sql_identity.fields["inst_id"] == 8
    assert first.items[0].sql_identity != second.items[0].sql_identity
    assert "GV$SQL" not in REGISTRY.get("top_sql_modern", DatabaseType.ORACLE).text.upper()


def test_oracle_missing_or_unreadable_container_scope_is_not_a_successful_empty_list() -> None:
    missing_context = _collect(FakeOracleAdapter(context=[]))
    failed_context = _collect(FakeOracleAdapter(context_success=False))
    assert missing_context.collection_status == CollectionStatus.SOURCE_UNAVAILABLE
    assert failed_context.collection_status == CollectionStatus.PERMISSION_DENIED
    for result in (missing_context, failed_context):
        assert result.completeness == Completeness.UNKNOWN
        assert result.data_visibility == DataVisibility.UNAVAILABLE
        assert result.items == ()


def test_oracle_missing_instance_identity_fails_closed_without_query() -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 0, 1)], instance_id=None)
    result = _collect(adapter)
    assert result.collection_status == CollectionStatus.SOURCE_UNAVAILABLE
    assert result.completeness == Completeness.UNKNOWN
    assert result.items == ()
    assert adapter.calls == []


def test_oracle_counter_epoch_is_unknown_but_eviction_is_not_invented() -> None:
    result = _collect(FakeOracleAdapter([_row("sql_a", 0, 1)]))
    assert result.completeness == Completeness.COMPLETE
    assert Limitation.COUNTER_EPOCH_UNKNOWN in result.limitations
    assert Limitation.CACHE_EVICTED not in result.limitations


def test_oracle_missing_sql_identity_is_partial_without_a_synthetic_identifier() -> None:
    adapter = FakeOracleAdapter([{"sql_id": None, "child_number": 0, "plan_hash_value": 44,
                                  "con_id": 3, "ranking_value": 5}])
    result = _collect(adapter)
    assert result.collection_status == CollectionStatus.COLLECTED
    assert result.completeness == Completeness.PARTIAL
    assert result.data_visibility == DataVisibility.PARTIAL
    assert result.items == ()
    assert Limitation.IDENTITY_AMBIGUOUS in result.limitations


def test_oracle_permission_failure_is_not_reported_as_an_empty_success() -> None:
    adapter = FakeOracleAdapter(data_success=False, data_error="PermissionDenied")
    result = _collect(adapter)
    assert result.collection_status == CollectionStatus.PERMISSION_DENIED
    assert result.data_visibility == DataVisibility.UNAVAILABLE
    assert result.completeness == Completeness.UNKNOWN
    assert result.items == ()


@pytest.mark.parametrize("metric,top_n", [
    ("cpu_time; select * from dual", 10), ("cpu_time", 0), ("cpu_time", 101), ("cpu_time", True),
])
def test_oracle_invalid_metric_or_top_n_is_rejected_before_collection(metric: str, top_n: int) -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 0, 1)])
    with pytest.raises(ToolkitError):
        _collect(adapter, metric, top_n)
    assert adapter.calls == []


class BindCursor:
    description = (("SQL_ID",), ("CHILD_NUMBER",), ("RANKING_VALUE",))
    arraysize = 0
    prefetchrows = 0

    def __init__(self) -> None:
        self.bound: dict[str, Any] | None = None
        self.closed = False
        self.returned = False

    def execute(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        assert "v$sql" in sql.casefold()
        self.bound = parameters

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        assert size == 1
        if self.returned:
            return []
        self.returned = True
        return [("sql_a", 0, 55)]

    def close(self) -> None:
        self.closed = True


class BindConnection:
    autocommit = True
    call_timeout = 0

    def __init__(self) -> None:
        self.item = BindCursor()

    def cursor(self) -> BindCursor:
        return self.item

    def rollback(self) -> None:
        raise AssertionError("Autocommit Oracle Top SQL query must not roll back.")

    def close(self) -> None:
        pass


def test_oracle_adapter_binds_only_approved_top_sql_parameters() -> None:
    instance = DatabaseInstance("oracle-lab", "Oracle Lab", DatabaseType.ORACLE,
                                "example.invalid", 1521, "oracle-monitor", service_name="FREEPDB1")
    adapter = OracleAdapter(instance, MockCredentialProvider({}), REGISTRY, mode=Mode.DEVELOPMENT,
                            driver=object())  # type: ignore[arg-type]
    connection = BindConnection()
    adapter._connection = connection  # type: ignore[assignment]
    adapter._ready = True
    adapter.instance_id = 7
    script = REGISTRY.get("top_sql_modern", DatabaseType.ORACLE)
    result = adapter.execute_top_sql_readonly(script, 10, "cpu_time", con_id=3)
    assert result.success
    assert connection.item.bound == {"metric": "cpu_time", "top_n": 10, "con_id": 3}
    assert connection.item.closed

    assert adapter.execute_top_sql_readonly(script, 100, "executions", con_id=3).success
    assert connection.item.bound == {"metric": "executions", "top_n": 100, "con_id": 3}
    assert adapter.execute_top_sql_readonly(
        REGISTRY.get("top_sql_11g", DatabaseType.ORACLE), 1, "cpu_time").success
    assert connection.item.bound == {"metric": "cpu_time", "top_n": 1}

    for metric, top_n, con_id in (("cpu_time; delete from dual", 10, 3), ("cpu_time", True, 3),
                                  ("cpu_time", 10, -1)):
        with pytest.raises(ToolkitError):
            adapter.execute_top_sql_readonly(script, top_n, metric, con_id=con_id)
    with pytest.raises(ToolkitError, match="reviewed bounded Registry scripts"):
        adapter.execute_top_sql_readonly(REGISTRY.get("active_requests", DatabaseType.ORACLE),
                                         10, "cpu_time", con_id=3)


def test_oracle_identity_contract_requires_child_and_instance_and_allows_container_id() -> None:
    identity = NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.STATEMENT,
                                 {"sql_id": "sql_a", "child_number": 2, "inst_id": 1, "con_id": 3})
    assert identity.fields["con_id"] == 3
    non_cdb_identity = NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.STATEMENT,
                                         {"sql_id": "sql_b", "child_number": 0,
                                          "inst_id": 1, "con_id": 0})
    assert non_cdb_identity.fields["con_id"] == 0
    with pytest.raises(ValueError, match="incomplete"):
        NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.STATEMENT, {"sql_id": "sql_a"})


def test_oracle_single_result_uses_shared_json_cli_and_html_contract() -> None:
    result = _collect(FakeOracleAdapter([
        _row("sql_a", 0, 55), _row("sql_a", 1, 42, plan_hash=44),
    ]), top_n=2)
    payload = result.to_dict()
    json_payload = json.loads(render_json(payload))
    cli = render_human("top-sql", payload)
    report = TopSQLDiagnosticReport(INSTANCE, result.collection_timestamp, "top-sql", (), {
        "version": result.database_version, "top_sql": payload,
    })
    html = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))

    assert json_payload["metric_scope"] == MetricScope.CHILD_CURSOR.value
    assert json_payload["collection_scope"] == CollectionScope.DATABASE.value
    assert json_payload["items"] == payload["items"]
    assert [item["metric_value"] for item in json_payload["items"]] == [55, 42]
    assert "sql_id=sql_a" in cli and "child_number=0" in cli and "55" in cli
    assert "sql_a" in html and "CHILD_CURSOR" in html and "55" in html


@pytest.mark.parametrize(("plan_hash", "expected_plan_hash"), [
    (44, "plan_hash_value=44"),
    (None, "plan_hash_value=—"),
    (0, "plan_hash_value=—"),
])
def test_oracle_top_sql_html_renders_container_and_only_valid_plan_identity(
        plan_hash: int | None, expected_plan_hash: str) -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 2, 55, plan_hash=plan_hash, con_id=3)])
    result = _collect(adapter)
    payload = result.to_dict()
    report = TopSQLDiagnosticReport(INSTANCE, result.collection_timestamp, "top-sql", (), {
        "version": result.database_version, "top_sql": payload,
    })

    html = HtmlReportRenderer().render(
        build_report_model(report, build_incident_summary(report)))

    assert "sql_id=sql_a" in html
    assert "child_number=2" in html
    assert "inst_id=7" in html
    assert "con_id=3" in html
    assert expected_plan_hash in html
    assert adapter.calls == [("top_sql_modern", 10, "cpu_time", 3)]


def test_oracle_11g_top_sql_html_does_not_invent_container_identity() -> None:
    adapter = FakeOracleAdapter([_row("sql_a", 2, 55, con_id=None)], family="11gr2")
    result = _collect(adapter, family="11gr2")
    report = TopSQLDiagnosticReport(INSTANCE, result.collection_timestamp, "top-sql", (), {
        "version": result.database_version, "top_sql": result.to_dict(),
    })

    html = HtmlReportRenderer().render(
        build_report_model(report, build_incident_summary(report)))

    assert "sql_id=sql_a" in html
    assert "child_number=2" in html
    assert "inst_id=7" in html
    assert "con_id=" not in html
    assert "plan_hash_value=44" in html
