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
from dba.disk_growth import (
    HISTORY_NOTE,
    _sqlserver_file_growth,
    build_disk_report,
    diagnose_disk_growth,
)
from dba.disk_growth_profiles import sql_profile_for_disk, validate_disk_profiles
from dba.disk_growth_reporter import render_console, render_markdown
from dba.inventory import ValidationError
from dba.registry import SqlScriptRegistry
from dba.security import check_sql
from dba.versioning import PROFILE_BINDINGS, PROFILE_RESOLVER, DetectedDatabaseVersion

FAMILY = {
    DatabaseType.SQLSERVER: "2022", DatabaseType.MYSQL: "8.4",
    DatabaseType.POSTGRESQL: "16", DatabaseType.ORACLE: "26ai",
}
RAW_VERSION = {
    DatabaseType.SQLSERVER: "16.0.1000.1", DatabaseType.MYSQL: "8.4.1",
    DatabaseType.POSTGRESQL: "16.1", DatabaseType.ORACLE: "23.0.0.0.0",
}


def resolved_disk_profile(db_type: DatabaseType, family: str | None = None,
                          raw: str | None = None, variant: str | None = None) -> Any:
    version_family = family or FAMILY[db_type]
    detected = DetectedDatabaseVersion(raw or RAW_VERSION[db_type], version_family, variant)
    return PROFILE_RESOLVER.resolve(db_type, version_family, detected, "disk-growth")


def report_with_default_profile(db_type: DatabaseType, collected: tuple[CollectorResult, ...],
                                version: str) -> Any:
    profile = resolved_disk_profile(db_type)
    contract = sql_profile_for_disk(profile.sql_profile_id or "")
    return build_disk_report(instance(db_type), collected, version,
                             required_collectors=contract.required,
                             optional_collectors=contract.optional,
                             profile_id=contract.profile_id)


def instance(db_type: DatabaseType) -> DatabaseInstance:
    return DatabaseInstance("demo", "Demo", db_type, "test.invalid", 1234, "monitor",
                            "test", environment="development")


def result(name: str, *rows: dict[str, Any], success: bool = True,
           truncated: bool = False) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult("demo", name, now, now, 1.0, rows, len(rows), truncated,
                           success, None if success else "QueryDenied", None)


@pytest.mark.parametrize("db_type", list(FAMILY))
def test_registry_dispatch_uses_reviewed_scripts(db_type: DatabaseType) -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    seen: list[str] = []

    class Adapter:
        def __init__(self) -> None:
            self.instance = instance(db_type)
            self.registry = registry
            self.disk_growth_profile = resolved_disk_profile(db_type)

        def get_server_version(self) -> str:
            return "test"

        def execute_readonly(self, script: Any) -> CollectorResult:
            registry.authorize(script, db_type)
            seen.append(script.metadata.name)
            return result(script.metadata.name)

    report = diagnose_disk_growth(Adapter())  # type: ignore[arg-type]
    assert seen == list(sql_profile_for_disk(resolved_disk_profile(db_type).sql_profile_id or "").ordered_scripts)
    assert report.scenario == "disk-growth"
    assert not report.details["incomplete"]
    assert report.details["history_note"] == HISTORY_NOTE


def test_disk_growth_profiles_bind_fixed_sql_roles_and_permissions() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_disk_profiles(registry, PROFILE_BINDINGS) == ()
    assert PROFILE_RESOLVER.validate() == ()
    for binding in PROFILE_BINDINGS:
        if binding.feature != "disk-growth" or binding.support_state.value != "SUPPORTED":
            continue
        contract = sql_profile_for_disk(binding.sql_profile_id)
        assert contract.required
        assert not set(contract.required) & set(contract.optional)
        assert binding.parser_contract_id == "disk_growth_normalized_v1"


