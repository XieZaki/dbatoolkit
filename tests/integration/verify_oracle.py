"""Explicit disposable Oracle CLI verification; never part of ordinary pytest."""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import oracledb
import yaml

ROOT = Path("/workspace")
DATABASE_ID = "oracle-v071-test"
MONITOR_USER = "DBA_TOOLKIT_V071_MON"
ACTOR_USER = "DBA_TOOLKIT_V071_ACT"
TABLE = "DBA_TOOLKIT_LOCK_TEST"


def run_cli(args: list[str], username: str, password: str, timeout: float = 90) -> tuple[int, str, list[dict[str, Any]]]:
    """Exercise the actual CLI entrypoint and interactive PromptCredentialProvider."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "dba", *args])
    output = bytearray()
    sent_user = sent_password = False
    deadline = time.monotonic() + timeout
    status: int | None = None
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
                decoded = output.decode("utf-8", errors="replace")
                if not sent_user and "Monitoring account username:" in decoded:
                    os.write(fd, (username + "\n").encode())
                    sent_user = True
                if sent_user and not sent_password and "Monitoring account password:" in decoded:
                    os.write(fd, (password + "\n").encode())
                    sent_password = True
            waited, code = os.waitpid(pid, os.WNOHANG)
            if waited:
                status = code
                break
        if status is None:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if not waited:
                os.kill(pid, signal.SIGTERM)
                _, status = os.waitpid(pid, 0)
                raise RuntimeError("CLI verification exceeded time limit")
    finally:
        os.close(fd)
    text = output.decode("utf-8", errors="replace")
    if password in text:
        raise RuntimeError("Credential appeared in CLI output")
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return os.waitstatus_to_exitcode(status), text, records


def payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [record for record in records if "success" in record]
    if not matching:
        raise RuntimeError("CLI returned no result payload")
    return matching[-1]


def required_sections(path: Path, passwords: tuple[str, ...]) -> None:
    contents = path.read_text(encoding="utf-8")
    for title in ("故障概况", "锁与阻塞链", "现场诊断结论", "会话身份与来源",
                  "诊断发现与建议", "证据完整性"):
        if title not in contents:
            raise RuntimeError("HTML report section missing")
    if (any(password in contents for password in passwords)
            or "password=" in contents.lower() or "pwd=" in contents.lower()):
        raise RuntimeError("Credential marker appeared in HTML report")


def actor_connect(config: dict[str, Any]) -> oracledb.Connection:
    connection = oracledb.connect(user=ACTOR_USER, password=config["actor_password"],
                                 host=config["host"], port=config["port"],
                                 service_name=config["service_name"],
                                 tcp_connect_timeout=5)
    connection.autocommit = False
    connection.call_timeout = 60000
    return connection


def main() -> int:
    config = json.load(sys.stdin)
    summary: dict[str, Any] = {"driver": oracledb.__version__, "thin_mode": oracledb.is_thin_mode(),
                               "service_name": config["service_name"], "cli": {},
                               "blocking": {}, "cleanup": {}}
    if not summary["thin_mode"]:
        raise RuntimeError("Oracle client unexpectedly entered Thick mode")
    with tempfile.TemporaryDirectory(prefix="dba-oracle-v071-") as temporary:
        temporary_path = Path(temporary)
        inventory = temporary_path / "inventory.yaml"
        inventory.write_text(yaml.safe_dump({"databases": [{
            "id": DATABASE_ID, "name": "Oracle V0.7.1 test", "db_type": "oracle",
            "host": config["host"], "port": config["port"],
            "service_name": config["service_name"],
            "credential_alias": "oracle-v071-monitor", "enabled": True
        }]}), encoding="utf-8")
        prefix = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
                  "--settings", str(ROOT / "config/runtime.yaml")]

        def invoke(command: list[str], *, report: Path | None = None) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
            full = [*prefix, *command]
            if report:
                full += ["--report", str(report)]
            code, rendered, records = run_cli(full, MONITOR_USER, config["monitor_password"])
            if code != 0:
                categories = [record.get("error_type") for record in records if record.get("error_type")]
                raise RuntimeError(f"CLI command failed: {command[-1]} ({categories[-1] if categories else 'unknown'})")
            found = next((record for record in reversed(records) if "success" in record), None)
            if found is not None and not found["success"]:
                raise RuntimeError("CLI returned unsuccessful collector or diagnosis")
            return found, rendered, records

        wrong_password = "WRONG_" + config["monitor_password"]
        denied_code, denied_text, denied_records = run_cli(
            [*prefix, "test-connection", DATABASE_ID], MONITOR_USER, wrong_password)
        if (denied_code == 0 or not denied_records
                or not any(record.get("success") is False for record in denied_records)
                or config["monitor_password"] in denied_text):
            raise RuntimeError("Credential failure was not handled safely")
        summary["credential_failure_redacted"] = True
        connected, _, connect_logs = invoke(["test-connection", DATABASE_ID])
        assert connected is not None
        summary["oracle_version"] = connected["version"]
        summary["preflight"] = connected["preflight"]
        summary["cli"]["test-connection"] = {"success": True}
        if not all(connected["preflight"].values()):
            raise RuntimeError("Monitor preflight capability missing")
        for name in ("server-info", "database-status", "active-sessions",
                     "active-requests", "long-running"):
            result, _, records = invoke(["collect", DATABASE_ID, name])
            assert result is not None
            summary["cli"][name] = {
                "success": result["success"], "row_count": result["row_count"],
                "truncated": result["truncated"], "elapsed_ms": result["elapsed_ms"]}
            if not any(record.get("module") == "oracle" and record.get("sql_script")
                       and record.get("timeout_seconds") is not None
                       and record.get("max_rows") is not None
                       for record in records):
                raise RuntimeError("Oracle audit event missing")
        if not any(record.get("module") == "oracle" for record in connect_logs):
            raise RuntimeError("Oracle connection audit missing")
        baseline_path = temporary_path / "baseline.html"
        _, baseline_console, _ = invoke(["diagnose", DATABASE_ID, "slow"], report=baseline_path)
        required_sections(baseline_path, (config["monitor_password"], config["actor_password"]))
        if ("Blocking Tree" not in baseline_console
                or "No blocking observed in available sample." not in baseline_console):
            raise RuntimeError("Console blocking section missing")
        summary["baseline_report"] = "passed"

        a = actor_connect(config)
        b_ready = threading.Event()
        b_done = threading.Event()
        c_done = threading.Event()
        worker_errors: list[str] = []

        def worker_b() -> None:
            connection = None
            try:
                connection = actor_connect(config)
                cursor = connection.cursor()
                cursor.execute(f"UPDATE {TABLE} SET marker=marker+1 WHERE id=2")
                b_ready.set()
                cursor.execute(f"UPDATE {TABLE} SET marker=marker+1 WHERE id=1")
            except Exception as exc:
                worker_errors.append(type(exc).__name__)
            finally:
                if connection:
                    connection.rollback()
                    connection.close()
                b_done.set()

        def worker_c() -> None:
            connection = None
            try:
                connection = actor_connect(config)
                cursor = connection.cursor()
                cursor.execute(f"UPDATE {TABLE} SET marker=marker+1 WHERE id=2")
            except Exception as exc:
                worker_errors.append(type(exc).__name__)
            finally:
                if connection:
                    connection.rollback()
                    connection.close()
                c_done.set()

        thread_b: threading.Thread | None = None
        thread_c: threading.Thread | None = None
        try:
            a.cursor().execute(f"UPDATE {TABLE} SET marker=marker+1 WHERE id=1")
            thread_b = threading.Thread(target=worker_b, daemon=True)
            thread_b.start()
            if not b_ready.wait(10):
                raise RuntimeError("Blocking fixture B did not acquire row 2")
            thread_c = threading.Thread(target=worker_c, daemon=True)
            thread_c.start()
            time.sleep(2)
            live_collectors: dict[str, int] = {}
            for name in ("active-sessions", "active-requests"):
                live_result, _, _ = invoke(["collect", DATABASE_ID, name])
                assert live_result is not None
                live_collectors[name] = live_result["row_count"]
            if live_collectors["active-sessions"] < 3 or live_collectors["active-requests"] < 2:
                raise RuntimeError("Live blocking sessions were absent from collectors")
            runtime = yaml.safe_load((ROOT / "config/runtime.yaml").read_text(encoding="utf-8"))
            runtime["oracle"]["long_running_seconds"] = 1
            runtime_path = temporary_path / "runtime-fast.yaml"
            runtime_path.write_text(yaml.safe_dump(runtime), encoding="utf-8")
            fast_prefix = ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
                           "--settings", str(runtime_path)]
            long_code, _, long_records = run_cli(
                [*fast_prefix, "collect", DATABASE_ID, "long-running"],
                MONITOR_USER, config["monitor_password"])
            long_result = payload(long_records)
            if long_code != 0 or not long_result["success"] or long_result["row_count"] < 1:
                raise RuntimeError("Configured long-running threshold produced no live requests")
            summary["live_collectors"] = {**live_collectors,
                                          "long-running": long_result["row_count"]}
            blocking_path = temporary_path / "blocking.html"
            blocking_result, _, _ = invoke(
                ["diagnose", DATABASE_ID, "slow", "--format", "json"],
                report=blocking_path)
            assert blocking_result is not None
            report = blocking_result["report"]
            detail = report["details"]
            findings = {item["title"]: item for item in report["findings"]}
            roots = detail["roots"]
            idle = detail["idle_transactions"]
            if len(roots) != 1 or len(idle) != 1:
                raise RuntimeError("Expected one idle root transaction")
            root, transaction = roots[0], idle[0]
            if (root.get("username") != ACTOR_USER or root.get("status") != "INACTIVE"
                    or type(root.get("sid")) is not int
                    or type(root.get("serial_number")) is not int
                    or not root.get("machine") or not root.get("program")
                    or not root.get("prev_sql_id") or not root.get("previous_sql")
                    or root.get("blocked_count", 0) < 2
                    or transaction.get("transaction_age_seconds") is None
                    or transaction.get("used_ublk") is None
                    or transaction.get("used_urec") is None
                    or not transaction.get("previous_sql_text")
                    or len(transaction.get("blocked_sessions", [])) < 2):
                raise RuntimeError("Root blocker identity or transaction details missing")
            idle_finding = findings.get("IDLE_TRANSACTION")
            if idle_finding is None or idle_finding["severity"] != "CRITICAL":
                raise RuntimeError("Idle transaction severity mismatch")
            commands = findings["ROOT_BLOCKER"]["reference_commands"]
            expected_command = (
                f"ALTER SYSTEM KILL SESSION '{root['sid']},{root['serial_number']}' IMMEDIATE;")
            if len(commands) != 1 or not commands[0]["manual_only"] or commands[0]["text"] != expected_command:
                raise RuntimeError("Root blocker reference command mismatch")
            summary["blocking"] = {
                "blocked_sessions": detail["concurrency"]["blocked_sessions"],
                "root_count": len(roots), "idle_transaction_count": len(idle),
                "graph_depth_two": any(
                    any(child in detail["blocking_graph"]["children"]
                        for child in children)
                    for children in detail["blocking_graph"]["children"].values()),
                "findings": sorted(findings),
                "root_inactive": any(row["status"] == "INACTIVE" for row in roots),
                "transaction_evidence": any(
                    row.get("transaction_start_time") and
                    row.get("used_ublk") is not None and row.get("used_urec") is not None
                    for row in idle),
                "previous_sql_available": any(row.get("previous_sql") for row in roots),
                "reference_text_only": any(
                    item["reference_commands"] and
                    item["reference_commands"][0]["manual_only"]
                    for item in report["findings"] if item["title"] == "ROOT_BLOCKER")}
            required_sections(blocking_path, (config["monitor_password"], config["actor_password"]))
            if (detail["concurrency"]["blocked_sessions"] < 1
                    or not any(row["status"] == "INACTIVE" for row in roots)
                    or "IDLE_TRANSACTION" not in findings
                    or not summary["blocking"]["reference_text_only"]):
                raise RuntimeError("Real blocking or idle transaction evidence missing")
            limit_root = temporary_path / "sql-limit"
            shutil.copytree(ROOT / "sql", limit_root)
            metadata_path = limit_root / "oracle" / "active_sessions.yaml"
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            metadata["max_rows"] = 1
            metadata_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")
            limited_prefix = ["--inventory", str(inventory), "--sql-root", str(limit_root),
                              "--settings", str(ROOT / "config/runtime.yaml")]
            limited_code, _, limited_records = run_cli(
                [*limited_prefix, "collect", DATABASE_ID, "active-sessions"],
                MONITOR_USER, config["monitor_password"])
            limited = payload(limited_records)
            summary["max_rows"] = {"success": limited_code == 0 and limited["success"],
                                   "truncated": limited["truncated"], "row_count": limited["row_count"]}
            if not summary["max_rows"]["truncated"] or limited["row_count"] != 1:
                raise RuntimeError("Real max_rows truncation not observed")
        finally:
            a.rollback()
            a.close()
            if thread_b:
                thread_b.join(10)
            if thread_c:
                thread_c.join(10)
            summary["cleanup"]["actor_connections"] = (
                b_done.is_set() and c_done.is_set() and not worker_errors)
            if not summary["cleanup"]["actor_connections"]:
                raise RuntimeError("Actor sessions did not close cleanly")

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print driver exception text; it may contain credentials.
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "error_message": "Oracle integration verification failed safely."}))
        raise SystemExit(1) from None
