from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from dba import memory_grant as memory_grant_module
from dba.cli import main
from dba.core import DatabaseInstance, DatabaseType
from dba.incident import build_incident_summary
from dba.memory_grant import SQLServerMemoryGrantCollector
from dba.memory_grant_profiles import (
    SUPPORTED_FAMILIES,
    validate_memory_grant_profiles,
)
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.sqlserver import SQLServerAdapter
from dba.versioning import (
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DetectedDatabaseVersion,
    SupportState,
)
from tests.test_sqlserver import FakeDriver, MemoryCredentialProvider

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TIME = datetime(2026, 10, 25, 1, 2, 3, tzinfo=timezone.utc)


def _instance() -> DatabaseInstance:
    return DatabaseInstance(
        "sqlserver-lab", "SQL Server lab", DatabaseType.SQLSERVER,
        "sqlserver.invalid", 1433, "monitor-alias", database="master",
        environment="test", version_family="2022",
    )


def _profile(family: str = "2022") -> Any:
    version = "16.0.4305.5" if family == "2022" else "15.0.4415.2"
    return PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, family, DetectedDatabaseVersion(version, family), "memory-grant"
    )


def _grant_row(
    session_id: int,
    request_id: int,
    *,
    granted: bool,
    request_found: bool = True,
    request_session_id: int | None = None,
    request_request_id: int | None = None,
    grant_sql_handle: str | None = "0x0102",
    request_sql_handle: str | None = "0x0102",
    grant_plan_handle: str | None = "0x0304",
    request_plan_handle: str | None = "0x0304",
    request_start_time: datetime | None = SAMPLE_TIME,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "request_id": request_id,
        "request_time": SAMPLE_TIME,
        "grant_time": SAMPLE_TIME if granted else None,
        "requested_memory_kb": 4096,
        "required_memory_kb": 1024,
        "granted_memory_kb": 3072 if granted else None,
        "used_memory_kb": 2048 if granted else None,
        "max_used_memory_kb": 2560 if granted else None,
        "wait_time_ms": 0 if granted else 2400,
        "queue_id": None if granted else 1,
        "wait_order": None if granted else 1,
        "resource_semaphore_id": 0,
        "grant_sql_handle": grant_sql_handle,
        "grant_plan_handle": grant_plan_handle,
        "request_found": int(request_found),
        "request_session_id": (session_id if request_session_id is None and request_found
                               else request_session_id),
        "request_request_id": (request_id if request_request_id is None and request_found
                               else request_request_id),
        "request_start_time": request_start_time if request_found else None,
        "request_status": "suspended" if not granted else "running",
        "wait_type": "RESOURCE_SEMAPHORE" if not granted and request_found else None,
        "database_id": 7 if request_found else None,
        "request_sql_handle": request_sql_handle if request_found else None,
        "request_plan_handle": request_plan_handle if request_found else None,
    }


@pytest.fixture
def driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def adapter(driver: FakeDriver) -> SQLServerAdapter:
    return SQLServerAdapter(
        _instance(), MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
        driver=cast(Any, driver),
    )


def test_memory_grant_registry_and_version_profiles_are_aligned() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_memory_grant_profiles(registry, PROFILE_BINDINGS) == ()
    assert PROFILE_RESOLVER.validate() == ()
    assert {binding.version_family for binding in PROFILE_BINDINGS
            if binding.feature == "memory-grant"
            and binding.support_state == SupportState.SUPPORTED} == SUPPORTED_FAMILIES
    collector_sql = " ".join(
        (ROOT / "sql" / "sqlserver" / f"{name}.sql").read_text(encoding="utf-8").upper()
        for name in ("memory_grant_waiting", "memory_grant_granted")
    )
    assert "SYS.DM_EXEC_QUERY_MEMORY_GRANTS" in collector_sql
    assert "SYS.DM_EXEC_REQUESTS" in collector_sql


