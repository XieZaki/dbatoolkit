"""Opt-in MySQL 8.4.11 SELECT timeout acceptance on the approved Windows host."""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, NoReturn, cast

from dba.collectors import CollectorResult
from dba.connection import resolve_connection_settings
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import (
    Credential,
    CredentialProvider,
    KeyringCredentialStore,
    validate_monitoring_credential,
)
from dba.database_errors import sanitize_vendor_text
from dba.encrypted_credentials import CredentialResolver, EncryptedFileCredentialStore
from dba.errors import ToolkitError, database_error_log_fields
from dba.incident import build_incident_summary
from dba.inventory import Inventory
from dba.logging import SafeFormatter
from dba.mysql import (
    _MYSQL_SERVER_RESPONSE_MARGIN_MS,
    _MYSQL_SESSION_SETUP_RESERVE_MS,
    Connection,
    Cursor,
    MySQLAdapter,
)
from dba.mysql_privileges import parse_mysql_grant, split_current_user
from dba.mysql_slow import build_slow_report
from dba.presentation import render_human, render_json
from dba.registry import SqlScriptRegistry, fetch_bounded_rows
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.normalizers import display_message, display_value
from dba.security import check_sql
from dba.settings import MySQLSettings, apply_diagnostic_settings
from dba.sql_timeout import SqlDeadline, SqlDeadlineExpired

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures") / "mysql_timeout" / "mysql"
TARGET = ROOT / "config/inventory/mysql/mysql.test.yaml"
SESSION_SET = "SET SESSION max_execution_time = %s"
FETCH_FIXTURE = "timeout_fetch"
NAMES = ("timeout_identity", "timeout_session", "timeout_hint", FETCH_FIXTURE)
FIXTURE_SQL = {
    "timeout_identity": "SELECT CONNECTION_ID() AS connection_id, VERSION() AS version, "
    "@@version_comment AS product, @@SESSION.max_execution_time AS session_limit_ms",
    "timeout_session": "SELECT 1 AS timeout_session_probe WHERE SLEEP(3) = 0",
    "timeout_hint": "SELECT /*+ MAX_EXECUTION_TIME(600) */ 1 AS timeout_hint_probe WHERE SLEEP(3) = 0",
    FETCH_FIXTURE: "SELECT 1 AS timeout_fetch_probe UNION ALL SELECT 2 + SLEEP(3)",
}
FIXTURE_LIMITS = {
    "timeout_identity": (4, 2, 1024),
    "timeout_session": (1, 2, 1024),
    "timeout_hint": (4, 2, 1024),
    FETCH_FIXTURE: (2, 4, 1024),
}
CURRENT = """
SELECT THREAD_ID, EVENT_ID, END_EVENT_ID, SQL_TEXT
FROM performance_schema.events_statements_current
WHERE THREAD_ID = %s
"""
HISTORY = """
SELECT THREAD_ID, EVENT_ID, END_EVENT_ID, SQL_TEXT
FROM performance_schema.events_statements_history
WHERE THREAD_ID = %s AND EVENT_ID = %s LIMIT 1
"""
HISTORY_LONG = HISTORY.replace("events_statements_history", "events_statements_history_long")
HISTORY_PROBE = """
SELECT THREAD_ID, EVENT_ID, END_EVENT_ID, SQL_TEXT
FROM performance_schema.events_statements_history
WHERE THREAD_ID = %s
ORDER BY EVENT_ID DESC LIMIT 10
"""
THREAD = """
SELECT THREAD_ID, INSTRUMENTED, HISTORY
FROM performance_schema.threads
WHERE PROCESSLIST_ID = %s
"""
INSTRUMENTS = """
SELECT NAME, ENABLED, TIMED FROM performance_schema.setup_instruments
WHERE NAME = 'statement/sql/select'
"""
CONSUMERS = """
SELECT NAME, ENABLED FROM performance_schema.setup_consumers
WHERE NAME IN ('global_instrumentation', 'thread_instrumentation',
               'events_statements_current', 'events_statements_history',
               'events_statements_history_long')
"""
REQUIRED_CONSUMERS = (
    "global_instrumentation", "thread_instrumentation",
    "events_statements_current", "events_statements_history",
)
REQUIRED_READ_OBJECTS = (
    "performance_schema.threads",
    "performance_schema.events_statements_current",
    "performance_schema.events_statements_history",
    "performance_schema.setup_consumers",
    "performance_schema.setup_instruments",
)
# Match the production Adapter's explicit server-first timeout budget policy.
OBSERVATION_MARGIN_MS = _MYSQL_SERVER_RESPONSE_MARGIN_MS
READ_ONLY_GRANTS = frozenset({"USAGE", "SELECT", "PROCESS", "REPLICATION CLIENT", "SHOW VIEW"})

class GateError(RuntimeError):
    """Fixed, safe acceptance gate message."""


class ResolvedCredentialProvider(CredentialProvider):
    """Pass the CLI-resolved credential to each Adapter without resolving it again."""

    def __init__(self, credential: Credential, source: str) -> None:
        self.credential = credential
        self.last_backend = source

    def get_credential(self, alias: str) -> Credential:
        return self.credential


class UnavailableCredentialProvider(CredentialProvider):
    """Disable CLI interactive enrollment during an automated acceptance run."""

    def get_credential(self, alias: str) -> Credential:
        raise ToolkitError("CredentialUnavailable", "No credential was resolved from an existing store.")


def reject_credential_prompt(database_id: str, alias: str) -> Credential:
    raise ToolkitError("CredentialUnavailable", "No credential was resolved from an existing store.")


def resolve_acceptance_credential(
    asset: DatabaseInstance, backend: str, store_path: Path | None,
) -> ResolvedCredentialProvider:
    from dba.sqlite_credentials import SQLiteCredentialStore

    store = (SQLiteCredentialStore(store_path) if backend == "sqlite"
             else EncryptedFileCredentialStore(store_path))
    resolver = CredentialResolver(
        KeyringCredentialStore(), store, UnavailableCredentialProvider(), backend,
        database_id=asset.id, credential_prompt=reject_credential_prompt,
    )
    credential = resolver.get_credential(asset.credential_alias)
    try:
        validate_monitoring_credential(credential)
    except ValueError:
        raise ToolkitError("CredentialRejected", "Resolved credential is not valid for monitoring.") from None
    source = resolver.last_backend or "Unknown Credential Provider"
    return ResolvedCredentialProvider(credential, source)


