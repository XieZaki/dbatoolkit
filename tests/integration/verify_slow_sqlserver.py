"""Explicit V0.3 disposable fixture; ordinary pytest never runs this file.

Fixture workload connections may write ONLY the newly created test table. All
Toolkit diagnosis uses the dedicated read-only monitor through the real CLI.
"""

import contextlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from verify_sqlserver import HOST, LOGIN, cli_run, connection_string

sys.path.insert(0, "/workspace")
from dba.environment import driver_info  # noqa: E402


def verify(config: dict[str, str]) -> dict[str, Any]:
    if sys.platform != "linux" or config.get("host") != HOST or not Path("/.dockerenv").exists():
        raise RuntimeError("Disposable Docker fixture required")
    pyodbc = importlib.import_module("pyodbc")
    pyodbc.pooling = False
    admin = pyodbc.connect(
        connection_string("sa", config["administrator_password"], "isolated-bootstrap"),
        autocommit=True,
        timeout=5,
    )
    admin.timeout = 15
    observer = None
    workers: list[Any] = []
    threads: list[threading.Thread] = []
    thread_errors: list[str] = []
    report: dict[str, Any] = {"success": False, "environment": driver_info(), "scenarios": []}
    setup_done = False
    owner = None
    owner_thread = None
    try:
        cursor = admin.cursor()
        try:
            pwd = config["workload_password"].replace("'", "''")
            cursor.execute(f"CREATE LOGIN toolkit_workload WITH PASSWORD=N'{pwd}';")
            cursor.execute(Path(__file__).with_name("slow_fixture.sql").read_text())
            while cursor.nextset():
                pass
            setup_done = True
        finally:
            cursor.close()
        observer = pyodbc.connect(
            connection_string(LOGIN, config["monitor_password"], "verification-observer"),
            autocommit=True,
            timeout=5,
            readonly=True,
        )
        observer.timeout = 10

        def scalar(sql: str) -> Any:
            cursor = observer.cursor()
            try:
                return cursor.execute(sql).fetchone()[0]
            finally:
                cursor.close()

        report["version"] = scalar("SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(32));")
        report["monitor_is_sysadmin"] = scalar("SELECT IS_SRVROLEMEMBER('sysadmin');")
        assert report["monitor_is_sysadmin"] == 0

        def worker() -> tuple[Any, int]:
            connection = pyodbc.connect(
                connection_string("toolkit_workload", config["workload_password"], "slow-fixture"),
                autocommit=True,
                timeout=5,
            )
            connection.timeout = 20
            workers.append(connection)
            cursor = connection.cursor()
            try:
                sid = int(cursor.execute("SELECT @@SPID;").fetchone()[0])
            finally:
                cursor.close()
            return connection, sid

        def execute(connection: Any, sql: str) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute(sql)
                while cursor.nextset():
                    pass
            finally:
                cursor.close()

        def start(connection: Any, sql: str, rollback: bool = False) -> threading.Thread:
            def run() -> None:
                try:
                    execute(connection, sql)
                except Exception:
                    thread_errors.append("FixtureQueryFailed")
                finally:
                    if rollback:
                        try:
                            execute(connection, "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;")
                        except Exception:
                            thread_errors.append("FixtureRollbackFailed")

            thread = threading.Thread(target=run)
            threads.append(thread)
            thread.start()
            return thread

        def wait_request(sid: int, blocker: int | None = None) -> None:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                condition = f" AND blocking_session_id={blocker}" if blocker else ""
                if scalar(
                    f"SELECT COUNT(*) FROM sys.dm_exec_requests WHERE session_id={sid}{condition};"
                ):
                    return
                time.sleep(0.1)
            raise RuntimeError("Expected fixture request unavailable")

        with tempfile.TemporaryDirectory(prefix="dba-slow-") as temp:
            root = Path(temp)
            inventory = root / "inventory.yaml"
            inventory.write_text(
                yaml.safe_dump(
                    {
                        "databases": [
                            {
                                "id": "isolated-sqlserver",
                                "name": "Slow fixture",
                                "db_type": "sqlserver",
                                "host": HOST,
                                "port": 1433,
                                "database": "master",
                                "credential_alias": "isolated-monitor",
                                "environment": "development",
                            }
                        ]
                    }
                )
            )
            settings = root / "runtime.yaml"

            def diagnose(name: str, threshold: int = 60) -> dict[str, Any]:
                settings.write_text(
                    yaml.safe_dump(
                        {
                            "sqlserver": {
                                "trust_server_certificate": True,
                                "long_running_seconds": threshold,
                                "slow_top_n": 10,
                                "high_concurrency_threshold": 2,
                            }
                        }
                    )
                )
                html_report = root / f"{name}.html"
                code, payload, captured = cli_run(
                    [
                        "--inventory",
                        str(inventory),
                        "--settings",
                        str(settings),
                        "--sql-root",
                        "/workspace/sql",
                        "diagnose",
                        "isolated-sqlserver",
                        "slow",
                        "--format",
                        "json",
                        "--report",
                        str(html_report),
                    ],
                    config["monitor_password"],
                )
                assert code == 0 and payload.get("success"), "Real diagnosis failed"
                text = html_report.read_text(encoding="utf-8")
                for key in ("monitor_password", "administrator_password", "workload_password"):
                    assert config[key] not in captured + text, "Credential leakage detected"
                assert "故障概况" in text and "锁与阻塞链" in text
                payload_report = payload["report"]
                report["scenarios"].append(
                    {
                        "name": name,
                        "success": True,
                        "concurrency": payload_report["details"]["concurrency"],
                        "findings": [f["title"] for f in payload_report["findings"]],
                        "roots": payload_report["details"]["roots"],
                        "blocking_graph": payload_report["details"]["blocking_graph"],
                        "samples": payload_report["details"]["samples"],
                        "html_verified": True,
                        "credential_leakage": False,
                    }
                )
                return payload_report

            active, active_sid = worker()
            thread = start(active, "SELECT 1 AS fixture_active; WAITFOR DELAY '00:00:08';")
            wait_request(active_sid)
            payload = diagnose("active-sql")
            assert any(r["session_id"] == active_sid for r in payload["details"]["top_requests"])
            time.sleep(1.1)
            payload = diagnose("long-running", 1)
            assert any(r["session_id"] == active_sid for r in payload["details"]["long_running"])
            thread.join(15)
            assert not thread.is_alive()

            owner, owner_sid = worker()
            blocked, blocked_sid = worker()
            execute(
                owner,
                "BEGIN TRANSACTION; UPDATE dbo.ToolkitSlowFixture SET value=value+1 WHERE id=1;",
            )
            thread_b = start(
                blocked,
                "BEGIN TRANSACTION; UPDATE dbo.ToolkitSlowFixture SET value=value+1 WHERE id=2; UPDATE dbo.ToolkitSlowFixture SET value=value+1 WHERE id=1;",
                True,
            )
            wait_request(blocked_sid, owner_sid)
            # Scenario 3: owner has a live low-cost batch while holding the lock.
            owner_thread = start(owner, "WAITFOR DELAY '00:00:05';")
            wait_request(owner_sid)
            payload = diagnose("blocking")
            assert owner_sid in payload["details"]["blocking_graph"]["roots"]
            assert not any(f["title"] == "IDLE_TRANSACTION_BLOCKER" for f in payload["findings"])
            owner_thread.join(10)
            assert not owner_thread.is_alive()
            # Set a real most-recent SQL batch and leave the transaction open.
            execute(owner, "UPDATE dbo.ToolkitSlowFixture SET value=value+1 WHERE id=1;")
            payload = diagnose("sleeping-transaction")
            idle = next(f for f in payload["findings"] if f["title"] == "IDLE_TRANSACTION_BLOCKER")
            evidence = idle["evidence"]
            assert evidence["session_id"] == owner_sid and evidence["status"] == "sleeping"
            assert (
                evidence["open_transaction_count"] > 0 and evidence["transaction_age_seconds"] >= 1
            )
            assert evidence["transactions"] and any("UPDATE" in sql for sql in evidence["last_sql"])
            assert blocked_sid in evidence["blocked_sessions"]
            assert any(
                c["text"] == f"KILL {owner_sid};"
                for f in payload["findings"]
                for c in f["reference_commands"]
            )
            third, third_sid = worker()
            thread_c = start(
                third,
                "BEGIN TRANSACTION; UPDATE dbo.ToolkitSlowFixture SET value=value+1 WHERE id=2;",
                True,
            )
            wait_request(third_sid, blocked_sid)
            payload = diagnose("multi-level-chain")
            graph = payload["details"]["blocking_graph"]
            assert blocked_sid in graph["children"][str(owner_sid)]
            assert third_sid in graph["children"][str(blocked_sid)]
            # Release through rollback, never KILL or cancellation.
            execute(owner, "ROLLBACK TRANSACTION;")
            thread_b.join(25)
            thread_c.join(25)
            assert not thread_b.is_alive() and not thread_c.is_alive()
            assert not thread_errors
            count = scalar(
                "SELECT COUNT(*) FROM sys.dm_tran_session_transactions t JOIN sys.dm_exec_sessions s ON t.session_id=s.session_id WHERE s.program_name='slow-fixture';"
            )
            report["remaining_fixture_transactions"] = count
            assert count == 0
            report["remaining_toolkit_sessions"] = scalar(
                "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE program_name='dba-toolkit';"
            )
            assert report["remaining_toolkit_sessions"] == 0
            cursor = admin.cursor()
            try:
                assert (
                    cursor.execute("SELECT SUM(value) FROM dbo.ToolkitSlowFixture;").fetchone()[0]
                    == 0
                )
            finally:
                cursor.close()
            report["success"] = True
    finally:
        # If a scenario fails during blocking, release the idle root before
        # waiting for blocked workers. Never share a connection with its thread.
        if owner is not None:
            if owner_thread is not None and owner_thread.is_alive():
                owner_thread.join(7)
            if owner_thread is None or not owner_thread.is_alive():
                try:
                    cursor = owner.cursor()
                    try:
                        cursor.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;")
                    finally:
                        cursor.close()
                except Exception:
                    thread_errors.append("FixtureOwnerRollbackFailed")
        # Busy workers use bounded query timeouts and their own finally rollback.
        for thread in threads:
            if thread.is_alive():
                thread.join(22)
        for connection in workers:
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;")
                finally:
                    cursor.close()
            except Exception:
                thread_errors.append("FixtureCleanupFailed")
            finally:
                try:
                    connection.close()
                except Exception:
                    thread_errors.append("FixtureCloseFailed")
        if observer is not None:
            observer.close()
        try:
            if setup_done:
                cursor = admin.cursor()
                try:
                    cursor.execute(
                        "DROP TABLE dbo.ToolkitSlowFixture; DROP USER toolkit_workload; DROP LOGIN toolkit_workload;"
                    )
                    while cursor.nextset():
                        pass
                    report["test_objects_removed"] = True
                finally:
                    cursor.close()
        finally:
            admin.close()
    return report


def main() -> int:
    config = json.load(sys.stdin)
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            report = verify(config)
        output = json.dumps(report, ensure_ascii=False, default=str, indent=2)
        if any(
            config[key] in output
            for key in ("monitor_password", "administrator_password", "workload_password")
        ):
            raise RuntimeError("Credential leakage detected")
        print(output)
        return 0 if report["success"] else 1
    except Exception as exc:
        # Never print original exceptions, SQL, credentials or locals.
        import traceback

        print(
            json.dumps(
                {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "locations": [
                        {"function": f.name, "line": f.lineno}
                        for f in traceback.extract_tb(exc.__traceback__)
                    ],
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
