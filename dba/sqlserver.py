from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Any, Protocol, cast

from dba.adapters import Adapter
from dba.capabilities import ServerCapabilities
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import CredentialProvider, Mode, validate_monitoring_credential
from dba.errors import ToolkitError, driver_error, sqlserver_connection_error
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.registry import SqlScript, SqlScriptRegistry, fetch_bounded_rows
from dba.settings import SQLServerSettings


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, sql: str, *parameters: object) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    autocommit: bool
    timeout: int

    def cursor(self) -> Cursor: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def drivers(self) -> Sequence[str]: ...

    def connect(
        self, connection_string: str, *, timeout: int, autocommit: bool, readonly: bool
    ) -> Connection: ...


def load_driver() -> Driver:
    from dba.driver_manager import load_database_driver

    try:
        module = load_database_driver(DatabaseType.SQLSERVER, "pyodbc")
    except ToolkitError as exc:
        if exc.error_type == "DriverUnavailable":
            raise ToolkitError(
                "DriverUnavailable",
                "Install the sqlserver extra and approved Microsoft ODBC Driver 18 or compatible Driver 17.",
            ) from None
        raise
    # Set before the first ODBC connection, so close releases the physical session.
    setattr(module, "pooling", False)
    return cast(Driver, module)


def _odbc_value(value: str) -> str:
    if "\x00" in value:
        raise ToolkitError("InvalidConnectionOption", "NUL is prohibited in connection options.")
    return "{" + value.replace("}", "}}") + "}"


