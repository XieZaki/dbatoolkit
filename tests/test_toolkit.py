import gc
import importlib
import inspect
import io
import logging
import shutil
import weakref
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
from dba.registry import (
    DEFAULT_MAX_RESULT_BYTES,
    MAX_METADATA_SOURCE_BYTES,
    MAX_SQL_ROWS,
    MAX_SQL_SOURCE_BYTES,
    MAX_SQL_TIMEOUT_SECONDS,
    SqlScriptRegistry,
    fetch_bounded_rows,
)
from dba.reporters import ConsoleReporter, MarkdownReporter
from dba.security import check_sql

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("module_name", "database_type", "script_name"), (
    ("test_sqlserver", DatabaseType.SQLSERVER, "monitor_capabilities"),
    ("test_mysql", DatabaseType.MYSQL, "preflight"),
    ("test_postgresql", DatabaseType.POSTGRESQL, "preflight"),
))
def test_fake_driver_registry_keys_preserve_crlf_checkout_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    module_name: str, database_type: DatabaseType, script_name: str,
) -> None:
    database_root = tmp_path / "sql" / database_type.value
    shutil.copytree(ROOT / "sql" / database_type.value, database_root)
    for path in database_root.glob("*.sql"):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(content.replace(b"\n", b"\r\n"))

    fixture_module = importlib.import_module(module_name)
    monkeypatch.setattr(fixture_module, "ROOT", tmp_path)
    driver = fixture_module.FakeDriver()
    script = SqlScriptRegistry(tmp_path / "sql").get(script_name, database_type)

    assert driver.connection.scripts[script.text] == script_name


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
        ("timeout_seconds", MAX_SQL_TIMEOUT_SECONDS + 1),
        ("max_rows", MAX_SQL_ROWS + 1),
        ("max_result_bytes", DEFAULT_MAX_RESULT_BYTES + 1),
        ("max_result_bytes", 0),
    ],
)
def test_registry_rejects_unbounded_metadata(
    script: Path, tmp_path: Path, key: str, value: int,
) -> None:
    metadata = script.with_suffix(".yaml")
    data = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    data[key] = value
    metadata.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        SqlScriptRegistry(tmp_path).load(script)


@pytest.mark.parametrize("source_limit", ("sql", "metadata"))
def test_registry_rejects_oversized_source(
    script: Path, tmp_path: Path, source_limit: str,
) -> None:
    path = script if source_limit == "sql" else script.with_suffix(".yaml")
    limit = MAX_SQL_SOURCE_BYTES if source_limit == "sql" else MAX_METADATA_SOURCE_BYTES
    path.write_bytes(b"x" * (limit + 1))
    with pytest.raises(ValidationError, match="size limit"):
        SqlScriptRegistry(tmp_path).load(script)


def test_registry_rejects_duplicate_metadata_keys(script: Path, tmp_path: Path) -> None:
    script.with_suffix(".yaml").write_text(
        "name: probe\nname: overridden\ndatabase_type: sqlserver\nrisk: READ_ONLY\n"
        "timeout_seconds: 5\nmax_rows: 1\nreviewed: true\ndescription: fixture\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Duplicate YAML key"):
        SqlScriptRegistry(tmp_path).load(script)


def test_registry_revalidates_metadata_digest_without_retaining_probe_scripts(
    script: Path, tmp_path: Path,
) -> None:
    registry = SqlScriptRegistry(tmp_path)
    registered = registry.load(script)
    registry.authorize(registered, DatabaseType.SQLSERVER)
    assert len(registry._issued) == 1

    metadata = script.with_suffix(".yaml")
    data = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    data["description"] = "changed after registration"
    metadata.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="changed after registration"):
        registry.authorize(registered, DatabaseType.SQLSERVER)
    assert len(registry._issued) == 1


def test_registry_releases_issued_script_capabilities(script: Path, tmp_path: Path) -> None:
    registry = SqlScriptRegistry(tmp_path)
    issued = registry.load(script)
    reference = weakref.ref(issued)
    assert len(registry._issued) == 1
    del issued
    gc.collect()
    assert reference() is None
    assert registry._issued == {}


