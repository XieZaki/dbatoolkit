import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from test_sqlserver import PASSWORD, ROOT, MemoryCredentialProvider

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider
from dba.errors import ToolkitError
from dba.mysql import MySQLAdapter
from dba.mysql_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    LongRunningCollector,
    host_ip,
)
from dba.mysql_slow import build_slow_report
from dba.mysql_slow_reporter import render_console, render_markdown
from dba.registry import SqlScriptRegistry
from dba.settings import MySQLSettings

INSTANCE = DatabaseInstance("my", "MySQL", DatabaseType.MYSQL, "test.invalid", 3306,
                            "monitor", "test", environment="development")
PREFLIGHT = {"server_version": "8.0.44", "monitor_user": "toolkit_monitor@%",
             "active_roles": "NONE", "performance_schema_enabled": 1,
             "connection_id": 9, "privileged_grants": 0, "non_readonly_grants": 0,
             "statements_consumer": 1, "waits_consumer": 1}


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] | None = None
        self.rows: list[dict[str, Any]] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        name = self.connection.scripts[sql]
        self.connection.executed.append(name)
        if name in self.connection.failures:
            raise self.connection.failures[name]
        self.rows = list(self.connection.results.get(name, []))
        self.description = [(key,) for key in self.rows[0]] if self.rows else [("id",)]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return [tuple(row.values()) for row in self.rows[:size]]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, scripts: dict[str, str]) -> None:
        self.scripts = scripts
        self.results: dict[str, list[dict[str, Any]]] = {
            "preflight": [dict(PREFLIGHT)],
            "monitor_grants": [
                {"Grants for toolkit_monitor@%":
                 "GRANT PROCESS ON *.* TO `toolkit_monitor`@`%`"},
                {"Grants for toolkit_monitor@%":
                 "GRANT SELECT ON `performance_schema`.* TO `toolkit_monitor`@`%`"},
            ],
        }
        self.failures: dict[str, BaseException] = {}
        self.executed: list[str] = []
        self.cursors: list[FakeCursor] = []
        self.closed = False
        self.autocommit = True
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        result = FakeCursor(self)
        self.cursors.append(result)
        return result

    def get_autocommit(self) -> bool:
        return self.autocommit

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self) -> None:
        scripts = {path.read_text(encoding="utf-8"): path.stem
                   for path in (ROOT / "sql/mysql").glob("*.sql")}
        self.connection = FakeConnection(scripts)
        self.options: dict[str, Any] = {}
        self.failure: Exception | None = None

    def connect(self, **kwargs: Any) -> FakeConnection:
        if self.failure:
            raise self.failure
        self.options = kwargs
        return self.connection


def adapter(driver: FakeDriver | None = None,
            settings: MySQLSettings | None = None) -> tuple[MySQLAdapter, FakeDriver]:
    fake = driver or FakeDriver()
    return MySQLAdapter(INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                        settings, driver=fake), fake


