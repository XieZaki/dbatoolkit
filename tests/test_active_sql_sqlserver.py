from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from dba.active_sql_collectors import SQLServerActiveSQLCollector
from dba.active_sql_profiles import validate_active_sql_profiles
from dba.active_sql_report import (
    ActiveSQLDiagnosticReport,
    active_sql_sections,
    render_active_sql_human,
)
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.incident import build_incident_summary
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.sqlserver import SQLServerAdapter
from dba.versioning import PROFILE_BINDINGS, PROFILE_RESOLVER, DetectedDatabaseVersion
from tests.test_sqlserver import FakeDriver, MemoryCredentialProvider

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_TIME = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _profile(family: str = "2022") -> Any:
    version = "16.0.4305.5" if family == "2022" else "10.50.6000.34"
    return PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, family, DetectedDatabaseVersion(version, family), "active-sql"
    )


def _row(
    session_id: int,
    *,
    request_id: int = 0,
    cpu: int = 0,
    reads: int = 0,
    elapsed: int = 0,
    handle: str | None = "0x01",
    query_hash: str | None = "0xAA",
    plan_handle: str | None = "0x11",
    start_offset: int | None = 0,
    end_offset: int | None = 18,
    database_id: int | None = 7,
    text: str | None = "SELECT 1",
    text_available: int = 1,
    text_truncated: int = 0,
    start_time: datetime | str = SNAPSHOT_TIME,
    wait_type: str | None = None,
    wait_time_ms: int | None = None,
    blocking_session_id: int | None = None,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "request_id": request_id,
        "start_time": start_time,
        "cpu_time_ms": cpu,
        "logical_reads": reads,
        "elapsed_time_ms": elapsed,
        "wait_type": wait_type,
        "wait_time_ms": wait_time_ms,
        "blocking_session_id": blocking_session_id,
        "sql_handle": handle,
        "query_hash": query_hash,
        "plan_handle": plan_handle,
        "statement_start_offset": start_offset,
        "statement_end_offset": end_offset,
        "database_id": database_id,
        "sql_text_available": text_available,
        "sql_text_truncated": text_truncated,
        "statement_text": text,
    }


@pytest.fixture
def driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def adapter(driver: FakeDriver) -> SQLServerAdapter:
    instance = DatabaseInstance(
        "sqlserver-lab", "SQL Server lab", DatabaseType.SQLSERVER, "sqlserver.invalid", 1433,
        "monitor-alias", database="master", environment="test", version_family="2022",
    )
    return SQLServerAdapter(
        instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
        driver=cast(Any, driver),
    )


def test_active_sql_profile_and_registry_routes_are_version_aware() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_active_sql_profiles(registry, PROFILE_BINDINGS) == ()
    script = registry.get("active_sql", DatabaseType.SQLSERVER)
    assert script.metadata.max_rows == 301
    assert script.metadata.risk == "READ_ONLY" and script.metadata.reviewed
    assert set(script.metadata.version_families) == {
        "2008r2", "2012", "2014", "2016", "2017", "2019", "2022",
    }


def test_registry_uses_one_2008r2_compatible_query_with_bounded_three_metric_union() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    script = registry.get("active_sql", DatabaseType.SQLSERVER)
    sql = " ".join(script.text.split()).upper()

    assert sql.count("?") == 3
    assert sql.count("ROW_NUMBER() OVER") == 3
    assert "UNION" in sql and "INNER JOIN CANDIDATE_REQUESTS" in sql
    assert "SYS.DM_EXEC_REQUESTS" in sql and "SYS.DM_EXEC_SQL_TEXT" in sql
    assert "STATEMENT_START_OFFSET" in sql and "STATEMENT_END_OFFSET" in sql
    assert "DATALENGTH(ST.TEXT)" in sql and "LEFT(STMT.STATEMENT_SAMPLE, 2000)" in sql
    assert "R.SESSION_ID <> @@SPID" in sql and "S.IS_USER_PROCESS = 1" in sql
    assert "ISNULL(S.PROGRAM_NAME, N'') <> N'DBA-TOOLKIT'" in sql
    assert "OFFSET 1 ROWS" not in sql and "FETCH NEXT" not in sql
    assert all(token not in sql for token in ("STRING_AGG(", "DATEDIFF_BIG(", "TRY_CONVERT("))
    assert "SQL_HANDLE" in sql and "QUERY_HASH" in sql and "PLAN_HANDLE" in sql