def test_fetch_bounded_rows_stops_at_row_and_byte_budgets() -> None:
    class Cursor:
        def __init__(self, rows: list[tuple[str, ...]]) -> None:
            self.rows = rows

        def fetchmany(self, size: int) -> list[tuple[str, ...]]:
            assert size == 1
            if not self.rows:
                return []
            return [self.rows.pop(0)]

    exact = fetch_bounded_rows(Cursor([("ab",), ("c",)]), 3, 3)
    assert exact.rows == (("ab",), ("c",))
    assert exact.result_bytes == 3 and not exact.truncated

    by_bytes = fetch_bounded_rows(Cursor([("ab",), ("cd",)]), 3, 3)
    assert by_bytes.rows == (("ab",),)
    assert by_bytes.result_bytes == 2 and by_bytes.byte_limit_reached and by_bytes.truncated

    by_rows = fetch_bounded_rows(Cursor([("a",), ("b",)]), 1, 8)
    assert by_rows.rows == (("a",),) and by_rows.row_limit_reached and by_rows.truncated


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
        "SELECT id FROM t FOR KEY SHARE",
        "SELECT id FROM t FOR/*comment*/KEY SHARE",
        "SELECT id FROM t FOR/*comment*/SHARE",
        "SELECT id FROM t LOCK IN SHARE MODE",
        "SELECT id FROM t LOCK/*comment*/IN SHARE MODE",
        "SELECT id FROM t WITH (UPDLOCK)",
        "SELECT id FROM t WITH (HOLDLOCK)",
        "SELECT id FROM t WITH (TABLOCKX)",
        "SELECT NEXT VALUE FOR dbo.sequence_name",
        "SELECT NEXT/*comment*/VALUE FOR dbo.sequence_name",
        "SELECT set_config('default_transaction_read_only', 'off', false)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT pg_advisory_lock(12)",
        "SELECT pg_try_advisory_xact_lock(12)",
        "SELECT GET_LOCK('dba-toolkit', 1)",
        "SELECT RELEASE_LOCK('dba-toolkit')",
        "SELECT RELEASE_ALL_LOCKS()",
        "SELECT 1; EXEC x",
        "SELECT 1; SELECT 2",
        "SELECT 1; -- trailing comment\nSELECT 2",
        "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
        "SELECT nextval('seq')",
        "SELECT * FROM t FOR SHARE",
        "SELECT 1; DROP TABLE x",
    ],
)
def test_dangerous_sql(sql: str) -> None:
    with pytest.raises(ValidationError):
        check_sql(sql)


def test_single_statement_check_ignores_quoted_and_trailing_comment_semicolons() -> None:
    check_sql("SELECT 'first; second'")
    check_sql("SELECT $$first; second$$")
    check_sql("SELECT 1; -- a trailing comment; is not a second statement")


def test_sqlserver_temp_table_hash_does_not_start_a_comment() -> None:
    check_sql("SELECT * FROM #temp", DatabaseType.SQLSERVER)
    with pytest.raises(ValidationError, match="one SQL statement"):
        check_sql("SELECT * FROM #temp; SELECT 2", DatabaseType.SQLSERVER)
    with pytest.raises(ValidationError, match="one SQL statement"):
        check_sql("SELECT 1 # 2; SELECT 3", DatabaseType.POSTGRESQL)


@pytest.mark.parametrize("database_type", (DatabaseType.MYSQL, None))
def test_hash_comment_remains_a_comment_for_mysql(
    database_type: DatabaseType | None,
) -> None:
    check_sql("SELECT 1 # trailing comment; not a second statement", database_type)


def test_carriage_return_ends_line_comment_for_statement_check() -> None:
    with pytest.raises(ValidationError, match="one SQL statement"):
        check_sql("SELECT 1; -- trailing comment\rSELECT 2")


def test_mysql_double_dash_requires_whitespace_before_comment() -> None:
    with pytest.raises(ValidationError, match="one SQL statement"):
        check_sql("SELECT 1--x; SELECT 2", DatabaseType.MYSQL)
    check_sql("SELECT 1-- comment; trailing comment", DatabaseType.MYSQL)


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
    assert "校验通过" in capsys.readouterr().out
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
