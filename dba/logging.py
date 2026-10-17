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
URI = re.compile(r"([a-zA-Z][\w+.-]*://)[^\s/@]+:[^\s/@]*@")


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
        return URI.sub(r"\1[REDACTED]@", ASSIGNMENT.sub(r"\1=[REDACTED]", value))
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
                "sanitized_message",
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
