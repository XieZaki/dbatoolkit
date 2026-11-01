"""Top SQL report payload using the shared diagnostic report protocol."""

from dataclasses import dataclass, field
from typing import Any

from dba.core import DiagnosticReport


@dataclass(frozen=True)
class TopSQLDiagnosticReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)
