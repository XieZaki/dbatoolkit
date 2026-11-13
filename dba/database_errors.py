"""Sanitized, structured errors from supported relational database drivers."""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from dba.logging import redact


@dataclass(frozen=True)
class DatabaseTimeoutEvidence:
    """Timeout policy and cancellation outcome for one reviewed SQL execution."""

    execution_phase: str
    timeout_seconds: int | None
    effective_timeout_seconds: int | None
    script_timeout_seconds: int | None
    elapsed_ms: int | None
    cancellation_status: str
    connection_status: str
    script_path: str | None
    mechanism: str | None = None
    cancellation_reason: str | None = None


@dataclass(frozen=True)
class DatabaseErrorDetail:
    database_type: str
    driver: str
    vendor_code: str | int | None
    sqlstate: str | None
    message: str
    classification: str = "UNKNOWN"
    guidance: str | None = None
    detail: str | None = None
    hint: str | None = None
    context: str | None = None
    error_category: str = "SQL_EXECUTION_ERROR"
    timeout: DatabaseTimeoutEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SECRET = re.compile(
    r"""(?i)(?P<key>\b(?:password|passwd|pwd|secret|token|access_token|uid|user(?:name|\s+id)?))"""
    r"""(?P<sep>["']?\s*[:=]\s*)(?:\{(?:[^}]|}})*\}|N?"(?:[^"\\]|\\.|"")*"|"""
    r"""N?'(?:[^'\\]|\\.|'')*'|[^;\s,)]+)"""
)
_ORACLE_CODE = re.compile(r"\b(?:ORA-\d{5}|(?:DPI|DPY|TNS)-\d{4,5})\b", re.I)
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_ODBC_NATIVE_CODE = re.compile(r"\((-?\d{1,9})\)(?=\s*(?:\(SQL\w+\)\s*)?(?:[.;]|$))")
_PASSWORD_USED = re.compile(r"(?i)using password:\s*YES")
_MAX_TEXT = 4000


def sanitize_vendor_text(value: object, secrets: tuple[str, ...] = ()) -> str:
    """Keep useful vendor evidence while removing credential material."""
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<REDACTED>")
    password_used: list[str] = []

    def preserve_password_used(match: re.Match[str]) -> str:
        marker = f"__DBA_PASSWORD_USED_{len(password_used)}__"
        password_used.append(match.group(0))
        return marker

    text = _PASSWORD_USED.sub(preserve_password_used, text)
    text = _SECRET.sub(r"\g<key>\g<sep>[REDACTED]", text)
    safe = redact(text)
    if isinstance(safe, str):
        for index, phrase in enumerate(password_used):
            safe = safe.replace(f"__DBA_PASSWORD_USED_{index}__", phrase)
    return (safe if isinstance(safe, str) else "Database driver reported an error")[:_MAX_TEXT]


def _message(exc: Exception, secrets: tuple[str, ...] = ()) -> str:
    args = exc.args
    if len(args) > 1 and isinstance(args[1], str):
        value = args[1]
    elif args and isinstance(args[0], str):
        value = args[0]
    else:
        value = str(exc)
    return sanitize_vendor_text(value, secrets)


def _sqlstate(value: object) -> str | None:
    return value if isinstance(value, str) and _SQLSTATE.fullmatch(value) else None


