"""Explicit read-only PostgreSQL 16.15 timeout acceptance on Windows Desktop."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from dba.cli import _CachedCredentialProvider, _effective_settings
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import (
    Credential,
    KeyringCredentialStore,
    PromptCredentialProvider,
    validate_monitoring_credential,
)
from dba.database_errors import sanitize_vendor_text
from dba.encrypted_credentials import CredentialResolver, EncryptedFileCredentialStore
from dba.errors import ToolkitError, database_error_log_fields
from dba.incident import build_incident_summary
from dba.inventory import Inventory
from dba.logging import SafeFormatter
from dba.postgresql import PostgreSQLAdapter, load_driver
from dba.postgresql_slow import build_slow_report
from dba.presentation import render_human, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.normalizers import display_label, display_message, display_value
from dba.settings import PostgreSQLSettings
from dba.sql_timeout import SqlDeadline
from dba.sqlite_credentials import SQLiteCredentialStore

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "config/inventory/postgressql/postgresql.test.yaml"
FIXTURES = Path(__file__).with_name("fixtures") / "postgresql_timeout" / "postgresql"
ENGINE = DatabaseType.POSTGRESQL
TIMEOUT_SECONDS = 2
OBSERVE_SECONDS = 4
FIXTURE_SHA256 = {
    "timeout_execute.sql": "5d7f6f60750e466f39e539260326107bae7cd42faf5cf1b2077952a6de1bac8a",
    "timeout_execute.yaml": "08ccda43a89515d1f8e515236983798813d96bdd21ddc45a59466bcbb8c68dd1",
    "timeout_identity.sql": "7e9976d582e06a3c00f7141be8dd45a47f356b583d73a47a786ce7416bca81a9",
    "timeout_identity.yaml": "abbbb3e0bfc58af55003bf13e814e14e47238b2230c4601d65b21cc1c1302939",
    "timeout_normal.sql": "5d9c3a493928b6edb6d3bea65adc2dd37b7e18d64b085fd661673a7ea270cf80",
    "timeout_normal.yaml": "f84d4c79173286ed28b85251f247af706c3638e77a942e4bbf6e4e139519c463",
    "timeout_observe.sql": "ed6c2f6fec0fcc1b08d7189d8bf9c70f8e37a98b15fa487c1d3a163ec09a3d32",
    "timeout_observe.yaml": "99c8b30f73841ddd526b6187d204a30fdf76e79159dd7794d492e3b23a7ae2dc",
}


def require_host() -> None:
    if (sys.platform != "win32" or Path.cwd() != ROOT
            or Path(sys.executable).resolve() != (ROOT / ".venv/Scripts/python.exe").resolve()):
        raise ToolkitError("HostRejected", "Use Windows PowerShell Desktop and the project venv.")
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    venv = str(ROOT / ".venv/Scripts/python.exe").replace("'", "''")
    probe = (
        "$ErrorActionPreference='Stop';"
        "if($PSVersionTable.PSEdition -ne 'Desktop'){exit 2};"
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={os.getpid()}';"
        "$parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$p.ParentProcessId);"
        f"if($parent.ExecutablePath -eq '{venv}'){{"
        "$parent=Get-CimInstance Win32_Process -Filter ('ProcessId='+$parent.ParentProcessId)};"
        "if($parent.ExecutablePath -ne (Join-Path $env:SystemRoot "
        r"'System32\WindowsPowerShell\v1.0\powershell.exe')){exit 3};"
        "if(-not (Get-CimInstance Win32_OperatingSystem)){exit 4}"
    )
    completed = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", probe],
                               capture_output=True, timeout=10, check=False)
    if completed.returncode:
        raise ToolkitError("HostRejected", "Windows PowerShell Desktop host verification failed.")


def load_asset(path: Path) -> DatabaseInstance:
    if path.resolve() != INVENTORY.absolute():
        raise ToolkitError("InventoryRejected", "Only the designated Inventory is supported.")
    instances = Inventory.load(path).instances
    if len(instances) != 1:
        raise ToolkitError("InventoryRejected", "Exactly one PostgreSQL test asset is required.")
    asset = instances[0]
    if (asset.id, asset.db_type, asset.host, asset.port, asset.environment, asset.enabled) != (
        "pg16", ENGINE, "127.0.0.1", 5432, "test", True,
    ):
        raise ToolkitError("InventoryRejected", "The designated test asset did not match.")
    if not asset.database:
        raise ToolkitError("InventoryRejected", "The Inventory must name its target database.")
    return asset


def reject_enrollment(database_id: str, alias: str) -> Credential:
    raise ToolkitError("CredentialUnavailable", "Resolve a saved monitoring credential first.")


def resolve_provider(asset: DatabaseInstance, backend: str,
                     path: Path | None) -> tuple[_CachedCredentialProvider, str]:
    store = SQLiteCredentialStore(path) if backend == "sqlite" else EncryptedFileCredentialStore(path)
    resolver = CredentialResolver(
        KeyringCredentialStore(), store, PromptCredentialProvider(), backend,
        database_id=asset.id, credential_prompt=reject_enrollment,
    )
    credential = resolver.get_credential(asset.credential_alias)
    validate_monitoring_credential(credential)
    return _CachedCredentialProvider(credential), resolver.last_backend or "UNKNOWN"


def stage_registry(root: Path, pid: int | None = None) -> SqlScriptRegistry:
    dest = root / ENGINE.value
    dest.mkdir(parents=True, exist_ok=False)
    for name in PostgreSQLAdapter.REQUIRED:
        for suffix in ("sql", "yaml"):
            filename = f"{name}.{suffix}"
            shutil.copyfile(ROOT / "sql" / ENGINE.value / filename, dest / filename)
    for filename, expected in FIXTURE_SHA256.items():
        body = (FIXTURES / filename).read_text(encoding="utf-8")
        if hashlib.sha256(body.encode()).hexdigest() != expected:
            raise ToolkitError("FixtureRejected", "A reviewed acceptance fixture has changed.")
        if filename == "timeout_observe.sql" and pid is not None:
            if type(pid) is not int or not 1 <= pid < 2**31:
                raise ToolkitError("IdentityRejected", "Invalid PostgreSQL backend PID.")
            body = body.replace("WHERE pid = -1;", f"WHERE pid = {pid};")
        (dest / filename).write_text(body, encoding="utf-8")
    registry = SqlScriptRegistry(root)
    for script in registry.validate():
        registry.authorize(script, ENGINE)
    return registry


def query(adapter: PostgreSQLAdapter, name: str) -> CollectorResult:
    return adapter.execute_readonly(adapter.registry.get(name, ENGINE))


def identity(adapter: PostgreSQLAdapter) -> dict[str, Any]:
    result = query(adapter, "timeout_identity")
    if not result.success:
        raise ToolkitError("IdentityFailed", "The reviewed identity query failed.",
                           database_error=result.database_error)
    if result.truncated or result.row_count != 1:
        raise ToolkitError("IdentityRejected", "The connection identity was incomplete.")
    row = result.rows[0]
    if (row.get("version_num") != 160015 or row.get("database_name") != adapter.instance.database
            or row.get("elevated_membership") is not False or type(row.get("backend_pid")) is not int
            or not row.get("backend_start") or not row.get("statement_timeout")):
        raise ToolkitError("IdentityRejected", "Version, database, privilege or backend identity did not match.")
    return row


def clean(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        value = re.sub(r"\bpostgres(?:ql)?://\S+", "[REDACTED]", value, flags=re.IGNORECASE)
        return sanitize_vendor_text(value, secrets)
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key in {"credential_alias", "username", "monitor_user", "current_user"}
                else clean(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item, secrets) for item in value]
    return value


class Audit(logging.Handler):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self.setFormatter(SafeFormatter())
        self.secrets = secrets
        self.events: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(clean(json.loads(self.format(record)), self.secrets))


@contextmanager
def capture_audit(secrets: tuple[str, ...]) -> Iterator[Audit]:
    logger = logging.getLogger("dba")
    previous = logger.handlers[:], logger.level, logger.propagate
    handler = Audit(secrets)
    logger.handlers = [handler]
    try:
        yield handler
    finally:
        logger.handlers, logger.level, logger.propagate = previous
        handler.close()


@contextmanager
def trace_cursor(connection: Any, trace: list[dict[str, Any]]) -> Iterator[None]:
    """Trace the existing client cursor without changing its protocol or wait path."""
    base = connection.cursor_factory

    def stamp() -> dict[str, Any]:
        deadline = connection._dba_sql_deadline
        if not isinstance(deadline, SqlDeadline):
            raise ToolkitError("TraceUnavailable", "The expected Adapter Deadline is unavailable.")
        return {"at_monotonic": deadline.clock(), "deadline_start": deadline.started_at,
                "deadline_end": deadline.expires_at, "remaining_ms": deadline.remaining() * 1000}

    def finish(event: dict[str, Any]) -> None:
        event["end"] = stamp()
        event["elapsed_ms"] = (event["end"]["at_monotonic"] - event["start"]["at_monotonic"]) * 1000

    class TracedCursor(base):  # type: ignore[valid-type,misc]
        def execute(self, sql: str, params: Any = None, **kwargs: Any) -> Any:
            self.event: dict[str, Any] = {
                "operation": "set_config" if sql.startswith(
                    "SELECT pg_catalog.set_config(") else "select",
                "cursor_type": base.__name__, "start": stamp(),
            }
            trace.append(self.event)
            try:
                result = super().execute(sql, params, **kwargs)
                self.event["status"] = "OK"
                if self.event["operation"] == "set_config" and self.pgresult is not None:
                    value = self.pgresult.get_value(0, 0)
                    if value is not None:
                        self.event["statement_timeout"] = value.decode("ascii")
                return result
            except Exception as exc:
                self.event["status"] = "FAILED"
                self.event["exception_type"] = type(exc).__name__
                state = getattr(exc, "sqlstate", None)
                if state is not None:
                    self.event["sqlstate"] = state
                if isinstance(exc, TimeoutError):
                    self.event["client_io_timeout"] = True
                if getattr(connection, "broken", False) is True:
                    self.event["driver_connection_broken"] = True
                raise
            finally:
                finish(self.event)

        def _execute_send(self, *args: Any, **kwargs: Any) -> None:
            super()._execute_send(*args, **kwargs)
            # libpq accepted the dispatch. This is not a server arrival timestamp.
            self.event["libpq_dispatch"] = stamp()

        def fetchmany(self, size: int = 0) -> Any:
            event: dict[str, Any] = {"operation": "fetch", "start": stamp()}
            trace.append(event)
            try:
                rows = super().fetchmany(size)
                event["rows"] = len(rows)
                return rows
            finally:
                finish(event)

    connection.cursor_factory = TracedCursor
    try:
        yield
    finally:
        connection.cursor_factory = base


def visibility(result: CollectorResult, expected: dict[str, Any]) -> str:
    if not result.success or result.truncated or result.row_count > 1:
        return "UNKNOWN"
    if not result.rows:
        return "ABSENT"
    row = result.rows[0]
    if row.get("backend_pid") != expected["backend_pid"] or not row.get("backend_start"):
        return "UNKNOWN"
    if row["backend_start"] != expected["backend_start"]:
        return "CONNECTION_ID_REBOUND"
    if row.get("state") == "active" and row.get("wait_event") == "PgSleep":
        return "ACTIVE_OBSERVED"
    return "PRESENT" if row.get("state") else "UNKNOWN"


def observe(observer: PostgreSQLAdapter, expected: dict[str, Any],
            done: Future[CollectorResult], initial: CollectorResult) -> dict[str, Any]:
    deadline = SqlDeadline.start(OBSERVE_SECONDS)
    if visibility(initial, expected) != "PRESENT":
        return {"status": "UNKNOWN", "active_observed": False, "samples": [],
                "reason": "Could not verify initial observer visibility of the target backend.",
                "error": initial.database_error, "query_stop_confirmed": False}
    samples: list[dict[str, Any]] = []
    active = False
    # Leave one full fixture deadline for each poll and one final probe.
    while not done.done() and deadline.remaining() > 2:
        result = query(observer, "timeout_observe")
        state = visibility(result, expected)
        samples.append({"status": state, "elapsed_ms": deadline.elapsed() * 1000})
        active |= state == "ACTIVE_OBSERVED"
        if not result.success:
            return {"status": "UNKNOWN", "active_observed": active, "samples": samples,
                    "error": result.database_error, "query_stop_confirmed": False}
        if state in {"ABSENT", "CONNECTION_ID_REBOUND"}:
            break
        time.sleep(min(0.025, deadline.remaining()))
    # This last read follows the target outcome and uses the same observer connection.
    if not done.done():
        return {"status": "UNKNOWN", "active_observed": active, "samples": samples,
                "reason": "Target still pending at the observation cutoff", "query_stop_confirmed": False}
    if deadline.remaining() <= 1:
        return {"status": "UNKNOWN", "active_observed": active, "samples": samples,
                "reason": "Observation budget exhausted", "query_stop_confirmed": False}
    final = query(observer, "timeout_observe")
    return {"status": visibility(final, expected), "active_observed": active, "samples": samples,
            "error": final.database_error, "query_stop_confirmed": False}


def classify(result: CollectorResult, trace: list[dict[str, Any]]) -> str:
    error = result.database_error or {}
    timeout = error.get("timeout") or {}
    if error.get("sqlstate") == "57014":
        if (error.get("error_category") == "TIMEOUT"
                and "statement timeout" in str(error.get("message")).casefold().replace("_", " ")
                and timeout.get("cancellation_status") == "CANCELLED_CONFIRMED"
                and timeout.get("mechanism") == "POSTGRESQL_STATEMENT_TIMEOUT"):
            return "SERVER_STATEMENT_TIMEOUT"
        return "SERVER_CANCEL_OTHER_OR_UNKNOWN"
    if error.get("error_category") == "CONNECTION_LOST":
        return "CONNECTION_LOST"
    if timeout.get("cancellation_status") == "CANCEL_STATUS_UNKNOWN":
        if any(event.get("exception_type") == "SqlDeadlineExpired" for event in trace):
            return "CLIENT_DEADLINE"
        if any(event.get("client_io_timeout") for event in trace):
            return "CLIENT_SOCKET_TIMEOUT"
        if result.error_type in {"SessionSetupTimeout", "ExecuteTimeout", "FetchTimeout"}:
            return "CLIENT_DEADLINE"
    if result.error_type == "ServerTimeoutBudgetUnavailable":
        return "CLIENT_BUDGET_REFUSED"
    if any(event.get("driver_connection_broken") for event in trace):
        return "DRIVER_CONNECTION_BROKEN"
    return "UNEXPECTED_RESULT"


def visible_error_fields(markup: str, cli: str, fields: dict[str, Any]) -> dict[str, bool]:
    labels = {
        "vendor_code": ("数据库错误码", "Code"),
        "sqlstate": ("SQLSTATE", "SQLSTATE"),
        "error_category": ("错误分类", "Error Category"),
        "execution_phase": ("阶段", "Phase"),
        "elapsed_ms": ("耗时ms", "Elapsed ms"),
        "timeout_seconds": (None, "Applied Timeout s"),
        "effective_timeout_seconds": ("生效超时s", "Effective Timeout s"),
        "cancellation_status": ("取消状态", "Cancellation Status"),
        "connection_status": ("连接状态", "Connection Status"),
        "script_path": ("SQL脚本", "SQL Script"),
    }
    cli_lines = [line.strip() for line in cli.splitlines()]

    def cli_field(label: str, value: Any) -> bool:
        shown = display_message(str(display_value(value)))
        return any(line.startswith(display_label(label) + " ")
                   and line[len(display_label(label)):].strip() == shown
                   for line in cli_lines)

    checks = {"html": True, "cli": True}
    for key, (html_label, cli_label) in labels.items():
        value = fields.get(key)
        if value is None:
            continue
        if html_label is not None:
            checks["html"] &= html.escape(f"{html_label}={value}") in markup
        if key == "error_category":
            shown = display_message(str(display_value(value)))
            checks["cli"] &= f"\n{display_label(cli_label)}\n  {shown}\n" in cli
        else:
            checks["cli"] &= cli_field(cli_label, value)
    message = fields.get("sanitized_message")
    if message:
        checks["html"] &= html.escape(message) in markup
        checks["cli"] &= message in cli
    checks["html"] &= fields.get("timeout_seconds") == fields.get("effective_timeout_seconds")
    return checks


def render_evidence(output: Path, adapter: PostgreSQLAdapter, baseline: CollectorResult,
                    result: CollectorResult, events: list[dict[str, Any]],
                    secrets: tuple[str, ...]) -> dict[str, bool]:
    baseline, result = (
        replace(item, rows=tuple(clean(item.rows, secrets)),
                error_message=clean(item.error_message, secrets),
                database_error=clean(item.database_error, secrets))
        for item in (baseline, result)
    )
    error = result.database_error or {}
    report = build_slow_report(
        replace(adapter.instance, credential_alias="[REDACTED]", connection={}),
        (baseline, result), adapter.settings, "16.15",
        required_collectors=(baseline.collector_name, result.collector_name),
    )
    payload = {"success": result.success, "collector_name": result.collector_name,
               "error": error, "error_type": result.error_type, "error_message": result.error_message}
    structured = render_json({**payload, "baseline": asdict(baseline), "result": asdict(result)})
    cli = render_human("diagnose", payload)
    markup = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    failed = [event for event in events if event.get("sql_script") == result.collector_name
              and event.get("result") == "failed"]
    projection = database_error_log_fields(error)
    fields = ("vendor_code", "sqlstate", "error_category", "execution_phase", "timeout_seconds",
              "effective_timeout_seconds", "cancellation_status", "connection_status", "script_path",
              "sanitized_message")
    timeout = error.get("timeout") or {}
    # Preserve the actual audit projection and append exact per-result evidence in its own field.
    jsonl = "".join(json.dumps({**event, "collector_elapsed_ms": timeout.get("elapsed_ms")},
                              ensure_ascii=False) + "\n" for event in failed)
    artifacts = {"json": structured, "txt": cli, "html": markup, "jsonl": jsonl}
    for suffix, body in artifacts.items():
        (output / f"timeout_execute.{suffix}").write_text(body, encoding="utf-8")
    visible = visible_error_fields(markup, cli, {**projection, "elapsed_ms": timeout.get("elapsed_ms")})
    return {
        "json": bool(error) and json.loads(structured)["error"] == error,
        "jsonl": bool(error) and len(failed) == 1
        and all(failed[0].get(key) == projection.get(key) for key in fields)
        and json.loads(jsonl)["collector_elapsed_ms"] == timeout.get("elapsed_ms"),
        "html": bool(error) and visible["html"],
        "cli": bool(error) and visible["cli"],
    }


def exercise(target: PostgreSQLAdapter, observer: PostgreSQLAdapter, output: Path,
             audit: Audit, secrets: tuple[str, ...], before: dict[str, Any]) -> dict[str, Any]:
    connection = cast(Any, target._connection)
    baseline_trace: list[dict[str, Any]] = []
    with trace_cursor(connection, baseline_trace):
        baseline = query(target, "timeout_normal")
    if not baseline.success or baseline.rows != ({"probe_value": 1},) or baseline.truncated:
        raise ToolkitError("BaselineFailed", "The normal query did not return its expected row.",
                           database_error=baseline.database_error)
    baseline_record = asdict(baseline)
    (output / "timeout_normal.json").write_text(render_json(clean(baseline_record, secrets)), encoding="utf-8")
    trace: list[dict[str, Any]] = []
    initial = query(observer, "timeout_observe")

    def attempt() -> CollectorResult:
        with trace_cursor(connection, trace):
            return query(target, "timeout_execute")

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(attempt)
        observed = observe(observer, before, pending, initial)
        result = pending.result()
    detached = target._connection is None
    closed = connection.closed is True
    refused = False
    if detached:
        try:
            query(target, "timeout_normal")
        except ToolkitError as exc:
            refused = exc.error_type == "PreflightRequired"
    parity = render_evidence(output, target, baseline, result, audit.events, secrets)
    recovery: dict[str, Any] = {"status": "NOT_EXECUTED"}
    if detached and refused and closed:
        first = len(audit.events)
        try:
            target.preflight_checks.clear()
            target.connect()
            after = identity(target)
            after_result = query(target, "timeout_normal")
            passed_scripts = {e.get("sql_script") for e in audit.events[first:] if e.get("result") == "ok"}
            preflight = {"connect", "preflight"} <= passed_scripts
            independent = after_result.success and after_result.rows == ({"probe_value": 1},)
            new = target._connection is not connection and after["backend_pid"] != before["backend_pid"]
            recovery = {"status": "PASS" if preflight and independent and new else "FAIL",
                        "preflight": preflight, "new_backend_pid": after["backend_pid"],
                        "independent_select": independent}
        except ToolkitError as exc:
            recovery = {"status": "FAIL", "error_type": exc.error_type,
                        "error": exc.database_error_payload()}
    outcome = classify(result, trace)
    settings = [event for event in trace if event["operation"] == "set_config"]
    statement_limit = settings[-1].get("statement_timeout") if settings else None
    timeout_audit = next((event for event in reversed(audit.events)
                          if event["sql_script"] == "timeout_execute"), {})
    applied_ms = timeout_audit.get("server_statement_timeout_ms")
    if statement_limit is None and not settings and applied_ms is not None:
        statement_limit = f"{applied_ms}ms"
    attempts = sum(event["operation"] == "select" and "libpq_dispatch" in event for event in trace)
    discarded = not result.success and not result.rows and result.row_count == 0
    error = result.database_error or {}
    timeout = error.get("timeout") or {}
    preserved = asdict(baseline) == baseline_record
    passed = (outcome == "SERVER_STATEMENT_TIMEOUT" and timeout.get("execution_phase") == "Execute"
              and timeout.get("connection_status") == "DISCARDED" and detached and refused and closed
              and discarded and attempts == 1 and preserved and recovery["status"] == "PASS"
              and observed["active_observed"] and observed["status"] in {"ABSENT", "PRESENT"}
              and all(parity.values()))
    return cast(dict[str, Any], clean({
        "status": "PASS" if passed else "PARTIAL", "normal": "PASS",
        "normal_trace": baseline_trace, "timeout_result": asdict(result), "outcome": outcome,
        "trace": trace, "set_config_executed": bool(settings),
        "server_statement_timeout": statement_limit,
        "server_statement_timeout_ms": applied_ms,
        "select_attempts": attempts, "observation": observed, "partial_result_discarded": discarded,
        "adapter_detached": detached, "driver_closed": closed, "preflight_required": refused,
        "recovery": recovery, "baseline_retained": preserved, "output_parity": parity,
        "fetch_network_timeout": {"status": "BLOCKED", "reason": "Default psycopg Cursor buffers in Execute."},
        "client_deadline": "OBSERVED" if outcome == "CLIENT_DEADLINE" else "NOT_EXECUTED",
    }, secrets))


def output_path(path: Path) -> Path:
    root = ROOT / "reports"
    path = path.resolve()
    if (path.parent != root or not path.name.startswith("postgresql-timeout-1615-")
            or path.exists()):
        raise ToolkitError("OutputRejected", "Use a fresh PostgreSQL acceptance directory under reports.")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--settings", type=Path, default=ROOT / "config/runtime.yaml")
    parser.add_argument("--credential-backend", choices=("auto", "os", "encrypted", "sqlite"),
                        default="auto")
    parser.add_argument("--credential-store-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path | None = None
    secrets: tuple[str, ...] = ()
    stage = "host"
    summary: dict[str, Any] = {"evidence_type": "NOT_EXECUTED"}
    try:
        require_host()
        stage = "inventory"
        asset = load_asset(args.inventory)
        dest = output_path(args.output)
        path = args.settings if args.settings.is_file() else None
        settings = cast(PostgreSQLSettings, _effective_settings(asset, path))
        settings = replace(settings, diagnostic_sql_timeout_seconds=TIMEOUT_SECONDS,
                           connect_timeout=min(settings.connect_timeout, 5))
        stage = "credentials"
        provider, source = resolve_provider(asset, args.credential_backend, args.credential_store_path)
        secrets = (provider.credential.password, provider.credential.username, asset.credential_alias)
        dest.mkdir(parents=True, exist_ok=False)
        output = dest
        driver = load_driver()
        if getattr(driver, "__version__", None) != "3.3.6":
            raise ToolkitError("DriverRejected", "This instrumentation was reviewed for psycopg 3.3.6.")
        summary.update({"asset": asset.id, "driver_version": "3.3.6", "credential_source": source,
                        "diagnostic_deadline_seconds": TIMEOUT_SECONDS})
        stage = "preflight"
        with TemporaryDirectory(prefix="pg-timeout-registry-") as temp, capture_audit(secrets) as audit:
            root = Path(temp)
            registry = stage_registry(root / "target")
            summary["evidence_type"] = "WINDOWS_LOCAL_REAL_E2E"
            with PostgreSQLAdapter(asset, provider, registry, settings, driver=driver) as target:
                before = identity(target)
                summary.update({"server_version": "16.15", "identity": before,
                                "account_classification": "NON_PRIVILEGED"})
                observer_registry = stage_registry(root / "observer", before["backend_pid"])
                with PostgreSQLAdapter(asset, provider, observer_registry, settings, driver=driver) as observer:
                    other = identity(observer)
                    if other["backend_pid"] == before["backend_pid"]:
                        raise ToolkitError("ObserverRejected", "The observer must use an independent backend.")
                    stage = "execute"
                    summary.update(exercise(target, observer, output, audit, secrets, before))
    except Exception as exc:
        summary.update({"status": "INCOMPLETE", "stage": stage,
                        "error_type": exc.error_type if isinstance(exc, ToolkitError) else type(exc).__name__})
        if isinstance(exc, ToolkitError) and exc.database_error_payload() is not None:
            summary["error"] = exc.database_error_payload()
    summary = clean(summary, secrets)
    if output is not None:
        with (output / "summary.json").open("x", encoding="utf-8") as stream:
            stream.write(render_json(summary) + "\n")
    print(json.dumps({"status": summary["status"], "stage": stage, "output": str(output) if output else None,
                      "error_type": summary.get("error_type")}, ensure_ascii=False))
    return {"PASS": 0, "PARTIAL": 1}.get(summary["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
