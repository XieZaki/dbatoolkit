"""Explicit Docker-only real verification; never collected by ordinary pytest.

The bootstrap mode is a user-approved test-fixture exception outside Toolkit.
Secrets arrive on stdin; neither mode stores or prints them.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, "/workspace")

from dba.credentials import Credential, CredentialProvider  # noqa: E402
from dba.environment import driver_info  # noqa: E402
from dba.inventory import Inventory  # noqa: E402
from dba.preflight import READ_PERMISSIONS  # noqa: E402
from dba.registry import SqlScriptRegistry  # noqa: E402
from dba.settings import SQLServerSettings  # noqa: E402
from dba.sqlserver import SQLServerAdapter  # noqa: E402

HOST = "dba-toolkit-v021-sqlserver"
LOGIN = "toolkit_monitor"


def odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def connection_string(username: str, password: str, app: str) -> str:
    values = {
        "DRIVER": "ODBC Driver 18 for SQL Server",
        "SERVER": f"tcp:{HOST},1433",
        "DATABASE": "master",
        "UID": username,
        "PWD": password,
        "Encrypt": "yes",
        "TrustServerCertificate": "yes",
        "APP": app,
    }
    return ";".join(f"{k}={odbc_value(v)}" for k, v in values.items())


class InputCredentialProvider(CredentialProvider):
    def __init__(self, password: str) -> None:
        self._password = password

    def get_credential(self, alias: str) -> Credential:
        if alias != "isolated-monitor":
            raise ValueError("Unexpected test alias")
        return Credential(LOGIN, self._password)


def bootstrap(config: dict[str, str], pyodbc: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    connection = None
    while time.monotonic() < deadline:
        try:
            connection = pyodbc.connect(
                connection_string("sa", config["administrator_password"], "isolated-bootstrap"),
                timeout=5,
                autocommit=True,
            )
            break
        except Exception:
            time.sleep(5)
    if connection is None:
        return {"bootstrap_success": False, "error_type": "IsolatedServerStartupFailed"}
    cursor = None
    try:
        connection.timeout = 10
        cursor = connection.cursor()
        # Fixed account identifiers; the only SQL interpolation is safely quoted
        # random test password. Never part of the production SQL Registry.
        quoted_password = config["monitor_password"].replace("'", "''")
        cursor.execute(
            f"CREATE LOGIN [{LOGIN}] WITH PASSWORD=N'{quoted_password}', CHECK_POLICY=ON;"
        )
        cursor.execute(f"CREATE USER [{LOGIN}] FOR LOGIN [{LOGIN}];")
        # Restrict inherited public procedure execution in this fresh fixture.
        # This reduces rights; production provisioning remains manual.
        cursor.execute(f"DENY EXECUTE TO [{LOGIN}];")
        cursor.execute(f"GRANT VIEW SERVER PERFORMANCE STATE TO [{LOGIN}];")
        cursor.execute(f"GRANT VIEW ANY DATABASE TO [{LOGIN}];")
        cursor.execute(f"GRANT VIEW ANY DEFINITION TO [{LOGIN}];")
        return {"bootstrap_success": True, "account": LOGIN, "isolated_only": True}
    except Exception:
        return {"bootstrap_success": False, "error_type": "IsolatedAccountInitializationFailed"}
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def cli_run(arguments: list[str], password: str) -> tuple[int, dict[str, Any], str]:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "dba", *arguments],
        cwd="/workspace",
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    output = bytearray()
    username_sent = password_sent = False
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.2)
            if readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if not username_sent and b"Monitoring account username:" in output:
                    os.write(master, (LOGIN + "\n").encode())
                    username_sent = True
                if not password_sent and b"Monitoring account password:" in output:
                    os.write(master, (password + "\n").encode())
                    password_sent = True
            if len(output) > 2_000_000:
                raise RuntimeError("Captured output limit exceeded")
        if process.poll() is None:
            process.wait(timeout=5)
        text = output.decode("utf-8", errors="replace")
        if password in text:
            raise RuntimeError("Credential leakage detected; output suppressed")
        result: dict[str, Any] = {}
        for line in text.splitlines():
            position = line.find("{")
            if position >= 0:
                try:
                    candidate = json.loads(line[position:])
                    if "success" in candidate:
                        result = candidate
                except json.JSONDecodeError:
                    pass
        return process.returncode, result, text
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait()


def reviewed_read(
    connection: Any, registry: SqlScriptRegistry, name: str
) -> tuple[list[str], list[dict[str, Any]]]:
    script = registry.get(name)
    registry.authorize(script, script.metadata.database_type)
    connection.timeout = script.metadata.timeout_seconds
    cursor = connection.cursor()
    try:
        cursor.execute(script.text)
        columns = [column[0] for column in cursor.description]
        return columns, [
            dict(zip(columns, row)) for row in cursor.fetchmany(script.metadata.max_rows)
        ]
    except Exception as exc:
        raw = " ".join(str(arg) for arg in exc.args)
        diagnostics = {
            "script": name,
            "sqlstate": exc.args[0]
            if exc.args and re.fullmatch(r"[0-9A-Z]{5}", str(exc.args[0]))
            else None,
            "native_error_numbers": re.findall(r"\((\d{3,6})\)", raw),
            "invalid_columns": re.findall(r"Invalid column name '([A-Za-z_]+)'", raw),
        }
        print(json.dumps({"readonly_probe_failure": diagnostics}))
        raise
    finally:
        cursor.close()


def verify(config: dict[str, str], pyodbc: Any) -> dict[str, Any]:
    password = config["monitor_password"]
    provider = InputCredentialProvider(password)
    registry = SqlScriptRegistry(Path("/workspace/sql"))
    report: dict[str, Any] = {
        "environment": driver_info(),
        "transport": "Linux Docker / TCP / encrypted",
        "trust_server_certificate": True,
        "commands": [],
        "success": False,
    }
    with tempfile.TemporaryDirectory(prefix="dba-verification-") as temp:
        root = Path(temp)
        inventory_path = root / "inventory.yaml"
        inventory_path.write_text(
            yaml.safe_dump(
                {
                    "databases": [
                        {
                            "id": "isolated-sqlserver",
                            "name": "Isolated verification",
                            "db_type": "sqlserver",
                            "host": HOST,
                            "port": 1433,
                            "database": "master",
                            "environment": "development",
                            "credential_alias": "isolated-monitor",
                            "enabled": True,
                        }
                    ]
                }
            )
        )
        settings_path = root / "runtime.yaml"
        settings_path.write_text(yaml.safe_dump({"sqlserver": {"trust_server_certificate": True}}))
        common = [
            "--inventory",
            str(inventory_path),
            "--settings",
            str(settings_path),
            "--sql-root",
            "/workspace/sql",
        ]
        instance = Inventory.load(inventory_path).get("isolated-sqlserver")
        observer = pyodbc.connect(
            connection_string(LOGIN, password, "verification-observer"),
            timeout=5,
            autocommit=True,
            readonly=True,
        )
        try:
            report["odbc_driver_version"] = observer.getinfo(pyodbc.SQL_DRIVER_VER)
            for command in [
                ["test-connection", instance.id],
                *[
                    ["collect", instance.id, name]
                    for name in (
                        "server-info",
                        "database-status",
                        "active-sessions",
                        "active-requests",
                        "long-running",
                    )
                ],
            ]:
                started = time.monotonic()
                code, result, captured = cli_run([*common, *command], password)
                if command[0] == "test-connection" and result.get("error_type") == "QueryTimeout":
                    report["initial_connection_attempt"] = {
                        "exit_code": code,
                        "success": result.get("success"),
                        "error_type": result.get("error_type"),
                        "error_message": result.get("error_message"),
                        "wall_elapsed_ms": (time.monotonic() - started) * 1000,
                        "scope": "One explicit harness retry after a cold-instance preflight timeout; Toolkit does not retry automatically.",
                    }
                    started = time.monotonic()
                    code, result, captured = cli_run([*common, *command], password)
                audits = []
                for line in captured.splitlines():
                    position = line.find("{")
                    if position < 0:
                        continue
                    try:
                        record = json.loads(line[position:])
                    except json.JSONDecodeError:
                        continue
                    if "module" in record and "sql_script" in record:
                        audits.append(record)
                item = {
                    "command": " ".join(command),
                    "exit_code": code,
                    "success": result.get("success", False),
                    "wall_elapsed_ms": (time.monotonic() - started) * 1000,
                    "elapsed_ms": result.get("elapsed_ms"),
                    "row_count": result.get("row_count"),
                    "truncated": result.get("truncated"),
                    "error_type": result.get("error_type"),
                    "error_message": result.get("error_message"),
                    "version": result.get("version"),
                    "preflight": result.get("preflight"),
                    "field_names": sorted(result["rows"][0]) if result.get("rows") else [],
                    "null_field_names": sorted(
                        key for key, value in result["rows"][0].items() if value is None
                    )
                    if result.get("rows")
                    else [],
                    "credential_leakage": False,
                    "audit_records": audits,
                    "audit_contains_sql_text": '"sql_text":' in captured.split('"rows":')[0],
                }
                report["commands"].append(item)
            for name in ("active_sessions", "active_requests"):
                columns, rows = reviewed_read(observer, registry, name)
                expected = (
                    {
                        "session_id",
                        "login_name",
                        "host_name",
                        "program_name",
                        "client_net_address",
                        "database_name",
                        "status",
                        "login_time",
                        "last_request_start_time",
                        "last_request_end_time",
                        "open_transaction_count",
                    }
                    if name == "active_sessions"
                    else {
                        "session_id",
                        "status",
                        "command",
                        "start_time",
                        "elapsed_ms",
                        "cpu_ms",
                        "logical_reads",
                        "reads",
                        "writes",
                        "wait_type",
                        "wait_ms",
                        "blocking_session_id",
                        "database_name",
                        "sql_text",
                    }
                )
                report[name + "_schema"] = {
                    "columns": columns,
                    "row_count": len(rows),
                    "missing_columns": sorted(expected - set(columns)),
                }
            _, permissions = reviewed_read(observer, registry, "monitor_permissions")
            report["permissions_not_allowlisted"] = [
                row for row in permissions if row["permission_name"] not in READ_PERMISSIONS
            ]
            _, capabilities = reviewed_read(observer, registry, "monitor_capabilities")
            report["monitor_capabilities"] = capabilities[0] if capabilities else None
            _, server = reviewed_read(observer, registry, "server_info")
            report["server_info"] = server[0] if server else None

            def toolkit_sessions() -> int:
                _, rows = reviewed_read(observer, registry, "active_sessions")
                return sum(
                    row.get("login_name") == LOGIN and row.get("program_name") == "dba-toolkit"
                    for row in rows
                )

            report["toolkit_sessions_after_cli"] = toolkit_sessions()
            if not all(item["success"] for item in report["commands"]):
                return report
            # Actual row bound on four system databases, without running load SQL.
            copied = root / "sql"
            shutil.copytree("/workspace/sql", copied)
            metadata = copied / "sqlserver/database_status.yaml"
            data = yaml.safe_load(metadata.read_text())
            data["max_rows"] = 1
            metadata.write_text(yaml.safe_dump(data))
            with SQLServerAdapter(
                instance,
                provider,
                SqlScriptRegistry(copied),
                SQLServerSettings(trust_server_certificate=True),
            ) as adapter:
                result = adapter.execute_readonly(adapter.registry.get("database_status"))
                report["max_rows_real"] = {
                    "success": result.success,
                    "row_count": result.row_count,
                    "truncated": result.truncated,
                    "configured_max_rows": 1,
                    "unbounded_observed_row_count": report["commands"][2]["row_count"],
                    "driver_query_timeout": adapter._connection.timeout,
                }
                report["autocommit"] = adapter._connection.autocommit
            # Python-level interruption after real connection; not a claim that
            # Ctrl+C interrupts native ODBC immediately during a blocking call.
            try:
                with SQLServerAdapter(
                    instance, provider, registry, SQLServerSettings(trust_server_certificate=True)
                ):
                    raise KeyboardInterrupt
            except KeyboardInterrupt:
                pass
            report["sessions_after_python_interrupt"] = toolkit_sessions()
            failure_sql = copied / "sqlserver/verification_query_failure.sql"
            failure_sql.write_text("SELECT toolkit_missing_column FROM sys.dm_exec_requests;")
            failure_sql.with_suffix(".yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": "verification_query_failure",
                        "database_type": "sqlserver",
                        "risk": "READ_ONLY",
                        "reviewed": True,
                        "timeout_seconds": 5,
                        "max_rows": 1,
                        "description": "Reviewed safe invalid-column SELECT for isolated query failure verification",
                    }
                )
            )
            with SQLServerAdapter(
                instance,
                provider,
                SqlScriptRegistry(copied),
                SQLServerSettings(trust_server_certificate=True),
            ) as adapter:
                failed = adapter.execute_readonly(
                    adapter.registry.get("verification_query_failure")
                )
                report["real_query_failure"] = {
                    "success": failed.success,
                    "error_type": failed.error_type,
                    "error_message": failed.error_message,
                }
            report["sessions_after_query_failure"] = toolkit_sessions()
            report["query_timeout_scope"] = (
                "Actual driver timeout attribute verified; any natural initial timeout is recorded separately. Controlled expiry regressions remain Fake-tested; no load or WAITFOR SQL executed."
            )
            report["success"] = (
                all(item["success"] for item in report["commands"])
                and not report["active_sessions_schema"]["missing_columns"]
                and not report["active_requests_schema"]["missing_columns"]
                and report["autocommit"] is True
                and not report["sessions_after_python_interrupt"]
                and report["max_rows_real"]["truncated"]
                and not failed.success
                and not toolkit_sessions()
            )
            return report
        finally:
            observer.close()


def main() -> int:
    try:
        config = json.load(sys.stdin)
        if config.get("host") != HOST or sys.platform != "linux":
            raise RuntimeError(
                "This harness only targets the explicitly approved isolated Docker instance"
            )
        pyodbc = importlib.import_module("pyodbc")
        pyodbc.pooling = False
        if sys.argv[1] == "bootstrap":
            report = bootstrap(config, pyodbc)
            success = report["bootstrap_success"]
        elif sys.argv[1] == "verify":
            with contextlib.redirect_stderr(open(os.devnull, "w")):
                report = verify(config, pyodbc)
            success = report["success"]
        else:
            raise RuntimeError("Unknown harness mode")
        output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        for key in ("administrator_password", "monitor_password"):
            if config.get(key) and config[key] in output:
                raise RuntimeError("Credential leakage detected; report suppressed")
        print(output)
        return 0 if success else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": "VerificationHarnessFailed",
                    "error_message": "Harness failed; original exception suppressed to protect credentials.",
                    "exception_class": type(exc).__name__,
                    "locations": [
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(exc.__traceback__)
                    ],
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