def _cancellation_context(
    detail: DatabaseErrorDetail, execution_phase: str
) -> tuple[str, str | None, str | None]:
    code = str(detail.vendor_code or "").upper()
    state = (detail.sqlstate or "").upper()
    message = " ".join((detail.message, detail.detail or "", detail.hint or "",
                        detail.context or "")).casefold()
    if execution_phase == "Connection":
        if detail.database_type == "sqlserver" and state == "HYT01":
            return "NOT_APPLICABLE", "ODBC_CONNECTION_TIMEOUT", None
        if detail.database_type == "sqlserver" and state == "HYT00":
            return "NOT_APPLICABLE", "ODBC_LOGIN_TIMEOUT", None
        return "NOT_APPLICABLE", "CLIENT_CONNECT_TIMEOUT", None
    if detail.error_category == "CANCELLED_CONFIRMED":
        if detail.database_type == "postgresql" and state == "57014":
            if "user request" in message:
                return "CANCELLED_CONFIRMED", "POSTGRESQL_QUERY_CANCEL", "user_cancel_reported"
            if "recovery conflict" in message:
                return "CANCELLED_CONFIRMED", "POSTGRESQL_QUERY_CANCEL", "recovery_conflict_reported"
            return "CANCELLED_CONFIRMED", "POSTGRESQL_QUERY_CANCEL", "query_cancelled_cause_unspecified"
        if detail.database_type == "mysql" and code == "3024":
            return "CANCELLED_CONFIRMED", "MYSQL_MAX_EXECUTION_TIME", "max_execution_time"
        if detail.database_type == "mysql" and code == "1317":
            return "CANCELLED_CONFIRMED", "MYSQL_QUERY_INTERRUPTED", "server_query_interrupted_source_unknown"
        if detail.database_type == "oracle" and code == "ORA-01013":
            return "CANCELLED_CONFIRMED", "ORACLE_CANCEL_RESPONSE", "cancellation_source_unknown"
        return "CANCELLED_CONFIRMED", "DATABASE_CANCEL_ERROR", "server_cancel_confirmed"
    if detail.error_category in {"CONNECTION_LOST", "CANCEL_STATUS_UNKNOWN"}:
        if detail.database_type == "mysql" and code == "2013":
            return "CANCEL_STATUS_UNKNOWN", "MYSQL_SOCKET_READ_CONNECTION_LOST", "database_stop_unconfirmed"
        if detail.database_type == "sqlserver" and state == "HY008":
            return "CANCEL_STATUS_UNKNOWN", "ODBC_OPERATION_CANCELLED", "server_stop_unconfirmed"
        mechanism = {
            "sqlserver": "ODBC_CONNECTION_LOST",
            "postgresql": "POSTGRESQL_CONNECTION_LOST",
            "mysql": "MYSQL_CONNECTION_LOST",
            "oracle": "ORACLE_CONNECTION_LOST",
        }.get(detail.database_type)
        return "CANCEL_STATUS_UNKNOWN", mechanism, "database_stop_unconfirmed"
    if detail.error_category == "TIMEOUT":
        if detail.database_type == "sqlserver" and state == "HYT00":
            return "DRIVER_REPORTED", "ODBC_QUERY_TIMEOUT_ATTENTION", "driver_reported_query_timeout"
        if detail.database_type == "postgresql" and state == "57014":
            return ("CANCELLED_CONFIRMED", "POSTGRESQL_STATEMENT_TIMEOUT",
                    "statement_timeout_reported_by_server")
        if detail.database_type == "mysql" and code == "3024":
            return "CANCELLED_CONFIRMED", "MYSQL_MAX_EXECUTION_TIME", "max_execution_time"
        if detail.database_type == "mysql" and code == "1205":
            return "CANCELLED_CONFIRMED", "MYSQL_LOCK_WAIT_TIMEOUT", "innodb_lock_wait_timeout"
        if detail.database_type == "oracle" and code == "DPY-4024":
            return "DRIVER_REPORTED", "ORACLE_CALL_TIMEOUT", "driver_reported_call_timeout"
        return "CANCEL_STATUS_UNKNOWN", "CLIENT_WAIT_TIMEOUT", "client_wait_stopped_database_stop_unconfirmed"
    return "NOT_APPLICABLE", None, None


