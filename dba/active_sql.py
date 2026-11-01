"""Typed observations and bounded transformation for current SQL Server requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

SORT_METRICS: dict[str, tuple[str, str, str]] = {
    "cpu": ("cpu_time_ms", "Current request CPU time", "milliseconds"),
    "logical-reads": ("logical_reads", "Current request logical reads", "pages"),
    "elapsed": ("elapsed_time_ms", "Current request elapsed time", "milliseconds"),
}


@dataclass(frozen=True)
class ActiveSQLRequest:
    sql_ref: str
    session_id: int
    request_id: int
    start_time: str
    cpu_time_ms: int
    logical_reads: int
    elapsed_time_ms: int
    wait_type: str | None
    wait_time_ms: int | None
    blocking_session_id: int | None

    def to_dict(self, rank: int, sort: str) -> dict[str, Any]:
        metric_field, metric_name, metric_unit = SORT_METRICS[sort]
        return {
            "rank": rank,
            "sql_ref": self.sql_ref,
            "request_identity": {
                "session_id": self.session_id,
                "request_id": self.request_id,
                "start_time": self.start_time,
            },
            "session_id": self.session_id,
            "request_id": self.request_id,
            "metric_name": metric_name,
            "metric_value": getattr(self, metric_field),
            "metric_unit": metric_unit,
            "metric_semantics": "CURRENT_REQUEST_CUMULATIVE",
            "wait_type": self.wait_type,
            "wait_time_ms": self.wait_time_ms,
            "blocking_session_id": self.blocking_session_id,
        }


@dataclass(frozen=True)
class ActiveSQLText:
    sql_ref: str
    identity_status: str
    sql_handle: str | None
    query_hash: tuple[str, ...]
    statement_start_offset: int | None
    statement_end_offset: int | None
    database_id: int | None
    plan_handles: tuple[str, ...]
    statement_text: str | None
    text_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql_ref": self.sql_ref,
            "identity_status": self.identity_status,
            "sql_handle": self.sql_handle,
            "query_hash": list(self.query_hash),
            "statement_start_offset": self.statement_start_offset,
            "statement_end_offset": self.statement_end_offset,
            "database_id": self.database_id,
            "plan_identities": [{"plan_handle": handle} for handle in self.plan_handles],
            "statement_text": self.statement_text,
            "text_status": self.text_status,
        }


@dataclass(frozen=True)
class ActiveSQLResult:
    database_id: str
    database_version: str
    profile_id: str
    collection_timestamp: datetime
    selected_sort: str
    top_n: int
    collection_status: str
    completeness: str
    candidate_count: int
    rankings: dict[str, tuple[ActiveSQLRequest, ...]]
    sql_texts: tuple[ActiveSQLText, ...]
    truncated: bool = False
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.selected_sort not in SORT_METRICS:
            raise ValueError("Unsupported Active SQL sort key.")
        if type(self.top_n) is not int or not 1 <= self.top_n <= 100:
            raise ValueError("Active SQL Top N must be between 1 and 100.")
        if self.candidate_count < 0 or self.candidate_count > 300:
            raise ValueError("Active SQL candidate count must be between 0 and 300.")
        if set(self.rankings) != set(SORT_METRICS):
            raise ValueError("Active SQL result must include all three ranking lists.")
        if any(len(items) > self.top_n for items in self.rankings.values()):
            raise ValueError("Active SQL ranking exceeds the requested Top N.")

    @property
    def primary_ranking(self) -> tuple[ActiveSQLRequest, ...]:
        return self.rankings[self.selected_sort]

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_id": self.database_id,
            "database_engine": "sqlserver",
            "database_version": self.database_version,
            "profile_id": self.profile_id,
            "collection_timestamp": self.collection_timestamp.isoformat(),
            "collection_scope": "INSTANCE",
            "evidence_source": "sys.dm_exec_requests, sys.dm_exec_sessions, sys.dm_exec_sql_text",
            "sort": self.selected_sort,
            "top_n": self.top_n,
            "collection_status": self.collection_status,
            "completeness": self.completeness,
            "candidate_count": self.candidate_count,
            "truncated": self.truncated,
            "error_type": self.error_type,
            "rankings": {
                sort: [request.to_dict(rank, sort) for rank, request in enumerate(items, 1)]
                for sort, items in self.rankings.items()
            },
            "sql_texts": [item.to_dict() for item in self.sql_texts],
        }
