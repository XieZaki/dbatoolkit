import inspect
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from dba.adapters import Adapter, MySQLAdapter, OracleAdapter, PostgreSQLAdapter
from dba.analyzers import Analyzer, AnalyzerRegistry, SlowAnalyzer
from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseType, DiagnosticReport, Finding, ReferenceCommand, Severity
from dba.credentials import (
    Credential,
    MockCredentialProvider,
    Mode,
    validate_monitoring_credential,
    validate_provider,
)
from dba.inventory import Inventory, ValidationError
from dba.logging import SafeFormatter, redact
from dba.registry import SqlScriptRegistry
from dba.reporters import ConsoleReporter, MarkdownReporter
from dba.security import check_sql

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entry() -> dict[str, object]:
    return {
        "id": "one",
        "name": "One",
        "db_type": "sqlserver",
        "host": "example.invalid",
        "port": 1433,
        "credential_alias": "monitor",
        "environment": "production",
    }


def inventory_file(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": entries}), encoding="utf-8")
    return path


def test_inventory_load_filter(tmp_path: Path, entry: dict[str, object]) -> None:
    other = {**entry, "id": "two", "db_type": "postgresql", "environment": "development"}
    inventory = Inventory.load(inventory_file(tmp_path, [entry, other]))
    assert inventory.get("one").credential_alias == "monitor"
    assert len(inventory.filter("sqlserver", "production")) == 1
    assert not inventory.filter("mysql")
    with pytest.raises(ValidationError):
        inventory.get("missing")


@pytest.mark.parametrize(
    "changes",
    [
        {"db_type": "invalid"},
        {"credential_alias": ""},
        {"port": True},
        {"port": 0},
        {"enabled": "false"},
        {"password": 'DBA-Test-Secret-987654'},
        {"nested": {"token": 'DBA-Test-Secret-987654'}},
        {"host": None},
        {"name": " "},
        {"port": 65536},
        {"host": 'postgresql://monitor:<EXAMPLE_PASSWORD>@host/db'},
        {"name": "password=hidden"},
    ],
)
def test_inventory_rejects(
    tmp_path: Path, entry: dict[str, object], changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        Inventory.load(inventory_file(tmp_path, [{**entry, **changes}]))


def test_duplicate_and_missing(tmp_path: Path, entry: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Inventory.load(inventory_file(tmp_path, [entry, entry]))
    del entry["credential_alias"]
    with pytest.raises(ValidationError):
        Inventory.load(inventory_file(tmp_path, [entry]))
    path = tmp_path / "duplicate.yaml"
    path.write_text("databases: []\ndatabases: []", encoding="utf-8")
    with pytest.raises(ValidationError):
        Inventory.load(path)


def test_credentials() -> None:
    credential = Credential("monitor", "hidden")
    provider = MockCredentialProvider({"alias": credential})
    assert provider.get_credential("alias") is credential
    assert "hidden" not in repr(credential)
    with pytest.raises(KeyError):
        provider.get_credential("unknown")
    with pytest.raises(ValueError):
        MockCredentialProvider({}, Mode.PRODUCTION)
    with pytest.raises(ValueError):
        validate_provider(provider, Mode.PRODUCTION)
    validate_provider(provider, Mode.DEVELOPMENT)
    validate_monitoring_credential(credential)
    for user in ["sa", "ROOT", "SYS", "SYSTEM", "postgres"]:
        with pytest.raises(ValueError):
            validate_monitoring_credential(Credential(user, ""))


@pytest.fixture
def script(tmp_path: Path) -> Path:
    directory = tmp_path / "sqlserver"
    directory.mkdir()
    path = directory / "probe.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    path.with_suffix(".yaml").write_text(
        yaml.safe_dump(
            {
                "name": "probe",
                "database_type": "sqlserver",
                "risk": "READ_ONLY",
                "timeout_seconds": 5,
                "max_rows": 1,
                "reviewed": True,
                "description": "fixture",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_sql_metadata(script: Path, tmp_path: Path) -> None:
    loaded = SqlScriptRegistry(tmp_path).load(script)
    assert loaded.metadata.max_rows == 1
    assert len(SqlScriptRegistry(tmp_path).validate()) == 1
    with pytest.raises(ValidationError):
        SqlScriptRegistry(script.parent).load(tmp_path / "outside.sql")


@pytest.mark.parametrize(
    "key,value",
    [
        ("risk", "WRITE"),
        ("reviewed", False),
        ("reviewed", "true"),
        ("timeout_seconds", 0),
        ("max_rows", True),
        ("database_type", "invalid"),
        ("database_type", "mysql"),
        ("name", "mismatch"),
        ("description", ""),
    ],
)
def test_metadata_rejects(script: Path, tmp_path: Path, key: str, value: object) -> None:
    metadata = script.with_suffix(".yaml")
    data = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    data[key] = value
    metadata.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        SqlScriptRegistry(tmp_path).load(script)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO x VALUES(1)",
        "UPDATE x SET a=1",
        "DELETE FROM x",
        "MERGE x",
        "ALTER TABLE x",
        "DROP TABLE x",
        "TRUNCATE TABLE x",
        "CREATE TABLE x(a int)",
        "GRANT x",
        "REVOKE x",
        "KILL 12",
        "SHUTDOWN",
        "SELECT pg_terminate_backend(12)",
        "SELECT pg_cancel_backend(12)",
        "SELECT 1 INTO x",
        "SELECT 1; EXEC x",
        "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
        "SELECT nextval('seq')",
        "SELECT * FROM t FOR SHARE",
        "SELECT 1; DROP TABLE x",
    ],
)
def test_dangerous_sql(sql: str) -> None:
    with pytest.raises(ValidationError):
        check_sql(sql)


def test_registry_refuses_dangerous(script: Path, tmp_path: Path) -> None:
    script.write_text("SELECT pg_terminate_backend(123);", encoding="utf-8")
    with pytest.raises(ValidationError):
        SqlScriptRegistry(tmp_path).validate()
    script.unlink()
    with pytest.raises(ValidationError):
        SqlScriptRegistry(tmp_path).validate()


def test_logging_redaction() -> None:
    data = {
        "password": 'DBA-Test-Secret-987654',
        "nested": [{"API_TOKEN": "p2"}],
        "text": "token=DBA-Test-Secret-987654",
    }
    rendered = str(redact(data))
    for secret in ["p1", "p2", "p3", "p 4", "p5", "odbc", "hidden"]:
        assert secret not in rendered
    record = logging.LogRecord("dba", logging.ERROR, "", 1, "unlabeled-secret", (), None)
    record.event = {"module": "cli", "result": "token=p6", "password": 'DBA-Test-Secret-987654'}
    output = SafeFormatter().format(record)
    assert "unlabeled-secret" not in output and "p6" not in output and "p7" not in output


def test_interfaces_and_reporters() -> None:
    for interface in [
        Adapter,
        Analyzer,
    ]:
        assert inspect.isabstract(interface)
    assert not inspect.isabstract(PostgreSQLAdapter)
    assert not inspect.isabstract(MySQLAdapter)
    assert not inspect.isabstract(OracleAdapter)
    assert not inspect.isabstract(SlowAnalyzer)
    reference = ReferenceCommand("KILL 182;", "Review session first")
    assert not hasattr(reference, "execute")
    assert reference.manual_only
    instance = Inventory.load(ROOT / "examples/inventory/demo.yaml").get("demo-sqlserver")

    class FixtureAnalyzer(Analyzer):
        def analyze(
            self, instance: object, results: tuple[CollectorResult, ...]
        ) -> tuple[Finding, ...]:
            return (
                Finding(
                    "Mock finding",
                    Severity.WARNING,
                    {"duration": 60},
                    "DBA review",
                    (reference,),
                    "182",
                    "monitor",
                    "client",
                    "127.0.0.1",
                    "SELECT 1",
                ),
            )

    registry = AnalyzerRegistry()
    analyzer = FixtureAnalyzer()
    registry.register(DatabaseType.SQLSERVER, "slow", analyzer)
    assert registry.get(DatabaseType.SQLSERVER, "slow") is analyzer
    with pytest.raises(KeyError):
        registry.get(DatabaseType.POSTGRESQL, "slow")
    with pytest.raises(ValueError):
        registry.register(DatabaseType.SQLSERVER, "slow", analyzer)
    report = DiagnosticReport(
        instance, datetime.now(timezone.utc), "mock", analyzer.analyze(instance, ())
    )
    for reporter in [ConsoleReporter(), MarkdownReporter()]:
        stream = io.StringIO()
        reporter.write(report, stream)
        text = stream.getvalue()
        for expected in [
            "demo-sqlserver",
            "sqlserver",
            "WARNING",
            "duration",
            "182",
            "monitor",
            "client",
            "127.0.0.1",
            "SELECT 1",
            "DBA review",
            "KILL 182",
            "manual",
        ]:
            assert expected in text


def test_production_adapter_rejects_mock() -> None:
    class FixtureAdapter(Adapter):
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def execute_readonly(self, script: object) -> list[dict[str, object]]:
            return []

        def get_server_version(self) -> str:
            return "fixture"

        def health_check(self) -> bool:
            return True

    instance = Inventory.load(ROOT / "examples/inventory/demo.yaml").get("demo-sqlserver")
    with pytest.raises(ValueError):
        FixtureAdapter(instance, MockCredentialProvider({}))


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--inventory", str(ROOT / "examples/inventory/demo.yaml"), "--sql-root", str(ROOT / "sql")]
    assert main([*args, "validate"]) == 0
    assert "Validation OK" in capsys.readouterr().out
    assert main([*args, "list", "--type", "postgresql"]) == 0
    output = capsys.readouterr().out
    assert "demo-postgresql" in output and "demo-sqlserver" not in output
    assert main([*args, "show", "demo-oracle"]) == 0
    assert main([*args, "show", "missing"]) == 1
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("databases: [password: DO_NOT_PRINT", encoding="utf-8")
    assert main(["--inventory", str(invalid), "validate"]) == 1
    assert "DO_NOT_PRINT" not in capsys.readouterr().out
    for command in ["diagnose", "--execute", "--force"]:
        with pytest.raises(SystemExit) as exc:
            main([command])
        assert exc.value.code == 2
