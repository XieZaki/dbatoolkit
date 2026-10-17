"""PostgreSQL read-only adapter with bounded Registry execution."""

from __future__ import annotations

import hashlib
import importlib
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
from dba.database_errors import normalize_postgresql_error
from dba.errors import ToolkitError
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.registry import SqlScript, SqlScriptRegistry
from dba.settings import PostgreSQLSettings


class Cursor(Protocol):
    description: Sequence[Any] | None

    def execute(self, sql: str) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    autocommit: bool

    def cursor(self) -> Cursor: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def connect(self, **kwargs: Any) -> Connection: ...


def load_driver() -> Driver:
    try:
        module = importlib.import_module("psycopg")
    except ImportError:
        raise ToolkitError("DriverUnavailable", "Install the postgresql extra from an offline wheelhouse.") from None
    return cast(Driver, module)


def _driver_error(exc: Exception, phase: str, secret: str = "") -> ToolkitError:
    detail = normalize_postgresql_error(exc, (secret,)) if phase == "Connection" else None
    state = getattr(exc, "sqlstate", None)
    if isinstance(exc, TimeoutError) or state == "57014":
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if state == "42501":
        return ToolkitError("QueryDenied", "PostgreSQL monitoring permission is insufficient.", database_error=detail)
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
        self._statement_timeout = self.settings.query_timeout
        self.preflight_checks: dict[str, bool] = {}
        self.permission_observations: set[str] = set()

    def _audit(self, script: str, started: float, error: ToolkitError | None,
               details: dict[str, Any] | None = None) -> None:
        configure_logging().info("event", extra={"event": {
            **(details or {}), "database_id": self.instance.id, "module": "postgresql",
            "sql_script": script, "result": "failed" if error else "ok",
            "elapsed_time": time.monotonic() - started,
            "error_type": error.error_type if error else None,
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
            self._statement_timeout = min(self.settings.query_timeout,
                                          *(script.metadata.timeout_seconds for script in scripts))
            phase = "Connection"
            driver = self._driver or load_driver()
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError("CredentialRejected", "Monitoring credential unavailable or prohibited.") from None
            self._password = credential.password
            self._connection = driver.connect(
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
            raise ToolkitError("PreflightFailed", "PostgreSQL monitor preflight is incomplete.")
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
        for name, capability in (("read_all_stats", "cross-session activity and SQL text"),
                                 ("read_all_settings", "monitoring settings"),
                                 ("locks_visible", "lock data"),
                                 ("database_stats_visible", "database statistics"),
                                 ("settings_visible", "settings data")):
            if row.get(name) is not True:
                raise ToolkitError("PreflightDenied", f"Missing {capability}; review pg_monitor membership manually.")
            self.preflight_checks[name] = True
        for name in ("activity_visible", "replication_visible", "slots_visible", "archiver_visible"):
            self.preflight_checks[name] = bool(row.get(name))
        self.permission_observations.update(("pg_monitor", "CONNECT"))

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
        return self._run(script)

    def _run(self, script: SqlScript) -> CollectorResult:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        cursor: Cursor | None = None
        rows: tuple[dict[str, Any], ...] = ()
        truncated = False
        error: ToolkitError | None = None
        authorized = False
        script_name = "unregistered"
        try:
            self.registry.authorize(script, DatabaseType.POSTGRESQL)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            cursor = self._connection.cursor()
            cursor.execute(script.text)
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column.name) for column in cursor.description]
            values = cursor.fetchmany(script.metadata.max_rows)
            truncated = len(values) >= script.metadata.max_rows
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in values)
        except ToolkitError as exc:
            error = exc
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "SQL is not an unchanged reviewed script from this registry.")
                     if not authorized else _driver_error(exc, "Query"))
        except Exception as exc:
            error = _driver_error(exc, "Query")
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
                self._close()
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated}
            if authorized:
                details.update({"sql_sha256": hashlib.sha256(script.text.encode()).hexdigest(),
                                "timeout_seconds": min(script.metadata.timeout_seconds,
                                                       self._statement_timeout),
                                "max_rows": script.metadata.max_rows})
            self._audit(script_name, started, error, details)
        return CollectorResult(self.instance.id, script_name, started_at,
                               datetime.now(timezone.utc), (time.monotonic() - started) * 1000,
                               rows, len(rows), truncated, error is None,
                               error.error_type if error else None, str(error) if error else None)

    def get_server_version(self) -> str:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "Connection preflight required.")
        return self._version

    def health_check(self) -> bool:
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.POSTGRESQL)).success