@pytest.mark.parametrize(("db_type", "family", "raw", "variant", "expected"), [
    (DatabaseType.SQLSERVER, "2008r2", "10.50.6000.34", None, "disk_log_legacy"),
    (DatabaseType.SQLSERVER, "2022", "16.0.4305.5", None, "disk_log"),
    (DatabaseType.MYSQL, "5.7", "5.7.44", None, "disk_binlog_config_legacy"),
    (DatabaseType.MYSQL, "8.0", "8.0.21", "pre_8022", "disk_binlog_config"),
    (DatabaseType.MYSQL, "8.0", "8.0.22", "8022_plus", "disk_binlog_config"),
    (DatabaseType.POSTGRESQL, "16", "16.3", None, "disk_slots"),
    (DatabaseType.POSTGRESQL, "17", "17.1", None, "disk_slots_17_plus"),
    (DatabaseType.ORACLE, "11gr2", "11.2.0.4", None, "disk_tablespaces"),
    (DatabaseType.ORACLE, "12c", "12.1.0.2", "12_1", "disk_tablespaces"),
])
def test_version_families_select_disk_sql_profile(db_type: DatabaseType, family: str,
                                                  raw: str, variant: str | None,
                                                  expected: str) -> None:
    profile = resolved_disk_profile(db_type, family, raw, variant)
    assert profile.version_status.value == "VERIFIED"
    assert expected in sql_profile_for_disk(profile.sql_profile_id or "").ordered_scripts


def test_disk_growth_mismatch_and_unknown_fail_closed() -> None:
    mismatch = PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, "8.0", DetectedDatabaseVersion("8.4.1", "8.4"), "disk-growth")
    assert mismatch.version_status.value == "MISMATCH"
    assert mismatch.sql_profile_id is None
    detected_only = PROFILE_RESOLVER.resolve(
        DatabaseType.POSTGRESQL, None, DetectedDatabaseVersion("16.3", "16"), "disk-growth")
    assert detected_only.version_status.value == "AUTO_DETECTED"
    assert detected_only.sql_profile_id == "postgresql_disk_13_16"


def test_sqlserver_log_reuse_and_transaction() -> None:
    rows = (result("disk_files", {"file_name": "data", "file_type": "ROWS",
                                        "size_bytes": 100, "used_bytes": 90, "max_size": 0}),
            result("disk_log", {"log_used_percent": 91,
                                "log_reuse_wait_desc": "ACTIVE_TRANSACTION"}),
            result("transactions", {"transaction_id": 7, "transaction_age_seconds": 4000}))
    report = report_with_default_profile(DatabaseType.SQLSERVER, rows, "16")
    assert {item.title for item in report.findings} >= {
        "SQLSERVER_LOG_HIGH_USAGE", "SQLSERVER_LOG_REUSE_BLOCKED",
        "SQLSERVER_OLD_ACTIVE_TRANSACTION", "SQLSERVER_DATAFILE_NEAR_LIMIT"}
    assert report.details["next_diagnostic_step"] == "transaction-log"
    assert all(item.evidence["source"] for item in report.findings)


def test_sqlserver_growth_limits_distinguish_percent_fixed_and_unlimited() -> None:
    percent = _sqlserver_file_growth({"growth": 10, "is_percent_growth": True, "max_size": -1})
    assert percent == {"growth_mode": "PERCENT", "growth_increment": 10.0,
                       "growth_increment_unit": "percent", "max_size_state": "UNLIMITED",
                       "max_size_bytes": None}
    fixed = _sqlserver_file_growth({"growth": 128, "is_percent_growth": False, "max_size": 1024})
    assert fixed["growth_mode"] == "FIXED_PAGES"
    assert fixed["growth_increment"] == 128 * 8192
    assert fixed["max_size_bytes"] == 1024 * 8192


def test_postgresql_slot_archiver_and_unknown_wal_size() -> None:
    rows = (result("disk_slots", {"slot_name": "etl", "slot_type": "logical",
                                        "active": False, "retained_wal_estimate_bytes": 4096}),
            result("disk_archiver", {"failed_count": 3, "last_failed_time": "2026-01-02",
                                           "last_archived_time": "2026-01-01"}),
            result("disk_database", {"size_bytes": 9000, "temp_bytes": 100,
                                           "temp_files": 1, "stats_reset": "2026-01-01"}))
    report = report_with_default_profile(DatabaseType.POSTGRESQL, rows, "16")
    assert {item.title for item in report.findings} == {"PG_WAL_RETENTION_SLOT", "PG_ARCHIVE_FAILURE"}
    assert report.details["other"]["wal_filesystem"].startswith("pg_wal filesystem size unavailable")
    assert report.details["next_diagnostic_step"] == "wal-growth"
    assert "PG_TEMP_GROWTH" not in {item.title for item in report.findings}


