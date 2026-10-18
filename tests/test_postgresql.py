import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_sqlserver import PASSWORD, ROOT, MemoryCredentialProvider

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.incident import build_incident_summary, build_priority_candidates, manual_action_decisions
from dba.postgresql import PostgreSQLAdapter
from dba.postgresql_collectors import ActiveSessionsCollector, LongRunningCollector
from dba.postgresql_slow import blocking_graph, build_slow_report, collect_slow_phase, diagnose_slow
from dba.postgresql_slow_reporter import render_console, render_markdown
from dba.presentation import render_emergency_triage, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.settings import PostgreSQLSettings

INSTANCE = DatabaseInstance("pg", "PG", DatabaseType.POSTGRESQL, "test.invalid", 5432, "monitor",
                            "postgres", environment="development")
PREFLIGHT = {
    "server_version_num": 160004, "default_transaction_read_only": "on",
    "transaction_read_only": "on", "monitor_user": "monitor",
    "rolsuper": False, "rolcreatedb": False, "rolcreaterole": False,
    "rolreplication": False, "rolbypassrls": False,
    "read_all_stats": True, "read_all_settings": True,
    "activity_visible": True, "locks_visible": True, "database_stats_visible": True,
    "replication_visible": False, "slots_visible": False, "archiver_visible": True,
    "settings_visible": True,
}


class Column:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[Column] | None = None
        self.rows: list[dict[str, Any]] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        name = self.connection.scripts[sql]
        self.connection.executed.append(name)
        if name in self.connection.failures:
            raise self.connection.failures[name]
        self.rows = list(self.connection.results.get(name, []))
        self.description = [Column(key) for key in self.rows[0]] if self.rows else [Column("pid")]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        rows = self.rows[:size]
        return [tuple(row.values()) for row in rows]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, scripts: dict[str, str]) -> None:
        self.scripts = scripts
        self.autocommit = True
        self.closed = False
        self.rollbacks = 0
        self.executed: list[str] = []
        self.cursors: list[FakeCursor] = []
        self.results: dict[str, list[dict[str, Any]]] = {
            "preflight": [dict(PREFLIGHT)],
            "version_detect": [{"raw_version": "16.4", "server_version_num": 160004}],
        }
        self.failures: dict[str, BaseException] = {}

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self) -> None:
        scripts = {path.read_text(encoding="utf-8"): path.stem
                   for path in (ROOT / "sql/postgresql").glob("*.sql")}
        self.connection = FakeConnection(scripts)
        self.options: dict[str, Any] = {}
        self.failure: Exception | None = None

    def connect(self, **kwargs: Any) -> FakeConnection:
        if self.failure:
            raise self.failure
        self.options = kwargs
        return self.connection


def adapter(driver: FakeDriver | None = None,
            settings: PostgreSQLSettings | None = None) -> tuple[PostgreSQLAdapter, FakeDriver]:
    fake = driver or FakeDriver()
    return PostgreSQLAdapter(INSTANCE, MemoryCredentialProvider(),
                             SqlScriptRegistry(ROOT / "sql"), settings, driver=fake), fake


