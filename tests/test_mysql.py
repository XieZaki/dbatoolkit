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
from dba.permissions import PermissionStatus, requirements_for
from dba.registry import SqlScriptRegistry
from dba.settings import MySQLSettings

INSTANCE = DatabaseInstance("my", "MySQL", DatabaseType.MYSQL, "test.invalid", 3306,
                            "monitor", "test", environment="development")
PREFLIGHT = {"server_version": "8.0.44", "monitor_user": "toolkit_monitor@%",
             "active_roles": "NONE", "performance_schema_enabled": 1,
             "connection_id": 9, "privileged_grants": 0, "non_readonly_grants": 0,
             "statements_consumer": 1, "waits_consumer": 1, "history_consumer": 1}


class FakeCursor:
    def __init__(self, connection: "FakeConnection", cursor_class: Any = None) -> None:
        self.connection = connection
        self.cursor_class = cursor_class
        self.script_name: str | None = None
        self.description: list[tuple[str]] | None = None
        self.rows: list[dict[str, Any]] = []
        self.offset = 0
        self.closed = False

    def execute(self, sql: str, parameters: tuple[int, ...] | None = None) -> None:
        name = self.connection.scripts[sql]
        self.script_name = name
        self.connection.executed.append(name)
        self.connection.executed_parameters.append(parameters)
        if name in self.connection.failures:
            raise self.connection.failures[name]
        self.rows = list(self.connection.results.get(name, []))
        self.description = [(key,) for key in self.rows[0]] if self.rows else [("id",)]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        selected = self.rows[self.offset:self.offset + size]
        self.offset += len(selected)
        return [tuple(row.values()) for row in selected]

    def close(self) -> None:
        self.closed = True
        self.connection.lifecycle.append("cursor.close")


class FakeConnection:
    def __init__(self, scripts: dict[str, str]) -> None:
        self.scripts = scripts
        self.results: dict[str, list[dict[str, Any]]] = {
            "preflight": [dict(PREFLIGHT)],
            "preflight_roles": [{"active_roles": "NONE"}],
            "version_detect": [{"raw_version": "8.0.44"}],
            "monitor_grants": [
                {"Grants for toolkit_monitor@%":
                 "GRANT PROCESS ON *.* TO `toolkit_monitor`@`%`"},
                {"Grants for toolkit_monitor@%":
                 "GRANT SELECT ON `performance_schema`.* TO `toolkit_monitor`@`%`"},
            ],
        }
        self.failures: dict[str, BaseException] = {}
        self.executed: list[str] = []
        self.executed_parameters: list[tuple[int, ...] | None] = []
        self.cursors: list[FakeCursor] = []
        self.closed = False
        self.autocommit = True
        self.rollbacks = 0
        self.lifecycle: list[str] = []
        self.cursor_classes: list[Any] = []

    def cursor(self, cursor_class: Any = None) -> FakeCursor:
        result = FakeCursor(self, cursor_class)
        self.cursors.append(result)
        self.cursor_classes.append(cursor_class)
        return result

    def get_autocommit(self) -> bool:
        return self.autocommit

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.lifecycle.append("connection.close")
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
        assert target.preflight_checks["statement_history_consumer"]
        assert "recent_statements" not in driver.connection.executed
        assert PASSWORD not in repr(target)
        before = len(driver.connection.executed)
        parameterized_rejected = target.execute_readonly("SELECT %s", (1,))  # type: ignore[arg-type]
        assert not parameterized_rejected.success and parameterized_rejected.error_type == "RegistryRejected"
        assert len(driver.connection.executed) == before