def result(name: str, rows: list[dict[str, Any]], truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("my", name, now, now, 1.0, tuple(rows), len(rows), truncated, True)


def session(pid: int, command: str = "Query") -> dict[str, Any]:
    return {"processlist_id": pid, "thread_id": pid + 100, "user_name": "app",
            "host_raw": "127.0.0.1:4444", "host_ip": "127.0.0.1",
            "database_name": "test", "command_name": command,
            "state_name": None, "sql_text": None if command == "Sleep" else "SELECT 1"}


def test_adapter_preflight_credentials_timeout_registry_and_cleanup() -> None:
    target, driver = adapter(settings=MySQLSettings(query_timeout=7, ssl_enabled=False))
    with target:
        assert target.get_server_version() == "8.0.44" and target.health_check()
        assert driver.options["connect_timeout"] == 5
        assert driver.options["read_timeout"] == 5
        assert driver.options["ssl_disabled"] and not driver.options["local_infile"]
        assert driver.options["password"] == PASSWORD
        assert driver.options["database"] == INSTANCE.database
        assert driver.options["ssl_disabled"] is True
        assert driver.options["connect_timeout"] == 5
        assert driver.options["read_timeout"] == 5
        assert driver.options["write_timeout"] == 5
        assert PASSWORD not in repr(target)
        before = len(driver.connection.executed)
        rejected = target.execute_readonly("SELECT 1")  # type: ignore[arg-type]
        assert not rejected.success and rejected.error_type == "RegistryRejected"
        assert len(driver.connection.executed) == before
    assert driver.connection.closed and all(item.closed for item in driver.connection.cursors)


@pytest.mark.parametrize("code,classification", [
    (1045, "AUTHENTICATION_FAILED"), (1049, "DATABASE_NOT_FOUND"),
    (2003, "CONNECTION_FAILED"), (2005, "DNS_RESOLUTION_FAILED"),
    (2006, "SERVER_GONE_AWAY"), (2013, "CONNECTION_LOST"),
])
def test_connect_failure_preserves_mysql_vendor_error(code: int, classification: str) -> None:
    target, driver = adapter()
    driver.failure = Exception(code, f"vendor failure {code}")
    with pytest.raises(ToolkitError) as caught:
        target.connect()
    detail = caught.value.database_error
    assert detail is not None
    assert detail.vendor_code == code
    assert detail.classification == classification
    assert f"vendor failure {code}" in detail.message


def test_mysql_connect_options_match_explicit_profile_without_exposing_password() -> None:
    target, driver = adapter(settings=MySQLSettings(ssl_enabled=True, connect_timeout=7,
                                                    query_timeout=12))
    target.connect()
    assert driver.options["host"] == INSTANCE.host
    assert driver.options["port"] == INSTANCE.port
    assert driver.options["user"] == "monitor"
    assert driver.options["password"] == PASSWORD
    assert driver.options["database"] == INSTANCE.database
    assert driver.options["connect_timeout"] == 7
    assert driver.options["ssl_disabled"] is False
    assert driver.options["ssl_verify_cert"] is True
    assert driver.options["ssl_verify_identity"] is True
    assert PASSWORD not in repr(target)
    target.disconnect()


def test_mysql_database_unset_and_credential_password_are_exact() -> None:
    class ExactProvider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            assert alias == INSTANCE.credential_alias
            return Credential("monitor", "  DBA test 密码!?  ")

    no_database = DatabaseInstance(
        INSTANCE.id, INSTANCE.name, INSTANCE.db_type, INSTANCE.host, INSTANCE.port,
        INSTANCE.credential_alias, None, environment="development",
    )
    fake = FakeDriver()
    target = MySQLAdapter(no_database, ExactProvider(), SqlScriptRegistry(ROOT / "sql"),
                          MySQLSettings(ssl_enabled=False), driver=fake)
    target.connect()
    assert fake.options["database"] is None
    assert fake.options["user"] == "monitor"
    assert fake.options["password"] == "  DBA test 密码!?  "
    target.disconnect()


def test_mysql_connect_exception_redacts_exact_password_and_preserves_auth_evidence() -> None:
    target, driver = adapter()
    driver.failure = Exception(
        1045, f"Access denied for user 'monitor' (using password: YES); PWD={PASSWORD}"
    )
    with pytest.raises(ToolkitError) as caught:
        target.connect()
    assert caught.value.database_error is not None
    detail = caught.value.database_error
    assert "Access denied for user 'monitor'" in detail.message
    assert "using password: YES" in detail.message
    assert PASSWORD not in detail.message
    assert PASSWORD not in repr(caught.value)
    assert PASSWORD not in str(caught.value)


def test_check_cli_displays_mysql_vendor_error_in_human_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "mysql84", "name": "MySQL test", "db_type": "mysql",
        "host": "127.0.0.1", "port": 3306, "database": "missing_db",
        "credential_alias": "mysql-monitor", "enabled": True,
        "environment": "test", "connection": {"ssl_disabled": True},
    }]}), encoding="utf-8")

    for json_mode in (False, True):
        fake = FakeDriver()
        fake.failure = Exception(1049, "Unknown database 'missing_db'")
        command = ["--inventory", str(inventory), "check", "mysql84"]
        if json_mode:
            command.append("--json")
        assert main(command, provider=MemoryCredentialProvider(), driver=fake) == 1
        output = capsys.readouterr().out
        assert "Unknown database 'missing_db'" in output
        assert "DATABASE_NOT_FOUND" in output
        if json_mode:
            parsed = json.loads(output)
            assert parsed["error"]["vendor_code"] == 1049
            assert parsed["effective_connection"]["database"] == "missing_db"
        else:
            assert "Effective Connection" in output


