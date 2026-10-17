"""Evidence-based disk diagnosis; ordinary tests never connect to a database."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from test_oracle import ROOT
from test_oracle import fixture as oracle_fixture

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential, CredentialProvider
from dba.disk_growth import HISTORY_NOTE, SCRIPTS, build_disk_report, diagnose_disk_growth
from dba.disk_growth_reporter import render_console, render_markdown
from dba.inventory import ValidationError
from dba.registry import SqlScriptRegistry
from dba.security import check_sql


def instance(db_type: DatabaseType) -> DatabaseInstance:
    return DatabaseInstance("demo", "Demo", db_type, "test.invalid", 1234, "monitor",
                            "test", environment="development")


def result(name: str, *rows: dict[str, Any], success: bool = True,
           truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("demo", name, now, now, 1.0, rows, len(rows), truncated,
                           success, None if success else "QueryDenied", None)


@pytest.mark.parametrize("db_type", list(SCRIPTS))
def test_registry_dispatch_uses_reviewed_scripts(db_type: DatabaseType) -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    seen: list[str] = []

    class Adapter:
        def __init__(self) -> None:
            self.instance = instance(db_type)
            self.registry = registry

        def get_server_version(self) -> str:
            return "test"

        def execute_readonly(self, script: Any) -> CollectorResult:
            registry.authorize(script, db_type)
            seen.append(script.metadata.name)
            return result(script.metadata.name)

    report = diagnose_disk_growth(Adapter())  # type: ignore[arg-type]
    assert seen == list(SCRIPTS[db_type])
    assert report.scenario == "disk-growth"
    assert not report.details["incomplete"]
    assert report.details["history_note"] == HISTORY_NOTE


def test_sqlserver_log_reuse_and_transaction() -> None:
    rows = (result("disk_files", {"file_name": "data", "file_type": "ROWS",
                                        "size_bytes": 100, "used_bytes": 90, "max_size": 0}),
            result("disk_log", {"log_used_percent": 91,
                                "log_reuse_wait_desc": "ACTIVE_TRANSACTION"}),
            result("transactions", {"transaction_id": 7, "transaction_age_seconds": 4000}))
    report = build_disk_report(instance(DatabaseType.SQLSERVER), rows, "16")
    assert {item.title for item in report.findings} >= {
        "SQLSERVER_LOG_HIGH_USAGE", "SQLSERVER_LOG_REUSE_BLOCKED",
        "SQLSERVER_OLD_ACTIVE_TRANSACTION", "SQLSERVER_DATAFILE_NEAR_LIMIT"}
    assert report.details["next_diagnostic_step"] == "transaction-log"
    assert all(item.evidence["source"] for item in report.findings)


def test_postgresql_slot_archiver_and_unknown_wal_size() -> None:
    rows = (result("disk_slots", {"slot_name": "etl", "slot_type": "logical",
                                        "active": False, "retained_wal_estimate_bytes": 4096}),
            result("disk_archiver", {"failed_count": 3, "last_failed_time": "2026-01-02",
                                           "last_archived_time": "2026-01-01"}),
            result("disk_database", {"size_bytes": 9000, "temp_bytes": 100,
                                           "temp_files": 1, "stats_reset": "2026-01-01"}))
    report = build_disk_report(instance(DatabaseType.POSTGRESQL), rows, "16")
    assert {item.title for item in report.findings} == {"PG_WAL_RETENTION_SLOT", "PG_ARCHIVE_FAILURE"}
    assert report.details["other"]["wal_filesystem"].startswith("pg_wal filesystem size unavailable")
    assert report.details["next_diagnostic_step"] == "wal-growth"
    assert "PG_TEMP_GROWTH" not in {item.title for item in report.findings}


def test_mysql_binlog_total_requires_complete_file_listing() -> None:
    large = 11 * 1024**3
    rows = (result("disk_binlogs", {"log_name": "bin.1", "file_size": large}),
            result("disk_schemas", {"schema_name": "app", "data_bytes": 50,
                                    "index_bytes": 10}))
    report = build_disk_report(instance(DatabaseType.MYSQL), rows, "8.0")
    assert report.details["log"]["binlog_total_bytes"] == large
    assert report.details["next_diagnostic_step"] == "mysql-binlog"
    assert any(item.title == "BINLOG_TOTAL_SIZE_HIGH" for item in report.findings)
    truncated = build_disk_report(instance(DatabaseType.MYSQL),
                                  (result("disk_binlogs", {"file_size": large}, truncated=True),), "8.0")
    assert "binlog_total_bytes" not in truncated.details["log"]
    assert truncated.details["incomplete"]


def test_oracle_fra_tablespace_and_report_redaction() -> None:
    rows = (result("disk_fra", {"space_limit": 100, "space_used": 90,
                                     "space_reclaimable": 5, "secret": 'DBA-Test-Secret-987654'}),
            result("disk_tablespaces", {"tablespace_name": "APP", "contents": "PERMANENT",
                                             "used_percent": 91}))
    report = build_disk_report(instance(DatabaseType.ORACLE), rows, "23")
    assert {item.title for item in report.findings} == {
        "ORACLE_FRA_HIGH_USAGE", "ORACLE_TABLESPACE_HIGH_USAGE"}
    assert report.details["next_diagnostic_step"] == "fra-archive"
    assert "hidden" not in json.dumps(report.details)
    assert "hidden" not in render_console(report)
    assert "hidden" not in render_markdown(report)


def test_oracle_archive_volume_and_sqlserver_tempdb_findings() -> None:
    oracle = build_disk_report(instance(DatabaseType.ORACLE),
                               (result("disk_archive_volume", {
                                   "archived_bytes_24h": 11 * 1024**3,
                                   "redo_switches_24h": 60}),), "23")
    assert "ORACLE_ARCHIVE_ACCUMULATION" in {item.title for item in oracle.findings}
    sqlserver = build_disk_report(instance(DatabaseType.SQLSERVER),
                                  (result("disk_temp_usage", {"file_id": 1,
                                      "total_pages": 100, "free_pages": 10}),), "16")
    temp = next(item for item in sqlserver.findings
                if item.title == "SQLSERVER_TEMPDB_HIGH_USAGE")
    assert temp.evidence["used_percent"] == 90


def test_missing_source_stops_after_failure() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    calls: list[str] = []
    db_type = DatabaseType.POSTGRESQL

    def execute(script: Any) -> CollectorResult:
        calls.append(script.metadata.name)
        return result(script.metadata.name, success=len(calls) != 2)

    adapter = SimpleNamespace(instance=instance(db_type), registry=registry,
                              get_server_version=lambda: "16", execute_readonly=execute)
    report = diagnose_disk_growth(adapter)  # type: ignore[arg-type]
    assert len(calls) == 2
    assert report.details["incomplete"]
    assert calls[-1] in report.details["unavailable"]
    assert len(report.details["unavailable"]) == len(SCRIPTS[db_type]) - 1
    assert not report.findings


def test_binary_log_allowlist_is_exact() -> None:
    check_sql("SHOW BINARY LOGS")
    for unsafe in ("SHOW BINARY LOGS; DELETE FROM x", "SHOW BINLOG EVENTS",
                   "SHOW BINARY LOGS -- suffix"):
        with pytest.raises(ValidationError):
            check_sql(unsafe)


def test_cli_oracle_disk_growth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    oracle_instance, _, _, driver = oracle_fixture()
    registry = SqlScriptRegistry(ROOT / "sql")
    driver.connection.sql_names.update({registry.get(name, DatabaseType.ORACLE).text: name
                                        for name in SCRIPTS[DatabaseType.ORACLE]})

    class Provider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            return Credential("MONITOR", "TOPSECRET")

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [
        {**vars(oracle_instance), "db_type": "oracle"}]}), encoding="utf-8")
    report_path = tmp_path / "disk.html"
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", oracle_instance.id, "disk-growth", "--format", "json",
            "--report", str(report_path)]
    assert main(args, provider=Provider(), driver=driver) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["scenario"] == "disk-growth"
    assert "Diagnostic Context" in report_path.read_text(encoding="utf-8")
    assert "TOPSECRET" not in report_path.read_text(encoding="utf-8")