def test_recent_statement_query_uses_bound_ids_and_registered_script() -> None:
    target, driver = adapter()
    script = target.registry.get("recent_statements", DatabaseType.MYSQL)
    driver.connection.results["recent_statements"] = [{
        "session_id": 36, "thread_id": 78, "event_id": 105,
        "duration_ms": 1, "sql_text": "SELECT 1", "sql_text_truncated": 0,
    }]
    with target:
        result = target.execute_readonly(script, (78, 36, 78))
        assert result.success
        assert driver.connection.executed[-1] == "recent_statements"
        assert driver.connection.executed_parameters[-1] == (78, 36, 78)
    assert driver.connection.closed and all(item.closed for item in driver.connection.cursors)
    unbound_target, _ = adapter()
    with unbound_target:
        rejected = unbound_target.execute_readonly("SELECT 1")  # type: ignore[arg-type]
        assert not rejected.success and rejected.error_type == "RegistryRejected"


def test_recent_statement_exactly_five_rows_reaches_limit_without_claiming_truncation() -> None:
    target, driver = adapter()
    script = target.registry.get("recent_statements", DatabaseType.MYSQL)
    driver.connection.results["recent_statements"] = [
        {"session_id": 36, "thread_id": 78, "event_id": event_id,
         "duration_ms": 1, "sql_text": "SELECT 1", "sql_text_truncated": 0}
        for event_id in range(5, 0, -1)
    ]
    with target:
        result = target.execute_readonly(script, (78, 36, 78))
    assert result.success and result.row_count == 5
    assert not result.truncated


def test_mysql57_preflight_does_not_execute_mysql80_role_function() -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "server_version": "5.7.44-log"}]
    driver.connection.results["monitor_grants"] = [
        {"grant": "GRANT REPLICATION CLIENT ON *.* TO `toolkit_monitor`@`%`"},
    ]
    with target:
        assert target.get_server_version() == "5.7.44-log"
        assert "preflight_roles" not in driver.connection.executed
        assert target.preflight_checks["permission_replication"]


def test_mysql_disk_growth_monitoring_grants_are_optional_in_preflight_contract() -> None:
    requirements = requirements_for(DatabaseType.MYSQL, "disk-growth", "8.4.11")
    assert requirements.required == ()
    assert requirements.optional == ("PROCESS", "REPLICATION CLIENT", "SELECT performance_schema")


def test_mysql80_preflight_fails_closed_when_active_roles_cannot_be_read() -> None:
    target, driver = adapter()
    driver.connection.failures["preflight_roles"] = Exception(1142, "denied")
    with pytest.raises(ToolkitError, match="active role state"):
        target.connect()
    assert driver.connection.closed


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
    ("server_version", "10.11.0-MariaDB", "MySQL 5.7"),
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


@pytest.mark.regression
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
@pytest.mark.regression
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
        "environment": "test", "version_family": "8.0",
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
        "environment": "test", "version_family": "8.0",
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


def test_permissions_slow_uses_atomic_version_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "mysql80", "name": "MySQL 8", "db_type": "mysql",
        "host": "127.0.0.1", "port": 3306, "database": "app",
        "credential_alias": "mysql-monitor", "enabled": True,
        "environment": "test", "version_family": "8.0",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "monitor_user": "dba_monitor@%"}]
    driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `dba_monitor`@`%`"
    }]
    code = main(["--inventory", str(inventory), "permissions", "mysql80",
                 "--feature", "slow", "--json"],
                provider=MemoryCredentialProvider(), driver=driver)
    payload = json.loads(capsys.readouterr().out)
    slow = next(row for row in payload["features"] if row["feature"] == "slow")
    assert code == 0
    assert slow["status"] == "MISSING"
    assert slow["required_privileges"] == ["PROCESS", "SELECT performance_schema"]
    assert slow["version_profile"]["sql_profile_id"] == "mysql80_slow"
    assert slow["version_profile"]["permission_profile_id"] == "mysql80_slow"
    assert slow["version_profile"]["parser_contract_id"] == "mysql_slow"


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


def test_missing_wait_instrumentation_is_partial_not_failed_core_evidence() -> None:
    target, driver = adapter()
    driver.connection.results["preflight"] = [{**PREFLIGHT, "waits_consumer": 0}]
    target.connect()
    report = build_slow_report(
        INSTANCE,
        (result("active_sessions", [session(1)]), result("active_requests", []),
         result("transactions", []), result("blocking", [])),
        target.settings, "8.4.11", target.statement_script,
        ("active_sessions", "active_requests", "transactions", "blocking"), (),
    )
    assert not report.details["incomplete"]
    assert report.details["data_visibility"] == "PARTIAL"


