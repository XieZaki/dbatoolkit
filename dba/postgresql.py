"""PostgreSQL read-only adapter with bounded Registry execution."""

from __future__ import annotations

import hashlib
import selectors
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Any, Protocol, cast

from dba.adapters import Adapter
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import CredentialProvider, Mode, validate_monitoring_credential
from dba.database_errors import normalize_postgresql_error, with_timeout_evidence
from dba.errors import (
    ToolkitError,
    client_sql_deadline_error,
    database_error_log_fields,
    set_database_error_connection_status,
)
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.registry import MAX_SQL_TIMEOUT_SECONDS, SqlScript, SqlScriptRegistry, fetch_bounded_rows
from dba.settings import PostgreSQLSettings
from dba.sql_timeout import SqlDeadline, SqlDeadlineExpired

POSTGRESQL_TOP_SQL_METRICS = frozenset({
    "total_exec_time", "mean_exec_time", "calls", "shared_blks_read",
    "shared_blks_hit", "temp_blks_written", "wal_bytes",
})


class _DeadlineWaitMixin:
    """Apply the active SQL deadline to Psycopg's synchronous socket wait."""

    _dba_sql_deadline: SqlDeadline | None = None

    def wait(self, gen: Any, interval: float = 0.1,
             timeout: float | None = None) -> Any:
        deadline = self._dba_sql_deadline
        if deadline is None:
            if timeout is None:
                return super().wait(gen, interval=interval)  # type: ignore[misc]
            return super().wait(gen, interval=interval, timeout=timeout)  # type: ignore[misc]
        # Psycopg 3.2 has no wait(timeout=...). Its generator yields read/write
        # masks compatible with selectors. Driving it here also bypasses the
        # driver's interrupt handler, which can send a protocol CancelRequest.
        deadline.require_remaining("Execute")
        wait_until = deadline.clock() + timeout if timeout is not None else deadline.expires_at
        try:
            events = next(gen)
            with selectors.DefaultSelector() as selector:
                sock = self.pgconn.socket  # type: ignore[attr-defined]
                selector.register(sock, events)
                while True:
                    remaining = min(deadline.require_remaining("Execute"), wait_until - deadline.clock())
                    if remaining <= 0:
                        raise SqlDeadlineExpired("Execute")
                    ready = selector.select(remaining)
                    deadline.require_remaining("Execute")
                    if ready:
                        new_events = gen.send(ready[0][1])
                        if new_events != events:
                            selector.modify(sock, new_events)
                            events = new_events
        except StopIteration as result:
            deadline.require_remaining("Execute")
            return result.value


def _connect_with_deadline_wait(driver: Any, **kwargs: Any) -> Any:
    """Use Psycopg's public Connection.connect factory with a deadline-aware subclass."""
    connection_type = getattr(driver, "Connection", None)
    if not isinstance(connection_type, type):
        # Lightweight injected Fake Drivers keep their existing connect API.
        return driver.connect(**kwargs)
    deadline_connection_type = type(
        "DeadlineConnection", (_DeadlineWaitMixin, connection_type), {}
    )
    connector = getattr(deadline_connection_type, "connect", None)
    if not callable(connector):
        return driver.connect(**kwargs)
    return connector(**kwargs)


class Cursor(Protocol):
    description: Sequence[Any] | None

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    autocommit: bool

    def cursor(self) -> Cursor: ...
    def execute(self, query: str, params: Sequence[Any] | None = None) -> Cursor: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def connect(self, **kwargs: Any) -> Connection: ...


def load_driver() -> Driver:
    from dba.driver_manager import load_database_driver

    return cast(Driver, load_database_driver(DatabaseType.POSTGRESQL, "psycopg"))