def require_host() -> None:
    if (sys.platform != "win32" or Path.cwd() != ROOT
            or Path(sys.executable).resolve() != (ROOT / ".venv/Scripts/python.exe").resolve()
            or not (ROOT / ".venv/Scripts/dba.exe").is_file()):
        raise GateError("Use the project Windows virtual environment from the repository root")
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    venv_python = str(Path(sys.executable)).replace("'", "''")
    probe = (
        "$ErrorActionPreference='Stop'; "
        "if($PSVersionTable.PSEdition -ne 'Desktop'){exit 2}; "
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={os.getpid()}'; "
        "$parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$p.ParentProcessId); "
        f"if($parent.ExecutablePath -eq '{venv_python}'){{"
        "$parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$parent.ParentProcessId)}; "
        "if($parent.ExecutablePath -ne "
        r"(Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')){exit 3}; "
        "if(-not (Get-CimInstance Win32_OperatingSystem)){exit 4}"
    )
    check = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True, timeout=10, check=False,
    )
    if check.returncode:
        raise GateError("Authorized Windows PowerShell Desktop host verification failed")


def load_asset(path: Path) -> DatabaseInstance:
    if path.resolve() != TARGET.absolute() or not path.is_file():
        raise GateError("Only the designated single-asset Inventory is permitted")
    inventory = Inventory.load(path)
    if len(inventory.instances) != 1:
        raise GateError("Inventory must contain exactly one asset")
    asset = inventory.instances[0]
    if (asset.id, asset.db_type, asset.host, asset.port, asset.environment, asset.enabled) != (
        "mysql84", DatabaseType.MYSQL, "127.0.0.1", 3306, "test", True
    ):
        raise GateError("Designated MySQL test asset did not match")
    return replace(asset, database="information_schema") if asset.database is None else asset


def stage_registry(root: Path) -> SqlScriptRegistry:
    destination = root / "mysql"
    destination.mkdir()
    for source in (ROOT / "sql/mysql", FIXTURES):
        for path in source.iterdir():
            if path.suffix in {".sql", ".yaml"}:
                target = destination / path.name
                if target.exists() or path.is_symlink():
                    raise GateError("Fixture collides with the formal Registry or is a symlink")
                shutil.copyfile(path, target)
    registry = SqlScriptRegistry(root)
    for name in NAMES:
        script = registry.get(name, DatabaseType.MYSQL)
        registry.authorize(script, DatabaseType.MYSQL)
        timeout, max_rows, max_bytes = FIXTURE_LIMITS[name]
        if (" ".join(script.text.split()) != FIXTURE_SQL[name]
                or script.metadata.timeout_seconds != timeout
                or script.metadata.max_rows != max_rows
                or script.metadata.max_result_bytes != max_bytes):
            raise GateError("Fixture does not match the reviewed statement and bounds")
    return registry


def enable_fetch_fixture_streaming(adapter: MySQLAdapter) -> None:
    """Opt only this acceptance Adapter into SSCursor for its reviewed Fetch fixture."""
    script = adapter.registry.get(FETCH_FIXTURE, DatabaseType.MYSQL)
    adapter.registry.authorize(script, DatabaseType.MYSQL)
    if script.metadata.name != FETCH_FIXTURE or script.text.strip() != FIXTURE_SQL[FETCH_FIXTURE]:
        raise GateError("Fetch fixture is not the reviewed streaming statement")
    adapter.STREAMING_SCRIPTS = frozenset((*adapter.STREAMING_SCRIPTS, FETCH_FIXTURE))


def query(adapter: MySQLAdapter, name: str) -> CollectorResult:
    return adapter.execute_readonly(adapter.registry.get(name, DatabaseType.MYSQL))


def readonly_privilege_evidence(adapter: MySQLAdapter) -> dict[str, Any]:
    """Accept any Inventory-selected account only when its effective grants are read-only."""
    current_user = getattr(adapter, "current_user", None)
    if not isinstance(current_user, str) or not current_user.strip():
        return {"safe": False, "reason": "current_user_unavailable"}

    # MySQLAdapter exposes this classifier after SHOW GRANTS and CURRENT_ROLE().
    # It intentionally rejects unresolved roles and unclassified dynamic grants.
    summary = getattr(adapter, "privilege_classification", None)
    if not isinstance(summary, dict) or summary.get("classification") != "NON_PRIVILEGED":
        return {"safe": False, "reason": "privilege_classification_not_non_privileged"}
    evidence = summary
    required_empty = ("high_risk_privileges", "write_privileges", "unclassified_dynamic_privileges")
    if any(evidence.get(name) != [] for name in required_empty):
        return {"safe": False, "reason": "privileged_or_unclassified_grant_detected"}
    active_roles = evidence.get("active_roles")
    role_grants_present = evidence.get("role_grants_present")
    if (not isinstance(active_roles, str) or active_roles.strip().upper() != "NONE"
            or role_grants_present is not False):
        # Existing Adapter treats unresolved/role-based grants as UNKNOWN.
        # Do not claim this auxiliary gate can prove their effective safety.
        return {"safe": False, "reason": "role_state_not_proven_safe"}

    return {
        "safe": True,
        "classification": "NON_PRIVILEGED",
        "current_user_present": True,
        "write_privileges": [],
        "high_risk_privileges": [],
        "active_roles": active_roles,
        "role_grants_present": role_grants_present,
        "unclassified_dynamic_privileges": [],
    }


def identity(adapter: MySQLAdapter, audit_records: list[dict[str, Any]] | None = None
             ) -> tuple[Connection, int, CollectorResult]:
    if not readonly_privilege_evidence(adapter)["safe"]:
        raise GateError("The Adapter did not prove that the monitoring account is read-only")
    # Fail closed for grants the general-purpose Adapter classifier does not categorize.
    grants = query(adapter, "monitor_grants")
    if (not grants.success or grants.truncated or not grants.rows
            or any(not grant_is_readonly(str(next(iter(row.values()), ""))) for row in grants.rows)):
        raise GateError("The monitoring account has unverified or non-read-only grants")
    result = query(adapter, "timeout_identity")
    binding = adapter._session_identity
    row = result.rows[0] if result.success and len(result.rows) == 1 else {}
    audit_limit = None
    if audit_records is not None:
        identity_audit = next((item for item in reversed(audit_records)
                               if item.get("sql_script") == "timeout_identity"), None)
        audit_limit = identity_audit.get("server_execution_limit_ms") if identity_audit else None
    if (binding is None or binding[0] is not adapter._connection or row.get("connection_id") != binding[1]
            or row.get("version") != "8.4.11" or adapter.get_server_version() != "8.4.11"
            or not str(row.get("product", "")).startswith("MySQL Community Server")
            or not adapter._server_execution_limit or type(row.get("session_limit_ms")) is not int
            or row["session_limit_ms"] <= 0
            or (audit_records is not None and row.get("session_limit_ms") != audit_limit)):
        raise GateError("Live product, read-only account, SESSION limit or connection identity did not match")
    return binding[0], binding[1], result


