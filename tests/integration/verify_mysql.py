"""Explicit MySQL disposable Docker fixture; never run by ordinary pytest."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import pymysql
import yaml
from verify_sqlserver import cli_run

HOST = "dba-toolkit-v06-mysql"


def connect(username: str, password: str) -> Any:
    return pymysql.connect(host=HOST, port=3306, database="toolkit_fixture",
                           user=username, password=password, connect_timeout=5,
                           read_timeout=25, charset="utf8mb4", ssl_disabled=True,
                           autocommit=True)


def verify(config: dict[str, str]) -> dict[str, Any]:
    if sys.platform != "linux" or config.get("host") != HOST or not Path("/.dockerenv").exists():
        raise RuntimeError("Disposable Docker fixture required")
    admin = None
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            admin = connect("root", config["administrator_password"])
            break
        except Exception:
            time.sleep(2)
    if admin is None:
        raise RuntimeError("Disposable MySQL server did not start")
    workers: list[Any] = []
    threads: list[threading.Thread] = []
    errors: list[str] = []
    report: dict[str, Any] = {"success": False, "scenarios": [], "version": None,
                              "test_objects_removed": False}
    setup = False
    observer = None
    try:
        with admin.cursor() as cursor:
            cursor.execute("CREATE USER 'toolkit_monitor'@'%%' IDENTIFIED BY %s",
                           (config["monitor_password"],))
            cursor.execute("GRANT PROCESS, SHOW DATABASES ON *.* TO 'toolkit_monitor'@'%'")
            cursor.execute("GRANT SELECT ON performance_schema.* TO 'toolkit_monitor'@'%'")
            cursor.execute("GRANT SELECT ON toolkit_fixture.* TO 'toolkit_monitor'@'%'")
            cursor.execute("CREATE TABLE toolkit_fixture.toolkit_test "
                           "(id integer PRIMARY KEY, value integer NOT NULL) ENGINE=InnoDB")
            cursor.execute("INSERT INTO toolkit_fixture.toolkit_test VALUES (1,0),(2,0),(3,0)")
            cursor.execute("UPDATE performance_schema.setup_consumers SET enabled='YES' "
                           "WHERE name='events_waits_current'")
            setup = True
        observer = connect("toolkit_monitor", config["monitor_password"])
        with observer.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            report["version"] = cursor.fetchone()[0]

        def worker_connection() -> tuple[Any, int]:
            connection = connect("toolkit_workload", config["workload_password"])
            workers.append(connection)
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID()")
                pid = int(cursor.fetchone()[0])
            return connection, pid

        def start(connection: Any, statements: list[str], rollback: bool = False) -> threading.Thread:
            def run() -> None:
                try:
                    with connection.cursor() as cursor:
                        for statement in statements:
                            cursor.execute(statement)
                except Exception:
                    errors.append("FixtureQueryFailed")
                finally:
                    if rollback:
                        try:
                            connection.rollback()
                        except Exception:
                            errors.append("FixtureRollbackFailed")
            thread = threading.Thread(target=run)
            threads.append(thread)
            thread.start()
            return thread

        def wait_for(pid: int, blocker: int | None = None) -> None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with observer.cursor() as cursor:
                    if blocker is None:
                        cursor.execute("SELECT PROCESSLIST_COMMAND FROM performance_schema.threads "
                                       "WHERE PROCESSLIST_ID = %s", (pid,))
                        row = cursor.fetchone()
                        if row and row[0] == "Query":
                            return
                    else:
                        cursor.execute("SELECT COUNT(*) FROM performance_schema.data_lock_waits w "
                                       "JOIN performance_schema.threads a "
                                       "ON a.THREAD_ID=w.REQUESTING_THREAD_ID "
                                       "JOIN performance_schema.threads b "
                                       "ON b.THREAD_ID=w.BLOCKING_THREAD_ID "
                                       "WHERE a.PROCESSLIST_ID=%s AND b.PROCESSLIST_ID=%s",
                                       (pid, blocker))
                        if cursor.fetchone()[0] > 0:
                            return
                time.sleep(0.1)
            raise RuntimeError("Expected fixture activity unavailable")

        with tempfile.TemporaryDirectory(prefix="dba-mysql-") as temp:
            root = Path(temp)
            inventory = root / "inventory.yaml"
            inventory.write_text(yaml.safe_dump({"databases": [{
                "id": "isolated-mysql", "name": "Disposable MySQL", "db_type": "mysql",
                "host": HOST, "port": 3306, "database": "toolkit_fixture",
                "credential_alias": "isolated-monitor", "environment": "development",
            }]}), encoding="utf-8")
            settings = root / "runtime.yaml"
            settings.write_text(yaml.safe_dump({"mysql": {"ssl_enabled": False,
                "long_running_seconds": 1, "idle_transaction_seconds": 1,
                "slow_top_n": 10}}), encoding="utf-8")
            base = ["--inventory", str(inventory), "--settings", str(settings),
                    "--sql-root", "/workspace/sql"]

            def run_cli(arguments: list[str]) -> dict[str, Any]:
                code, payload, captured = cli_run([*base, *arguments], config["monitor_password"])
                for key in ("administrator_password", "monitor_password", "workload_password"):
                    assert config[key] not in captured, "Credential leakage detected"
                if code != 0 or not payload.get("success"):
                    kind = payload.get("error_type") or "IncompleteReport"
                    quality = payload.get("report", {}).get("details", {}).get("quality", [])
                    raise RuntimeError(f"Toolkit CLI failed: {kind}; quality={quality}")
                return payload

            connected = run_cli(["test-connection", "isolated-mysql"])
            assert connected["preflight"]["performance_schema"]
            for collector in ("server-info", "database-status", "active-sessions",
                              "active-requests", "long-running"):
                payload = run_cli(["collect", "isolated-mysql", collector])
                assert payload["success"] and not payload["truncated"]
            report["scenarios"].append({"name": "connection_and_collectors", "success": True})

            def diagnose(name: str) -> dict[str, Any]:
                html_report = root / f"{name}.html"
                payload = run_cli(["diagnose", "isolated-mysql", "slow",
                                   "--format", "json", "--report", str(html_report)])
                content = html_report.read_text(encoding="utf-8")
                assert "故障概况" in content and "诊断发现与建议" in content
                for key in ("administrator_password", "monitor_password", "workload_password"):
                    assert config[key] not in content, "Credential leakage detected"
                detail = payload["report"]["details"]
                report["scenarios"].append({"name": name, "success": True,
                    "findings": [item["title"] for item in payload["report"]["findings"]],
                    "concurrency": detail["concurrency"],
                    "roots": [item["processlist_id"] for item in detail["roots"]]})
                return payload["report"]

            active, active_pid = worker_connection()
            active_thread = start(active, ["SELECT SLEEP(15)"])
            wait_for(active_pid)
            payload = diagnose("active")
            assert payload["details"]["concurrency"]["active_statements"] >= 1
            time.sleep(1.2)
            payload = diagnose("long_running")
            assert "LONG_RUNNING_SQL" in [item["title"] for item in payload["findings"]], (
                "missing long-running finding; durations=" + str([
                    row.get("duration_ms") for row in payload["details"]["top_queries"]]))
            active_thread.join(20)
            assert not active_thread.is_alive()

            owner, owner_pid = worker_connection()
            blocked, blocked_pid = worker_connection()
            owner.begin()
            with owner.cursor() as cursor:
                cursor.execute("UPDATE toolkit_fixture.toolkit_test "
                               "SET value=value+1 WHERE id=1")
            thread_b = start(blocked, ["BEGIN", "UPDATE toolkit_fixture.toolkit_test "
                "SET value=value+1 WHERE id=2", "UPDATE toolkit_fixture.toolkit_test "
                "SET value=value+1 WHERE id=1"], rollback=True)
            wait_for(blocked_pid, owner_pid)
            payload = diagnose("idle_root")
            roots = payload["details"]["roots"]
            assert any(item["processlist_id"] == owner_pid and item["command_name"] == "Sleep"
                       and blocked_pid in item["blocked_pids"] for item in roots)
            assert "IDLE_TRANSACTION" in [item["title"] for item in payload["findings"]]
            assert any(item["reference_commands"] for item in payload["findings"]
                       if item["title"] == "ROOT_BLOCKER")

            third, third_pid = worker_connection()
            thread_c = start(third, ["BEGIN", "UPDATE toolkit_fixture.toolkit_test "
                "SET value=value+1 WHERE id=2"], rollback=True)
            wait_for(third_pid, blocked_pid)
            payload = diagnose("multi_level")
            graph = payload["details"]["blocking_graph"]
            assert blocked_pid in graph["children"][str(owner_pid)]
            assert third_pid in graph["children"][str(blocked_pid)]
            owner.rollback()
            thread_b.join(15)
            thread_c.join(15)
            assert not thread_b.is_alive() and not thread_c.is_alive() and not errors

            with observer.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM performance_schema.threads "
                               "WHERE PROCESSLIST_USER='toolkit_monitor' "
                               "AND PROCESSLIST_ID<>CONNECTION_ID()")
                report["remaining_toolkit_sessions"] = cursor.fetchone()[0]
            assert report["remaining_toolkit_sessions"] == 0
            report["success"] = True
    finally:
        for connection in workers:
            try:
                connection.rollback()
            except Exception:
                pass
        for thread in threads:
            if thread.is_alive():
                thread.join(20)
        for connection in workers:
            connection.close()
        if observer is not None:
            observer.close()
        try:
            if setup:
                with admin.cursor() as cursor:
                    cursor.execute("DROP TABLE toolkit_fixture.toolkit_test")
                    cursor.execute("DROP USER 'toolkit_monitor'@'%'")
                    report["test_objects_removed"] = True
        finally:
            admin.close()
    return report


def main() -> int:
    config = json.load(sys.stdin)
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            report = verify(config)
        output = json.dumps(report, ensure_ascii=False, default=str, indent=2)
        if any(config[key] in output for key in
               ("administrator_password", "monitor_password", "workload_password")):
            raise RuntimeError("Credential leakage detected")
        print(output)
        return 0 if report["success"] else 1
    except Exception as exc:
        message = str(exc)
        for key in ("administrator_password", "monitor_password", "workload_password"):
            message = message.replace(config[key], "[REDACTED]")
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "error_message": message[:300],
                          "locations": [{"function": item.name, "line": item.lineno}
                                        for item in traceback.extract_tb(exc.__traceback__)]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