def test_mysql_binlog_total_requires_complete_file_listing() -> None:
    large = 11 * 1024**3
    rows = (result("disk_binlogs", {"log_name": "bin.1", "file_size": large}),
            result("disk_schemas", {"schema_name": "app", "data_bytes": 50,
                                    "index_bytes": 10}))
    report = report_with_default_profile(DatabaseType.MYSQL, rows, "8.0")
    assert report.details["log"]["binlog_total_bytes"] == large
    assert report.details["next_diagnostic_step"] == "mysql-binlog"
    assert any(item.title == "BINLOG_TOTAL_SIZE_HIGH" for item in report.findings)
    truncated = report_with_default_profile(DatabaseType.MYSQL, (
        result("disk_schemas", {"schema_name": "app", "data_bytes": 50, "index_bytes": 10}),
        result("disk_binlog_config", {"binary_log_enabled": True}),
        result("disk_binlogs", {"file_size": large}, truncated=True),
    ), "8.0")
    assert "binlog_total_bytes" not in truncated.details["log"]
    assert not truncated.details["incomplete"]
    assert truncated.details["data_visibility"] == "PARTIAL"


def test_oracle_fra_tablespace_and_report_redaction() -> None:
    rows = (result("disk_fra", {"space_limit": 100, "space_used": 90,
                                     "space_reclaimable": 5, "secret": 'DBA-Test-Secret-987654'}),
            result("disk_tablespaces", {"tablespace_name": "APP", "contents": "PERMANENT",
                                             "used_percent": 91}))
    report = report_with_default_profile(DatabaseType.ORACLE, rows, "23")
    assert {item.title for item in report.findings} == {
        "ORACLE_FRA_HIGH_USAGE", "ORACLE_TABLESPACE_HIGH_USAGE"}
    assert report.details["next_diagnostic_step"] == "fra-archive"
    assert "hidden" not in json.dumps(report.details)
    assert "hidden" not in render_console(report)
    assert "hidden" not in render_markdown(report)


def test_oracle_archive_volume_and_sqlserver_tempdb_findings() -> None:
    oracle = report_with_default_profile(DatabaseType.ORACLE,
        (result("disk_archive_volume", {"archived_bytes_24h": 11 * 1024**3,
                                          "redo_switches_24h": 60}),), "23")
    assert "ORACLE_ARCHIVE_ACCUMULATION" in {item.title for item in oracle.findings}
    sqlserver = report_with_default_profile(DatabaseType.SQLSERVER,
        (result("disk_temp_usage", {"file_id": 1, "total_pages": 100,
                                      "free_pages": 10}),), "16")
    temp = next(item for item in sqlserver.findings
                if item.title == "SQLSERVER_TEMPDB_HIGH_USAGE")
    assert temp.evidence["used_percent"] == 90


def test_missing_source_stops_after_failure() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    calls: list[str] = []
    db_type = DatabaseType.POSTGRESQL

    def execute(script: Any) -> CollectorResult:
        calls.append(script.metadata.name)
        return result(script.metadata.name, success=len(calls) != 1)

    adapter = SimpleNamespace(instance=instance(db_type), registry=registry,
                              disk_growth_profile=resolved_disk_profile(db_type),
                              get_server_version=lambda: "16", execute_readonly=execute)
    report = diagnose_disk_growth(adapter)  # type: ignore[arg-type]
    assert len(calls) == 1
    assert report.details["incomplete"]
    assert calls[-1] in report.details["unavailable"]
    assert len(report.details["unavailable"]) >= 1
    assert not report.findings


def test_optional_collector_failure_keeps_core_diagnosis_complete() -> None:
    rows = (result("disk_database", {"database_name": "db", "size_bytes": 100}),
            result("disk_slots", success=False))
    report = report_with_default_profile(DatabaseType.POSTGRESQL, rows, "16")
    assert not report.details["incomplete"]
    assert report.details["data_visibility"] == "PARTIAL"
    assert "disk_slots" in report.details["optional_unavailable"]


