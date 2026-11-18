import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from dba.cli import _effective_settings
from dba.core import DatabaseInstance, DatabaseType
from dba.diagnostic_config import PARAMETERS, flatten_diagnostics, resolve_diagnostics
from dba.incident import build_incident_summary
from dba.inventory import Inventory, ValidationError
from dba.presentation import render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.settings import MySQLSettings, OracleSettings, PostgreSQLSettings, SQLServerSettings
from dba.slow import SlowReport


@pytest.mark.parametrize("engine,settings", [
    (DatabaseType.SQLSERVER, SQLServerSettings()),
    (DatabaseType.ORACLE, OracleSettings()),
    (DatabaseType.MYSQL, MySQLSettings()),
    (DatabaseType.POSTGRESQL, PostgreSQLSettings()),
])
def test_defaults_and_instance_policy(engine: DatabaseType, settings: object) -> None:
    instance = DatabaseInstance("example", "Example", engine, "db.example", 1234, "example")
    effective, evidence = resolve_diagnostics(settings, instance, None)
    assert effective.long_running_seconds == 10
    assert effective.slow_top_n == 10
    assert effective.high_concurrency_threshold == 50
    assert effective.diagnostic_sql_timeout_seconds == 30
    assert all(item["source"] == "DEFAULT" for item in evidence.values())
    instance = replace(instance, diagnostics={"slow": {"threshold_seconds": 12}, "limits": {"top_n": 7}})
    effective, evidence = resolve_diagnostics(settings, instance, None, top=3)
    assert effective.long_running_seconds == 12
    assert effective.slow_top_n == 3
    assert effective.effective_sql_timeout(5) == 5
    assert evidence["limits.top_n"]["source"] == "CLI"
    assert evidence["slow.threshold_seconds"]["source"] == "INVENTORY_INSTANCE"