def result(name: str, rows: list[dict[str, Any]], truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("pg", name, now, now, 1.0, tuple(rows), len(rows), truncated, True)


def session(pid: int, state: str = "active", **extra: Any) -> dict[str, Any]:
    return {"pid": pid, "state": state, "datname": "postgres", "usename": "app",
            "application_name": "fixture", "client_addr": "127.0.0.1", "client_hostname": None,
            "backend_start": "2026-01-01T00:00:00",
            "client_port": 40000 + pid, "query": "SELECT 1", "query_start": "2026-01-01",
            "xact_start": "2026-01-01", "transaction_age_seconds": 120,
            "wait_event_type": None, "wait_event": None, "duration_ms": 120000, **extra}


def test_adapter_preflight_credentials_timeout_and_close() -> None:
    target, driver = adapter(settings=PostgreSQLSettings(query_timeout=7, sslmode="disable"))
    with target:
        assert target.get_server_version() == "160004"
        assert target.health_check()
        assert driver.options["connect_timeout"] == 5
        assert driver.options["sslmode"] == "disable"
        assert driver.options["application_name"] == "dba-toolkit"
        assert "default_transaction_read_only=on" in driver.options["options"]
        assert "statement_timeout=5000" in driver.options["options"]
        assert driver.options["password"] == PASSWORD
        assert PASSWORD not in repr(target)
    assert driver.connection.closed and all(cursor.closed for cursor in driver.connection.cursors)


@pytest.mark.parametrize("field,value,error", [
    ("read_all_stats", False, "cross-session activity"),
    ("read_all_settings", False, "monitoring settings"),
    ("rolsuper", True, "non-privileged"),
    ("transaction_read_only", "off", "read-only"),
    ("server_version_num", 110000, "PostgreSQL 12"),
])
def test_preflight_rejects_insufficient_monitor(field: str, value: Any, error: str) -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, field: value}]
    with pytest.raises(ToolkitError, match=error):
        target.connect()
    assert driver.connection.closed


def test_connection_query_failure_timeout_and_registry(capsys: pytest.CaptureFixture[str]) -> None:
    target, driver = adapter()
    driver.failure = RuntimeError(PASSWORD)
    with pytest.raises(ToolkitError, match="Connection failed"):
        target.connect()
    assert PASSWORD not in capsys.readouterr().err
    driver.failure = None
    target.connect()
    before = len(driver.connection.executed)
    rejected = target.execute_readonly("SELECT 1")  # type: ignore[arg-type]
    assert not rejected.success and rejected.error_type == "RegistryRejected"
    assert len(driver.connection.executed) == before

    second, driver2 = adapter()
    driver2.connection.failures["active_sessions"] = TimeoutError(PASSWORD)
    second.connect()
    returned = ActiveSessionsCollector(second).collect(INSTANCE)
    assert not returned.success and returned.error_type == "QueryTimeout"
    assert driver2.connection.closed
    assert PASSWORD not in capsys.readouterr().err


def test_collector_rows_limit_ranking_and_query_redaction() -> None:
    target, driver = adapter(settings=PostgreSQLSettings(long_running_seconds=60))
    driver.connection.results["active_sessions"] = [
        session(1, query="password=hidden"), session(2)]
    driver.connection.results["long_running"] = [
        session(1, duration_ms=90000), session(2, duration_ms=1000)]
    target.connect()
    active = ActiveSessionsCollector(target).collect(INSTANCE)
    assert active.row_count == 2 and "hidden" not in str(active.rows)
    long = LongRunningCollector(target).collect(INSTANCE)
    assert [row["pid"] for row in long.rows] == [1]
    script = target.registry.get("server_info", DatabaseType.POSTGRESQL)
    driver.connection.results["server_info"] = [{"version": "x"}, {"version": "y"}]
    limited = target.execute_readonly(script)
    assert limited.row_count == 2 and limited.truncated


def test_blocking_graph_multilevel_multiple_roots_cycle() -> None:
    graph = blocking_graph([
        {"blocked_pid": 2, "blocker_pid": 1}, {"blocked_pid": 3, "blocker_pid": 2},
        {"blocked_pid": 5, "blocker_pid": 4},
        {"blocked_pid": 7, "blocker_pid": 8}, {"blocked_pid": 8, "blocker_pid": 7},
    ])
    assert graph["roots"] == [1, 4]
    assert graph["children"]["1"] == [2] and graph["children"]["2"] == [3]
    assert graph["unresolved"] == [7, 8]


