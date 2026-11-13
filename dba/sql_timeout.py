"""Global diagnostic SQL timeout policy shared by all database adapters."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dba.inventory import ValidationError
from dba.registry import MAX_SQL_TIMEOUT_SECONDS

DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS = 30
# The global value is a ceiling over reviewed per-script limits, which are
# themselves bounded by the Registry; a larger ceiling could never apply.
MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS = MAX_SQL_TIMEOUT_SECONDS


def _positive_seconds(value: object, label: str, upper: int) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        raise ValidationError(f"{label} must be an integer in 1..{upper}")
    return value


def validate_diagnostic_sql_timeout(value: object) -> int:
    return _positive_seconds(value, "diagnostic_sql_timeout_seconds", MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS)


def effective_sql_timeout(script_timeout: int, global_timeout: int,
                          legacy_cap: int | None = None) -> int:
    """Return min(script, global[, configured legacy cap]); never extends a shorter limit."""
    limits = [
        _positive_seconds(script_timeout, "script timeout_seconds", MAX_SQL_TIMEOUT_SECONDS),
        validate_diagnostic_sql_timeout(global_timeout),
    ]
    if legacy_cap is not None:
        limits.append(_positive_seconds(legacy_cap, "query_timeout", 86400))
    return min(limits)


@dataclass(frozen=True)
class SqlDeadline:
    """One absolute monotonic deadline shared by Execute and every Fetch of one SQL."""

    timeout_seconds: int
    started_at: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def start(cls, timeout_seconds: int, *, clock: Callable[[], float] = time.monotonic) -> SqlDeadline:
        _positive_seconds(timeout_seconds, "effective timeout", MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS)
        return cls(timeout_seconds, clock(), clock)

    @property
    def expires_at(self) -> float:
        return self.started_at + self.timeout_seconds

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started_at)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - self.clock())

    def expired(self) -> bool:
        return self.clock() >= self.expires_at

    def require_remaining(self, phase: str) -> float:
        """Return the remaining budget or stop before another driver operation."""
        remaining = self.remaining()
        if remaining <= 0:
            raise SqlDeadlineExpired(phase)
        return remaining


class SqlDeadlineExpired(Exception):
    """A local absolute SQL deadline elapsed without a database cancellation error."""

    def __init__(self, phase: str) -> None:
        super().__init__(f"The client SQL deadline expired during {phase}.")
        self.phase = phase