class SQLServerAdapter(Adapter):
    """One synchronous connection per adapter; not safe for concurrent sharing."""

    def __init__(
        self,
        instance: DatabaseInstance,
        provider: CredentialProvider,
        registry: SqlScriptRegistry,
        settings: SQLServerSettings | None = None,
        mode: Mode = Mode.PRODUCTION,
        *,
        driver: Driver | None = None,
    ) -> None:
        try:
            super().__init__(instance, provider, mode)
            if instance.environment == "production":
                from dba.credentials import validate_provider

                validate_provider(provider, Mode.PRODUCTION)
        except ValueError:
            raise ToolkitError(
                "CredentialRejected", "MockCredentialProvider is prohibited in production."
            ) from None
        if instance.db_type != DatabaseType.SQLSERVER:
            raise ToolkitError("NotImplemented", "Not implemented for this database type.")
        if not instance.enabled:
            raise ToolkitError("InstanceDisabled", "Inventory instance is disabled.")
        self.registry = registry
        self.settings = settings or SQLServerSettings()
        self._driver = driver
        self._connection: Connection | None = None
        self._password = ""
        self._ready = False
        self._version = ""
        self.preflight_checks: dict[str, bool] = {}
        self.privilege_classification: dict[str, Any] = {}
        self.capabilities: ServerCapabilities | None = None
        self.odbc_driver: str | None = None

    def script_for(self, name: str) -> SqlScript:
        if name == "active_sessions" and self.capabilities is not None:
            name = self.capabilities.sessions_script
        return self.registry.get(name)

    def connect(self) -> None:
        if self._connection is not None:
            raise ToolkitError("AlreadyConnected", "Adapter already connected.")
        self.preflight_checks = {}
        started = time.monotonic()
        error: ToolkitError | None = None
        phase = "Registry"
        try:
            # Validate local assets before asking for credentials or opening a socket.
            for name in (
                "monitor_capabilities",
                "monitor_permissions",
                "server_info",
                "database_status",
                "active_sessions",
                "active_sessions_legacy",
                "active_requests",
                "memory_grant_waiting",
                "memory_grant_granted",
            ):
                self.registry.authorize(self.registry.get(name), DatabaseType.SQLSERVER)
            phase = "Connection"
            driver = self._driver or load_driver()
            if self._driver is None:
                from dba.runtime import select_sqlserver_driver

                selected = select_sqlserver_driver(list(driver.drivers()))
                if selected is None:
                    raise ToolkitError(
                        "DriverUnavailable",
                        "SQL Server runtime unavailable: Microsoft ODBC Driver 18 or 17 is required. Run `dba doctor` for details.",
                    )
                self.odbc_driver = selected
            else:
                self.odbc_driver = "ODBC Driver 18 for SQL Server"
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError(
                    "CredentialRejected", "Monitoring credential unavailable or prohibited."
                ) from None
            self._password = credential.password
            parts = {
                "DRIVER": self.odbc_driver,
                "SERVER": f"tcp:{self.instance.host},{self.instance.port}",
                "DATABASE": self.instance.database or "master",
                "UID": credential.username,
                "PWD": credential.password,
                "Encrypt": "yes" if self.settings.encrypt else "no",
                "TrustServerCertificate": "yes" if self.settings.trust_server_certificate else "no",
                "APP": "dba-toolkit",
            }
            connection_string = ";".join(
                f"{key}={_odbc_value(value)}" for key, value in parts.items()
            )
            self._connection = driver.connect(
                connection_string,
                timeout=self.settings.connect_timeout,
                autocommit=True,
                readonly=True,
            )
            if not self._connection.autocommit:
                raise ToolkitError("AutocommitRequired", "Driver did not enable autocommit.")
            self.preflight_checks["connect"] = True
            from dba.preflight import SQLServerMonitorPreflight

            SQLServerMonitorPreflight().check(self)
            self._ready = True
        except ToolkitError as exc:
            error = exc
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (
                ToolkitError("RegistryRejected", "Required SQL assets are missing or invalid.")
                if phase == "Registry"
                else sqlserver_connection_error(
                    exc,
                    driver=self.odbc_driver or "unavailable",
                    encrypt=self.settings.encrypt,
                    trust_server_certificate=self.settings.trust_server_certificate,
                    secret=self._password,
                )
            )
        except Exception as exc:
            error = sqlserver_connection_error(
                exc,
                driver=self.odbc_driver or "unavailable",
                encrypt=self.settings.encrypt,
                trust_server_certificate=self.settings.trust_server_certificate,
                secret=self._password,
            )
        except BaseException:
            error = ToolkitError("ConnectionInterrupted", "Connection interrupted.")
            raise
        finally:
            self._audit("connect", started, error)
            if error is not None:
                self._close()
        if error is not None:
            raise error from None

    def _close(self) -> bool:
        connection, self._connection = self._connection, None
        self._ready = False
        self._password = ""
        self._version = ""
        self.capabilities = None
        self.odbc_driver = None
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
        if not ok:
            self._audit(
                "disconnect",
                time.monotonic(),
                ToolkitError("CleanupFailed", "Connection cleanup failed."),
            )
        return ok

    def disconnect(self) -> None:
        if not self._close():
            raise ToolkitError(
                "CleanupFailed", "Connection cleanup failed; check server session state manually."
            )

    def __enter__(self) -> SQLServerAdapter:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.disconnect()
        else:
            self._close()

    def _safe_value(self, value: Any) -> Any:
        if isinstance(value, str):
            text = value.replace(self._password, "[REDACTED]") if self._password else value
            return redact(text)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            # XML attributes can entity-encode a credential assignment. Leave
            # only this fixed internal column intact until the XML parser can
            # decode, redact and reserialize attributes safely.
            result: dict[str, Any] = {}
            for key, item in value.items():
                name = str(self._safe_value(key))
                result[name] = (
                    "[REDACTED]" if SENSITIVE.search(name) else
                    item if name == "plan_xml" and isinstance(item, str) else self._safe_value(item)
                )
            return result
        return redact(value)

    def execute_readonly(
        self, script: SqlScript, *, plan_parameters: tuple[int, int, int] | None = None,
        top_sql_parameters: tuple[int, str] | None = None,
        active_sql_top_n: int | None = None,
    ) -> CollectorResult:
        if not self._ready:
            raise ToolkitError(
                "PreflightRequired",
                "A connected, preflight-approved monitoring account is required.",
            )
        # Preserve the registry's controlled rejection path for runtime callers
        # passing a raw string despite the public type annotation.
        if not isinstance(script, SqlScript):
            return self._run(script)
        is_plan = script.metadata.name in {"plan_request", "plan_cached"}
        is_top_sql = script.metadata.name == "top_sql"
        is_active_sql = script.metadata.name == "active_sql"
        if (is_plan != (plan_parameters is not None)
                or is_top_sql != (top_sql_parameters is not None)
                or is_active_sql != (active_sql_top_n is not None)
                or sum(value is not None for value in
                       (plan_parameters, top_sql_parameters, active_sql_top_n)) > 1):
            raise ToolkitError("InvalidParameters",
                               "Plan probes require matching controlled parameters; Top SQL and Active SQL "
                               "probes require their matching controlled parameters.")
        if plan_parameters is not None:
            size1, size2, session_id = plan_parameters
            if (type(size1) is not int or type(size2) is not int or type(session_id) is not int
                    or size1 != self.settings.plan_max_xml_size or size2 != size1
                    or not 1 <= session_id <= 32767):
                raise ToolkitError("InvalidParameters", "Plan limits or session identifier are invalid.")
        if top_sql_parameters is not None:
            top_n, metric = top_sql_parameters
            if (type(top_n) is not int or not 1 <= top_n <= 100
                    or metric not in {"cpu_time", "logical_reads", "physical_reads", "execution_count",
                                      "elapsed_time", "average_cpu_time", "average_elapsed_time"}):
                raise ToolkitError("InvalidParameters", "Top SQL limit or ranking metric is invalid.")
        if active_sql_top_n is not None and (type(active_sql_top_n) is not int
                                             or not 1 <= active_sql_top_n <= 100):
            raise ToolkitError("InvalidParameters", "Active SQL Top N must be an integer between 1 and 100.")
        parameters: tuple[Any, ...] | None = (
            plan_parameters or top_sql_parameters
            or ((active_sql_top_n, active_sql_top_n, active_sql_top_n)
                if active_sql_top_n is not None else None)
        )
        return self._run(script, parameters)

    def _run(
        self, script: SqlScript, parameters: tuple[Any, ...] | None = None
    ) -> CollectorResult:
        """Private execution path shared only with the fixed preflight probes."""
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        cursor: Cursor | None = None
        rows: tuple[dict[str, Any], ...] = ()
        truncated = False
        result_bytes = 0
        error: ToolkitError | None = None
        script_name = "unregistered"
        authorized = False
        try:
            self.registry.authorize(script, DatabaseType.SQLSERVER)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            # pyodbc applies connection.timeout to newly created cursors.
            self._connection.timeout = min(
                script.metadata.timeout_seconds, self.settings.query_timeout
            )
            cursor = self._connection.cursor()
            if parameters is None:
                cursor.execute(script.text)
            else:
                cursor.execute(script.text, *parameters)
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(item[0]) for item in cursor.description]
            bounded = fetch_bounded_rows(
                cursor, script.metadata.max_rows, script.metadata.max_result_bytes
            )
            truncated = bounded.truncated
            result_bytes = bounded.result_bytes
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in bounded.rows)
        except ToolkitError as exc:
            error = exc
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (
                ToolkitError(
                    "RegistryRejected",
                    "SQL is not an unchanged reviewed script from this registry.",
                )
                if not authorized
                else driver_error(exc, "Query", secret=self._password, driver=self.odbc_driver or "pyodbc")
            )
        except Exception as exc:
            error = driver_error(exc, "Query", secret=self._password, driver=self.odbc_driver or "pyodbc")
        except BaseException:
            error = ToolkitError("QueryInterrupted", "Query interrupted.")
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
                self._close()
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated,
                                       "result_bytes": result_bytes}
            if authorized:
                details.update(
                    {
                        "sql_sha256": hashlib.sha256(script.text.encode("utf-8")).hexdigest(),
                        "timeout_seconds": min(
                            script.metadata.timeout_seconds, self.settings.query_timeout
                        ),
                        "max_rows": script.metadata.max_rows,
                        "max_result_bytes": script.metadata.max_result_bytes,
                    }
                )
            self._audit(script_name, started, error, details)
        database_error_payload: dict[str, Any] | None = None
        if error is not None and error.database_error is not None:
            detail = error.database_error
            if isinstance(detail, dict):
                database_error_payload = detail
            else:
                to_dict = getattr(detail, "to_dict", None)
                payload = to_dict() if callable(to_dict) else None
                if isinstance(payload, dict):
                    database_error_payload = payload
        return CollectorResult(
            self.instance.id,
            script_name,
            started_at,
            datetime.now(timezone.utc),
            (time.monotonic() - started) * 1000,
            rows,
            len(rows),
            truncated,
            error is None,
            error.error_type if error else None,
            str(error) if error else None,
            database_error_payload,
        )

    def _audit(
        self,
        script: str,
        started: float,
        error: ToolkitError | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        configure_logging().info(
            "event",
            extra={
                "event": {
                    **(details or {}),
                    "database_id": self.instance.id,
                    "module": "sqlserver",
                    "sql_script": script,
                    "result": "failed" if error else "ok",
                    "elapsed_time": time.monotonic() - started,
                    "error_type": error.error_type if error else None,
                }
            },
        )

    def get_server_version(self) -> str:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "Connection preflight required.")
        return self._version

    def health_check(self) -> bool:
        if not self._ready:
            return False
        return self.execute_readonly(self.registry.get("server_info")).success
