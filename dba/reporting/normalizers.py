"""Database-specific normalization for human-readable evidence fields."""

from __future__ import annotations

import re
from typing import Any

_SQLSERVER_TRANSACTION_TYPE = {1: "Read/Write", 2: "Read-Only", 3: "System", 4: "Distributed"}
_SQLSERVER_TRANSACTION_STATE = {
    0: "Not Initialized", 1: "Initialized", 2: "Active", 3: "Ended",
    4: "Commit Initiated", 5: "Prepared", 6: "Committed", 7: "Rolling Back", 8: "Rolled Back",
}


def sqlserver_transaction_label(value: Any, *, state: bool = False) -> str:
    """Translate documented SQL Server DMV enums; preserve unknown numeric values."""
    if type(value) is not int:
        return "—" if value is None else str(value)
    labels = _SQLSERVER_TRANSACTION_STATE if state else _SQLSERVER_TRANSACTION_TYPE
    return labels.get(value, f"Unknown ({value})")


def postgresql_version_label(value: Any) -> str:
    """Render PostgreSQL server_version or server_version_num consistently."""
    if value is None:
        return "UNKNOWN"
    if type(value) is int:
        return _postgresql_version_num_label(value)
    if not isinstance(value, str):
        return str(value)
    raw = value.strip()
    if raw.isdecimal() and len(raw) >= 5:
        return _postgresql_version_num_label(int(raw))
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if match is None:
        return raw or "UNKNOWN"
    major, minor = int(match[1]), int(match[2])
    if major >= 10:
        return f"{major}.{minor}"
    patch = match[3]
    return f"{major}.{minor}" + (f".{int(patch)}" if patch is not None else "")


def postgresql_server_version_num(value: Any) -> str | None:
    """Return a raw numeric PostgreSQL server_version_num without rewriting it."""
    if type(value) is int and value >= 10000:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and len(value) >= 5:
        return value
    return None


def _postgresql_version_num_label(value: int) -> str:
    if value < 10000:
        return str(value)
    if value >= 100000:
        return f"{value // 10000}.{value % 10000}"
    major, remainder = divmod(value, 10000)
    minor, patch = divmod(remainder, 100)
    return f"{major}.{minor}.{patch}"