def test_inventory_defaults_global_legacy_and_cli(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
defaults:
  diagnostics:
    limits: {top_n: 7}
    slow: {threshold_seconds: 15}
databases:
  - id: demo
    name: Example
    db_type: sqlserver
    host: db.example
    port: 1433
    credential_alias: example
    enabled: false
    diagnostics:
      slow: {threshold_seconds: 21}
""", encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    settings.write_text("""
sqlserver:
  long_running_seconds: 11
diagnostics:
  slow: {threshold_seconds: 13}
  limits: {top_n: 6}
  transactions: {sqlserver_long_transaction_seconds: 4000}
""", encoding="utf-8")
    instance = Inventory.load(inventory).get("demo")
    effective, evidence = resolve_diagnostics(_effective_settings(instance, settings), instance, settings, top=4)
    assert effective.long_running_seconds == 21
    assert effective.slow_top_n == 4
    assert effective.sqlserver_long_transaction_seconds == 4000
    assert [item["source"] for item in evidence["slow.threshold_seconds"]["overridden"]] == [
        "DEFAULT", "SETTINGS_ENGINE", "SETTINGS_GLOBAL", "INVENTORY_DEFAULTS",
    ]
    assert evidence["limits.top_n"]["overridden"][-1] == {"source": "INVENTORY_DEFAULTS", "value": 7}


@pytest.mark.parametrize("name", PARAMETERS)
@pytest.mark.parametrize("value", [None, "10", 1.5, [], {}, -1, 0, 1000000001])
def test_invalid_policy_values(name: str, value: object) -> None:
    parts = name.split(".")
    document: object = value
    for part in reversed(parts):
        document = {part: document}
    with pytest.raises(ValidationError):
        flatten_diagnostics(document)


@pytest.mark.parametrize("document", [
    {"slow": {"unknown": 1}}, {"unknown": 1}, {"plans": {"auto_collect": True}},
    {"limits": {"top_n": 101}}, {"diagnostic_sql_timeout_seconds": 61},
    {"plans": {"sqlserver": {"top_operators": 101}}},
])
def test_unsafe_or_unimplemented_settings_rejected(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        flatten_diagnostics(document)


@pytest.mark.parametrize("document", [
    {"plans": {"auto_collect": False}}, {"plans": {"auto_collect": True}},
    {"plans": {"max_plans_per_report": 5}},
])
def test_automatic_plan_options_are_not_formal_configuration(document: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="supported policy fields"):
        flatten_diagnostics(document)


@pytest.mark.parametrize("engine", [
    DatabaseType.SQLSERVER, DatabaseType.MYSQL, DatabaseType.ORACLE, DatabaseType.POSTGRESQL,
])
def test_report_policy_evidence_uses_resolved_values(engine: DatabaseType) -> None:
    instance = DatabaseInstance("demo", "Example", engine, "db.example", 1234, "example",
                                diagnostics={"slow": {"threshold_seconds": 17}})
    settings = _effective_settings(instance, None)
    _, evidence = resolve_diagnostics(settings, instance, None)
    report = SlowReport(instance, datetime.now(timezone.utc), "slow", (), {
        "diagnostic_configuration": evidence, "all_requests": [], "active_requests": [],
    })
    payload = json.loads(render_json(report))
    assert payload["details"]["diagnostic_configuration"]["slow.threshold_seconds"]["value"] == 17
    model = build_report_model(report, build_incident_summary(report))
    assert dict(model.metadata)["slow.threshold_seconds"] == "17 (INVENTORY_INSTANCE)"
    page = HtmlReportRenderer().render(model)
    assert "17 (INVENTORY_INSTANCE)" in page


def test_legacy_settings_and_shared_only_yaml(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    instance = DatabaseInstance("demo", "Example", DatabaseType.SQLSERVER, "db.example", 1433, "example")
    path.write_text("sqlserver:\n  long_running_seconds: 22\n", encoding="utf-8")
    assert _effective_settings(instance, path).long_running_seconds == 22
    path.write_text("diagnostics:\n  slow:\n    threshold_seconds: 18\n", encoding="utf-8")
    assert _effective_settings(instance, path).long_running_seconds == 18


def test_sqlserver_transaction_threshold_is_independent(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("diagnostics:\n  transactions:\n"
                    "    idle_threshold_seconds: 40\n"
                    "    sqlserver_long_transaction_seconds: 7200\n", encoding="utf-8")
    instance = DatabaseInstance("demo", "Example", DatabaseType.SQLSERVER, "db.example", 1433, "example")
    sqlserver = _effective_settings(instance, path)
    postgres = _effective_settings(replace(instance, db_type=DatabaseType.POSTGRESQL), path)
    assert sqlserver.sqlserver_long_transaction_seconds == 7200
    assert postgres.idle_transaction_seconds == 40
    assert not hasattr(sqlserver, "idle_transaction_seconds")


@pytest.mark.parametrize("top,expected,source", [(None, 7, "INVENTORY_INSTANCE"), (3, 3, "CLI")])
@pytest.mark.parametrize("scenario", ["top-sql", "active-sql"])
@pytest.mark.parametrize("json_console", [True, False])
def test_cli_uses_effective_top_and_same_report_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], top: int | None, expected: int, source: str,
    scenario: str, json_console: bool,
) -> None:
    from dba.cli import main
    from tests.test_active_sql_sqlserver import _row
    from tests.test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider
    from tests.test_top_sql_sqlserver import row

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
databases:
  - id: demo
    name: Example
    db_type: sqlserver
    host: test.invalid
    port: 1433
    credential_alias: example
    version_family: "2022"
    diagnostics:
      limits: {top_n: 7}
""", encoding="utf-8")
    driver = FakeDriver()
    driver.connection.results["top_sql"] = [row()]
    driver.connection.results["active_sql"] = [_row(51, cpu=80, reads=12, elapsed=20)]
    html = tmp_path / "top.html"
    json_path = tmp_path / "capture.json"
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "demo", scenario, "--report", str(html), "--json-report", str(json_path)]
    if json_console:
        args.append("--json")
    if top is not None:
        args.extend(["--top", str(top)])
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    console = capsys.readouterr().out
    output = json.loads(json_path.read_text(encoding="utf-8"))
    if json_console:
        assert json.loads(console) == output
    else:
        assert f"limits.top_n = {expected} ({source})" in console
    assert driver.connection.bound_parameters[-1] == (
        (expected, "cpu_time") if scenario == "top-sql" else (expected, expected, expected))
    evidence = output["diagnostic_configuration"]["limits.top_n"]
    assert (evidence["value"], evidence["source"]) == (expected, source)
    assert output["report"]["details"]["diagnostic_configuration"] == output["diagnostic_configuration"]
    assert f"{expected} ({source})" in html.read_text(encoding="utf-8")


