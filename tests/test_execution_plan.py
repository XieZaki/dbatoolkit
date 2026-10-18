import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider

from dba.cli import main
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.execution_plan import (
    ExecutionPlanAnalyzer,
    ExecutionPlanCollector,
    ExecutionPlanParser,
    PlanSample,
)
from dba.execution_plan_reporter import render_console, render_markdown
from dba.permissions import requirements_for_profile
from dba.registry import SqlScriptRegistry
from dba.settings import SQLServerSettings
from dba.sqlserver import SQLServerAdapter
from dba.versioning import (
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    SupportState,
    VersionStatus,
)

INSTANCE = DatabaseInstance("test", "Test", DatabaseType.SQLSERVER, "test.invalid", 1433, "monitor")
XML = '''<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">
<BatchSequence><Batch><Statements><StmtSimple StatementText="SELECT 1; -- PASSWORD=&apos;private&apos;">
<QueryPlan><MissingIndexes><MissingIndexGroup Impact="88.2"><MissingIndex Database="[db]" Schema="[dbo]" Table="[T]">
<ColumnGroup Usage="EQUALITY"><Column Name="[a]"/></ColumnGroup>
<ColumnGroup Usage="INEQUALITY"><Column Name="[b]"/></ColumnGroup>
<ColumnGroup Usage="INCLUDE"><Column Name="[c]"/></ColumnGroup>
</MissingIndex></MissingIndexGroup></MissingIndexes>
<RelOp NodeId="1" PhysicalOp="Nested Loops" LogicalOp="Inner Join" EstimateRows="1" EstimatedTotalSubtreeCost="50" Parallel="0">
<NestedLoops><RelOp NodeId="2" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="100" EstimatedTotalSubtreeCost="30" EstimateIO="2" EstimateCPU="1" Parallel="1">
<IndexScan><Object Database="[db]" Schema="[dbo]" Table="[T]" Index="[PK_T]"/></IndexScan>
<RunTimeInformation><RunTimeCountersPerThread ActualRows="1000000" ActualExecutions="1"/></RunTimeInformation>
<Warnings SpillToTempDb="1" NoJoinPredicate="true"><PlanAffectingConvert ConvertIssue="Seek Plan"/></Warnings>
</RelOp><RelOp NodeId="3" PhysicalOp="Clustered Index Seek" LogicalOp="Key Lookup" EstimateRows="1">
<IndexScan><Object Schema="[dbo]" Table="[T]" Index="[PK_T]"/></IndexScan>
<RunTimeInformation><RunTimeCountersPerThread ActualRows="100000" ActualExecutions="100000"/></RunTimeInformation>
<Predicate><ScalarOperator ScalarString="CONVERT_IMPLICIT(int,[T].[a],0)=1"/></Predicate>
</RelOp></NestedLoops></RelOp>
<RelOp NodeId="4" PhysicalOp="Sort" LogicalOp="Sort" EstimateRows="100" EstimatedTotalSubtreeCost="5" />
<RelOp NodeId="5" PhysicalOp="Hash Match" LogicalOp="Aggregate" EstimateRows="100" EstimatedTotalSubtreeCost="4" />
<RelOp NodeId="6" PhysicalOp="Table Scan" LogicalOp="Table Scan" />
<UnknownFutureOperator NewAttribute="safe"/>
</QueryPlan></StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>'''


def sample(xml: str | None = XML, **kwargs: Any) -> PlanSample:
    row = {"session_id": 51, "request_id": 0, "sql_text": "SELECT 1",
           "statement_text": "SELECT 1", "plan_xml": xml, "plan_oversize": 0,
           "sql_handle": "0x01", "plan_handle": "0x02",
           "statement_start_offset": 0, "statement_end_offset": 18,
           "query_hash": "0x01", "query_plan_hash": "0x02", **kwargs}
    return PlanSample(51, "active_request", True, row, None, ())


def plan_profile(family: str = "2022"):
    return PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, family,
        DetectedDatabaseVersion("16.0.1000.6", family), "plan",
    )


def test_xml_nodes_missing_fields_and_unknown_nodes() -> None:
    parsed = ExecutionPlanParser(200000).parse(XML)
    assert len(parsed.nodes) == 6
    parent, scan, lookup = parsed.nodes[:3]
    assert parent.child_node_ids == (2, 3) and parent.actual_rows is None
    assert scan.actual_rows == 1000000 and scan.estimated_rows == 100
    assert scan.estimated_total_subtree_cost == 30 and scan.estimate_io == 2
    assert scan.estimate_cpu == 1 and scan.parallel is True
    assert scan.object_name == "[db].[dbo].[T]" and scan.index_name == "[PK_T]"
    assert lookup.predicate and "CONVERT_IMPLICIT" in lookup.predicate
    assert lookup.actual_executions == 100000
    assert parsed.nodes[-1].actual_rows is None
    assert "private" not in parsed.sanitized_xml and "[REDACTED]" in parsed.sanitized_xml
    assert parsed.missing_indexes[0].equality_columns == ("[a]",)
    assert parsed.missing_indexes[0].inequality_columns == ("[b]",)
    assert parsed.missing_indexes[0].include_columns == ("[c]",)


