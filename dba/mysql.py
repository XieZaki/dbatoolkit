"""MySQL 5.7+ Registry-backed, bounded read-only connection."""

from __future__ import annotations

import hashlib
import math
import re
import socket
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Any, NoReturn, Protocol, cast

from dba.adapters import Adapter
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import CredentialProvider, Mode, validate_monitoring_credential
from dba.database_errors import DatabaseErrorDetail, normalize_mysql_error, with_timeout_evidence
from dba.errors import (
    ToolkitError,
    client_sql_deadline_error,
    database_error_log_fields,
    set_database_error_connection_status,
)
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.mysql_privileges import classify_mysql_grants
from dba.registry import MAX_SQL_TIMEOUT_SECONDS, SqlScript, SqlScriptRegistry, fetch_bounded_rows
from dba.settings import MySQLSettings
from dba.sql_timeout import SqlDeadline, SqlDeadlineExpired
from dba.top_sql import TOP_LIMIT

MYSQL_TOP_SQL_METRICS = frozenset({
    "sum_timer_wait", "avg_timer_wait", "count_star", "sum_rows_examined",
    "sum_rows_sent", "sum_created_tmp_disk_tables", "sum_no_index_used",
})

_SET_EXECUTION_LIMIT = "SET SESSION max_execution_time = %s"
_SHOW_SCRIPTS = frozenset({
    "disk_binlogs", "monitor_grants", "replication_status", "replication_status_legacy",
})


def _supports_execution_limit(version: str, comment: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+]|$)", version)
    product = comment.casefold()
    return (match is not None and "mysql" in product
            and not any(fork in (version + " " + comment).casefold()
                        for fork in ("mariadb", "percona", "tidb", "aurora"))
            and (int(match[1]), int(match[2])) in {(5, 7), (8, 0), (8, 4)}
            and tuple(map(int, match.groups())) >= (5, 7, 8))


class _DeadlineSocket:
    """Proxy PyMySQL's socket so every packet read/write uses current remaining time."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock
        self._deadline: SqlDeadline | None = None
        self._phase = "Execute"

    def set_deadline(self, deadline: SqlDeadline | None, phase: str = "Execute") -> None:
        self._deadline = deadline
        self._phase = phase

    def settimeout(self, timeout: float | None) -> None:
        self._sock.settimeout(timeout)

    def _arm(self) -> None:
        if self._deadline is not None:
            self._sock.settimeout(self._deadline.require_remaining(self._phase))

    def _expired_socket_error(self, exc: OSError) -> NoReturn:
        if self._deadline is not None:
            raise SqlDeadlineExpired(self._phase) from None
        raise exc

    def sendall(self, data: bytes) -> None:
        self._arm()
        try:
            self._sock.sendall(data)
        except (TimeoutError, socket.timeout) as exc:
            self._expired_socket_error(exc)

    def recv(self, size: int, flags: int = 0) -> bytes:
        self._arm()
        try:
            return cast(bytes, self._sock.recv(size, flags))
        except (TimeoutError, socket.timeout) as exc:
            self._expired_socket_error(exc)

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        self._arm()
        try:
            if nbytes:
                return cast(int, self._sock.recv_into(buffer, nbytes, flags))
            return cast(int, self._sock.recv_into(buffer, flags=flags))
        except (TimeoutError, socket.timeout) as exc:
            self._expired_socket_error(exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


class _DeadlineReader:
    """Keep PyMySQL's existing buffered bytes while rearming each raw read."""

    def __init__(self, reader: Any, sock: _DeadlineSocket) -> None:
        self._reader = reader
        self._sock = sock

    def read(self, size: int = -1) -> bytes:
        deadline = self._sock._deadline
        if deadline is None:
            return cast(bytes, self._reader.read(size))
        if size < 0:
            raise RuntimeError("Unbounded MySQL socket reads are prohibited during SQL execution.")
        chunks = bytearray()
        while len(chunks) < size:
            self._sock._arm()
            try:
                part = self._reader.read1(size - len(chunks))
            except (TimeoutError, socket.timeout) as exc:
                self._sock._expired_socket_error(exc)
            if not part:
                break
            chunks.extend(part)
        deadline.require_remaining(self._sock._phase)
        return bytes(chunks)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reader, name)


