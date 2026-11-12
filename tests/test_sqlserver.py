import io
import json
import logging
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from dba.cli import main
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider, MockCredentialProvider, Mode
from dba.errors import ToolkitError
from dba.logging import SafeFormatter, configure_logging
from dba.registry import SqlScript, SqlScriptRegistry
from dba.settings import SQLServerSettings
from dba.sqlserver import SQLServerAdapter
from dba.sqlserver_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    LongRunningRequestsCollector,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_CREDENTIAL = "DBA-Test-Secret-987654};PWD={DBA-Other-Test-Secret-987654}; value"
PASSWORD = TEST_CREDENTIAL  # Shared fake credential imported by other adapter tests.


class MemoryCredentialProvider(CredentialProvider):
    """Test double for a real provider; no real credentials/network."""

    def __init__(self, username: str = "monitor") -> None:
        self.username = username
        self.aliases: list[str] = []

    def get_credential(self, alias: str) -> Credential:
        self.aliases.append(alias)
        return Credential(self.username, TEST_CREDENTIAL)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] | None = None
        self.rows: list[dict[str, Any]] = []
        self.offset = 0
        self.closed = False

    def execute(self, text: str, *parameters: object) -> None:
        name = self.connection.scripts[text]
        self.connection.executed.append(name)
        if parameters:
            self.connection.bound_parameters.append(parameters)
        self.connection.timeouts.append(self.connection.timeout)
        if name in self.connection.failures:
            raise self.connection.failures[name]
        self.rows = self.connection.results[name]
        self.offset = 0
        self.description = [(key,) for key in self.rows[0]] if self.rows else [("empty",)]

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        self.connection.fetch_sizes.append(size)
        if self.connection.fetch_error:
            raise self.connection.fetch_error
        keys = [str(item[0]) for item in self.description or ()]
        selected = self.rows[self.offset:self.offset + size]
        self.offset += len(selected)
        return [tuple(row.get(key) for key in keys) for row in selected]

    def close(self) -> None:
        self.closed = True
        if self.connection.cursor_close_error:
            raise RuntimeError(TEST_CREDENTIAL)


class FakeConnection:
    def __init__(self) -> None:
        self.autocommit = True
        self.timeout = 0
        self.closed = False
        self.rollbacks = 0
        self.rollback_error = False
        self.close_error = False
        self.cursor_close_error = False
        self.fetch_error: Exception | None = None
        self.executed: list[str] = []
        self.bound_parameters: list[tuple[object, ...]] = []
        self.timeouts: list[int] = []
        self.fetch_sizes: list[int] = []
        self.cursors: list[FakeCursor] = []
        self.failures: dict[str, Exception] = {}
        self.scripts = {
            p.read_bytes().decode("utf-8"): p.stem
            for p in (ROOT / "sql/sqlserver").glob("*.sql")
        }
        self.results: dict[str, list[dict[str, Any]]] = {
            "monitor_capabilities": [
                {
                    "is_sysadmin": 0,
                    "is_securityadmin": 0,
                    "is_serveradmin": 0,
                    "is_db_owner": 0,
                    "is_database_owner": 0,
                    "major_version": 16,
                    "view_server_performance_state": 1,
                    "view_server_state": 0,
                    "view_any_database": 1,
                    "sessions_database_id": 1,
                    "sessions_open_transaction_count": 1,
                }
            ],
            "monitor_permissions": [
                {"scope": "SERVER", "permission_name": "CONNECT SQL", "source_type": "EFFECTIVE",
                 "source_name": "effective server permissions; source not exposed", "object_name": None,
                 "object_type": None, "execute_as_principal_id": None},
                {"scope": "SERVER", "permission_name": "VIEW SERVER PERFORMANCE STATE",
                 "source_type": "EFFECTIVE", "source_name": "effective server permissions; source not exposed",
                 "object_name": None, "object_type": None, "execute_as_principal_id": None},
            ],
            "server_info": [{"product_version": "16.0.1000.6", "edition": "Developer"}],
            "database_status": [{"database_name": "master", "state": "ONLINE"}],
            "active_sessions": [
                {"session_id": 51, "status": "sleeping", "open_transaction_count": 1}
            ],
            "active_requests": [
                {"session_id": 52, "elapsed_ms": 90000, "sql_text": "SELECT 1"},
                {"session_id": 53, "elapsed_ms": 59999, "sql_text": "SELECT 2"},
            ],
            "version_probe": [{"toolkit_probe": 1}],
            "version_detect": [{"raw_version": "16.0.1000.6"}],
        }

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error:
            raise RuntimeError(TEST_CREDENTIAL)

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError(TEST_CREDENTIAL)


class FakeDriver:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.failure: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def connect(self, connection_string: str, **kwargs: Any) -> FakeConnection:
        self.calls.append({"connection_string": connection_string, **kwargs})
        if self.failure:
            raise self.failure
        return self.connection


@pytest.fixture
def instance() -> DatabaseInstance:
    return DatabaseInstance(
        "test",
        "Test",
        DatabaseType.SQLSERVER,
        "test.invalid",
        1433,
        "monitor-alias",
        database="master",
        environment="production",
    )


@pytest.fixture
def driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def adapter(instance: DatabaseInstance, driver: FakeDriver) -> SQLServerAdapter:
    return SQLServerAdapter(
        instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"), driver=driver
    )


