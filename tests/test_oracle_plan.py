from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_oracle import fixture as oracle_fixture

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.oracle import OracleAdapter, OraclePlanCursor, OraclePlanTarget
from dba.oracle_plan import OraclePlanCollector
from dba.oracle_plan_profiles import validate_oracle_plan_profiles
from dba.presentation import render_json, render_oracle_plan_human
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.versioning import (
    DataVisibility,
    ResolvedDiagnosticProfile,
    RuntimeConfigState,
    SupportState,
    VersionStatus,
)

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = DatabaseInstance("oracle-test", "Oracle Test", DatabaseType.ORACLE,
                            "127.0.0.1", 1521, "oracle-monitor", service_name="FREEPDB1")
NOW = datetime.now(timezone.utc)


def profile(family: str = "26ai", variant: str = "default") -> ResolvedDiagnosticProfile:
    from dba.oracle_plan_profiles import (
        ORACLE_PLAN_PARSER_CONTRACT_ID,
        ORACLE_PLAN_PROFILE_ID,
    )

    return ResolvedDiagnosticProfile(
        DatabaseType.ORACLE, "plan", family, "26.0.0.0.0", family,
        VersionStatus.VERIFIED, SupportState.SUPPORTED, ORACLE_PLAN_PROFILE_ID,
        ORACLE_PLAN_PROFILE_ID, ORACLE_PLAN_PARSER_CONTRACT_ID,
        variant, RuntimeConfigState.CONFIGURED,
        DataVisibility.FULL,
    )


def result(name: str, rows: tuple[dict[str, Any], ...] = (), *, success: bool = True,
           truncated: bool = False, error_type: str | None = None) -> CollectorResult:
    return CollectorResult("oracle-test", name, NOW, NOW, 1.0, rows, len(rows),
                           truncated, success, error_type,
                           "permission denied" if not success else None)


def cursor_row(*, child: int = 3, plan_hash: int = 99, sql_id: str = "0123456789abc",
               con_id: int = 3, inst_id: int = 1, match_count: int = 1) -> dict[str, Any]:
    return {"sql_id": sql_id, "child_number": child, "plan_hash_value": plan_hash,
            "address_hex": "0102030405060708", "hash_value": 123456,
            "child_address_hex": "1112131415161718", "inst_id": inst_id,
            "con_id": con_id, "cursor_match_count": match_count}


def plan_row(node_id: int, *, child: int = 3, plan_hash: int = 99, con_id: int = 3,
             inst_id: int = 1, match_count: int = 1) -> dict[str, Any]:
    return {"sql_id": "0123456789abc", "child_number": child,
            "plan_hash_value": plan_hash, "con_id": con_id, "inst_id": inst_id,
            "cursor_match_count": match_count, "id": node_id,
            "parent_id": None if node_id == 0 else 0,
            "depth": 0 if node_id == 0 else 1, "position": node_id,
            "operation": "TABLE ACCESS", "options": "FULL", "object_owner": "APP",
            "object_name": f"T{node_id}", "cardinality": None, "cost": 4,
            "bytes": None}


class FakeOraclePlanAdapter:
    def __init__(self, profile_family: str = "26ai", *, con_id: int = 3,
                 inst_id: int = 1, identity_rows: tuple[dict[str, Any], ...] | None = None,
                 plan_rows: tuple[dict[str, Any], ...] = (), fail: str | None = None,
                 plan_truncated: bool = False) -> None:
        self.instance = INSTANCE
        self.instance_id = inst_id
        self.registry = SqlScriptRegistry(ROOT / "sql")
        self.con_id = con_id
        self.identity_rows = identity_rows
        self.plan_rows = plan_rows
        self.fail = fail
        self.plan_truncated = plan_truncated
        self.calls: list[str] = []
        self.profile = profile(profile_family, "12_2_plus" if profile_family == "12c" else "default")

    def execute_readonly(self, script: Any) -> CollectorResult:
        self.calls.append(script.metadata.name)
        return result(script.metadata.name, ({"inst_id": self.instance_id,
                                               "con_id": self.con_id},))

    def execute_plan_identity_readonly(self, script: Any, target: OraclePlanTarget) -> CollectorResult:
        self.calls.append(script.metadata.name)
        if self.fail == "identity":
            return result(script.metadata.name, success=False, error_type="PermissionDenied")
        rows = self.identity_rows if self.identity_rows is not None else (cursor_row(
            child=target.child_number, sql_id=target.sql_id, con_id=target.con_id or 0,
            inst_id=target.inst_id),)
        return result(script.metadata.name, rows)

    def execute_plan_rows_readonly(self, script: Any, identity: OraclePlanCursor) -> CollectorResult:
        self.calls.append(script.metadata.name)
        if self.fail == "rows":
            return result(script.metadata.name, success=False, error_type="QueryTimeout")
        rows = self.plan_rows
        if not rows:
            return result(script.metadata.name)
        return result(script.metadata.name, rows, truncated=self.plan_truncated)