def with_timeout_evidence(
    detail: DatabaseErrorDetail,
    *,
    execution_phase: str,
    timeout_seconds: int | None,
    effective_timeout_seconds: int | None,
    script_timeout_seconds: int | None,
    elapsed_ms: int | None,
    script_path: str | None,
) -> DatabaseErrorDetail:
    pg_message = " ".join((detail.message, detail.detail or "", detail.hint or "",
                            detail.context or "")).casefold()
    if (
        detail.database_type == "postgresql"
        and detail.sqlstate == "57014"
        and detail.error_category == "CANCELLED_CONFIRMED"
        and ("statement timeout" in pg_message or "statement_timeout" in pg_message)
        and "user request" not in pg_message
        and "recovery conflict" not in pg_message
    ):
        detail = replace(
            detail,
            classification="TIMEOUT",
            guidance="The server reported that statement_timeout cancelled the query.",
            error_category="TIMEOUT",
        )
    cancellation_status, mechanism, reason = _cancellation_context(detail, execution_phase)
    evidence = DatabaseTimeoutEvidence(
        execution_phase=execution_phase,
        timeout_seconds=timeout_seconds,
        effective_timeout_seconds=effective_timeout_seconds,
        script_timeout_seconds=script_timeout_seconds,
        elapsed_ms=elapsed_ms,
        cancellation_status=cancellation_status,
        connection_status="UNKNOWN",
        script_path=script_path,
        mechanism=mechanism,
        cancellation_reason=reason,
    )
    return replace(detail, timeout=evidence)


def with_connection_status(detail: DatabaseErrorDetail, status: str) -> DatabaseErrorDetail:
    if detail.timeout is None:
        return detail
    return replace(detail, timeout=replace(detail.timeout, connection_status=status))


def normalize_mysql_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    code = exc.args[0] if exc.args and type(exc.args[0]) is int else None
    state = _sqlstate(getattr(exc, "sqlstate", None))
    known: Mapping[int, tuple[str, str | None]] = {
        1045: ("AUTHENTICATION_FAILED", "Check the stored monitoring credential and MySQL user@host grants."),
        1044: ("ACCESS_DENIED", "Review docs/DATABASE_PERMISSIONS.md for this MySQL version and denied operation."),
        1142: ("ACCESS_DENIED", "Review docs/DATABASE_PERMISSIONS.md for this MySQL version and denied operation."),
        1227: ("ACCESS_DENIED", "Review docs/DATABASE_PERMISSIONS.md for this MySQL version and denied operation."),
        1049: ("DATABASE_NOT_FOUND", "Check the configured database name; Toolkit preserves the Inventory value."),
        2003: ("CONNECTION_FAILED", "Check that the server is listening and reachable at the configured host and port."),
        2005: ("DNS_RESOLUTION_FAILED", "Check the configured host name and name resolution."),
        2006: ("SERVER_GONE_AWAY", "Check server availability, network stability, and connection limits."),
        2013: ("CONNECTION_LOST", "Check network stability and server availability."),
        1205: ("TIMEOUT", "Check the reviewed query's lock-wait behavior and database timeout settings."),
        1317: ("CANCELLED_CONFIRMED", None),
        3024: ("TIMEOUT", "Check the effective MySQL statement execution limit."),
    }
    classification, guidance = known.get(code if code is not None else -1, ("UNKNOWN", None))
    if isinstance(exc, (TimeoutError, socket.timeout)):
        classification, guidance = "TIMEOUT", "Check the configured connection timeout and network path."
    if code in {3024, 1317, 1205} and isinstance(exc, (TimeoutError, socket.timeout)):
        classification, guidance = "TIMEOUT", "Check the effective MySQL statement or lock-wait limit."
    if classification == "ACCESS_DENIED":
        category = "PERMISSION_DENIED"
    elif classification == "AUTHENTICATION_FAILED":
        category = "AUTHENTICATION_FAILED"
    elif code in {2006, 2013}:
        category = "CONNECTION_LOST"
    elif classification in {"CONNECTION_FAILED", "DNS_RESOLUTION_FAILED"}:
        category = "CONNECTION_FAILED"
    elif classification == "CANCELLED_CONFIRMED":
        category = "CANCELLED_CONFIRMED"
    elif classification == "TIMEOUT":
        category = "TIMEOUT"
    else:
        category = "SQL_EXECUTION_ERROR"
    return DatabaseErrorDetail("mysql", "PyMySQL", code, state,
                               _message(exc, secrets), classification, guidance,
                               error_category=category)


