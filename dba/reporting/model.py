"""Database-neutral immutable types used by report presenters and renderer."""

from __future__ import annotations

from dataclasses import dataclass

from dba.incident import IncidentSummary


@dataclass(frozen=True)
class HumanReportPolicy:
    max_root_blockers: int = 20
    max_blocking_nodes_per_root: int = 50
    max_related_sessions: int = 100
    max_blocked_requests: int = 50
    max_nonblocked_long_running: int = 25
    max_transactions: int = 50
    max_wait_rows: int = 50
    max_appendix_sessions: int = 50
    max_sql_chars: int = 16 * 1024
    max_plan_operators: int = 10
    soft_report_bytes: int = 2 * 1024 * 1024
    hard_report_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        limits = (self.max_root_blockers, self.max_blocking_nodes_per_root,
                  self.max_related_sessions, self.max_blocked_requests,
                  self.max_nonblocked_long_running, self.max_transactions,
                  self.max_wait_rows, self.max_appendix_sessions, self.max_sql_chars,
                  self.max_plan_operators, self.soft_report_bytes, self.hard_report_bytes)
        if any(type(value) is not int or value < 1 for value in limits):
            raise ValueError("Human report limits must be positive integers.")
        if self.hard_report_bytes < 8192:
            raise ValueError("Hard report limit must be at least 8192 bytes to preserve valid HTML.")
        if self.soft_report_bytes > self.hard_report_bytes:
            raise ValueError("Soft report limit cannot exceed the hard report limit.")


DEFAULT_HUMAN_REPORT_POLICY = HumanReportPolicy()


@dataclass(frozen=True)
class ReportTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ReportCodeBlock:
    title: str
    content: str
    language: str = "text"
    collapsed: bool = False


@dataclass(frozen=True)
class ReportSection:
    section_id: str
    title: str
    severity: str = "INFO"
    summary: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    tables: tuple[ReportTable, ...] = ()
    code_blocks: tuple[ReportCodeBlock, ...] = ()
    collapsed: bool = False


@dataclass(frozen=True)
class ReportModel:
    title: str
    metadata: tuple[tuple[str, str], ...]
    incident_summary: IncidentSummary
    sections: tuple[ReportSection, ...]
