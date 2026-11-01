"""Read-only SQL Server current-request Top N collector."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dba.active_sql import SORT_METRICS, ActiveSQLRequest, ActiveSQLResult, ActiveSQLText
from dba.active_sql_profiles import PARSER_CONTRACT_ID, SQL_PROFILE_ID
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.sqlserver import SQLServerAdapter
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus

TOP_LIMIT = 100
SQL_TEXT_LIMIT = 2000
MAX_CANDIDATES = 300


class SQLServerActiveSQLCollector:
    def __init__(self, adapter: SQLServerAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.SQLSERVER or profile.feature != "active-sql"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or profile.sql_profile_id != SQL_PROFILE_ID
                or profile.parser_contract_id != PARSER_CONTRACT_ID):
            raise ToolkitError("VersionVerificationRequired",
                               "Active SQL collection requires a verified supported SQL Server profile.")
        self.profile = profile

    def collect(self, instance: DatabaseInstance, sort: str = "cpu", top_n: int = 10) -> ActiveSQLResult:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        if sort not in SORT_METRICS:
            raise ToolkitError("InvalidSort", "Active SQL sort must be cpu, logical-reads, or elapsed.")
        if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
            raise ToolkitError("InvalidTopN", f"Active SQL Top N must be between 1 and {TOP_LIMIT}.")
        script = self.adapter.registry.get("active_sql", DatabaseType.SQLSERVER)
        metadata = script.metadata
        if (metadata.feature != "active-sql"
                or self.profile.detected_version_family not in metadata.version_families
                or self.profile.runtime_variant not in metadata.runtime_variants):
            raise ToolkitError("ProfileRegistryMismatch",
                               "Active SQL Registry route does not match the resolved profile.")
        result = self.adapter.execute_readonly(script, active_sql_top_n=top_n)
        timestamp = result.finished_at or datetime.now(timezone.utc)
        if not result.success:
            error_type = result.error_type or "CollectionFailed"
            permission_failure = "permission" in error_type.casefold() or "denied" in error_type.casefold()
            return ActiveSQLResult(
                instance.id, self.profile.detected_raw_version or "UNKNOWN", SQL_PROFILE_ID,
                timestamp, sort, top_n,
                "PERMISSION_DENIED" if permission_failure else "FAILED", "UNKNOWN", 0,
                {key: () for key in SORT_METRICS}, (), error_type=error_type)

        rows: dict[tuple[int, int, str], dict[str, Any]] = {}
        for row in result.rows:
            for field in ("cpu_time_ms", "logical_reads", "elapsed_time_ms"):
                if type(row.get(field)) is not int or row[field] < 0:
                    raise ToolkitError("InvalidActiveSQLResult",
                                       "SQL Server returned an invalid active-request metric.")
            for field in ("wait_time_ms", "blocking_session_id", "statement_start_offset",
                          "statement_end_offset", "database_id"):
                value = row.get(field)
                invalid = (value is not None and type(value) is not int)
                if value is not None and type(value) is int:
                    invalid = (field == "statement_end_offset" and value < 0 and value != -1
                               or field not in {"blocking_session_id", "statement_end_offset"}
                               and value < 0)
                if invalid:
                    raise ToolkitError("InvalidActiveSQLResult",
                                       "SQL Server returned an invalid active-request detail value.")
            if row.get("wait_type") is not None and not isinstance(row["wait_type"], str):
                raise ToolkitError("InvalidActiveSQLResult", "SQL Server returned an invalid wait type.")
            for field in ("sql_text_available", "sql_text_truncated"):
                if row.get(field) not in (0, 1, False, True):
                    raise ToolkitError("InvalidActiveSQLResult",
                                       "SQL Server returned an invalid SQL text status flag.")
            request_key = _request_key(row)
            previous = rows.get(request_key)
            if previous is not None and previous != row:
                raise ToolkitError("ConflictingActiveSQLRequest",
                                   "SQL Server returned conflicting rows for one active request identity.")
            rows[request_key] = row
        if len(rows) > MAX_CANDIDATES:
            raise ToolkitError("InvalidActiveSQLResult", "SQL Server returned more than 300 active SQL candidates.")

        statement_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
        request_statement_keys: dict[tuple[int, int, str], tuple[Any, ...]] = {}
        for request_key, row in rows.items():
            statement_key = _statement_key(row, request_key)
            request_statement_keys[request_key] = statement_key
            current = statement_rows.get(statement_key)
            text_available = row.get("sql_text_available") in (1, True)
            text = row.get("statement_text")
            if text_available and isinstance(text, str) and len(text) > SQL_TEXT_LIMIT:
                raise ToolkitError("InvalidActiveSQLText",
                                   "SQL Server returned statement text over the reviewed 2000-character limit.")
            if current is None:
                statement_rows[statement_key] = {
                    "sql_handle": row.get("sql_handle"),
                    "query_hashes": set(),
                    "statement_start_offset": row.get("statement_start_offset"),
                    "statement_end_offset": row.get("statement_end_offset"),
                    "database_id": row.get("database_id"),
                    "plan_handles": set(),
                    "statement_text": text if text_available and isinstance(text, str) else None,
                    "text_available": text_available and isinstance(text, str),
                    "text_truncated": row.get("sql_text_truncated") in (1, True),
                    "identity_status": "NATIVE_STATEMENT" if statement_key[0] == "NATIVE" else "REQUEST_SCOPED_ONLY",
                }
                current = statement_rows[statement_key]
            elif text_available and isinstance(text, str):
                existing = current["statement_text"]
                if existing is not None and existing != text:
                    raise ToolkitError("ConflictingActiveSQLText",
                                       "One native statement identity resolved to different SQL text.")
                current["statement_text"] = text
                current["text_available"] = True
                current["text_truncated"] = current["text_truncated"] or row.get("sql_text_truncated") in (1, True)
            query_hash = row.get("query_hash")
            if isinstance(query_hash, str) and query_hash:
                current["query_hashes"].add(query_hash)
            plan_handle = row.get("plan_handle")
            if isinstance(plan_handle, str) and plan_handle:
                current["plan_handles"].add(plan_handle)

        ordered_statement_keys = sorted(statement_rows, key=repr)
        references = {key: f"SQL-{index:03d}" for index, key in enumerate(ordered_statement_keys, 1)}
        statements: list[ActiveSQLText] = []
        for key in ordered_statement_keys:
            item = statement_rows[key]
            text_status = ("UNAVAILABLE" if not item["text_available"] else
                           "TRUNCATED" if item["text_truncated"] else "AVAILABLE")
            statements.append(ActiveSQLText(
                references[key], item["identity_status"], item["sql_handle"],
                tuple(sorted(item["query_hashes"])), item["statement_start_offset"],
                item["statement_end_offset"], item["database_id"],
                tuple(sorted(item["plan_handles"])), item["statement_text"], text_status))

        requests = [ActiveSQLRequest(
            references[request_statement_keys[key]], row["session_id"], row["request_id"],
            str(row["start_time"]), row["cpu_time_ms"], row["logical_reads"],
            row["elapsed_time_ms"], row.get("wait_type"), row.get("wait_time_ms"),
            row.get("blocking_session_id")) for key, row in rows.items()]
        rankings = {sort_name: tuple(sorted(
            requests, key=lambda item: (-getattr(item, metric_field), item.session_id,
                                        item.request_id, item.start_time))[:top_n])
            for sort_name, (metric_field, _, _) in SORT_METRICS.items()}
        partial = (result.truncated or any(item.text_status != "AVAILABLE" for item in statements)
                   or any(item.identity_status != "NATIVE_STATEMENT" for item in statements))
        completeness = "PARTIAL" if partial else "COMPLETE"
        return ActiveSQLResult(
            instance.id, self.profile.detected_raw_version or "UNKNOWN", SQL_PROFILE_ID,
            timestamp, sort, top_n, "COLLECTED", completeness,
            len(rows), rankings, tuple(statements), truncated=result.truncated)


def _request_key(row: dict[str, Any]) -> tuple[int, int, str]:
    session_id, request_id, start_time = row.get("session_id"), row.get("request_id"), row.get("start_time")
    if (type(session_id) is not int or type(request_id) is not int
            or not isinstance(start_time, (str, datetime))):
        raise ToolkitError("InvalidActiveSQLResult", "SQL Server returned an incomplete active request identity.")
    return session_id, request_id, start_time.isoformat() if isinstance(start_time, datetime) else start_time


def _statement_key(row: dict[str, Any], request_key: tuple[int, int, str]) -> tuple[Any, ...]:
    handle = row.get("sql_handle")
    start, end, database_id = (row.get("statement_start_offset"), row.get("statement_end_offset"),
                               row.get("database_id"))
    if isinstance(handle, str) and handle and type(start) is int and type(end) is int:
        return ("NATIVE", database_id if type(database_id) is int else None, handle, start, end)
    return ("REQUEST", *request_key)
