"""Bounded MySQL 8.0 monitoring collectors."""

import ipaddress
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.mysql import MySQLAdapter
from dba.slow_profiles import normalize_slow_result, profile_for_adapter, script_for_slow

MAX_RECENT_SQL_TARGETS = 5
MAX_RECENT_SQL_ROWS = 5
MAX_RECENT_SQL_CHARS = 1000
_NUMBER = re.compile(r"(?<![A-Za-z0-9_$])(?:\d+(?:\.\d+)?)(?![A-Za-z0-9_$])")
_HEX = re.compile(r"\b0x[0-9A-F]+\b", re.I)


def redact_statement(value: object) -> tuple[str | None, bool, str]:
    """Conservatively remove literals/comments before any SQL reaches output."""
    if not isinstance(value, str):
        return None, False, "SQL_TEXT_UNAVAILABLE"
    output: list[str] = []
    i = 0
    length = len(value)
    while i < length:
        char = value[i]
        if char in "'\"`":
            quote = char
            if quote == "`":
                # Identifiers are retained only after control characters are stripped.
                end = i + 1
                identifier: list[str] = []
                while end < length:
                    if value[end] == "`":
                        if end + 1 < length and value[end + 1] == "`":
                            identifier.append("`")
                            end += 2
                            continue
                        break
                    if ord(value[end]) < 32 or ord(value[end]) == 127:
                        return None, False, "REDACTION_UNCERTAIN"
                    identifier.append(value[end])
                    end += 1
                if end >= length:
                    return None, False, "REDACTION_UNCERTAIN"
                output.append("`" + "".join(identifier) + "`")
                i = end + 1
                continue
            end = i + 1
            closed = False
            while end < length:
                if value[end] == "\\":
                    end += 2
                    continue
                if value[end] == quote:
                    if end + 1 < length and value[end + 1] == quote:
                        end += 2
                        continue
                    closed = True
                    end += 1
                    break
                end += 1
            if not closed:
                return None, False, "REDACTION_UNCERTAIN"
            output.append("?")
            i = end
            continue
        if value.startswith("/*", i):
            end = value.find("*/", i + 2)
            if end < 0:
                return None, False, "REDACTION_UNCERTAIN"
            output.append(" ")
            i = end + 2
            continue
        if value.startswith("--", i) or char == "#":
            output.append(" ")
            while i < length and value[i] not in "\r\n":
                i += 1
            continue
        if ord(char) < 32 and char not in "\r\n\t" or ord(char) == 127:
            output.append(" ")
            i += 1
            continue
        output.append(char)
        i += 1
    text = " ".join("".join(output).split())
    text = _HEX.sub("?", text)
    text = _NUMBER.sub("?", text)
    from dba.logging import redact

    text = str(redact(text))
    truncated = len(text) > MAX_RECENT_SQL_CHARS
    if truncated:
        marker = " …[TRUNCATED]"
        text = text[: MAX_RECENT_SQL_CHARS - len(marker)] + marker
    return text, truncated, "AVAILABLE"


