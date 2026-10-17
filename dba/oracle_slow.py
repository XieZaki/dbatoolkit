"""Evidence-only Oracle slow diagnosis with instance-qualified blocking identities."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DiagnosticReport, Finding, ReferenceCommand, Severity
from dba.logging import redact
from dba.oracle import OracleAdapter
from dba.oracle_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    BlockingCollector,
    PreviousSQLCollector,
    TransactionsCollector,
)
from dba.settings import OracleSettings


@dataclass(frozen=True)
class OracleSlowReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


def _positive(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _key(instance: Any, sid: Any) -> str | None:
    first, second = _positive(instance), _positive(sid)
    return f"{first}:{second}" if first is not None and second is not None else None


def blocking_graph(rows: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    known = {_key(s.get("inst_id"), s.get("sid")) for s in sessions}
    known.discard(None)
    parents: dict[str, str] = {}
    children: dict[str, set[str]] = {}
    anomalies: list[str] = []
    for row in rows:
        blocked = _key(row.get("inst_id"), row.get("blocked_sid"))
        blocker = _key(row.get("blocker_inst_id"), row.get("blocker_sid"))
        if blocked is None or blocker is None or blocked == blocker:
            anomalies.append("invalid or self-referential blocking edge")
            continue
        if blocked in parents and parents[blocked] != blocker:
            anomalies.append(f"multiple blockers reported for {blocked}")
            continue
        parents[blocked] = blocker
        children.setdefault(blocker, set()).add(blocked)
    roots = sorted(set(children) - set(parents))
    unresolved = sorted(node for node in set(parents) | set(children) if node not in known)
    cycles: list[list[str]] = []
    visited: set[str] = set()
    for node in parents:
        path: list[str] = []
        index: dict[str, int] = {}
        current = node
        while current in parents and current not in visited:
            if current in index:
                cycles.append(path[index[current]:])
                break
            index[current] = len(path)
            path.append(current)
            current = parents[current]
        visited.update(path)
    if cycles:
        anomalies.append("blocking cycle detected")
    if unresolved:
        anomalies.append("missing session in sampled instance or remote RAC instance")
    return {"parents": parents, "children": {k: sorted(v) for k, v in children.items()},
            "roots": roots, "unresolved": unresolved, "cycles": cycles,
            "anomalies": anomalies}


def _descendants(root: str, children: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    pending = list(children.get(root, ()))
    while pending:
        node = pending.pop()
        if node == root or node in seen:
            continue
        seen.add(node)
        pending.extend(children.get(node, ()))
    return sorted(seen)


def build_slow_report(instance: DatabaseInstance, results: tuple[CollectorResult, ...],
                      settings: OracleSettings, version: str,
                      *, rac_global_snapshot: bool = False,
                      rac_enabled: bool = False,
                      lock_evidence: bool = True) -> OracleSlowReport:
    by_name = {result.collector_name: result for result in results}

    def rows(name: str) -> list[dict[str, Any]]:
        result = by_name.get(name)
        return [dict(row) for row in result.rows] if result and result.success else []

    sessions = rows("active_sessions")
    requests = rows("active_requests")
    transactions = rows("transactions")
    edges = rows("blocking")
    previous = rows("previous_sql")
    session_map = {_key(row.get("inst_id"), row.get("sid")): row for row in sessions}
    transaction_map = {_key(row.get("inst_id"), row.get("sid")): row for row in transactions}
    previous_map = {_key(row.get("inst_id"), row.get("sid")): row for row in previous}
    request_map = {_key(row.get("inst_id"), row.get("sid")): row for row in requests}
    graph = blocking_graph(edges, sessions)
    quality = [f"{r.collector_name}: {'failed' if not r.success else 'truncated' if r.truncated else 'ok'}"
               for r in results]
    required = {"active_sessions", "active_requests", "transactions", "blocking"}
    incomplete = (any(not r.success or r.truncated for r in results)
                  or not required <= set(by_name)
                  or (rac_enabled and not rac_global_snapshot))
    if rac_enabled and not rac_global_snapshot:
        quality.append("RAC global snapshot unavailable; remote blockers may be missing")
    if not lock_evidence:
        quality.append("V$LOCK/V$LOCKED_OBJECT evidence unavailable; blocking uses V$SESSION only")
    blocked = set(graph["parents"])
    waiting = [row for row in sessions if row.get("status") == "ACTIVE"
               and ((row.get("state") == "WAITING" and row.get("wait_class") != "Idle")
                    or _key(row.get("inst_id"), row.get("sid")) in blocked)]
    idle: list[dict[str, Any]] = []
    for candidate in sessions:
        key = _key(candidate.get("inst_id"), candidate.get("sid"))
        if candidate.get("status") != "INACTIVE" or key not in transaction_map:
            continue
        tx = transaction_map[key]
        if tx.get("transaction_start_time") is None:
            continue
        affected = _descendants(key, graph["children"]) if key else []
        prior = previous_map.get(key, {})
        idle.append({**candidate, **tx, "previous_sql_text": prior.get("previous_sql_text"),
                     "previous_sql_reason": None if prior.get("previous_sql_text")
                     else "unavailable: cursor aged out, capability missing or query failed",
                     "blocked_sessions": affected, "client_ip": None,
                     "client_ip_reason": "Oracle session source exposes machine/program, not a reliable client IP"})
    active = [row for row in sessions if row.get("status") == "ACTIVE"]
    concurrency = {"total_sessions": len(sessions), "active_sessions": len(active),
                   "active_sql_sessions": len(requests), "waiting_sessions": len(waiting),
                   "blocked_sessions": len(blocked), "open_transactions": len(transactions),
                   "idle_transaction_sessions": len(idle)}
    findings: list[Finding] = []
    if blocked:
        findings.append(Finding("BLOCKING", Severity.CRITICAL,
                                {"blocked_count": len(blocked), "edges": graph["children"]},
                                "核对会话身份、锁等待和事务；顺序采样可能变化。"))
    if graph["anomalies"]:
        findings.append(Finding("BLOCKING_GRAPH_ANOMALY", Severity.WARNING,
                                {"anomalies": graph["anomalies"]}, "重新采样并检查 RAC 可见性。"))
    roots: list[dict[str, Any]] = []
    for root_key in graph["roots"]:
        session: dict[str, Any] | None = session_map.get(root_key)
        tx = transaction_map.get(root_key, {})
        current = request_map.get(root_key, {})
        prior = previous_map.get(root_key, {})
        affected = _descendants(root_key, graph["children"])
        root = {"session_key": root_key, "inst_id": session.get("inst_id") if session else None,
                "sid": session.get("sid") if session else None,
                "serial_number": session.get("serial_number") if session else None,
                "username": session.get("username") if session else None,
                "status": session.get("status") if session else None,
                "machine": session.get("machine") if session else None,
                "osuser": session.get("osuser") if session else None,
                "program": session.get("program") if session else None,
                "module": session.get("module") if session else None,
                "client_identifier": session.get("client_identifier") if session else None,
                "service_name": session.get("service_name") if session else None,
                "sql_id": session.get("sql_id") if session else None,
                "prev_sql_id": session.get("prev_sql_id") if session else None,
                "current_sql": current.get("sql_text"),
                "previous_sql": prior.get("previous_sql_text"),
                "previous_sql_reason": None if prior.get("previous_sql_text")
                else "unavailable: cursor aged out, capability missing or query failed",
                "event": session.get("event") if session else None,
                "wait_class": session.get("wait_class") if session else None,
                "transaction_age_seconds": tx.get("transaction_age_seconds"),
                "used_ublk": tx.get("used_ublk"), "used_urec": tx.get("used_urec"),
                "blocked_count": len(affected), "blocked_sessions": affected,
                "client_ip": None, "client_ip_reason": "not available from V$SESSION",
                "session_available": session is not None}
        roots.append(root)
        references: tuple[ReferenceCommand, ...] = ()
        sid = _positive(root["sid"])
        serial = _positive(root["serial_number"])
        inst_id = _positive(root["inst_id"])
        safe = (session is not None and sid is not None and serial is not None
                and inst_id is not None and not incomplete and not graph["anomalies"]
                and root_key not in graph["unresolved"] and rac_global_snapshot)
        # A local single-instance snapshot is sufficient when no remote blocker appears.
        local_instances = {_positive(s.get("inst_id")) for s in sessions}
        remote_edges = any(_positive(e.get("blocker_inst_id")) not in local_instances for e in edges)
        if not rac_enabled and not rac_global_snapshot and not remote_edges and len(local_instances) == 1:
            safe = (session is not None and sid is not None and serial is not None
                    and not incomplete and not graph["anomalies"])
        if safe:
            suffix = f",@{inst_id}" if rac_global_snapshot else ""
            references = (ReferenceCommand(
                f"ALTER SYSTEM KILL SESSION '{sid},{serial}{suffix}' IMMEDIATE;",
                "仅供 DBA 人工核对 SID/SERIAL#/实例及回滚影响；Toolkit did NOT execute this command."),)
        findings.append(Finding("ROOT_BLOCKER", Severity.WARNING, root,
                                "重新核对会话身份与事务，必要时由 DBA 在原生客户端处理。",
                                references, session=root_key))
    for detail in idle:
        age = detail.get("transaction_age_seconds")
        affected = detail["blocked_sessions"]
        if affected or type(age) is int and age >= settings.idle_transaction_seconds:
            findings.append(Finding("IDLE_TRANSACTION",
                                    Severity.CRITICAL if affected else Severity.WARNING,
                                    detail, "INACTIVE 会话仍有关联事务；联系应用核对提交或回滚。"))
    def duration(row: dict[str, Any]) -> int:
        value = row.get("duration_seconds")
        return value if type(value) is int else -1

    ranked = sorted(requests, key=duration, reverse=True)
    top = [{**row, "sql_summary": row["sql_text"][:500]
            if isinstance(row.get("sql_text"), str) else None}
           for row in ranked[:settings.slow_top_n]]
    long = [row for row in ranked if type(row.get("duration_seconds")) is int
            and row["duration_seconds"] >= settings.long_running_seconds]
    if long:
        findings.append(Finding("LONG_RUNNING_SQL", Severity.WARNING,
                                {"threshold_seconds": settings.long_running_seconds,
                                 "queries": long[:settings.slow_top_n]},
                                "核对当前执行时长；V$SQL 统计是累计值。"))
    if len(requests) >= settings.high_concurrency_threshold:
        findings.append(Finding("HIGH_CONCURRENCY", Severity.WARNING,
                                {"active_sql_sessions": len(requests),
                                 "threshold": settings.high_concurrency_threshold},
                                "对照业务并发基线。"))
    if waiting:
        findings.append(Finding("WAITING_SQL", Severity.WARNING,
                                {"waiting_sessions": len(waiting)},
                                "核对当前等待事件及阻塞关系。"))
    if incomplete:
        findings.append(Finding("INCOMPLETE_SNAPSHOT", Severity.WARNING,
                                {"quality": quality}, "采集失败或截断，结论不完整。"))
    details = {"version": version, "concurrency": concurrency, "top_queries": top,
               "long_running": long[:settings.slow_top_n], "blocking_graph": graph,
               "blocking_evidence": edges,
               "lock_evidence_reason": None if lock_evidence
               else "unavailable: optional V$LOCK/V$LOCKED_OBJECT grants missing",
               "roots": roots, "transactions": transactions, "idle_transactions": idle,
               "sessions": sessions, "active_requests": requests,
               "quality": quality, "incomplete": incomplete,
               "rac_global_snapshot": rac_global_snapshot,
               "rac_enabled": rac_enabled,
               "samples": [{"collector": r.collector_name, "started_at": r.started_at,
                            "finished_at": r.finished_at, "row_count": r.row_count,
                            "truncated": r.truncated, "success": r.success,
                            "error_type": r.error_type} for r in results]}
    return OracleSlowReport(instance, datetime.now(timezone.utc), "slow",
                            tuple(findings), redact(details))


def diagnose_slow(adapter: OracleAdapter) -> OracleSlowReport:
    collectors = (ActiveSessionsCollector, ActiveRequestsCollector, TransactionsCollector,
                  BlockingCollector, PreviousSQLCollector)
    results: list[CollectorResult] = []
    for collector in collectors:
        if results and not results[-1].success:
            break
        results.append(collector(adapter).collect(adapter.instance))
    return build_slow_report(adapter.instance, tuple(results), adapter.settings,
                             adapter.get_server_version(),
                             rac_global_snapshot=adapter.capabilities["rac_global_snapshot"],
                             rac_enabled=adapter.rac_enabled,
                             lock_evidence=adapter.capabilities["lock_evidence"])
