"""Database-specific normalization for human-readable evidence fields."""

from __future__ import annotations

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