def _driver_error(
    exc: Exception,
    phase: str,
    secret: str = "",
    *,
    top_sql: bool = False,
    execution_phase: str | None = None,
    timeout_seconds: int | None = None,
    effective_timeout_seconds: int | None = None,
    script_timeout_seconds: int | None = None,
    elapsed_ms: int | None = None,
    script_path: str | None = None,
) -> ToolkitError:
    detail = normalize_postgresql_error(exc, (secret,))
    state = detail.sqlstate
    if script_path is not None and execution_phase is not None:
        detail = with_timeout_evidence(
            detail,
            execution_phase=execution_phase,
            timeout_seconds=timeout_seconds,
            effective_timeout_seconds=effective_timeout_seconds,
            script_timeout_seconds=script_timeout_seconds,
            elapsed_ms=elapsed_ms,
            script_path=script_path,
        )
    if detail.error_category == "TIMEOUT":
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if detail.error_category == "PERMISSION_DENIED":
        return ToolkitError(
            "QueryDenied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this PostgreSQL version and denied operation.",
            database_error=detail,
        )
    if top_sql and state == "55000":
        return ToolkitError("TopSQLSourceNotConfigured",
                            "pg_stat_statements is not active; verify its reviewed module configuration manually.",
                            database_error=detail)
    if detail.error_category == "CONNECTION_LOST":
        error_type = "ConnectionLost" if phase == "Connection" else "QueryConnectionLost"
        return ToolkitError(error_type, "Connection was lost during database operation.", database_error=detail)
    if detail.error_category == "CANCELLED_CONFIRMED":
        return ToolkitError("QueryCancelled", "The database confirmed that the query was cancelled.",
                            database_error=detail)
    if detail.error_category == "CANCEL_STATUS_UNKNOWN":
        return ToolkitError("QueryCancelStatusUnknown", "Query cancellation status is unknown; connection will be discarded.",
                            database_error=detail)
    error_type = "ConnectionFailed" if phase == "Connection" else f"{phase}Failed"
    message = (f"{phase} failed; vendor error details are available below."
               if detail is not None else
               f"{phase} failed; check connectivity and monitoring account.")
    return ToolkitError(error_type, message, database_error=detail)