def test_connect_credential_options_cleanup(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    assert TEST_CREDENTIAL not in repr(adapter)
    with adapter:
        assert adapter.get_server_version() == "16.0.1000.6"
        assert all(adapter.preflight_checks.values())
        assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"
        assert "effective_monitoring_permissions" not in adapter.privilege_classification
        assert adapter.provider.aliases == ["monitor-alias"]
        call = driver.calls[0]
        assert call["timeout"] == 5 and call["autocommit"] and call["readonly"]
        assert "Encrypt={yes}" in call["connection_string"]
        assert "TrustServerCertificate={no}" in call["connection_string"]
        assert "PWD={DBA-Test-Secret-987654}};PWD={DBA-Other-Test-Secret-987654}}; value}" in call["connection_string"]
        assert {"monitor_capabilities", "monitor_permissions", "server_info"}.issubset(
            set(driver.connection.executed)
        )
        assert not {"database_status", "active_sessions", "active_requests"}.intersection(
            driver.connection.executed
        )
    assert driver.connection.closed and driver.connection.rollbacks == 0
    assert all(cursor.closed for cursor in driver.connection.cursors)
    assert adapter._password == ""


@pytest.mark.parametrize(
    ("available", "selected"),
    [
        (["ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"],
         "ODBC Driver 18 for SQL Server"),
        (["ODBC Driver 17 for SQL Server"], "ODBC Driver 17 for SQL Server"),
    ],
)
def test_real_driver_path_selects_odbc_preference(
    instance: DatabaseInstance, monkeypatch: pytest.MonkeyPatch,
    available: list[str], selected: str,
) -> None:
    driver = FakeDriver()
    driver.drivers = lambda: available  # type: ignore[attr-defined]
    monkeypatch.setattr("dba.sqlserver.load_driver", lambda: driver)
    adapter = SQLServerAdapter(
        instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql")
    )
    with adapter:
        assert adapter.odbc_driver == selected
        assert f"DRIVER={{{selected}}}" in driver.calls[0]["connection_string"]


@pytest.mark.regression
def test_driver17_honors_explicit_tls_settings(
    instance: DatabaseInstance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FakeDriver()
    driver.drivers = lambda: ["ODBC Driver 17 for SQL Server"]  # type: ignore[attr-defined]
    monkeypatch.setattr("dba.sqlserver.load_driver", lambda: driver)
    adapter = SQLServerAdapter(
        instance,
        MemoryCredentialProvider(),
        SqlScriptRegistry(ROOT / "sql"),
        SQLServerSettings(encrypt=False, trust_server_certificate=True),
    )
    with adapter:
        connection_string = driver.calls[0]["connection_string"]
        assert "DRIVER={ODBC Driver 17 for SQL Server}" in connection_string
        assert "DATABASE={master}" in connection_string
        assert "Encrypt={no}" in connection_string
        assert "TrustServerCertificate={yes}" in connection_string


@pytest.mark.parametrize(
    "failure,kind",
    [
        (RuntimeError(TEST_CREDENTIAL), "ConnectionFailed"),
        (TimeoutError(TEST_CREDENTIAL), "ConnectionTimeout"),
        (RuntimeError("HYT00", TEST_CREDENTIAL), "ConnectionTimeout"),
        (RuntimeError("08001", "SSL Provider: certificate verify failed"),
         "ConnectionTLSFailed"),
    ],
)
def test_connect_failures(
    adapter: SQLServerAdapter, driver: FakeDriver, failure: Exception, kind: str
) -> None:
    driver.failure = failure
    with pytest.raises(ToolkitError) as exc:
        adapter.connect()
    assert exc.value.error_type == kind and TEST_CREDENTIAL not in str(exc.value)
    assert TEST_CREDENTIAL not in repr(exc.value)
    assert adapter._connection is None and adapter._password == ""


def test_account_rejections(instance: DatabaseInstance, driver: FakeDriver) -> None:
    with pytest.raises(ToolkitError, match="MockCredentialProvider"):
        SQLServerAdapter(
            instance, MockCredentialProvider({}), SqlScriptRegistry(ROOT / "sql"), driver=driver
        )
    with pytest.raises(ToolkitError, match="MockCredentialProvider"):
        SQLServerAdapter(
            instance,
            MockCredentialProvider({}),
            SqlScriptRegistry(ROOT / "sql"),
            mode=Mode.DEVELOPMENT,
            driver=driver,
        )
    adapter = SQLServerAdapter(
        instance, MemoryCredentialProvider(" SA "), SqlScriptRegistry(ROOT / "sql"), driver=driver
    )
    with pytest.raises(ToolkitError, match="prohibited"):
        adapter.connect()
    assert not driver.calls


@pytest.mark.parametrize(
    "change,fragment",
    [
        ({"is_sysadmin": 1}, "sysadmin"),
        ({"is_securityadmin": 1}, "securityadmin"),
        ({"is_serveradmin": 1}, "serveradmin"),
        ({"is_db_owner": 1}, "db_owner"),
        ({"is_database_owner": 1}, "database owner"),
    ],
)
def test_preflight_denies(
    adapter: SQLServerAdapter, driver: FakeDriver, change: dict[str, Any], fragment: str
) -> None:
    driver.connection.results["monitor_capabilities"][0].update(change)
    with pytest.raises(ToolkitError, match=fragment):
        adapter.connect()
    assert driver.connection.closed


def test_missing_feature_read_grants_do_not_block_connection_preflight(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["monitor_capabilities"][0].update(
        view_server_state=0, view_server_performance_state=0, view_any_database=0,
    )
    with adapter:
        assert adapter.health_check()
        assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"


def test_indeterminate_membership_is_unknown_not_privileged(adapter: SQLServerAdapter) -> None:
    driver = adapter._driver
    assert isinstance(driver, FakeDriver)
    driver.connection.results["monitor_capabilities"][0]["is_sysadmin"] = None
    with adapter:
        assert adapter.privilege_classification["classification"] == "UNKNOWN"
        assert "sysadmin" in adapter.privilege_classification["unknown_membership_checks"]


@pytest.mark.parametrize(
    ("scope", "permission"),
    [
        ("SERVER", "CONTROL SERVER"),
        ("SERVER", "ALTER ANY LOGIN"),
        ("SERVER", "IMPERSONATE ANY LOGIN"),
        ("SERVER", "TAKE OWNERSHIP"),
        ("DATABASE", "CONTROL"),
        ("DATABASE", "TAKE OWNERSHIP"),
        ("DATABASE", "IMPERSONATE"),
        ("SERVER", "UPDATE"),
        ("DATABASE", "EXECUTE"),
        ("DATABASE", "ALTER"),
        ("SERVER", "DELEGATION"),
    ],
)
def test_write_permissions_rejected(
    adapter: SQLServerAdapter, driver: FakeDriver, scope: str, permission: str
) -> None:
    driver.connection.results["monitor_permissions"].append(
        {"scope": scope, "permission_name": permission, "source_type": "EXPLICIT",
         "source_name": "monitor"}
    )
    with pytest.raises(ToolkitError, match="Explicit high-risk") as exc:
        adapter.connect()
    assert exc.value.trigger_reason == {
        "type": "server_permission" if scope == "SERVER" else "database_permission",
        "name": "CONTROL DATABASE" if scope == "DATABASE" and permission == "CONTROL" else permission,
        "source": "explicit",
        "principal": "monitor",
        "scope": scope,
    }
    assert driver.connection.closed


@pytest.mark.parametrize(
    "permission",
    ["CONNECT SQL", "VIEW ANY DATABASE", "VIEW SERVER STATE", "VIEW SERVER PERFORMANCE STATE"],
)
def test_monitoring_effective_permissions_are_allowed(
    adapter: SQLServerAdapter, driver: FakeDriver, permission: str
) -> None:
    driver.connection.results["monitor_permissions"].append(
        {"scope": "SERVER", "permission_name": permission, "source_type": "EFFECTIVE",
         "source_name": "effective permissions; source not exposed", "object_name": None,
         "object_type": None, "execute_as_principal_id": None}
    )
    with adapter:
        assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"
        assert "effective_monitoring_permissions" not in adapter.privilege_classification


def test_unknown_implied_effective_permission_is_warning_not_rejection(
    adapter: SQLServerAdapter, driver: FakeDriver
) -> None:
    driver.connection.results["monitor_permissions"].append(
        {"scope": "SERVER", "permission_name": "UNRECOGNIZED EFFECTIVE RIGHT",
         "source_type": "EFFECTIVE", "source_name": "source unavailable", "object_name": None,
         "object_type": None, "execute_as_principal_id": None}
    )
    with adapter:
        classification = adapter.privilege_classification
        assert classification["classification"] == "MONITORING_ACCOUNT"
        assert "unknown_effective_permissions" not in classification


def test_explicit_high_risk_error_has_trigger_reason(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    driver.connection.results["monitor_permissions"].append(
        {"scope": "SERVER", "permission_name": "CONTROL SERVER", "source_type": "EXPLICIT",
         "source_name": "monitor", "object_name": None, "object_type": None,
         "execute_as_principal_id": None}
    )
    with pytest.raises(ToolkitError) as exc:
        adapter.connect()
    assert exc.value.error_type == "PrivilegedAccount"
    assert exc.value.trigger_reason == {
        "type": "server_permission", "name": "CONTROL SERVER", "source": "explicit",
        "principal": "monitor", "scope": "SERVER",
    }


@pytest.mark.regression
def test_public_inherited_execute_on_system_object_is_unknown_not_privileged(
    adapter: SQLServerAdapter, driver: FakeDriver
) -> None:
    driver.connection.results["monitor_permissions"].append({
        "scope": "OBJECT", "permission_name": "EXECUTE", "source_type": "INHERITED",
        "source_name": "public", "object_name": "[sys].[sp_help]",
        "object_type": "SQL_STORED_PROCEDURE", "execute_as_principal_id": None,
    })
    with adapter:
        result = adapter.privilege_classification
        assert result["classification"] == "MONITORING_ACCOUNT"
        assert "unclassified_permissions" not in result


def test_public_inherited_database_execute_is_unknown_not_privileged(
    adapter: SQLServerAdapter, driver: FakeDriver
) -> None:
    driver.connection.results["monitor_permissions"].append({
        "scope": "DATABASE", "permission_name": "EXECUTE", "source_type": "INHERITED",
        "source_name": "public", "object_name": None, "object_type": "DATABASE",
        "execute_as_principal_id": None,
    })
    with adapter:
        assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"
        assert "unclassified_permissions" not in adapter.privilege_classification


def test_direct_database_execute_is_rejected_with_scope(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    driver.connection.results["monitor_permissions"].append({
        "scope": "DATABASE", "permission_name": "EXECUTE", "source_type": "EXPLICIT",
        "source_name": "monitor", "object_name": None, "object_type": "DATABASE",
        "execute_as_principal_id": None,
    })
    with pytest.raises(ToolkitError) as exc:
        adapter.connect()
    assert exc.value.trigger_reason == {
        "type": "database_permission", "name": "EXECUTE", "source": "explicit",
        "principal": "monitor", "scope": "DATABASE", "object_type": "DATABASE",
    }


@pytest.mark.parametrize(
    ("object_name", "execute_as", "source_type", "principal", "expected"),
    [
        ("[dbo].[maintenance_proc]", None, "INHERITED", "reporting_role", True),
        ("[sys].[sp_addsrvrolemember]", None, "INHERITED", "reporting_role", True),
        ("[sys].[custom_wrapper]", -2, "INHERITED", "reporting_role", True),
        ("[sys].[sp_help]", None, "INHERITED", "reporting_role", False),
        ("[sys].[sp_addsrvrolemember]", None, "INHERITED", "public", False),
    ],
)
def test_object_execute_considers_object_and_module_context(
    adapter: SQLServerAdapter, driver: FakeDriver, object_name: str,
    execute_as: int | None, source_type: str, principal: str, expected: bool,
) -> None:
    driver.connection.results["monitor_permissions"].append({
        "scope": "OBJECT", "permission_name": "EXECUTE", "source_type": source_type,
        "source_name": principal, "object_name": object_name,
        "object_type": "SQL_STORED_PROCEDURE", "execute_as_principal_id": execute_as,
    })
    if expected:
        with pytest.raises(ToolkitError) as exc:
            adapter.connect()
        assert exc.value.trigger_reason is not None
        assert exc.value.trigger_reason["scope"] == "OBJECT"
        assert exc.value.trigger_reason["object"] == object_name
    else:
        with adapter:
            assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"


def test_public_inherited_system_execute_is_warning_not_privileged(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    driver.connection.results["monitor_permissions"].append({
        "scope": "OBJECT", "permission_name": "EXECUTE", "source_type": "INHERITED",
        "source_name": "public", "object_name": "[sys].[sp_addsrvrolemember]",
        "object_type": "SQL_STORED_PROCEDURE", "execute_as_principal_id": None,
    })
    with adapter:
        assert adapter.privilege_classification["classification"] == "MONITORING_ACCOUNT"
        assert "unclassified_permissions" not in adapter.privilege_classification


def test_older_server_permissions(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    driver.connection.results["monitor_capabilities"][0].update(
        major_version=15, view_server_state=1, view_server_performance_state=None
    )
    with adapter:
        assert adapter.health_check()


@pytest.mark.parametrize(
    "permission",
    ["VIEW ANY COLUMN ENCRYPTION KEY DEFINITION", "VIEW ANY COLUMN MASTER KEY DEFINITION"],
)
def test_readonly_key_metadata_permissions(
    adapter: SQLServerAdapter, driver: FakeDriver, permission: str
) -> None:
    driver.connection.results["monitor_permissions"].append(
        {"scope": "DATABASE", "permission_name": permission, "source_type": "EFFECTIVE",
         "source_name": "database effective permissions; source not exposed", "object_name": None,
         "object_type": None, "execute_as_principal_id": None}
    )
    with adapter:
        assert adapter.health_check()


@pytest.mark.parametrize(
    "name", ["monitor_capabilities", "monitor_permissions", "server_info"]
)
def test_preflight_probe_failure(adapter: SQLServerAdapter, driver: FakeDriver, name: str) -> None:
    driver.connection.failures[name] = RuntimeError("42000", TEST_CREDENTIAL)
    with pytest.raises(ToolkitError, match=name) as exc:
        adapter.connect()
    assert TEST_CREDENTIAL not in str(exc.value) and driver.connection.closed


def test_query_timeout_audit(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter())
    logger = configure_logging()
    logger.addHandler(handler)
    try:
        adapter.connect()
        driver.connection.failures["active_requests"] = RuntimeError("HYT00", TEST_CREDENTIAL)
        result = ActiveRequestsCollector(adapter).collect(adapter.instance)
        assert not result.success and result.error_type == "QueryTimeout"
        assert result.rows == () and result.row_count == 0
        assert TEST_CREDENTIAL not in str(asdict(result))
        assert driver.connection.closed and all(c.closed for c in driver.connection.cursors)
    finally:
        logger.removeHandler(handler)
    audit = stream.getvalue()
    assert TEST_CREDENTIAL not in audit and "PWD=" not in audit and "SELECT 1" not in audit
    assert '"error_type": "QueryTimeout"' in audit
    assert '"sql_sha256":' in audit and '"max_rows": 5000' in audit


def test_results_collectors_threshold(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    with adapter:
        sessions = ActiveSessionsCollector(adapter).collect(adapter.instance)
        assert sessions.rows[0]["open_transaction_count"] == 1
        requests = ActiveRequestsCollector(adapter).collect(adapter.instance)
        assert requests.row_count == 2 and not requests.truncated and requests.success
        assert requests.started_at <= requests.finished_at and requests.elapsed_ms >= 0
        assert requests.database_id == "test"
        adapter.settings = SQLServerSettings(long_running_seconds=90)
        result = LongRunningRequestsCollector(adapter).collect(adapter.instance)
        assert result.row_count == 1 and result.rows[0]["session_id"] == 52
        adapter.settings = SQLServerSettings(long_running_seconds=91)
        assert LongRunningRequestsCollector(adapter).collect(adapter.instance).row_count == 0


def test_active_requests_registry_and_collector_preserve_native_identity(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    script = adapter.registry.get("active_requests", DatabaseType.SQLSERVER)
    normalized_sql = " ".join(script.text.split())
    assert "r.database_id" in normalized_sql
    assert "r.request_id" in normalized_sql
    assert "r.statement_start_offset" in normalized_sql
    assert "r.statement_end_offset" in normalized_sql
    assert "CONVERT(varbinary(max), t.text) AS batch_bytes" in normalized_sql
    assert "source.end_offset + 2 - source.start_offset" in normalized_sql
    assert "source.end_offset IN (-1, source.batch_length)" in normalized_sql
    assert "AS statement_sql_text_truncated" in normalized_sql
    assert "CONVERT(varchar(130), r.plan_handle, 1) AS plan_handle" in normalized_sql
    assert "CONVERT(varchar(130), r.query_plan_hash, 1) AS query_plan_hash" in normalized_sql
    assert normalized_sql.count("sys.dm_exec_requests") == 1
    assert "sys.dm_exec_query_stats" not in normalized_sql
    assert "plan_generation" not in normalized_sql
    assert "r.query_hash" in normalized_sql
    assert script.metadata.max_rows == 5000

    driver.connection.results["active_requests"] = [{
        "session_id": 52, "database_id": 7, "sql_handle": "0x01",
        "statement_start_offset": 0, "statement_end_offset": 42,
        "plan_handle": "0x02", "query_hash": "0x03", "query_plan_hash": "0x04",
        "request_id": 3, "sql_text": "batch: SELECT 1", "sql_text_truncated": 0,
        "statement_sql_text": "SELECT 1", "statement_sql_text_truncated": 0,
    }]
    with adapter:
        result = ActiveRequestsCollector(adapter).collect(adapter.instance)

    assert result.success and result.row_count == 1
    row = result.rows[0]
    assert row["database_id"] == 7 and type(row["database_id"]) is int
    assert row["sql_handle"] == "0x01"
    assert row["statement_start_offset"] == 0 and row["statement_end_offset"] == 42
    assert row["plan_handle"] == "0x02" and row["query_plan_hash"] == "0x04"
    assert row["query_hash"] == "0x03" and row["session_id"] == 52
    assert "plan_generation_num" not in row and "plan_generation_match_count" not in row
    assert row["request_id"] == 3 and row["statement_sql_text"] == "SELECT 1"
    assert row["sql_text"] == "batch: SELECT 1"

    sessions = adapter.registry.get("active_sessions", DatabaseType.SQLSERVER)
    normalized_sessions_sql = " ".join(sessions.text.split())
    assert "s.original_login_name" in normalized_sessions_sql
    assert "s.nt_user_name" in normalized_sessions_sql
    assert "s.host_process_id" in normalized_sessions_sql
    assert normalized_sessions_sql.count("s.client_interface_name") == 2
    assert "c.client_interface_name" not in normalized_sessions_sql
    assert normalized_sessions_sql.count("sys.dm_exec_connections") == 1
    legacy = adapter.registry.get("active_sessions_legacy", DatabaseType.SQLSERVER)
    assert legacy.text.count("s.client_interface_name") == 2
    assert "c.client_interface_name" not in legacy.text


@pytest.mark.parametrize("size,truncated", [(0, False), (1, False), (2, True), (100, True)])
def test_bounded_fetch(
    adapter: SQLServerAdapter, driver: FakeDriver, size: int, truncated: bool
) -> None:
    with adapter:
        driver.connection.results["server_info"] = [{"product_version": "16.0"}] * size
        result = adapter.execute_readonly(adapter.registry.get("server_info"))
        assert result.row_count == min(size, 2) and result.truncated is truncated
        assert driver.connection.fetch_sizes[-1] == 1


def test_query_timeout_configuration(instance: DatabaseInstance, driver: FakeDriver) -> None:
    with SQLServerAdapter(
        instance,
        MemoryCredentialProvider(),
        SqlScriptRegistry(ROOT / "sql"),
        SQLServerSettings(connect_timeout=3, query_timeout=7),
        driver=driver,
    ) as adapter:
        adapter.execute_readonly(adapter.registry.get("active_requests"))
        assert driver.connection.timeouts[-1] == 7
        adapter.execute_readonly(adapter.registry.get("server_info"))
        assert driver.connection.timeouts[-1] == 5
        assert driver.calls[0]["timeout"] == 3


@pytest.mark.parametrize("source", ["string", "forged", "other_registry", "unreviewed"])
def test_registry_only_execution(
    adapter: SQLServerAdapter, driver: FakeDriver, source: str
) -> None:
    adapter.connect()
    original = adapter.registry.get("server_info")
    candidates = {
        "string": "SELECT 1",
        "forged": SqlScript(original.metadata, "SELECT 2"),
        "other_registry": SqlScriptRegistry(ROOT / "sql").get("server_info"),
        "unreviewed": replace(original, metadata=replace(original.metadata, reviewed=False)),
    }
    before = len(driver.connection.executed)
    result = adapter.execute_readonly(candidates[source])
    assert not result.success and result.error_type == "RegistryRejected"
    assert len(driver.connection.executed) == before and driver.connection.closed


def test_row_password_redaction(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    with adapter:
        driver.connection.results["active_requests"][0]["sql_text"] = f"SELECT '{TEST_CREDENTIAL}'"
        result = ActiveRequestsCollector(adapter).collect(adapter.instance)
        assert TEST_CREDENTIAL not in repr(result) and "[REDACTED]" in result.rows[0]["sql_text"]


def test_collector_access_denial_preserves_vendor_error_and_permissions_guide(
    adapter: SQLServerAdapter, driver: FakeDriver,
) -> None:
    adapter.connect()
    driver.connection.failures["active_sessions"] = RuntimeError(
        "42000", "The SELECT permission was denied on the object (229)."
    )
    result = ActiveSessionsCollector(adapter).collect(adapter.instance)
    assert not result.success and result.error_type == "QueryDenied"
    assert "SELECT permission was denied" in (result.error_message or "")
    assert "docs/DATABASE_PERMISSIONS.md" in (result.error_message or "")
    assert result.database_error is not None
    assert result.database_error["classification"] == "ACCESS_DENIED"
    assert result.database_error["vendor_code"] == 229
    adapter.disconnect()


def test_cleanup_on_transaction_and_errors(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    adapter.connect()
    driver.connection.autocommit = False
    driver.connection.rollback_error = True
    with pytest.raises(ToolkitError, match="cleanup") as exc:
        adapter.disconnect()
    assert driver.connection.rollbacks == 1 and driver.connection.closed
    assert TEST_CREDENTIAL not in str(exc.value)


@pytest.mark.parametrize("failure", ["fetch", "cursor_close", "connection_close"])
def test_cleanup_failures(adapter: SQLServerAdapter, driver: FakeDriver, failure: str) -> None:
    adapter.connect()
    if failure == "connection_close":
        driver.connection.close_error = True
        with pytest.raises(ToolkitError):
            adapter.disconnect()
    else:
        if failure == "fetch":
            driver.connection.fetch_error = RuntimeError(TEST_CREDENTIAL)
        else:
            driver.connection.cursor_close_error = True
        result = ActiveSessionsCollector(adapter).collect(adapter.instance)
        assert not result.success and TEST_CREDENTIAL not in str(result)
    assert driver.connection.closed


@pytest.mark.parametrize(
    "command",
    [
        ["test-connection", "test"],
        ["collect", "test", "active-sessions"],
        ["collect", "test", "active-requests"],
        ["collect", "test", "server-info"],
        ["collect", "test", "database-status"],
        ["collect", "test", "long-running-requests"],
    ],
)
def test_cli_commands(
    instance: DatabaseInstance,
    driver: FakeDriver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    path = tmp_path / "inventory.yaml"
    entry = asdict(instance)
    entry["db_type"] = "sqlserver"
    path.write_text(yaml.safe_dump({"databases": [entry]}), encoding="utf-8")
    assert (
        main(
            ["--inventory", str(path), "--sql-root", str(ROOT / "sql"), *command, "--json"],
            provider=MemoryCredentialProvider(),
            driver=driver,
        )
        == 0
    )
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["success"] and TEST_CREDENTIAL not in output
    if command[0] == "test-connection":
        assert {key: result["connection"][key] for key in (
            "driver", "database", "encrypt", "trust_server_certificate"
        )} == {
            "driver": "ODBC Driver 18 for SQL Server",
            "database": "master",
            "encrypt": True,
            "trust_server_certificate": False,
        }
    assert driver.connection.closed


def test_cli_privileged_account_outputs_trigger_reason(
    instance: DatabaseInstance,
    driver: FakeDriver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "inventory.yaml"
    entry = asdict(instance)
    entry["db_type"] = "sqlserver"
    path.write_text(yaml.safe_dump({"databases": [entry]}), encoding="utf-8")
    driver.connection.results["monitor_permissions"].append(
        {"scope": "SERVER", "permission_name": "CONTROL SERVER", "source_type": "EXPLICIT",
         "source_name": "monitor", "object_name": None, "object_type": None,
         "execute_as_principal_id": None}
    )
    assert main(
        ["--inventory", str(path), "--sql-root", str(ROOT / "sql"), "test-connection", "test", "--json"],
        provider=MemoryCredentialProvider(), driver=driver,
    ) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_type"] == "PrivilegedAccount"
    assert result["trigger_reason"] == {
        "type": "server_permission", "name": "CONTROL SERVER", "source": "explicit",
        "principal": "monitor", "scope": "SERVER",
    }


def test_cli_other_database_and_disabled(
    capsys: pytest.CaptureFixture[str], driver: FakeDriver
) -> None:
    args = ["--inventory", str(ROOT / "examples/inventory/demo.yaml")]
    assert main([*args, "test-connection", "demo-postgresql"], driver=driver) == 1
    assert "已禁用" in capsys.readouterr().out
    assert main([*args, "test-connection", "demo-sqlserver"], driver=driver) == 1
    assert "已禁用" in capsys.readouterr().out
    assert not driver.calls


@pytest.mark.integration
def test_cli_check_and_status_human_and_json(
    instance: DatabaseInstance, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [{**asdict(instance), "db_type": "sqlserver"}]}),
                    encoding="utf-8")
    common = ["--inventory", str(path), "--sql-root", str(ROOT / "sql")]
    assert main([*common, "check", instance.id, "--json"],
                provider=MemoryCredentialProvider(), driver=FakeDriver()) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["success"] and checked["database_id"] == instance.id
    assert checked["connection"]["status"] == "CONNECTED"
    assert checked["account_security"]["classification"] == "NON_PRIVILEGED"
    assert "permissions" not in checked and not checked.get("password")
    assert main([*common, "check", instance.id],
                provider=MemoryCredentialProvider(), driver=FakeDriver()) == 0
    human = capsys.readouterr().out
    assert "DBA Toolkit 检查" in human and not human.lstrip().startswith("{")
    assert "版本状态" in human and "自动检测" in human
    assert TEST_CREDENTIAL not in human
    assert main([*common, "status", instance.id, "--json"],
                provider=MemoryCredentialProvider(), driver=FakeDriver()) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["success"] and current["active_requests"] == 2
    assert current["long_running"] == 2
    assert current["database_states"] == {"ONLINE": 1}


@pytest.mark.regression
def test_check_version_mismatch_reports_only_version_evidence(
    instance: DatabaseInstance, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "inventory.yaml"
    entry = {**asdict(instance), "db_type": "sqlserver", "version_family": "2019"}
    path.write_text(yaml.safe_dump({"databases": [entry]}), encoding="utf-8")
    assert main(["--inventory", str(path), "--sql-root", str(ROOT / "sql"),
                 "check", instance.id, "--json"],
                provider=MemoryCredentialProvider(), driver=FakeDriver()) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["version_profile"]["declared_version_family"] == "2019"
    assert result["version_profile"]["detected_version_family"] == "2022"
    assert result["version_profile"]["version_status"] == "MISMATCH"
    assert result["status"] == "VERSION_MISMATCH"
    assert "permissions" not in result


def test_status_reports_partial_collector_degradation(
    instance: DatabaseInstance, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    class DegradedCollector:
        def __init__(self, target: SQLServerAdapter) -> None:
            self.target = target

        def collect(self, target_instance: DatabaseInstance):
            result = ActiveRequestsCollector(self.target).collect(target_instance)
            return replace(result, success=False, error_type="CapabilityUnavailable")

    monkeypatch.setitem(__import__("dba.cli", fromlist=["COLLECTORS"]).COLLECTORS,
                        "active-requests", DegradedCollector)
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [{**asdict(instance), "db_type": "sqlserver"}]}),
                    encoding="utf-8")
    driver = FakeDriver()
    assert main(["--inventory", str(path), "--sql-root", str(ROOT / "sql"),
                 "status", instance.id, "--json"],
                provider=MemoryCredentialProvider(), driver=driver) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PARTIAL"
    assert any("active-requests" in warning for warning in result["warnings"])


def test_settings_validation(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    for values in [
        {"password": 'DBA-Test-Secret-987654'},
        {"connect_timeout": 0},
        {"query_timeout": True},
        {"long_running_seconds": -1},
        {"encrypt": "yes"},
    ]:
        path.write_text(yaml.safe_dump({"sqlserver": values}), encoding="utf-8")
        with pytest.raises(ValueError):
            SQLServerSettings.load(path)


def test_prompt_no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba.credentials import PromptCredentialProvider

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ToolkitError, match="terminal"):
        PromptCredentialProvider().get_credential("monitor")


def test_driver_lazy_import(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import driver_manager
    from dba.core import DatabaseType
    from dba.sqlserver import load_driver

    module = Mock()
    calls: list[tuple[DatabaseType, str]] = []

    def load(database: DatabaseType, module_name: str) -> Mock:
        calls.append((database, module_name))
        return module

    monkeypatch.setattr(driver_manager, "load_database_driver", load)
    assert load_driver() is module and module.pooling is False
    assert calls == [(DatabaseType.SQLSERVER, "pyodbc")]


def test_changed_registered_script(
    adapter: SQLServerAdapter, driver: FakeDriver, tmp_path: Path
) -> None:
    shutil.copytree(ROOT / "sql", tmp_path / "sql")
    adapter.registry = SqlScriptRegistry(tmp_path / "sql")
    adapter.connect()
    script = adapter.registry.get("server_info")
    (tmp_path / "sql/sqlserver/server_info.sql").write_text("SELECT 42;", encoding="utf-8")
    before = len(driver.connection.executed)
    result = adapter.execute_readonly(script)
    assert not result.success and result.error_type == "RegistryRejected"
    assert len(driver.connection.executed) == before


def test_legit_unreviewed_script_cannot_execute(adapter: SQLServerAdapter, tmp_path: Path) -> None:
    shutil.copytree(ROOT / "sql", tmp_path / "sql")
    adapter.registry = SqlScriptRegistry(tmp_path / "sql", formal=False)
    adapter.connect()
    path = tmp_path / "sql/sqlserver/version_probe.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["reviewed"] = False
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = adapter.execute_readonly(adapter.registry.get("version_probe"))
    assert not result.success and result.error_type == "RegistryRejected"


def test_long_requests_preserve_truncation(
    adapter: SQLServerAdapter, driver: FakeDriver, tmp_path: Path
) -> None:
    shutil.copytree(ROOT / "sql", tmp_path / "sql")
    path = tmp_path / "sql/sqlserver/active_requests.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["max_rows"] = 2
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    adapter.registry = SqlScriptRegistry(tmp_path / "sql")
    with adapter:
        result = LongRunningRequestsCollector(adapter).collect(adapter.instance)
        assert result.truncated and result.row_count == 2


def test_preflight_truncation_fails_closed(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    limit = adapter.registry.get("monitor_permissions").metadata.max_rows
    driver.connection.results["monitor_permissions"] = [
        {"scope": "SERVER", "permission_name": "CONNECT SQL", "source_type": "EFFECTIVE",
         "source_name": "effective server permissions; source not exposed", "object_name": None,
         "object_type": None, "execute_as_principal_id": None}
    ] * limit
    with pytest.raises(ToolkitError, match="row limit"):
        adapter.connect()
    assert driver.connection.closed


def test_python_query_timeout(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    adapter.connect()
    driver.connection.failures["active_requests"] = TimeoutError(TEST_CREDENTIAL)
    result = ActiveRequestsCollector(adapter).collect(adapter.instance)
    assert result.error_type == "QueryTimeout" and driver.connection.closed


def test_no_autocommit_rejected(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    driver.connection.autocommit = False
    with pytest.raises(ToolkitError, match="autocommit"):
        adapter.connect()
    assert driver.connection.rollbacks == 1 and driver.connection.closed
    assert not driver.connection.executed


def test_preflight_required(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    with pytest.raises(ToolkitError, match="preflight"):
        adapter.execute_readonly(adapter.registry.get("server_info"))
    assert not driver.calls


def test_sql_literal_password_redaction() -> None:
    from dba.logging import redact

    for text in [
        "PASSWORD=N'<TEST_ONLY> with spaces'",
        '"password": "DBA-Test-Secret-987654"',
        "PWD={escaped}};still secret}",
        "Server=host;Password=DBA-Test-Secret-987654",
    ]:
        assert "secret" not in redact(text)


def test_cli_failure_safe(
    instance: DatabaseInstance,
    driver: FakeDriver,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = {**asdict(instance), "db_type": "sqlserver"}
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [entry]}), encoding="utf-8")
    driver.failure = RuntimeError(TEST_CREDENTIAL)
    assert (
        main(
            ["--inventory", str(path), "--sql-root", str(ROOT / "sql"), "test-connection", "test", "--json"],
            provider=MemoryCredentialProvider(),
            driver=driver,
        )
        == 1
    )
    output = capsys.readouterr().out
    result = json.loads(output)
    assert TEST_CREDENTIAL not in output and result["error_type"] == "ConnectionFailed"
    assert "Encrypt=yes" in result["error_message"]
    assert "TrustServerCertificate=no" in result["error_message"]
    assert "weaker TLS" in result["error_message"]


def test_prompt_provider_gets_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba.credentials import PromptCredentialProvider

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "monitor")
    monkeypatch.setattr("getpass.getpass", lambda prompt: TEST_CREDENTIAL)
    value = PromptCredentialProvider().get_credential("alias")
    assert value.password == TEST_CREDENTIAL and TEST_CREDENTIAL not in repr(value)


def test_interrupted_query_closes(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    adapter.connect()
    driver.connection.failures["active_requests"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ActiveRequestsCollector(adapter).collect(adapter.instance)
    assert driver.connection.closed and all(c.closed for c in driver.connection.cursors)


def test_interrupted_preflight_closes(adapter: SQLServerAdapter, driver: FakeDriver) -> None:
    driver.connection.failures["monitor_permissions"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        adapter.connect()
    assert driver.connection.closed and adapter._connection is None


def evaluate_active_request_text(batch: str | None, start: int | None,
                                 end: int | None) -> tuple[Any, ...]:
    """Exercise the Registry's byte expressions on SQLite blobs without a live DMV."""
    sql = (ROOT / "sql/sqlserver/active_requests.sql").read_text(encoding="utf-8")
    projection = sql[sql.index("CONVERT(nvarchar(max), SUBSTRING"):sql.index("FROM sys.dm_exec_requests")]
    stages = re.findall(r"CROSS APPLY \(\s*SELECT (.*?)\n\) AS (\w+)", sql, re.DOTALL)
    assert [alias for _, alias in stages] == ["source", "span", "limits"]

    def sqlite_syntax(expression: str) -> str:
        for original, replacement in (
                ("CONVERT(varbinary(max), ", "utf16("), ("CONVERT(nvarchar(max), ", "decode_utf16("),
                ("CONVERT(bigint, ", "as_bigint(")):
            expression = expression.replace(original, replacement)
        return re.sub(r"0x([0-9a-fA-F]+)", r"X'\1'", expression)

    with closing(sqlite3.connect(":memory:")) as connection:
        connection.create_function("utf16", 1, lambda value: value.encode("utf-16-le") if value is not None else None)
        connection.create_function("decode_utf16", 1, lambda value: value.decode("utf-16-le") if value is not None else None)
        connection.create_function("DATALENGTH", 1, lambda value: len(value.encode("utf-16-le")) if value is not None else None)
        connection.create_function("as_bigint", 1, lambda value: value)
        ctes = ["t(text) AS (VALUES (?))", "r(statement_start_offset, statement_end_offset) AS (VALUES (?, ?))"]
        sources = ["t", "r"]
        for expression, alias in stages:
            ctes.append(f"{alias} AS (SELECT {sqlite_syntax(expression)} FROM {', '.join(sources)})")
            sources.append(alias)
        row = connection.execute(
            f"WITH {', '.join(ctes)} SELECT {sqlite_syntax(projection)} FROM {', '.join(sources)}",
            (batch, start, end),
        ).fetchone()
    assert isinstance(row, tuple)
    return row


@pytest.mark.parametrize("statement", [
    "SELECT 1 OPTION (HASH JOIN, MAXDOP 1)",
    "SELECT 1 AS x",
    "SELECT 17",
    "SELECT N'中文🚀'",
    "WAITFOR DELAY '00:00:01'",
])
def test_active_request_inclusive_end_offset_preserves_final_character(statement: str) -> None:
    prefix = "SELECT N'前😀';\n"
    batch = prefix + statement + ";\nSELECT 3;"
    start = len(prefix.encode("utf-16-le"))
    end = start + len(statement.encode("utf-16-le")) - 2
    assert evaluate_active_request_text(batch, start, end) == (batch, 0, statement, None, 0)


def test_active_request_utf16_pair_end_and_batch_end_sentinel() -> None:
    assert evaluate_active_request_text("a🚀b", 2, 2)[2:] == ("🚀", None, 0)
    assert evaluate_active_request_text("a🚀b", 2, 4)[2:] == ("🚀", None, 0)
    assert evaluate_active_request_text("SELECT 1;SELECT N'😀'  ", 18, -1)[2:] == ("SELECT N'😀'  ", None, 0)
    assert evaluate_active_request_text("SELECT 1", 0, 16)[2:] == ("SELECT 1", None, 0)


@pytest.mark.parametrize(("start", "end"), [(None, -1), (1, -1), (4, 2), (4, 100), (4, -2), (4, 5)])
def test_active_request_rejects_invalid_or_split_start_offsets(start: int | None, end: int) -> None:
    row = evaluate_active_request_text("a🚀b", start, end)
    assert row[2:] == (None, "Current statement offsets are unavailable or invalid", 0)


def test_active_request_caps_batch_and_statement_without_splitting_surrogate() -> None:
    prefix = "x" * 16383
    batch = prefix + "😀tail"
    for end in (-1, len(batch.encode("utf-16-le")) - 2):
        assert evaluate_active_request_text(batch, 0, end) == (prefix, 1, prefix, None, 1)
    start = len((prefix + "😀").encode("utf-16-le"))
    assert evaluate_active_request_text(batch, start, -1) == (prefix, 1, "tail", None, 0)
    assert evaluate_active_request_text("x", 2, -1)[2:] == (
        None, "Current statement contains no text at the sampled offsets", 0)
    assert evaluate_active_request_text(None, 0, 40000) == (
        None, 0, None, "SQL text unavailable from sys.dm_exec_sql_text", 0)
