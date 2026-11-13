import json
import logging
import re
from collections.abc import Mapping
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

SENSITIVE = re.compile(r"password|pwd|secret|token|connection.?string|dsn", re.I)
ASSIGNMENT = re.compile(
    r"""(?i)\b(password|pwd|secret|token)["']?\s*[:=]\s*"""
    r"""(?:\{(?:[^}]|}})*\}|N?"(?:[^"\\]|\\.|"")*"|N?'(?:[^'\\]|\\.|'')*'|[^;\s,]+)"""
)


def _redact_uri_credentials(value: str) -> str:
    """Hide URI user-info without retrying every position on long non-URI text."""
    pieces: list[str] = []
    copied_until = 0
    search_from = 0
    while True:
        separator = value.find("://", search_from)
        if separator < 0:
            break
        scheme_start = separator
        while scheme_start > 0:
            char = value[scheme_start - 1]
            if not (char.isalnum() or char in "_+.-"):
                break
            scheme_start -= 1
        scheme = value[scheme_start:separator]
        if (scheme_start < copied_until or not scheme or not scheme[0].isascii()
                or not scheme[0].isalpha()):
            search_from = separator + 3
            continue

        authority_start = separator + 3
        authority_end = authority_start
        while authority_end < len(value) and not value[authority_end].isspace() \
                and value[authority_end] != "/":
            authority_end += 1
        authority = value[authority_start:authority_end]
        at_sign = authority.find("@")
        if at_sign < 0 or ":" not in authority[:at_sign]:
            search_from = max(authority_end, separator + 3)
            continue

        user_info_end = authority_start + at_sign
        pieces.extend((value[copied_until:authority_start], "[REDACTED]@"))
        copied_until = user_info_end + 1
        search_from = copied_until

    pieces.append(value[copied_until:])
    return "".join(pieces)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): (v if str(k).casefold() == "password" and v == "STORED" else
                     "[REDACTED]" if SENSITIVE.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_uri_credentials(ASSIGNMENT.sub(r"\1=[REDACTED]", value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED OBJECT]"


class SafeFormatter(logging.Formatter):
    """Only structured event fields; never format arbitrary messages/tracebacks.

    Unknown free text can contain unlabeled secrets, so it is never emitted.
    """

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", {})
        if not isinstance(event, dict):
            event = {}
        data = {
            key: event.get(key)
            for key in (
                "database_id",
                "target",
                "module",
                "analyzer",
                "sql_script",
                "result",
                "elapsed_time",
                "error_type",
                "sql_sha256",
                "timeout_seconds",
                "max_rows",
                "row_count",
                "truncated",
                "database_type",
                "driver",
                "vendor_code",
                "sqlstate",
                "classification",
                "error_category",
                "sanitized_message",
                "execution_phase",
                "effective_timeout_seconds",
                "script_timeout_seconds",
                "elapsed_ms",
                "cancellation_status",
                "connection_status",
                "script_path",
                "mechanism",
                "cancellation_reason",
                "replication_role",
                "replication_health",
                "replication_issue_count",
                "replication_highest_severity",
            )
        }
        data["time"] = self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
        return json.dumps(redact(data), ensure_ascii=False)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("dba")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "dba.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(SafeFormatter())
        logger.addHandler(handler)
    return logger