def grant_is_readonly(statement: str) -> bool:
    grant = parse_mysql_grant(statement)
    return (grant.scope_type in {"GLOBAL", "DATABASE", "TABLE"}
            and not grant.role_grant and not grant.grant_option
            and bool(grant.privileges) and READ_ONLY_GRANTS.issuperset(grant.privileges))


class TimedCursor:
    """Record Execute and per-batch Fetch boundaries without logging SQL values."""

    def __init__(self, cursor: Cursor, adapter: MySQLAdapter, sql: str,
                 events: list[dict[str, Any]], deadlines: list[SqlDeadline],
                 remaining_at_creation_ms: float | None,
                 requested_cursor: Any | None) -> None:
        self.cursor = cursor
        self.adapter = adapter
        self.sql = sql
        self.events = events
        self.deadlines = deadlines
        self.remaining_at_creation_ms = remaining_at_creation_ms
        self.requested_cursor = requested_cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)

    def execute(self, sql: str, parameters: tuple[Any, ...] | None = None) -> Any:
        if sql not in {SESSION_SET, self.sql}:
            raise GateError("Unexpected statement in timeout exercise")
        sock = self.adapter._deadline_socket
        deadline = sock._deadline if sock is not None else None
        if deadline is None:
            raise GateError("Production socket deadline unavailable")
        event: dict[str, Any] = {
            "operation": "session" if sql == SESSION_SET else "select",
            "execute_start_monotonic": time.monotonic(),
            "deadline_start_monotonic": deadline.started_at,
            "deadline_end_monotonic": deadline.expires_at,
            "remaining_before_ms": deadline.remaining() * 1000,
            "same_deadline_as_setup": not self.deadlines or deadline is self.deadlines[0],
            "cursor_class": type(self.cursor).__name__,
            "streaming_cursor_requested": (self.requested_cursor is not None
                                           and self.requested_cursor is self.adapter._streaming_cursor),
        }
        self.deadlines.append(deadline)
        if sql == SESSION_SET:
            event["server_execution_limit_ms"] = parameters[0] if parameters else None
            event["remaining_at_cursor_creation_ms"] = self.remaining_at_creation_ms
        self.events.append(event)
        event["execute_status"] = "FAILED"
        try:
            result = self.cursor.execute(sql, parameters)
            event["execute_status"] = "OK"
            return result
        except TimeoutError:
            event["socket_timeout_phase"] = "Execute"
            raise
        finally:
            event["execute_end_monotonic"] = time.monotonic()
            event["elapsed_ms"] = (event["execute_end_monotonic"] - event["execute_start_monotonic"]) * 1000
            event["remaining_after_ms"] = deadline.remaining() * 1000

    def fetchmany(self, size: int) -> Any:
        sock = self.adapter._deadline_socket
        deadline = sock._deadline if sock is not None else None
        if deadline is None:
            raise GateError("Production socket deadline unavailable during Fetch")
        event: dict[str, Any] = {
            "operation": "fetch",
            "fetch_start_monotonic": time.monotonic(),
            "remaining_before_ms": deadline.remaining() * 1000,
            "same_deadline_as_setup": bool(self.deadlines and deadline is self.deadlines[0]),
            "cursor_class": type(self.cursor).__name__,
            "streaming_cursor_requested": (self.requested_cursor is not None
                                           and self.requested_cursor is self.adapter._streaming_cursor),
            "batch_size": size,
            "fetch_status": "FAILED",
        }
        self.events.append(event)
        try:
            rows = self.cursor.fetchmany(size)
            event["fetch_status"] = "OK"
            event["rows_received"] = len(rows)
            return rows
        except BaseException as exc:
            event["fetch_error_type"] = type(exc).__name__
            if isinstance(exc, TimeoutError):
                event["socket_timeout_phase"] = "Fetch"
            raise
        finally:
            event["fetch_end_monotonic"] = time.monotonic()
            event["elapsed_ms"] = (event["fetch_end_monotonic"] - event["fetch_start_monotonic"]) * 1000
            event["remaining_after_ms"] = deadline.remaining() * 1000


def timed_query(adapter: MySQLAdapter, connection: Connection, name: str,
                events: list[dict[str, Any]]) -> CollectorResult:
    script = adapter.registry.get(name, DatabaseType.MYSQL)
    cursor = connection.cursor
    sock = adapter._deadline_socket
    deadlines: list[SqlDeadline] = []
    if sock is None:
        raise GateError("Production socket deadline unavailable")

    def instrumented(*args: Any, **kwargs: Any) -> TimedCursor:
        created = cursor(*args, **kwargs)
        remaining = sock._deadline.remaining() * 1000 if sock._deadline else None
        requested_cursor = args[0] if args else None
        return TimedCursor(created, adapter, script.text, events, deadlines, remaining,
                           requested_cursor)

    sendall = sock.sendall
    socket_error = sock._expired_socket_error

    def record_send(data: bytes) -> None:
        sendall(data)
        if (events and "execute_end_monotonic" not in events[-1]
                and "send_completed_monotonic" not in events[-1]):
            events[-1]["send_completed_monotonic"] = time.monotonic()
            events[-1]["remaining_at_send_ms"] = sock._deadline.remaining() * 1000 if sock._deadline else 0

    def record_socket_timeout(exc: OSError) -> NoReturn:
        # The Adapter turns these errors into the same result as a local deadline.
        if events and ("execute_end_monotonic" not in events[-1] or sock._phase == "Fetch"):
            events[-1]["socket_timeout_phase"] = sock._phase
        socket_error(exc)

    setattr(connection, "cursor", instrumented)
    setattr(sock, "sendall", record_send)
    setattr(sock, "_expired_socket_error", record_socket_timeout)
    try:
        return adapter.execute_readonly(script)
    finally:
        delattr(connection, "cursor")
        delattr(sock, "sendall")
        delattr(sock, "_expired_socket_error")