def collect(adapter: FakeOraclePlanAdapter, *, family: str = "26ai", sql_id: str = "0123456789abc",
            child: int = 3, inst_id: int | None = None, con_id: int | None = None,
            plan_hash: int | None = None):
    return OraclePlanCollector(adapter, adapter.profile).collect(
        INSTANCE, sql_id, child, inst_id=inst_id, con_id=con_id, plan_hash_value=plan_hash)


def test_oracle_plan_profile_routes_and_registry_are_reviewed() -> None:
    from dba.versioning import PROFILE_BINDINGS

    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_oracle_plan_profiles(registry, PROFILE_BINDINGS) == ()


@pytest.mark.parametrize("child,plan_hash", [(3, 99), (4, 99), (4, 100)])
def test_child_and_plan_identity_are_never_merged(child: int, plan_hash: int) -> None:
    adapter = FakeOraclePlanAdapter(identity_rows=(cursor_row(child=child, plan_hash=plan_hash),),
                                    plan_rows=(plan_row(0, child=child, plan_hash=plan_hash),))
    report = collect(adapter, child=child)
    assert report.details["plan_status"] == "AVAILABLE"
    assert report.details["request"]["child_number"] == child
    assert report.details["request"]["current_plan_hash_value"] == plan_hash
    assert adapter.calls[-1] == "oracle_plan_rows_modern"


def test_oracle_26ai_native_three_row_plan_accepts_null_root_position() -> None:
    sql_id = "0sbbcuruzd66f"
    plan_hash = 2239883476
    identity = cursor_row(child=2, plan_hash=plan_hash, sql_id=sql_id)
    native_rows = (
        {**plan_row(0, child=2, plan_hash=plan_hash), "sql_id": sql_id,
         "parent_id": None, "depth": 0, "position": None,
         "operation": "SELECT STATEMENT", "options": None,
         "cardinality": 42, "cost": 1.5, "bytes": 512},
        {**plan_row(1, child=2, plan_hash=plan_hash), "sql_id": sql_id,
         "parent_id": 0, "depth": 1, "position": 1,
         "operation": "TABLE ACCESS", "options": "BY INDEX ROWID",
         "cardinality": None, "cost": None, "bytes": None},
        {**plan_row(2, child=2, plan_hash=plan_hash), "sql_id": sql_id,
         "parent_id": 1, "depth": 2, "position": 1,
         "operation": "INDEX", "options": "RANGE SCAN"},
    )
    report = collect(FakeOraclePlanAdapter(identity_rows=(identity,), plan_rows=native_rows),
                     sql_id=sql_id, child=2, plan_hash=plan_hash)
    assert report.details["plan_status"] == "AVAILABLE"
    assert report.details["operator_count"] == 3
    assert [row["id"] for row in report.details["top_operators"]] == [0, 1, 2]
    assert [row["parent_id"] for row in report.details["top_operators"]] == [None, 0, 1]
    assert report.details["top_operators"][0]["position"] is None
    assert report.details["top_operators"][0]["estimated_rows"] == 42
    assert report.details["top_operators"][0]["estimated_cost"] == 1.5
    assert report.details["top_operators"][0]["estimated_bytes"] == 512
    assert report.details["top_operators"][1]["estimated_rows"] is None
    incident = build_incident_summary(report, "report.html")
    assert incident.status == "UNKNOWN"
    assert incident.observed_now == "NOT_ASSESSED"
    assert incident.immediate_action is None
    incident_json = render_json({"incident_summary": asdict(incident)})
    assert '"status": "UNKNOWN"' in incident_json
    assert '"observed_now": "NOT_ASSESSED"' in incident_json
    html = HtmlReportRenderer().render(build_report_model(report, incident))
    for text in ("SELECT STATEMENT", "TABLE ACCESS", "BY INDEX ROWID", "INDEX", "RANGE SCAN"):
        assert text in html
    assert "估算行数" in html and "估算成本" in html and "估算字节数" in html
    assert "42" in html and "1.5" in html and "512" in html and "—" in html
    assert "No obvious active database-side issue detected" not in html
    assert "未评估" in html
    plan_tree = next(section for section in build_report_model(report, incident).sections
                     if section.section_id == "oracle-plan-tree")
    assert "Estimated Rows" in plan_tree.tables[0].headers
    assert "Actual Rows" not in plan_tree.tables[0].headers


