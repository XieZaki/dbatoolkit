"""Explicit V0.8 Docker check; reuses four existing databases and removes test accounts."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker") or "docker"
TARGETS = {
    "sqlserver": ("sqlserver2022", "dba-toolkit-verification-client:v021", 1433,
                  "DBA_TOOLKIT_V08_MON"),
    "postgresql": ("pg16", "dba-toolkit-pg-client:v05", 5432, "dba_toolkit_v08_mon"),
    "mysql": ("mysql84", "dba-toolkit-mysql-client:v06", 3306, "dba_toolkit_v08_mon"),
    "oracle": ("oraclefree", "dba-toolkit-mysql-client:v06", 1521, "DBA_TOOLKIT_V08_MON"),
}


def docker(*args: str, input_text: str | None = None) -> str:
    completed = subprocess.run([DOCKER, *args], input=input_text, text=True,
                               capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError("Docker verification step failed safely")
    return completed.stdout


def admin(db_type: str, server: str, sql: str) -> str:
    if db_type == "sqlserver":
        command = ["exec", "-i", server, "bash", "-c",
                   'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; '
                   '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -i /dev/stdin']
        payload = sql + "\nGO\n"
    elif db_type == "postgresql":
        command = ["exec", "-i", "-u", "postgres", server, "psql", "-v", "ON_ERROR_STOP=1",
                   "-Atq", "postgres"]
        payload = sql + "\n"
    elif db_type == "mysql":
        command = ["exec", "-i", server, "sh", "-c",
                   'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N']
        payload = sql + "\n"
    else:
        command = ["exec", "-i", server, "sqlplus", "-s", "/", "as", "sysdba"]
        payload = ("whenever sqlerror exit 5\nalter session set container=FREEPDB1;\n"
                   + sql + "\nexit\n")
    return docker(*command, input_text=payload)


def cleanup(db_type: str, server: str, user: str) -> None:
    if db_type == "sqlserver":
        admin(db_type, server, f"USE msdb; DROP USER IF EXISTS [{user}];")
        admin(db_type, server, f"USE master; DROP USER IF EXISTS [{user}]; DROP LOGIN [{user}];")
    elif db_type == "postgresql":
        admin(db_type, server, f"DROP ROLE {user};")
    elif db_type == "mysql":
        admin(db_type, server, f"DROP USER '{user}'@'%';")
    else:
        admin(db_type, server, f"DROP USER {user} CASCADE;")


def verify(db_type: str) -> dict[str, Any]:
    server, image, port, user = TARGETS[db_type]
    client = f"dba-toolkit-v08-{db_type}-client"
    if client in docker("ps", "-a", "--format", "{{.Names}}").splitlines():
        raise RuntimeError("Temporary client name already exists")
    if docker("inspect", server, "--format", "{{.State.Status}}").strip() != "running":
        raise RuntimeError("Existing database container is not running")
    password = secrets.token_hex(24) + "aA1!"
    created = client_created = False
    try:
        # The first CREATE is deliberately isolated so partial setup is removable.
        if db_type == "sqlserver":
            admin(db_type, server, f"CREATE LOGIN [{user}] WITH PASSWORD=N'{password}', CHECK_POLICY=ON;")
            created = True
            admin(db_type, server, f"CREATE USER [{user}] FOR LOGIN [{user}];\n"
                 f"DENY EXECUTE TO [{user}];\n"
                 f"GRANT VIEW SERVER PERFORMANCE STATE TO [{user}];\n"
                 f"GRANT VIEW ANY DATABASE TO [{user}];\n"
                 f"GRANT VIEW ANY DEFINITION TO [{user}];")
            admin(db_type, server, f"USE msdb; CREATE USER [{user}] FOR LOGIN [{user}];\n"
                 f"GRANT SELECT ON dbo.backupset TO [{user}];")
        elif db_type == "postgresql":
            admin(db_type, server, f"CREATE ROLE {user} LOGIN PASSWORD '{password}';")
            created = True
            admin(db_type, server, f"GRANT pg_monitor TO {user};")
        elif db_type == "mysql":
            admin(db_type, server, f"CREATE USER '{user}'@'%' IDENTIFIED BY '{password}';")
            created = True
            admin(db_type, server,
                  f"GRANT PROCESS, SHOW DATABASES, REPLICATION CLIENT ON *.* TO '{user}'@'%';\n"
                  f"GRANT SELECT ON performance_schema.* TO '{user}'@'%';")
        else:
            admin(db_type, server, f'CREATE USER {user} IDENTIFIED BY "{password}";')
            created = True
            setup_oracle_grants(server, user)
        docker("create", "--name", client, "--network", f"container:{server}",
               "--mount", f"type=bind,source={ROOT},target=/workspace,readonly",
               image, "sleep", "infinity")
        client_created = True
        docker("start", client)
        if db_type == "oracle":
            docker("exec", "-u", "root", client, "pip", "install", "--no-cache-dir",
                   "--disable-pip-version-check", "--index-url", "https://pypi.org/simple",
                   "oracledb>=2,<4")
        payload = json.dumps({"db_type": db_type, "port": port, "username": user,
                              "password": password, "database": "master" if db_type == "sqlserver"
                              else "postgres" if db_type == "postgresql" else None,
                              "service_name": "freepdb1" if db_type == "oracle" else None})
        output = docker("exec", "-i", client, "python",
                        "/workspace/tests/integration/verify_disk_growth.py", input_text=payload)
        if password in output:
            raise RuntimeError("Credential appeared in verification output")
        result = json.loads(output)
        if not result.get("success"):
            raise RuntimeError(f"{db_type} CLI verification failed safely")
        return result
    finally:
        if client_created:
            docker("stop", client)
            docker("rm", client)
        if created:
            cleanup(db_type, server, user)


def setup_oracle_grants(server: str, user: str) -> None:
    admin("oracle", server, f"GRANT CREATE SESSION TO {user};")
    for view in ("V_$INSTANCE", "V_$DATABASE", "V_$SESSION", "V_$SQL",
                 "V_$TRANSACTION", "V_$LOCK", "V_$LOCKED_OBJECT", "V_$PROCESS",
                 "V_$SESSION_WAIT", "V_$RECOVERY_FILE_DEST", "V_$RECOVERY_AREA_USAGE",
                 "V_$ARCHIVE_DEST_STATUS", "V_$ARCHIVED_LOG", "V_$LOG_HISTORY",
                 "DBA_TABLESPACE_USAGE_METRICS", "DBA_TABLESPACES",
                 "DBA_DATA_FILES", "DBA_TEMP_FREE_SPACE"):
        admin("oracle", server, f"GRANT SELECT ON SYS.{view} TO {user};")


if __name__ == "__main__":
    targets = sys.argv[1:] or list(TARGETS)
    try:
        summaries = []
        for name in targets:
            if name not in TARGETS:
                raise RuntimeError("Unknown verification target")
            summary = verify(name)
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        artifact = ROOT / ".tmp" / "docker-disk-growth-verification.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "error_message": "V0.8 Docker verification failed safely."}))
        raise SystemExit(1) from None