def test_server_numeric_duration_preserves_integer_comparison() -> None:
    target, driver = adapter(settings=MySQLSettings(long_running_seconds=1))
    target.connect()
    driver.connection.results["active_requests"] = [
        {**session(1), "duration_ms": Decimal("3134")},
    ]
    assert LongRunningCollector(target).collect(INSTANCE).rows[0]["duration_ms"] == 3134


def test_buffered_cursor_truncation_does_not_abort_connection() -> None:
    driver = FakeDriver()
    driver.cursors = SimpleNamespace(SSCursor=FakeCursor)
    target, _ = adapter(driver)
    target.connect()
    driver.connection.results["server_info"] = [{"version": "x"}, {"version": "y"}]
    result = target.execute_readonly(target.registry.get("server_info", DatabaseType.MYSQL))
    assert result.truncated and result.row_count == 2
    assert not driver.connection.closed
    assert driver.connection.cursor_classes[-1] is None


def test_metadata_and_replication_queries_use_buffered_cursor_and_read_all_rows() -> None:
    driver = FakeDriver()
    driver.cursors = SimpleNamespace(SSCursor=FakeCursor)
    driver.connection.results["monitor_grants"] = [
        {"grant": "GRANT SELECT ON *.* TO `toolkit_monitor`@`%`"},
        {"grant": "GRANT REPLICATION CLIENT ON *.* TO `toolkit_monitor`@`%`"},
    ]
    target, _ = adapter(driver)
    target.connect()
    assert any("REPLICATION CLIENT" in item for item in target.permission_observations)
    assert target.preflight_checks["permission_replication"]
    cursor_classes = {cursor.script_name: cursor.cursor_class
                      for cursor in driver.connection.cursors}
    for name in ("preflight", "monitor_grants", "server_info"):
        assert cursor_classes[name] is None
    grants = target._run(target.registry.get("monitor_grants", DatabaseType.MYSQL))
    status = target.execute_readonly(target.registry.get("replication_status", DatabaseType.MYSQL))
    assert grants.row_count == 2
    assert status.success and status.row_count == 0
    assert driver.connection.cursor_classes[-2:] == [None, None]
    assert all(cursor.closed for cursor in driver.connection.cursors)


def test_missing_replication_permission_has_structured_preflight_guidance() -> None:
    target, _ = adapter()
    target.connect()
    target._ready = False
    with pytest.raises(ToolkitError) as exc_info:
        target.execute_readonly(target.registry.get("replication_status", DatabaseType.MYSQL))
    error = exc_info.value
    assert error.error_type == "MissingPermission"
    assert error.permission_readiness is not None
    assert error.permission_readiness["feature"] == "replication"
    assert error.permission_readiness["status"] == PermissionStatus.MISSING
    assert error.permission_readiness["missing_required"] == ("REPLICATION CLIENT",)
    assert "GRANT REPLICATION CLIENT ON *.*" in str(error.permission_readiness["suggested_grant"])
    assert error.permission_readiness["toolkit_executed"] is False


@pytest.mark.regression
def test_streaming_cursor_is_closed_before_connection_on_truncation() -> None:
    driver = FakeDriver()
    driver.cursors = SimpleNamespace(SSCursor=FakeCursor)
    target, _ = adapter(driver)
    target.connect()
    driver.connection.results["active_sessions"] = [session(pid) for pid in range(2001)]
    result = target.execute_readonly(target.registry.get("active_sessions", DatabaseType.MYSQL))
    assert result.truncated and result.row_count == 2000
    assert driver.connection.cursor_classes[-1] is FakeCursor
    assert driver.connection.cursors[-1].closed
    assert driver.connection.lifecycle[-2:] == ["cursor.close", "connection.close"]
    assert driver.connection.closed


