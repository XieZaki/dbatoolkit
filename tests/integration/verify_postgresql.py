"""Explicit PostgreSQL disposable Docker fixture; never run by ordinary pytest."""

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

import psycopg
import yaml
from psycopg import sql
from verify_sqlserver import cli_run

HOST = "dba-toolkit-v05-postgresql"


def connect(username: str, password: str, app: str, read_only: bool = False) -> Any:
    return psycopg.connect(host=HOST, port=5432, dbname="postgres", user=username,
                           password=password, connect_timeout=5, sslmode="disable",
                           application_name=app,
                           options="-c default_transaction_read_only=on" if read_only else "",
                           autocommit=True)


def verify(config: dict[str, str]) -> dict[str, Any]:
    if sys.platform != "linux" or config.get("host") != HOST or not Path("/.dockerenv").exists():
        raise RuntimeError("Disposable Docker fixture required")
    deadline = time.monotonic() + 120
    admin = None
    while time.monotonic() < deadline:
        try:
            admin = connect("postgres", config["administrator_password"], "fixture-bootstrap")
            break
        except Exception:
            time.sleep(2)
    if admin is None:
        raise RuntimeError("Disposable server did not start")
    observer = None
    workers: list[Any] = []
    threads: list[threading.Thread] = []
    errors: list[str] = []
    report: dict[str, Any] = {"success": False, "scenarios": [], "version": None,
                              "test_objects_removed": False}
    setup = False
    try:
        with admin.cursor() as cursor:
            # Fixed identifiers and psycopg Literal quoting; only this new test container.
            cursor.execute(sql.SQL("CREATE ROLE toolkit_monitor LOGIN PASSWORD {}").format(
                sql.Literal(config["monitor_password"])))
            cursor.execute("GRANT pg_monitor TO toolkit_monitor")
            cursor.execute(sql.SQL("CREATE ROLE toolkit_workload LOGIN PASSWORD {}").format(
                sql.Literal(config["workload_password"])))
            cursor.execute("CREATE TABLE public.toolkit_pg_fixture (id integer PRIMARY KEY, value integer NOT NULL)")
            cursor.execute("INSERT INTO public.toolkit_pg_fixture VALUES (1, 0), (2, 0), (3, 0)")
            cursor.execute("GRANT USAGE ON SCHEMA public TO toolkit_workload")
            cursor.execute("GRANT SELECT, UPDATE ON public.toolkit_pg_fixture TO toolkit_workload")
            setup = True
        observer = connect("toolkit_monitor", config["monitor_password"],
                           "verification-observer", read_only=True)
        with observer.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::integer")
            report["version"] = cursor.fetchone()[0]
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            assert cursor.fetchone()[0] is False

        def worker_connection() -> tuple[Any, int]:
            connection = connect("toolkit_workload", config["workload_password"], "pg-fixture")
            workers.append(connection)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
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
                            connection.execute("ROLLBACK")
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
                        cursor.execute("SELECT state FROM pg_stat_activity WHERE pid = %s", (pid,))
                        row = cursor.fetchone()
                        if row and row[0] == "active":
                            return
                    else:
                        cursor.execute("SELECT pg_blocking_pids(%s)", (pid,))
                        if blocker in cursor.fetchone()[0]:
                            return
                time.sleep(0.1)
            raise RuntimeError("Expected fixture activity unavailable")

        with tempfile.TemporaryDirectory(prefix="dba-pg-") as temp:
            root = Path(temp)
            inventory = root / "inventory.yaml"
            inventory.write_text(yaml.safe_dump({"databases": [{
                "id": "isolated-postgresql", "name": "Disposable PostgreSQL",
                "db_type": "postgresql", "host": HOST, "port": 5432,
                "database": "postgres", "credential_alias": "isolated-monitor",
                "environment": "development",
            }]}), encoding="utf-8")
            settings = root / "runtime.yaml"
            settings.write_text(yaml.safe_dump({"postgresql": {
                "sslmode": "disable", "long_running_seconds": 1,
                "idle_transaction_seconds": 1, "slow_top_n": 10,
            }}), encoding="utf-8")
            base = ["--inventory", str(inventory), "--settings", str(settings),
                    "--sql-root", "/workspace/sql"]

            def run_cli(arguments: list[str]) -> dict[str, Any]:
                code, payload, captured = cli_run([*base, *arguments], config["monitor_password"])
                assert code == 0 and payload.get("success"), "Toolkit CLI failed"
                for key in ("administrator_password", "monitor_password", "workload_password"):
                    assert config[key] not in captured, "Credential leakage detected"
                return payload

            connected = run_cli(["test-connection", "isolated-postgresql"])
            assert connected["preflight"]["read_all_stats"]
            for collector in ("server-info", "database-status", "active-sessions", "long-running"):
                payload = run_cli(["collect", "isolated-postgresql", collector])
                assert payload["success"] and not payload["truncated"]
            report["scenarios"].append({"name": "connection_and_collectors", "success": True})

            def diagnose(name: str) -> dict[str, Any]:
                html_report = root / f"{name}.html"
                payload = run_cli(["diagnose", "isolated-postgresql", "slow",
                                   "--format", "json", "--report", str(html_report)])
                text = html_report.read_text(encoding="utf-8")
                assert "故障概况" in text and "诊断发现与建议" in text
                for key in ("administrator_password", "monitor_password", "workload_password"):
                    assert config[key] not in text, "Credential leakage detected"
                report["scenarios"].append({"name": name, "success": True,
                    "findings": [item["title"] for item in payload["report"]["findings"]],
                    "concurrency": payload["report"]["details"]["concurrency"],
                    "roots": [item["pid"] for item in payload["report"]["details"]["roots"]]})
                return payload["report"]

            active, active_pid = worker_connection()
            active_thread = start(active, ["SELECT pg_sleep(6)"])
            wait_for(active_pid)
            payload = diagnose("active")
            assert payload["details"]["concurrency"]["active_queries"] >= 1
            time.sleep(1.2)
            payload = diagnose("long_running")
            assert "LONG_RUNNING_SQL" in [item["title"] for item in payload["findings"]]
            active_thread.join(10)
            assert not active_thread.is_alive()

            owner, owner_pid = worker_connection()
            blocked, blocked_pid = worker_connection()
            owner.execute("BEGIN")
            owner.execute("UPDATE public.toolkit_pg_fixture SET value = value + 1 WHERE id = 1")
            thread_b = start(blocked, ["BEGIN", "UPDATE public.toolkit_pg_fixture "
                "SET value = value + 1 WHERE id = 2", "UPDATE public.toolkit_pg_fixture "
                "SET value = value + 1 WHERE id = 1"], rollback=True)
            wait_for(blocked_pid, owner_pid)
            payload = diagnose("idle_root")
            roots = payload["details"]["roots"]
            assert any(item["pid"] == owner_pid and item["state"] == "idle in transaction"
                       and blocked_pid in item["blocked_pids"] and item["last_query"] for item in roots)
            assert "IDLE_IN_TRANSACTION" in [item["title"] for item in payload["findings"]]
            assert any(item["reference_commands"] for item in payload["findings"]
                       if item["title"] == "ROOT_BLOCKER")

            third, third_pid = worker_connection()
            thread_c = start(third, ["BEGIN", "UPDATE public.toolkit_pg_fixture "
                "SET value = value + 1 WHERE id = 2"], rollback=True)
            wait_for(third_pid, blocked_pid)
            payload = diagnose("multi_level")
            graph = payload["details"]["blocking_graph"]
            assert blocked_pid in graph["children"][str(owner_pid)]
            assert third_pid in graph["children"][str(blocked_pid)]
            owner.execute("ROLLBACK")
            thread_b.join(15)
            thread_c.join(15)
            assert not thread_b.is_alive() and not thread_c.is_alive() and not errors

            with observer.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity "
                               "WHERE application_name = 'dba-toolkit'")
                report["remaining_toolkit_sessions"] = cursor.fetchone()[0]
            assert report["remaining_toolkit_sessions"] == 0
            report["success"] = True
    finally:
        for connection in workers:
            try:
                connection.execute("ROLLBACK")
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
                    cursor.execute("DROP TABLE public.toolkit_pg_fixture")
                    cursor.execute("REVOKE USAGE ON SCHEMA public FROM toolkit_workload")
                    cursor.execute("DROP ROLE toolkit_workload")
                    cursor.execute("DROP ROLE toolkit_monitor")
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
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "locations": [{"function": item.name, "line": item.lineno}
                                        for item in traceback.extract_tb(exc.__traceback__)]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