@pytest.mark.parametrize("field,value,error", [
    ("server_version", "5.7.44", "MySQL 8.0"),
    ("server_version", "10.11.0-MariaDB", "MySQL 8.0"),
    ("performance_schema_enabled", 0, "Performance Schema"),
])
def test_preflight_fail_closed(field: str, value: Any, error: str) -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, field: value}]
    with pytest.raises(ToolkitError, match=error):
        target.connect()
    assert driver.connection.closed


def test_newer_mysql_version_requires_same_capability_probes() -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "server_version": "8.4.0"}]
    target.connect()
    assert target.get_server_version() == "8.4.0"
    assert "blocking" in driver.connection.executed


def test_global_select_is_non_privileged_but_permission_readiness_is_missing() -> None:
    target, driver = adapter()
    driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `toolkit_monitor`@`%`"
    }]
    target.connect()
    assert target.privilege_classification["classification"] == "NON_PRIVILEGED"
    assert target.permission_observations == {
        "GRANT SELECT ON *.* TO `toolkit_monitor`@`%`"
    }
    assert not target.preflight_checks["permission_slow"]
    target.disconnect()


@pytest.mark.parametrize("grant", [
    "GRANT ALL PRIVILEGES ON *.* TO `toolkit_monitor`@`%`",
    "GRANT SELECT ON *.* TO `toolkit_monitor`@`%` WITH GRANT OPTION",
])
def test_mysql_high_risk_grant_reports_trigger(grant: str) -> None:
    target, driver = adapter()
    driver.connection.results["monitor_grants"] = [{"Grants": grant}]
    with pytest.raises(ToolkitError) as caught:
        target.connect()
    assert caught.value.error_type == "PrivilegedAccount"
    assert caught.value.trigger_reason is not None
    assert caught.value.trigger_reason["scope"] == "*.*"
    assert driver.connection.closed


def test_missing_slow_permission_returns_exact_identity_reference_grant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "mysql84", "name": "MySQL", "db_type": "mysql",
        "host": "127.0.0.1", "port": 3306, "database": "app",
        "credential_alias": "mysql-monitor", "enabled": True,
        "environment": "test",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "monitor_user": "dba_monitor@%"}]
    driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `dba_monitor`@`%`"
    }]
    code = main(["--inventory", str(inventory), "diagnose", "mysql84", "slow", "--json"],
                provider=MemoryCredentialProvider(), driver=driver)
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["permission_readiness"]["account_security"]["classification"] == "NON_PRIVILEGED"
    assert payload["permission_readiness"]["status"] == "MISSING"
    assert payload["permission_readiness"]["missing_required"] == ["PROCESS"]
    assert "GRANT PROCESS ON *.* TO 'dba_monitor'@'%';" in payload["permission_readiness"]["suggested_grant"]
    assert "GRANT SELECT ON `performance_schema`.*" not in payload["permission_readiness"]["suggested_grant"]
    assert payload["permission_readiness"]["toolkit_executed"] is False
    assert payload["permission_readiness"]["dba_approval_required"] is True
    assert not any(name in {"grant", "revoke", "create_user", "alter_user"}
                   for name in driver.connection.executed)


def test_check_keeps_account_security_and_permission_readiness_separate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "mysql84", "name": "MySQL", "db_type": "mysql",
        "host": "127.0.0.1", "port": 3306, "database": "app",
        "credential_alias": "mysql-monitor", "enabled": True,
        "environment": "test",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "monitor_user": "dba_monitor@%"}]
    driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `dba_monitor`@`%`"
    }]
    code = main(["--inventory", str(inventory), "check", "mysql84"],
                provider=MemoryCredentialProvider(), driver=driver)
    rendered = capsys.readouterr().out
    assert code == 1
    assert "Connection" in rendered and "READY" in rendered
    assert "Account Security" in rendered and "NON_PRIVILEGED" in rendered
    assert "Permission Readiness" in rendered and "slow" in rendered
    assert "MISSING" in rendered and "PROCESS" in rendered


