import json
from pathlib import Path

import pytest
import yaml

from dba.cli import main
from dba.core import DatabaseType
from dba.credentials import Credential, MockCredentialProvider
from dba.database_errors import normalize_oracle_error
from dba.errors import ToolkitError
from dba.permissions import (
    ORACLE_COMMON_MONITOR_REQUIREMENTS,
    REGISTRY,
    VERSION_DETECTION_PRIVILEGES,
    PermissionStatus,
    render_profile_sql,
    render_sql,
    requirements_for,
    requirements_for_profile,
    scan_registry_objects,
    status_for,
)
from dba.versioning import PROFILE_RESOLVER, DetectedDatabaseVersion, VersionStatus

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("database_type", [DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                              DatabaseType.MYSQL, DatabaseType.ORACLE])
def test_permission_registry_covers_supported_database_types(database_type: DatabaseType) -> None:
    assert "slow" in REGISTRY[database_type]
    assert "disk-growth" in REGISTRY[database_type]
    assert "replication" in REGISTRY[database_type]
    assert ("plan" in REGISTRY[database_type]) is (database_type == DatabaseType.SQLSERVER)


def test_status_semantics() -> None:
    definition = REGISTRY[DatabaseType.MYSQL]["disk-growth"]
    assert status_for(definition, None)[0] == PermissionStatus.UNKNOWN
    assert status_for(definition, ())[0] == PermissionStatus.MISSING
    assert status_for(definition, (
        "GRANT PROCESS ON *.* TO 'monitor'@'%';",
        "GRANT REPLICATION CLIENT ON *.* TO 'monitor'@'%';",
    ))[0] == PermissionStatus.MISSING
    assert status_for(definition, (
        "GRANT PROCESS ON *.* TO 'monitor'@'%';",
        "GRANT REPLICATION CLIENT ON *.* TO 'monitor'@'%';",
        "GRANT SELECT ON performance_schema.* TO 'monitor'@'%';",
    ))[0] == PermissionStatus.READY


def test_sqlserver_version_specific_requirements() -> None:
    old = requirements_for(DatabaseType.SQLSERVER, "slow", "15.0.2000")
    new = requirements_for(DatabaseType.SQLSERVER, "slow", "16.0.1000")
    assert "VIEW SERVER STATE" in old.required
    assert "VIEW SERVER PERFORMANCE STATE" not in old.required
    assert "VIEW SERVER PERFORMANCE STATE" in new.required
    assert "VIEW SERVER STATE" not in new.required
    assert "sysadmin" not in render_sql(DatabaseType.SQLSERVER, ("slow",), "monitor", version="16.0")
    assert "CONTROL SERVER" not in render_sql(DatabaseType.SQLSERVER, ("slow",), "monitor", version="16.0")


def test_postgres_mysql_oracle_minimum_grants() -> None:
    pg = render_sql(DatabaseType.POSTGRESQL, ("slow",), "dbamon", "orders")
    assert "GRANT pg_monitor TO \"dbamon\"" in pg
    assert 'GRANT CONNECT ON DATABASE "orders"' in pg
    mysql = render_sql(DatabaseType.MYSQL, ("disk-growth",), "dbamon")
    assert "GRANT PROCESS ON *.*" in mysql
    assert "GRANT REPLICATION CLIENT ON *.*" in mysql
    assert "SUPER" not in mysql and "SELECT ON *.*" not in mysql
    oracle = render_sql(DatabaseType.ORACLE, ("slow",), "DBA_MONITOR")
    assert "GRANT SELECT ON SYS.V_$SESSION TO DBA_MONITOR" in oracle
    assert "SELECT_CATALOG_ROLE" not in oracle and "SYSDBA" not in oracle


def test_grant_revoke_deduplicate_and_identifier_escape() -> None:
    grant = render_sql(DatabaseType.SQLSERVER, ("slow", "disk-growth"), "a]b", version="15.0")
    revoke = render_sql(DatabaseType.SQLSERVER, ("slow", "disk-growth"), "a]b", version="15.0", revoke=True)
    assert grant.count("GRANT VIEW SERVER STATE") == 1
    assert "TO [a]]b]" in grant
    assert "REVOKE VIEW SERVER STATE" in revoke
    assert "DBA Toolkit does NOT execute" in grant
    mysql = render_sql(DatabaseType.MYSQL, ("slow",), "a'b")
    assert "'a''b'@'<approved_monitor_client_host>'" in mysql
    with pytest.raises(ValueError):
        render_sql(DatabaseType.ORACLE, ("slow",), "x; GRANT DBA TO x")