@pytest.mark.regression
def test_permission_query_failure_closes_cursor_before_connection() -> None:
    driver = FakeDriver()
    target, _ = adapter(driver)
    target.connect()
    driver.connection.failures["replication_status"] = RuntimeError(
        1142, f"SELECT command denied {PASSWORD}"
    )
    result = target.execute_readonly(target.registry.get("replication_status", DatabaseType.MYSQL))
    assert not result.success and result.error_type == "QueryDenied"
    assert result.database_error is not None
    assert result.database_error["vendor_code"] == 1142
    assert result.database_error["message"] == "SELECT command denied <REDACTED>"
    assert PASSWORD not in repr(result.database_error)
    cursor = driver.connection.cursors[-1]
    assert cursor.closed and driver.connection.closed
    assert driver.connection.lifecycle[-2:] == ["cursor.close", "connection.close"]


def test_replica_status_zero_or_multiple_channel_rows_are_consumed_without_streaming() -> None:
    driver = FakeDriver()
    driver.cursors = SimpleNamespace(SSCursor=FakeCursor)
    target, _ = adapter(driver)
    target.connect()
    script = target.registry.get("replication_status", DatabaseType.MYSQL)
    driver.connection.results["replication_status"] = []
    empty = target.execute_readonly(script)
    assert empty.success and empty.row_count == 0 and not empty.truncated
    driver.connection.results["replication_status"] = [
        {"Channel_Name": "one"}, {"Channel_Name": "two"},
    ]
    multiple = target.execute_readonly(script)
    assert multiple.success and multiple.row_count == 2
    assert driver.connection.cursor_classes[-2:] == [None, None]


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
    assert [item.text for item in refs] == ["KILL CONNECTION 1;"]
    assert all(not hasattr(item, "execute") for item in refs)
    assert "Toolkit did not execute" in render_console(report)
    for heading in ("Summary", "Concurrency", "Long Running SQL", "Blocking Tree",
                    "Root Blocker", "Transactions", "Idle Transactions", "Session Source",
                    "Findings", "Recommendations", "Reference Commands"):
        assert f"## {heading}" in render_markdown(report)


def test_mysql_processlist_id_is_reference_target_when_optional_wait_detail_is_partial() -> None:
    session_row = {**session(26, "Sleep"), "thread_id": 68}
    now = datetime.now(timezone.utc)
    report = build_slow_report(INSTANCE, (
        result("active_sessions", [session_row]),
        CollectorResult("my", "active_requests", now, now, 1.0, (), 0, False, False,
                        error_type="CapabilityUnavailable"),
        result("transactions", [{"trx_mysql_thread_id": 26, "thread_id": 68,
                                 "trx_id": "4272", "trx_started": None,
                                 "transaction_age_seconds": None}]),
        result("blocking", [{"blocked_pid": 30, "blocker_pid": 26},
                             {"blocked_pid": 31, "blocker_pid": 30}]),
    ), MySQLSettings(), "8.4.0", statement_script="active_requests_no_waits",
        required_collectors=("active_sessions", "transactions", "blocking"))
    root = next(item for item in report.findings
                if item.title == "ROOT_BLOCKER" and item.session == "26")
    assert report.details["data_visibility"] == "PARTIAL"
    assert [command.text for command in root.reference_commands] == ["KILL CONNECTION 26;"]
    assert "68" not in " ".join(command.text for command in root.reference_commands)
    assert "4272" not in " ".join(command.text for command in root.reference_commands)
    assert root.evidence["manual_action"]["identity_evidence"]["thread_id"] == 68
    assert root.evidence["manual_action"]["identity_evidence"]["trx_id"] == "4272"
    assert not any(hasattr(command, "execute") for command in root.reference_commands)