def observer_read(adapter: MySQLAdapter, sql: str, args: tuple[Any, ...] = (),
                  until: float | None = None) -> list[tuple[Any, ...]]:
    if sql not in {CURRENT, HISTORY, HISTORY_LONG, HISTORY_PROBE, THREAD, INSTRUMENTS, CONSUMERS}:
        raise GateError("Unexpected observer statement")
    check_sql(sql, DatabaseType.MYSQL)
    connection, sock = adapter._connection, adapter._deadline_socket
    if connection is None or sock is None:
        raise GateError("Observer connection or bounded socket unavailable")
    now = time.monotonic()
    previous, phase = sock._deadline, sock._phase
    sock.set_deadline(SqlDeadline(1, now if until is None else min(now, until - 1)))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(sql, args)
            deadline = sock._deadline
            fetched = fetch_bounded_rows(cursor, 16, 16_384, deadline=deadline)
            if fetched.row_limit_reached or fetched.byte_limit_reached:
                raise GateError("Observer result exceeded the reviewed bounds")
            return [tuple(row) for row in fetched.rows]
        finally:
            cursor.close()
    except Exception as exc:
        if isinstance(exc, (SqlDeadlineExpired, OSError)) or error_code(exc) in {2006, 2013}:
            adapter._close()
        raise
    finally:
        sock.set_deadline(previous, phase)


def normalize_sql_text(sql: Any) -> str:
    return " ".join(str(sql or "").split()).rstrip(";")


class Observation:
    def __init__(self, sql: str, thread_id: int | None = None) -> None:
        self.sql = normalize_sql_text(sql)
        self.thread_id = thread_id
        self.key: tuple[int, int] | None = None
        self.end_event_id: int | None = None
        self.active_at: float | None = None
        self.ended_at: float | None = None
        self.currently_running = False
        self.connection_visibility = "UNKNOWN"
        self.status = "INSUFFICIENT"

    def sample(self, rows: list[tuple[Any, ...]], *, historical: bool = False) -> None:
        if not historical:
            self.currently_running = False
            if self.key is not None and self.status == "RUNNING":
                self.status = "HISTORY_NOT_VISIBLE"
        for thread, event, end, sql in rows:
            if (normalize_sql_text(sql) != self.sql or type(thread) is not int or type(event) is not int
                    or thread <= 0 or event <= 0 or self.thread_id not in {None, thread}):
                continue
            key = (thread, event)
            if self.key is None and end is None and not historical:
                self.key, self.active_at = key, time.monotonic()
            if self.key == key and end is None and not historical:
                self.currently_running = True
                self.status = "RUNNING"
            if self.key == key and type(end) is int and end >= event and self.ended_at is None:
                self.end_event_id = end
                self.ended_at = time.monotonic()
                self.currently_running = False
                self.status = "ENDED"

    def record_connection(self, rows: list[tuple[Any, ...]]) -> None:
        matching = [row for row in rows if row and type(row[0]) is int]
        if not matching:
            self.connection_visibility = "DISAPPEARED"
            if self.key is not None and self.status != "ENDED":
                self.status = "CONNECTION_DISAPPEARED"
            return
        thread_id = matching[0][0]
        expected_thread = self.key[0] if self.key is not None else self.thread_id
        if expected_thread is not None and thread_id != expected_thread:
            self.connection_visibility = "REBOUND"
            if self.key is not None and self.status != "ENDED":
                self.status = "CONNECTION_ID_REBOUND"
            return
        self.connection_visibility = "PRESENT"
        if self.key is not None and not self.currently_running and self.status != "ENDED":
            self.status = "HISTORY_NOT_VISIBLE"

    def evidence(self) -> dict[str, Any]:
        return {"status": self.status,
                "event_lifecycle_status": self.status,
                "connection_visibility": self.connection_visibility,
                "thread_id": self.key[0] if self.key else self.thread_id,
                "event_id": self.key[1] if self.key else None,
                "end_event_id": self.end_event_id, "sql": self.sql,
                "active_at": self.active_at, "ended_at": self.ended_at}


def error_code(exc: Exception) -> int | None:
    return exc.args[0] if exc.args and type(exc.args[0]) is int else None


def safe_database_error(exc: Exception, redactions: tuple[str, ...] = ()) -> dict[str, Any]:
    message = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], str) else str(exc)
    return {
        "vendor_code": error_code(exc),
        "message": hide_monitor_identity(sanitize_vendor_text(message), redactions),
        "error_class": type(exc).__name__,
    }


def denied_observer_objects(errors: list[dict[str, Any]]) -> list[str]:
    return [name for name in REQUIRED_READ_OBJECTS
            if any(item.get("object") == name and item.get("vendor_code") in {1142, 1143}
                   for item in errors)]