def _install_mysql_deadline_socket(connection: Any) -> _DeadlineSocket | None:
    """Wrap PyMySQL's socket and buffered reader without discarding buffered data."""
    sock = getattr(connection, "_sock", None)
    reader = getattr(connection, "_rfile", None)
    if sock is None or reader is None or not callable(getattr(reader, "read1", None)):
        return None
    if isinstance(sock, _DeadlineSocket):
        return sock
    proxy = _DeadlineSocket(sock)
    connection._sock = proxy
    connection._rfile = _DeadlineReader(reader, proxy)
    return proxy


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, sql: str, parameters: tuple[Any, ...] | None = None) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self, cursor: Any | None = None) -> Cursor: ...
    def get_autocommit(self) -> bool: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def connect(self, **kwargs: Any) -> Connection: ...


def load_driver() -> Driver:
    from dba.driver_manager import load_database_driver

    return cast(Driver, load_database_driver(DatabaseType.MYSQL, "pymysql"))


def _driver_error(
    exc: Exception,
    phase: str,
    secret: str = "",
    *,
    execution_phase: str | None = None,
    timeout_seconds: int | None = None,
    effective_timeout_seconds: int | None = None,
    script_timeout_seconds: int | None = None,
    elapsed_ms: int | None = None,
    script_path: str | None = None,
) -> ToolkitError:
    detail = normalize_mysql_error(exc, (secret,))
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
    code = detail.vendor_code
    if detail.error_category == "TIMEOUT":
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if detail.error_category == "PERMISSION_DENIED":
        return ToolkitError(
            f"{phase}Denied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this MySQL version and denied operation.",
            database_error=detail,
        )
    if detail.error_category == "CONNECTION_LOST":
        error_type = "ConnectionLost" if phase == "Connection" else "QueryConnectionLost"
        return ToolkitError(error_type, "Connection was lost during database operation.", database_error=detail)
    if detail.error_category == "CANCELLED_CONFIRMED":
        return ToolkitError("QueryCancelled", "The database confirmed that the query was cancelled.",
                            database_error=detail)
    if detail.error_category == "CANCEL_STATUS_UNKNOWN":
        return ToolkitError("QueryCancelStatusUnknown", "Query cancellation status is unknown; connection will be discarded.",
                            database_error=detail)
    if code in {1054, 1146}:
        return ToolkitError("CapabilityUnavailable", "Required MySQL monitoring view or column is unavailable.", database_error=detail)
    error_type = "ConnectionFailed" if phase == "Connection" else f"{phase}Failed"
    message = (f"{phase} failed; vendor error details are available below."
               if detail is not None else
               f"{phase} failed; check connectivity and monitoring account.")
    return ToolkitError(error_type, message, database_error=detail)