def test_failed_show_grants_preserves_sanitized_vendor_error() -> None:
    target, driver = adapter()
    driver.connection.failures["monitor_grants"] = Exception(
        1142, f"SHOW command denied to user; PWD={PASSWORD}"
    )
    with pytest.raises(ToolkitError) as caught:
        target.connect()
    assert caught.value.error_type == "PermissionInspectionFailed"
    detail = caught.value.database_error
    assert detail is not None and detail.vendor_code == 1142
    assert "SHOW command denied" in detail.message
    assert PASSWORD not in repr(caught.value)


def test_missing_monitoring_view_connection_and_query_timeout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    target, driver = adapter()
    driver.connection.failures["blocking"] = PermissionError(PASSWORD)
    target.connect()
    assert not target.preflight_checks["blocking"]
    assert "GRANT SELECT ON `performance_schema`.* TO `toolkit_monitor`@`%`" in target.permission_observations
    target.disconnect()
    assert PASSWORD not in capsys.readouterr().err
    second, driver2 = adapter()
    driver2.failure = TimeoutError(PASSWORD)
    with pytest.raises(ToolkitError, match="timed out"):
        second.connect()
    third, driver3 = adapter()
    third.connect()
    driver3.connection.failures["active_requests"] = TimeoutError(PASSWORD)
    response = ActiveRequestsCollector(third).collect(INSTANCE)
    assert response.error_type == "QueryTimeout" and driver3.connection.closed
    assert PASSWORD not in capsys.readouterr().err


def test_fallback_collectors_limits_and_host_parsing() -> None:
    target, driver = adapter(settings=MySQLSettings(long_running_seconds=60))
    driver.connection.results["preflight"] = [{**PREFLIGHT, "statements_consumer": 0}]
    driver.connection.results["active_sessions"] = [session(1)]
    driver.connection.results["active_requests_basic"] = [
        {**session(1), "duration_ms": 90000}, {**session(2), "duration_ms": 1000}]
    target.connect()
    assert target.statement_script == "active_requests_basic"
    assert ActiveSessionsCollector(target).collect(INSTANCE).rows[0]["host_ip"] == "127.0.0.1"
    assert ActiveRequestsCollector(target).collect(INSTANCE).row_count == 2
    assert LongRunningCollector(target).collect(INSTANCE).rows[0]["processlist_id"] == 1
    assert host_ip("[::1]:3306") == "::1" and host_ip("db.example:3306") is None
    script = target.registry.get("server_info", DatabaseType.MYSQL)
    driver.connection.results["server_info"] = [{"version": "x"}, {"version": "y"}]
    assert target.execute_readonly(script).truncated


def test_wait_consumer_fallback_keeps_instrumented_statements() -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "waits_consumer": 0}]
    driver.connection.results["active_requests_no_waits"] = [
        {**session(1), "duration_ms": 1200, "rows_examined": 3, "current_wait": None}]
    target.connect()
    assert target.statement_script == "active_requests_no_waits"
    assert target.preflight_checks["instrumented_statements"]
    assert not target.preflight_checks["instrumented_waits"]
    assert ActiveRequestsCollector(target).collect(INSTANCE).rows[0]["rows_examined"] == 3


def test_server_numeric_duration_preserves_integer_comparison() -> None:
    target, driver = adapter(settings=MySQLSettings(long_running_seconds=1))
    target.connect()
    driver.connection.results["active_requests"] = [
        {**session(1), "duration_ms": Decimal("3134")},
    ]
    assert LongRunningCollector(target).collect(INSTANCE).rows[0]["duration_ms"] == 3134


def test_streaming_cursor_aborts_connection_at_row_cap() -> None:
    driver = FakeDriver()
    driver.cursors = SimpleNamespace(SSCursor=FakeCursor)
    target, _ = adapter(driver)
    target.connect()
    driver.connection.results["server_info"] = [{"version": "x"}, {"version": "y"}]
    result = target.execute_readonly(target.registry.get("server_info", DatabaseType.MYSQL))
    assert result.truncated and result.row_count == 2
    assert driver.connection.closed and driver.options["cursorclass"] is FakeCursor