def test_analysis_findings_evidence_and_reports() -> None:
    parsed = ExecutionPlanParser(200000).parse(XML)
    settings = SQLServerSettings(plan_large_scan_rows=10000)
    report = ExecutionPlanAnalyzer(settings).analyze(sample(), parsed, INSTANCE)
    titles = {finding.title for finding in report.findings}
    assert titles >= {"LARGE_SCAN", "SCAN_OPERATOR", "CARDINALITY_MISMATCH", "KEY_LOOKUP",
                      "SORT_OPERATOR", "HASH_MATCH", "POTENTIAL_EXPENSIVE_NESTED_LOOP",
                      "IMPLICIT_CONVERSION", "MISSING_INDEX_EVIDENCE", "PLAN_WARNING"}
    mismatch = next(f for f in report.findings if f.title == "CARDINALITY_MISMATCH")
    assert mismatch.evidence["ratio"] == 10000
    assert mismatch.severity.value == "CRITICAL"
    assert not any(f.reference_commands for f in report.findings)
    markdown = render_markdown(report)
    for title in ("SQL Summary", "Runtime Metrics", "Plan Summary", "Findings", "Evidence",
                  "Recommendations", "Raw Plan Reference"):
        assert f"## {title}" in markdown
    assert "Toolkit did not modify database state." in markdown
    assert "private" not in markdown and "private" not in render_console(report)


@pytest.mark.parametrize("xml,error", [
    ("<ShowPlanXML><unclosed>", "InvalidPlanXML"),
    ("<!DOCTYPE x [<!ENTITY a 'boom'>]><ShowPlanXML/>", "InvalidPlanXML"),
    ("<notaplan/>", "InvalidPlanXML"),
    ("<ShowPlanXML>" + "x" * 10000 + "</ShowPlanXML>", "PlanTooLarge"),
])
def test_xml_rejected_safely(xml: str, error: str) -> None:
    with pytest.raises(ToolkitError) as exc:
        ExecutionPlanParser(4096).parse(xml)
    assert exc.value.error_type == error


def test_missing_actual_rows_prevents_cardinality_claim() -> None:
    parsed = ExecutionPlanParser(4096).parse('<ShowPlanXML><RelOp NodeId="1" PhysicalOp="Table Scan" EstimateRows="1"/></ShowPlanXML>')
    report = ExecutionPlanAnalyzer(SQLServerSettings()).analyze(sample(), parsed, INSTANCE)
    assert "SCAN_OPERATOR" in [f.title for f in report.findings]
    assert "CARDINALITY_MISMATCH" not in [f.title for f in report.findings]


def test_empty_plan_and_missing_session() -> None:
    parsed = ExecutionPlanParser(4096).parse("<ShowPlanXML><FutureNode/></ShowPlanXML>")
    assert not parsed.nodes
    report = ExecutionPlanAnalyzer(SQLServerSettings()).analyze(
        PlanSample(51, None, False, None,
                   "Active request no longer exists. Execution plan unavailable.", ()), None, INSTANCE)
    assert report.details["plan_available"] is False
    assert "Active request no longer exists." in render_console(report)
    assert "Execution plan unavailable." in render_markdown(report)


def test_bound_registry_and_cache_fallback() -> None:
    driver = FakeDriver()
    driver.connection.results.update(plan_request=[], plan_cached=[sample().row])
    with SQLServerAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                          driver=driver) as adapter:
        selected = ExecutionPlanCollector(adapter, plan_profile()).collect(51)
        assert selected.source == "cached_last_execution" and not selected.request_found
        assert driver.connection.bound_parameters == [(1048576, 1048576, 51)] * 2
        with pytest.raises(ToolkitError, match="Plan probes require"):
            adapter.execute_readonly(adapter.registry.get("plan_request"))
        with pytest.raises(ToolkitError, match="Plan limits"):
            adapter.execute_readonly(adapter.registry.get("plan_request"),
                                     plan_parameters=(1, 1, 51))
        with pytest.raises(ToolkitError, match="Plan probes require"):
            adapter.execute_readonly(adapter.registry.get("server_info"),
                                     plan_parameters=(1048576, 1048576, 51))
    assert driver.connection.closed


