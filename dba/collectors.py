from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dba.core import DatabaseInstance


@dataclass(frozen=True)
class CollectorResult:
    database_id: str
    collector_name: str
    started_at: datetime
    finished_at: datetime
    elapsed_ms: float
    rows: tuple[dict[str, Any], ...]
    row_count: int
    truncated: bool
    success: bool
    error_type: str | None = None
    error_message: str | None = None
    database_error: dict[str, Any] | None = None


class Collector(ABC):
    @abstractmethod
    def collect(self, instance: DatabaseInstance) -> CollectorResult: ...


class NativeTool(ABC):
    """Separate audited native read-only tooling, e.g. future remote mysqlbinlog.

    No arbitrary shell/command interface. Implementations must define their own
    fixed argument allowlist and time/output limits, without server SSH.
    """

    @abstractmethod
    def collect(self, instance: DatabaseInstance) -> CollectorResult: ...
