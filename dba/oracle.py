"""Oracle Thin monitoring connection with reviewed, bounded SQL only."""

from __future__ import annotations

import hashlib
import importlib
import re
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
from dba.database_errors import normalize_oracle_error
from dba.errors import ToolkitError
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.permissions import VERSION_DETECTION_PRIVILEGES, OraclePrincipal
from dba.registry import SqlScript, SqlScriptRegistry
from dba.settings import OracleSettings


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None
    arraysize: int
    prefetchrows: int

    def execute(self, sql: str) -> Any: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...
    def close(self) -> None: ...


class Connection(Protocol):
    autocommit: bool
    call_timeout: int

    def cursor(self) -> Cursor: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class Driver(Protocol):
    def connect(self, **kwargs: Any) -> Connection: ...


def load_driver() -> Driver:
    try:
        return cast(Driver, importlib.import_module("oracledb"))
    except ImportError:
        raise ToolkitError("DriverUnavailable", "Install the oracle extra from an offline wheelhouse.") from None


def _driver_error(exc: Exception, phase: str, secret: str = "") -> ToolkitError:
    # Never pass through a driver exception: it can contain a DSN or credentials.
    detail = normalize_oracle_error(exc, (secret,))
    number = getattr(exc, "code", None)
    if exc.args:
        number = getattr(exc.args[0], "code", number)
    if isinstance(exc, TimeoutError) or number in {1013, 12170, 12535, 3136}:
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if number in {1031, 942, 904}:
        return ToolkitError("CapabilityUnavailable", "Required Oracle monitoring view or column is unavailable.", database_error=detail)
    error_type = "ConnectionFailed" if phase == "Connection" else f"{phase}Failed"
    message = (f"{phase} failed; vendor error details are available below."
               if detail is not None else
               f"{phase} failed; check connectivity and monitoring account.")
    return ToolkitError(error_type, message, database_error=detail)