def test_plan_profiles_bind_version_permission_sql_and_parser() -> None:
    errors = PROFILE_RESOLVER.validate()
    assert not errors
    for family in ("2008r2", "2012", "2014", "2016", "2017", "2019"):
        profile = PROFILE_RESOLVER.resolve(
            DatabaseType.SQLSERVER, family,
            DetectedDatabaseVersion("15.0.2000.5", family), "plan",
        )
        assert profile.version_status == VersionStatus.VERIFIED
        assert profile.support_state == SupportState.SUPPORTED
        assert profile.sql_profile_id == "sqlserver_plan_shared"
        assert profile.permission_profile_id == "sqlserver_plan_legacy"
        assert profile.parser_contract_id == "sqlserver_showplan_v1"
        assert requirements_for_profile(profile.permission_profile_id).required == ("VIEW SERVER STATE",)
    current = plan_profile()
    assert current.permission_profile_id == "sqlserver_plan_2022"
    assert requirements_for_profile(current.permission_profile_id).required == (
        "VIEW SERVER PERFORMANCE STATE",
    )
    future = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2025",
        DetectedDatabaseVersion("17.0.1000.1", "2025"), "plan",
    )
    assert future.support_state == SupportState.UNKNOWN
    assert future.sql_profile_id is None
    mismatch = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2019",
        DetectedDatabaseVersion("16.0.1000.1", "2022"), "plan",
    )
    assert mismatch.version_status == VersionStatus.MISMATCH
    assert mismatch.sql_profile_id is None


def test_plan_collector_rejects_unverified_profile_before_sql() -> None:
    driver = FakeDriver()
    unknown = PROFILE_RESOLVER.resolve(DatabaseType.SQLSERVER, "2022", None, "plan")
    with SQLServerAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                          driver=driver) as adapter:
        with pytest.raises(ToolkitError, match="verified supported diagnostic profile"):
            ExecutionPlanCollector(adapter, unknown)
    assert not any(name in {"plan_request", "plan_cached"} for name in driver.connection.executed)


def test_cached_plan_multiple_rows_are_ambiguous() -> None:
    driver = FakeDriver()
    driver.connection.results.update(plan_request=[], plan_cached=[sample().row, sample().row])
    with SQLServerAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                          driver=driver) as adapter:
        selected = ExecutionPlanCollector(adapter, plan_profile()).collect(51)
    assert selected.status == "PLAN_AMBIGUOUS"
    assert selected.row is None
    assert "no plan was selected" in (selected.note or "")


def test_cached_fallback_requires_statement_offset_identity() -> None:
    driver = FakeDriver()
    active = sample(None).row
    cached = sample().row
    assert active is not None and cached is not None
    active["statement_start_offset"] = None
    cached["statement_start_offset"] = None
    driver.connection.results.update(plan_request=[active], plan_cached=[cached])
    with SQLServerAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                          driver=driver) as adapter:
        selected = ExecutionPlanCollector(adapter, plan_profile()).collect(51)
    assert selected.source == "active_request"
    assert selected.status == "PLAN_NOT_AVAILABLE"


def test_current_plan_and_stale_cache_never_misattributed() -> None:
    driver = FakeDriver()
    active = sample(None).row
    cached = sample().row
    assert active is not None and cached is not None
    cached["sql_handle"] = "0x03"
    driver.connection.results.update(plan_request=[active], plan_cached=[cached])
    with SQLServerAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                          driver=driver) as adapter:
        result = ExecutionPlanCollector(adapter, plan_profile()).collect(51)
    assert result.source == "active_request" and result.row is not None
    assert result.row["plan_xml"] is None and result.note == "Execution plan unavailable."


def test_cli_plan_report_and_no_secret(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    driver.connection.results.update(plan_request=[sample().row], plan_cached=[])
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "version_family": "2022",
        "credential_alias": "monitor"}]}))
    report_path = tmp_path / "plan.html"
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "test", "plan", "--session", "51", "--report", str(report_path),
            "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] and result["report"]["details"]["operator_count"] == 6
    assert result["incident_summary"]["observed_now"] == "ACTIVE"
    assert result["incident_summary"]["report_path"] == str(report_path)
    html = report_path.read_text(encoding="utf-8")
    assert "Execution Plan XML" not in html
    assert "Raw Execution Plan" in html
    assert "ShowPlanXML" not in html
    assert "Important Operators" in html and "Warnings and Index Evidence" in html
    assert "Missing Index Evidence" in html and "Implicit Conversion" in html
    assert report_path.with_suffix(".sqlplan").exists()
    assert "ShowPlanXML" in report_path.with_suffix(".sqlplan").read_text(encoding="utf-8")
    assert "private" not in html
    assert not any(name in driver.connection.executed for name in ("CREATE", "KILL"))
    assert driver.connection.closed
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    assert "File could not be read" in capsys.readouterr().out

    console_path = tmp_path / "plan-console.html"
    console_driver = FakeDriver()
    console_driver.connection.results.update(plan_request=[sample().row], plan_cached=[])
    human_args = [*args[:-1], "--report", str(console_path)]
    assert main(human_args, provider=MemoryCredentialProvider(), driver=console_driver) == 0
    human = capsys.readouterr().out
    assert "Incident Summary" in human and "Detailed Report" in human
    assert "SELECT private" not in human and "Index Scan" not in human