def normalize_sqlserver_error(
    exc: Exception, secrets: tuple[str, ...] = (), driver: str = "pyodbc"
) -> DatabaseErrorDetail:
    args = exc.args
    state = next((item for item in args if _sqlstate(item) is not None), None)
    joined = " ".join(str(item) for item in args if _sqlstate(item) is None)
    if state is None:
        state_match = re.match(r"\s*\[([0-9A-Z]{5})\]", joined)
        state = state_match.group(1) if state_match else None
    code_match = _ODBC_NATIVE_CODE.search(joined) if state is not None else None
    code: str | int | None = int(code_match.group(1)) if code_match else None
    message = sanitize_vendor_text(joined or str(exc), secrets)
    if state in {"HYT00", "HYT01"} or isinstance(exc, TimeoutError):
        classification = "TIMEOUT"
    elif state is not None and state.startswith("08"):
        classification = "CONNECTION_LOST"
    elif state == "HY008":
        classification = "CANCEL_STATUS_UNKNOWN"
    elif state == "28000" or code == 18456:
        classification = "AUTHENTICATION_FAILED"
    elif code in {229, 297, 300, 916}:
        classification = "ACCESS_DENIED"
    else:
        classification = "UNKNOWN"
    if classification == "ACCESS_DENIED":
        category = "PERMISSION_DENIED"
    elif classification == "AUTHENTICATION_FAILED":
        category = "AUTHENTICATION_FAILED"
    elif classification == "CONNECTION_LOST":
        category = "CONNECTION_LOST"
    elif classification == "CANCEL_STATUS_UNKNOWN":
        category = "CANCEL_STATUS_UNKNOWN"
    elif classification == "TIMEOUT":
        category = "TIMEOUT"
    else:
        category = "SQL_EXECUTION_ERROR"
    guidance = (
        "Review docs/DATABASE_PERMISSIONS.md for this SQL Server version and denied operation."
        if classification == "ACCESS_DENIED" else None
    )
    return DatabaseErrorDetail("sqlserver", driver, code, state, message, classification, guidance,
                               error_category=category)


def normalize_postgresql_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    diag = getattr(exc, "diag", None)
    state = _sqlstate(getattr(exc, "sqlstate", None)) or _sqlstate(getattr(diag, "sqlstate", None))
    primary = getattr(diag, "message_primary", None)
    message = sanitize_vendor_text(primary or str(exc), secrets)
    raw_detail = getattr(diag, "message_detail", None)
    raw_hint = getattr(diag, "message_hint", None)
    raw_context = getattr(diag, "context", None)
    detail = sanitize_vendor_text(raw_detail, secrets) if raw_detail else None
    hint = sanitize_vendor_text(raw_hint, secrets) if raw_hint else None
    context = sanitize_vendor_text(raw_context, secrets) if raw_context else None
    known: Mapping[str, tuple[str, str | None]] = {
        "28P01": ("AUTHENTICATION_FAILED", "Check the stored monitoring credential and PostgreSQL authentication rules."),
        "42501": ("ACCESS_DENIED", "Review docs/DATABASE_PERMISSIONS.md for this PostgreSQL version and denied operation."),
        "3D000": ("DATABASE_NOT_FOUND", "Check the configured database name."),
        "08001": ("CONNECTION_FAILED", "Check the server endpoint and network path."),
        "08006": ("CONNECTION_LOST", "Check server availability and network stability."),
    }
    classification, guidance = known.get(state or "", ("UNKNOWN", None))
    if state == "57014":
        classification, guidance = "CANCELLED_CONFIRMED", None
    elif state is not None and state.startswith("08"):
        classification, guidance = "CONNECTION_LOST", "Check server availability and network stability."
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        classification, guidance = "TIMEOUT", "Check the client network wait limit; server-side cancellation is unconfirmed."
    if classification == "ACCESS_DENIED":
        category = "PERMISSION_DENIED"
    elif classification == "AUTHENTICATION_FAILED":
        category = "AUTHENTICATION_FAILED"
    elif classification in {"CONNECTION_FAILED", "CONNECTION_LOST"}:
        category = classification
    elif classification == "TIMEOUT":
        category = "TIMEOUT"
    elif classification == "CANCELLED_CONFIRMED":
        category = "CANCELLED_CONFIRMED"
    else:
        category = "SQL_EXECUTION_ERROR"
    return DatabaseErrorDetail("postgresql", "psycopg", None, state,
                               message, classification, guidance, detail, hint, context,
                               error_category=category)