def observer_preflight(observer: MySQLAdapter, connection_id: int,
                       redactions: tuple[str, ...] = ()) -> dict[str, Any]:
    """Prove read access and live per-thread statement history before the slow SELECT."""
    evidence: dict[str, Any] = {
        "status": "INSUFFICIENT",
        "required_read_objects": list(REQUIRED_READ_OBJECTS),
        "denied_select_objects": [],
        "required_server_configuration": {
            "consumers": {name: "YES" for name in REQUIRED_CONSUMERS},
            "instrument": {"name": "statement/sql/select", "enabled": "YES"},
            "target_thread": {"instrumented": "YES", "history": "YES"},
            "toolkit_changes_server_configuration": False,
        },
        "errors": [],
    }
    reads: dict[str, list[tuple[Any, ...]]] = {}
    for obj, sql in (("setup_consumers", CONSUMERS), ("setup_instruments", INSTRUMENTS)):
        try:
            reads[obj] = observer_read(observer, sql)
        except Exception as exc:
            evidence["errors"].append({"object": f"performance_schema.{obj}",
                                       **safe_database_error(exc, redactions)})
            evidence["denied_select_objects"] = denied_observer_objects(evidence["errors"])
            return evidence
    consumers = {str(name): str(enabled) for name, enabled in reads["setup_consumers"]}
    instruments = {str(name): {"enabled": str(enabled), "timed": str(timed)}
                   for name, enabled, timed in reads["setup_instruments"]}
    evidence.update(consumers=consumers, instruments=instruments)

    try:
        thread_rows = observer_read(observer, THREAD, (connection_id,))
        if len(thread_rows) != 1:
            evidence["errors"].append({"object": "performance_schema.threads",
                                       "reason": "target_thread_not_visible"})
            return evidence
        thread_id, instrumented, history_enabled = thread_rows[0]
        if type(thread_id) is not int or thread_id <= 0:
            raise GateError("Observer could not bind the target thread ID")
        evidence["target_thread"] = {
            "thread_id": thread_id,
            "instrumented": str(instrumented),
            "history": str(history_enabled),
        }
    except Exception as exc:
        evidence["errors"].append({"object": "performance_schema.threads",
                                   **safe_database_error(exc, redactions)})
        evidence["denied_select_objects"] = denied_observer_objects(evidence["errors"])
        return evidence

    # Exercise SELECT access to the current table and prove that the target
    # connection's already-finished identity probe was actually retained in
    # the per-thread history table. This avoids requiring the global history_long
    # consumer, which is commonly disabled by default.
    try:
        observer_read(observer, CURRENT, (thread_id,))
    except Exception as exc:
        evidence["errors"].append({"object": "performance_schema.events_statements_current",
                                   **safe_database_error(exc, redactions)})

    retained: list[tuple[int, int]] = []
    try:
        history_probe_rows = observer_read(
            observer, HISTORY_PROBE, (thread_id,)
        )
        # This is a *preflight capability check*, not the slow-query stop
        # proof: identity has already finished. Check for its exact finished
        # event in the bound target THREAD_ID history (without pretending we
        # observed the identity query while it was running). The slow-query
        # proof below STILL requires current->same-key history transition.
        retained = [
            (event, end) for thread, event, end, sql in history_probe_rows
            if thread == thread_id and type(event) is int and event > 0
            and type(end) is int and end >= event
            and normalize_sql_text(sql) == normalize_sql_text(FIXTURE_SQL["timeout_identity"])
        ]
        evidence["target_history_probe"] = {
            "status": "CONFIRMED" if retained else "INSUFFICIENT",
            "matched_event_count": len(retained),
        }
        if not retained:
            evidence["errors"].append({
                "object": "performance_schema.events_statements_history",
                "reason": "target_identity_event_not_retained",
            })
    except Exception as exc:
        evidence["errors"].append({"object": "performance_schema.events_statements_history",
                                   **safe_database_error(exc, redactions)})

    missing_consumers = [name for name in REQUIRED_CONSUMERS if name not in consumers]
    disabled_consumers = [name for name in REQUIRED_CONSUMERS if consumers.get(name) != "YES"]
    select_instrument = instruments.get("statement/sql/select")
    instrument_ready = select_instrument is not None and select_instrument["enabled"] == "YES"
    thread_ready = str(instrumented) == "YES"
    history_ready = str(history_enabled) == "YES"
    evidence["missing_consumers"] = missing_consumers
    evidence["disabled_consumers"] = disabled_consumers
    evidence["select_instrument_ready"] = instrument_ready
    evidence["target_thread_instrumented"] = thread_ready
    evidence["target_thread_history_enabled"] = history_ready
    if disabled_consumers:
        evidence["errors"].append({"reason": "required_statement_consumer_disabled",
                                   "consumers": disabled_consumers})
    if not instrument_ready:
        evidence["errors"].append({"reason": "statement_sql_select_instrument_disabled_or_missing"})
    if not thread_ready:
        evidence["errors"].append({"reason": "target_thread_not_instrumented"})
    if not history_ready:
        evidence["errors"].append({"reason": "target_thread_history_not_enabled"})
    evidence["history_long_available"] = False
    if retained and not evidence["errors"] and consumers.get("events_statements_history_long") == "YES":
        try:
            long_rows = observer_read(observer, HISTORY_LONG, (thread_id, retained[0][0]))
            evidence["history_long_available"] = any(
                thread == thread_id and (event, end) in retained
                and normalize_sql_text(sql) == normalize_sql_text(FIXTURE_SQL["timeout_identity"])
                for thread, event, end, sql in long_rows
            )
        except Exception as exc:
            evidence["optional_history_long_error"] = {
                "object": "performance_schema.events_statements_history_long",
                **safe_database_error(exc, redactions),
            }
            if isinstance(exc, (SqlDeadlineExpired, OSError)) or error_code(exc) in {2006, 2013}:
                evidence["errors"].append({"reason": "observer_connection_lost"})
    evidence["denied_select_objects"] = denied_observer_objects(evidence["errors"])
    if not evidence["errors"]:
        evidence["status"] = "READY"
    return evidence


def observe(observer: MySQLAdapter, thread_id: int, connection_id: int, observation: Observation,
            consumers: dict[str, str], redactions: tuple[str, ...] = (), *,
            history_long: bool = False) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    if consumers.get("events_statements_current") != "YES" or \
            consumers.get("events_statements_history") != "YES":
        return {**observation.evidence(), "reason": "required_statement_consumer_unavailable"}
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and observation.status not in {
        "ENDED", "CONNECTION_DISAPPEARED", "CONNECTION_ID_REBOUND",
    }:
        observation.currently_running = False
        try:
            observation.sample(observer_read(observer, CURRENT, (thread_id,), deadline))
        except Exception as exc:
            problems.append({"source": "current", **safe_database_error(exc, redactions)})
            break
        if observation.key and observation.ended_at is None and time.monotonic() < deadline:
            try:
                observation.sample(observer_read(observer, HISTORY, observation.key, deadline), historical=True)
                if history_long and observation.ended_at is None and time.monotonic() < deadline:
                    observation.sample(
                        observer_read(observer, HISTORY_LONG, observation.key, deadline), historical=True,
                    )
            except Exception as exc:
                problems.append({"source": "history", **safe_database_error(exc, redactions)})
                break
        if observation.ended_at is None and time.monotonic() < deadline:
            try:
                observation.record_connection(observer_read(observer, THREAD, (connection_id,), deadline))
            except Exception as exc:
                problems.append({"source": "threads", **safe_database_error(exc, redactions)})
                break
        if observation.ended_at is None:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    return {**observation.evidence(), "observation_errors": problems}


def verify_database_session_visibility(observer: MySQLAdapter, connection_id: int,
                                       expected_thread_id: int, *, adapter_detached: bool,
                                       redactions: tuple[str, ...] = ()) -> dict[str, Any]:
    """Independently query threads after the target result; this is not query-stop proof."""
    try:
        rows = observer_read(observer, THREAD, (connection_id,))
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "source": "performance_schema.threads",
            "connection_id": connection_id,
            "expected_thread_id": expected_thread_id,
            "adapter_detached_at_probe": adapter_detached,
            "error": safe_database_error(exc, redactions),
        }
    if not rows:
        status = "ABSENT"
        observed_thread_id = None
    elif len(rows) != 1 or type(rows[0][0]) is not int:
        status = "UNKNOWN"
        observed_thread_id = None
    else:
        observed_thread_id = rows[0][0]
        status = ("PRESENT" if observed_thread_id == expected_thread_id
                  else "CONNECTION_ID_REBOUND")
    return {
        "status": status,
        "source": "performance_schema.threads",
        "connection_id": connection_id,
        "expected_thread_id": expected_thread_id,
        "observed_thread_id": observed_thread_id,
        "adapter_detached_at_probe": adapter_detached,
        "query_stop_confirmed": False,
    }


