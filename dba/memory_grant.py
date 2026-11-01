"""Bounded SQL Server current memory grant queue collection and evidence model."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any

from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport
from dba.errors import ToolkitError
from dba.logging import redact
from dba.sqlserver import SQLServerAdapter
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus

MAX_DISPLAY_ROWS = 100
FETCH_LIMIT = MAX_DISPLAY_ROWS + 1
_HANDLE_PATTERN = re.compile(r"0x(?:[0-9a-fA-F]{2}){0,64}\Z")


@dataclass(frozen=True)
class MemoryGrantRecord:
    session_id: int
    request_id: int
    request_time: str | None
    grant_time: str | None
    requested_memory_kb: int | None
    required_memory_kb: int | None
    granted_memory_kb: int | None
    used_memory_kb: int | None
    max_used_memory_kb: int | None
    wait_time_ms: int | None
    queue_id: int | None
    wait_order: int | None
    resource_semaphore_id: int | None
    request_context_status: str
    request_start_time: str | None
    request_status: str | None
    wait_type: str | None
    database_id: int | None
    sql_handle: str | None
    plan_handle: str | None
    request_sql_handle: str | None
    request_plan_handle: str | None

    @property
    def request_identity(self) -> tuple[int, int, str | None]:
        return self.session_id, self.request_id, self.request_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "request_time": self.request_time,
            "grant_time": self.grant_time,
            "requested_memory_kb": self.requested_memory_kb,
            "required_memory_kb": self.required_memory_kb,
            "granted_memory_kb": self.granted_memory_kb,
            "used_memory_kb": self.used_memory_kb,
            "max_used_memory_kb": self.max_used_memory_kb,
            "wait_time_ms": self.wait_time_ms,
            "queue_id": self.queue_id,
            "wait_order": self.wait_order,
            "resource_semaphore_id": self.resource_semaphore_id,
            "request_identity": {
                "session_id": self.session_id,
                "request_id": self.request_id,
                "request_start_time": self.request_start_time,
                "request_context_status": self.request_context_status,
                "database_id": self.database_id,
                "wait_type": self.wait_type,
                "request_status": self.request_status,
            },
            "sql_handle": self.sql_handle,
            "plan_handle": self.plan_handle,
            "request_sql_handle": self.request_sql_handle,
            "request_plan_handle": self.request_plan_handle,
        }


@dataclass(frozen=True)
class MemoryGrantSample:
    sample_type: str
    started_at: str
    finished_at: str
    elapsed_ms: float | None
    rows: tuple[MemoryGrantRecord, ...]
    row_count: int
    truncated: bool
    success: bool
    error_type: str | None = None
    error_message: str | None = None
    elapsed_scope: str = "CLIENT_END_TO_END"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_type": self.sample_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
            "elapsed_scope": self.elapsed_scope,
            "elapsed_note": (
                "Client elapsed time includes driver execution, network fetch, and row parsing; "
                "server-side SQL execution duration is not separately measured."
                if self.elapsed_scope == "CLIENT_END_TO_END" else "Elapsed time was not measured."
            ),
            "rows": [row.to_dict() for row in self.rows],
            "row_count": self.row_count,
            "displayed_count": len(self.rows),
            "truncated": self.truncated,
            "success": self.success,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class MemoryGrantResult:
    database_id: str
    database_version: str
    profile_id: str
    collection_status: str
    completeness: str
    waiting: MemoryGrantSample
    granted: MemoryGrantSample
    identity_conflicts: int
    health_assessment: str = "NOT_ASSESSED"

    @property
    def success(self) -> bool:
        return self.waiting.success and self.granted.success

    @property
    def collection_timestamp(self) -> str:
        return self.granted.finished_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_id": self.database_id,
            "database_engine": "sqlserver",
            "database_version": self.database_version,
            "profile_id": self.profile_id,
            "collection_status": self.collection_status,
            "completeness": self.completeness,
            "health_assessment": self.health_assessment,
            "waiting_count": len(self.waiting.rows),
            "granted_count": len(self.granted.rows),
            "identity_conflicts": self.identity_conflicts,
            "snapshot_consistency": "SEQUENTIAL_BEST_EFFORT_NOT_ATOMIC",
            "waiting_sample": self.waiting.to_dict(),
            "granted_sample": self.granted.to_dict(),
            "limitations": [
                "Waiting and granted DMV queries are separate sequential samples, not an atomic snapshot.",
                "A request can complete or change state between samples; session/request IDs are time-scoped.",
                "A memory grant wait does not identify a unique root cause or prove a granted request is the culprit.",
                "No cumulative Top SQL or execution-plan query was run.",
            ],
        }


class SQLServerMemoryGrantCollector:
    def __init__(self, adapter: SQLServerAdapter, profile: ResolvedDiagnosticProfile) -> None:
        if (profile.db_type != DatabaseType.SQLSERVER or profile.feature != "memory-grant"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or not profile.sql_profile_id
                or profile.sql_profile_id != "sqlserver_memory_grant_v1"):
            raise ToolkitError("VersionVerificationRequired",
                               "Memory Grant collection requires a verified supported SQL Server profile.")
        self.adapter = adapter
        self.profile = profile

    def collect(self, instance: DatabaseInstance) -> MemoryGrantResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.SQLSERVER:
            raise ToolkitError("InstanceMismatch", "Memory Grant collector and adapter must target the same SQL Server instance.")
        waiting = self._sample("memory_grant_waiting", "WAITING")
        granted = self._sample("memory_grant_granted", "GRANTED")
        conflicts = sum(row.request_context_status in {"IDENTITY_CONFLICT", "DUPLICATE_GRANT_IDENTITY"}
                        for sample in (waiting, granted) for row in sample.rows)
        any_success = waiting.success or granted.success
        both_success = waiting.success and granted.success
        truncated = waiting.truncated or granted.truncated
        partial_identity = conflicts > 0 or any(
            row.request_context_status in {"REQUEST_NOT_FOUND", "IDENTITY_INCOMPLETE"}
            for sample in (waiting, granted) for row in sample.rows
        )
        completeness = ("UNKNOWN" if not any_success else
                        "PARTIAL" if not both_success or truncated or partial_identity else "COMPLETE")
        collection_status = ("FAILED" if not any_success else
                             "PARTIAL" if not both_success or truncated or partial_identity else "COLLECTED")
        return MemoryGrantResult(
            instance.id, self.profile.detected_raw_version or "UNKNOWN",
            self.profile.sql_profile_id or "UNKNOWN", collection_status, completeness,
            waiting, granted, conflicts,
        )

    def _sample(self, script_name: str, sample_type: str) -> MemoryGrantSample:
        script = self.adapter.registry.get(script_name, DatabaseType.SQLSERVER)
        metadata = script.metadata
        if (metadata.feature != "memory-grant" or not metadata.reviewed
                or metadata.risk != "READ_ONLY" or metadata.max_rows != FETCH_LIMIT
                or self.profile.detected_version_family not in metadata.version_families
                or self.profile.runtime_variant not in metadata.runtime_variants):
            raise ToolkitError("ProfileRegistryMismatch",
                               "Memory Grant Registry route does not match the resolved profile.")
        started = datetime.now(timezone.utc)
        timer = time.perf_counter()
        try:
            result = self.adapter.execute_readonly(script)
        except ToolkitError as exc:
            finished = datetime.now(timezone.utc)
            elapsed_ms = (time.perf_counter() - timer) * 1000
            return MemoryGrantSample(
                sample_type, started.isoformat(), finished.isoformat(),
                elapsed_ms, (), 0, False, False,
                exc.error_type, str(redact(str(exc))),
            )
        records: tuple[MemoryGrantRecord, ...] = ()
        truncated = bool(result.truncated or result.row_count >= FETCH_LIMIT)
        if result.success:
            parsed = [_parse_record(row, sample_type) for row in result.rows[:MAX_DISPLAY_ROWS]]
            counts: dict[tuple[int, int], int] = {}
            for item in parsed:
                key = (item.session_id, item.request_id)
                counts[key] = counts.get(key, 0) + 1
            records = tuple(replace(item, request_context_status="DUPLICATE_GRANT_IDENTITY")
                            if counts[(item.session_id, item.request_id)] > 1 else item for item in parsed)
        finished = datetime.now(timezone.utc)
        elapsed_ms = (time.perf_counter() - timer) * 1000
        return MemoryGrantSample(
            sample_type, started.isoformat(), finished.isoformat(), elapsed_ms,
            records, result.row_count, truncated, result.success, result.error_type,
            str(redact(result.error_message)) if result.error_message else None,
        )


def _parse_record(row: dict[str, Any], sample_type: str) -> MemoryGrantRecord:
    session_id = _required_nonnegative_int(row, "session_id")
    request_id = _required_nonnegative_int(row, "request_id")
    request_time = _timestamp(row.get("request_time"))
    grant_time = _timestamp(row.get("grant_time"))
    if sample_type == "WAITING" and grant_time is not None:
        raise ToolkitError("InvalidMemoryGrantResult", "Waiting sample contained an already granted request.")
    if sample_type == "GRANTED" and grant_time is None:
        raise ToolkitError("InvalidMemoryGrantResult", "Granted sample contained a pending request.")
    request_found = row.get("request_found") in (1, True)
    request_session = row.get("request_session_id")
    request_request = row.get("request_request_id")
    start_time = _timestamp(row.get("request_start_time"))
    grant_sql = _handle(row.get("grant_sql_handle"), "grant_sql_handle")
    grant_plan = _handle(row.get("grant_plan_handle"), "grant_plan_handle")
    request_sql = _handle(row.get("request_sql_handle"), "request_sql_handle")
    request_plan = _handle(row.get("request_plan_handle"), "request_plan_handle")
    context_status = "REQUEST_NOT_FOUND"
    if request_found:
        if (type(request_session) is not int or type(request_request) is not int
                or request_session != session_id or request_request != request_id):
            context_status = "IDENTITY_CONFLICT"
        elif (grant_sql and request_sql and grant_sql.casefold() != request_sql.casefold()
              or grant_plan and request_plan and grant_plan.casefold() != request_plan.casefold()):
            context_status = "IDENTITY_CONFLICT"
        elif request_time is None or start_time is None:
            context_status = "IDENTITY_INCOMPLETE"
        else:
            context_status = "MATCHED_AT_SAMPLE"
    if not request_found and (request_session is not None or request_request is not None):
        context_status = "IDENTITY_CONFLICT"
    return MemoryGrantRecord(
        session_id, request_id, request_time, grant_time,
        _optional_nonnegative_int(row, "requested_memory_kb"),
        _optional_nonnegative_int(row, "required_memory_kb"),
        _optional_nonnegative_int(row, "granted_memory_kb"),
        _optional_nonnegative_int(row, "used_memory_kb"),
        _optional_nonnegative_int(row, "max_used_memory_kb"),
        _optional_nonnegative_int(row, "wait_time_ms"),
        _optional_nonnegative_int(row, "queue_id"),
        _optional_nonnegative_int(row, "wait_order"),
        _optional_nonnegative_int(row, "resource_semaphore_id"),
        context_status, start_time,
        _optional_string(row, "request_status"), _optional_string(row, "wait_type"),
        _optional_nonnegative_int(row, "database_id"), grant_sql, grant_plan, request_sql, request_plan,
    )


def _required_nonnegative_int(row: dict[str, Any], name: str) -> int:
    value = row.get(name)
    if type(value) is not int or value < 0:
        raise ToolkitError("InvalidMemoryGrantResult", "SQL Server returned an invalid memory grant identity.")
    return value


def _optional_nonnegative_int(row: dict[str, Any], name: str) -> int | None:
    value = row.get(name)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ToolkitError("InvalidMemoryGrantResult", "SQL Server returned an invalid memory grant metric.")
    return value


def _optional_string(row: dict[str, Any], name: str) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolkitError("InvalidMemoryGrantResult", "SQL Server returned an invalid request detail.")
    return str(redact(value))


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return None


def _handle(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 130 or not _HANDLE_PATTERN.fullmatch(value):
        raise ToolkitError("InvalidMemoryGrantResult", f"SQL Server returned an invalid {field} value.")
    return value.lower()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MemoryGrantDiagnosticReport(DiagnosticReport):
    details: dict[str, Any]