class PostgreSQLAdapter(Adapter):
    """One serial, autocommit, read-only connection per adapter."""

    REQUIRED = ("preflight", "server_info", "database_status", "active_sessions",
                "long_running", "transactions", "blocking")

    def __init__(self, instance: DatabaseInstance, provider: CredentialProvider,
                 registry: SqlScriptRegistry, settings: PostgreSQLSettings | None = None,
                 mode: Mode = Mode.PRODUCTION, *, driver: Driver | None = None) -> None:
        try:
            super().__init__(instance, provider, mode)
        except ValueError:
            raise ToolkitError("CredentialRejected", "MockCredentialProvider is prohibited in production.") from None
        if instance.db_type != DatabaseType.POSTGRESQL:
            raise ToolkitError("NotImplemented", "Not implemented for this database type.")
        if not instance.enabled:
            raise ToolkitError("InstanceDisabled", "Inventory instance is disabled.")
        self.registry = registry
        self.settings = settings or PostgreSQLSettings()
        self._driver = driver
        self._connection: Connection | None = None
        self._password = ""
        self._ready = False
        self._version = ""
        self._statement_timeout = self.settings.effective_sql_timeout(MAX_SQL_TIMEOUT_SECONDS)
        self._statement_timeout_cap = self._statement_timeout
        self.preflight_checks: dict[str, bool] = {}

    def _audit(self, script: str, started: float, error: ToolkitError | None,
               details: dict[str, Any] | None = None) -> None:
        configure_logging().info("event", extra={"event": {
            **(details or {}), "database_id": self.instance.id, "module": "postgresql",
            "sql_script": script, "result": "failed" if error else "ok",
            "elapsed_time": time.monotonic() - started,
            "error_type": error.error_type if error else None,
            **database_error_log_fields(error.database_error_payload() if error else None),
        }})

    def connect(self) -> None:
        if self._connection is not None:
            raise ToolkitError("AlreadyConnected", "Adapter already connected.")
        started = time.monotonic()
        error: ToolkitError | None = None
        phase = "Registry"
        try:
            scripts = [self.registry.get(name, DatabaseType.POSTGRESQL) for name in self.REQUIRED]
            for script in scripts:
                self.registry.authorize(script, DatabaseType.POSTGRESQL)
            # statement_timeout is connection-wide, so it cannot carry per-script limits.
            self._statement_timeout = min(self.settings.effective_sql_timeout(script.metadata.timeout_seconds)
                                          for script in scripts)
            self._statement_timeout_cap = self._statement_timeout
            phase = "Connection"
            driver = self._driver or load_driver()
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError("CredentialRejected", "Monitoring credential unavailable or prohibited.") from None
            self._password = credential.password
            self._connection = _connect_with_deadline_wait(
                driver,
                host=self.instance.host, port=self.instance.port,
                dbname=self.instance.database or "postgres", user=credential.username,
                password=credential.password, connect_timeout=self.settings.connect_timeout,
                sslmode=self.settings.sslmode, application_name="dba-toolkit",
                options=(f"-c default_transaction_read_only=on "
                         f"-c statement_timeout={self._statement_timeout * 1000}"),
                autocommit=True,
            )
            if not self._connection.autocommit:
                raise ToolkitError("AutocommitRequired", "Driver did not enable autocommit.")
            self.preflight_checks["connect"] = True
            self._preflight()
            self._ready = True
        except ToolkitError as exc:
            error = exc
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "Required SQL assets are missing or invalid.")
                     if phase == "Registry" else _driver_error(exc, "Connection", self._password))
        except Exception as exc:
            error = _driver_error(exc, "Connection", self._password)
        except BaseException:
            self._close()
            raise
        finally:
            self._audit("connect", started, error)
            if error is not None:
                self._close()
        if error is not None:
            raise error from None

    def _preflight(self) -> None:
        result = self._run(self.registry.get("preflight", DatabaseType.POSTGRESQL))
        if not result.success or result.truncated or len(result.rows) != 1:
            raise ToolkitError(
                result.error_type or "PreflightFailed",
                result.error_message or "PostgreSQL monitor preflight is incomplete.",
                database_error=result.database_error,
            )
        row = result.rows[0]
        version_num = row.get("server_version_num")
        if type(version_num) is not int or version_num < 120000:
            raise ToolkitError("UnsupportedVersion", "PostgreSQL 12 or newer is required.")
        self._version = str(version_num)
        if row.get("default_transaction_read_only") != "on" or row.get("transaction_read_only") != "on":
            raise ToolkitError("ReadOnlyRequired", "PostgreSQL session is not read-only.")
        privileged = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")
        if any(row.get(name) is not False for name in privileged):
            raise ToolkitError("PrivilegedAccount", "Dedicated non-privileged monitoring account required.")
        # Feature visibility is not preflighted here. The selected reviewed
        # collector query reports its vendor access error when invoked.

    def _close(self) -> bool:
        connection, self._connection = self._connection, None
        self._ready = False
        self._password = ""
        self._version = ""
        ok = True
        if connection is not None:
            try:
                if not connection.autocommit:
                    connection.rollback()
            except Exception:
                ok = False
            finally:
                try:
                    connection.close()
                except Exception:
                    ok = False
        return ok

    def disconnect(self) -> None:
        if not self._close():
            raise ToolkitError("CleanupFailed", "Connection cleanup failed; check server session state.")

    def __enter__(self) -> PostgreSQLAdapter:
        self.connect()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        if exc_type is None:
            self.disconnect()
        else:
            self._close()

    def _safe_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value.replace(self._password, "[REDACTED]") if self._password else value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {str(key): "[REDACTED]" if SENSITIVE.search(str(key)) else self._safe_value(item)
                    for key, item in value.items()}
        return redact(value)

    def execute_readonly(self, script: SqlScript) -> CollectorResult:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved PostgreSQL monitor is required.")
        if (isinstance(script, SqlScript) and script.metadata.name == "top_sql"
                and script.metadata.feature == "top-sql"):
            raise ToolkitError("InvalidParameters", "Top SQL must use the validated parameter binding interface.")
        return self._run(script)

    def execute_top_sql_readonly(self, script: SqlScript, top_n: int, metric: str) -> CollectorResult:
        """Execute only the fixed, parameterized PostgreSQL Top SQL Registry entry."""
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved PostgreSQL monitor is required.")
        if (script.metadata.name != "top_sql" or script.metadata.feature != "top-sql"
                or script.metadata.database_type != DatabaseType.POSTGRESQL
                or type(top_n) is not int or not 1 <= top_n <= 100
                or metric not in POSTGRESQL_TOP_SQL_METRICS):
            raise ToolkitError("InvalidParameters", "Top SQL limit or ranking metric is invalid.")
        return self._run(script, (top_n, metric))

    def _run(self, script: SqlScript, parameters: tuple[Any, ...] | None = None) -> CollectorResult:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        cursor: Cursor | None = None
        rows: tuple[dict[str, Any], ...] = ()
        truncated = False
        result_bytes = 0
        error: ToolkitError | None = None
        authorized = False
        script_name = "unregistered"
        execution_phase = "Execute"
        deadline: SqlDeadline | None = None
        try:
            self.registry.authorize(script, DatabaseType.POSTGRESQL)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            effective_timeout = min(
                self.settings.effective_sql_timeout(script.metadata.timeout_seconds),
                self._statement_timeout_cap,
            )
            deadline = SqlDeadline.start(effective_timeout)
            setattr(self._connection, "_dba_sql_deadline", deadline)
            if self._statement_timeout != deadline.timeout_seconds:
                timeout_cursor = self._connection.execute(
                    "SELECT pg_catalog.set_config('statement_timeout', %s, false)",
                    (f"{deadline.timeout_seconds * 1000}ms",),
                )
                timeout_cursor.close()
                self._statement_timeout = deadline.timeout_seconds
            cursor = self._connection.cursor()
            deadline.require_remaining("Execute")
            if parameters is None:
                cursor.execute(script.text)
            else:
                if (script.metadata.name != "top_sql" or script.metadata.feature != "top-sql"
                        or len(parameters) != 2):
                    raise ToolkitError("InvalidParameters", "Only the reviewed Top SQL query accepts bindings.")
                cursor.execute(script.text, parameters)
            deadline.require_remaining("Execute")
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column.name) for column in cursor.description]
            execution_phase = "Fetch"
            bounded = fetch_bounded_rows(
                cursor, script.metadata.max_rows, script.metadata.max_result_bytes,
                deadline=deadline,
            )
            truncated = bounded.truncated
            result_bytes = bounded.result_bytes
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in bounded.rows)
        except ToolkitError as exc:
            error = exc
        except SqlDeadlineExpired:
            assert deadline is not None
            error = client_sql_deadline_error(
                "postgresql", "psycopg", execution_phase=execution_phase,
                timeout_seconds=deadline.timeout_seconds,
                script_timeout_seconds=script.metadata.timeout_seconds,
                elapsed_ms=int(deadline.elapsed() * 1000),
                script_path=f"{script.metadata.database_type.value}/{script_name}.sql",
            )
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "SQL is not an unchanged reviewed script from this registry.")
                     if not authorized else _driver_error(
                         exc, "Query", self._password, top_sql=script_name == "top_sql",
                         execution_phase=execution_phase,
                         timeout_seconds=deadline.timeout_seconds if deadline else self._statement_timeout,
                         effective_timeout_seconds=(deadline.timeout_seconds if deadline else
                                                     self.settings.effective_sql_timeout(
                                                         script.metadata.timeout_seconds)),
                         script_timeout_seconds=script.metadata.timeout_seconds,
                         elapsed_ms=int((time.monotonic() - started) * 1000),
                         script_path=f"{script.metadata.database_type.value}/{script_name}.sql"))
        except Exception as exc:
            error = _driver_error(
                exc, "Query", self._password, top_sql=script_name == "top_sql",
                execution_phase=execution_phase,
                timeout_seconds=deadline.timeout_seconds if deadline else self._statement_timeout,
                effective_timeout_seconds=(deadline.timeout_seconds if deadline else
                                           self.settings.effective_sql_timeout(
                                               script.metadata.timeout_seconds)),
                script_timeout_seconds=script.metadata.timeout_seconds,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                script_path=f"{script.metadata.database_type.value}/{script_name}.sql")
        except BaseException:
            self._close()
            raise
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    error = error or ToolkitError("CleanupFailed", "Query cursor cleanup failed.")
            if error is not None:
                rows = ()
                truncated = False
                result_bytes = 0
                close_ok = self._close()
                set_database_error_connection_status(error, "DISCARDED" if close_ok else "UNKNOWN")
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated,
                                       "result_bytes": result_bytes}
            if authorized:
                details.update({"sql_sha256": hashlib.sha256(script.text.encode()).hexdigest(),
                                "timeout_seconds": deadline.timeout_seconds if deadline else self._statement_timeout,
                                "effective_timeout_seconds": (deadline.timeout_seconds if deadline else
                                                               self.settings.effective_sql_timeout(
                                                                   script.metadata.timeout_seconds)),
                                "max_rows": script.metadata.max_rows})
                details["max_result_bytes"] = script.metadata.max_result_bytes
            self._audit(script_name, started, error, details)
        return CollectorResult(self.instance.id, script_name, started_at,
                               datetime.now(timezone.utc), (time.monotonic() - started) * 1000,
                               rows, len(rows), truncated, error is None,
                               error.error_type if error else None, str(error) if error else None,
                               error.database_error_payload() if error else None)

    def get_server_version(self) -> str:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "Connection preflight required.")
        return self._version

    def health_check(self) -> bool:
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.POSTGRESQL)).success