def classify(result: CollectorResult, trace: list[dict[str, Any]] | None = None) -> str:
    error = result.database_error or {}
    timeout = error.get("timeout") or {}
    if (result.error_type == "ServerTimeoutBudgetUnavailable"
            and error.get("error_category") == "TIMEOUT"
            and timeout.get("cancellation_status") == "NOT_APPLICABLE"):
        return "CLIENT_BUDGET_REFUSED"
    if (error.get("vendor_code"), error.get("error_category"),
            timeout.get("cancellation_status"), timeout.get("mechanism")) == (
        3024, "TIMEOUT", "CANCELLED_CONFIRMED", "MYSQL_MAX_EXECUTION_TIME"
    ) and timeout.get("execution_phase") in {"Execute", "Fetch"} and error.get("message"):
        return "SERVER_3024"
    if error.get("error_category") == "CONNECTION_LOST":
        return "CONNECTION_LOST"
    if error.get("error_category") == "TIMEOUT" and timeout.get("cancellation_status") == "CANCEL_STATUS_UNKNOWN":
        if trace and any(item.get("socket_timeout_phase") for item in trace):
            return "CLIENT_SOCKET_TIMEOUT"
        if trace is not None and error.get("message") == (
            "Client SQL deadline expired; database-side stop is unconfirmed."
        ):
            return "CLIENT_DEADLINE_TIMEOUT"
        return "CLIENT_TIMEOUT_UNSPECIFIED"
    return "UNEXPECTED_RESULT"


def session_timing_valid(trace: list[dict[str, Any]], audit: dict[str, Any] | None = None) -> bool:
    """Verify the Adapter reserved time around SET and before the SELECT send."""
    execute_events = [item for item in trace if item.get("operation") in {"session", "select"}]
    if ([item.get("operation") for item in execute_events] != ["session", "select"]
            or audit is None):
        return False
    setup, select = execute_events
    limit = setup.get("server_execution_limit_ms")
    remaining_at_creation = setup.get("remaining_at_cursor_creation_ms")
    client_remaining_before_set = audit.get("client_remaining_before_session_ms")
    setup_reserve = audit.get("session_setup_reserve_ms")
    response_margin = audit.get("server_timeout_response_margin_ms")
    sent = select.get("send_completed_monotonic")
    remaining_at_send = select.get("remaining_at_send_ms")
    remaining_before = select.get("remaining_before_ms")
    return (
        type(limit) is int and limit > 0
        and type(client_remaining_before_set) is int
        and setup_reserve == _MYSQL_SESSION_SETUP_RESERVE_MS
        and response_margin == _MYSQL_SERVER_RESPONSE_MARGIN_MS
        and limit == client_remaining_before_set - setup_reserve - response_margin
        and isinstance(remaining_at_creation, (int, float))
        and limit < math.floor(remaining_at_creation)
        and setup["execute_status"] == "OK"
        and select["same_deadline_as_setup"]
        and isinstance(sent, float) and isinstance(remaining_at_send, (int, float))
        and isinstance(remaining_before, (int, float))
        and remaining_before > limit + response_margin
        and remaining_at_send > limit
        and setup["execute_start_monotonic"] <= setup["execute_end_monotonic"]
        <= select["execute_start_monotonic"] <= sent <= select["execute_end_monotonic"]
        and 0 < remaining_at_send <= remaining_before <= setup["remaining_after_ms"]
        <= setup["remaining_before_ms"]
    )


