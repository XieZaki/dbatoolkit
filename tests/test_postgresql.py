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
from dba.postgresql import PostgreSQLAdapter
from dba.postgresql_collectors import ActiveSessionsCollector, LongRunningCollector
from dba.postgresql_slow import blocking_graph, build_slow_report, diagnose_slow
from dba.postgresql_slow_reporter import render_console, render_markdown
from dba.registry import SqlScriptRegistry
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
    assert [cmd.text for cmd in refs] == [
        "SELECT pg_cancel_backend(1);", "SELECT pg_terminate_backend(1);"]
    assert all(not hasattr(cmd, "execute") for cmd in refs)
    markdown = render_markdown(report)
    for heading in ("Summary", "Concurrency", "Long Running Queries", "Blocking Tree",
                    "Root Blockers", "Idle in Transaction", "Session Source", "Findings",
                    "Recommendations", "Reference Commands"):
        assert f"## {heading}" in markdown
    assert "Toolkit did not execute" in markdown and "Toolkit did not execute" in render_console(report)


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


def test_interrupt_closes_connection() -> None:
    target, driver = adapter()
    driver.connection.failures["active_sessions"] = KeyboardInterrupt()
    target.connect()
    with pytest.raises(KeyboardInterrupt):
        ActiveSessionsCollector(target).collect(INSTANCE)
    assert driver.connection.closed


@pytest.mark.integration
def test_pg_cli_connection_collect_and_diagnose(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
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
    assert main([*common, "diagnose", "pg", "plan", "--session", "1", "--json"],
                provider=provider, driver=driver) == 1
    assert "not implemented" in capsys.readouterr().out.lower()