def test_all_feature_union_deduplicates_privileges() -> None:
    sql = render_sql(DatabaseType.SQLSERVER, ("slow", "plan", "disk-growth"), "monitor", version="16.0")
    assert sql.count("GRANT VIEW SERVER PERFORMANCE STATE") == 1
    assert sql.count("GRANT VIEW ANY DATABASE") == 1


def test_permission_cli_has_no_automatic_execution_switch(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "pg", "db_type": "postgresql", "host": "localhost",
        "port": 5432, "database": "orders", "credential_alias": "mon", "enabled": True,
    }]}), encoding="utf-8")
    provider = MockCredentialProvider({"mon": Credential("dba_monitor", "not-output")})
    with pytest.raises(SystemExit):
        main(["--inventory", str(inventory), "permissions", "pg", "--auto-fix"], provider=provider)


def test_registry_sql_scanner_stays_aligned() -> None:
    scanned = scan_registry_objects(ROOT / "sql")
    for database_type, objects in scanned.items():
        declared = {item.lower() for definition in REGISTRY[database_type].values()
                    for item in definition.objects}
        declared.update(item.removeprefix("SELECT ").lower() for item in
                        VERSION_DETECTION_PRIVILEGES.get(database_type, ()))
        assert objects <= declared
    assert "show binary logs" in scanned[DatabaseType.MYSQL]


def test_permission_registry_preflight_alignment() -> None:
    sqlserver = (ROOT / "sql/sqlserver/monitor_capabilities.sql").read_text(encoding="utf-8").lower()
    assert "view server state" in sqlserver
    assert "view server performance state" in sqlserver
    assert "view any database" in sqlserver
    for item in ("securityadmin", "serveradmin", "db_owner", "owner_sid"):
        assert item in sqlserver
    permission_sql = (ROOT / "sql/sqlserver/monitor_permissions.sql").read_text(encoding="utf-8").lower()
    for item in ("sys.server_principals", "sys.database_principals", "sys.login_token",
                 "sys.user_token", "source_type", "inherited"):
        assert item in permission_sql
    postgres = (ROOT / "sql/postgresql/preflight.sql").read_text(encoding="utf-8").lower()
    assert "read_all_stats" in postgres and "read_all_settings" in postgres
    mysql = (ROOT / "sql/mysql/preflight.sql").read_text(encoding="utf-8").lower()
    assert "replication client" in mysql
    oracle = (ROOT / "dba/oracle.py").read_text(encoding="utf-8")
    for view in ("V_$INSTANCE", "V_$DATABASE", "V_$SESSION", "V_$SQL", "V_$TRANSACTION"):
        assert view in oracle
        assert f"SELECT SYS.{view}" in REGISTRY[DatabaseType.ORACLE]["slow"].required
    assert VERSION_DETECTION_PRIVILEGES[DatabaseType.ORACLE] == ("SELECT SYS.V_$VERSION",)
    assert "version_detection_view" in oracle


def test_generate_permissions_cli_is_offline_text_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "pg", "db_type": "postgresql", "host": "localhost",
        "port": 5432, "database": "orders", "credential_alias": "mon", "enabled": True,
    }]}), encoding="utf-8")
    provider = MockCredentialProvider({"mon": Credential("dba_monitor", "not-output")})
    assert main(["--inventory", str(inventory), "permissions", "pg", "--feature", "slow",
                 "--generate-grant", "--json"], provider=provider) == 0
    raw = capsys.readouterr().out
    output = json.loads(raw)
    assert "not-output" not in raw
    assert output["toolkit_executes_grants"] is False
    assert output["features"][0]["status"] == "UNKNOWN"
    assert "GRANT pg_monitor" in output["generated_sql"]
    assert "REVOKE" not in output["generated_sql"]