def test_idle_transaction_root_source_reference_and_report() -> None:
    rows = (result("active_sessions", [
        session(1, "idle in transaction", query="UPDATE fixture SET x=1"),
        session(2, wait_event_type="Lock", wait_event="transactionid"),
        session(3)]),
        result("long_running", [session(3, duration_ms=120000)]),
        result("transactions", [session(1, "idle in transaction")]),
        result("blocking", [{"blocked_pid": 2, "blocker_pid": 1}]))
    report = build_slow_report(INSTANCE, rows, PostgreSQLSettings(), "160004")
    titles = {item.title for item in report.findings}
    assert titles >= {"BLOCKING", "ROOT_BLOCKER", "IDLE_IN_TRANSACTION",
                      "LONG_RUNNING_SQL", "WAITING_SQL"}
    root = report.details["roots"][0]
    assert root["last_query"] and root["current_query"] is None
    assert root["blocked_pids"] == [2] and root["client_addr"] == "127.0.0.1"
    assert root["client_hostname_reason"]
    blocked_query = next(row for row in report.details["top_queries"] if row["pid"] == 2)
    assert blocked_query["blocking_pids"] == [1]
    assert blocked_query["query_summary"] == "SELECT 1"
    refs = next(item.reference_commands for item in report.findings if item.title == "ROOT_BLOCKER")
    assert [cmd.text for cmd in refs] == ["SELECT pg_terminate_backend(1);"]
    assert root["unfinished_transaction_confirmed"] is True
    assert root["manual_action"]["status"] == "AVAILABLE"
    assert all(not hasattr(cmd, "execute") for cmd in refs)
    markdown = render_markdown(report)
    for heading in ("Summary", "Concurrency", "Long Running Queries", "Blocking Tree",
                    "Root Blockers", "Idle in Transaction", "Session Source", "Findings",
                    "Recommendations", "Reference Commands"):
        assert f"## {heading}" in markdown
    assert "Toolkit did not execute" in markdown and "Toolkit did not execute" in render_console(report)


def test_postgres_manual_action_is_shared_and_profile_partial_does_not_suppress() -> None:
    rows = (result("active_sessions", [
        session(2977, "idle in transaction", query="UPDATE fixture SET x=1"),
        session(2978, wait_event_type="Lock", wait_event="transactionid"),
        session(2979, wait_event_type="Lock", wait_event="transactionid")]),
        CollectorResult("pg", "long_running", datetime.now(timezone.utc),
                        datetime.now(timezone.utc), 1.0, (), 0, False, False,
                        "QueryTimeout", "optional profile query timed out"),
        result("transactions", [session(2977, "idle in transaction")]),
        result("blocking", [{"blocked_pid": 2978, "blocker_pid": 2977},
                             {"blocked_pid": 2979, "blocker_pid": 2978}]))
    report = build_slow_report(INSTANCE, rows, PostgreSQLSettings(), "160015",
                               required_collectors=("active_sessions", "transactions", "blocking"))
    report.details.update({"emergency_evidence_complete": True,
                           "full_profile_evidence_status": "PARTIAL",
                           "incomplete": True})
    decisions = manual_action_decisions(report)
    summary = build_incident_summary(report)
    report.details["manual_action_decisions"] = decisions
    cli = render_emergency_triage(report, capture_started_at="captured")
    html = HtmlReportRenderer().render(build_report_model(report, summary))
    payload = render_json({"report": report, "incident_summary": summary,
                           "manual_action_decisions": decisions})
    expected = "SELECT pg_terminate_backend(2977);"
    assert report.details["blocking_graph"]["children"] == {"2977": [2978], "2978": [2979]}
    root = report.details["roots"][0]
    assert (root["pid"], root["direct_blocked_count"], root["indirect_blocked_count"],
            root["blocked_count"]) == (2977, 1, 1, 2)
    assert root["unfinished_transaction_confirmed"] is True
    assert root["identity_quality"] == "SAMPLED_IDENTITY_VERIFIED_RECHECK_REQUIRED"
    assert decisions[0]["commands"] == (expected,)
    assert [item.reference_command for item in summary.recovery_actions] == [expected]
    assert expected in cli and expected in html and expected in payload
    assert "pg_cancel_backend(2977)" not in cli + html + payload
    assert "Toolkit 未执行" in cli
    assert "2026-01-01" in html and "pg_stat_activity" in html
    assert "DMV" not in html and "open_transaction_count" not in html


