"""Evidence-only Oracle slow diagnosis with instance-qualified blocking identities."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DiagnosticReport, Finding, ReferenceCommand, Severity
from dba.errors import ToolkitError
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
from dba.slow_profiles import (
    collection_completeness,
    emergency_collectors_for_adapter,
    optional_collectors_for_adapter,
    profile_for_adapter,
    required_collectors_for_adapter,
)


@dataclass(frozen=True)
class OracleSlowReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


def _positive(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _key(instance: Any, sid: Any) -> str | None:
    first, second = _positive(instance), _positive(sid)
    return f"{first}:{second}" if first is not None and second is not None else None


def _is_present(value: Any) -> bool:
    return value not in (None, "", b"")


def _address(value: Any) -> str | None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex().upper() or None
    if isinstance(value, str) and value.strip() and value != "[REDACTED OBJECT]":
        return value.strip().upper()
    return None


def _hide_addresses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    address_fields = {"saddr", "taddr", "transaction_addr", "session_address",
                      "session_transaction_address"}
    return [{key: "[REDACTED OBJECT]" if key in address_fields and value is not None else value
             for key, value in row.items()} for row in rows]


def _matches_session_transaction(session: dict[str, Any], tx: dict[str, Any]) -> bool:
    """Confirm the V$SESSION/V$TRANSACTION row join still identifies this session."""
    return (session.get("serial_number") == tx.get("serial_number")
            and _is_present(session.get("logon_time"))
            and session.get("logon_time") == tx.get("logon_time")
            and _address(session.get("saddr")) is not None
            and _address(session.get("saddr")) == _address(tx.get("saddr"))
            and _address(session.get("taddr")) is not None
            and _address(session.get("taddr")) == _address(tx.get("taddr"))
            and _address(tx.get("taddr")) == _address(tx.get("transaction_addr")))


def _oracle_xid(tx: dict[str, Any]) -> str | None:
    values = (tx.get("xidusn"), tx.get("xidslot"), tx.get("xidsqn"))
    if all(type(value) is int and value >= 0 for value in values):
        return ":".join(str(value) for value in values)
    return None


def _exact_oracle_sleep(sql: Any, event: Any, truncated: Any) -> bool:
    if not isinstance(sql, str) or bool(truncated) or event != "PL/SQL lock timer":
        return False
    normalized = " ".join(sql.strip().rstrip(";").split())
    return re.fullmatch(r"(?i)BEGIN\s+DBMS_SESSION\.SLEEP\s*\(\s*:1\s*\)\s*;?\s*END", normalized) is not None


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
                      lock_evidence: bool = True,
                      required_collectors: tuple[str, ...] =
                      ("active_sessions", "active_requests", "transactions", "blocking"),
                      optional_collectors: tuple[str, ...] = ()) -> OracleSlowReport:
    by_name = {result.collector_name: result for result in results}

    def rows(name: str) -> list[dict[str, Any]]:
        result = by_name.get(name)
        return [dict(row) for row in result.rows] if result and result.success else []

    sessions = rows("active_sessions")
    requests = rows("active_requests")
    transactions = rows("transactions")
    transactions = [{**row, "transaction_id": _oracle_xid(row),
                     "transaction_identity_quality": "VERIFIED" if _oracle_xid(row)
                     else "IDENTITY_INCOMPLETE"} for row in transactions]
    edges = rows("blocking")
    previous = rows("previous_sql")
    session_counts: dict[str, int] = {}
    transaction_counts: dict[str, int] = {}
    for row in sessions:
        key = _key(row.get("inst_id"), row.get("sid"))
        if key is not None:
            session_counts[key] = session_counts.get(key, 0) + 1
    for row in transactions:
        key = _key(row.get("inst_id"), row.get("sid"))
        if key is not None:
            transaction_counts[key] = transaction_counts.get(key, 0) + 1
    session_map = {_key(row.get("inst_id"), row.get("sid")): row for row in sessions
                   if session_counts.get(_key(row.get("inst_id"), row.get("sid")) or "") == 1}
    transaction_candidates: dict[str, list[dict[str, Any]]] = {}
    for row in transactions:
        tx_key = _key(row.get("inst_id"), row.get("sid"))
        if tx_key is not None:
            transaction_candidates.setdefault(tx_key, []).append(row)
    transaction_map = {key: candidates[0] for key, candidates in transaction_candidates.items()
                       if len(candidates) == 1}
    previous_map = {_key(row.get("inst_id"), row.get("sid")): row for row in previous}
    request_map = {_key(row.get("inst_id"), row.get("sid")): row for row in requests}
    graph = blocking_graph(edges, sessions)
    edge_identity_conflicts: list[str] = []
    for edge in edges:
        blocked_key = _key(edge.get("inst_id"), edge.get("blocked_sid"))
        blocker_key = _key(edge.get("blocker_inst_id"), edge.get("blocker_sid"))
        blocked_session = session_map.get(blocked_key or "")
        blocker_session = session_map.get(blocker_key or "")
        if (blocked_session is not None and edge.get("blocked_serial_number") is not None
                and blocked_session.get("serial_number") != edge.get("blocked_serial_number")):
            edge_identity_conflicts.append(f"blocked session identity changed: {blocked_key}")
        if (blocker_session is not None and edge.get("blocker_serial_number") is not None
                and blocker_session.get("serial_number") != edge.get("blocker_serial_number")):
            edge_identity_conflicts.append(f"blocker session identity changed: {blocker_key}")
    if edge_identity_conflicts:
        graph["anomalies"].extend(edge_identity_conflicts)
        graph["identity_conflicts"] = edge_identity_conflicts
    quality = [f"{r.collector_name}: {'failed' if not r.success else 'truncated' if r.truncated else 'ok'}"
               for r in results]
    incomplete, visibility = collection_completeness(results, required_collectors, optional_collectors)
    command_evidence_complete = all(
        name in by_name and by_name[name].success and not by_name[name].truncated
        for name in ("active_sessions", "active_requests", "transactions", "blocking")
    )
    transaction_sample_complete = bool(
        by_name.get("transactions") and by_name["transactions"].success
        and not by_name["transactions"].truncated
    )
    if not lock_evidence:
        visibility = "PARTIAL"
    incomplete = incomplete or (rac_enabled and not rac_global_snapshot)
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
        if not _matches_session_transaction(candidate, tx):
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
        candidates = transaction_candidates.get(root_key, [])
        tx_candidate = transaction_map.get(root_key, candidates[0] if len(candidates) == 1 else {})
        tx_associated = (transaction_sample_complete and session is not None and len(candidates) == 1
                         and _matches_session_transaction(session, tx_candidate)
                         and _oracle_xid(tx_candidate) is not None)
        tx = tx_candidate if tx_associated else {}
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
                "logon_time": session.get("logon_time") if session else None,
                "session_address": session.get("saddr") if session else None,
                "session_transaction_address": session.get("taddr") if session else None,
                "sql_id": session.get("sql_id") if session else None,
                "current_sql_id": current.get("sql_id") or (session.get("sql_id") if session else None),
                "current_sql": current.get("sql_text"),
                "current_sql_reason": None if current.get("sql_text") else
                "SQL text not obtained; session may be inactive, cursor unavailable or permission-limited",
                "prev_sql_id": prior.get("prev_sql_id") or (session.get("prev_sql_id") if session else None),
                "previous_sql": prior.get("previous_sql_text"),
                "previous_sql_reason": None if prior.get("previous_sql_text")
                else ("previous_sql collector failed: " + str(by_name["previous_sql"].error_type)
                      if by_name.get("previous_sql") and not by_name["previous_sql"].success else
                      "SQL text not obtained; cursor unavailable or permission-limited, cause unconfirmed"),
                "previous_sql_cause_note": "说明：上一条 SQL 不等于一定是持锁 SQL。",
                "event": session.get("event") if session else None,
                "wait_class": session.get("wait_class") if session else None,
                "transaction_observed": (bool(candidates) if by_name.get("transactions")
                                          and by_name["transactions"].success
                                          and (candidates or not by_name["transactions"].truncated)
                                          else None),
                "transaction_identity_verified": bool(tx_associated) if candidates else None,
                "transaction_age_seconds": tx_candidate.get("transaction_age_seconds") if candidates else None,
                "transaction_start_time": tx_candidate.get("transaction_start_time") if candidates else None,
                "transaction_start_time_source": "V$TRANSACTION.START_DATE" if candidates else None,
                "transaction_id": _oracle_xid(tx_candidate) if candidates else None,
                "transaction_identity_quality": "VERIFIED" if tx_associated else
                "UNVERIFIED" if candidates else "UNCONFIRMED",
                "unfinished_transaction_confirmed": True if tx_associated else None,
                "transaction_start_time_text": tx_candidate.get("transaction_start_time_text")
                if candidates else None,
                "used_ublk": tx_candidate.get("used_ublk") if candidates else None,
                "used_urec": tx_candidate.get("used_urec") if candidates else None,
                "blocked_count": len(affected), "blocked_sessions": affected,
                "direct_blocked_count": len(graph["children"].get(root_key, [])),
                "indirect_blocked_count": max(0, len(affected) - len(graph["children"].get(root_key, []))),
                "client_ip": None, "client_ip_reason": "not available from V$SESSION",
                "session_available": session is not None}
        roots.append(root)
        references: tuple[ReferenceCommand, ...] = ()
        sid = _positive(root["sid"])
        serial = _positive(root["serial_number"])
        inst_id = _positive(root["inst_id"])
        identity_conflict = False
        for sampled_identity in (request_map.get(root_key), previous_map.get(root_key),
                                 tx_candidate if tx_candidate else None):
            if sampled_identity and session is not None:
                identity_conflict = identity_conflict or any(
                    sampled_identity.get(field) is not None and session.get(field) is not None
                    and sampled_identity.get(field) != session.get(field)
                    for field in ("serial_number", "logon_time", "username", "machine", "program"))
        tx_verified = tx_associated
        identity_checks = {
            "INST_ID": inst_id is not None, "SID": sid is not None,
            "SERIAL#": serial is not None, "LOGON_TIME": _is_present(root.get("logon_time")),
            "USERNAME": bool(root.get("username")), "MACHINE": bool(root.get("machine")),
            "PROGRAM": bool(root.get("program")),
        }
        tx_checks = {
            "transaction row observed": bool(candidates),
            "unique transaction row": len(candidates) == 1,
            "transaction sample complete": transaction_sample_complete,
            "serial number matches": bool(candidates) and
            tx_candidate.get("serial_number") == root.get("serial_number"),
            "logon time matches": bool(candidates) and _is_present(root.get("logon_time"))
            and tx_candidate.get("logon_time") == root.get("logon_time"),
            "SADDR matches": bool(candidates) and _address(root.get("session_address")) is not None
            and _address(root.get("session_address")) == _address(tx_candidate.get("saddr")),
            "TADDR matches transaction ADDR": bool(candidates)
            and _address(root.get("session_transaction_address")) is not None
            and _address(root.get("session_transaction_address")) == _address(tx_candidate.get("taddr"))
            and _address(tx_candidate.get("taddr")) == _address(tx_candidate.get("transaction_addr")),
            "XID complete": bool(candidates) and _oracle_xid(tx_candidate) is not None,
        }
        instance_semantics_known = (not rac_enabled or rac_global_snapshot)
        safe = (all(identity_checks.values()) and tx_verified and not identity_conflict
                and session_counts.get(root_key) == 1 and transaction_counts.get(root_key) == 1
                and not graph["anomalies"] and root_key not in graph["unresolved"]
                and instance_semantics_known and command_evidence_complete)
        if not safe:
            failed_identity = [name for name, passed in identity_checks.items() if not passed]
            failed_transaction = [name for name, passed in tx_checks.items() if not passed]
            failures = ([f"会话身份字段缺失：{', '.join(failed_identity)}"] if failed_identity else [])
            if failed_transaction:
                failures.append(f"事务证据未通过：{', '.join(failed_transaction)}")
            if identity_conflict:
                failures.append("采样间会话身份冲突")
            if not instance_semantics_known:
                failures.append("RAC 全局实例范围未确认")
            if graph["anomalies"]:
                failures.append("阻塞图存在异常")
            if root_key in graph["unresolved"]:
                failures.append("阻塞图根会话未解析")
            if not command_evidence_complete:
                failures.append("必需采集失败或被截断")
            if not failures:
                failures.append("会话或事务存在重复采样行")
            reason = "；".join(failures)
        else:
            reason = None
        if safe:
            suffix = f",@{inst_id}" if rac_global_snapshot else ""
            references = (ReferenceCommand(
                f"ALTER SYSTEM KILL SESSION '{sid},{serial}{suffix}' IMMEDIATE;",
                "仅供 DBA 人工核对 INST_ID/SID/SERIAL#/LOGON_TIME、USERNAME、MACHINE、PROGRAM、事务 XID/开始时间及 SQL 上下文；会话可能已变化，执行可能引发回滚且不保证立即结束。Toolkit did NOT execute this command."),)
        root["manual_action"] = {
            "status": "AVAILABLE" if safe else "SUPPRESSED",
            "reason": reason,
            "target_type": "ORACLE_RAC_SESSION" if rac_enabled else "ORACLE_SESSION",
            "identity_evidence": {
                "inst_id": inst_id, "sid": sid, "serial_number": serial,
                "logon_time": root.get("logon_time"), "username": root.get("username"),
                "machine": root.get("machine"), "program": root.get("program"),
                "transaction_confirmed": tx_verified,
                "transaction_id": root.get("transaction_id"),
                "transaction_start_time": root.get("transaction_start_time"),
                "session_checks": identity_checks,
                "transaction_checks": tx_checks,
                "blocking_graph_reliable": not graph["anomalies"],
                "required_samples_complete": command_evidence_complete,
                "rac_enabled": rac_enabled, "rac_global_snapshot": rac_global_snapshot,
            },
        }
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

    blocked_victims: list[dict[str, Any]] = []
    for victim_key, direct_key in sorted(graph["parents"].items()):
        victim_session = session_map.get(victim_key, {})
        root_key = victim_key
        seen: set[str] = set()
        while root_key in graph["parents"] and root_key not in seen:
            seen.add(root_key)
            root_key = graph["parents"][root_key]
        blocked_victims.append({
            "session_key": victim_key,
            "inst_id": victim_session.get("inst_id"), "sid": victim_session.get("sid"),
            "serial_number": victim_session.get("serial_number"),
            "direct_blocker_id": direct_key, "root_blocker_id": root_key,
            "status": victim_session.get("status"), "event": victim_session.get("event"),
            "duration_seconds": request_map.get(victim_key, {}).get("duration_seconds"),
        })
    blocked_keys = set(graph["parents"])
    active_waits = [
        {"inst_id": row.get("inst_id"), "sid": row.get("sid"),
         "serial_number": row.get("serial_number"), "status": row.get("state"),
         "state": row.get("state"), "wait_event": row.get("event"),
         "duration_seconds": row.get("duration_seconds"),
         "sql": row.get("sql_text"), "sql_text_truncated": row.get("sql_text_truncated", False),
         "request_interpretation": "明确主动等待，不作为异常慢 SQL 优先候选"}
        for row in requests
        if _key(row.get("inst_id"), row.get("sid")) not in blocked_keys
        and _exact_oracle_sleep(row.get("sql_text"), row.get("event"), row.get("sql_text_truncated"))
    ]
    ranked = sorted((row for row in requests if _key(row.get("inst_id"), row.get("sid"))
                     not in blocked_keys and not _exact_oracle_sleep(
                         row.get("sql_text"), row.get("event"), row.get("sql_text_truncated"))),
                    key=duration, reverse=True)
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
    details = {"version": version,
               "version_identity": {"profile_route_version": version,
                                    "profile_route_source": "V$INSTANCE.VERSION",
                                    "product_release_name": None,
                                    "product_release_source": "not independently verified in slow snapshot"},
               "concurrency": concurrency, "top_queries": top,
               "long_running": long[:settings.slow_top_n], "blocking_graph": graph,
               "blocking_evidence": edges,
               "lock_evidence_reason": None if lock_evidence
               else "unavailable: optional V$LOCK/V$LOCKED_OBJECT grants missing",
               "roots": _hide_addresses(roots), "transactions": _hide_addresses(transactions),
               "idle_transactions": _hide_addresses(idle),
               "blocked_victims": blocked_victims, "active_waits": active_waits,
               "sessions": _hide_addresses(sessions), "active_requests": requests,
               "quality": quality, "incomplete": incomplete, "data_visibility": visibility,
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
    routed = profile_for_adapter(adapter) is not None
    required = set(required_collectors_for_adapter(
        adapter, ("active_sessions", "active_requests", "transactions", "blocking")
    ))
    fatal = required if routed else set()
    for collector in collectors:
        result = collector(adapter).collect(adapter.instance)
        results.append(result)
        if not result.success and result.collector_name in fatal:
            raise ToolkitError(result.error_type or "QueryFailed",
                               result.error_message or "Required sample unavailable.")
        if not result.success:
            break
    return build_slow_report(adapter.instance, tuple(results), adapter.settings,
                             adapter.get_server_version(),
                             rac_global_snapshot=adapter.capabilities["rac_global_snapshot"],
                             rac_enabled=adapter.rac_enabled,
                             lock_evidence=adapter.capabilities["lock_evidence"],
                             required_collectors=tuple(required),
                             optional_collectors=optional_collectors_for_adapter(adapter))


_STAGED_COLLECTORS = {
    "active_sessions": ActiveSessionsCollector,
    "active_requests": ActiveRequestsCollector,
    "transactions": TransactionsCollector,
    "blocking": BlockingCollector,
    "previous_sql": PreviousSQLCollector,
}


def collect_slow_phase(adapter: OracleAdapter, names: tuple[str, ...],
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


def build_slow_phase_report(adapter: OracleAdapter, results: tuple[CollectorResult, ...],
                            *, emergency: bool) -> OracleSlowReport:
    required = (emergency_collectors_for_adapter(adapter) if emergency else
                required_collectors_for_adapter(
                    adapter, ("active_sessions", "active_requests", "transactions", "blocking")))
    optional = () if emergency else optional_collectors_for_adapter(adapter)
    report = build_slow_report(
        adapter.instance, results, adapter.settings, adapter.get_server_version(),
        rac_global_snapshot=adapter.capabilities["rac_global_snapshot"],
        rac_enabled=adapter.rac_enabled, lock_evidence=adapter.capabilities["lock_evidence"],
        required_collectors=required, optional_collectors=optional)
    core = emergency_collectors_for_adapter(adapter)
    by_name = {item.collector_name: item for item in results}
    complete = all(name in by_name and by_name[name].success and not by_name[name].truncated
                   for name in core) and not (adapter.rac_enabled and not adapter.capabilities["rac_global_snapshot"])
    full_complete = all(name in by_name and by_name[name].success and not by_name[name].truncated
                        for name in required + optional) and bool(adapter.capabilities["lock_evidence"])
    report.details.update({"diagnostic_phase": "EMERGENCY" if emergency else "FINAL",
                           "emergency_evidence_complete": complete,
                           "emergency_evidence_status": "COMPLETE" if complete else "INCOMPLETE",
                           "full_profile_evidence_complete": full_complete if not emergency else False,
                           "full_profile_evidence_status": "PENDING" if emergency else
                           "COMPLETE" if full_complete else "PARTIAL",
                           "supplemental_not_collected": tuple(
                               name for name in required + optional if name not in by_name) if emergency else ()})
    return report