def test_cli_plan_unavailable_and_invalid_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    driver.connection.results.update(plan_request=[], plan_cached=[])
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "version_family": "2022",
        "credential_alias": "monitor"}]}))
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "test", "plan", "--session", "51", "--report", str(tmp_path / "missing.html"), "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    unavailable = json.loads(capsys.readouterr().out)
    assert unavailable["success"] is True
    assert unavailable["report"]["details"]["plan_status"] == "PLAN_NOT_AVAILABLE"
    assert unavailable["report"]["details"]["plan_available"] is False
    assert "Active request no longer exists." in unavailable["report"]["details"]["note"]
    assert "Execution plan unavailable." in (tmp_path / "missing.html").read_text(encoding="utf-8")
    args[args.index("--session") + 1] = "0"
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    assert json.loads(capsys.readouterr().out)["error_type"] == "InvalidSession"


def test_cli_plan_permission_profile_is_versioned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "credential_alias": "monitor", "version_family": "2022"}]}))
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "permissions", "test", "--feature", "plan", "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    feature = json.loads(capsys.readouterr().out)["features"][0]
    assert feature["status"] == "READY"
    assert feature["required_privileges"] == ["VIEW SERVER PERFORMANCE STATE"]
    assert feature["version_profile"]["permission_profile_id"] == "sqlserver_plan_2022"


def test_cli_plan_missing_permission_reports_versioned_reference_grant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = FakeDriver()
    capabilities = driver.connection.results["monitor_capabilities"][0]
    capabilities["view_server_performance_state"] = 0
    capabilities["view_server_state"] = 1
    driver.connection.results["monitor_permissions"] = [
        {"scope": "SERVER", "permission_name": "VIEW SERVER STATE",
         "source_type": "EXPLICIT", "source_name": "dba_monitor", "object_name": None,
         "object_type": None, "execute_as_principal_id": None},
    ]
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "credential_alias": "monitor", "version_family": "2022"}]}))
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "permissions", "test", "--feature", "plan", "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    feature = json.loads(capsys.readouterr().out)["features"][0]
    assert feature["status"] == "MISSING"
    assert feature["missing_required"] == ["VIEW SERVER PERFORMANCE STATE"]
    assert "GRANT VIEW SERVER PERFORMANCE STATE TO [monitor]" in feature["reference_grant"]
    assert feature["toolkit_executed_grant"] is False


def test_cli_plan_unknown_declared_version_never_runs_collector(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = FakeDriver()
    driver.connection.results["version_detect"] = [{"raw_version": "unknown vendor version"}]
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "credential_alias": "monitor", "version_family": "2022"}]}))
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "test", "plan", "--session", "51", "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["error_type"] == "VersionVerificationRequired"
    assert failure["permission_readiness"]["version_profile"]["version_status"] == "UNVERIFIED"
    assert "plan_request" not in driver.connection.executed
    assert "plan_cached" not in driver.connection.executed


def test_cli_plan_version_mismatch_stops_before_plan_sql(tmp_path: Path,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{"id": "test", "name": "Test",
        "db_type": "sqlserver", "host": "test.invalid", "port": 1433,
        "credential_alias": "monitor", "version_family": "2019"}]}))
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "test", "plan", "--session", "51", "--json"]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["error_type"] == "VersionVerificationRequired"
    assert "plan_request" not in driver.connection.executed
    assert "plan_cached" not in driver.connection.executed


@pytest.mark.parametrize("name,bad", [
    ("plan_max_xml_size", 100), ("plan_max_xml_size", 10_000_000),
    ("plan_top_operators", 0), ("plan_large_scan_rows", 0),
    ("plan_cardinality_ratio_warning", 101),
])
def test_plan_settings_validation(name: str, bad: int) -> None:
    with pytest.raises(ValueError):
        SQLServerSettings(**{name: bad})


def test_sqlserver_only() -> None:
    assert replace(INSTANCE, db_type=DatabaseType.SQLSERVER).db_type.value == "sqlserver"