def normalize_oracle_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    args = exc.args
    vendor = args[0] if args else None
    code_value = getattr(vendor, "code", None)
    raw_message = getattr(vendor, "message", None) or str(exc)
    message = sanitize_vendor_text(raw_message, secrets)
    full_code = getattr(vendor, "full_code", None)
    code_match = (_ORACLE_CODE.fullmatch(full_code) if isinstance(full_code, str) else None)
    code_match = code_match or _ORACLE_CODE.search(str(raw_message))
    code = code_match.group(0).upper() if code_match else None
    if code is None and type(code_value) is int and 0 < code_value <= 99999:
        code = f"ORA-{code_value:05d}"
    if code == "DPY-4024" or isinstance(exc, TimeoutError):
        classification = "TIMEOUT"
    elif code in {"DPY-4011", "DPY-1001"} or getattr(vendor, "is_session_dead", False) is True:
        classification = "CONNECTION_LOST"
    elif code in {"ORA-12170", "ORA-12535", "ORA-03136"}:
        classification = "TIMEOUT"
    elif code == "ORA-01013":
        classification = "CANCELLED_CONFIRMED"
    elif code == "ORA-01017":
        classification = "AUTHENTICATION_FAILED"
    elif code in {"ORA-01031", "ORA-01045"}:
        classification = "ACCESS_DENIED"
    else:
        classification = "UNKNOWN"
    if classification == "ACCESS_DENIED":
        category = "PERMISSION_DENIED"
    elif classification in {"AUTHENTICATION_FAILED", "CONNECTION_LOST", "TIMEOUT", "CANCELLED_CONFIRMED"}:
        category = classification
    else:
        category = "SQL_EXECUTION_ERROR"
    guidance = ("Check the stored monitoring credential." if classification == "AUTHENTICATION_FAILED" else
                "Review docs/DATABASE_PERMISSIONS.md for this Oracle version and denied operation."
                if classification == "ACCESS_DENIED" else None)
    if code == "ORA-00942":
        # ORA-00942 is ambiguous in general. Classify only when Oracle names a
        # fixed monitoring object used by reviewed Toolkit SQL; the object may
        # still be absent on that server version, so guidance remains qualified.
        known_monitoring_objects = {
            "V_$INSTANCE", "V_$VERSION", "V_$DATABASE", "V_$SESSION", "V_$SQL",
            "V_$TRANSACTION", "V_$DATAGUARD_STATS", "V_$MANAGED_STANDBY",
            "V_$DATAGUARD_PROCESS", "V_$ARCHIVE_DEST_STATUS", "V_$LOCK",
            "V_$LOCKED_OBJECT", "V_$PROCESS", "V_$SESSION_WAIT",
            "V_$RECOVERY_FILE_DEST", "V_$RECOVERY_AREA_USAGE", "V_$ARCHIVED_LOG",
            "V_$LOG_HISTORY",
        }
        normalized = message.replace('"', "").replace("'", "").upper()
        if any(re.search(rf"(?<![A-Z0-9_$.])(?:SYS\.)?{re.escape(name)}(?![A-Z0-9_$])", normalized)
               for name in known_monitoring_objects):
            classification = "MONITORING_OBJECT_UNAVAILABLE"
            category = "SQL_EXECUTION_ERROR"
            guidance = (
                "Ask the DBA to verify that the named Toolkit monitoring view exists for this "
                "Oracle version and that the monitor account has direct SELECT on its SYS.V_$ object."
            )
    if code is None and isinstance(exc, (TimeoutError, socket.timeout)):
        category = "TIMEOUT"
    return DatabaseErrorDetail("oracle", "python-oracledb", code, None, message,
                               classification, guidance, error_category=category)
