"""MySQL 8.0+ Registry-backed, bounded read-only connection."""

from __future__ import annotations

import hashlib
import importlib
import re
import socket
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
from dba.database_errors import normalize_mysql_error
from dba.errors import ToolkitError
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.mysql_privileges import classify_mysql_grants
from dba.permissions import assess_permissions, requirements_for
from dba.registry import SqlScript, SqlScriptRegistry
from dba.settings import MySQLSettings


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, sql: str) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def get_autocommit(self) -> bool: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def connect(self, **kwargs: Any) -> Connection: ...


def load_driver() -> Driver:
    try:
        return cast(Driver, importlib.import_module("pymysql"))
    except ImportError:
        raise ToolkitError("DriverUnavailable", "Install the mysql extra from an offline wheelhouse.") from None


def _driver_error(exc: Exception, phase: str, secret: str = "") -> ToolkitError:
    detail = normalize_mysql_error(exc, (secret,)) if phase == "Connection" else None
    code = exc.args[0] if exc.args and type(exc.args[0]) is int else None
    if isinstance(exc, (TimeoutError, socket.timeout)) or code in {1205, 3024}:
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if code in {1044, 1045, 1142, 1227}:
        return ToolkitError(f"{phase}Denied", f"{phase} denied; monitoring permission is insufficient.", database_error=detail)
    if code in {1054, 1146}:
        return ToolkitError("CapabilityUnavailable", "Required MySQL monitoring view or column is unavailable.", database_error=detail)
    error_type = "ConnectionFailed" if phase == "Connection" else f"{phase}Failed"
    message = (f"{phase} failed; vendor error details are available below."
               if detail is not None else
               f"{phase} failed; check connectivity and monitoring account.")
    return ToolkitError(error_type, message, database_error=detail)


class MySQLAdapter(Adapter):
    """One serial monitoring connection; never accepts user-authored SQL."""

    REQUIRED = ("preflight", "server_info", "database_status", "active_sessions",
                "active_requests", "active_requests_no_waits", "active_requests_basic",
                "transactions", "blocking", "monitor_grants")

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
        self._statement_script = "active_requests"
        self._read_timeout = self.settings.query_timeout
        self._streaming = False
        self.preflight_checks: dict[str, bool] = {}
        self.permission_observations: set[str] = set()
        self.privilege_classification: dict[str, Any] = {"classification": "UNKNOWN"}
        self.current_user: str | None = None
        self._last_database_error: object | None = None

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
            self._read_timeout = min(self.settings.query_timeout,
                                     *(script.metadata.timeout_seconds for script in scripts))
            phase = "Connection"
            driver = self._driver or load_driver()
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError("CredentialRejected", "Monitoring credential unavailable or prohibited.") from None
            self._password = credential.password
            cursor_class = getattr(getattr(driver, "cursors", None), "SSCursor", None)
            if cursor_class is not None:
                self._streaming = True
            connection_options: dict[str, Any] = {"cursorclass": cursor_class} if cursor_class else {}
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
                **connection_options,
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
            raise ToolkitError("PreflightFailed", "MySQL monitor preflight is incomplete.")
        row = result.rows[0]
        version = row.get("server_version")
        if not isinstance(version, str):
            raise ToolkitError("UnsupportedVersion", "MySQL 8.0 or newer is required; other engines need reviewed SQL.")
        match = re.match(r"^(\d+)\.(\d+)\.", version)
        if (match is None or (int(match[1]), int(match[2])) < (8, 0)
                or "mariadb" in version.casefold()):
            raise ToolkitError("UnsupportedVersion", "MySQL 8.0 or newer is required; other engines need reviewed SQL.")
        self._version = version
        if row.get("performance_schema_enabled") != 1:
            raise ToolkitError("PreflightDenied", "Missing enabled Performance Schema.")
        if type(row.get("connection_id")) is not int or row["connection_id"] <= 0:
            raise ToolkitError("PreflightFailed", "MySQL connection identity unavailable.")
        current_user = row.get("monitor_user")
        if not isinstance(current_user, str) or "@" not in current_user:
            raise ToolkitError("PreflightFailed", "MySQL CURRENT_USER identity is unavailable.")
        self.current_user = current_user
        grants_result = self._run(self.registry.get("monitor_grants", DatabaseType.MYSQL))
        if not grants_result.success or grants_result.truncated or not grants_result.rows:
            raise ToolkitError("PermissionInspectionFailed",
                               "MySQL account grants could not be inspected safely.",
                               database_error=self._last_database_error)
        grants = [str(next(iter(grant.values()))) for grant in grants_result.rows if grant]
        self.privilege_classification, observed = classify_mysql_grants(
            grants, current_user, str(row.get("active_roles") or "NONE")
        )
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
        self.permission_observations = set(observed)
        self.preflight_checks["privilege_classification"] = True
        for feature in ("slow", "disk-growth"):
            readiness = assess_permissions(
                requirements_for(DatabaseType.MYSQL, feature, self._version),
                self.permission_observations,
            )
            self.preflight_checks[f"permission_{feature}"] = readiness["status"].value == "READY"
        if row.get("statements_consumer") != 1:
            self._statement_script = "active_requests_basic"
        elif row.get("waits_consumer") != 1:
            self._statement_script = "active_requests_no_waits"
        else:
            self._statement_script = "active_requests"
        self.preflight_checks["instrumented_statements"] = self._statement_script != "active_requests_basic"
        self.preflight_checks["instrumented_waits"] = self._statement_script == "active_requests"
        for name in ("server_info", "database_status", "active_sessions",
                     self._statement_script, "transactions", "blocking"):
            probe = self._run(self.registry.get(name, DatabaseType.MYSQL))
            self.preflight_checks[name] = probe.success

    def _close(self) -> bool:
        connection, self._connection = self._connection, None
        self._ready = False
        self._password = ""
        self._version = ""
        self._streaming = False
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

    def execute_readonly(self, script: SqlScript) -> CollectorResult:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved MySQL monitor is required.")
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
            self.registry.authorize(script, DatabaseType.MYSQL)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            cursor = self._connection.cursor()
            cursor.execute(script.text)
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column[0]) for column in cursor.description]
            values = cursor.fetchmany(script.metadata.max_rows)
            truncated = len(values) >= script.metadata.max_rows
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in values)
        except ToolkitError as exc:
            error = exc
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "SQL is not an unchanged reviewed script from this registry.")
                     if not authorized else _driver_error(exc, "Query"))
        except Exception as exc:
            self._last_database_error = normalize_mysql_error(exc, (self._password,))
            error = _driver_error(exc, "Query")
        except BaseException:
            self._close()
            raise
        finally:
            if truncated and self._streaming:
                # SSCursor.close() drains all unread rows. Abort the connection first.
                self._close()
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    if not truncated:
                        error = error or ToolkitError("CleanupFailed", "Query cursor cleanup failed.")
            if error is not None:
                rows = ()
                truncated = False
                self._close()
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated}
            if authorized:
                details.update({"sql_sha256": hashlib.sha256(script.text.encode()).hexdigest(),
                                "timeout_seconds": min(script.metadata.timeout_seconds,
                                                       self._read_timeout),
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
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.MYSQL)).success
