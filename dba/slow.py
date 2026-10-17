"""Bounded, non-atomic DMV snapshot analysis. No command execution path."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import (
    DatabaseInstance,
    DatabaseType,
    DiagnosticReport,
    Finding,
    ReferenceCommand,
    Severity,
)
from dba.errors import ToolkitError
from dba.logging import redact
from dba.settings import SQLServerSettings
from dba.slow_profiles import (
    collection_completeness,
    optional_collectors_for_adapter,
    profile_for_adapter,
    required_collectors_for_adapter,
)
from dba.sqlserver import SQLServerAdapter
from dba.sqlserver_collectors import (
    ActiveRequestsCollector,
    ActiveSessionsCollector,
    ConnectionsCollector,
    TransactionsCollector,
)


@dataclass(frozen=True)
class SlowReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


def number(value: Any) -> int:
    return value if type(value) is int else 0


def blocking_graph(requests: list[dict[str, Any]]) -> dict[str, Any]:
    children: dict[int, set[int]] = {}
    parents: dict[int, set[int]] = {}
    anomalies: list[dict[str, Any]] = []
    special = {
        -2: "orphaned distributed transaction",
        -3: "deferred recovery",
        -4: "latch owner unknown",
        -5: "asynchronous/latch owner not tracked",
    }
    for row in requests:
        sid, blocker = number(row.get("session_id")), number(row.get("blocking_session_id"))
        if blocker < 0:
            anomalies.append(
                {
                    "session_id": sid,
                    "blocking_session_id": blocker,
                    "reason": special.get(blocker, "unknown negative blocker"),
                }
            )
        elif blocker > 32767 or (blocker > 0 and (sid <= 0 or sid > 32767 or sid == blocker)):
            anomalies.append(
                {
                    "session_id": sid,
                    "blocking_session_id": blocker,
                    "reason": "invalid or self-referencing edge",
                }
            )
        elif blocker > 0:
            children.setdefault(blocker, set()).add(sid)
            parents.setdefault(sid, set()).add(blocker)
    roots = sorted(set(children) - set(parents))
    # Kahn's algorithm: every edge processed once, no recursive stack growth.
    nodes = set(children) | set(parents)
    indegree = {node: len(parents.get(node, set())) for node in nodes}
    queue = [node for node in nodes if indegree[node] == 0]
    processed: set[int] = set()
    while queue:
        node = queue.pop()
        processed.add(node)
        for child in children.get(node, set()):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    unresolved = sorted(nodes - processed)
    if unresolved:
        anomalies.append(
            {
                "reason": "cycle or descendant of cycle; root attribution unavailable",
                "sessions": unresolved,
            }
        )
    for sid, owners in parents.items():
        if len(owners) > 1:
            anomalies.append(
                {
                    "session_id": sid,
                    "reason": "multiple request blockers",
                    "blockers": sorted(owners),
                }
            )
    return {
        "roots": roots,
        "children": {k: sorted(v) for k, v in children.items()},
        "anomalies": anomalies,
        "unresolved": unresolved,
    }


def descendants(root: int, children: dict[int, list[int]]) -> set[int]:
    seen = {root}
    pending = [root]
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in seen:
                seen.add(child)
                pending.append(child)
    return seen - {root}


def build_slow_report(
    instance: DatabaseInstance,
    results: tuple[CollectorResult, ...],
    settings: SQLServerSettings,
    version: str = "unavailable",
    required_collectors: tuple[str, ...] = ("active_requests", "active_sessions", "transactions", "connections"),
    optional_collectors: tuple[str, ...] = (),
) -> SlowReport:
    if instance.db_type != DatabaseType.SQLSERVER:
        raise ToolkitError("NotImplemented", "Not implemented for this database type.")
    by_name = {r.collector_name: r for r in results}
    quality: list[str] = [
        "Sequential DMV samples are not an atomic snapshot; counts are observed values."
    ]

    def rows(name: str) -> list[dict[str, Any]]:
        result = by_name.get(name)
        if result is None or not result.success:
            quality.append(
                f"{name}: unavailable ({result.error_type if result else 'not collected'})"
            )
            return []
        if result.truncated:
            quality.append(
                f"{name}: truncated; counts are lower bounds and roots may be incomplete"
            )
        return [dict(row) for row in result.rows]

    requests, sessions = rows("active_requests"), rows("active_sessions")
    transactions, connections = rows("transactions"), rows("connections")
    session_map = {number(r.get("session_id")): r for r in sessions}
    connection_map: dict[int, list[dict[str, Any]]] = {}
    for connection in connections:
        connection_map.setdefault(number(connection.get("session_id")), []).append(connection)
    for request in requests:
        session = session_map.get(number(request.get("session_id")), {})
        for key in (
            "login_name",
            "host_name",
            "program_name",
            "client_net_address",
            "open_transaction_count",
        ):
            request.setdefault(key, session.get(key))
        request.setdefault("cpu_time", request.get("cpu_ms"))
        request.setdefault("wait_time_ms", request.get("wait_ms"))
    requests.sort(key=lambda row: number(row.get("elapsed_ms")), reverse=True)
    waiting = [r for r in requests if r.get("wait_type") or r.get("status") == "suspended"]
    blocked = [r for r in requests if number(r.get("blocking_session_id")) > 0]
    concurrency = {
        "connected_sessions": len(session_map),
        "active_sessions": len({r.get("session_id") for r in requests}),
        "active_requests": len(requests),
        "running_requests": sum(r.get("status") == "running" for r in requests),
        "waiting_requests": len(waiting),
        "blocked_requests": len(blocked),
    }
    graph = blocking_graph(requests)
    findings: list[Finding] = []
    if blocked:
        findings.append(
            Finding(
                "BLOCKING",
                Severity.CRITICAL,
                {"blocked_requests": len(blocked), "edges": graph["children"]},
                "核对阻塞链和业务事务；联系应用负责人确认提交或回滚方案。",
            )
        )
    if graph["anomalies"]:
        findings.append(
            Finding(
                "BLOCKING_GRAPH_ANOMALY",
                Severity.WARNING,
                {"anomalies": graph["anomalies"]},
                "存在特殊等待所有者、循环或不一致采样；重新只读采样，不推断缺失根节点。",
            )
        )
    roots: list[dict[str, Any]] = []
    for sid in graph["roots"]:
        session = session_map.get(sid, {})
        active = [r for r in requests if r.get("session_id") == sid]
        associated = [r for r in transactions if r.get("session_id") == sid]
        unfinished = [
            r
            for r in associated
            if r.get("transaction_state") in (2, 4, 5) and r.get("transaction_type") in (1, 2, 4)
        ]
        sources = connection_map.get(sid, [])
        last = [r.get("last_sql") for r in sources if r.get("last_sql")]
        affected = descendants(sid, graph["children"])
        root: dict[str, Any] = {
            "session_id": sid,
            "status": session.get("status"),
            **{
                key: session.get(key)
                for key in (
                    "login_name",
                    "host_name",
                    "program_name",
                    "client_net_address",
                    "database_name",
                    "open_transaction_count",
                    "login_time",
                    "last_request_start_time",
                    "last_request_end_time",
                )
            },
            "current_sql": [r.get("sql_text") for r in active if r.get("sql_text")],
            "last_sql": last,
            "connections": sources,
            "transactions": associated,
            "transaction_begin_time": min(
                (
                    str(r["transaction_begin_time"])
                    for r in unfinished
                    if r.get("transaction_begin_time")
                ),
                default=None,
            ),
            "transaction_age_seconds": max(
                (
                    r["transaction_age_seconds"]
                    for r in unfinished
                    if type(r.get("transaction_age_seconds")) is int
                ),
                default=None,
            ),
            "blocked_session_count": len(affected),
            "blocked_sessions": sorted(affected),
            "session_available": bool(session),
            "unfinished_transaction_confirmed": bool(unfinished),
            "sql_note": "Current/last batch is contextual evidence, not proof of the statement that acquired a lock.",
        }
        if not root["client_net_address"]:
            root["client_net_address"] = next(
                (r.get("client_net_address") for r in sources if r.get("client_net_address")), None
            )
        roots.append(root)
        requests_complete = (
            by_name.get("active_requests") is not None and by_name["active_requests"].success
            and not by_name["active_requests"].truncated
        )
        reference_evidence_complete = all(
            by_name.get(name) is not None and by_name[name].success and not by_name[name].truncated
            for name in ("active_requests", "active_sessions", "transactions", "connections")
        ) and not graph["anomalies"]
        references = (
            (
                ReferenceCommand(
                    f"KILL {sid};", "仅供 DBA 人工审核；重新确认 session 身份及回滚影响。"
                ),
            )
            if session and sid > 0 and affected and reference_evidence_complete
            else ()
        )
        findings.append(
            Finding(
                "ROOT_BLOCKER",
                Severity.WARNING,
                root,
                "检查来源和事务证据；采样变化或截断可能导致根节点信息不完整。",
                references,
                session=str(sid),
            )
        )
        if (
            requests_complete
            and not active
            and session.get("status") == "sleeping"
            and number(session.get("open_transaction_count")) > 0
            and unfinished
        ):
            findings.append(
                Finding(
                    "IDLE_TRANSACTION_BLOCKER",
                    Severity.CRITICAL,
                    root,
                    f"Root blocker 处于 sleeping，事务 DMV 确认存在未结束事务；当前阻塞 {len(affected)} 个会话。联系事务所属应用处理。",
                    session=str(sid),
                )
            )
    long = [
        r for r in requests if number(r.get("elapsed_ms")) >= settings.long_running_seconds * 1000
    ]
    if long:
        findings.append(
            Finding(
                "LONG_RUNNING_SQL",
                Severity.WARNING,
                {
                    "threshold_seconds": settings.long_running_seconds,
                    "count": len(long),
                    "requests": long[: settings.slow_top_n],
                },
                "核对业务预期耗时、当前等待及阻塞证据；耗时本身不证明 CPU/IO 根因。",
            )
        )
    if len(requests) >= settings.high_concurrency_threshold:
        findings.append(
            Finding(
                "HIGH_CONCURRENCY",
                Severity.WARNING,
                {
                    "active_requests": len(requests),
                    "threshold": settings.high_concurrency_threshold,
                },
                "核对业务并发基线和连接池；不将 connected sessions 当作并发 SQL。",
            )
        )
    if waiting:
        findings.append(
            Finding(
                "WAITING_SQL",
                Severity.WARNING,
                {"count": len(waiting), "requests": waiting[: settings.slow_top_n]},
                "结合 wait_type 和阻塞链解释当前等待，不据单次快照推断资源根因。",
            )
        )
    incomplete, visibility = collection_completeness(results, required_collectors, optional_collectors)
    if visibility == "PARTIAL":
        quality.append("Optional evidence is unavailable or truncated; visibility is partial.")
    if incomplete:
        findings.append(
            Finding(
                "INCOMPLETE_SNAPSHOT",
                Severity.WARNING,
                {"limitations": quality},
                "采集不完整；计数、根节点及未发现问题均不能解释为完整实例结论。",
            )
        )
    return SlowReport(
        instance,
        datetime.now(timezone.utc),
        "slow",
        tuple(findings),
        redact(
            {
                "version": version,
                "compatibility": "Only SQL Server 2022 tested; 2008 R2+ is a compatibility target.",
                "concurrency": concurrency,
                "top_requests": requests[: settings.slow_top_n],
                "all_sessions": sessions,
                "all_requests": requests,
                "long_running": long[: settings.slow_top_n],
                "blocking_graph": graph,
                "roots": roots,
                "transactions": transactions,
                "sources": connections,
                "quality": quality,
                "incomplete": incomplete,
                "data_visibility": visibility,
                "samples": [
                    {
                        "collector": r.collector_name,
                        "started_at": r.started_at,
                        "finished_at": r.finished_at,
                        "row_count": r.row_count,
                        "truncated": r.truncated,
                        "success": r.success,
                        "error_type": r.error_type,
                    }
                    for r in results
                ],
            }
        ),
    )


def diagnose_slow(adapter: SQLServerAdapter) -> SlowReport:
    version = adapter.get_server_version()
    results: list[CollectorResult] = []
    routed = profile_for_adapter(adapter) is not None
    required = set(required_collectors_for_adapter(
        adapter, ("active_requests", "active_sessions", "transactions", "connections")
    ))
    fatal = required if routed else {"active_requests", "active_sessions"}
    # Essential samples first. Optional DMV failure retains the useful snapshot;
    # the adapter still closes on failure, so no subsequent query uses that handle.
    for collector in (
        ActiveRequestsCollector,
        ActiveSessionsCollector,
        TransactionsCollector,
        ConnectionsCollector,
    ):
        result = collector(adapter).collect(adapter.instance)
        results.append(result)
        if not result.success and result.collector_name in fatal:
            raise ToolkitError(
                result.error_type or "QueryFailed",
                result.error_message or "Required sample unavailable.",
            )
        if not result.success:
            break
    return build_slow_report(adapter.instance, tuple(results), adapter.settings, version,
                             tuple(required), optional_collectors_for_adapter(adapter))
