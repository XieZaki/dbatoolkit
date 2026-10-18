"""Evidence-only PostgreSQL slow diagnosis over bounded, non-atomic snapshots."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DiagnosticReport, Finding, ReferenceCommand, Severity
from dba.errors import ToolkitError
from dba.logging import redact
from dba.postgresql import PostgreSQLAdapter
from dba.postgresql_collectors import (
    ActiveSessionsCollector,
    BlockingCollector,
    LongRunningCollector,
    TransactionsCollector,
)
from dba.settings import PostgreSQLSettings
from dba.slow_profiles import (
    collection_completeness,
    emergency_collectors_for_adapter,
    optional_collectors_for_adapter,
    profile_for_adapter,
    required_collectors_for_adapter,
)


@dataclass(frozen=True)
class PostgreSQLSlowReport(DiagnosticReport):
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


def blocking_graph(rows: list[dict[str, Any]]) -> dict[str, Any]:
    children: dict[int, set[int]] = {}
    parents: dict[int, set[int]] = {}
    anomalies: list[dict[str, Any]] = []
    for row in rows:
        blocked, blocker = _pid(row.get("blocked_pid")), _pid(row.get("blocker_pid"))
        if blocked is None or blocker is None or blocked == blocker:
            anomalies.append({"blocked_pid": blocked, "blocker_pid": blocker,
                              "reason": "invalid or self blocking edge"})
            continue
        children.setdefault(blocker, set()).add(blocked)
        parents.setdefault(blocked, set()).add(blocker)
    all_nodes = set(children) | set(parents)
    roots = sorted(pid for pid in children if pid not in parents)
    reached: set[int] = set()
    for root in roots:
        reached.add(root)
        reached.update(_descendants(root, children))
    indegree = {pid: len(parents.get(pid, ())) for pid in all_nodes}
    pending = [pid for pid, degree in indegree.items() if degree == 0]
    while pending:
        pid = pending.pop()
        for child in children.get(pid, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    unresolved = sorted((all_nodes - reached) | {pid for pid, degree in indegree.items()
                                                  if degree > 0})
    if unresolved:
        anomalies.append({"pids": unresolved, "reason": "cycle or unresolved blocking component"})
    return {"roots": roots, "children": {str(pid): sorted(links) for pid, links in children.items()},
            "parents": {str(pid): sorted(links) for pid, links in parents.items()},
            "unresolved": unresolved, "anomalies": anomalies}


def build_slow_report(instance: DatabaseInstance, results: tuple[CollectorResult, ...],
                      settings: PostgreSQLSettings, version: str,
                      required_collectors: tuple[str, ...] =
                      ("active_sessions", "long_running", "transactions", "blocking"),
                      optional_collectors: tuple[str, ...] = ()) -> PostgreSQLSlowReport:
    by_name = {result.collector_name: result for result in results}

    def rows(name: str) -> list[dict[str, Any]]:
        result = by_name.get(name)
        return [dict(row) for row in result.rows] if result and result.success else []

    sessions = rows("active_sessions")
    transactions = rows("transactions")
    long = rows("long_running")
    blocking = rows("blocking")
    session_map = {pid: row for row in sessions if (pid := _pid(row.get("pid"))) is not None}
    transaction_map = {pid: row for row in transactions
                       if (pid := _pid(row.get("pid"))) is not None}
    graph = blocking_graph(blocking)
    children = {int(pid): set(links) for pid, links in graph["children"].items()}
    blocked_pids = {pid for links in children.values() for pid in links}
    active = [row for row in sessions if row.get("state") == "active"]
    waiting = [row for row in active if row.get("wait_event_type")]
    idle = [row for row in sessions if str(row.get("state") or "").startswith("idle in transaction")]
    concurrency = {"total_sessions": len(sessions), "active_sessions": len(active),
                   "active_queries": len(active), "waiting_sessions": len(waiting),
                   "blocked_sessions": len(blocked_pids),
                   "idle_in_transaction_sessions": len(idle)}
    quality = [f"{r.collector_name}: {'failed' if not r.success else 'truncated' if r.truncated else 'ok'}"
               for r in results]
    incomplete, visibility = collection_completeness(results, required_collectors, optional_collectors)
    findings: list[Finding] = []
    if blocked_pids:
        findings.append(Finding("BLOCKING", Severity.CRITICAL,
            {"blocked_count": len(blocked_pids), "edges": graph["children"]},
            "核对锁等待链、事务状态及业务来源；采样并非原子快照。"))
    if graph["anomalies"]:
        findings.append(Finding("BLOCKING_GRAPH_ANOMALY", Severity.WARNING,
            {"anomalies": graph["anomalies"]},
            "阻塞链存在循环或无效边；重新采样，不推断缺失 Root。"))
    complete_graph = (not incomplete and not graph["anomalies"] and
                      all(by_name.get(name) is not None and by_name[name].success
                          and not by_name[name].truncated
                          for name in ("active_sessions", "long_running", "transactions", "blocking")))
    roots: list[dict[str, Any]] = []
    for root_pid in graph["roots"]:
        session = session_map.get(root_pid)
        tx = transaction_map.get(root_pid, {})
        affected = sorted(_descendants(root_pid, children))
        state = session.get("state") if session else None
        query = session.get("query") if session else None
        root = {"pid": root_pid, "datname": session.get("datname") if session else None,
                "usename": session.get("usename") if session else None,
                "application_name": session.get("application_name") if session else None,
                "client_addr": session.get("client_addr") if session else None,
                "client_hostname": session.get("client_hostname") if session else None,
                "client_hostname_reason": (None if session and session.get("client_hostname") else
                                           "unavailable; reverse DNS may be disabled"),
                "state": state, "xact_start": session.get("xact_start") if session else None,
                "transaction_age_seconds": tx.get("transaction_age_seconds",
                                                     session.get("transaction_age_seconds") if session else None),
                "query_start": session.get("query_start") if session else None,
                "wait_event_type": session.get("wait_event_type") if session else None,
                "wait_event": session.get("wait_event") if session else None,
                "current_query": query if state == "active" else None,
                "last_query": query if state != "active" else None,
                "query_reason": None if query else "unavailable or not visible",
                "blocked_count": len(affected), "blocked_pids": affected,
                "session_available": session is not None}
        roots.append(root)
        references = (
            (ReferenceCommand(f"SELECT pg_cancel_backend({root_pid});",
                              "仅供 DBA 人工审核；cancel 只尝试取消当前查询，Toolkit 未执行。"),
             ReferenceCommand(f"SELECT pg_terminate_backend({root_pid});",
                              "仅供 DBA 人工审核；terminate 将结束会话，Toolkit 未执行。"))
            if (session and complete_graph and not graph["anomalies"]
                and session.get("backend_start") is not None and session.get("usename")) else ()
        )
        findings.append(Finding("ROOT_BLOCKER", Severity.WARNING, root,
            "确认 PID、连接来源、事务及受影响会话；人工决定处理。", references,
            session=str(root_pid)))
    idle_details: list[dict[str, Any]] = []
    for row in idle:
        pid = _pid(row.get("pid"))
        if pid is None:
            continue
        tx = transaction_map.get(pid, {})
        age = tx.get("transaction_age_seconds", row.get("transaction_age_seconds"))
        if not row.get("xact_start") or type(age) is not int:
            continue
        affected = sorted(_descendants(pid, children))
        detail = {"pid": pid, "transaction_age_seconds": age,
                  "last_query": row.get("query"), "client_addr": row.get("client_addr"),
                  "application_name": row.get("application_name"), "usename": row.get("usename"),
                  "datname": row.get("datname"), "blocked_pids": affected}
        idle_details.append(detail)
        if pid in graph["roots"] or age >= settings.idle_transaction_seconds:
            findings.append(Finding("IDLE_IN_TRANSACTION",
                Severity.CRITICAL if affected else Severity.WARNING, detail,
                "事务空闲且未结束；若无当前查询，cancel 可能无作用。联系所属应用确认提交或回滚。"))
    def duration(row: dict[str, Any]) -> int:
        value = row.get("duration_ms")
        return value if type(value) is int else 0

    long.sort(key=duration, reverse=True)
    top_queries = []
    for row in sorted(active, key=duration, reverse=True)[:settings.slow_top_n]:
        pid = _pid(row.get("pid"))
        query_text = row.get("query")
        top_queries.append({**row,
                            "blocking_pids": graph["parents"].get(str(pid), []) if pid else [],
                            "query_summary": query_text[:500] if isinstance(query_text, str) else None})
    if long:
        findings.append(Finding("LONG_RUNNING_SQL", Severity.WARNING,
            {"threshold_seconds": settings.long_running_seconds, "queries": long[:settings.slow_top_n]},
            "核对业务预期耗时及等待，不单凭耗时判断 CPU/IO 根因。"))
    if concurrency["active_queries"] >= settings.high_concurrency_threshold:
        findings.append(Finding("HIGH_CONCURRENCY", Severity.WARNING,
            {"active_queries": len(active), "threshold": settings.high_concurrency_threshold},
            "核对应用并发基线；总连接数不是活动 SQL 并发。"))
    if waiting:
        findings.append(Finding("WAITING_SQL", Severity.WARNING,
            {"count": len(waiting), "sessions": waiting[:settings.slow_top_n]},
            "结合 wait_event_type、wait_event 和阻塞链解释等待。"))
    if incomplete:
        findings.append(Finding("INCOMPLETE_SNAPSHOT", Severity.WARNING,
            {"quality": quality}, "采集失败或截断，不能将未发现的问题视为完整结论。"))
    return PostgreSQLSlowReport(instance, datetime.now(timezone.utc), "slow", tuple(findings),
        redact({"version": version, "concurrency": concurrency,
                "top_queries": top_queries, "long_running": long[:settings.slow_top_n],
                "blocking_graph": graph, "roots": roots, "idle_transactions": idle_details,
                "sessions": sessions, "active_requests": active, "transactions": transactions,
                "quality": quality, "incomplete": incomplete, "data_visibility": visibility,
                "samples": [{"collector": r.collector_name, "started_at": r.started_at,
                             "finished_at": r.finished_at, "row_count": r.row_count,
                             "truncated": r.truncated, "success": r.success,
                             "error_type": r.error_type} for r in results]}))


def diagnose_slow(adapter: PostgreSQLAdapter) -> PostgreSQLSlowReport:
    version = adapter.get_server_version()
    results: list[CollectorResult] = []
    routed = profile_for_adapter(adapter) is not None
    required = set(required_collectors_for_adapter(
        adapter, ("active_sessions", "long_running", "transactions", "blocking")
    ))
    fatal = required if routed else {"active_sessions", "blocking"}
    for collector in (ActiveSessionsCollector, LongRunningCollector,
                      TransactionsCollector, BlockingCollector):
        result = collector(adapter).collect(adapter.instance)
        results.append(result)
        if not result.success and result.collector_name in fatal:
            raise ToolkitError(result.error_type or "QueryFailed",
                               result.error_message or "Required sample unavailable.")
        if not result.success:
            break
    return build_slow_report(adapter.instance, tuple(results), adapter.settings,
                             version, tuple(required), optional_collectors_for_adapter(adapter))


_STAGED_COLLECTORS = {
    "active_sessions": ActiveSessionsCollector,
    "long_running": LongRunningCollector,
    "transactions": TransactionsCollector,
    "blocking": BlockingCollector,
}


def collect_slow_phase(adapter: PostgreSQLAdapter, names: tuple[str, ...],
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


def build_slow_phase_report(adapter: PostgreSQLAdapter, results: tuple[CollectorResult, ...],
                            *, emergency: bool) -> PostgreSQLSlowReport:
    required = (emergency_collectors_for_adapter(adapter) if emergency else
                required_collectors_for_adapter(adapter, ("active_sessions", "long_running", "transactions", "blocking")))
    optional = () if emergency else optional_collectors_for_adapter(adapter)
    report = build_slow_report(adapter.instance, results, adapter.settings, adapter.get_server_version(),
                               required, optional)
    core = emergency_collectors_for_adapter(adapter)
    by_name = {item.collector_name: item for item in results}
    complete = all(name in by_name and by_name[name].success and not by_name[name].truncated
                   for name in core)
    full_complete = all(name in by_name and by_name[name].success and not by_name[name].truncated
                        for name in required + optional)
    report.details.update({"diagnostic_phase": "EMERGENCY" if emergency else "FINAL",
                           "emergency_evidence_complete": complete,
                           "emergency_evidence_status": "COMPLETE" if complete else "INCOMPLETE",
                           "full_profile_evidence_complete": full_complete if not emergency else False,
                           "full_profile_evidence_status": "PENDING" if emergency else
                           "COMPLETE" if full_complete else "PARTIAL",
                           "supplemental_not_collected": tuple(
                               name for name in required + optional if name not in by_name) if emergency else ()})
    return report