def test_mysql_binary_logging_disabled_is_not_applicable() -> None:
    contract = sql_profile_for_disk(resolved_disk_profile(DatabaseType.MYSQL).sql_profile_id or "")
    observed = tuple(result(name, {"binary_log_enabled": False} if name == "disk_binlog_config" else {})
                     for name in contract.required + tuple(n for n in contract.optional if n != "disk_binlogs"))
    report = report_with_default_profile(DatabaseType.MYSQL, observed, "8.4")
    assert not report.details["incomplete"]
    assert report.details["not_applicable"] == ["disk_binlogs"]


def test_mysql57_binary_logging_disabled_is_not_applicable() -> None:
    contract = sql_profile_for_disk("mysql_disk_57")
    observed = (result("disk_schemas", {"schema_name": "app", "data_bytes": 100}),
                result("disk_binlog_config_legacy", {"binary_log_enabled": False}))
    report = build_disk_report(instance(DatabaseType.MYSQL), observed, "5.7.44",
                               required_collectors=contract.required,
                               optional_collectors=contract.optional,
                               profile_id="mysql_disk_57")
    assert not report.details["incomplete"]
    assert report.details["not_applicable"] == ["disk_binlogs"]
    assert report.details["data_visibility"] == "PARTIAL"
    assert report.details["log"]["binlog_status"] == "Not configured"


def test_mysql_binlog_configuration_failure_does_not_invalidate_schema_totals() -> None:
    contract = sql_profile_for_disk("mysql_disk_84")
    observed = (result("disk_schemas", {"schema_name": "app", "data_bytes": 100}),
                result("disk_binlog_config", {}, success=False))
    report = build_disk_report(instance(DatabaseType.MYSQL), observed, "8.4.11",
                               required_collectors=contract.required,
                               optional_collectors=contract.optional,
                               profile_id="mysql_disk_84")
    assert not report.details["incomplete"]
    assert "disk_binlog_config" in report.details["optional_unavailable"]
    assert report.details["data_visibility"] == "PARTIAL"


def test_postgresql_no_slots_and_archive_off_are_normal_states() -> None:
    rows = (result("disk_database", {"size_bytes": 100}), result("disk_slots"),
            result("disk_archiver", {"archive_mode": "off", "failed_count": 0}),
            result("disk_replicas"))
    report = report_with_default_profile(DatabaseType.POSTGRESQL, rows, "16")
    assert report.details["other"]["slot_status"] == "NOT_CONFIGURED"
    assert report.details["other"]["archive_status"] == "NOT_CONFIGURED"
    assert not report.details["incomplete"]


def test_binary_log_allowlist_is_exact() -> None:
    check_sql("SHOW BINARY LOGS")
    for unsafe in ("SHOW BINARY LOGS; DELETE FROM x", "SHOW BINLOG EVENTS",
                   "SHOW BINARY LOGS -- suffix"):
        with pytest.raises(ValidationError):
            check_sql(unsafe)
    check_sql("DBCC SQLPERF(LOGSPACE)")
    with pytest.raises(ValidationError):
        check_sql("DBCC SQLPERF(LOGSPACE); DBCC CHECKDB")


def test_cli_oracle_disk_growth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    oracle_instance, _, _, driver = oracle_fixture()
    driver.connection.data["preflight_object_grants"].extend(
        {"owner": "SYS", "table_name": name, "privilege": "SELECT", "grantable": "NO"}
        for name in ("DBA_TABLESPACE_USAGE_METRICS", "DBA_TABLESPACES",
                     "DBA_DATA_FILES", "DBA_TEMP_FREE_SPACE")
    )
    registry = SqlScriptRegistry(ROOT / "sql")
    profile = resolved_disk_profile(DatabaseType.ORACLE, "19c", "19.0.0.0.0")
    driver.connection.sql_names.update({registry.get(name, DatabaseType.ORACLE).text: name
                                        for name in sql_profile_for_disk(profile.sql_profile_id or "").ordered_scripts})

    class Provider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            return Credential("MONITOR", "TOPSECRET")

    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [
        {**vars(oracle_instance), "db_type": "oracle", "version_family": "19c"}]}), encoding="utf-8")
    report_path = tmp_path / "disk.html"
    args = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", oracle_instance.id, "disk-growth", "--format", "json",
            "--report", str(report_path)]
    assert main(args, provider=Provider(), driver=driver) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["scenario"] == "disk-growth"
    assert "Diagnostic Context" in report_path.read_text(encoding="utf-8")
    assert "TOPSECRET" not in report_path.read_text(encoding="utf-8")