def test_sleep_transaction_multilevel_roots_references_and_markdown() -> None:
    rows = (result("active_sessions", [session(1, "Sleep"), session(2), session(3),
                                       session(4, "Sleep")]),
            result("active_requests", [{**session(2), "duration_ms": 120000,
                                        "current_wait": "wait/lock", "rows_examined": 2},
                                       {**session(3), "duration_ms": 1000}]),
            result("transactions", [{"trx_mysql_thread_id": 1, "trx_id": "123",
                                     "trx_started": "2026-01-01", "transaction_age_seconds": 90,
                                     "trx_rows_locked": 1, "trx_rows_modified": 1}]),
            result("blocking", [{"blocked_pid": 2, "blocker_pid": 1},
                                {"blocked_pid": 3, "blocker_pid": 2},
                                {"blocked_pid": 5, "blocker_pid": 4}]))
    report = build_slow_report(INSTANCE, rows, MySQLSettings(), "8.0.44")
    titles = {item.title for item in report.findings}
    assert titles >= {"BLOCKING", "ROOT_BLOCKER", "IDLE_TRANSACTION", "LONG_RUNNING_SQL",
                      "WAITING_SQL"}
    assert report.details["blocking_graph"]["roots"] == [1, 4]
    assert report.details["top_queries"][0]["blocking_pids"] == [1]
    assert report.details["idle_transactions"][0]["last_sql"] is None
    refs = next(item.reference_commands for item in report.findings
                if item.title == "ROOT_BLOCKER" and item.session == "1")
    assert [item.text for item in refs] == ["KILL QUERY 1;", "KILL 1;"]
    assert all(not hasattr(item, "execute") for item in refs)
    assert "Toolkit did not execute" in render_console(report)
    for heading in ("Summary", "Concurrency", "Long Running SQL", "Blocking Tree",
                    "Root Blocker", "Transactions", "Idle Transactions", "Session Source",
                    "Findings", "Recommendations", "Reference Commands"):
        assert f"## {heading}" in render_markdown(report)


def test_cycle_and_incomplete_suppress_references() -> None:
    report = build_slow_report(INSTANCE, (
        result("active_sessions", [session(1), session(2)], truncated=True),
        result("active_requests", []), result("transactions", []),
        result("blocking", [{"blocked_pid": 1, "blocker_pid": 2},
                            {"blocked_pid": 2, "blocker_pid": 1}]),
    ), MySQLSettings(), "8.0.44")
    assert report.details["incomplete"] and report.details["blocking_graph"]["unresolved"]
    assert not any(item.reference_commands for item in report.findings)


def test_high_concurrency_uses_statements_and_interrupt_closes() -> None:
    report = build_slow_report(INSTANCE, (
        result("active_sessions", [session(1), session(2)]),
        result("active_requests", [{**session(1), "duration_ms": 100}]),
        result("transactions", []), result("blocking", []),
    ), MySQLSettings(high_concurrency_threshold=1), "8.0.44")
    assert report.details["concurrency"]["total_connections"] == 2
    assert report.details["concurrency"]["active_statements"] == 1
    assert any(item.title == "HIGH_CONCURRENCY" for item in report.findings)
    target, driver = adapter()
    target.connect()
    driver.connection.failures["active_sessions"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ActiveSessionsCollector(target).collect(INSTANCE)
    assert driver.connection.closed


def test_mysql_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [
        {"id": "my", "name": "MySQL", "db_type": "mysql", "host": "test.invalid",
         "port": 3306, "database": "test", "credential_alias": "monitor",
         "environment": "development"}]}), encoding="utf-8")
    settings = tmp_path / "runtime.yaml"
    settings.write_text(yaml.safe_dump({"mysql": {"ssl_enabled": False}}), encoding="utf-8")
    common = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
              "--settings", str(settings)]
    provider = MemoryCredentialProvider()
    assert main([*common, "test-connection", "my", "--json"], provider=provider,
                driver=FakeDriver()) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    driver = FakeDriver()
    driver.connection.results["active_sessions"] = [session(1)]
    assert main([*common, "collect", "my", "active-sessions", "--json"], provider=provider,
                driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["processlist_id"] == 1
    report = tmp_path / "slow.html"
    assert main([*common, "diagnose", "my", "slow", "--format", "json",
                 "--report", str(report)], provider=provider, driver=FakeDriver()) == 0
    assert json.loads(capsys.readouterr().out)["success"] and report.exists()