def test_unavailable_automatic_plans_rejected_before_credentials(tmp_path: Path) -> None:
    from unittest.mock import Mock

    from dba.cli import main

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
databases:
  - id: demo
    name: Example
    db_type: sqlserver
    host: test.invalid
    port: 1433
    credential_alias: example
    diagnostics:
      plans: {auto_collect: true}
""", encoding="utf-8")
    provider, driver = Mock(), Mock()
    assert main(["--inventory", str(inventory), "diagnose", "demo", "slow", "--json"],
                provider=provider, driver=driver) == 1
    provider.get_credential.assert_not_called()
    driver.connect.assert_not_called()


PRIORITY_CASES = [
    (engine, name)
    for engine in (DatabaseType.SQLSERVER, DatabaseType.MYSQL, DatabaseType.POSTGRESQL, DatabaseType.ORACLE)
    for name in (
        "diagnostic_sql_timeout_seconds", "slow.threshold_seconds", "limits.top_n",
        "concurrency.active_requests_threshold",
        "transactions.sqlserver_long_transaction_seconds" if engine == DatabaseType.SQLSERVER
        else "transactions.idle_threshold_seconds",
    )
]


def nested_policy(name: str, value: int) -> dict:
    result: dict = {}
    node = result
    parts = name.split(".")
    for part in parts[:-1]:
        node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return result


@pytest.mark.parametrize("engine,name", PRIORITY_CASES)
@pytest.mark.parametrize("level", range(5))
def test_each_policy_priority_layer(
    tmp_path: Path, engine: DatabaseType, name: str, level: int,
) -> None:
    field, default, _, _ = PARAMETERS[name]
    values = [default, 11, 21, 31, 41]
    if name == "diagnostic_sql_timeout_seconds":
        values = [30, 30, 19, 17, 13]  # Legacy engine placement is deliberately forbidden.
    elif name == "transactions.sqlserver_long_transaction_seconds":
        values = [3600, 3611, 3621, 3631, 3641]
    config: dict = {engine.value: {}}
    if level >= 1 and name != "diagnostic_sql_timeout_seconds":
        config[engine.value][field] = values[1]
    if level >= 2:
        config["diagnostics"] = nested_policy(name, values[2])
    entry = {
        "id": "demo", "name": "Example", "db_type": engine.value, "host": "test.invalid",
        "port": 1234, "credential_alias": "example", "enabled": False,
    }
    inventory: dict = {"databases": [entry]}
    if level >= 3:
        inventory["defaults"] = {"diagnostics": nested_policy(name, values[3])}
    if level >= 4:
        entry["diagnostics"] = nested_policy(name, values[4])
    inventory_path, settings_path = tmp_path / "inventory.yaml", tmp_path / "settings.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory), encoding="utf-8")
    settings_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    instance = Inventory.load(inventory_path).get("demo")
    settings = _effective_settings(instance, settings_path)
    _, evidence = resolve_diagnostics(settings, instance, settings_path)
    expected_source = ["DEFAULT", "SETTINGS_ENGINE", "SETTINGS_GLOBAL",
                       "INVENTORY_DEFAULTS", "INVENTORY_INSTANCE"][level]
    if name == "diagnostic_sql_timeout_seconds" and level == 1:
        expected_source = "DEFAULT"
    assert getattr(settings, field) == values[level]
    assert evidence[name]["value"] == values[level]
    assert evidence[name]["source"] == expected_source
    if name == "limits.top_n":
        cli_settings, cli_evidence = resolve_diagnostics(settings, instance, settings_path, top=3)
        assert cli_settings.slow_top_n == 3
        assert cli_evidence[name]["source"] == "CLI"


@pytest.mark.parametrize("name", PARAMETERS)
def test_hard_limit_evidence_and_rejection(name: str) -> None:
    _, _, _, maximum = PARAMETERS[name]
    instance = DatabaseInstance("demo", "Example", DatabaseType.SQLSERVER, "test.invalid", 1433, "example",
                                diagnostics=nested_policy(name, maximum))
    # Ratio pairs are constrained together.
    if name == "plans.sqlserver.cardinality_ratio_warning":
        instance.diagnostics["plans"]["sqlserver"]["cardinality_ratio_critical"] = maximum
        with pytest.raises(ValidationError):
            _effective_settings(instance, None)
    elif name != "transactions.idle_threshold_seconds":
        _, evidence = resolve_diagnostics(SQLServerSettings(), instance, None)
        assert evidence[name]["at_hard_limit"] is True
    with pytest.raises(ValidationError):
        flatten_diagnostics(nested_policy(name, maximum + 1))


@pytest.mark.parametrize("engine", [
    DatabaseType.SQLSERVER, DatabaseType.MYSQL, DatabaseType.POSTGRESQL, DatabaseType.ORACLE,
])
def test_real_collectors_filter_at_instance_threshold(engine: DatabaseType) -> None:
    from dba.mysql_collectors import LongRunningCollector as MySQLLong
    from dba.oracle_collectors import LongRunningCollector as OracleLong
    from dba.postgresql_collectors import LongRunningCollector as PgLong
    from dba.registry import SqlScriptRegistry
    from dba.sqlserver import SQLServerAdapter
    from dba.sqlserver_collectors import LongRunningRequestsCollector
    from tests.test_mysql import adapter as mysql_adapter
    from tests.test_oracle import adapter_for_test as oracle_adapter
    from tests.test_postgresql import adapter as pg_adapter
    from tests.test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider

    instance = DatabaseInstance("demo", "Example", engine, "test.invalid", 1234, "example",
                                diagnostics={"slow": {"threshold_seconds": 17}})
    settings = _effective_settings(instance, None)
    if engine == DatabaseType.MYSQL:
        target, driver = mysql_adapter(settings=settings)
        collector, script = MySQLLong, "active_requests"
    elif engine == DatabaseType.POSTGRESQL:
        target, driver = pg_adapter(settings=settings)
        collector, script = PgLong, "long_running"
    elif engine == DatabaseType.ORACLE:
        target, driver = oracle_adapter(settings=settings)
        collector, script = OracleLong, "long_running"
    else:
        driver = FakeDriver()
        target = SQLServerAdapter(instance, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"),
                                  settings, driver=driver)
        collector, script = LongRunningRequestsCollector, "active_requests"
    target.connect()
    try:
        rows_by_script = driver.connection.data if engine == DatabaseType.ORACLE else driver.connection.results
        rows_by_script[script] = [
            {"session_id": 11, "duration_ms": 16999, "elapsed_ms": 16999, "duration_seconds": 16},
            {"session_id": 12, "duration_ms": 17000, "elapsed_ms": 17000, "duration_seconds": 17},
        ]
        result = collector(target).collect(target.instance)
        assert result.success
        assert [row["session_id"] for row in result.rows] == [12]
    finally:
        target.disconnect()