def test_mysql_suppresses_reference_for_transaction_identity_conflict() -> None:
    report = build_slow_report(INSTANCE, (
        result("active_sessions", [{**session(26, "Sleep"), "thread_id": 68}]),
        result("active_requests", []),
        result("transactions", [{"trx_mysql_thread_id": 26, "thread_id": 99,
                                 "trx_id": "4272", "trx_started": "2026-10-18"}]),
        result("blocking", [{"blocked_pid": 30, "blocker_pid": 26}]),
    ), MySQLSettings(), "8.4.0")
    root = next(item for item in report.findings
                if item.title == "ROOT_BLOCKER" and item.session == "26")
    assert not root.reference_commands
    assert root.evidence["manual_action"]["status"] == "SUPPRESSED"
    assert "Conflicting" in root.evidence["manual_action"]["reason"]


def test_mysql_realistic_blocking_chain_threshold_sleep_and_transaction_identity() -> None:
    from dba.incident import (
        build_incident_summary,
        build_priority_candidates,
        manual_action_decisions,
    )
    from dba.presentation import render_emergency_triage, render_json
    from dba.reporting import HtmlReportRenderer, build_report_model

    rows = (
        result("active_sessions", [
            {**session(29, "Sleep"), "thread_id": 72},
            session(30),
            {**session(31), "state_name": "User sleep", "sql_text": "SELECT SLEEP(115)"},
            session(32),
        ]),
        result("active_requests", [
            {**session(30), "duration_ms": 6000, "current_wait": None},
            {**session(31), "duration_ms": 6000, "current_wait": None,
             "state_name": "User sleep", "sql_text": "SELECT SLEEP(115)"},
            {**session(32), "duration_ms": 5500, "current_wait": None},
        ]),
        result("transactions", [{"trx_mysql_thread_id": 29, "thread_id": 72,
                                 "trx_id": "4282", "trx_started": "2026-10-18 23:14:32",
                                 "transaction_age_seconds": 6, "trx_state": "LOCK WAIT"}]),
        result("blocking", [{"blocked_pid": 30, "blocker_pid": 29},
                             {"blocked_pid": 32, "blocker_pid": 30}]),
    )
    report = build_slow_report(INSTANCE, rows, MySQLSettings(long_running_seconds=10),
                               "8.4.11", statement_script="active_requests_no_waits")
    candidates = build_priority_candidates(report)
    assert [item["session_id"] for item in candidates] == [29]
    assert not any(item.title == "LONG_RUNNING_SQL" for item in report.findings)
    assert [(row["processlist_id"], row["direct_blocker_id"], row["root_blocker_id"])
            for row in report.details["blocked_victims"]] == [(30, 29, 29), (32, 30, 29)]
    assert [row["processlist_id"] for row in report.details["active_waits"]] == [31]
    assert report.details["roots"][0]["transaction_id"] == "4282"
    assert report.details["roots"][0]["transaction_identity_quality"] == "VERIFIED"
    summary = build_incident_summary(report)
    assert summary.root_cause_candidate is not None
    assert summary.root_cause_candidate["transaction_id"] == "4282"
    assert summary.root_cause_candidate["transaction_identity_quality"] == "VERIFIED"
    report.details["priority_candidates"] = candidates
    report.details["manual_action_decisions"] = manual_action_decisions(report)
    html = HtmlReportRenderer().render(build_report_model(report, summary))
    json_report = render_json(report)
    assert "4282" in html and "事务关联已核验" in html
    assert "被阻塞会话" in html and "主动等待语句" in html
    assert "KILL CONNECTION 29;" in html
    assert '"trx_id": "4282"' in json_report and '"transaction_identity_quality": "VERIFIED"' in json_report
    assert '"target": 29' in json_report and '"trx_id": 29' not in json_report
    root = next(item for item in report.findings if item.title == "ROOT_BLOCKER")
    assert [command.text for command in root.reference_commands] == ["KILL CONNECTION 29;"]
    assert all("THREAD_ID" not in command.text and "4282" not in command.text
               for command in root.reference_commands)
    text = render_emergency_triage(report, candidates, capture_started_at="2026-10-18T23:14:32+08:00")
    assert "会话30" in text and "会话32" in text and "主动等待语句" in text
    assert "Performance Schema THREAD_ID：72" in text
    assert "InnoDB 事务 ID：4282" in text
    assert "KILL CONNECTION 29;" in text
    assert "会话31｜长运行 SQL" not in text


