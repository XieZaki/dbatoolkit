import json
from types import SimpleNamespace

import pytest
from test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider

from dba.capabilities import ServerCapabilities
from dba.cli import main
from dba.core import DatabaseInstance, DatabaseType
from dba.environment import driver_info
from dba.errors import ToolkitError
from dba.registry import SqlScriptRegistry
from dba.sqlserver import SQLServerAdapter
from dba.sqlserver_collectors import ActiveSessionsCollector


@pytest.mark.parametrize("version", ["10.0.6000.1", "9.0.5000.0", "unknown"])
def test_old_or_unknown_version_rejected(version: str) -> None:
    with pytest.raises(ToolkitError):
        ServerCapabilities(version, False, False)


def test_capability_routing() -> None:
    assert (
        ServerCapabilities("10.50.6000.34", False, False).sessions_script
        == "active_sessions_legacy"
    )
    assert ServerCapabilities("11.0.7001.0", True, True).sessions_script == "active_sessions"
    assert (
        ServerCapabilities("16.0.1000.6", True, False).sessions_script == "active_sessions_legacy"
    )


def test_legacy_preflight_and_sessions() -> None:
    instance = DatabaseInstance(
        "legacy", "Legacy", DatabaseType.SQLSERVER, "legacy.invalid", 1433, "monitor"
    )
    driver = FakeDriver()
    driver.connection.results["monitor_capabilities"][0].update(
        major_version=10,
        view_server_state=1,
        view_server_performance_state=None,
        sessions_database_id=0,
        sessions_open_transaction_count=0,
    )
    driver.connection.results["server_info"][0]["product_version"] = "10.50.6000.34"
    driver.connection.results["active_sessions_legacy"] = [
        {
            "session_id": 52,
            "database_name": "master",
            "open_transaction_count": 2,
            "open_transaction_count_source": "sys.sysprocesses.open_tran",
        }
    ]
    with SQLServerAdapter(
        instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"), driver=driver
    ) as adapter:
        result = ActiveSessionsCollector(adapter).collect(instance)
        assert result.success and result.collector_name == "active_sessions"
        assert result.rows[0]["open_transaction_count"] == 2
        assert result.rows[0]["open_transaction_count_source"] == "sys.sysprocesses.open_tran"
        assert "active_sessions_legacy" in driver.connection.executed
        assert "active_sessions" not in driver.connection.executed
    assert driver.connection.closed


def test_unavailable_capability_fails_closed() -> None:
    instance = DatabaseInstance(
        "test", "Test", DatabaseType.SQLSERVER, "test.invalid", 1433, "monitor"
    )
    driver = FakeDriver()
    del driver.connection.results["monitor_capabilities"][0]["sessions_database_id"]
    with pytest.raises(ToolkitError, match="capability information"):
        SQLServerAdapter(
            instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"), driver=driver
        ).connect()
    assert driver.connection.closed


def test_environment_missing_pyodbc(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> object:
        raise ImportError("potential secret not to emit")

    monkeypatch.setattr("dba.environment.importlib.import_module", missing)
    monkeypatch.setattr("dba.environment._windows_drivers", lambda: ["SQL Server"])
    info = driver_info()
    assert info["pyodbc_version"] is None and not info["ready"]
    assert "potential secret" not in json.dumps(info)


def test_environment_ready_without_inventory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = SimpleNamespace(version="5.2.0", drivers=lambda: ["ODBC Driver 18 for SQL Server"])
    monkeypatch.setattr("dba.environment.importlib.import_module", lambda name: module)
    assert main(["--inventory", "does-not-exist.yaml", "driver-info", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["ready"] and info["pyodbc_version"] == "5.2.0"


def test_environment_missing_driver18(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(version="5.2.0", drivers=lambda: ["SQL Server"])
    monkeypatch.setattr("dba.environment.importlib.import_module", lambda name: module)
    info = driver_info()
    assert not info["ready"] and not info["driver18_present"]


def test_environment_failure_no_exception_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> list[str]:
        raise RuntimeError("password=hidden")

    module = SimpleNamespace(version="5.2.0", drivers=fail)
    monkeypatch.setattr("dba.environment.importlib.import_module", lambda name: module)
    monkeypatch.setattr("dba.environment._windows_drivers", lambda: [])
    info = driver_info()
    assert not info["ready"] and "hidden" not in json.dumps(info)