class MySQLAdapter(Adapter):
    """One serial monitoring connection; never accepts user-authored SQL."""

    REQUIRED = ("preflight", "preflight_roles", "server_info", "database_status", "active_sessions",
                "active_requests", "active_requests_no_waits", "active_requests_basic",
                "transactions", "blocking", "recent_statements", "monitor_grants")
    STREAMING_SCRIPTS = frozenset({
        "active_sessions", "active_requests", "active_requests_no_waits",
        "active_requests_basic", "transactions", "blocking", "database_status",
        "disk_binlogs", "disk_schemas", "disk_replica",
    })
    TOP_SQL_METRICS = MYSQL_TOP_SQL_METRICS

    def __init__(self, instance: DatabaseInstance, provider: CredentialProvider,
                 registry: SqlScriptRegistry, settings: MySQLSettings | None = None,
                 mode: Mode = Mode.PRODUCTION, *, driver: Driver | None = None) -> None:
        try:
            super().__init__(instance, provider, mode)
        except ValueError:
            raise ToolkitError("CredentialRejected", "MockCredentialProvider is prohibited in production.") from None
        if instance.db_type != DatabaseType.MYSQL:
            raise ToolkitError("NotImplemented", "Not implemented for this database type.")
        if not instance.enabled:
            raise ToolkitError("InstanceDisabled", "Inventory instance is disabled.")
        self.registry = registry
        self.settings = settings or MySQLSettings()
        self._driver = driver
        self._connection: Connection | None = None
        self._password = ""
        self._ready = False
        self._version = ""
        self._server_execution_limit = False
        self._session_identity: tuple[Connection, int] | None = None
        self._statement_script = "active_requests"
        self._read_timeout = self.settings.effective_sql_timeout(MAX_SQL_TIMEOUT_SECONDS)
        self._deadline_socket: _DeadlineSocket | None = None
        self._streaming_cursor: Any | None = None
        self.preflight_checks: dict[str, bool] = {}
        self.statement_history_consumer: bool | None = None
        self.privilege_classification: dict[str, Any] = {"classification": "UNKNOWN"}
        self.current_user: str | None = None

    @property
    def statement_script(self) -> str:
        return self._statement_script

    def _audit(self, script: str, started: float, error: ToolkitError | None,
               details: dict[str, Any] | None = None) -> None:
        configure_logging().info("event", extra={"event": {
            **(details or {}), "database_id": self.instance.id, "module": "mysql",
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
            scripts = [self.registry.get(name, DatabaseType.MYSQL) for name in self.REQUIRED]
            for script in scripts:
                self.registry.authorize(script, DatabaseType.MYSQL)
            # read_timeout is a connection-wide socket bound, not a server-side cancel.
            self._read_timeout = min(self.settings.effective_sql_timeout(script.metadata.timeout_seconds)
                                     for script in scripts)
            phase = "Connection"
            driver = self._driver or load_driver()
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError("CredentialRejected", "Monitoring credential unavailable or prohibited.") from None
            self._password = credential.password
            self._streaming_cursor = getattr(getattr(driver, "cursors", None), "SSCursor", None)
            self._connection = driver.connect(
                host=self.instance.host, port=self.instance.port,
                database=self.instance.database, user=credential.username,
                password=credential.password, connect_timeout=self.settings.connect_timeout,
                read_timeout=self._read_timeout, write_timeout=self.settings.connect_timeout,
                charset=self.settings.charset, autocommit=True, local_infile=False,
                ssl_disabled=not self.settings.ssl_enabled,
                ssl_ca=self.settings.ssl_ca if self.settings.ssl_enabled else None,
                ssl_verify_cert=(self.settings.ssl_verify_cert if self.settings.ssl_enabled else False),
                ssl_verify_identity=(
                    self.settings.ssl_verify_identity if self.settings.ssl_enabled else False
                ),
            )
            self._deadline_socket = _install_mysql_deadline_socket(self._connection)
            if self._driver is None and self._deadline_socket is None:
                raise ToolkitError(
                    "DeadlineUnavailable",
                    "The MySQL driver does not expose a bounded packet-read path.",
                )
            if not self._connection.get_autocommit():
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
        result = self._run(self.registry.get("preflight", DatabaseType.MYSQL))
        if not result.success or result.truncated or len(result.rows) != 1:
            raise ToolkitError(
                result.error_type or "PreflightFailed",
                "MySQL monitor preflight is incomplete. "
                f"{result.error_message}" if result.error_message else
                "MySQL monitor preflight is incomplete.",
                database_error=result.database_error,
            )
        row = result.rows[0]
        version = row.get("server_version")
        if not isinstance(version, str):
            raise ToolkitError("UnsupportedVersion", "MySQL 5.7 or newer requires a reviewed version profile.")
        match = re.match(r"^(\d+)\.(\d+)\.", version)
        comment = row.get("version_comment")
        product_text = f"{version} {comment if isinstance(comment, str) else ''}".casefold()
        if (match is None or (int(match[1]), int(match[2])) < (5, 7)
                or "mariadb" in product_text):
            raise ToolkitError("UnsupportedVersion", "MySQL 5.7 or newer requires a reviewed version profile.")
        self._version = version
        connection_id = row.get("connection_id")
        if self._connection is None or type(connection_id) is not int or connection_id <= 0:
            raise ToolkitError("PreflightFailed", "MySQL connection identity unavailable.")
        self._session_identity = (self._connection, connection_id)
        active_roles = "NONE"
        if tuple(int(part) for part in match.groups()) >= (8, 0):
            role_result = self._run(self.registry.get("preflight_roles", DatabaseType.MYSQL))
            if not role_result.success or role_result.truncated or len(role_result.rows) != 1:
                raise ToolkitError(
                    role_result.error_type or "PreflightFailed",
                    "MySQL active role state could not be inspected safely. "
                    f"{role_result.error_message}" if role_result.error_message else
                    "MySQL active role state could not be inspected safely.",
                    database_error=role_result.database_error,
                )
            role_value = role_result.rows[0].get("active_roles")
            if not isinstance(role_value, str):
                raise ToolkitError("PreflightFailed", "MySQL active role state is unavailable.")
            active_roles = role_value
        if row.get("performance_schema_enabled") != 1:
            raise ToolkitError("PreflightDenied", "Missing enabled Performance Schema.")
        current_user = row.get("monitor_user")
        if not isinstance(current_user, str) or "@" not in current_user:
            raise ToolkitError("PreflightFailed", "MySQL CURRENT_USER identity is unavailable.")
        self.current_user = current_user
        grants_result = self._run(self.registry.get("monitor_grants", DatabaseType.MYSQL))
        if not grants_result.success or grants_result.truncated or not grants_result.rows:
            raise ToolkitError("PermissionInspectionFailed",
                               "MySQL account grants could not be inspected safely.",
                               database_error=grants_result.database_error)
        grants = [str(next(iter(grant.values()))) for grant in grants_result.rows if grant]
        self.privilege_classification = classify_mysql_grants(grants, current_user, active_roles)
        if self.privilege_classification["classification"] == "PRIVILEGED":
            high_risk = self.privilege_classification["high_risk_privileges"]
            first = high_risk[0] if isinstance(high_risk, list) and high_risk else {}
            raise ToolkitError(
                "PrivilegedAccount",
                "MySQL monitoring account has a detected high-risk privilege.",
                trigger_reason={"name": str(first.get("privilege", "")), **{
                    key: str(first.get(key, "")) for key in
                    ("privilege", "scope", "source", "principal")}},
            )
        if self.privilege_classification["classification"] == "UNKNOWN":
            raise ToolkitError(
                "PrivilegeClassificationUnknown",
                "MySQL roles are present and their effective privileges could not be verified.",
                trigger_reason={"name": "unresolved role", "source": "role",
                                "principal": current_user,
                                "scope": str(self.privilege_classification.get("active_roles", "UNKNOWN"))},
            )
        write_grants = self.privilege_classification.get("write_privileges", [])
        if write_grants:
            first_write = write_grants[0] if isinstance(write_grants, list) else {}
            raise ToolkitError(
                "UnsafeMonitoringAccount",
                "MySQL account has write-capable grants; use a read-only monitoring account.",
                trigger_reason={"name": str(first_write.get("privilege", "")), **{
                    key: str(first_write.get(key, "")) for key in
                    ("privilege", "scope", "source", "principal")}},
            )
        self.preflight_checks["performance_schema"] = True
        self.preflight_checks["privilege_classification"] = True
        self._server_execution_limit = (
            isinstance(comment, str) and _supports_execution_limit(version, comment)
        )
        if row.get("statements_consumer") != 1:
            self._statement_script = "active_requests_basic"
        elif row.get("waits_consumer") != 1:
            self._statement_script = "active_requests_no_waits"
        else:
            self._statement_script = "active_requests"
        self.preflight_checks["instrumented_statements"] = self._statement_script != "active_requests_basic"
        self.preflight_checks["instrumented_waits"] = self._statement_script == "active_requests"
        history_consumer = row.get("history_consumer")
        self.statement_history_consumer = history_consumer == 1 if type(history_consumer) is int else None
        self.preflight_checks["statement_history_consumer"] = self.statement_history_consumer is True
        # Do not probe every diagnostic at connection time.  The selected
        # reviewed query reports its own vendor access error when the operator
        # invokes that feature; deployment grants live in the shared guide.

    def _close(self) -> bool:
        connection, self._connection = self._connection, None
        self._ready = False
        self._password = ""
        self._version = ""
        self._server_execution_limit = False
        self._session_identity = None
        self._streaming_cursor = None
        self._deadline_socket = None
        ok = True
        if connection is not None:
            try:
                if not connection.get_autocommit():
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

    def __enter__(self) -> MySQLAdapter:
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
            return int(value) if value == value.to_integral_value() else str(value)
        if isinstance(value, dict):
            return {str(key): "[REDACTED]" if SENSITIVE.search(str(key)) else self._safe_value(item)
                    for key, item in value.items()}
        return redact(value)

    def execute_readonly(self, script: SqlScript,
                         parameters: tuple[int, ...] | None = None) -> CollectorResult:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "MySQL connection preflight did not complete.")
        if (isinstance(script, SqlScript) and script.metadata.name == "top_sql"
                and script.metadata.feature == "top-sql"):
            raise ToolkitError("InvalidParameters", "Top SQL requires the validated metric and limit binding interface.")
        return self._run(script, parameters)

    def execute_top_sql_readonly(self, script: SqlScript, top_n: int, metric: str) -> CollectorResult:
        """Execute only the reviewed, parameterized MySQL Top SQL Registry entry."""
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved MySQL monitor is required.")
        if (script.metadata.name != "top_sql" or script.metadata.feature != "top-sql"
                or script.metadata.database_type != DatabaseType.MYSQL
                or type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT
                or top_n >= script.metadata.max_rows
                or not isinstance(metric, str) or metric not in self.TOP_SQL_METRICS):
            raise ToolkitError("InvalidParameters", "Top SQL limit or ranking metric is invalid.")
        return self._run(script, (metric, top_n), parameter_kind="top_sql")

    def _run(self, script: SqlScript, parameters: tuple[Any, ...] | None = None,
             *, parameter_kind: str | None = None) -> CollectorResult:
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
        statement_started = False
        server_limit_ms: int | None = None
        try:
            self.registry.authorize(script, DatabaseType.MYSQL)
            authorized = True
            script_name = script.metadata.name
            if parameters is not None:
                if parameter_kind == "top_sql":
                    if (script_name != "top_sql" or script.metadata.feature != "top-sql"
                            or script.metadata.database_type != DatabaseType.MYSQL or len(parameters) != 2
                            or not isinstance(parameters[0], str)
                            or parameters[0] not in self.TOP_SQL_METRICS
                            or type(parameters[1]) is not int
                            or not 1 <= parameters[1] <= TOP_LIMIT
                            or parameters[1] >= script.metadata.max_rows):
                        raise ToolkitError("RegistryRejected", "Top SQL bindings are invalid for this reviewed query.")
                elif (script_name != "recent_statements" or parameter_kind is not None or len(parameters) != 3
                      or any(type(value) is not int or value <= 0 for value in parameters)
                      or parameters[0] != parameters[2]):
                    raise ToolkitError("RegistryRejected", "Bound parameters are not valid for this reviewed query.")
            elif parameter_kind is not None or script_name in {"recent_statements", "top_sql"}:
                raise ToolkitError("RegistryRejected", "This reviewed query requires validated bound parameters.")
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            effective_timeout = min(
                self.settings.effective_sql_timeout(script.metadata.timeout_seconds),
                self._read_timeout,
            )
            deadline = SqlDeadline.start(effective_timeout)
            if self._deadline_socket is not None:
                self._deadline_socket.set_deadline(deadline, "Execute")
            if self._server_execution_limit and script_name not in _SHOW_SCRIPTS:
                setup = self._connection.cursor()
                try:
                    # SET itself spends part of the client deadline. The client
                    # still enforces the original deadline after SET returns.
                    server_limit_ms = math.floor(deadline.require_remaining("Execute") * 1000)
                    if server_limit_ms < 1:
                        raise SqlDeadlineExpired("Execute")
                    setup.execute(_SET_EXECUTION_LIMIT, (server_limit_ms,))
                finally:
                    setup.close()
            streaming = script.metadata.name in self.STREAMING_SCRIPTS
            if streaming and self._streaming_cursor is not None:
                cursor = self._connection.cursor(self._streaming_cursor)
            else:
                cursor = self._connection.cursor()
            deadline.require_remaining("Execute")
            statement_started = True
            if parameters is None:
                cursor.execute(script.text)
            else:
                cursor.execute(script.text, parameters)
            deadline.require_remaining("Execute")
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column[0]) for column in cursor.description]
            execution_phase = "Fetch"

            def before_fetch(_remaining: float) -> None:
                if self._deadline_socket is not None:
                    self._deadline_socket.set_deadline(deadline, "Fetch")

            bounded = fetch_bounded_rows(
                cursor, script.metadata.max_rows, script.metadata.max_result_bytes,
                deadline=deadline, before_fetch=before_fetch,
            )
            # The recent-statement Registry SQL uses an explicit LIMIT 5.
            # Exactly five rows means the sample limit was reached, but does
            # not establish that a sixth row exists.
            truncated = (bounded.byte_limit_reached
                         or (bounded.row_limit_reached and script.metadata.name != "recent_statements"))
            result_bytes = bounded.result_bytes
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in bounded.rows)
        except ToolkitError as exc:
            error = exc
        except SqlDeadlineExpired:
            assert deadline is not None
            error = client_sql_deadline_error(
                "mysql", "PyMySQL", execution_phase=execution_phase,
                timeout_seconds=deadline.timeout_seconds,
                script_timeout_seconds=script.metadata.timeout_seconds,
                elapsed_ms=int(deadline.elapsed() * 1000),
                script_path=f"{script.metadata.database_type.value}/{script_name}.sql",
            )
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "SQL is not an unchanged reviewed script from this registry.")
                     if not authorized else _driver_error(
                         exc, "Query", self._password, execution_phase=execution_phase,
                         timeout_seconds=deadline.timeout_seconds if deadline else self._read_timeout,
                         effective_timeout_seconds=(deadline.timeout_seconds if deadline else
                                                     self.settings.effective_sql_timeout(
                                                         script.metadata.timeout_seconds)),
                         script_timeout_seconds=script.metadata.timeout_seconds,
                         elapsed_ms=int((time.monotonic() - started) * 1000),
                         script_path=f"{script.metadata.database_type.value}/{script_name}.sql"))
        except Exception as exc:
            error = _driver_error(
                exc, "Query", self._password, execution_phase=execution_phase,
                timeout_seconds=deadline.timeout_seconds if deadline else self._read_timeout,
                effective_timeout_seconds=(deadline.timeout_seconds if deadline else
                                           self.settings.effective_sql_timeout(
                                               script.metadata.timeout_seconds)),
                script_timeout_seconds=script.metadata.timeout_seconds,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                script_path=f"{script.metadata.database_type.value}/{script_name}.sql")
        except BaseException:
            error = ToolkitError("Interrupted", "Query was interrupted; connection will be closed.")
            raise
        finally:
            identity = self._session_identity
            identity_recorded = identity is not None and identity[0] is self._connection
            streaming_cursor = (cursor is not None
                                and script.metadata.name in self.STREAMING_SCRIPTS)
            connection_closed_before_cursor = False
            connection_close_ok: bool | None = None
            connection_close_attempted = False
            if streaming_cursor and (error is not None or truncated):
                connection_close_attempted = True
                connection_close_ok = self._close()
                connection_closed_before_cursor = connection_close_ok
                if not connection_closed_before_cursor:
                    error = error or ToolkitError(
                        "CleanupFailed", "Streaming result connection cleanup failed."
                    )
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    if not connection_closed_before_cursor:
                        error = error or ToolkitError("CleanupFailed", "Query cursor cleanup failed.")
            if error is not None:
                detail = error.database_error
                if (statement_started and script_name in _SHOW_SCRIPTS
                        and isinstance(detail, DatabaseErrorDetail) and detail.timeout is not None
                        and detail.timeout.cancellation_status == "CANCEL_STATUS_UNKNOWN"):
                    # MySQL KILL matches a numeric ID only. A separate lookup
                    # cannot make identity verification atomic with that match.
                    error.database_error = replace(
                        detail, timeout=replace(detail.timeout, cancellation_reason=(
                            "show_target_identity_not_atomic"
                            if identity_recorded
                            else "show_target_identity_unavailable"
                        )),
                    )
                rows = ()
                truncated = False
                result_bytes = 0
                if not connection_close_attempted:
                    connection_close_attempted = True
                    connection_close_ok = self._close()
                set_database_error_connection_status(
                    error, "DISCARDED" if connection_close_ok is True else "UNKNOWN")
            elif truncated and streaming_cursor and not connection_close_attempted:
                self._close()
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated,
                                       "result_bytes": result_bytes}
            if authorized:
                details.update({"sql_sha256": hashlib.sha256(script.text.encode()).hexdigest(),
                                "timeout_seconds": deadline.timeout_seconds if deadline else self._read_timeout,
                                "effective_timeout_seconds": self.settings.effective_sql_timeout(
                                    script.metadata.timeout_seconds) if deadline is None else
                                    deadline.timeout_seconds,
                                "max_rows": script.metadata.max_rows})
                details["max_result_bytes"] = script.metadata.max_result_bytes
                if server_limit_ms is not None:
                    details["server_execution_limit_ms"] = server_limit_ms
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
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.MYSQL)).success