@pytest.mark.parametrize(("elapsed", "is_long"), [(5500, False), (6000, False), (10000, True), (11000, True)])
def test_mysql_slow_threshold_boundary(elapsed: int, is_long: bool) -> None:
    rows = (result("active_sessions", [session(7)]),
            result("active_requests", [{**session(7), "duration_ms": elapsed}]),
            result("transactions", []), result("blocking", []))
    report = build_slow_report(INSTANCE, rows, MySQLSettings(long_running_seconds=10), "8.4.11")
    finding = next((item for item in report.findings if item.title == "LONG_RUNNING_SQL"), None)
    assert (finding is not None) is is_long
    assert bool(report.details["long_running"]) is is_long


def test_long_standalone_sleep_keeps_wait_interpretation_without_review_candidate() -> None:
    from dba.incident import build_priority_candidates

    rows = (result("active_sessions", [{**session(31), "state_name": "User sleep",
                                        "sql_text": "SELECT SLEEP(115)"}]),
            result("active_requests", [{**session(31), "state_name": "User sleep",
                                         "sql_text": "SELECT SLEEP(115)", "duration_ms": 15000}]),
            result("transactions", []), result("blocking", []))
    report = build_slow_report(INSTANCE, rows, MySQLSettings(long_running_seconds=10), "8.4.11")
    assert report.details["long_running"][0]["request_kind"] == "ACTIVE_WAIT"
    assert [row["processlist_id"] for row in report.details["active_waits"]] == [31]
    assert not build_priority_candidates(report)


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


@pytest.mark.integration
def test_mysql_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [
        {"id": "my", "name": "MySQL", "db_type": "mysql", "host": "test.invalid",
         "port": 3306, "database": "test", "credential_alias": "monitor",
         "environment": "development", "version_family": "8.0"}]}), encoding="utf-8")
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


@pytest.mark.integration
@pytest.mark.regression
def test_replication_cli_explains_missing_replication_client_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "my", "name": "MySQL", "db_type": "mysql", "host": "test.invalid",
        "port": 3306, "database": "test", "credential_alias": "monitor",
        "environment": "development", "version_family": "8.4",
    }]}), encoding="utf-8")
    common = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql")]
    provider = MemoryCredentialProvider()

    permission_driver = FakeDriver()
    permission_driver.connection.results["version_detect"] = [{"raw_version": "8.4.1"}]
    permission_driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `toolkit_monitor`@`%`"
    }]
    assert main([*common, "permissions", "my", "--feature", "replication"],
                provider=provider, driver=permission_driver) == 0
    permission_output = capsys.readouterr()
    assert "replication" in permission_output.out
    assert "MISSING" in permission_output.out
    assert "REPLICATION CLIENT" in permission_output.out
    assert not permission_output.err

    diagnostic_driver = FakeDriver()
    diagnostic_driver.connection.results["version_detect"] = [{"raw_version": "8.4.1"}]
    diagnostic_driver.connection.results["monitor_grants"] = [{
        "Grants": "GRANT SELECT ON *.* TO `toolkit_monitor`@`%`"
    }]
    diagnostic_driver.connection.failures["replication_status"] = Exception(
        1142, "SHOW command denied to user 'toolkit_monitor'"
    )
    report = tmp_path / "replication.html"
    assert main([*common, "diagnose", "my", "replication", "--report", str(report)],
                provider=provider, driver=diagnostic_driver) == 1
    diagnostic_output = capsys.readouterr()
    assert "MISSING_PERMISSION" in diagnostic_output.out
    assert "SHOW command denied" in diagnostic_output.out
    assert "REPLICATION CLIENT" in diagnostic_output.out
    assert "Toolkit Executed  NO" in diagnostic_output.out
    assert not diagnostic_output.err
    assert report.exists()