def test_memory_grant_queries_are_separate_bounded_read_only_samples() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    for name, condition in (("memory_grant_waiting", "MG.GRANT_TIME IS NULL"),
                            ("memory_grant_granted", "MG.GRANT_TIME IS NOT NULL")):
        script = registry.get(name, DatabaseType.SQLSERVER)
        normalized = " ".join(script.text.upper().split())
        assert script.metadata.reviewed and script.metadata.risk == "READ_ONLY"
        assert script.metadata.timeout_seconds == 5 and script.metadata.max_rows == 101
        assert "SELECT TOP (101)" in normalized and condition in normalized
        assert "LEFT JOIN SYS.DM_EXEC_REQUESTS" in normalized
        assert "R.SESSION_ID = MG.SESSION_ID AND R.REQUEST_ID = MG.REQUEST_ID" in normalized
        assert "ORDER BY" not in normalized and "GROUP BY" not in normalized
        assert "SYS.DM_EXEC_SQL_TEXT" not in normalized and "QUERY_PLAN" not in normalized


def test_collector_preserves_waiting_and_granted_rows_with_same_session_distinct_requests(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [_grant_row(54, 1, granted=False)]
    driver.connection.results["memory_grant_granted"] = [_grant_row(54, 2, granted=True)]
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert result.collection_status == "COLLECTED" and result.completeness == "COMPLETE"
    assert result.waiting.rows[0].request_identity[:2] == (54, 1)
    assert result.granted.rows[0].request_identity[:2] == (54, 2)
    assert result.waiting.rows[0].request_context_status == "MATCHED_AT_SAMPLE"
    assert result.waiting.rows[0].wait_type == "RESOURCE_SEMAPHORE"
    assert result.waiting.rows[0].requested_memory_kb == 4096
    assert result.granted.rows[0].granted_memory_kb == 3072
    assert result.granted.rows[0].used_memory_kb == 2048
    assert driver.connection.executed.count("memory_grant_waiting") == 1
    assert driver.connection.executed.count("memory_grant_granted") == 1
    # Each nonempty sample is followed by one empty fetch; each pull is bounded.
    assert driver.connection.fetch_sizes[-4:] == [1, 1, 1, 1]
    assert driver.connection.timeouts[-2:] == [5, 5]
    adapter.disconnect()


def test_client_elapsed_is_measured_and_propagated_to_json(
    adapter: SQLServerAdapter, driver: FakeDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver.connection.results["memory_grant_waiting"] = []
    driver.connection.results["memory_grant_granted"] = []
    ticks = iter((100.0, 100.0125, 200.0, 200.0100))
    monkeypatch.setattr(memory_grant_module.time, "perf_counter", lambda: next(ticks))
    adapter.connect()

    payload = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance).to_dict()
    adapter.disconnect()

    assert payload["waiting_sample"]["elapsed_ms"] == pytest.approx(12.5)
    assert payload["granted_sample"]["elapsed_ms"] == pytest.approx(10.0)
    for sample in (payload["waiting_sample"], payload["granted_sample"]):
        assert sample["elapsed_scope"] == "CLIENT_END_TO_END"
        assert "server-side SQL execution duration is not separately measured" in sample["elapsed_note"]


def test_missing_request_join_preserves_grant_evidence_and_marks_partial(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [
        _grant_row(61, 4, granted=False, request_found=False),
    ]
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)
    row = result.waiting.rows[0]

    assert row.session_id == 61 and row.request_id == 4
    assert row.request_context_status == "REQUEST_NOT_FOUND"
    assert row.wait_type is None and row.database_id is None
    assert result.completeness == "PARTIAL"
    assert result.collection_status == "PARTIAL"
    adapter.disconnect()


def test_incomplete_request_time_marks_memory_grant_partial(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [
        _grant_row(61, 5, granted=False, request_start_time=None),
    ]
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert result.waiting.rows[0].request_context_status == "IDENTITY_INCOMPLETE"
    assert result.completeness == "PARTIAL" and result.collection_status == "PARTIAL"
    adapter.disconnect()


def test_conflicting_request_identity_and_duplicate_grant_identity_are_partial(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [
        _grant_row(62, 3, granted=False, request_session_id=63),
        _grant_row(62, 3, granted=False),
    ]
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert all(item.request_context_status == "DUPLICATE_GRANT_IDENTITY"
               for item in result.waiting.rows)
    assert result.identity_conflicts == 2
    assert result.completeness == "PARTIAL"
    adapter.disconnect()


def test_inconsistent_handles_do_not_confirm_request_identity(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [
        _grant_row(64, 5, granted=False, request_sql_handle="0xFFFF"),
    ]
    adapter.connect()
    row = SQLServerMemoryGrantCollector(adapter, _profile()).collect(
        adapter.instance).waiting.rows[0]

    assert row.request_context_status == "IDENTITY_CONFLICT"
    adapter.disconnect()


def test_query_failure_does_not_hide_the_successful_other_sample(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [_grant_row(65, 6, granted=False)]
    driver.connection.failures["memory_grant_granted"] = RuntimeError("PWD=DBA-Test-Secret-not-logged")
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert result.waiting.success is True and result.granted.success is False
    assert result.collection_status == "PARTIAL" and result.completeness == "PARTIAL"
    assert "DBA-Test-Secret-not-logged" not in (result.granted.error_message or "")
    adapter.disconnect()


def test_query_timeout_is_reported_and_successful_sample_is_preserved(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = [_grant_row(66, 8, granted=False)]
    driver.connection.failures["memory_grant_granted"] = RuntimeError(
        "HYT00", "PWD=DBA-Test-Secret-timeout"
    )
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert result.waiting.success is True
    assert result.granted.success is False and result.granted.error_type == "QueryTimeout"
    assert result.collection_status == "PARTIAL" and result.completeness == "PARTIAL"
    assert "DBA-Test-Secret-timeout" not in (result.granted.error_message or "")
    assert driver.connection.timeouts[-1] == 5
    adapter.disconnect()


def test_101st_row_is_a_bounded_truncation_sentinel(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["memory_grant_waiting"] = []
    driver.connection.results["memory_grant_granted"] = [
        _grant_row(100 + index, 0, granted=True) for index in range(101)
    ]
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)

    assert result.granted.row_count == 101
    assert len(result.granted.rows) == 100
    assert result.granted.truncated is True
    assert result.completeness == "PARTIAL"
    adapter.disconnect()


def test_empty_waiting_and_granted_samples_are_complete_but_not_health() -> None:
    driver = FakeDriver()
    driver.connection.results["memory_grant_waiting"] = []
    driver.connection.results["memory_grant_granted"] = []
    adapter = SQLServerAdapter(
        _instance(), MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
        driver=cast(Any, driver),
    )
    adapter.connect()
    result = SQLServerMemoryGrantCollector(adapter, _profile()).collect(adapter.instance)
    adapter.disconnect()

    assert result.completeness == "COMPLETE" and result.collection_status == "COLLECTED"
    assert result.waiting.rows == () and result.granted.rows == ()
    assert result.health_assessment == "NOT_ASSESSED"


def test_memory_grant_only_report_is_not_a_health_assessment() -> None:
    from dba.memory_grant import MemoryGrantDiagnosticReport

    waiting_row = _grant_row(67, 1, granted=False)
    waiting_row["request_identity"] = {
        "session_id": 67, "request_id": 1, "request_context_status": "MATCHED_AT_SAMPLE",
        "request_status": "suspended", "wait_type": "RESOURCE_SEMAPHORE",
    }
    granted_row = _grant_row(68, 2, granted=True)
    granted_row["request_identity"] = {
        "session_id": 68, "request_id": 2, "request_context_status": "MATCHED_AT_SAMPLE",
        "request_status": "running", "wait_type": None,
    }

    report = MemoryGrantDiagnosticReport(_instance(), SAMPLE_TIME, "memory-grant", (), {
        "version": "16.0.4305.5",
        "memory_grant": {
            "database_id": "sqlserver-lab", "database_version": "16.0.4305.5",
            "collection_status": "COLLECTED",
            "completeness": "COMPLETE", "health_assessment": "NOT_ASSESSED",
            "waiting_count": 1, "granted_count": 1,
            "waiting_sample": {"started_at": "before", "finished_at": "after", "elapsed_ms": 12.5,
                    "elapsed_scope": "CLIENT_END_TO_END", "row_count": 1,
                    "success": True, "truncated": False, "rows": [waiting_row]},
            "granted_sample": {"started_at": "before2", "finished_at": "after2", "elapsed_ms": 10.0,
                    "elapsed_scope": "CLIENT_END_TO_END", "row_count": 1,
                    "success": True, "truncated": False, "rows": [granted_row]},
            "snapshot_consistency": "SEQUENTIAL_BEST_EFFORT_NOT_ATOMIC",
            "identity_conflicts": 0,
        },
        "samples": [], "incomplete": False,
        "snapshot_consistency": "SEQUENTIAL_BEST_EFFORT_NOT_ATOMIC",
    })
    incident = build_incident_summary(report)
    html = HtmlReportRenderer().render(build_report_model(report, incident))

    assert incident.status == "UNKNOWN" and incident.observed_now == "NOT_ASSESSED"
    assert incident.immediate_action is None
    assert "Waiting for Memory Grant" in html and "Granted Memory" in html
    assert "Requested (KB)" in html and "Granted (KB)" in html and "Used (KB)" in html
    assert "RESOURCE_SEMAPHORE" in html
    assert "12.500 ms (client end-to-end)" in html
    assert "10.000 ms (client end-to-end)" in html
    assert "server-side SQL execution duration is not separately measured" in html
    assert "Overall database health: NOT ASSESSED" not in html  # only human CLI includes this banner


def test_memory_grant_cli_wires_json_and_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], driver: FakeDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "sqlserver-lab", "name": "SQL Server lab", "db_type": "sqlserver",
        "host": "sqlserver.invalid", "port": 1433, "version_family": "2022",
        "credential_alias": "monitor-alias", "database": "master", "environment": "test",
    }]}), encoding="utf-8")
    driver.connection.results["memory_grant_waiting"] = [_grant_row(69, 1, granted=False)]
    driver.connection.results["memory_grant_granted"] = [_grant_row(70, 2, granted=True)]
    ticks = iter((300.0, 300.00825, 400.0, 400.00375))
    monkeypatch.setattr(memory_grant_module.time, "perf_counter", lambda: next(ticks))
    report_path = tmp_path / "memory-grant.html"
    base = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql")]

    assert main([*base, "diagnose", "sqlserver-lab", "memory-grant", "--report",
                 str(report_path), "--json"], provider=MemoryCredentialProvider(), driver=driver) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["memory_grant"]["waiting_count"] == 1
    assert payload["memory_grant"]["granted_count"] == 1
    assert "permission_readiness" not in payload
    assert "permission_status" not in payload["memory_grant"]
    assert payload["memory_grant"]["waiting_sample"]["elapsed_ms"] == pytest.approx(8.25)
    assert payload["memory_grant"]["granted_sample"]["elapsed_ms"] == pytest.approx(3.75)
    assert payload["incident_summary"]["observed_now"] == "NOT_ASSESSED"
    assert "memory_grant" not in payload["report"]["details"]
    assert payload["report"]["instance"]["credential_alias"] == "[REDACTED]"
    assert "monitor-alias" not in json_output
    assert report_path.is_file()
    html = report_path.read_text(encoding="utf-8")
    assert "Waiting for Memory Grant" in html and "Granted Memory" in html
    assert "4096" in html and "2048" in html
    assert driver.connection.executed.count("memory_grant_waiting") == 1
    assert driver.connection.executed.count("memory_grant_granted") == 1
