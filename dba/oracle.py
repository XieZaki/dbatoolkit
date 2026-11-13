"""Oracle Thin monitoring connection with reviewed, bounded SQL only."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Any, Protocol, cast

from dba.adapters import Adapter
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import CredentialProvider, Mode, validate_monitoring_credential
from dba.database_errors import normalize_oracle_error, with_timeout_evidence
from dba.errors import (
    ToolkitError,
    client_sql_deadline_error,
    database_error_log_fields,
    set_database_error_connection_status,
)
from dba.inventory import ValidationError
from dba.logging import SENSITIVE, configure_logging, redact
from dba.oracle_identity import OraclePrincipal
from dba.registry import MAX_SQL_TIMEOUT_SECONDS, SqlScript, SqlScriptRegistry, fetch_bounded_rows
from dba.settings import OracleSettings
from dba.sql_timeout import SqlDeadline, SqlDeadlineExpired
from dba.top_sql import TOP_LIMIT

ORACLE_TOP_SQL_METRICS = frozenset({
    "cpu_time", "elapsed_time", "buffer_gets", "disk_reads", "executions",
})


@dataclass(frozen=True)
class OraclePlanTarget:
    """Validated user-selected Oracle child-cursor identity."""

    sql_id: str
    child_number: int
    inst_id: int
    con_id: int | None = None
    requested_plan_hash_value: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sql_id, str) or re.fullmatch(r"[0-9A-Za-z]{13}", self.sql_id) is None:
            raise ValueError("Oracle SQL_ID must be exactly 13 alphanumeric characters.")
        if type(self.child_number) is not int or not 0 <= self.child_number <= 2_147_483_647:
            raise ValueError("Oracle CHILD_NUMBER must be a non-negative integer.")
        if type(self.inst_id) is not int or not 1 <= self.inst_id <= 999_999:
            raise ValueError("Oracle INST_ID must be a positive integer.")
        if self.con_id is not None and (type(self.con_id) is not int or not 0 <= self.con_id <= 999_999):
            raise ValueError("Oracle CON_ID must be a non-negative integer.")
        if (self.requested_plan_hash_value is not None
                and (type(self.requested_plan_hash_value) is not int
                     or self.requested_plan_hash_value <= 0)):
            raise ValueError("Oracle PLAN_HASH_VALUE must be a positive integer when supplied.")


@dataclass(frozen=True)
class OraclePlanCursor:
    """Current V$SQL cursor address evidence used only for the immediate plan read."""

    target: OraclePlanTarget
    address_hex: str
    hash_value: int
    child_address_hex: str
    plan_hash_value: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, str)
               or re.fullmatch(r"(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{16})", value) is None
               for value in (self.address_hex, self.child_address_hex)):
            raise ValueError("Oracle ADDRESS and CHILD_ADDRESS must be bounded hexadecimal RAW values.")
        if any(type(value) is not int or value <= 0
               for value in (self.hash_value, self.plan_hash_value)):
            raise ValueError("Oracle HASH_VALUE and PLAN_HASH_VALUE must be positive integers.")


class Cursor(Protocol):
    description: Sequence[Sequence[Any]] | None
    arraysize: int
    prefetchrows: int

    def execute(self, sql: str, parameters: Mapping[str, Any] | None = None) -> Any: ...
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
    from dba.driver_manager import load_database_driver

    return cast(Driver, load_database_driver(DatabaseType.ORACLE, "oracledb"))


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
    # Never pass through a driver exception: it can contain a DSN or credentials.
    detail = normalize_oracle_error(exc, (secret,))
    number = getattr(exc, "code", None)
    if exc.args:
        number = getattr(exc.args[0], "code", number)
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
    if detail.error_category == "TIMEOUT" or number in {12170, 12535, 3136}:
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.", database_error=detail)
    if detail.error_category == "PERMISSION_DENIED":
        return ToolkitError(
            f"{phase}Denied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this Oracle version and denied operation.",
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
    if phase == "Query" and number == 1031:
        return ToolkitError(
            "QueryDenied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this Oracle version and denied operation.",
            database_error=detail,
        )
    if number == 942:
        return ToolkitError(
            "CapabilityUnavailable",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this Oracle version and denied operation.",
            database_error=detail,
        )
    if number == 904:
        return ToolkitError("CapabilityUnavailable", "Required Oracle monitoring column is unavailable.", database_error=detail)
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
                "blocking", "blocking_basic", "previous_sql")
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
        self.capabilities: dict[str, bool] = {}
        self.blocking_script = "blocking"

    def _audit(self, script: str, started: float, error: ToolkitError | None,
               details: dict[str, Any] | None = None) -> None:
        configure_logging().info("event", extra={"event": {
            **(details or {}), "database_id": self.instance.id, "module": "oracle",
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
            self._connection.call_timeout = self.settings.effective_sql_timeout(MAX_SQL_TIMEOUT_SECONDS) * 1000
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
        owned = self._run(self.registry.get("preflight_owned_objects", DatabaseType.ORACLE))
        if not owned.success:
            raise ToolkitError("PreflightFailed", "Oracle owned object inventory is incomplete.",
                               database_error=owned.database_error)
        if owned.truncated or len(owned.rows) != 1:
            raise ToolkitError("PreflightFailed", "Oracle owned object inventory is incomplete.")
        if owned.rows[0].get("owned_objects") != 0:
            raise ToolkitError("PrivilegedAccount", "Oracle monitoring account owns database objects.")
        self.preflight_checks["least_privilege"] = True
        self.capabilities = {"single_instance": not self.rac_enabled,
                             "rac_global_snapshot": False,
                             # This is a routed source, not a permission assertion.
                             # A denied blocking read is reported by the actual query.
                             "lock_evidence": True}

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

    def execute_top_sql_readonly(self, script: SqlScript, top_n: int, metric: str,
                                 *, con_id: int | None = None) -> CollectorResult:
        """Execute only the two reviewed Oracle Top SQL scripts with fixed binds."""
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved Oracle monitor is required.")
        if (not isinstance(metric, str) or metric not in ORACLE_TOP_SQL_METRICS
                or type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT):
            raise ToolkitError("InvalidTopSQLParameters", "Oracle Top SQL requires an approved metric and Top N 1-100.")
        metadata = script.metadata
        if (metadata.name not in {"top_sql_11g", "top_sql_modern"}
                or metadata.feature != "top-sql" or metadata.risk != "READ_ONLY"
                or not metadata.reviewed or metadata.max_rows != TOP_LIMIT + 1):
            raise ToolkitError("RegistryRejected", "Oracle Top SQL accepts only its reviewed bounded Registry scripts.")
        parameters: dict[str, Any] = {"metric": metric, "top_n": top_n}
        if metadata.name == "top_sql_11g":
            if con_id is not None or "11gr2" not in metadata.version_families:
                raise ToolkitError("RegistryRejected", "Oracle 11g Top SQL route does not accept a container bind.")
        else:
            if (type(con_id) is not int or con_id < 0
                    or not (set(metadata.version_families) - {"11gr2"})):
                raise ToolkitError("InvalidTopSQLParameters", "Oracle 12c+ Top SQL requires the current non-negative CON_ID.")
            parameters["con_id"] = con_id
        return self._run(script, parameters)

    def execute_plan_identity_readonly(self, script: SqlScript,
                                       target: OraclePlanTarget) -> CollectorResult:
        """Bind only a validated Oracle SQL_ID/child and local instance/container to reviewed plan SQL."""
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved Oracle monitor is required.")
        legacy = script.metadata.name == "oracle_plan_identity_11g"
        expected_name = "oracle_plan_identity_11g" if target.con_id is None else "oracle_plan_identity_modern"
        if script.metadata.name != expected_name or legacy != (target.con_id is None):
            raise ToolkitError("RegistryRejected", "Oracle plan identity script does not match the target version route.")
        self._validate_plan_script(script, expected_name, 2, legacy)
        parameters: dict[str, Any] = {
            "sql_id": target.sql_id,
            "child_number": target.child_number,
            "inst_id": target.inst_id,
        }
        if target.con_id is not None:
            parameters["con_id"] = target.con_id
        return self._run(script, parameters)

    def execute_plan_rows_readonly(self, script: SqlScript,
                                   cursor_identity: OraclePlanCursor) -> CollectorResult:
        """Read bounded plan rows only for the immediately verified V$SQL cursor identity."""
        if not self._ready:
            raise ToolkitError("PreflightRequired", "A preflight-approved Oracle monitor is required.")
        target = cursor_identity.target
        legacy = target.con_id is None
        expected_name = "oracle_plan_rows_11g" if legacy else "oracle_plan_rows_modern"
        if script.metadata.name != expected_name:
            raise ToolkitError("RegistryRejected", "Oracle plan rows script does not match the target version route.")
        self._validate_plan_script(script, expected_name, 501, legacy)
        parameters: dict[str, Any] = {
            "sql_id": target.sql_id,
            "child_number": target.child_number,
            "inst_id": target.inst_id,
            "address_hex": cursor_identity.address_hex.upper(),
            "hash_value": cursor_identity.hash_value,
            "child_address_hex": cursor_identity.child_address_hex.upper(),
            "plan_hash_value": cursor_identity.plan_hash_value,
        }
        if target.con_id is not None:
            parameters["con_id"] = target.con_id
        return self._run(script, parameters)

    @staticmethod
    def _validate_plan_script(script: SqlScript, expected_name: str,
                              expected_max_rows: int, legacy: bool) -> None:
        metadata = script.metadata
        if (metadata.name != expected_name or metadata.feature != "plan"
                or metadata.risk != "READ_ONLY" or not metadata.reviewed
                or metadata.database_type != DatabaseType.ORACLE
                or metadata.max_rows != expected_max_rows
                or (legacy and metadata.version_families != ("11gr2",))
                or (not legacy and (not metadata.version_families
                                    or "11gr2" in metadata.version_families))):
            raise ToolkitError("RegistryRejected", "Oracle Plan accepts only its reviewed bounded Registry scripts.")

    def _run(self, script: SqlScript,
             bind_parameters: Mapping[str, Any] | None = None) -> CollectorResult:
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
            self.registry.authorize(script, DatabaseType.ORACLE)
            authorized = True
            script_name = script.metadata.name
            if self._connection is None:
                raise ToolkitError("NotConnected", "Connection is closed.")
            deadline = SqlDeadline.start(
                self.settings.effective_sql_timeout(script.metadata.timeout_seconds)
            )

            def set_call_timeout(remaining: float) -> None:
                assert self._connection is not None
                self._connection.call_timeout = max(1, math.ceil(remaining * 1000))

            cursor = self._connection.cursor()
            cursor.arraysize = min(100, script.metadata.max_rows)
            cursor.prefetchrows = min(100, script.metadata.max_rows)
            # The driver applies this value per round trip inside the call.
            set_call_timeout(deadline.require_remaining("Execute"))
            deadline.require_remaining("Execute")
            if bind_parameters is None:
                cursor.execute(script.text)
            else:
                cursor.execute(script.text, dict(bind_parameters))
            deadline.require_remaining("Execute")
            if cursor.description is None:
                raise ToolkitError("InvalidResult", "Read-only script returned no rowset.")
            columns = [str(column[0]).lower() for column in cursor.description]
            execution_phase = "Fetch"
            bounded = fetch_bounded_rows(
                cursor, script.metadata.max_rows, script.metadata.max_result_bytes,
                deadline=deadline, before_fetch=set_call_timeout,
            )
            truncated = bounded.truncated
            result_bytes = bounded.result_bytes
            rows = tuple(self._safe_value(dict(zip(columns, row, strict=True))) for row in bounded.rows)
        except ToolkitError as exc:
            error = exc
        except SqlDeadlineExpired:
            assert deadline is not None
            error = client_sql_deadline_error(
                "oracle", "python-oracledb", execution_phase=execution_phase,
                timeout_seconds=deadline.timeout_seconds,
                script_timeout_seconds=script.metadata.timeout_seconds,
                elapsed_ms=int(deadline.elapsed() * 1000),
                script_path=f"{script.metadata.database_type.value}/{script_name}.sql",
            )
        except (ValidationError, OSError, UnicodeError) as exc:
            error = (ToolkitError("RegistryRejected", "SQL is not an unchanged reviewed script from this registry.")
                     if not authorized else _driver_error(
                         exc, "Query", self._password, execution_phase=execution_phase,
                         timeout_seconds=self.settings.effective_sql_timeout(
                             script.metadata.timeout_seconds),
                         effective_timeout_seconds=self.settings.effective_sql_timeout(
                             script.metadata.timeout_seconds),
                         script_timeout_seconds=script.metadata.timeout_seconds,
                         elapsed_ms=int((time.monotonic() - started) * 1000),
                         script_path=f"{script.metadata.database_type.value}/{script_name}.sql"))
        except Exception as exc:
            error = _driver_error(
                exc, "Query", self._password, execution_phase=execution_phase,
                timeout_seconds=self.settings.effective_sql_timeout(script.metadata.timeout_seconds),
                effective_timeout_seconds=self.settings.effective_sql_timeout(
                    script.metadata.timeout_seconds),
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
                                "timeout_seconds": self.settings.effective_sql_timeout(
                                    script.metadata.timeout_seconds),
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
        return self.execute_readonly(self.registry.get("server_info", DatabaseType.ORACLE)).success
