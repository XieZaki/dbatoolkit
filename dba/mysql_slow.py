"""Evidence-only MySQL slow diagnosis with transaction-backed idle findings."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DiagnosticReport, Finding, ReferenceCommand, Severity
from dba.errors import ToolkitError
from dba.logging import redact
from dba.mysql import MySQLAdapter
from dba.mysql_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    BlockingCollector,
    TransactionsCollector,
)
from dba.postgresql_slow import blocking_graph
from dba.settings import MySQLSettings
from dba.slow_profiles import (
    collection_completeness,
    emergency_collectors_for_adapter,
    optional_collectors_for_adapter,
    profile_for_adapter,
    required_collectors_for_adapter,
)


@dataclass(frozen=True)
class MySQLSlowReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


def _pid(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _descendants(root: int, children: dict[int, set[int]]) -> set[int]:
    seen: set[int] = set()
    pending = list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        if pid == root or pid in seen:
            continue
        seen.add(pid)
        pending.extend(children.get(pid, ()))
    return seen


def build_slow_report(instance: DatabaseInstance, results: tuple[CollectorResult, ...],
                      settings: MySQLSettings, version: str,
                      statement_script: str = "active_requests",
                      required_collectors: tuple[str, ...] =
                      ("active_sessions", "active_requests", "transactions", "blocking"),
                      optional_collectors: tuple[str, ...] = ()) -> MySQLSlowReport:
    by_name = {result.collector_name: result for result in results}

    def rows(name: str) -> list[dict[str, Any]]:
        result = by_name.get(name)
        return [dict(row) for row in result.rows] if result and result.success else []

    sessions = rows("active_sessions")
    requests = rows("active_requests")
    transactions = rows("transactions")
    blocking = rows("blocking")
    session_map = {pid: row for row in sessions
                   if (pid := _pid(row.get("processlist_id"))) is not None}
    transaction_map = {pid: row for row in transactions
                       if (pid := _pid(row.get("trx_mysql_thread_id"))) is not None}
    graph = blocking_graph(blocking)
    children = {int(pid): set(links) for pid, links in graph["children"].items()}
    blocked_pids = {pid for links in children.values() for pid in links}
    waiting = [row for row in requests if row.get("current_wait") or
               row.get("processlist_id") in blocked_pids]
    idle = [row for row in sessions if row.get("command_name") == "Sleep"
            and _pid(row.get("processlist_id")) in transaction_map]
    limited = statement_script != "active_requests"
    quality = [f"{r.collector_name}: {'failed' if not r.success else 'truncated' if r.truncated else 'ok'}"
               for r in results]
    if limited:
        quality.append("active_requests: current wait detail unavailable" if
                       statement_script == "active_requests_no_waits" else
                       "active_requests: thread fallback; statement timer, rows and wait detail unavailable")
    incomplete, visibility = collection_completeness(results, required_collectors, optional_collectors)
    if limited and visibility == "FULL":
        # The compatibility statement remains usable, but it omits wait detail;
        # expose reduced visibility without treating successful core queries as
        # a failed diagnosis.
        visibility = "PARTIAL"
    concurrency = {"total_connections": len(sessions), "active_statements": len(requests),
                   "running_statements": len(requests) - len(waiting),
                   "waiting_statements": len(waiting), "blocked_sessions": len(blocked_pids),
                   "active_transactions": len(transactions), "idle_transactions": len(idle)}
    findings: list[Finding] = []
    if blocked_pids:
        findings.append(Finding("BLOCKING", Severity.CRITICAL,
            {"blocked_count": len(blocked_pids), "edges": graph["children"]},
            "核对锁等待链和事务状态；顺序采样可能发生变化。"))
    if graph["anomalies"]:
        findings.append(Finding("BLOCKING_GRAPH_ANOMALY", Severity.WARNING,
            {"anomalies": graph["anomalies"]}, "阻塞图有无效边或循环；重新采样。"))
    complete_graph = (not incomplete and not graph["anomalies"] and
                      all(by_name.get(name) is not None and by_name[name].success
                          and not by_name[name].truncated
                          for name in ("active_sessions", "active_requests", "transactions", "blocking")))
    roots: list[dict[str, Any]] = []
    for root_pid in graph["roots"]:
        session = session_map.get(root_pid)
        tx = transaction_map.get(root_pid, {})
        affected = sorted(_descendants(root_pid, children))
        current_sql = session.get("sql_text") if session and session.get("command_name") != "Sleep" else None
        root = {"processlist_id": root_pid, "thread_id": session.get("thread_id") if session else None,
                "user_name": session.get("user_name") if session else None,
                "host_raw": session.get("host_raw") if session else None,
                "host_ip": session.get("host_ip") if session else None,
                "database_name": session.get("database_name") if session else None,
                "command_name": session.get("command_name") if session else None,
                "state_name": session.get("state_name") if session else None,
                "current_sql": current_sql, "last_sql": None,
                "last_sql_reason": "MySQL current snapshots do not prove the last completed SQL",
                "trx_id": tx.get("trx_id"), "transaction_age_seconds": tx.get("transaction_age_seconds"),
                "trx_rows_locked": tx.get("trx_rows_locked"),
                "trx_rows_modified": tx.get("trx_rows_modified"),
                "blocked_count": len(affected), "blocked_pids": affected,
                "session_available": session is not None}
        roots.append(root)
        references = (
            (ReferenceCommand(f"KILL QUERY {root_pid};",
                              "仅供 DBA 人工审核；停止当前语句，保留连接，Toolkit 未执行。"),
             ReferenceCommand(f"KILL {root_pid};",
                              "仅供 DBA 人工审核；终止整个连接，Toolkit 未执行。"))
            if (session and complete_graph and session.get("user_name") and session.get("host_raw")
                and session.get("thread_id") is not None) else ()
        )
        findings.append(Finding("ROOT_BLOCKER", Severity.WARNING, root,
            "重新核对连接身份及回滚影响；Sleep 会话通常没有当前查询可供 KILL QUERY 取消。",
            references, session=str(root_pid)))
    idle_details: list[dict[str, Any]] = []
    for session in idle:
        pid = _pid(session.get("processlist_id"))
        if pid is None:
            continue
        tx = transaction_map[pid]
        age = tx.get("transaction_age_seconds")
        if type(age) is not int or not tx.get("trx_started"):
            continue
        affected = sorted(_descendants(pid, children))
        detail = {"processlist_id": pid, "transaction_age_seconds": age,
                  "user_name": session.get("user_name"), "host_raw": session.get("host_raw"),
                  "host_ip": session.get("host_ip"), "database_name": session.get("database_name"),
                  "current_sql": None, "last_sql": None,
                  "last_sql_reason": "unavailable from current monitoring snapshots",
                  "blocked_pids": affected, "trx_rows_locked": tx.get("trx_rows_locked"),
                  "trx_rows_modified": tx.get("trx_rows_modified")}
        idle_details.append(detail)
        if pid in graph["roots"] or age >= settings.idle_transaction_seconds:
            findings.append(Finding("IDLE_TRANSACTION",
                Severity.CRITICAL if affected else Severity.WARNING, detail,
                "Sleep 且 InnoDB 事务未结束；KILL QUERY 通常无当前语句可取消，请联系应用确认提交或回滚。"))

    def duration(row: dict[str, Any]) -> int:
        value = row.get("duration_ms")
        return value if type(value) is int else 0

    ranked = sorted(requests, key=duration, reverse=True)
    top_queries = []
    for row in ranked[:settings.slow_top_n]:
        pid = _pid(row.get("processlist_id"))
        sql_text = row.get("sql_text")
        top_queries.append({**row,
                            "blocking_pids": graph["parents"].get(str(pid), []) if pid else [],
                            "sql_summary": sql_text[:500] if isinstance(sql_text, str) else None})
    long = [row for row in ranked if duration(row) >= settings.long_running_seconds * 1000]
    if long:
        findings.append(Finding("LONG_RUNNING_SQL", Severity.WARNING,
            {"threshold_seconds": settings.long_running_seconds, "queries": long[:settings.slow_top_n]},
            "核对业务预期耗时及等待，不单凭耗时推断 CPU/IO 根因。"))
    if len(requests) >= settings.high_concurrency_threshold:
        findings.append(Finding("HIGH_CONCURRENCY", Severity.WARNING,
            {"active_statements": len(requests), "threshold": settings.high_concurrency_threshold},
            "核对应用并发基线；连接数不等于正在执行的 SQL 数。"))
    if waiting:
        findings.append(Finding("WAITING_SQL", Severity.WARNING,
            {"count": len(waiting), "queries": waiting[:settings.slow_top_n]},
            "结合当前等待和锁链核对；等待事件并非完整根因。"))
    if incomplete:
        findings.append(Finding("INCOMPLETE_SNAPSHOT", Severity.WARNING,
            {"quality": quality}, "采集失败、截断或降级，不能将未发现的问题视为完整结论。"))
    return MySQLSlowReport(instance, datetime.now(timezone.utc), "slow", tuple(findings),
        redact({"version": version, "concurrency": concurrency,
                "top_queries": top_queries, "long_running": long[:settings.slow_top_n],
                "blocking_graph": graph, "roots": roots, "transactions": transactions,
                "idle_transactions": idle_details, "sessions": sessions,
                "active_requests": requests,
                "quality": quality, "incomplete": incomplete, "data_visibility": visibility,
                "samples": [{"collector": r.collector_name, "started_at": r.started_at,
                             "finished_at": r.finished_at, "row_count": r.row_count,
                             "truncated": r.truncated, "success": r.success,
                             "error_type": r.error_type} for r in results]}))


def diagnose_slow(adapter: MySQLAdapter) -> MySQLSlowReport:
    version = adapter.get_server_version()
    results: list[CollectorResult] = []
    routed = profile_for_adapter(adapter) is not None
    required = set(required_collectors_for_adapter(
        adapter, ("active_sessions", "active_requests", "transactions", "blocking")
    ))
    fatal = required if routed else {"active_sessions", "blocking"}
    for collector in (ActiveSessionsCollector, ActiveRequestsCollector,
                      TransactionsCollector, BlockingCollector):
        result = collector(adapter).collect(adapter.instance)
        results.append(result)
        if not result.success and result.collector_name in fatal:
            raise ToolkitError(result.error_type or "QueryFailed",
                               result.error_message or "Required sample unavailable.")
        if not result.success:
            break
    return build_slow_report(adapter.instance, tuple(results), adapter.settings,
                             version, adapter.statement_script, tuple(required),
                             optional_collectors_for_adapter(adapter))


_STAGED_COLLECTORS = {
    "active_sessions": ActiveSessionsCollector,
    "active_requests": ActiveRequestsCollector,
    "transactions": TransactionsCollector,
    "blocking": BlockingCollector,
}


def collect_slow_phase(adapter: MySQLAdapter, names: tuple[str, ...],
                       existing: tuple[CollectorResult, ...] = ()) -> tuple[CollectorResult, ...]:
    results = list(existing)
    have = {item.collector_name for item in results}
    supplemental = bool(existing)
    reconnect_pending = False
    for name in names:
        if name in have or name not in _STAGED_COLLECTORS:
            continue
        if reconnect_pending:
            try:
                adapter.connect()
            except Exception:
                break
            reconnect_pending = False
        result = _STAGED_COLLECTORS[name](adapter).collect(adapter.instance)
        results.append(result)
        have.add(name)
        if not result.success:
            if supplemental and name != names[-1]:
                reconnect_pending = True
            else:
                break
    return tuple(results)


def build_slow_phase_report(adapter: MySQLAdapter, results: tuple[CollectorResult, ...],
                            *, emergency: bool) -> MySQLSlowReport:
    required = (emergency_collectors_for_adapter(adapter) if emergency else
                required_collectors_for_adapter(
                    adapter, ("active_sessions", "active_requests", "transactions", "blocking")))
    optional = () if emergency else optional_collectors_for_adapter(adapter)
    report = build_slow_report(adapter.instance, results, adapter.settings, adapter.get_server_version(),
                               adapter.statement_script, required, optional)
    core = emergency_collectors_for_adapter(adapter)
    by_name = {item.collector_name: item for item in results}
    complete = all(name in by_name and by_name[name].success and not by_name[name].truncated
                   for name in core)
    full_complete = (all(name in by_name and by_name[name].success and not by_name[name].truncated
                         for name in required + optional)
                     and adapter.statement_script == "active_requests")
    report.details.update({"diagnostic_phase": "EMERGENCY" if emergency else "FINAL",
                           "emergency_evidence_complete": complete,
                           "emergency_evidence_status": "COMPLETE" if complete else "INCOMPLETE",
                           "full_profile_evidence_complete": full_complete if not emergency else False,
                           "full_profile_evidence_status": "PENDING" if emergency else
                           "COMPLETE" if full_complete else "PARTIAL",
                           "supplemental_not_collected": tuple(
                               name for name in required + optional if name not in by_name) if emergency else ()})
    return report