@pytest.mark.parametrize(("state", "session_updates", "transaction_updates", "blocking_rows", "reason"), [
    ("idle in transaction", {"backend_start": None}, {}, None, "backend_start"),
    ("idle in transaction", {}, {"xact_start": "different"}, None, "xact_start do not match"),
    ("idle in transaction", {"xact_start": None}, {}, None, "session xact_start is unavailable"),
    ("idle in transaction", {}, None, None, "transaction evidence is missing or ambiguous"),
    ("active", {"query_start": None}, {}, None, "query_start"),
    ("active", {"query_truncated": True}, {}, None, "truncated"),
])
def test_postgres_action_suppresses_ambiguous_or_incomplete_identity(
    state: str, session_updates: dict[str, Any], transaction_updates: dict[str, Any] | None,
    blocking_rows: list[dict[str, Any]] | None, reason: str,
) -> None:
    sampled = session(50, state, **session_updates)
    tx_rows = [session(50, state, **(transaction_updates or {}))] if transaction_updates is not None else []
    results = (result("active_sessions", [sampled, session(51)]), result("long_running", []),
               result("transactions", tx_rows),
               result("blocking", blocking_rows or [{"blocked_pid": 51, "blocker_pid": 50}]))
    report = build_slow_report(INSTANCE, results, PostgreSQLSettings(), "160015",
                               required_collectors=("active_sessions", "transactions", "blocking"))
    finding = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert finding.reference_commands == ()
    assert reason in finding.evidence["manual_action"]["reason"]


@pytest.mark.parametrize("results", [
    "duplicate_session", "unknown_root_state", "truncated_blocking", "failed_transactions",
    "missing_root_session", "graph_anomaly",
])
def test_postgres_action_suppressed_for_duplicate_or_incomplete_graph_evidence(results: str) -> None:
    sessions = [session(70, "idle in transaction"), session(71)]
    transactions = [session(70, "idle in transaction")]
    blocking = [{"blocked_pid": 71, "blocker_pid": 70}]
    samples = [result("active_sessions", sessions), result("long_running", []),
               result("transactions", transactions), result("blocking", blocking)]
    if results == "duplicate_session":
        sessions.append(session(70, "idle in transaction", usename="different"))
        samples[0] = result("active_sessions", sessions)
    elif results == "unknown_root_state":
        sessions[0] = session(70, "unknown")
        samples[0] = result("active_sessions", sessions)
    elif results == "truncated_blocking":
        samples[3] = result("blocking", blocking, truncated=True)
    elif results == "failed_transactions":
        now = datetime.now(timezone.utc)
        samples[2] = CollectorResult("pg", "transactions", now, now, 1.0, (), 0,
                                     False, False, "PermissionDenied", "unavailable")
    elif results == "missing_root_session":
        samples[0] = result("active_sessions", [session(71)])
    elif results == "graph_anomaly":
        samples[3] = result("blocking", [*blocking,
                                          {"blocked_pid": 72, "blocker_pid": 73},
                                          {"blocked_pid": 73, "blocker_pid": 72}])
    report = build_slow_report(INSTANCE, tuple(samples), PostgreSQLSettings(), "160015",
                               required_collectors=("active_sessions", "transactions", "blocking"))
    finding = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert not finding.reference_commands


def test_active_postgres_root_uses_cancel_only() -> None:
    rows = (result("active_sessions", [session(90, "active", query="UPDATE fixture SET x=1"),
                                        session(91)]), result("long_running", []),
            result("transactions", [session(90, "active")]),
            result("blocking", [{"blocked_pid": 91, "blocker_pid": 90}]))
    report = build_slow_report(INSTANCE, rows, PostgreSQLSettings(), "160015",
                               required_collectors=("active_sessions", "transactions", "blocking"))
    finding = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert [item.text for item in finding.reference_commands] == ["SELECT pg_cancel_backend(90);"]
    assert "pg_terminate_backend" not in str(finding.reference_commands)