@pytest.mark.parametrize("declared_family", ["26ai", None])
def test_oracle_replication_permissions_render_static_guidance_when_preflight_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    declared_family: str | None,
) -> None:
    inventory = tmp_path / "inventory.yaml"
    database: dict[str, object] = {
        "id": "oracle", "name": "Oracle test", "db_type": "oracle",
        "host": "127.0.0.1", "port": 1521, "service_name": "FREEPDB1",
        "credential_alias": "oracle-monitor", "enabled": True,
    }
    if declared_family is not None:
        database["version_family"] = declared_family
    inventory.write_text(yaml.safe_dump({"databases": [database]}), encoding="utf-8")

    class FailedPreflightAdapter:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> object:
            raise ToolkitError(
                "PreflightFailed", "Oracle monitor identity or version unavailable.",
                database_error={"database_type": "oracle", "driver": "python-oracledb",
                                "vendor_code": "ORA-00942", "sqlstate": None,
                                "message": 'ORA-00942: table or view "SYS"."V_$INSTANCE" does not exist',
                                "classification": "MONITORING_OBJECT_UNAVAILABLE",
                                "guidance": "Verify view existence and direct SELECT grant."},
            )

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("dba.cli.OracleAdapter", FailedPreflightAdapter)
    provider = MockCredentialProvider({"oracle-monitor": Credential("DBA_MONITOR", "never-output")})
    assert main(["--inventory", str(inventory), "permissions", "oracle", "--feature",
                 "replication", "--json"], provider=provider, driver=object()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["success"] is True
    assert output["features"][0]["status"] == "UNKNOWN"
    assert output["runtime_verification"]["status"] == "FAILED"
    assert output["runtime_verification"]["vendor_error"]["vendor_code"] == "ORA-00942"
    assert output["runtime_verification"]["vendor_error"]["classification"] == "MONITORING_OBJECT_UNAVAILABLE"
    assert "never-output" not in json.dumps(output)
    if declared_family == "26ai":
        row = output["features"][0]
        assert output["version_profile"]["version_status"] == "UNVERIFIED"
        assert "SELECT SYS.V_$DATAGUARD_PROCESS" in row["required_privileges"]
        assert "SELECT SYS.V_$INSTANCE" in row["required_privileges"]
        assert "GRANT SELECT ON SYS.V_$INSTANCE TO DBA_MONITOR" in row["reference_grant"]
        assert "SELECT SYS.V_$SESSION" in row["required_privileges"]
        assert "SELECT SYS.V_$SQL" in row["required_privileges"]
        assert "SELECT SYS.V_$TRANSACTION" in row["required_privileges"]
        assert "GRANT SELECT ON SYS.V_$DATAGUARD_PROCESS TO DBA_MONITOR" in row["reference_grant"]
        assert "V_$MANAGED_STANDBY" not in row["required_privileges"]
        assert main(["--inventory", str(inventory), "permissions", "oracle", "--feature",
                     "replication"], provider=provider, driver=object()) == 0
        human = capsys.readouterr().out
        assert "Version Status" in human and "UNVERIFIED" in human
        assert "Vendor Error" in human and "ORA-00942" in human
        assert "MONITORING_OBJECT_UNAVAILABLE" in human
        assert "Vendor Guidance" in human and "direct SELECT" in human
        assert "Reference Grants" in human
        assert "Toolkit did NOT execute this script." in human
    else:
        assert output["version_profile"]["version_status"] == "UNKNOWN"
        assert output["features"][0]["support_state"] == "UNKNOWN"
        assert "reference_grant" not in output["features"][0]


def test_oracle_runtime_mismatch_does_not_resolve_static_permission_grant() -> None:
    profile = PROFILE_RESOLVER.resolve(
        DatabaseType.ORACLE, "19c", DetectedDatabaseVersion("Oracle 26.0", "26ai"),
        "replication",
    )
    assert profile.version_status == VersionStatus.MISMATCH
    assert profile.permission_profile_id is None


def test_oracle_11gr2_offline_replication_grant_uses_legacy_objects_only() -> None:
    profile = PROFILE_RESOLVER.resolve(DatabaseType.ORACLE, "11gr2", None, "replication")
    assert profile.version_status == VersionStatus.UNVERIFIED
    assert profile.permission_profile_id == "oracle11gr2_replication"
    required = requirements_for_profile(profile.permission_profile_id).required
    grant = render_profile_sql(profile.permission_profile_id, DatabaseType.ORACLE, "DBA_MONITOR")
    assert "SELECT SYS.V_$MANAGED_STANDBY" in required
    assert "SELECT SYS.V_$DATAGUARD_PROCESS" not in required
    assert "V_$DATAGUARD_PROCESS" not in grant


@pytest.mark.parametrize(("family", "profile_id", "feature_objects"), [
    ("11gr2", "oracle11gr2_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$MANAGED_STANDBY",
                                            "SYS.V_$ARCHIVE_DEST_STATUS")),
    ("12c", "oracle12c_121_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$MANAGED_STANDBY",
                                            "SYS.V_$ARCHIVE_DEST_STATUS")),
    ("12c", "oracle12c_122_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                            "SYS.V_$ARCHIVE_DEST_STATUS")),
    ("19c", "oracle_modern_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                            "SYS.V_$ARCHIVE_DEST_STATUS")),
    ("23ai", "oracle_modern_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                             "SYS.V_$ARCHIVE_DEST_STATUS")),
    ("26ai", "oracle_modern_replication", ("SYS.V_$DATAGUARD_STATS", "SYS.V_$DATAGUARD_PROCESS",
                                             "SYS.V_$ARCHIVE_DEST_STATUS")),
])
def test_oracle_replication_profiles_include_bootstrap_version_and_feature_permissions(
    family: str, profile_id: str, feature_objects: tuple[str, ...],
) -> None:
    detected = None
    if family == "12c":
        variant = "12_1" if profile_id.endswith("121_replication") else "12_2_plus"
        detected = DetectedDatabaseVersion(
            f"Oracle 12.{1 if variant == '12_1' else 2}.0.1", "12c", variant
        )
    profile = PROFILE_RESOLVER.resolve(DatabaseType.ORACLE, family, detected, "replication")
    assert profile.permission_profile_id == profile_id
    required = requirements_for_profile(profile_id).required
    assert all(item in required for item in ORACLE_COMMON_MONITOR_REQUIREMENTS)
    assert all("SELECT " + item in required for item in feature_objects)
    if family == "11gr2" or (family == "12c" and profile.runtime_variant == "12_1"):
        assert "SELECT SYS.V_$DATAGUARD_PROCESS" not in required
    grant = render_profile_sql(profile_id, DatabaseType.ORACLE, "DBA_MONITOR")
    assert "GRANT CREATE SESSION TO DBA_MONITOR" in grant
    assert "GRANT SELECT ON SYS.V_$INSTANCE TO DBA_MONITOR" in grant
    assert "GRANT SELECT ON SYS.V_$VERSION TO DBA_MONITOR" in grant
    assert "SYSDBA" not in grant and "SELECT_CATALOG_ROLE" not in grant and " TO DBA;" not in grant


def test_oracle_replication_contract_explicitly_covers_fixed_bootstrap_sql_dependencies() -> None:
    # This is intentionally an explicit contract, not SQL-derived permission inference.
    required = requirements_for_profile("oracle_modern_replication").required
    audited_preflight_views = {
        "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION", "SELECT SYS.V_$DATABASE",
        "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL", "SELECT SYS.V_$TRANSACTION",
    }
    assert audited_preflight_views <= set(required)
    assert VERSION_DETECTION_PRIVILEGES[DatabaseType.ORACLE][0] in required


def test_oracle_replication_missing_bootstrap_object_is_isolated_but_full_grant_is_complete() -> None:
    requirements = requirements_for_profile("oracle_modern_replication")
    observed = tuple(item for item in requirements.required
                     if item != "SELECT SYS.V_$INSTANCE")
    status, missing = status_for(requirements, observed)
    assert status == PermissionStatus.MISSING
    assert missing == ("SELECT SYS.V_$INSTANCE",)
    grant = render_profile_sql("oracle_modern_replication", DatabaseType.ORACLE, "DBA_MONITOR")
    assert "GRANT SELECT ON SYS.V_$INSTANCE TO DBA_MONITOR" in grant
    assert all(item.removeprefix("SELECT ") in grant for item in requirements.required
               if item.startswith("SELECT "))


@pytest.mark.parametrize(("message", "classification"), [
    ('ORA-00942: table or view "SYS"."V_$INSTANCE" does not exist',
     "MONITORING_OBJECT_UNAVAILABLE"),
    ('ORA-00942: table or view "APP"."V_$INSTANCE" does not exist', "UNKNOWN"),
    ('ORA-00942: table or view "APP"."CUSTOM_TABLE" does not exist', "UNKNOWN"),
])
def test_oracle_942_classifies_only_known_toolkit_monitoring_objects(
    message: str, classification: str,
) -> None:
    error = normalize_oracle_error(RuntimeError(message))
    assert error.vendor_code == "ORA-00942"
    assert error.message == message
    assert error.classification == classification
    if classification != "UNKNOWN":
        assert "direct SELECT" in (error.guidance or "")