@pytest.mark.parametrize("bad_rows", [
    (plan_row(0), plan_row(0)),
    (plan_row(0), {**plan_row(1), "parent_id": 8}),
    (plan_row(0), {**plan_row(1), "parent_id": 1, "depth": 1}),
])
def test_duplicate_or_invalid_plan_node_relationship_fails_closed(
    bad_rows: tuple[dict[str, Any], ...],
) -> None:
    report = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),), plan_rows=bad_rows))
    assert report.details["plan_status"] == "PLAN_AMBIGUOUS"
    assert report.details["plan_available"] is False
    assert report.details["top_operators"] == []


@pytest.mark.parametrize(("field", "value"), [
    ("sql_id", "different_sql_id"),
    ("child_number", 9),
    ("inst_id", 2),
    ("con_id", 4),
    ("plan_hash_value", 100),
    ("cursor_match_count", 2),
])
def test_plan_rows_with_conflicting_cursor_identity_remain_rejected(field: str, value: Any) -> None:
    root = {**plan_row(0), field: value}
    report = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),), plan_rows=(root,)))
    assert report.details["plan_status"] == "PLAN_AMBIGUOUS"
    assert report.details["plan_available"] is False
    assert report.details["top_operators"] == []


def test_missing_child_plan_hash_mismatch_and_ambiguous_identity_fail_closed() -> None:
    missing = FakeOraclePlanAdapter(identity_rows=())
    assert collect(missing).details["plan_status"] == "CURSOR_NOT_CACHED"
    mismatch = FakeOraclePlanAdapter(identity_rows=(cursor_row(plan_hash=99),))
    assert collect(mismatch, plan_hash=100).details["plan_status"] == "PLAN_HASH_MISMATCH"
    ambiguous = FakeOraclePlanAdapter(identity_rows=(cursor_row(), cursor_row()))
    assert collect(ambiguous).details["plan_status"] == "IDENTITY_AMBIGUOUS"


def test_instance_container_and_cursor_conflicts_are_rejected() -> None:
    adapter = FakeOraclePlanAdapter()
    assert collect(adapter, inst_id=2).details["plan_status"] == "IDENTITY_MISMATCH"
    assert collect(adapter, con_id=4).details["plan_status"] == "IDENTITY_MISMATCH"
    conflict = FakeOraclePlanAdapter(identity_rows=(cursor_row(child=4),))
    assert collect(conflict).details["plan_status"] == "IDENTITY_CONFLICT"


def test_11g_uses_no_container_column_or_context_query() -> None:
    adapter = FakeOraclePlanAdapter("11gr2", identity_rows=(cursor_row(con_id=0),),
                                    plan_rows=(plan_row(0, con_id=0),))
    report = collect(adapter, family="11gr2")
    assert report.details["plan_status"] == "AVAILABLE"
    assert "con_id" not in report.details["request"]
    assert adapter.calls == ["oracle_plan_identity_11g", "oracle_plan_rows_11g"]
    bad = collect(adapter, family="11gr2", con_id=3)
    assert bad.details["plan_status"] == "IDENTITY_MISMATCH"


def test_empty_plan_and_500_operator_boundary() -> None:
    assert collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),))).details[
        "plan_status"] == "PLAN_NOT_CACHED"
    exactly_500 = tuple(plan_row(i) for i in range(500))
    report = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),), plan_rows=exactly_500))
    assert report.details["plan_status"] == "AVAILABLE"
    over_500 = tuple(plan_row(i) for i in range(501))
    too_large = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),),
        plan_rows=over_500, plan_truncated=True))
    assert too_large.details["plan_status"] == "PLAN_TOO_LARGE"
    assert too_large.details["top_operators"] == []


def test_adaptive_nodes_and_null_estimates_are_preserved_without_interpretation() -> None:
    rows = (plan_row(0), plan_row(7))  # Non-contiguous IDs are valid plan node IDs.
    report = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),), plan_rows=rows))
    assert report.details["plan_status"] == "AVAILABLE"
    assert report.details["top_operators"][1]["id"] == 7
    assert report.details["top_operators"][0]["estimated_rows"] is None
    assert report.details["top_operators"][0]["actual_rows"] is None
    assert "ADAPTIVE_PLAN_BRANCH_NOT_CLASSIFIED" in report.details["limitations"]


def test_permission_and_timeout_failures_are_classified() -> None:
    denied = collect(FakeOraclePlanAdapter(fail="identity"))
    assert denied.details["plan_status"] == "QUERY_DENIED"
    assert "docs/DATABASE_PERMISSIONS.md" in denied.details["note"]
    timeout = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),), fail="rows"))
    assert timeout.details["plan_status"] == "TIMEOUT"


def test_identity_parameters_reject_sql_injection_and_bad_ranges() -> None:
    for sql_id in ("' OR 1=1 --", "0123456789abc'", "short"):
        with pytest.raises(ValueError):
            OraclePlanTarget(sql_id, 0, 1)
    with pytest.raises(ValueError):
        OraclePlanTarget("0123456789abc", -1, 1)
    with pytest.raises(ValueError):
        OraclePlanTarget("0123456789abc", 0, 1, requested_plan_hash_value=0)