def test_suppressed_postgres_decision_is_identical_across_cli_html_and_json() -> None:
    rows = (result("active_sessions", [session(80, "idle in transaction", xact_start=None),
                                        session(81)]), result("long_running", []),
            result("transactions", [session(80, "idle in transaction")]),
            result("blocking", [{"blocked_pid": 81, "blocker_pid": 80}]))
    report = build_slow_report(INSTANCE, rows, PostgreSQLSettings(), "160015",
                               required_collectors=("active_sessions", "transactions", "blocking"))
    report.details["emergency_evidence_complete"] = True
    report.details["incomplete"] = False
    decisions = manual_action_decisions(report)
    report.details["manual_action_decisions"] = decisions
    summary = build_incident_summary(report)
    cli = render_emergency_triage(report, capture_started_at="captured")
    html = HtmlReportRenderer().render(build_report_model(report, summary))
    payload = render_json({"report": report, "incident_summary": summary,
                           "manual_action_decisions": decisions})
    reason = "session xact_start is unavailable"
    assert decisions[0]["status"] == "SUPPRESSED"
    assert reason in cli and reason in html and reason in payload
    assert "pg_terminate_backend(80)" not in cli + html + payload
    assert "KILL <" not in cli + html + payload


@pytest.mark.parametrize("query", ["SELECT pg_sleep(120);", "SELECT pg_sleep($1)",
                                    "select pg_catalog.pg_sleep(1.5);"])
def test_pg_sleep_wait_is_separated_from_long_sql(query: str) -> None:
    rows = (result("active_sessions", [session(65, query=query, wait_event_type="Timeout",
                                                  wait_event="PgSleep", duration_ms=65000)]),
            result("long_running", [session(65, query=query, duration_ms=65000)]),
            result("transactions", []), result("blocking", []))
    report = build_slow_report(INSTANCE, rows, PostgreSQLSettings(long_running_seconds=60), "160015")
    assert report.details["active_waits"][0]["pid"] == 65
    assert report.details["active_waits"][0]["request_kind"] == "ACTIVE_WAIT"
    assert report.details["long_running"] == []
    assert report.details["active_requests"] == []
    assert not any(item.title == "LONG_RUNNING_SQL" for item in report.findings)
    assert not any(item["session_id"] == 65 for item in build_priority_candidates(report))
    cli = render_emergency_triage(report, capture_started_at="captured")
    html = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    assert "PID 65" in cli and query in cli and "不代表数据库资源瓶颈" in cli
    assert "65" in html and query in html and "pg_sleep" in html


def test_pg_sleep_recognition_requires_timeout_event_simple_sql_and_no_blocker() -> None:
    for query, wait_type, wait_event, truncated, blocked in (
        ("SELECT 'pg_sleep(120)'", "Timeout", "PgSleep", False, False),
        ("SELECT pg_sleep(120); -- comment", "Timeout", "PgSleep", False, False),
        ("SELECT pg_sleep(120), 1", "Timeout", "PgSleep", False, False),
        ("SELECT pg_sleep(120)", "IO", "PgSleep", False, False),
        ("SELECT pg_sleep(120)", "Timeout", "PgSleep", True, False),
        ("SELECT pg_sleep(120)", "Timeout", "PgSleep", False, True),
    ):
        results = (result("active_sessions", [session(65, query=query, wait_event_type=wait_type,
                                                        wait_event=wait_event,
                                                        query_truncated=truncated)]),
                   result("long_running", []), result("transactions", []),
                   result("blocking", [{"blocked_pid": 65, "blocker_pid": 66}] if blocked else []))
        report = build_slow_report(INSTANCE, results, PostgreSQLSettings(), "160015")
        assert report.details["active_waits"] == []


def test_incomplete_snapshot_suppresses_reference() -> None:
    report = build_slow_report(INSTANCE, (
        result("active_sessions", [session(1)], truncated=True),
        result("long_running", []), result("transactions", []),
        result("blocking", [{"blocked_pid": 2, "blocker_pid": 1}]),
    ), PostgreSQLSettings(), "160004")
    assert report.details["incomplete"]
    assert any(item.title == "INCOMPLETE_SNAPSHOT" for item in report.findings)
    assert not any(item.reference_commands for item in report.findings)