def test_active_sql_collector_ranks_all_metrics_and_shares_only_exact_statement_identity(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = [
        _row(51, cpu=300, reads=10, elapsed=10, handle="0x01", plan_handle="0x11"),
        _row(52, cpu=200, reads=30, elapsed=20, handle="0x01", plan_handle="0x12"),
        _row(53, cpu=100, reads=20, elapsed=40, handle="0x02", plan_handle="0x21",
             text="SELECT 2"),
        _row(54, cpu=50, reads=40, elapsed=30, handle="0x03", plan_handle="0x31",
             text="SELECT 3"),
    ]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(
        adapter.instance, sort="logical-reads", top_n=2
    )
    payload = result.to_dict()

    assert result.collection_status == "COLLECTED" and result.completeness == "COMPLETE"
    assert result.selected_sort == "logical-reads" and result.candidate_count == 4
    assert [item.session_id for item in result.rankings["cpu"]] == [51, 52]
    assert [item.session_id for item in result.rankings["logical-reads"]] == [54, 52]
    assert [item.session_id for item in result.rankings["elapsed"]] == [53, 54]
    assert [item.sql_ref for item in result.rankings["cpu"]] == ["SQL-001", "SQL-001"]
    shared = next(item for item in result.sql_texts if item.sql_ref == "SQL-001")
    assert shared.sql_handle == "0x01" and set(shared.plan_handles) == {"0x11", "0x12"}
    assert shared.query_hash == ("0xAA",)
    assert shared.statement_start_offset == 0 and shared.statement_end_offset == 18
    assert shared.database_id == 7
    assert len(result.sql_texts) == 3  # same query_hash does not merge different handles
    assert {item.statement_text for item in result.sql_texts} == {"SELECT 1", "SELECT 2", "SELECT 3"}
    assert all("statement_text" not in item for ranking in payload["rankings"].values()
               for item in ranking)
    assert payload["collection_scope"] == "INSTANCE"
    assert payload["evidence_source"] == (
        "sys.dm_exec_requests, sys.dm_exec_sessions, sys.dm_exec_sql_text"
    )
    assert payload["rankings"]["cpu"][0]["metric_unit"] == "milliseconds"
    assert payload["rankings"]["cpu"][0]["metric_semantics"] == "CURRENT_REQUEST_CUMULATIVE"
    assert payload["rankings"]["logical-reads"][0]["metric_unit"] == "pages"
    assert driver.connection.executed[-1] == "active_sql"
    assert driver.connection.bound_parameters[-1] == (2, 2, 2)
    # The shared byte-bounded fetcher requests one row at a time.
    assert driver.connection.fetch_sizes[-5:] == [1] * 5
    assert driver.connection.timeouts[-1] == 10
    adapter.disconnect()


@pytest.mark.parametrize("top_n", [0, -1, 101, True, 1.5])
def test_active_sql_collector_rejects_invalid_or_unbounded_top_n(
    adapter: SQLServerAdapter, top_n: Any,
) -> None:
    collector = SQLServerActiveSQLCollector(adapter, _profile())
    with pytest.raises(ToolkitError, match="between 1 and 100"):
        collector.collect(adapter.instance, top_n=top_n)


def test_active_sql_collector_accepts_maximum_top_n_and_exact_300_without_truncation(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = [
        _row(session_id, cpu=300 - session_id, handle=f"0x{session_id:04X}")
        for session_id in range(1, 301)
    ]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance, top_n=100)

    assert result.candidate_count == 300 and not result.truncated
    assert result.completeness == "COMPLETE"
    assert all(len(items) == 100 for items in result.rankings.values())
    assert driver.connection.bound_parameters[-1] == (100, 100, 100)
    # 300 rows plus the final empty fetch, still bounded to one row per pull.
    assert driver.connection.fetch_sizes[-301:] == [1] * 301
    adapter.disconnect()


def test_active_sql_collector_defaults_to_cpu_top_ten_and_parameterizes_limit(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = [_row(51, cpu=10)]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance)

    assert result.selected_sort == "cpu" and result.top_n == 10
    assert driver.connection.bound_parameters[-1] == (10, 10, 10)
    adapter.disconnect()


def test_active_sql_adapter_rejects_unbound_or_invalid_top_n(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    adapter.connect()
    script = adapter.registry.get("active_sql", DatabaseType.SQLSERVER)
    before = driver.connection.executed.count("active_sql")
    with pytest.raises(ToolkitError, match="matching controlled parameters"):
        adapter.execute_readonly(script)
    with pytest.raises(ToolkitError, match="between 1 and 100"):
        adapter.execute_readonly(script, active_sql_top_n=101)
    assert driver.connection.executed.count("active_sql") == before
    adapter.disconnect()


def test_active_sql_collector_deduplicates_same_request_and_keeps_start_time_identity(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    first = _row(51, cpu=20)
    second = _row(51, cpu=30, start_time="2026-10-24T12:01:00+00:00")
    driver.connection.results["active_sql"] = [first, first.copy(), second]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance, top_n=10)

    assert result.candidate_count == 2
    assert len(result.rankings["cpu"]) == 2
    assert len({(item.session_id, item.request_id, item.start_time)
                for item in result.rankings["cpu"]}) == 2
    adapter.disconnect()


def test_active_sql_collector_reports_unavailable_and_truncated_statement_text_as_partial(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = [
        _row(51, text=None, text_available=0, handle="0x01"),
        _row(52, text="x" * 2000, text_truncated=1, handle="0x02"),
    ]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance)

    assert result.completeness == "PARTIAL"
    statuses = {item.text_status: item for item in result.sql_texts}
    assert statuses["UNAVAILABLE"].statement_text is None
    assert statuses["TRUNCATED"].statement_text == "x" * 2000
    adapter.disconnect()


def test_active_sql_collector_rejects_server_text_over_reviewed_limit(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = [_row(51, text="x" * 2001)]
    adapter.connect()
    with pytest.raises(ToolkitError, match="2000-character limit"):
        SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance)
    adapter.disconnect()


def test_empty_active_sql_snapshot_is_collected_without_fabricated_ranking(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["active_sql"] = []
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(adapter.instance)
    payload = result.to_dict()

    assert result.collection_status == "COLLECTED" and result.completeness == "COMPLETE"
    assert result.candidate_count == 0 and payload["sql_texts"] == []
    assert payload["rankings"] == {"cpu": [], "logical-reads": [], "elapsed": []}
    adapter.disconnect()


def test_active_sql_cli_json_html_share_one_collector_result_and_link_sql_text_once(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    sql = "SELECT '<script>not markup</script>' AS sample"
    driver.connection.results["active_sql"] = [
        _row(51, cpu=80, reads=4, elapsed=10, text=sql, handle="0x01"),
        _row(52, cpu=20, reads=9, elapsed=5, text=sql, handle="0x01", plan_handle="0x12"),
    ]
    adapter.connect()
    result = SQLServerActiveSQLCollector(adapter, _profile()).collect(
        adapter.instance, sort="cpu", top_n=10
    )
    payload = json.loads(json.dumps(result.to_dict()))
    cli = render_active_sql_human(payload)
    report = ActiveSQLDiagnosticReport(
        adapter.instance, result.collection_timestamp, "active-sql", (),
        {"version": result.database_version, "active_sql": payload},
    )
    incident = build_incident_summary(report)
    model = build_report_model(report, incident)
    html = HtmlReportRenderer().render(model)

    assert payload["rankings"]["cpu"][0]["session_id"] == 51
    assert payload["rankings"]["logical-reads"][0]["session_id"] == 52
    assert payload["rankings"]["elapsed"][0]["session_id"] == 51
    assert payload["rankings"]["cpu"][0]["metric_value"] == 80
    assert payload["rankings"]["cpu"][0]["metric_unit"] == "milliseconds"
    assert payload["rankings"]["logical-reads"][0]["metric_unit"] == "pages"
    assert payload["sql_texts"][0]["plan_identities"]
    assert len(payload["sql_texts"]) == 1
    assert cli.count(sql) == 1 and "Top N: 10" in cli
    assert "Current request CPU time (milliseconds)" in cli
    assert 'href="#sql-text-sql-001"' in html and 'id="sql-text-sql-001"' in html
    assert html.count("&lt;script&gt;not markup&lt;/script&gt;") == 1
    assert "<script>not markup</script>" not in html
    assert incident.status == "UNKNOWN" and incident.observed_now == "NOT_ASSESSED"
    adapter.disconnect()


def test_active_sql_terminal_text_cannot_emit_ansi_control_sequences() -> None:
    payload = {
        "collection_status": "COLLECTED", "completeness": "COMPLETE",
        "sort": "cpu", "top_n": 1,
        "rankings": {"cpu": [{"rank": 1, "sql_ref": "SQL-001", "session_id": 51,
            "request_id": 0, "metric_value": 1, "metric_unit": "milliseconds",
            "wait_type": None, "blocking_session_id": None}],
            "logical-reads": [], "elapsed": []},
        "sql_texts": [{"sql_ref": "SQL-001", "text_status": "AVAILABLE", "query_hash": [],
            "sql_handle": "0x01", "statement_start_offset": 0, "statement_end_offset": 2,
            "plan_identities": [], "statement_text": "SELECT 1\x1b[2J"}],
    }
    rendered = render_active_sql_human(payload)
    assert "\x1b" not in rendered and r"\x1b[2J" in rendered


def test_active_sql_cli_entry_uses_one_collector_call_and_writes_json_and_offline_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], driver: FakeDriver,
) -> None:
    from dba.cli import main

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "sqlserver-lab", "name": "SQL Server lab", "db_type": "sqlserver",
        "host": "sqlserver.invalid", "port": 1433, "version_family": "2022",
        "credential_alias": "monitor-alias", "database": "master", "environment": "test",
    }]}), encoding="utf-8")
    driver.connection.results["active_sql"] = [_row(51, cpu=80, reads=12, elapsed=20)]
    report_path = tmp_path / "active-sql.html"
    command = [
        "--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
        "diagnose", "sqlserver-lab", "active-sql", "--sort", "logical-reads", "--top", "3",
        "--report", str(report_path), "--json",
    ]

    assert main(command, provider=MemoryCredentialProvider(), driver=driver) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["active_sql"]["sort"] == "logical-reads"
    assert payload["active_sql"]["top_n"] == 3
    assert payload["active_sql"]["rankings"]["logical-reads"][0]["metric_value"] == 12
    assert len(payload["active_sql"]["sql_texts"]) == 1
    assert "active_sql" not in payload["report"]["details"]
    assert driver.connection.executed.count("active_sql") == 1
    assert driver.connection.bound_parameters[-1] == (3, 3, 3)
    assert report_path.is_file()
    html = report_path.read_text(encoding="utf-8")
    assert "SQL 文本" in html and "SQL-001" in html


def test_active_sql_report_does_not_repeat_sql_text_in_rankings() -> None:
    payload = {
        "collection_status": "COLLECTED", "sort": "cpu", "top_n": 1,
        "rankings": {"cpu": [{"rank": 1, "sql_ref": "SQL-001", "session_id": 51,
            "request_id": 0, "request_identity": {"session_id": 51, "request_id": 0,
                "start_time": "2026-10-24T12:00:00"}, "metric_value": 4,
            "metric_unit": "milliseconds"}], "logical-reads": [], "elapsed": []},
        "sql_texts": [{"sql_ref": "SQL-001", "identity_status": "NATIVE_STATEMENT",
            "query_hash": [], "sql_handle": "0x01", "statement_start_offset": 0,
            "statement_end_offset": 2, "database_id": 7, "plan_identities": [],
            "statement_text": "SELECT 1", "text_status": "AVAILABLE"}],
    }
    sections = active_sql_sections(payload)
    ranking_tables = [table for section in sections[:3] for table in section.tables]
    sql_blocks = sections[3].code_blocks
    assert sum("SELECT 1" in " ".join(row) for table in ranking_tables for row in table.rows) == 0
    assert len(sql_blocks) == 1 and sql_blocks[0].content.count("SELECT 1") == 1