class OracleAdapter(Adapter):
    """Serial Oracle monitor; no arbitrary SQL entry point or administrative API."""

    REQUIRED = ("session_identity", "preflight", "preflight_privileges", "preflight_object_grants",
                "preflight_owned_objects", "server_info", "database_status",
                "active_sessions", "active_requests", "long_running", "transactions",
                "blocking", "blocking_basic", "previous_sql", "probe_lock",
                "probe_locked_object", "probe_wait", "probe_process")
    PROBES = ("server_info", "database_status", "active_sessions", "active_requests",
              "transactions", "blocking_basic")

    def __init__(self, instance: DatabaseInstance, provider: CredentialProvider,
                 registry: SqlScriptRegistry, settings: OracleSettings | None = None,
                 mode: Mode = Mode.PRODUCTION, *, driver: Driver | None = None) -> None:
        try:
            super().__init__(instance, provider, mode)
        except ValueError:
            raise ToolkitError("CredentialRejected", "MockCredentialProvider is prohibited in production.") from None
        if instance.db_type != DatabaseType.ORACLE:
            raise ToolkitError("NotImplemented", "Not implemented for this database type.")
        if not instance.enabled:
            raise ToolkitError("InstanceDisabled", "Inventory instance is disabled.")
        if bool(instance.service_name) == bool(instance.sid):
            raise ToolkitError("InvalidTarget", "Oracle requires exactly one service_name or sid.")
        self.registry = registry
        self.settings = settings or OracleSettings()
        self._driver = driver
        self._connection: Connection | None = None
        self._password = ""
        self._ready = False
        self._version = ""
        self.principal: OraclePrincipal | None = None
        self.instance_id: int | None = None
        self.rac_enabled = False
        self.preflight_checks: dict[str, bool] = {}
        self.permission_observations: set[str] = set()
        self.capabilities: dict[str, bool] = {}
        self.blocking_script = "blocking_basic"

    def _audit(self, script: str, started: float, error: ToolkitError | None,
               details: dict[str, Any] | None = None) -> None:
        configure_logging().info("event", extra={"event": {
            **(details or {}), "database_id": self.instance.id, "module": "oracle",
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
            for name in self.REQUIRED:
                script = self.registry.get(name, DatabaseType.ORACLE)
                self.registry.authorize(script, DatabaseType.ORACLE)
            phase = "Connection"
            driver = self._driver or load_driver()
            try:
                credential = self.provider.get_credential(self.instance.credential_alias)
                validate_monitoring_credential(credential)
            except Exception:
                raise ToolkitError("CredentialRejected", "Monitoring credential unavailable or prohibited.") from None
            self._password = credential.password
            target: dict[str, str] = ({"service_name": self.instance.service_name}
                                      if self.instance.service_name
                                      else {"sid": self.instance.sid or ""})
            self._connection = driver.connect(
                user=credential.username, password=credential.password,
                host=self.instance.host, port=self.instance.port,
                tcp_connect_timeout=self.settings.connect_timeout, **target)
            self._connection.autocommit = True
            self._connection.call_timeout = self.settings.call_timeout * 1000
            if not self._connection.autocommit:
                raise ToolkitError("AutocommitRequired", "Oracle driver did not enable autocommit.")
            self.preflight_checks["connect"] = True
            self._identify_session_user()
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
        result = self._run(self.registry.get("preflight", DatabaseType.ORACLE))
        if not result.success:
            raise ToolkitError("PreflightFailed", "Oracle monitor identity or version unavailable.",
                               database_error=result.database_error)
        if result.truncated or len(result.rows) != 1:
            raise ToolkitError("PreflightFailed", "Oracle monitor identity or version unavailable.")
        row = result.rows[0]
        if (self.principal is None or row.get("monitor_user") != self.principal.raw_name):
            raise ToolkitError("PreflightFailed", "Oracle session principal could not be confirmed consistently.",
                               database_error=result.database_error)
        version = row.get("server_version")
        match = re.match(r"^(\d+)\.(\d+)", version) if isinstance(version, str) else None
        if match is None or (int(match[1]), int(match[2])) < (11, 2):
            raise ToolkitError("UnsupportedVersion", "Oracle 11gR2 or newer requires a reviewed version profile.")
        if str(row.get("monitor_user", "")).casefold() in {"sys", "system"}:
            raise ToolkitError("PrivilegedAccount", "Dedicated Oracle monitoring account required.")
        self._version = cast(str, version)
        self.instance_id = row.get("inst_id") if type(row.get("inst_id")) is int else None
        if row.get("rac_mode") not in {"YES", "NO"}:
            raise ToolkitError("PreflightFailed", "Oracle RAC capability could not be determined.")
        self.rac_enabled = row["rac_mode"] == "YES"
        privileges = self._run(self.registry.get("preflight_privileges", DatabaseType.ORACLE))
        if not privileges.success:
            raise ToolkitError("PreflightFailed", "Oracle role and privilege inventory is incomplete.",
                               database_error=privileges.database_error)
        if privileges.truncated:
            raise ToolkitError("PreflightFailed", "Oracle role and privilege inventory is incomplete.")
        allowed_system = {"CREATE SESSION"}
        for grant in privileges.rows:
            kind, name = grant.get("kind"), grant.get("privilege_name")
            if kind == "ROLE":
                raise ToolkitError("PrivilegedAccount", "Oracle monitoring account has an unreviewed role.")
            if kind == "SYSTEM" and name not in allowed_system:
                raise ToolkitError("PrivilegedAccount", "Oracle monitoring account has a non-monitor system privilege.")
        object_grants = self._run(self.registry.get("preflight_object_grants", DatabaseType.ORACLE))
        if not object_grants.success:
            raise ToolkitError("PreflightFailed", "Oracle object grant inventory is incomplete.",
                               database_error=object_grants.database_error)
        if object_grants.truncated:
            raise ToolkitError("PreflightFailed", "Oracle object grant inventory is incomplete.")
        if any(row.get("privilege") not in {"SELECT", "READ"} or row.get("grantable") != "NO"
               for row in object_grants.rows):
            raise ToolkitError("PrivilegedAccount", "Oracle monitoring account has non-read-only object grants.")
        granted_views = {str(row.get("table_name", "")).upper() for row in object_grants.rows
                         if str(row.get("owner", "")).upper() == "SYS"}
        required_views = {"V_$INSTANCE", "V_$DATABASE", "V_$SESSION", "V_$SQL",
                          "V_$TRANSACTION"}
        missing = sorted(required_views - granted_views)
        if missing:
            raise ToolkitError("PreflightDenied",
                               "Missing Oracle monitoring SELECT capability: "
                               + ", ".join("SYS." + view for view in missing)
                               + "; ask DBA to review grants manually.")
        self.permission_observations.add("CREATE SESSION")
        self.permission_observations.update("SELECT SYS." + view for view in required_views)
        self.permission_observations.update("SELECT SYS." + view for view in granted_views)
        version_object = VERSION_DETECTION_PRIVILEGES[DatabaseType.ORACLE][0].removeprefix(
            "SELECT SYS."
        )
        self.preflight_checks["version_detection_view"] = version_object in granted_views
        owned = self._run(self.registry.get("preflight_owned_objects", DatabaseType.ORACLE))
        if not owned.success:
            raise ToolkitError("PreflightFailed", "Oracle owned object inventory is incomplete.",
                               database_error=owned.database_error)
        if owned.truncated or len(owned.rows) != 1:
            raise ToolkitError("PreflightFailed", "Oracle owned object inventory is incomplete.")
        if owned.rows[0].get("owned_objects") != 0:
            raise ToolkitError("PrivilegedAccount", "Oracle monitoring account owns database objects.")
        self.preflight_checks["least_privilege"] = True
        for name in self.PROBES:
            sample = self._run(self.registry.get(name, DatabaseType.ORACLE))
            if not sample.success:
                raise ToolkitError("PreflightDenied",
                                   f"Missing Oracle monitoring capability: {name}; ask DBA to review SELECT on required SYS.V_$ views.",
                                   database_error=sample.database_error)
            self.preflight_checks[name] = True
        optional_views = {"probe_lock": "V_$LOCK", "probe_locked_object": "V_$LOCKED_OBJECT",
                          "probe_wait": "V_$SESSION_WAIT", "probe_process": "V_$PROCESS"}
        for probe_name, view in optional_views.items():
            available = view in granted_views
            if available:
                result = self._run(self.registry.get(probe_name, DatabaseType.ORACLE))
                if not result.success:
                    raise ToolkitError("PreflightDenied",
                                       f"Oracle monitoring capability {probe_name} ({view}) was granted but cannot be read.")
            self.preflight_checks[probe_name] = available
        if self.preflight_checks["probe_lock"] and self.preflight_checks["probe_locked_object"]:
            advanced = self._run(self.registry.get("blocking", DatabaseType.ORACLE))
            if not advanced.success:
                raise ToolkitError("PreflightDenied", "Oracle lock evidence query cannot be read.")
            self.blocking_script = "blocking"
        self.capabilities = {"single_instance": not self.rac_enabled,
                             "rac_global_snapshot": False,
                             "lock_evidence": self.blocking_script == "blocking",
                             "process_view": self.preflight_checks["probe_process"],
                             "session_wait_view": self.preflight_checks["probe_wait"]}

    @property
    def current_user(self) -> str | None:
        return self.principal.raw_name if self.principal and self.principal.verified else None

    def _identify_session_user(self) -> None:
        result = self._run(self.registry.get("session_identity", DatabaseType.ORACLE))
        if not result.success:
            raise ToolkitError("PreflightFailed", "Oracle session principal could not be verified.",
                               database_error=result.database_error)
        if result.truncated or len(result.rows) != 1:
            raise ToolkitError("PreflightFailed", "Oracle session principal result was incomplete.")
        raw_name = result.rows[0].get("session_user")
        if not isinstance(raw_name, str):
            raise ToolkitError("PreflightFailed", "Oracle session principal result was invalid.")
        try:
            self.principal = OraclePrincipal.from_runtime(raw_name)
        except ValueError:
            raise ToolkitError("PreflightFailed", "Oracle session principal result was invalid.") from None
        self.preflight_checks["session_identity"] = True

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
            raise ToolkitError("CleanupFailed", "Connection cleanup failed; check Oracle session state.")

    def __enter__(self) -> OracleAdapter:
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
        if isinstance(value, (bytes, bytearray, memoryview)):
            # Oracle RAW values (including V$SESSION SADDR/TADDR and
            # V$TRANSACTION.ADDR) are identity evidence.  The generic redactor
            # treats opaque objects alike, which destroys address comparison.
            return bytes(value).hex().upper()
        if isinstance(value, dict):
            return {str(key): "[REDACTED]" if SENSITIVE.search(str(key))
                    else self._safe_value(item) for key, item in value.items()}
        return redact(value)

    def execute_readonly(self, script: SqlScript) -> CollectorResult:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved Oracle monitor is required.")
        return self._run(script)

    def _run(self, script: SqlScript) -> CollectorResult:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        cursor: Cursor | None = None
        rows: tuple[dict[str, Any], ...] = ()
        database_error: dict[str, Any] | None = None
        truncated = False
        error: ToolkitError | None = None
        authorized = False
        script_name = "unregistered"
        try:
            self.registry.authorize(script, DatabaseType.ORACLE)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            self._connection.call_timeout = min(script.metadata.timeout_seconds,
                                                self.settings.call_timeout) * 1000
            cursor = self._connection.cursor()
            cursor.arraysize = min(100, script.metadata.max_rows)
            cursor.prefetchrows = min(100, script.metadata.max_rows)
            cursor.execute(script.text)
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column[0]).lower() for column in cursor.description]
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
                if error.database_error is not None:
                    to_dict = getattr(error.database_error, "to_dict", None)
                    if callable(to_dict):
                        payload = to_dict()
                        if isinstance(payload, dict):
                            database_error = payload
                    elif isinstance(error.database_error, dict):
                        database_error = error.database_error
                self._close()
            details: dict[str, Any] = {"row_count": len(rows), "truncated": truncated}
            if authorized:
                details.update({"sql_sha256": hashlib.sha256(script.text.encode()).hexdigest(),
                                "timeout_seconds": min(script.metadata.timeout_seconds,
                                                       self.settings.call_timeout),
                                "max_rows": script.metadata.max_rows})
            self._audit(script_name, started, error, details)
        return CollectorResult(self.instance.id, script_name, started_at,
                               datetime.now(timezone.utc), (time.monotonic() - started) * 1000,
                               rows, len(rows), truncated, error is None,
                               error.error_type if error else None, str(error) if error else None,
                               database_error)

    def get_server_version(self) -> str:
        if not self._ready:
            raise ToolkitError("PreflightRequired", "Connection preflight required.")
        return self._version

    def health_check(self) -> bool:
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.ORACLE)).success