def host_ip(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = raw
    if raw.startswith("[") and "]:" in raw:
        candidate = raw[1:raw.index("]")]
    elif raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit():
        candidate = raw.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


class MySQLCollector(Collector):
    script_name = ""

    def __init__(self, adapter: MySQLAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.MYSQL:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        name = script_for_slow(self.adapter, self.script_name, self.script_name)
        if self.script_name == "active_requests" and name == self.script_name:
            name = self.adapter.statement_script
        script = self.adapter.registry.get(name, DatabaseType.MYSQL)
        result = self.adapter.execute_readonly(script)
        if self.script_name in {"active_sessions", "active_requests", "transactions"} and result.success:
            result = replace(result, rows=tuple({**row, "host_ip": host_ip(row.get("host_raw"))}
                                                for row in result.rows))
        result = replace(result, collector_name=self.script_name)
        profile = profile_for_adapter(self.adapter)
        return normalize_slow_result(profile, result) if profile else result


class ServerInfoCollector(MySQLCollector):
    script_name = "server_info"


class DatabaseStatusCollector(MySQLCollector):
    script_name = "database_status"


class ActiveSessionsCollector(MySQLCollector):
    script_name = "active_sessions"


class ActiveRequestsCollector(MySQLCollector):
    script_name = "active_requests"


class TransactionsCollector(MySQLCollector):
    script_name = "transactions"


class BlockingCollector(MySQLCollector):
    script_name = "blocking"


def collect_recent_statements(adapter: MySQLAdapter, report: Any) -> CollectorResult:
    """Collect bounded history only for analyzed roots and confirmed idle transactions."""
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    details = report.details
    sessions = [row for row in details.get("sessions", ()) if isinstance(row, dict)]
    process_counts: dict[int, int] = {}
    thread_counts: dict[int, int] = {}
    session_by_pid: dict[int, dict[str, Any]] = {}
    for row in sessions:
        pid, tid = row.get("processlist_id"), row.get("thread_id")
        if type(pid) is int and pid > 0:
            process_counts[pid] = process_counts.get(pid, 0) + 1
            session_by_pid[pid] = row
        if type(tid) is int and tid > 0:
            thread_counts[tid] = thread_counts.get(tid, 0) + 1

    candidates: list[tuple[int, str]] = []
    graph = details.get("blocking_graph", {})
    graph_roots = set(graph.get("roots", ())) if isinstance(graph, dict) else set()
    for root in details.get("roots", ()):
        if not isinstance(root, dict) or root.get("processlist_id") not in graph_roots \
                or type(root.get("blocked_count")) is not int or root["blocked_count"] <= 0:
            continue
        pid = root.get("processlist_id")
        if type(pid) is int and pid > 0:
            candidates.append((pid, "ROOT_BLOCKER"))
    for idle in details.get("idle_transactions", ()):
        if isinstance(idle, dict):
            pid = idle.get("processlist_id")
            if type(pid) is int and pid > 0:
                candidates.append((pid, "IDLE_TRANSACTION"))
    unique: dict[int, str] = {}
    for pid, source in candidates:
        unique.setdefault(pid, source)

    targets = list(unique.items())
    omitted = max(0, len(targets) - MAX_RECENT_SQL_TARGETS)
    targets = targets[:MAX_RECENT_SQL_TARGETS]
    rows: list[dict[str, Any]] = []
    failures = False
    truncated = False
    if not targets:
        rows.append({"status": "NOT_APPLICABLE", "reason": "No confirmed root blocker or idle transaction candidate."})
    for index, (pid, source) in enumerate(targets):
        session = session_by_pid.get(pid)
        tid = session.get("thread_id") if session else None
        target: dict[str, Any] = {
            "session_id": pid, "thread_id": tid, "source": source,
            "evidence_status": "UNKNOWN", "statements": [],
        }
        if process_counts.get(pid) != 1 or type(tid) is not int or tid <= 0 \
                or thread_counts.get(tid) != 1:
            target.update(evidence_status="IDENTITY_AMBIGUOUS" if session else "IDENTITY_INCOMPLETE",
                          reason="PROCESSLIST_ID and unique current THREAD_ID could not be verified.")
            rows.append(target)
            failures = True
            continue
        if adapter.statement_history_consumer is not True:
            state = "CONSUMER_DISABLED" if adapter.statement_history_consumer is False else "UNKNOWN"
            target.update(evidence_status=state,
                          reason=("Performance Schema statement history consumer is disabled."
                                  if state == "CONSUMER_DISABLED" else
                                  "Statement history consumer state was not available from preflight."))
            rows.append(target)
            failures = True
            continue
        script_name = script_for_slow(adapter, "recent_statements", "recent_statements")
        try:
            script = adapter.registry.get(script_name, DatabaseType.MYSQL)
            result = adapter.execute_readonly(script, (tid, pid, tid))
        except Exception as exc:
            error_type = getattr(exc, "error_type", "QueryFailed")
            target.update(evidence_status=_recent_error_status(str(error_type)),
                          reason="Recent statement history query failed; see sanitized collection evidence.")
            rows.append(target)
            rows.extend({"session_id": next_pid, "source": next_source,
                         "evidence_status": "NOT_COLLECTED_AFTER_FAILURE", "statements": []}
                        for next_pid, next_source in targets[index + 1:])
            failures = True
            break
        if not result.success:
            error_type = result.error_type or "QueryFailed"
            target.update(evidence_status=_recent_error_status(error_type),
                          reason="Recent statement history query failed; see sanitized collection evidence.")
            rows.append(target)
            rows.extend({"session_id": next_pid, "source": next_source,
                         "evidence_status": "NOT_COLLECTED_AFTER_FAILURE", "statements": []}
                        for next_pid, next_source in targets[index + 1:])
            failures = True
            break
        # The Registry query has its own LIMIT 5. Fetching exactly five rows
        # proves only that the sample cap was reached, not that another row
        # exists. A fake/alternate driver returning more than five rows does
        # provide direct evidence that rows were omitted.
        target["limit_reached"] = len(result.rows) >= MAX_RECENT_SQL_ROWS
        target["more_rows_known"] = len(result.rows) > MAX_RECENT_SQL_ROWS
        target["truncated"] = len(result.rows) > MAX_RECENT_SQL_ROWS
        truncated = truncated or target["truncated"]
        if not result.rows:
            target.update(evidence_status="THREAD_NOT_CURRENT",
                          reason="The sampled connection/thread identity is no longer present.")
        elif any(item.get("session_id") != pid or item.get("thread_id") != tid
                 for item in result.rows):
            target.update(evidence_status="IDENTITY_AMBIGUOUS",
                          reason="Returned history rows did not match the requested connection/thread identity.")
            failures = True
        elif result.rows[0].get("event_id") is None:
            target.update(evidence_status="NO_HISTORY_ROWS",
                          reason="No visible statement history rows were returned for the current thread.")
        else:
            statements: list[dict[str, Any]] = []
            if any(type(item.get("event_id")) is not int or item["event_id"] <= 0
                   for item in result.rows):
                target.update(evidence_status="MALFORMED_EVENT",
                              reason="Statement history contained an invalid event identity.")
                rows.append(target)
                failures = True
                continue
            event_ids = [item["event_id"] for item in result.rows]
            if len(event_ids) != len(set(event_ids)):
                target.update(evidence_status="MALFORMED_EVENT",
                              reason="Statement history contained duplicate event identities.")
                rows.append(target)
                failures = True
                continue
            ordered_rows = sorted(result.rows, key=lambda item: item["event_id"], reverse=True)
            for item in ordered_rows[:MAX_RECENT_SQL_ROWS]:
                event_id = item.get("event_id")
                safe_sql, safe_truncated, sql_status = redact_statement(item.get("sql_text"))
                statements.append({
                    "session_id": pid, "thread_id": tid, "event_id": event_id,
                    "sql_text_redacted": safe_sql, "duration_ms": item.get("duration_ms"),
                    "source": "performance_schema.events_statements_history",
                    "evidence_status": sql_status,
                    "truncated": bool(item.get("sql_text_truncated")) or safe_truncated,
                })
                if sql_status != "AVAILABLE":
                    failures = True
            if statements:
                target.update(evidence_status="AVAILABLE", statements=statements)
        rows.append(target)
    if omitted:
        rows.append({"evidence_status": "NOT_COLLECTED_BUDGET", "omitted_targets": omitted,
                     "reason": f"Target limit is {MAX_RECENT_SQL_TARGETS} connections."})
        failures = True
        truncated = True
    return CollectorResult(adapter.instance.id, "recent_statements", started_at,
                           datetime.now(timezone.utc), (time.perf_counter() - started) * 1000,
                           tuple(rows), len(rows), truncated, not failures,
                           "RecentStatementsPartial" if failures else None,
                           "Some recent statement history evidence is unavailable." if failures else None)


def _recent_error_status(error_type: str) -> str:
    if error_type in {"QueryDenied", "MissingPermission", "PermissionDenied"}:
        return "PERMISSION_DENIED"
    if "Timeout" in error_type:
        return "QUERY_TIMEOUT"
    if error_type.endswith("Failed"):
        return "QUERY_FAILED"
    return "UNKNOWN"


class LongRunningCollector(ActiveRequestsCollector):
    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        started = time.monotonic()
        result = super().collect(instance)
        threshold = self.adapter.settings.long_running_seconds * 1000
        rows = tuple(row for row in result.rows
                     if type(row.get("duration_ms")) is int and row["duration_ms"] >= threshold)
        return replace(result, collector_name="long_running", rows=rows,
                       row_count=len(rows), finished_at=datetime.now(timezone.utc),
                       elapsed_ms=(time.monotonic() - started) * 1000)


COLLECTORS: dict[str, type[MySQLCollector]] = {
    "server-info": ServerInfoCollector,
    "database-status": DatabaseStatusCollector,
    "active-sessions": ActiveSessionsCollector,
    "active-requests": ActiveRequestsCollector,
    "long-running": LongRunningCollector,
    "long-running-requests": LongRunningCollector,
}
