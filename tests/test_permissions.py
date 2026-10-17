import json
from pathlib import Path

import pytest
import yaml

from dba.cli import main
from dba.core import DatabaseType
from dba.credentials import Credential, MockCredentialProvider
from dba.permissions import (
    REGISTRY,
    PermissionStatus,
    render_sql,
    requirements_for,
    scan_registry_objects,
    status_for,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("database_type", [DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                              DatabaseType.MYSQL, DatabaseType.ORACLE])
def test_permission_registry_covers_supported_database_types(database_type: DatabaseType) -> None:
    assert "slow" in REGISTRY[database_type]
    assert "disk-growth" in REGISTRY[database_type]
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

