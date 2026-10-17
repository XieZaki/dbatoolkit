"""Sanitized, structured errors from supported relational database drivers."""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from dba.logging import redact


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SECRET = re.compile(
    r"(?i)(?P<key>password|passwd|pwd|secret|token|access_token)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>[^\s;,]+)"
)
_ODBC_PWD = re.compile(r"(?i)(\bPWD\s*=\s*)(?:\{[^}]*\}|[^;\s)]+)")
_ORA_CODE = re.compile(r"\bORA-(\d{5})\b", re.I)
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
    text = _ODBC_PWD.sub(r"\1<REDACTED>", text)
    text = _SECRET.sub(lambda match: f"{match.group('key')}{match.group('sep')}<REDACTED>", text)
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


def normalize_mysql_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    code = exc.args[0] if exc.args and type(exc.args[0]) is int else None
    state = getattr(exc, "sqlstate", None)
    known: Mapping[int, tuple[str, str | None]] = {
        1045: ("AUTHENTICATION_FAILED", "Check the stored monitoring credential and MySQL user@host grants."),
        1049: ("DATABASE_NOT_FOUND", "Check the configured database name; Toolkit preserves the Inventory value."),
        2003: ("CONNECTION_FAILED", "Check that the server is listening and reachable at the configured host and port."),
        2005: ("DNS_RESOLUTION_FAILED", "Check the configured host name and name resolution."),
        2006: ("SERVER_GONE_AWAY", "Check server availability, network stability, and connection limits."),
        2013: ("CONNECTION_LOST", "Check network stability and server availability."),
    }
    classification, guidance = known.get(code if code is not None else -1, ("UNKNOWN", None))
    if isinstance(exc, (TimeoutError, socket.timeout)):
        classification, guidance = "TIMEOUT", "Check the configured connection timeout and network path."
    return DatabaseErrorDetail("mysql", "PyMySQL", code, state if isinstance(state, str) else None,
                               _message(exc, secrets), classification, guidance)


def normalize_sqlserver_error(
    exc: Exception, secrets: tuple[str, ...] = (), driver: str = "pyodbc"
) -> DatabaseErrorDetail:
    args = exc.args
    state = next((item for item in args if isinstance(item, str) and re.fullmatch(r"[0-9A-Z]{5}", item)), None)
    joined = " ".join(str(item) for item in args)
    code_match = re.search(r"\((\d{3,6})\)", joined)
    code: str | int | None = int(code_match.group(1)) if code_match else None
    message = sanitize_vendor_text(joined or str(exc), secrets)
    if state in {"HYT00", "HYT01"} or isinstance(exc, TimeoutError):
        classification = "TIMEOUT"
    elif state == "28000" or code == 18456:
        classification = "AUTHENTICATION_FAILED"
    else:
        classification = "UNKNOWN"
    return DatabaseErrorDetail("sqlserver", driver, code, state, message, classification)


def normalize_postgresql_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    state = getattr(exc, "sqlstate", None)
    diag = getattr(exc, "diag", None)
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
        "3D000": ("DATABASE_NOT_FOUND", "Check the configured database name."),
        "08001": ("CONNECTION_FAILED", "Check the server endpoint and network path."),
        "08006": ("CONNECTION_LOST", "Check server availability and network stability."),
        "57014": ("TIMEOUT", "Check the configured query or connection limits."),
    }
    classification, guidance = known.get(state if isinstance(state, str) else "", ("UNKNOWN", None))
    return DatabaseErrorDetail("postgresql", "psycopg", None, state if isinstance(state, str) else None,
                               message, classification, guidance, detail, hint, context)


def normalize_oracle_error(exc: Exception, secrets: tuple[str, ...] = ()) -> DatabaseErrorDetail:
    args = exc.args
    vendor = args[0] if args else None
    code_value = getattr(vendor, "code", None)
    raw_message = getattr(vendor, "message", None) or str(exc)
    message = sanitize_vendor_text(raw_message, secrets)
    code_match = _ORA_CODE.search(message)
    code: str | int | None = f"ORA-{code_match.group(1)}" if code_match else None
    if code_value is not None and code is None:
        code = f"ORA-{code_value:05d}" if type(code_value) is int else str(code_value)
    classification = "AUTHENTICATION_FAILED" if code == "ORA-01017" else "UNKNOWN"
    guidance = "Check the stored monitoring credential." if classification == "AUTHENTICATION_FAILED" else None
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
            guidance = (
                "Ask the DBA to verify that the named Toolkit monitoring view exists for this "
                "Oracle version and that the monitor account has direct SELECT on its SYS.V_$ object."
            )
    return DatabaseErrorDetail("oracle", "python-oracledb", code, None, message,
                               classification, guidance)