def test_adapter_plan_methods_accept_only_reviewed_bounded_scripts() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    adapter = object.__new__(OracleAdapter)
    adapter._ready = True
    adapter.registry = registry
    adapter.instance_id = 1
    captured: list[tuple[str, dict[str, Any]]] = []
    adapter._run = lambda script, params: captured.append((script.metadata.name, params)) or result(
        script.metadata.name)
    target = OraclePlanTarget("0123456789abc", 3, 1, 3)
    script = registry.get("oracle_plan_identity_modern", DatabaseType.ORACLE)
    adapter.execute_plan_identity_readonly(script, target)
    assert captured[-1][1] == {"sql_id": "0123456789abc", "child_number": 3,
                               "inst_id": 1, "con_id": 3}
    rows_script = registry.get("oracle_plan_rows_modern", DatabaseType.ORACLE)
    cursor = OraclePlanCursor(target, "0102030405060708", 123456,
                              "1112131415161718", 99)
    adapter.execute_plan_rows_readonly(rows_script, cursor)
    assert captured[-1][1]["plan_hash_value"] == 99
    with pytest.raises(ToolkitError, match="script does not match"):
        adapter.execute_plan_rows_readonly(
            registry.get("oracle_plan_identity_modern", DatabaseType.ORACLE), cursor)


def test_json_cli_and_html_expose_same_oracle_identity_and_plan_tree() -> None:
    report = collect(FakeOraclePlanAdapter(identity_rows=(cursor_row(),),
        plan_rows=(plan_row(0), plan_row(1))))
    json_payload = render_json(report)
    assert '"sql_id": "0123456789abc"' in json_payload
    assert '"plan_hash_value": 99' in json_payload
    console = render_oracle_plan_human(report, "report.html")
    assert "CHILD_NUMBER=3" in console and "PLAN_HASH_VALUE=99" in console
    assert "TABLE ACCESS FULL APP.T0" in console
    incident = build_incident_summary(report, "report.html")
    model = build_report_model(report, incident)
    html = HtmlReportRenderer().render(model)
    assert "Oracle 缓存执行计划身份" in html
    assert "缓存执行计划树" in html
    assert "TABLE ACCESS" in html
    assert "Estimated Cost" in model.sections[2].tables[0].headers
    assert report.details["top_operators"][0]["estimated_rows"] is None
    assert "ADAPTIVE_PLAN_BRANCH_NOT_CLASSIFIED" in report.details["limitations"]


def test_oracle_plan_cli_executes_only_bound_registry_route(tmp_path: Path,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    instance, _, _, driver = oracle_fixture()

    class TestProvider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            assert alias == instance.credential_alias
            return Credential("MONITOR", "test-password")

    provider = TestProvider()
    registry = SqlScriptRegistry(ROOT / "sql")
    route_data: dict[str, list[dict[str, Any]]] = {
        "oracle_plan_context_modern": [{"inst_id": 1, "con_id": 3, "con_name": "FREEPDB1"}],
        "oracle_plan_identity_modern": [cursor_row(sql_id="0123456789abc", child=3)],
        "oracle_plan_rows_modern": [plan_row(0)],
    }
    for name, rows in route_data.items():
        script = registry.get(name, DatabaseType.ORACLE)
        driver.connection.sql_names[script.text] = name
        driver.connection.data[name] = rows
    driver.connection.data["preflight_object_grants"].append(
        {"owner": "SYS", "table_name": "V_$SQL_PLAN", "privilege": "SELECT", "grantable": "NO"})
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        **asdict(instance), "db_type": "oracle", "version_family": "19c"}]}), encoding="utf-8")
    report_path = tmp_path / "oracle-plan.html"
    status = main(["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
                   "diagnose", instance.id, "plan", "--sql-id", "0123456789abc",
                   "--child-number", "3", "--report", str(report_path), "--json"],
                  provider=provider, driver=driver)
    payload = json.loads(capsys.readouterr().out)
    assert status == 0, payload
    assert payload["report"]["details"]["plan_status"] == "AVAILABLE"
    assert "缓存执行计划树" in report_path.read_text(encoding="utf-8")
    plan_calls = [(name, parameters) for name, parameters in driver.connection.bound_parameters
                  if name.startswith("oracle_plan_")]
    assert [item[0] for item in plan_calls] == [
        "oracle_plan_context_modern", "oracle_plan_identity_modern", "oracle_plan_rows_modern"]
    assert plan_calls[1][1] == {"sql_id": "0123456789abc", "child_number": 3,
                                 "inst_id": 1, "con_id": 3}