def server_first_window(trace: list[dict[str, Any]], audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report the recorded server/client budget gap; event evidence stays separate."""
    if not isinstance(audit, dict) or not session_timing_valid(trace, audit):
        return {"status": "NOT_OBSERVED", "margin_ms": None,
                "required_margin_ms": OBSERVATION_MARGIN_MS}
    setup, select = (item for item in trace if item.get("operation") in {"session", "select"})
    margin_ms = math.floor(select["remaining_at_send_ms"] - setup["server_execution_limit_ms"])
    return {
        "status": ("SERVER_BEFORE_CLIENT_WINDOW_OBSERVED" if margin_ms >= OBSERVATION_MARGIN_MS
                   else "CLIENT_MAY_EXPIRE_FIRST"),
        "margin_ms": margin_ms,
        "required_margin_ms": OBSERVATION_MARGIN_MS,
        "note": ("This is a recorded timing margin, not proof of server-side stop under arbitrary "
                 "network delay. Server error and observer lifecycle evidence are reported separately."),
    }


class CaptureAudit(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []
        self.redactions: tuple[str, ...] = ()
        self.setFormatter(SafeFormatter())

    def add_redactions(self, *values: str | None) -> None:
        candidates = {*self.redactions, *(value for value in values if value)}
        for value in tuple(candidates):
            identity = split_current_user(value)
            if identity is not None:
                user, host = identity
                candidates.update({
                    f"'{user}'@'{host}'", f"'{user}'@{host}", f"{user}@'{host}'",
                    f"`{user}`@`{host}`", f"'{user}'", f"`{user}`",
                })
        self.redactions = tuple(sorted(candidates, key=len, reverse=True))
        self.records = [hide_monitor_identity(record, self.redactions) for record in self.records]

    def emit(self, record: logging.LogRecord) -> None:
        event = json.loads(self.format(record))
        fields = getattr(record, "event", None)
        if isinstance(fields, dict):
            for key in (
                "server_execution_limit_ms", "client_remaining_before_session_ms",
                "session_setup_reserve_ms", "server_timeout_response_margin_ms",
                "session_setup_elapsed_ms", "client_remaining_before_execute_ms", "execution_phase",
            ):
                if key in fields:
                    event[key] = fields[key]
        self.records.append(hide_monitor_identity(event, self.redactions))


def hide_monitor_identity(value: Any, redactions: tuple[str, ...] = ()) -> Any:
    sensitive_keys = {"user", "username", "current_user", "monitor_user", "credential_alias"}
    if isinstance(value, str):
        result = value
        for identity_value in redactions:
            result = re.sub(r"(?<![\w])" + re.escape(identity_value) + r"(?![\w])", "[REDACTED]", result)
        return result
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if str(key).lower() in sensitive_keys else
                      hide_monitor_identity(item, redactions)) for key, item in value.items()}
    if isinstance(value, list):
        return [hide_monitor_identity(item, redactions) for item in value]
    return value


@contextmanager
def capture_audit() -> Iterator[CaptureAudit]:
    logger = logging.getLogger("dba")
    old = logger.handlers[:], logger.level, logger.propagate
    handler = CaptureAudit()
    logger.handlers = [handler]
    try:
        yield handler
    finally:
        logger.handlers, logger.level, logger.propagate = old
        handler.close()


def render_evidence(asset: DatabaseInstance, settings: MySQLSettings, baseline: CollectorResult,
                    result: CollectorResult, audit: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, Any]]:
    report = build_slow_report(
        replace(asset, credential_alias="[REDACTED]"), (baseline, result), settings, "8.4.11",
        required_collectors=(baseline.collector_name, result.collector_name),
    )
    payload = {"success": result.success, "collector_name": result.collector_name, "error": result.database_error,
               "error_type": result.error_type, "error_message": result.error_message}
    json_text = render_json({**payload, "report": asdict(report), "baseline": asdict(baseline)})
    human = render_human("diagnose", payload)
    markup = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    error = result.database_error or {}
    failures = [e for e in audit if e.get("sql_script") == result.collector_name
                and e.get("result") == "failed"]
    projection = database_error_log_fields(error)
    keys = ("vendor_code", "error_category", "execution_phase", "cancellation_status",
            "connection_status", "script_path", "sanitized_message")
    checks = {
        "json": json.loads(json_text).get("error") == result.database_error
        and json.loads(json_text)["report"]["instance"]["credential_alias"] == "[REDACTED]",
        "jsonl": len(failures) == 1 and all(failures[0].get(k) == projection.get(k) for k in keys),
        "html": all(html.escape(str(projection[k])) in markup for k in keys if projection.get(k) is not None),
        "cli": all(
            (str(projection[k]) in human or display_message(str(display_value(projection[k]))) in human)
            for k in keys if projection.get(k) is not None
        ),
    }
    if not error:
        checks = dict.fromkeys(checks, False)
    return {"json": json_text, "html": markup, "txt": human,
            "jsonl": "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in failures)}, checks


def exercise(target: MySQLAdapter, observer: MySQLAdapter, name: str,
             audit: CaptureAudit, output: Path) -> dict[str, Any]:
    if name == FETCH_FIXTURE:
        enable_fetch_fixture_streaming(target)
    old, connection_id, baseline = identity(target, audit.records)
    observer_binding = observer._session_identity
    if (observer_binding is None or observer_binding[0] is not observer._connection
            or observer_binding[0] is old or observer_binding[1] == connection_id):
        return {"case": name, "status": "BLOCKED", "reason": "observer_not_independently_bound"}
    gate = observer_preflight(observer, connection_id, audit.redactions)
    if gate["status"] != "READY":
        return {"case": name, "status": "BLOCKED", "observer": gate}
    thread_id = gate["target_thread"]["thread_id"]
    frozen_baseline = deepcopy(baseline)
    trace: list[dict[str, Any]] = []
    observation = Observation(target.registry.get(name, DatabaseType.MYSQL).text, thread_id)
    audit_start = len(audit.records)
    observer_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as executor:
        run = executor.submit(timed_query, target, old, name, trace)
        observed = observe(observer, thread_id, connection_id, observation,
                           gate["consumers"], audit.redactions,
                           history_long=gate["history_long_available"])
        observed["elapsed_ms"] = (time.monotonic() - observer_started) * 1000
        result = run.result()
    session_visibility = verify_database_session_visibility(
        observer, connection_id, thread_id, adapter_detached=target._connection is None,
        redactions=audit.redactions,
    )
    native = classify(result, trace)
    result = replace(result, database_error=hide_monitor_identity(result.database_error, audit.redactions),
                     error_message=hide_monitor_identity(result.error_message, audit.redactions))
    detail = result.database_error or {}
    timeout = detail.get("timeout") or {}
    # A detached Adapter, a closed socket, and an ended server event are separate facts.
    detached = target._connection is None and target._session_identity is None
    socket_closed = getattr(old, "open", None) is False
    queries = sum(e["operation"] == "select" for e in trace)
    fetched_before_failure = sum(
        int(event.get("rows_received", 0)) for event in trace if event.get("operation") == "fetch"
    )
    execution_audit = next((item for item in reversed(audit.records[audit_start:])
                            if item.get("sql_script") == name), None)
    audit_server_limit = execution_audit.get("server_execution_limit_ms") if execution_audit else None
    audit_limit_consistent = bool(trace and trace[0].get("server_execution_limit_ms") == audit_server_limit)
    session_timing = (session_timing_valid(trace, execution_audit)
                      if name == "timeout_session" else None)
    timing_window = (server_first_window(trace, execution_audit)
                     if name == "timeout_session" else None)
    artifacts, parity = render_evidence(target.instance, target.settings, baseline, result,
                                       audit.records[audit_start:])
    for suffix, content in artifacts.items():
        with (output / f"{name}.{suffix}").open("x", encoding="utf8") as stream:
            stream.write(content)
    recovery: dict[str, Any] = {"status": "NOT_EXECUTED"}
    if detached and not result.success:
        started = time.monotonic()
        try:
            target.preflight_checks.clear()
            recovery_audit_start = len(audit.records)
            target.connect()
            new, new_id, sample = identity(target, audit.records)
            successful_scripts = [record.get("sql_script") for record in audit.records[recovery_audit_start:]
                                  if record.get("result") == "ok"]
            full_preflight = all(script in successful_scripts
                                 for script in ("preflight", "preflight_roles", "monitor_grants", "connect"))
            recovery = {"status": "PASS" if new is not old and new_id != connection_id and full_preflight else "FAIL",
                        "connection_id": new_id, "query_elapsed_ms": sample.elapsed_ms,
                        "preflight": dict(target.preflight_checks), "preflight_observed": full_preflight,
                        "successful_scripts": successful_scripts}
        except Exception as exc:
            recovery = {"status": "FAIL", "error_class": type(exc).__name__,
                        "error_type": exc.error_type if isinstance(exc, ToolkitError) else None}
        recovery["elapsed_ms"] = (time.monotonic() - started) * 1000
    retained = (baseline == frozen_baseline
                and json.loads(artifacts["json"])["baseline"]["rows"] == list(baseline.rows))
    server_error_evidence = {
        "status": "CONFIRMED" if native == "SERVER_3024" else "NOT_CONFIRMED",
        "source": "MYSQL_MAX_EXECUTION_TIME_ERROR" if native == "SERVER_3024" else None,
        "vendor_code": detail.get("vendor_code"),
        "execution_phase": timeout.get("execution_phase"),
    }
    return {"case": name, "status": "EXECUTED", "observer": gate,
            "classification": native, "elapsed_ms": result.elapsed_ms,
            "connection_id": connection_id, "observer_connection_id": observer_binding[1],
            "trace": trace, "error": detail,
            "fetched_rows_before_failure": fetched_before_failure,
            "incomplete_result_discarded": (not result.success and result.row_count == 0
                                             and not result.rows),
            "server_stop": observed, "server_error_evidence": server_error_evidence,
            "adapter_detached": detached, "driver_socket_closed": socket_closed,
            "database_session_disappearance": session_visibility["status"],
            "database_session_disappearance_evidence": session_visibility,
            "select_attempts": queries,
            "session_timing_valid": session_timing,
            "server_first_window": timing_window,
            "audit_server_limit_ms": audit_server_limit,
            "audit_limit_consistent": audit_limit_consistent,
            "connection_disposition": timeout.get("connection_status"),
            "connection_disposition_consistent": timeout.get("connection_status") == "DISCARDED"
                and detached and socket_closed,
            "recovery": recovery,
            "baseline_retained": retained, "output_parity": parity}


def verified_case(case: dict[str, Any], expected_phase: str = "Execute") -> bool:
    return (
        case["status"] == "EXECUTED"
        and case["classification"] == "SERVER_3024"
        and case["error"]["timeout"]["execution_phase"] == expected_phase
        and (case["server_error_evidence"]["status"] == "CONFIRMED"
             or case["server_stop"]["status"] == "ENDED")
        and case["connection_disposition_consistent"]
        and case["recovery"]["status"] == "PASS"
        and case["select_attempts"] == 1
        and case["baseline_retained"]
        and all(case["output_parity"].values())
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--credential-backend",
                        choices=("auto", "os", "encrypted", "sqlite", "interactive"), default="auto")
    parser.add_argument("--credential-store-path", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory under reports")
    args = parser.parse_args()
    stage = "host"
    cases: list[dict[str, Any]] = []
    try:
        require_host()
        stage = "inventory"
        asset = load_asset(args.inventory)
        settings = cast(MySQLSettings, resolve_connection_settings(
            DatabaseType.MYSQL, asset.connection, apply_diagnostic_settings(
                MySQLSettings.load(args.settings) if args.settings else MySQLSettings(), args.settings)))
        output = args.output.resolve()
        if not output.is_relative_to(ROOT / "reports") or output == ROOT / "reports":
            raise GateError("Output must be a new subdirectory under reports")
        stage = "credentials"
        provider = resolve_acceptance_credential(
            asset, args.credential_backend, args.credential_store_path,
        )
        credential_source = provider.last_backend
        output.mkdir(parents=True, exist_ok=False)
        stage = "preflight"
        with TemporaryDirectory(prefix="mysql-timeout-registry-") as temp, capture_audit() as audit:
            registry = stage_registry(Path(temp))
            with MySQLAdapter(asset, provider, registry, settings) as target, MySQLAdapter(
                asset, provider, registry, settings
            ) as observer:
                audit.add_redactions(
                    target.current_user, observer.current_user, asset.credential_alias,
                )
                _, observer_id, _ = identity(observer, audit.records)
                if target._session_identity is None or target._session_identity[1] == observer_id:
                    raise GateError("Observer connection is not independent")
                stage = "session"
                first = exercise(target, observer, "timeout_session", audit, output)
                cases.append(first)
                if first.get("recovery", {}).get("status") == "PASS" or (
                    first["status"] == "EXECUTED" and first["classification"] == "UNEXPECTED_RESULT"
                    and target._connection is not None
                ):
                    stage = "hint"
                    hint = exercise(target, observer, "timeout_hint", audit, output)
                    cases.append(hint)
                    if hint.get("recovery", {}).get("status") == "PASS" or (
                        hint["status"] == "EXECUTED" and hint["classification"] == "UNEXPECTED_RESULT"
                        and target._connection is not None
                    ):
                        stage = "fetch"
                        cases.append(exercise(target, observer, FETCH_FIXTURE, audit, output))
        session_verified = (verified_case(first) and first["session_timing_valid"]
                            and first["audit_limit_consistent"])
        hint_case = next((case for case in cases if case.get("case") == "timeout_hint"), None)
        control_verified = hint_case is not None and verified_case(hint_case)
        fetch_case = next((case for case in cases if case.get("case") == FETCH_FIXTURE), None)
        fetch_verified = bool(
            fetch_case is not None and verified_case(fetch_case, "Fetch")
            and fetch_case["fetched_rows_before_failure"] > 0
            and fetch_case["incomplete_result_discarded"]
        )
        passed = (session_verified and control_verified and fetch_verified
                  and first["server_first_window"]["status"] == "SERVER_BEFORE_CLIENT_WINDOW_OBSERVED")
        status = "BLOCKED" if first["status"] == "BLOCKED" else "PASS" if passed else "PARTIAL"
        summary = {"evidence_type": "WINDOWS_LOCAL_REAL_E2E", "asset": "mysql84",
                   "server_version": "8.4.11", "cases": cases, "status": status,
                   "credential_source": credential_source,
                   "session_result": "VERIFIED" if session_verified else first["status"],
                   "adapter_modified_for_timeout_race": True,
                   "session_timeout_race": "KNOWN_LIMITATION_IN_PRODUCTION_ADAPTER",
                   "fetch": ({"status": "VERIFIED" if fetch_verified else
                              fetch_case["status"] if fetch_case else "NOT_EXECUTED",
                              "case": fetch_case} if fetch_case else {
                                  "status": "NOT_EXECUTED",
                                  "reason": "A prior case did not recover to a fresh preflighted connection",
                              }),
                   "hint_control": ("VERIFIED" if control_verified else hint_case["status"]
                                    if hint_case else "NOT_EXECUTED")}
        text = render_json(summary)
        with (output / "summary.json").open("x", encoding="utf8") as stream:
            stream.write(text + "\n")
        print(text)
        # A real 3024 proves one server-side outcome, but it does not erase an
        # unsafe server/client timeout ordering. Strict overall PASS requires
        # both the event evidence and an observed earlier server-time window.
        return 2 if status == "BLOCKED" else 0 if passed else 1
    except Exception as exc:
        # Never echo arbitrary exceptions, connection strings, or credential objects.
        detail = (str(exc) if isinstance(exc, GateError) else exc.error_type
                  if isinstance(exc, ToolkitError) else type(exc).__name__)
        response: dict[str, Any] = {"stage": stage, "status": "INCOMPLETE", "reason": detail,
                                    "cases": cases}
        if isinstance(exc, ToolkitError) and exc.database_error_payload() is not None:
            response["database_error"] = hide_monitor_identity(exc.database_error_payload())
        print(json.dumps(response, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