def test_optional_collector_failure_returns_incomplete_report() -> None:
    target, driver = adapter()
    driver.connection.results["active_sessions"] = [session(1)]
    driver.connection.failures["long_running"] = TimeoutError(PASSWORD)
    target.connect()
    report = diagnose_slow(target)
    assert report.details["version"] == "160004"
    assert report.details["incomplete"]
    assert report.details["samples"][1]["error_type"] == "QueryTimeout"
    assert driver.connection.closed


def test_supplemental_collection_reconnects_after_optional_failure() -> None:
    target, driver = adapter()
    target.connect()
    existing = (result("active_sessions", [session(1)]),)
    driver.connection.failures["long_running"] = TimeoutError(PASSWORD)
    collected = collect_slow_phase(target, ("long_running", "transactions"), existing)
    assert [item.collector_name for item in collected] == [
        "active_sessions", "long_running", "transactions"
    ]
    assert not collected[1].success
    assert collected[2].success


def test_interrupt_closes_connection() -> None:
    target, driver = adapter()
    driver.connection.failures["active_sessions"] = KeyboardInterrupt()
    target.connect()
    with pytest.raises(KeyboardInterrupt):
        ActiveSessionsCollector(target).collect(INSTANCE)
    assert driver.connection.closed


@pytest.mark.integration
def test_pg_cli_connection_collect_and_diagnose(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [
        {"id": "pg", "name": "PG", "db_type": "postgresql", "host": "test.invalid",
         "port": 5432, "credential_alias": "monitor", "database": "postgres",
             "environment": "development", "version_family": "16"}]}), encoding="utf-8")
    settings = tmp_path / "runtime.yaml"
    settings.write_text(yaml.safe_dump({"postgresql": {"sslmode": "disable"}}), encoding="utf-8")
    common = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
              "--settings", str(settings)]
    driver = FakeDriver()
    driver.connection.results["active_sessions"] = [session(1)]
    provider = MemoryCredentialProvider()
    assert main([*common, "test-connection", "pg", "--json"], provider=provider, driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert provider.aliases == ["monitor"]
    assert main([*common, "collect", "pg", "active-sessions", "--json"],
                provider=provider, driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["pid"] == 1
    report = tmp_path / "slow.html"
    assert main([*common, "diagnose", "pg", "slow", "--format", "json",
                 "--report", str(report)], provider=provider, driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["success"] and report.exists()
    import builtins

    first_stage: list[tuple[str, ...]] = []
    report_absent_at_triage: list[bool] = []
    original_print = builtins.print
    before = len(driver.connection.executed)

    def observe_print(*args: Any, **kwargs: Any) -> None:
        if args and isinstance(args[0], str) and args[0].startswith("数据库性能应急诊断"):
            first_stage.append(tuple(driver.connection.executed[before:]))
            report_absent_at_triage.append(not human_report.exists())
        original_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", observe_print)
    human_report = tmp_path / "slow-human.html"
    assert main([*common, "diagnose", "pg", "slow", "--report", str(human_report)],
                provider=provider, driver=driver) == 0
    assert first_stage and "long_running" not in first_stage[0]
    assert report_absent_at_triage == [True]
    assert first_stage[0].count("active_sessions") == 1
    assert first_stage[0].count("transactions") == 1
    assert first_stage[0].count("blocking") == 1
    after_human = driver.connection.executed[before:]
    assert after_human.count("active_sessions") == 1
    assert after_human.count("transactions") == 1
    assert after_human.count("blocking") == 1
    assert after_human.count("long_running") == 1
    monkeypatch.setattr(builtins, "print", original_print)
    assert main([*common, "diagnose", "pg", "plan", "--session", "1", "--json"],
                provider=provider, driver=driver) == 1
    assert "not implemented" in capsys.readouterr().out.lower()
