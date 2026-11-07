r"""Bounded user-run monitor for an already reviewed local SQL Server lab query.

The user runs the workload manually in DBeaver as sa. This helper retrieves
only the existing Toolkit monitor credential and invokes the normal dba CLI.
It does not execute workload SQL, receive sa credentials, or prove a plan safe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dba.core import DatabaseInstance, DatabaseType
from dba.credential_session import CredentialSessionClient
from dba.credentials import Credential, KeyringCredentialStore
from dba.inventory import Inventory
from dba.memory_grant_report import _safe

ROOT = Path(__file__).resolve().parents[2]
DBA = ROOT / ".venv" / "Scripts" / "dba.exe"
EXPECTED_VERSION = "16.0.4305.5"
MAX_ATTEMPTS = 3
MAX_MONITOR_SECONDS = 30.0
CLI_TIMEOUT_SECONDS = 12.0
MAX_OUTPUT_BYTES = 8 * 1024 * 1024


class Blocked(Exception):
    """A fixed, non-secret reason safe to display."""


def _target(instance: DatabaseInstance) -> None:
    if (instance.id != "sqlserver2022" or instance.db_type is not DatabaseType.SQLSERVER
            or instance.host != "127.0.0.1" or instance.port != 1433
            or (instance.database or "master").casefold() != "master"
            or not instance.enabled or (instance.environment or "").casefold() != "test"):
        raise Blocked("INVENTORY_TARGET_MISMATCH")


def _monitor_credential(alias: str) -> Credential:
    try:
        store = KeyringCredentialStore()
        credential = store.get(alias) if store.available() else None
        if credential is None:
            session = CredentialSessionClient()
            if session.status().get("status") != "UNLOCKED":
                raise Blocked("MONITOR_CREDENTIAL_SESSION_LOCKED")
            credential = session.get(alias)
        if credential is None or credential.username.casefold() != "dba_monitor" or not credential.password:
            raise Blocked("MONITOR_CREDENTIAL_NOT_VERIFIED")
        return credential
    except Blocked:
        raise
    except Exception:
        raise Blocked("MONITOR_CREDENTIAL_UNAVAILABLE") from None


def _run_cli(arguments: list[str], timeout: float, secret: str) -> dict[str, Any]:
    try:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [str(DBA), *arguments, "--json"], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise Blocked("TOOLKIT_TIMEOUT") from None
    except Exception:
        raise Blocked("TOOLKIT_START_FAILED") from None
    if len(result.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise Blocked("TOOLKIT_OUTPUT_TOO_LARGE")
    if secret and (secret in result.stdout or secret in result.stderr):
        raise Blocked("CREDENTIAL_DISCLOSURE_DETECTED")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Blocked("TOOLKIT_INVALID_JSON") from None
    if not isinstance(payload, dict) or result.returncode != 0 or payload.get("success") is not True:
        raise Blocked("TOOLKIT_COMMAND_FAILED")
    return payload


def _session(payload: dict[str, Any], session_id: int) -> dict[str, Any]:
    rows = payload.get("rows")
    if payload.get("truncated") is not False or not isinstance(rows, list):
        raise Blocked("SESSION_VISIBILITY_INCOMPLETE")
    matches = [r for r in rows if isinstance(r, dict) and r.get("session_id") == session_id]
    if len(matches) != 1:
        raise Blocked("WORKLOAD_SESSION_NOT_UNIQUE")
    row = matches[0]
    if (str(row.get("login_name", "")).casefold() != "sa"
            or str(row.get("database_name", "")).casefold() != "adventureworks2022"
            or not isinstance(row.get("login_time"), str) or not row["login_time"]):
        raise Blocked("WORKLOAD_SESSION_IDENTITY_UNVERIFIED")
    return row


def _stable_session(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (before["session_id"] == after["session_id"]
            and before["login_time"] == after["login_time"]
            and str(before["login_name"]).casefold() == str(after["login_name"]).casefold()
            and str(before["database_name"]).casefold() == str(after["database_name"]).casefold())


def _request_newer_than_baseline(row: dict[str, Any], before: dict[str, Any]) -> bool:
    identity = row.get("request_identity")
    if not isinstance(identity, dict):
        return False
    current = identity.get("request_start_time")
    previous = before.get("last_request_start_time")
    if not isinstance(current, str) or not isinstance(previous, str):
        return False
    try:
        return datetime.fromisoformat(current) > datetime.fromisoformat(previous)
    except (ValueError, TypeError):
        return False


def _handle_status(first: Any, second: Any) -> str:
    if first is None or second is None or first == "" or second == "":
        return "MISSING"
    for value in (first, second):
        if not isinstance(value, str) or not re.fullmatch(r"0[xX](?:[0-9a-fA-F]{2}){1,64}", value):
            return "INVALID"
        if not any(character != "0" for character in value[2:]):
            return "MISSING"
    return "MATCH" if first.casefold() == second.casefold() else "CONFLICT"


def _optional_handle(value: str) -> str | None:
    if not value:
        return None
    if (not re.fullmatch(r"0[xX](?:[0-9a-fA-F]{2}){1,64}", value)
            or not any(character != "0" for character in value[2:])):
        raise Blocked("INVALID_REVIEWED_HANDLE")
    return value.lower()


def _candidate(data: dict[str, Any], session_id: int, database_id: int) -> dict[str, Any] | None:
    sample = data.get("granted_sample")
    if not isinstance(sample, dict) or sample.get("success") is not True:
        raise Blocked("GRANTED_SAMPLE_FAILED")
    rows = sample.get("rows")
    if not isinstance(rows, list):
        raise Blocked("GRANTED_SAMPLE_INVALID")
    matches = [r for r in rows if isinstance(r, dict) and r.get("session_id") == session_id]
    if len(matches) > 1:
        raise Blocked("MULTIPLE_REQUESTS_AMBIGUOUS")
    if not matches:
        return None
    row = matches[0]
    identity = row.get("request_identity")
    if (not isinstance(identity, dict) or type(row.get("request_id")) is not int
            or row["request_id"] < 0 or not isinstance(row.get("grant_time"), str)
            or not row["grant_time"] or type(row.get("granted_memory_kb")) is not int
            or row["granted_memory_kb"] < 0):
        raise Blocked("GRANTED_ROW_INCOMPLETE")
    if identity.get("request_context_status") != "MATCHED_AT_SAMPLE":
        raise Blocked("REQUEST_ASSOCIATION_UNVERIFIED")
    if type(identity.get("database_id")) is not int or identity["database_id"] != database_id:
        raise Blocked("REQUEST_DATABASE_UNVERIFIED")
    statuses = {
        _handle_status(row.get("sql_handle"), row.get("request_sql_handle")),
        _handle_status(row.get("plan_handle"), row.get("request_plan_handle")),
    }
    if "CONFLICT" in statuses or "INVALID" in statuses:
        raise Blocked("HANDLE_CONFLICT_OR_INVALID")
    return row


class _GrantedTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.seen = False
        self.in_cell = False
        self.cell = ""
        self.row: list[str] | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "section" and dict(attrs).get("id") == "memory-grant-granted":
            self.depth = 1
            self.seen = True
        elif self.depth and tag == "section":
            self.depth += 1
        elif self.depth and tag == "tr":
            self.row = []
        elif self.depth and tag == "td" and self.row is not None:
            self.in_cell = True
            self.cell = ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == "td" and self.in_cell and self.row is not None:
            self.row.append(self.cell)
            self.in_cell = False
        elif self.depth and tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif self.depth and tag == "section":
            self.depth -= 1


def _shared_projection(row: dict[str, Any]) -> list[str]:
    from dba.reporting.normalizers import display_value

    identity = row.get("request_identity")
    identity = identity if isinstance(identity, dict) else {}
    return [_safe(value) for value in (
        row.get("session_id"), row.get("request_id"),
        display_value(identity.get("request_status")),
        row.get("requested_memory_kb"), row.get("required_memory_kb"),
        row.get("granted_memory_kb"), row.get("used_memory_kb"),
        row.get("max_used_memory_kb"), identity.get("wait_type"),
        display_value(identity.get("request_context_status")),
    )]


def _html_matches(html: str, rows: list[Any]) -> bool:
    if any(not isinstance(row, dict) for row in rows):
        return False
    parser = _GrantedTable()
    parser.feed(html)
    return parser.seen and parser.rows == [_shared_projection(row) for row in rows]


def _runtime_gate() -> None:
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if (os.name != "nt" or Path.cwd().resolve() != ROOT
            or Path(sys.executable).resolve() != python.resolve()
            or Path(sys.prefix).resolve() != (ROOT / ".venv").resolve()
            or not DBA.is_file() or not sys.stdin.isatty() or not sys.stderr.isatty()):
        raise Blocked("WINDOWS_HOST_INTERACTIVE_VENV_REQUIRED")
    print("Run only in the confirmed Windows host test lab. Confirm host and reviewed SQL [y/N]: ",
          end="", file=sys.stderr, flush=True)
    if sys.stdin.readline().strip().casefold() != "y":
        raise Blocked("HOST_OR_PLAN_NOT_CONFIRMED")


def run(session_id: int, database_id: int, instance: DatabaseInstance,
        credential: Credential, reviewed_sql_handle: str | None = None,
        reviewed_plan_handle: str | None = None) -> dict[str, Any]:
    _target(instance)
    if type(session_id) is not int or not 1 <= session_id <= 32767:
        raise Blocked("INVALID_SESSION_ID")
    if type(database_id) is not int or not 1 <= database_id <= 2147483647:
        raise Blocked("INVALID_DATABASE_ID")
    secret = credential.password
    connection = _run_cli(["test-connection", instance.id], CLI_TIMEOUT_SECONDS, secret)
    if connection.get("version") != EXPECTED_VERSION:
        raise Blocked("SERVER_VERSION_MISMATCH")
    before = _session(_run_cli(["collect", instance.id, "active-sessions"],
                               CLI_TIMEOUT_SECONDS, secret), session_id)
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now().strftime("sqlserver2022_memory-grant-acceptance_%Y%m%d_%H%M%S_%f")
    deadline = time.monotonic() + MAX_MONITOR_SECONDS
    saved: list[dict[str, Any]] = []
    found: tuple[dict[str, Any], dict[str, Any], str] | None = None
    print(f"Evidence prefix: {reports / prefix}", file=sys.stderr, flush=True)
    print("Monitoring started. Execute the reviewed SELECT now in the same DBeaver sa session.",
          file=sys.stderr, flush=True)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"Toolkit sampling attempt {attempt}/{MAX_ATTEMPTS}.", file=sys.stderr, flush=True)
        report = reports / f"{prefix}_{attempt}.html"
        if report.exists():
            raise Blocked("REPORT_PATH_ALREADY_EXISTS")
        payload = _run_cli(["diagnose", instance.id, "memory-grant", "--report", str(report)],
                           min(CLI_TIMEOUT_SECONDS, remaining), secret)
        data = payload.get("memory_grant")
        report_data = payload.get("report")
        if (not isinstance(data, dict) or not isinstance(report_data, dict)
                or not isinstance(report_data.get("instance"), dict)
                or report_data["instance"].get("credential_alias") != "[REDACTED]"):
            raise Blocked("DIAGNOSTIC_JSON_NOT_SAFE")
        if data.get("database_version") != EXPECTED_VERSION:
            raise Blocked("SERVER_VERSION_MISMATCH")
        if (data.get("collection_status") != "COLLECTED"
                or data.get("completeness") != "COMPLETE"):
            raise Blocked("COLLECTION_INCOMPLETE")
        try:
            if not report.is_file() or report.stat().st_size > MAX_OUTPUT_BYTES:
                raise Blocked("REPORT_MISSING_OR_TOO_LARGE")
            html = report.read_text(encoding="utf-8")
            if secret and secret in html:
                report.unlink(missing_ok=True)
                raise Blocked("CREDENTIAL_DISCLOSURE_DETECTED")
            sample = data.get("granted_sample")
            rows = sample.get("rows") if isinstance(sample, dict) else None
            if not isinstance(sample, dict) or sample.get("truncated") is not False:
                raise Blocked("GRANTED_SAMPLE_TRUNCATED_OR_UNKNOWN")
            if not isinstance(rows, list) or not _html_matches(html, rows):
                raise Blocked("JSON_HTML_SHARED_FIELDS_MISMATCH")
            json_path = reports / f"{prefix}_{attempt}.json"
            with json_path.open("x", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=True, indent=2)
            saved.append({"json": str(json_path), "html": str(report)})
            row = _candidate(data, session_id, database_id)
            if row is not None:
                found = (data, row, str(json_path))
                break
        except OSError:
            raise Blocked("REPORT_IO_FAILED") from None
        if attempt < MAX_ATTEMPTS and deadline - time.monotonic() > 2:
            time.sleep(2)
    after = _session(_run_cli(["collect", instance.id, "active-sessions"],
                              CLI_TIMEOUT_SECONDS, secret), session_id)
    if not _stable_session(before, after):
        raise Blocked("WORKLOAD_SESSION_CHANGED")
    if found is None:
        return {"result": "BLOCKED", "reason": "NO_MATCHING_GRANTED_SAMPLE",
                "attempts": len(saved), "artifacts": saved, "e2e": "NOT_VERIFIED"}
    data, row, matched_json_path = found
    if not _request_newer_than_baseline(row, before):
        raise Blocked("REQUEST_START_NOT_VERIFIED")
    reviewed_sql = (_handle_status(row.get("sql_handle"), reviewed_sql_handle)
                    if reviewed_sql_handle is not None else "NOT_PROVIDED")
    reviewed_plan = (_handle_status(row.get("plan_handle"), reviewed_plan_handle)
                     if reviewed_plan_handle is not None else "NOT_PROVIDED")
    if {reviewed_sql, reviewed_plan} & {"CONFLICT", "INVALID"}:
        raise Blocked("REVIEWED_PLAN_IDENTITY_CONFLICT")
    sample = data["granted_sample"]
    return {
        "result": "GRANTED_OBSERVED_REVIEW_REQUIRED",
        "e2e": "NOT_VERIFIED",
        "session_id": session_id,
        "request_database_id": database_id,
        "request_id": row["request_id"],
        "grant_time": row["grant_time"],
        "granted_memory_kb": row["granted_memory_kb"],
        "request_context_status": row["request_identity"]["request_context_status"],
        "sql_handle_comparison": _handle_status(row.get("sql_handle"), row.get("request_sql_handle")),
        "plan_handle_comparison": _handle_status(row.get("plan_handle"), row.get("request_plan_handle")),
        "reviewed_sql_handle_comparison": reviewed_sql,
        "reviewed_plan_handle_comparison": reviewed_plan,
        "sample_started_at": sample.get("started_at"), "sample_finished_at": sample.get("finished_at"),
        "request_start_time_server": row["request_identity"].get("request_start_time"),
        "previous_request_start_time_server": before.get("last_request_start_time"),
        "truncated": sample.get("truncated"), "completeness": data.get("completeness"),
        "session_identity": "SAME_LOGIN_TIME_BEFORE_AND_AFTER",
        "request_start": "NEWER_THAN_PRE_MONITOR_SESSION_SNAPSHOT",
        "open_transaction_count_after": after.get("open_transaction_count"),
        "plan_and_actual_access_range": "NOT_VERIFIED",
        "workload_session_release": "USER_CONTROLLED_NOT_VERIFIED",
        "limitations": [
            "Session and diagnostic samples are sequential, not atomic.",
            "Server-local request times and UTC sample times are not compared as one clock.",
            "Session ID and login time do not prove the captured SQL is the intended statement.",
            "MATCHED_AT_SAMPLE does not mean both handle pairs were present.",
            "grant_time and handles are JSON-only; HTML parity covers shared fields.",
        ],
        "matched_json": matched_json_path, "artifacts": saved,
    }


def main(argv: list[str]) -> int:
    try:
        if argv:
            raise Blocked("COMMAND_ARGUMENTS_NOT_ACCEPTED")
        _runtime_gate()
        instance = Inventory.load(ROOT / "config" / "inventory.yaml").get("sqlserver2022")
        _target(instance)
        credential = _monitor_credential(instance.credential_alias)
        print("DBeaver sa session ID (run SELECT @@SPID in that session): ",
              end="", file=sys.stderr, flush=True)
        raw_id = sys.stdin.readline().strip()
        if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 5:
            raise Blocked("INVALID_SESSION_ID")
        print("AdventureWorks2022 database ID (run SELECT DB_ID() in that session): ",
              end="", file=sys.stderr, flush=True)
        raw_database_id = sys.stdin.readline().strip()
        if (not raw_database_id.isascii() or not raw_database_id.isdecimal()
                or len(raw_database_id) > 10):
            raise Blocked("INVALID_DATABASE_ID")
        print("Reviewed SQL handle from the same workload plan (Enter if unavailable): ",
              end="", file=sys.stderr, flush=True)
        reviewed_sql = _optional_handle(sys.stdin.readline().strip())
        print("Reviewed plan handle from the same workload plan (Enter if unavailable): ",
              end="", file=sys.stderr, flush=True)
        reviewed_plan = _optional_handle(sys.stdin.readline().strip())
        result = run(int(raw_id), int(raw_database_id), instance, credential,
                     reviewed_sql, reviewed_plan)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("result") == "GRANTED_OBSERVED_REVIEW_REQUIRED" else 2
    except Blocked as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc), "e2e": "NOT_VERIFIED"}))
        return 2
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"result": "BLOCKED", "reason": "LOCAL_OPERATION_FAILED",
                          "e2e": "NOT_VERIFIED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
