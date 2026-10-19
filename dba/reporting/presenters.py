"""Bounded, human-oriented diagnostic report model and evidence presenters."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from dba.core import DiagnosticReport
from dba.incident import IncidentSummary
from dba.logging import redact
from dba.reporting.model import (
    DEFAULT_HUMAN_REPORT_POLICY,
    HumanReportPolicy,
    ReportCodeBlock,
    ReportModel,
    ReportSection,
    ReportTable,
)
from dba.reporting.normalizers import sqlserver_transaction_label

_VALUE_LABELS = {
    "TRANSACTION_LOG": "事务日志", "WAL": "WAL", "BINLOG": "Binlog",
    "REDO": "Redo", "ARCHIVE": "归档", "FRA": "FRA", "UNDO": "Undo",
    "TEMP": "临时空间", "DATA": "数据文件", "TABLE": "表", "INDEX": "索引",
    "ROOT_BLOCKER": "根阻塞", "IDLE_OPEN_TRANSACTION": "未结束事务",
    "BLOCKED_VICTIM": "被阻塞会话", "LONG_RUNNING_SQL": "长运行 SQL",
    "ACTIVE_WAIT": "主动等待语句",
    "TERMINATE_SESSION": "终止会话参考", "CANCEL_QUERY": "取消查询参考",
    "ACTIVE": "活动", "COMPLETE": "完整", "CRITICAL": "严重",
    "INFO": "提示", "WARNING": "警告", "HIGH": "高", "UNKNOWN": "未知",
    "RECHECK_REQUIRED": "需要重新核验", "VERIFIED": "事务关联已核验",
    "AMBIGUOUS": "身份存在冲突", "IDENTITY_INCOMPLETE": "身份信息不完整",
}
_FINDING_LABELS = {
    "SQLSERVER_DATAFILE_NEAR_LIMIT": "SQL Server 数据文件接近容量上限",
    "SQLSERVER_TEMPDB_HIGH_USAGE": "SQL Server TempDB 使用率较高",
    "SQLSERVER_LOG_HIGH_USAGE": "SQL Server 事务日志使用率较高",
    "SQLSERVER_LOG_REUSE_BLOCKED": "SQL Server 事务日志复用受阻",
    "SQLSERVER_OLD_ACTIVE_TRANSACTION": "SQL Server 存在长期活动事务",
    "PG_WAL_RETENTION_SLOT": "PostgreSQL 复制槽保留 WAL",
    "PG_ARCHIVE_FAILURE": "PostgreSQL WAL 归档失败线索",
    "PG_LONG_TRANSACTION": "PostgreSQL 存在长期事务",
    "BINLOG_TOTAL_SIZE_HIGH": "MySQL Binlog 总量较大",
    "REPLICA_RELAY_LOG_RISK": "MySQL 复制中继日志风险",
    "LONG_TRANSACTION": "MySQL 存在长期事务",
    "ORACLE_UNDO_HIGH_USAGE": "Oracle Undo 使用率较高",
    "ORACLE_TEMP_HIGH_USAGE": "Oracle 临时空间使用率较高",
    "ORACLE_TABLESPACE_HIGH_USAGE": "Oracle 表空间使用率较高",
    "ORACLE_FRA_HIGH_USAGE": "Oracle FRA 使用率较高",
    "ORACLE_FLASHBACK_HIGH_USAGE": "Oracle Flashback 日志使用率较高",
    "ORACLE_ARCHIVE_DEST_FAILURE": "Oracle 归档目标异常",
    "ORACLE_ARCHIVE_ACCUMULATION": "Oracle 归档量较大",
}


def _local_time(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(sep=" ", timespec="seconds") + " (时区未知)"
        moment = value.astimezone()
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.isoformat(sep=" ", timespec="seconds") + " (时区未知)"
            moment = parsed.astimezone()
        except ValueError:
            return value
    else:
        return "UNKNOWN"
    return moment.isoformat(sep=" ", timespec="seconds")


def _human(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, datetime):
        return _local_time(value)
    if isinstance(value, dict):
        return "; ".join(f"{_label(str(key))}: {_human(item)}" for key, item in value.items())
    if isinstance(value, (tuple, list, set)):
        return ", ".join(_human(item) for item in value) if value else "—"
    if isinstance(value, str) and value in _VALUE_LABELS:
        return _VALUE_LABELS[value]
    return str(redact(value))


def _label(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def _duration_ms(value: Any) -> str:
    if type(value) not in (int, float) or value < 0:
        return _human(value)
    seconds = int(value // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _duration_seconds(value: Any) -> str:
    return _duration_ms(value * 1000) if type(value) in (int, float) and value >= 0 else _human(value)


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _session_id(row: dict[str, Any]) -> str | None:
    for key in ("session_id", "pid", "processlist_id", "sid"):
        value = row.get(key)
        if value not in (None, ""):
            if key == "sid" and row.get("inst_id") is not None:
                return f"{row['inst_id']}:{value}"
            return str(value)
    return None


def _first(row: dict[str, Any], *keys: str) -> Any:
    return next((row[key] for key in keys if row.get(key) is not None), None)


def _fixed_table(rows: list[dict[str, Any]], columns: tuple[tuple[str, tuple[str, ...], Callable[[Any], str]], ...]
                 ) -> ReportTable | None:
    if not rows:
        return None
    visible = [(label, keys, formatter) for label, keys, formatter in columns
               if any(_first(row, *keys) not in (None, "") for row in rows)]
    if not visible:
        return None
    return ReportTable(tuple(label for label, _, _ in visible), tuple(
        tuple(formatter(_first(row, *keys)) for _, keys, formatter in visible) for row in rows))


def _table(rows: list[dict[str, Any]], columns: tuple[tuple[str, tuple[str, ...]], ...]
           ) -> ReportTable | None:
    return _fixed_table(rows, tuple((label, keys, _human) for label, keys in columns))


def _omitted(total: int, displayed: int, noun: str) -> str:
    omitted = max(0, total - displayed)
    text = f"共{total}条，显示{displayed}条，省略{omitted}条。"
    if omitted:
        text += f" 人工报告省略其余{noun}；结构化数据请使用 --json。"
    return text


def _sql_block(row: dict[str, Any], policy: HumanReportPolicy, label: str = "SQL") -> ReportCodeBlock | None:
    sql = _first(row, "sql_text", "query", "sql_fulltext", "current_sql", "previous_sql")
    if not isinstance(sql, str) or not sql:
        return None
    clipped = sql[:policy.max_sql_chars]
    is_truncated = clipped != sql or bool(_first(row, "sql_text_truncated", "query_truncated"))
    title = f"Session {_session_id(row) or 'UNKNOWN'} {label}"
    if is_truncated:
        title += " (truncated)"
        clipped += "\n[SQL text omitted by human report limit.]" if len(sql) > policy.max_sql_chars else ""
    return ReportCodeBlock(title, str(redact(clipped)), "sql", True)


def _role_map(roots: list[Any], children: dict[str, list[Any]]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for root in roots:
        root_id = str(root)
        roles[root_id] = "Root Blocker"
        pending = [str(child) for child in children.get(root_id, [])]
        while pending:
            child = pending.pop(0)
            if child in roles:
                continue
            roles[child] = "Blocked"
            pending.extend(str(item) for item in children.get(child, []))
    return roles


def _blocking_sections(roots: list[Any], children: dict[str, list[Any]],
                       sessions: dict[str, dict[str, Any]], requests: dict[str, dict[str, Any]],
                       policy: HumanReportPolicy) -> tuple[list[ReportSection], set[str]]:
    root_rows: list[dict[str, Any]] = []
    tree_rows: list[dict[str, Any]] = []
    related: set[str] = set()
    parent_of = {str(child): str(parent) for parent, values in children.items() for child in values}
    for root in roots[:policy.max_root_blockers]:
        root_id = str(root)
        pending = [(root_id, 0)]
        visited: set[str] = set()
        ordered: list[tuple[str, int]] = []
        max_wait = 0
        while pending:
            sid, depth = pending.pop(0)
            if sid in visited:
                continue
            visited.add(sid)
            ordered.append((sid, depth))
            related.add(sid)
            request = requests.get(sid, {})
            wait = _first(request, "wait_time_ms", "wait_time", "wait_ms")
            if type(wait) in (int, float):
                max_wait = max(max_wait, int(wait))
            pending.extend((str(child), depth + 1) for child in children.get(sid, []))
        if not ordered:
            continue
        descendants_count = max(0, len(ordered) - 1)
        root_rows.append({"root": root_id, "affected": descendants_count,
                          "indirect": max(0, descendants_count - len(children.get(root_id, []))),
                          "direct": len(children.get(root_id, [])),
                          "depth": max(depth for _, depth in ordered),
                          "longest": _duration_ms(max_wait) if max_wait else "—"})
        shown = ordered[:policy.max_blocking_nodes_per_root]
        for index, (sid, depth) in enumerate(shown):
            request = requests.get(sid, {})
            session = sessions.get(sid, {})
            tree_rows.append({"root": root_id,
                              "session": f"{'  ' * depth}{'└─ ' if depth else ''}{sid}",
                              "role": "Root Blocker" if depth == 0 else "Blocked",
                              "parent": parent_of.get(sid, "—"),
                              "wait": _first(request, "wait_type", "wait_event", "current_wait"),
                              "wait_resource": request.get("wait_resource"),
                              "elapsed": (_duration_ms(_first(request, "elapsed_ms", "duration_ms"))
                                         if _first(request, "elapsed_ms", "duration_ms") is not None else
                                         _duration_seconds(request.get("duration_seconds"))),
                              "login": _first(session, "login_name", "usename", "user_name", "username")})
        tree_rows.append({"root": root_id, "session": "—", "role": "Display summary",
                          "parent": "—", "wait": "—", "wait_resource": "—",
                          "elapsed": _omitted(len(ordered), len(shown), "blocking nodes"),
                          "login": "—"})
    root_table = _table(root_rows, (("Root Session", ("root",)),
                                   ("Affected Sessions", ("affected",)),
                                   ("Indirectly Affected", ("indirect",)),
                                   ("Direct Blocked", ("direct",)),
                                   ("Maximum Depth", ("depth",)),
                                   ("Longest Block", ("longest",))))
    summaries = [ReportSection(
        "root-blockers", "Root Blockers",
        summary=_omitted(len(roots), len(root_rows), "root blockers"),
        tables=(root_table,) if root_table else ())]
    tree_table = _table(tree_rows, (("Root", ("root",)), ("Session", ("session",)),
                                   ("Role", ("role",)), ("Direct Blocker", ("parent",)),
                                   ("Wait", ("wait",)), ("Wait Resource", ("wait_resource",)),
                                   ("Elapsed", ("elapsed",)), ("Login", ("login",))))
    tree = ReportSection("blocking", "Blocking Analysis",
                         summary="当前有界采样观察到的阻塞关系；直接与间接受影响会话分别计数。"
                         if tree_table else "当前采样未观察到阻塞关系。",
                         tables=(tree_table,) if tree_table else ())
    return summaries + [tree], related


def _long_running_rows(report: DiagnosticReport, details: dict[str, Any]) -> list[dict[str, Any]]:
    configured = _dict_rows(details.get("long_running"))
    if report.instance.db_type.value in {"mysql", "oracle"}:
        # These analyzers exclude confirmed blocked victims and exact intentional waits.
        return configured
    finding = next((item for item in report.findings if item.title == "LONG_RUNNING_SQL"), None)
    threshold = finding.evidence.get("threshold_seconds") if finding else None
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return configured
    requests = _dict_rows(details.get("all_requests", details.get("active_requests")))
    long_rows = []
    for row in requests:
        elapsed = _first(row, "elapsed_ms", "duration_ms")
        duration_seconds = row.get("duration_seconds")
        if elapsed is None and isinstance(duration_seconds, (int, float)) \
                and not isinstance(duration_seconds, bool):
            elapsed = duration_seconds * 1000
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) \
                and elapsed >= float(threshold) * 1000:
            long_rows.append(row)
    return long_rows or configured


def _session_and_request_sections(report: DiagnosticReport, details: dict[str, Any],
                                  policy: HumanReportPolicy
                                  ) -> tuple[list[ReportSection], set[str]]:
    sessions_list = _dict_rows(details.get("all_sessions", details.get("sessions")))
    requests_list = _dict_rows(details.get("all_requests", details.get("active_requests")))
    long_rows = _long_running_rows(report, details)
    graph_value = details.get("blocking_graph")
    graph = graph_value if isinstance(graph_value, dict) else {}
    roots_value = graph.get("roots")
    roots: list[Any] = roots_value if isinstance(roots_value, list) else []
    raw_children = graph.get("children")
    raw_children_dict: dict[Any, Any] = raw_children if isinstance(raw_children, dict) else {}
    children: dict[str, list[Any]] = {
        str(key): value for key, value in raw_children_dict.items() if isinstance(value, list)}
    session_by_id = {(_session_id(row) or ""): row for row in sessions_list}
    request_by_id = {(_session_id(row) or ""): row for row in requests_list}
    sections, related = _blocking_sections(roots, children, session_by_id, request_by_id, policy)
    roles = _role_map(roots, children)
    long_by_id = {(_session_id(row) or ""): row for row in long_rows}
    for sid in long_by_id:
        if sid:
            related.add(sid)
            roles.setdefault(sid, "Long Running")

    def session_order(sid: str) -> tuple[int, int, str]:
        role_order = {"Root Blocker": 0, "Blocked": 1, "Long Running": 2}.get(roles.get(sid, ""), 3)
        numeric = int(sid) if sid.isdigit() else 0
        return role_order, numeric, sid

    related_rows = []
    for sid in sorted(related, key=session_order):
        session = session_by_id.get(sid, {})
        request = request_by_id.get(sid, {})
        related_rows.append({"session": sid, "role": roles.get(sid, "Related"),
                             "database": _first(request, "database_name", "datname") or
                                         _first(session, "database_name", "datname"),
                             "status": _first(request, "status", "state") or
                                       _first(session, "status", "state"),
                             "login": _first(session, "login_name", "usename", "user_name") or
                                      _first(request, "login_name", "usename", "user_name"),
                             "host": _first(session, "host_name", "client_hostname", "machine") or
                                     _first(request, "host_name", "client_hostname", "machine"),
                             "program": _first(session, "program_name", "application_name", "program") or
                                        _first(request, "program_name", "application_name", "program"),
                             "client_ip": _first(session, "client_net_address", "client_addr", "client_ip") or
                                          _first(request, "client_net_address", "client_addr", "client_ip"),
                             "open_txn": _first(session, "open_transaction_count", "open_tran")})
    related_total = len(related_rows)
    related_rows = related_rows[:policy.max_related_sessions]
    identity_columns: tuple[tuple[str, tuple[str, ...]], ...] = (("Session", ("session",)), ("Role", ("role",)),
                        ("Database", ("database",)), ("Status", ("status",)),
                        ("Login", ("login",)), ("Host", ("host",)),
                        ("Program", ("program",)), ("Client IP", ("client_ip",)))
    if report.instance.db_type.value == "sqlserver":
        identity_columns += (("Session Open Transaction Count", ("open_txn",)),)
    identity_table = _table(related_rows, identity_columns)
    identity_summary = ("PostgreSQL 会话身份来自 pg_stat_activity 采样；backend_start 和 xact_start 用于人工复核。"
                        if report.instance.db_type.value == "postgresql" else
                        "Oracle 会话身份按 INST_ID/SID 区分；事务关联依据会话地址及 SERIAL#/LOGON_TIME 交叉核验。"
                        if report.instance.db_type.value == "oracle" else
                        "会话级 open_transaction_count 与事务 DMV 返回的事务行数语义不同。")
    sections.append(ReportSection(
        "session-sources", "Session Identity",
        summary=identity_summary + " " +
                _omitted(related_total, len(related_rows), "incident-related sessions"),
        tables=(identity_table,) if identity_table else ()))

    runtime_rows = []
    database_type = report.instance.db_type.value
    visible_related_ids = {str(row["session"]) for row in related_rows}
    for sid in sorted(related, key=session_order):
        if sid not in visible_related_ids:
            continue
        row = request_by_id.get(sid, {})
        if not row:
            continue
        runtime_rows.append({"session": sid, "status": _first(row, "session_status", "status", "state"),
                             "elapsed": _duration_ms(_first(row, "elapsed_ms", "duration_ms"))
                                       if _first(row, "elapsed_ms", "duration_ms") is not None else
                                       _duration_seconds(row.get("duration_seconds")),
                             "wait": _first(row, "wait_type", "wait_event", "event", "current_wait"),
                             "wait_resource": row.get("wait_resource"),
                             "blocked_by": (f"{row.get('blocking_instance')}:{row.get('blocking_session')}"
                                            if database_type == "oracle" and row.get("blocking_session") is not None
                                            else _first(row, "blocking_session_id", "blocking_pid",
                                                        "blocking_sid")),
                             "sql_id": row.get("sql_id"), "sql": row.get("sql_text"),
                             "cpu": _first(row, "cpu_time_ms", "cpu_ms", "cpu_time"),
                             "logical_reads": _first(row, "logical_reads"),
                             "reads": _first(row, "reads"), "writes": _first(row, "writes")})
    runtime_columns = [("Session", ("session",)), ("Status", ("status",)),
                       ("Elapsed", ("elapsed",)), ("Wait", ("wait",)),
                       ("Blocked By", ("blocked_by",))]
    if database_type == "oracle":
        runtime_columns.extend([("SQL ID", ("sql_id",)), ("SQL", ("sql",))])
    elif database_type == "sqlserver":
        runtime_columns.extend([("Wait Resource", ("wait_resource",)), ("CPU", ("cpu",)),
                                ("Logical Reads", ("logical_reads",)), ("Reads", ("reads",)),
                                ("Writes", ("writes",))])
    runtime_table = _table(runtime_rows, tuple(runtime_columns))
    if runtime_table:
        runtime_summary = ("MySQL Performance Schema 提供当前语句耗时和可见等待事件；等待 consumer 未启用时等待详情不可用。"
                           if report.instance.db_type.value == "mysql" else
                           "PostgreSQL pg_stat_activity 提供当前 backend 的语句耗时与 wait event；不表示累计 CPU 或 I/O 计数。"
                           if report.instance.db_type.value == "postgresql" else
                           "Oracle V$SESSION/V$SQL 当前 SQL 与等待信息；耗时为本次执行时长，V$SQL 资源计数在本报告中不投影。"
                           if report.instance.db_type.value == "oracle" else
                           "CPU、logical reads、reads、writes 为 DMV 当前累计值；单次采样为零不代表历史没有资源消耗。")
        sections.append(ReportSection(
            "request-runtime", "Request Runtime",
            summary=runtime_summary,
            tables=(runtime_table,)))

    root_ids = {str(item) for item in roots}
    blocked_ids = {str(child) for values in children.values() for child in values}
    parent_of = {str(child): str(parent) for parent, values in children.items() for child in values}

    def root_for(session_id: str) -> str | None:
        seen: set[str] = set()
        current = session_id
        while current not in seen:
            seen.add(current)
            parent = parent_of.get(current)
            if parent is None:
                return current if current in root_ids else None
            current = parent
        return None
    root_related: list[dict[str, Any]] = []
    blocked_long: list[dict[str, Any]] = []
    nonblocked: list[dict[str, Any]] = []
    for row in long_rows:
        sid = _session_id(row) or "UNKNOWN"
        blocker = _first(row, "blocking_session_id", "blocking_pid", "blocking_sid")
        is_blocked = blocker not in (None, "", 0, "0") or sid in blocked_ids
        if sid in roles and (sid in root_ids or sid in blocked_ids):
            root_related.append(row)
        elif is_blocked:
            blocked_long.append(row)
        else:
            nonblocked.append(row)
    def elapsed_key(row: dict[str, Any]) -> float:
        value = _first(row, "elapsed_ms", "duration_ms")
        if type(value) in (int, float):
            return float(value)
        seconds = row.get("duration_seconds")
        return (float(seconds) * 1000 if isinstance(seconds, (int, float))
                and not isinstance(seconds, bool) else 0.0)

    root_related.sort(key=elapsed_key, reverse=True)
    blocked_long.sort(key=elapsed_key, reverse=True)
    nonblocked.sort(key=elapsed_key, reverse=True)
    counts = (f"长运行请求 {len(long_rows)} 条 · 阻塞相关 "
              f"{len(root_related) + len(blocked_long)} 条 · 非阻塞 {len(nonblocked)} 条。")
    if long_rows:
        sections.append(ReportSection("workload-summary", "Workload Summary", summary=counts))
        blocked_budget = policy.max_blocked_requests
        for section_id, title, values, cap, noun in (
            ("root-related-requests", "根阻塞相关请求", root_related,
             policy.max_blocked_requests, "root-related requests"),
            ("blocked-long-running", "被阻塞的长运行请求", blocked_long,
             policy.max_blocked_requests, "blocked requests"),
            ("nonblocked-long-running", "非阻塞的长运行请求", nonblocked,
             policy.max_nonblocked_long_running, "non-blocked requests"),
        ):
            visible = values[:cap]
            if section_id in {"root-related-requests", "blocked-long-running"}:
                visible = values[:blocked_budget]
                blocked_budget -= len(visible)
            rows = [{"session": _session_id(row), "status": _first(row, "status", "state"),
                     "elapsed": _duration_ms(_first(row, "elapsed_ms", "duration_ms"))
                               if _first(row, "elapsed_ms", "duration_ms") is not None else
                               _duration_seconds(row.get("duration_seconds")),
                     "wait": _first(row, "wait_type", "wait_event", "current_wait"),
                     "wait_resource": row.get("wait_resource"),
                     "blocker": _first(row, "blocking_session_id", "blocking_pid", "blocking_sid"),
                     "root_blocker": root_for(str(_session_id(row) or "")),
                     "interpretation": ("明确的主动等待语句，不代表数据库资源瓶颈" if
                         row.get("request_kind") == "ACTIVE_WAIT" else
                         "长时间主动等待（WAITFOR）" if
                         str(row.get("wait_type", "")).upper() == "WAITFOR" and
                         isinstance(row.get("sql_text"), str) and
                         re.search(r"\bWAITFOR\s+(DELAY|TIME)\b", row["sql_text"], re.IGNORECASE)
                         else "WAITFOR 等待事件；SQL 文本不可用，语义需复核" if
                         str(row.get("wait_type", "")).upper() == "WAITFOR" else None),
                     "database": _first(row, "database_name", "datname")} for row in visible]
            table = _table(rows, (("Session", ("session",)), ("Status", ("status",)),
                                  ("Elapsed", ("elapsed",)), ("Wait", ("wait",)),
                                  ("Wait Resource", ("wait_resource",)),
                                  ("Direct Blocker", ("blocker",)),
                                  ("Root Blocker", ("root_blocker",)),
                                  ("Observation", ("interpretation",)),
                                  ("Database", ("database",))))
            blocks = tuple(block for row in visible
                           if (block := _sql_block(row, policy)) is not None)
            if values:
                sections.append(ReportSection(section_id, title,
                                              summary=_omitted(len(values), len(visible), noun),
                                              tables=(table,) if table else (), code_blocks=blocks))

    wait_source = requests_list
    wait_rows = [{"session": _session_id(row),
                  "wait": _first(row, "wait_type", "wait_event", "current_wait"),
                  "duration": _duration_ms(_first(row, "wait_time_ms", "wait_time", "wait_ms")),
                  "blocker": _first(row, "blocking_session_id", "blocking_pid", "blocking_sid")}
                 for row in wait_source if _first(row, "wait_type", "wait_event", "current_wait")]
    shown_waits = wait_rows[:policy.max_wait_rows]
    wait_table = _table(shown_waits, (("Session", ("session",)), ("Wait", ("wait",)),
                                      ("Duration", ("duration",)), ("Blocker", ("blocker",))))
    if wait_rows:
        sections.append(ReportSection("wait-analysis", "Wait Analysis",
                                      summary=_omitted(len(wait_rows), len(shown_waits), "wait rows"),
                                      tables=(wait_table,) if wait_table else ()))

    unrelated = [row for row in sessions_list if (_session_id(row) or "") not in related]
    appendix_rows = [{"session": _session_id(row),
                      "database": _first(row, "database_name", "datname"),
                      "status": _first(row, "status", "state"),
                      "login": _first(row, "login_name", "usename", "user_name"),
                      "host": _first(row, "host_name", "client_hostname", "machine"),
                      "program": _first(row, "program_name", "application_name", "program")}
                     for row in unrelated[:policy.max_appendix_sessions]]
    appendix_table = _table(appendix_rows, (("Session", ("session",)), ("Database", ("database",)),
                                           ("Status", ("status",)), ("Login", ("login",)),
                                           ("Host", ("host",)), ("Program", ("program",))))
    if unrelated:
        sections.append(ReportSection("appendix", "Appendix — Other Sessions",
                                      summary=_omitted(len(unrelated), len(appendix_rows), "other sessions"),
                                      tables=(appendix_table,) if appendix_table else (), collapsed=True))
    return sections, related


def _transaction_section(details: dict[str, Any], database_type: str,
                         policy: HumanReportPolicy) -> ReportSection | None:
    rows = _dict_rows(details.get("transactions")) or _dict_rows(details.get("idle_transactions"))
    if not rows:
        return None
    output = []
    for row in rows:
        age = _first(row, "transaction_age_seconds", "age_seconds")
        if database_type == "sqlserver":
            kind = sqlserver_transaction_label(row.get("transaction_type"))
            state = sqlserver_transaction_label(row.get("transaction_state"), state=True)
        else:
            kind = _human(_first(row, "transaction_type", "type"))
            state = _human(_first(row, "transaction_state", "state"))
        session = row.get("trx_mysql_thread_id") if database_type == "mysql" else _session_id(row)
        output.append({"session": session,
                       "transaction_id": _first(row, "transaction_id", "trx_id", "xid"),
                       "begin": _local_time(_first(row, "transaction_begin_time", "begin_time",
                                                     "trx_started", "xact_start"))
                                if _first(row, "transaction_begin_time", "begin_time",
                                          "trx_started", "xact_start") is not None else "—",
                       "age": _duration_seconds(age), "type": kind,
                       "state": _human(_first(row, "trx_state")) if database_type == "mysql"
                       else state})
    shown = output[:policy.max_transactions]
    table = _table(shown, (("Session", ("session",)), ("Transaction ID", ("transaction_id",)),
                           ("Begin Time", ("begin",)), ("Age", ("age",)),
                           ("Type", ("type",)), ("State", ("state",))))
    return ReportSection("transaction-analysis", "Transaction Analysis",
                         summary=_omitted(len(output), len(shown), "transactions"),
                         tables=(table,) if table else ())


def _recovery_section(incident: IncidentSummary, details: dict[str, Any], scenario: str,
                      database_type: str = "", report: DiagnosticReport | None = None) -> ReportSection:
    identity = {
        "sqlserver": "session_id、login_time、login_name、host_name、program_name、transaction identity",
        "postgresql": "pid、backend_start、xact_start、usename、application_name",
        "mysql": "connection_id、user、host、transaction id/start",
        "oracle": "SID、SERIAL#、INST_ID（适用时）、USERNAME、事务和 SQL 上下文",
    }.get(database_type, "会话标识、登录时间、用户、来源、事务及 SQL 上下文")
    safety_fields = (("人工身份复核", identity),
                     ("处置风险", "终止不等于立即恢复；可能发生回滚并增加 I/O、日志/Undo 压力和应用错误。"),
                     ("Toolkit 是否执行", "否"))
    actions = incident.recovery_actions
    if not actions:
        root_finding = next((finding for finding in report.findings
                             if finding.title == "ROOT_BLOCKER"), None) if report else None
        manual_action = (root_finding.evidence.get("manual_action", {})
                         if root_finding is not None else {})
        explicit_reason = (manual_action.get("reason")
                           if isinstance(manual_action, dict) else None)
        if scenario == "disk-growth":
            reason = "空间诊断不会生成自动或破坏性修复操作。"
        elif explicit_reason:
            reason = f"未生成具体命令：{explicit_reason}"
        elif incident.data_completeness != "COMPLETE":
            reason = "完整 Profile 证据不完整；人工处置参考仍按根阻塞身份证据单独判定。"
        elif scenario != "slow":
            reason = "此诊断场景不适用会话处置参考命令。"
        elif not isinstance(details.get("blocking_graph"), dict) or not details["blocking_graph"].get("roots"):
            reason = "未观察到具有业务影响的已确认根阻塞。"
        else:
            reason = "当前证据不足以生成安全的人工处置参考。"
        return ReportSection("immediate-recovery", "Immediate Recovery", summary=reason,
                             fields=(("需要 DBA 审批", "如后续决定处置，必须先经 DBA 审核"),
                                     *safety_fields))
    rows = [{"session": action.target_session, "action": action.action_type,
             "reason": action.reason, "open_txn": action.open_transaction,
             "risk": action.rollback_risk, "duration": action.rollback_duration,
             "approval": "YES" if action.approval_required else "NO",
             "executed": "NO"} for action in actions]
    table = _table(rows, (("Target Session", ("session",)), ("Action", ("action",)),
                          ("Reason", ("reason",)), ("Open Transaction", ("open_txn",)),
                          ("Rollback Risk", ("risk",)), ("Rollback Duration", ("duration",)),
                          ("DBA Approval Required", ("approval",)),
                          ("Toolkit Executed", ("executed",))))
    commands = tuple(ReportCodeBlock(f"会话 {action.target_session} 参考语句（折叠；人工审核）",
                                     action.reference_command, "sql", True)
                     for action in actions)
    return ReportSection("immediate-recovery", "Immediate Recovery", severity="WARNING",
                         summary="以下仅为基于历史采样的参考文本；执行前必须重新核对会话身份。Toolkit 未执行。"
                                 "回滚耗时未知，终止大事务可能增加日志占用、I/O 压力并引发应用错误。",
                         fields=safety_fields,
                         tables=(table,) if table else (), code_blocks=commands)


def _quality_section(report: DiagnosticReport, incident: IncidentSummary,
                     details: dict[str, Any]) -> ReportSection:
    quality = []
    for sample in details.get("samples", []):
        if not isinstance(sample, dict):
            continue
        name = sample.get("collector", sample.get("source", "unknown"))
        success = bool(sample.get("success", True))
        truncated = bool(sample.get("truncated", False))
        collector = str(name)
        optional = collector in details.get("optional_unavailable", [])
        required = collector in details.get("required_unavailable", [])
        status = ("MISSING_REQUIRED" if required else
                  "OPTIONAL_UNAVAILABLE" if optional else
                  "FAILED" if not success else "TRUNCATED" if truncated else "COMPLETE")
        impact = (sample.get("error_type") or sample.get("error_message") or
                  ("Collector row limit reached" if truncated else "—"))
        quality.append({"collector": name, "status": status, "impact": impact})
    sampled = {str(item.get("collector", item.get("source", "unknown")))
               for item in details.get("samples", []) if isinstance(item, dict)}
    for name in details.get("required_unavailable", []):
        if name not in sampled:
            quality.append({"collector": name, "status": "MISSING_REQUIRED",
                            "impact": "Required evidence was not collected."})
    for name in details.get("optional_unavailable", []):
        if name not in sampled:
            quality.append({"collector": name, "status": "OPTIONAL_UNAVAILABLE",
                            "impact": "Optional context was not collected."})
    for name in details.get("supplemental_not_collected", []):
        quality.append({"collector": name, "status": "NOT_COLLECTED",
                        "impact": "应急初报后未采集；不表示查询失败。"})
    for note in details.get("quality", []):
        if isinstance(note, str):
            quality.append({"collector": "Snapshot", "status": "NOTE", "impact": note})
    if not quality:
        quality = [{"collector": item, "status": "MISSING", "impact": "Required evidence unavailable"}
                   for item in incident.missing_evidence]
    table = _table(quality, (("Collector", ("collector",)), ("Status", ("status",)),
                             ("Impact", ("impact",))))
    summary = f"证据完整性：{incident.data_completeness}。"
    if details.get("emergency_evidence_status"):
        summary += (f" 应急证据：{details.get('emergency_evidence_status')}。"
                    f"完整配置证据：{details.get('full_profile_evidence_status', 'UNKNOWN')}。")
    if report.scenario == "disk-growth":
        summary += (f" 必需证据：{incident.required_evidence_status}。"
                    f"可选证据：{incident.optional_evidence_status}。")
    if details.get("incomplete"):
        summary += " 当前采样不完整；未发现异常不代表异常不存在。"
    return ReportSection("collection-quality", "Collection Quality", summary=summary,
                         tables=(table,) if table else ())


def _finding_section(report: DiagnosticReport) -> ReportSection | None:
    findings = getattr(report, "findings", ())
    if not findings:
        return None
    rows = []
    for item in findings:
        title = _FINDING_LABELS.get(item.title, item.title.replace("_", " ").title())
        evidence_ref = "Root Blockers" if "BLOCK" in item.title else \
                       "Transaction Analysis" if "TRANSACTION" in item.title else \
                       "Request Runtime" if item.session else "Diagnostic Summary"
        severity = {"CRITICAL": "严重", "HIGH": "高", "WARNING": "警告",
                    "INFO": "提示"}.get(item.severity.value, item.severity.value)
        rows.append({"finding": title, "severity": severity,
                     "session": item.session or "—", "evidence": evidence_ref,
                     "recommendation": item.recommendation})
    table = _table(rows, (("Finding", ("finding",)), ("Severity", ("severity",)),
                          ("Session", ("session",)), ("Evidence Reference", ("evidence",)),
                          ("Recommendation", ("recommendation",))))
    return ReportSection("findings", "Findings and Recommendations", tables=(table,) if table else ())


def _slow_sections(report: DiagnosticReport, incident: IncidentSummary,
                   details: dict[str, Any], policy: HumanReportPolicy) -> list[ReportSection]:
    sections: list[ReportSection] = [_recovery_section(incident, details, report.scenario,
                                                       report.instance.db_type.value, report)]
    priority = [item for item in details.get("priority_candidates", []) if isinstance(item, dict)]
    priority_rows = [{"rank": item.get("rank"), "session": item.get("session_id"),
                      "category": _human(item.get("categories", item.get("category"))),
                      "reason": item.get("reason"),
                      "user": item.get("username"), "host": item.get("host"),
                      "client_ip": item.get("client_ip"), "database": item.get("database"),
                      "application": item.get("application"), "state": item.get("state"),
                      "runtime": item.get("running_seconds"),
                      "sql_id": item.get("sql_id"), "transaction_id": item.get("transaction_id"),
                      "mysql_connection_id": item.get("mysql_connection_id"),
                      "performance_schema_thread_id": item.get("performance_schema_thread_id"),
                      "sql_id_type": item.get("sql_id_type"),
                      "login_time": item.get("login_time"),
                      "transaction_age": item.get("transaction_age_seconds"),
                      "oracle_inst_id": item.get("oracle_inst_id"),
                      "oracle_serial_number": item.get("oracle_serial_number"),
                      "transaction_start_time": item.get("transaction_start_time"),
                      "blocked_count": item.get("blocked_session_count"),
                      "evidence_timestamp": item.get("evidence_timestamp"),
                      "identity_quality": item.get("identity_quality"),
                      "identity_reuse_note": item.get("identity_reuse_note"),
                      "identity_recheck": "人工处置前必须重新核对会话身份"}
                     for item in priority[:5]]
    priority_table = _table(priority_rows, (("Rank", ("rank",)), ("Session", ("session",)),
                                           ("Type", ("category",)), ("Reason", ("reason",)),
                                           ("User", ("user",)), ("Host", ("host",)),
                                           ("Client IP", ("client_ip",)), ("Database", ("database",)),
                                           ("Application", ("application",)), ("State", ("state",)),
                                           ("Runtime seconds", ("runtime",)),
                                           ("SQL identity", ("sql_id",)),
                                           ("SQL identifier type", ("sql_id_type",)),
                                           ("MySQL connection ID", ("mysql_connection_id",)),
                                           ("Performance Schema THREAD_ID", ("performance_schema_thread_id",)),
                                           ("Transaction identity", ("transaction_id",)),
                                           ("Login time", ("login_time",)),
                                           ("Transaction age seconds", ("transaction_age",)),
                                           ("Oracle INST_ID", ("oracle_inst_id",)),
                                           ("Oracle SERIAL#", ("oracle_serial_number",)),
                                           ("Transaction start time", ("transaction_start_time",)),
                                           ("Blocked session count", ("blocked_count",)),
                                           ("Evidence timestamp", ("evidence_timestamp",)),
                                           ("Identity quality", ("identity_quality",)),
                                           ("Identity reuse note", ("identity_reuse_note",)),
                                           ("Identity recheck", ("identity_recheck",))))
    sections.append(ReportSection("priority-candidates", "Priority Review Candidates",
                                  summary="候选仅用于人工核查，不代表 Toolkit 已验证当前会话身份或建议自动终止。",
                                  tables=(priority_table,) if priority_table else ()))
    victims = _dict_rows(details.get("blocked_victims"))
    if victims:
        is_oracle = report.instance.db_type.value == "oracle"
        victim_rows = [{"session": (f"{row.get('inst_id')}:{row.get('sid')}"
                                    if is_oracle else row.get("processlist_id")),
                        "direct_blocker": row.get("direct_blocker_id"),
                        "root_blocker": row.get("root_blocker_id"),
                        "status": row.get("status"), "wait_event": row.get("event"),
                        "request_runtime": _duration_ms(row.get("duration_ms"))
                                           if row.get("duration_ms") is not None else None,
                        "wait_detail": "可用" if row.get("wait_detail_available") else "不可用"}
                       for row in victims]
        victim_columns = [("Session", ("session",)), ("Direct Blocker", ("direct_blocker",)),
                          ("Root Blocker", ("root_blocker",))]
        if is_oracle:
            victim_columns.extend([("Status", ("status",)), ("Wait Event", ("wait_event",))])
        victim_columns.extend([("Request Runtime", ("request_runtime",)),
                               ("Wait Detail", ("wait_detail",))])
        table = _table(victim_rows, tuple(victim_columns))
        sections.append(ReportSection("blocked-victims", "Blocked Victims",
                                      summary="按已采样阻塞关系单独列出受害会话，不生成终止建议。",
                                      tables=(table,) if table else ()))
    recent_section = _mysql_recent_statements_section(report, details)
    if recent_section:
        sections.append(recent_section)
    active_waits = _dict_rows(details.get("active_waits"))
    if active_waits:
        is_postgresql = report.instance.db_type.value == "postgresql"
        is_oracle = report.instance.db_type.value == "oracle"
        wait_rows = [{"session": (f"{row.get('inst_id')}:{row.get('sid')}"
                                  if is_oracle else _first(row, "pid", "processlist_id")),
                      "state": _first(row, "state", "state_name"),
                      "runtime": (row.get("duration_seconds") if is_oracle else
                                  _duration_ms(row.get("duration_ms"))),
                      "wait": _first(row, "wait_event", "wait_event_type"),
                      "sql": row.get("sql"),
                      "interpretation": row.get("request_interpretation")}
                     for row in active_waits]
        table = _table(wait_rows, (("Session", ("session",)), ("Status", ("state",)),
                                   ("Request Runtime", ("runtime",)),
                                   *(((("Wait Event", ("wait",)), ("SQL", ("sql",)))
                                      if is_postgresql or is_oracle else ())),
                                   ("Interpretation", ("interpretation",))))
        sections.append(ReportSection("active-waits", "Active Wait Statements",
                                      summary=("仅对无锁阻塞且精确匹配 pg_sleep(...) 的 PostgreSQL 查询分类；SQL 来自已脱敏采样。"
                                               if is_postgresql else
                                               "仅对无锁阻塞、完整 SQL 精确匹配 DBMS_SESSION.SLEEP(:1)，且等待事件为 PL/SQL lock timer 的请求分类。"
                                               if is_oracle else
                                               "仅对明确独立 SELECT SLEEP(...) 且状态为 User sleep 的语句分类。"),
                                      tables=(table,) if table else ()))
    if incident.root_cause_candidate:
        fields = tuple((_label(str(key)), _human(value))
                       for key, value in incident.root_cause_candidate.items())
        sections.append(ReportSection("root-cause", "Root Cause Candidate",
                                      severity=incident.severity, fields=fields))
    session_sections, _ = _session_and_request_sections(report, details, policy)
    sections.extend(session_sections)
    plan_commands = [(f"Session {_session_id(row)}",
                      f"dba diagnose {report.instance.id} plan --session {_session_id(row)}")
                     for row in _dict_rows(details.get("long_running"))
                     if type(row.get("session_id")) is int and row["session_id"] > 0]
    if report.instance.db_type.value == "sqlserver" and plan_commands:
        sections.append(ReportSection(
            "execution-plan-follow-up", "Execution Plan Follow-up",
            summary="当前 CLI 支持按会话采集执行计划。仅当目标会话仍有可用请求、权限和版本能力满足时才可成功；执行前先重新核对会话。",
            fields=tuple(plan_commands)))
    transaction = _transaction_section(details, report.instance.db_type.value, policy)
    if transaction:
        sections.append(transaction)
    return sections


def _mysql_recent_statements_section(report: DiagnosticReport,
                                     details: dict[str, Any]) -> ReportSection | None:
    recent = _dict_rows(details.get("recent_statements"))
    if report.instance.db_type.value != "mysql" or not recent:
        return None
    fields: list[tuple[str, str]] = []
    blocks: list[ReportCodeBlock] = []
    elapsed = details.get("recent_statements_elapsed_ms")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0:
        elapsed_text = "<0.1" if elapsed < 0.1 else f"{elapsed:.1f}"
    else:
        elapsed_text = "未记录"
    fields.append(("采集耗时（毫秒）", elapsed_text))
    for target in recent[:5]:
        session_id = target.get("session_id", "不可用")
        fields.append((f"连接 {session_id} 状态", str(target.get("evidence_status", "UNKNOWN"))))
        if target.get("limit_reached"):
            fields.append((f"连接 {session_id} 采集上限",
                           "已达到每连接5条记录上限；是否还有更早事件未知"))
        elif target.get("truncated"):
            fields.append((f"连接 {session_id} 采集上限", "存在未显示的历史记录"))
        if target.get("reason"):
            fields.append((f"连接 {session_id} 说明", str(target["reason"])))
        statements = target.get("statements", [])
        if not isinstance(statements, list):
            continue
        for statement in statements[:5]:
            if not isinstance(statement, dict):
                continue
            event_id = statement.get("event_id", "不可用")
            fields.extend((("连接 ID", str(statement.get("session_id", session_id))),
                           ("事件 ID", str(event_id)),
                           ("执行耗时（毫秒）", str(statement.get("duration_ms", "不可用"))),
                           ("数据来源", str(statement.get("source", "未知"))),
                           ("证据状态", str(statement.get("evidence_status", "UNKNOWN"))),
                           ("SQL 文本截断", "是" if statement.get("truncated") else "否")))
            sql = statement.get("sql_text_redacted")
            if isinstance(sql, str):
                clipped = sql[:1000]
                text_truncated = len(sql) > 1000
                title = f"连接 {session_id} 事件 {event_id}"
                if statement.get("truncated") or text_truncated:
                    title += " (truncated)"
                if text_truncated:
                    clipped += "\n[SQL text omitted by 1000 character report limit.]"
                blocks.append(ReportCodeBlock(title, clipped,
                                              language="sql", collapsed=True))
    return ReportSection(
        "mysql-recent-statements", "Recent SQL Evidence",
        summary="最近执行 SQL；不保证属于当前事务，也不保证是持锁 SQL。SQL 文本已脱敏并折叠显示。",
        fields=tuple(fields), code_blocks=tuple(blocks))


def _disk_growth_sections(details: dict[str, Any]) -> list[ReportSection]:
    sections = []
    for key, title in (("space", "Database Space"), ("log", "Transaction Log"),
                       ("other", "Other Storage Evidence")):
        value = details.get(key)
        if isinstance(value, dict):
            rows = [{"area": label, "value": _human(item)} for label, item in value.items()]
        elif isinstance(value, list):
            rows = [{"area": _human(_first(row, "area", "category", "name", "database", "file")),
                     "value": _human(_first(row, "size_mb", "size_bytes", "value", "status"))}
                    for row in _dict_rows(value)]
        else:
            rows = []
        table = _table(rows, (("Area", ("area",)), ("Value", ("value",))))
        if table:
            sections.append(ReportSection(key, title, tables=(table,)))
    context_keys = ("dominant_space_category", "history_note", "next_diagnostic_step",
                    "required_evidence_complete", "optional_evidence_status", "data_visibility",
                    "required_unavailable", "optional_unavailable", "not_applicable",
                    "note", "compatibility")
    context_rows = [{"item": _label(key), "value": _human(details[key])}
                    for key in context_keys if key in details]
    context_table = _table(context_rows, (("Item", ("item",)), ("Value", ("value",))))
    if context_table:
        sections.append(ReportSection("diagnostic-context", "Diagnostic Context",
                                      tables=(context_table,)))
    return sections


def _plan_sections(details: dict[str, Any], plan_artifact: str | None,
                   policy: HumanReportPolicy) -> list[ReportSection]:
    sections: list[ReportSection] = []
    request_value = details.get("request")
    request: dict[str, Any] = request_value if isinstance(request_value, dict) else {}
    summary_fields = []
    for label, keys in (("Session", ("session_id",)), ("Source", ("source",)),
                        ("Statement", ("statement_text", "sql_summary")),
                        ("Estimated Cost", ("estimated_total_subtree_cost", "estimated_cost")),
                        ("Estimated Rows", ("estimated_rows",)), ("Actual Rows", ("actual_rows",)),
                        ("Parallel Plan", ("parallel",)), ("Optimization Level", ("optimization_level",)),
                        ("Memory Grant", ("requested_memory_kb", "granted_memory_kb"))):
        value = _first(request, *keys)
        if value is not None:
            rendered = _human(value)
            if label == "Statement" and len(rendered) > policy.max_sql_chars:
                rendered = rendered[:policy.max_sql_chars] + " [statement omitted by report limit]"
            summary_fields.append((label, rendered))
    if details.get("plan_available") is False:
        sections.append(ReportSection(
            "plan-availability", "Execution Plan Availability", severity="WARNING",
            summary=str(details.get("note") or "Execution plan unavailable; no plan evidence was returned.")))
    if summary_fields:
        sections.append(ReportSection("plan-summary", "Execution Plan Summary",
                                      fields=tuple(summary_fields)))
    operators = _dict_rows(details.get("top_operators"))
    shown = operators[:policy.max_plan_operators]
    operator_rows = [{"operator": _first(row, "physical_op", "logical_op"),
                      "object": _first(row, "object_name", "index_name"),
                      "estimated": row.get("estimated_rows"), "actual": row.get("actual_rows"),
                      "cost": row.get("estimated_total_subtree_cost"),
                      "predicate": row.get("predicate"), "warnings": row.get("warnings"),
                      "conversions": row.get("implicit_conversions")} for row in shown]
    operator_table = _table(operator_rows, (("Operator", ("operator",)), ("Object / Index", ("object",)),
                                            ("Estimated Rows", ("estimated",)),
                                            ("Actual Rows", ("actual",)), ("Cost", ("cost",)),
                                            ("Predicate", ("predicate",)),
                                            ("Warnings", ("warnings",)),
                                            ("Implicit Conversion", ("conversions",))))
    if operators:
        sections.append(ReportSection("plan-operators", "Important Operators",
                                      summary=_omitted(len(operators), len(shown), "plan operators"),
                                      tables=(operator_table,) if operator_table else ()))
    missing = _dict_rows(details.get("missing_indexes"))
    warning_rows = []
    for row in missing:
        warning_rows.append({"type": "Missing Index Evidence",
                             "target": ".".join(str(row.get(key)) for key in ("database", "schema", "table")
                                                if row.get(key)),
                             "details": f"Equality: {_human(row.get('equality_columns'))}; "
                                        f"Inequality: {_human(row.get('inequality_columns'))}; "
                                        f"Include: {_human(row.get('include_columns'))}; "
                                        f"Impact: {_human(row.get('impact'))}"})
    for operator in operators:
        for warning in operator.get("warnings", []) if isinstance(operator.get("warnings"), list) else []:
            if isinstance(warning, dict):
                warning_rows.append({"type": warning.get("name", "Plan Warning"),
                             "target": _human(operator.get("object_name")),
                                     "details": _human(warning.get("evidence"))})
        for conversion in operator.get("implicit_conversions", []) \
                if isinstance(operator.get("implicit_conversions"), list) else []:
            warning_rows.append({"type": "Implicit Conversion", "target": _human(operator.get("object_name")),
                                 "details": _human(conversion)})
    if warning_rows:
        visible_warnings = warning_rows[:policy.max_plan_operators]
        table = _table(visible_warnings, (("Type", ("type",)), ("Target", ("target",)),
                                          ("Details", ("details",))))
        sections.append(ReportSection("plan-warnings", "Warnings and Index Evidence",
                                      summary=_omitted(len(warning_rows), len(visible_warnings),
                                                       "plan warnings"),
                                      tables=(table,) if table else ()))
    if plan_artifact:
        sections.append(ReportSection("plan-artifact", "Raw Execution Plan",
                                      summary="Open the SQLPLAN artifact with SQL Server Management Studio.",
                                      fields=(("Artifact", plan_artifact),)))
    return sections


def _replication_sections(report: DiagnosticReport, policy: HumanReportPolicy) -> list[ReportSection]:
    summary = getattr(report, "summary", None)
    members = list(getattr(report, "members", ()))
    lags = list(getattr(report, "lag", ()))
    backlog = getattr(report, "backlog", {})
    issues = list(getattr(report, "issues", ()))
    actions = list(getattr(report, "recommendations", ()))
    summary_fields = tuple((label, _human(value)) for label, value in (
        ("Snapshot Time", getattr(summary, "snapshot_time", None)),
        ("Database", getattr(summary, "database_id", None)),
        ("Platform", getattr(summary, "platform", None)),
        ("Version", getattr(summary, "version", None)),
        ("Local Role", getattr(summary, "local_role", None)),
        ("Vendor Role", getattr(summary, "vendor_role", None)),
        ("Replication Mode", getattr(summary, "replication_mode", None)),
        ("Overall", getattr(summary, "overall", None)),
        ("Status", getattr(summary, "status", None)),
        ("Data Completeness", getattr(summary, "data_completeness", None)),
        ("Transport", getattr(summary, "transport_status", None)),
        ("Apply / Replay", getattr(summary, "apply_status", None)),
        ("Toolkit Executed", "NO"),
    ))
    sections = [ReportSection("replication-summary", "Replication Summary", fields=summary_fields)]
    permission = getattr(report, "details", {}).get("permission_readiness", {})
    if permission:
        permission_fields = tuple((label, _human(permission.get(key))) for key, label in (
            ("status", "Permission Status"), ("missing_required", "Missing Required"),
            ("missing_optional", "Missing Optional"), ("impact", "Impact"),
            ("toolkit_executed", "Toolkit Executed"),
            ("dba_approval_required", "DBA Approval Required")))
        grant = permission.get("suggested_grant")
        sections.append(ReportSection("replication-permissions", "Permission Readiness",
                                      fields=permission_fields,
                                      code_blocks=(ReportCodeBlock("Reference Grant (manual review only)",
                                                                   str(grant), "sql", True),)
                                      if grant else ()))
    visible_members = members[:policy.max_replication_rows]
    member_rows = [{"name": item.name, "local": _human(item.local), "role": item.role,
                    "vendor_role": item.vendor_role or "—", "status": item.status,
                    "transport": item.transport_status or "—", "apply": item.apply_status or "—"}
                   for item in visible_members]
    member_table = _table(member_rows, (("Member", ("name",)), ("Local", ("local",)),
                                       ("Role", ("role",)), ("Vendor Role", ("vendor_role",)),
                                       ("Status", ("status",)), ("Transport", ("transport",)),
                                       ("Apply / Replay", ("apply",))))
    sections.append(ReportSection("replication-topology", "Topology", summary=_omitted(
        len(members), len(visible_members), "members"), tables=(member_table,) if member_table else ()))
    visible_lags = lags[:policy.max_replication_rows]
    lag_rows = [{"kind": item.kind, "value": _human(item.value), "unit": item.unit,
                 "severity": item.severity, "source": item.source, "note": item.note or "—"}
                for item in visible_lags]
    lag_table = _table(lag_rows, (("Metric", ("kind",)), ("Value", ("value",)),
                                 ("Unit", ("unit",)), ("Severity", ("severity",)),
                                 ("Source", ("source",)), ("Interpretation", ("note",))))
    sections.append(ReportSection("replication-lag", "Transport, Apply / Replay and Lag",
                                  summary=("Not applicable because replication is not configured."
                                           if getattr(getattr(report, "summary", None), "status", None)
                                           == "NOT_CONFIGURED" and not lags
                                           else _omitted(len(lags), len(visible_lags), "lag metrics")),
                                  tables=(lag_table,) if lag_table else ()))
    backlog_rows = [{"item": key, "observed": _human(value)} for key, value in list(backlog.items())[:policy.max_replication_rows]]
    backlog_table = _table(backlog_rows, (("Backlog", ("item",)), ("Observed", ("observed",))))
    sections.append(ReportSection("replication-backlog", "Observed Backlog",
                                  summary=_omitted(len(backlog), len(backlog_rows), "backlog metrics"),
                                  tables=(backlog_table,) if backlog_table else ()))
    visible_issues = issues[:policy.max_replication_rows]
    issue_rows = [{"code": item.code, "severity": item.severity,
                   "candidate": "Candidate" if item.candidate else "Evidence",
                   "message": item.message,
                   "vendor_message": item.vendor_message or "—"} for item in visible_issues]
    issue_table = _table(issue_rows, (("Issue", ("code",)), ("Severity", ("severity",)),
                                      ("Classification", ("candidate",)),
                                      ("Finding", ("message",)), ("Vendor Error", ("vendor_message",))))
    sections.append(ReportSection("replication-issues", "Issues and Vendor Evidence",
                                  summary=_omitted(len(issues), len(visible_issues), "issues"),
                                  tables=(issue_table,) if issue_table else ()))
    actions_rows = [{"recommendation": item.message,
                     "approval": "DBA review required" if item.requires_dba_review else "No"}
                    for item in actions[:policy.max_replication_rows]]
    actions_table = _table(actions_rows, (("Recommended DBA Action", ("recommendation",)),
                                         ("Review", ("approval",))))
    sections.append(ReportSection("replication-actions", "Recommended Actions",
                                  summary="Toolkit Executed: NO. No automatic failover, promotion, restart, reset, or repair.",
                                  tables=(actions_table,) if actions_table else ()))
    return sections


def build_report_model(report: DiagnosticReport, incident: IncidentSummary,
                       policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
                       plan_artifact: str | None = None) -> ReportModel:
    """Create a bounded presentation model; plan XML is never embedded in HTML."""
    instance = report.instance
    details = getattr(report, "details", {})
    metadata: tuple[tuple[str, str], ...] = (("Database ID", instance.id), ("Instance", instance.name),
                ("Database Type", instance.db_type.value),
                ("Version", _human(details.get("version", "UNKNOWN"))),
                ("Scenario", _label(report.scenario)), ("Generated At", _local_time(report.diagnosed_at)),
                ("Environment", instance.environment or "UNKNOWN"),
                ("Data Completeness", incident.data_completeness),
                ("Capture Started", str(details.get("capture_started_at", "N/A"))),
                ("Triage Completed", str(details.get("triage_completed_at", "N/A"))),
                ("Capture Finished", str(details.get("capture_finished_at", "N/A"))),
                ("Snapshot Consistency", str(details.get("snapshot_consistency", "BEST_EFFORT"))))
    if report.scenario == "slow":
        sections = _slow_sections(report, incident, details, policy)
    elif report.scenario == "plan":
        sections = [_recovery_section(incident, details, report.scenario, instance.db_type.value, report),
                    *_plan_sections(details, plan_artifact, policy)]
    elif report.scenario == "replication":
        sections = _replication_sections(report, policy)
    else:
        sections = _disk_growth_sections(details)
        sections.insert(0, _recovery_section(incident, details, report.scenario,
                                             instance.db_type.value, report))
    findings = _finding_section(report)
    if findings:
        sections.append(findings)
    sections.append(_quality_section(report, incident, details))
    titles = {
        "Immediate Recovery": "人工处置参考", "Root Cause Candidate": "现场诊断结论",
        "Root Blockers": "根阻塞", "Blocking Analysis": "锁与阻塞链",
        "Session Identity": "会话身份与来源", "Request Runtime": "请求运行信息",
        "Workload Summary": "当前数据库状态", "Wait Analysis": "等待分析",
        "Appendix — Other Sessions": "其他会话（附录）", "Transaction Analysis": "长期未提交事务",
        "Execution Plan Follow-up": "执行计划后续检查", "Collection Quality": "证据完整性",
        "Findings and Recommendations": "诊断发现与建议", "Database Space": "数据文件与表空间",
        "Transaction Log": "事务日志与保留证据", "Other Storage Evidence": "空间与日志证据",
        "Diagnostic Context": "未获取的证据与诊断上下文", "Priority Review Candidates": "优先核查对象",
        "Blocked Victims": "被阻塞会话", "Active Wait Statements": "主动等待语句",
        "Transaction Identity Quality": "事务身份质量",
    }
    headers = {"Rank": "排序", "Session": "会话", "Type": "类型", "Reason": "入选原因",
               "User": "用户", "Host": "主机", "Client IP": "客户端 IP", "Database": "数据库",
               "Application": "应用", "State": "状态",
               "Runtime seconds": "运行秒数", "Transaction age seconds": "事务时长秒",
               "SQL identity": "SQL 标识", "Transaction identity": "事务标识", "Login time": "登录时间",
               "Blocked session count": "影响会话数", "Evidence timestamp": "证据时间",
               "Identity quality": "身份信息质量", "Identity reuse note": "会话 ID 复用提示",
               "MySQL connection ID": "MySQL 连接 ID",
               "Performance Schema THREAD_ID": "Performance Schema THREAD_ID",
               "Request Runtime": "当前请求时长", "Wait Detail": "等待详情",
               "Direct Blocker": "直接阻塞者", "Root Blocker": "根阻塞者",
               "Interpretation": "说明",
               "Transaction Identity Quality": "事务身份质量",
               "Identity recheck": "身份复核", "Finding": "发现", "Severity": "严重程度",
               "Recommendation": "建议", "Database ID": "数据库 ID", "Instance": "实例",
               "Database Type": "数据库类型", "Version": "版本", "Scenario": "诊断场景",
               "Generated At": "报告生成时间", "Environment": "环境", "Data Completeness": "证据完整性",
               "Capture Started": "采集开始", "Triage Completed": "初报完成", "Capture Finished": "采集结束",
               "Snapshot Consistency": "快照一致性", "Area": "资源类别", "Value": "观测值",
               "Item": "条目", "Observed": "观测值"}
    sections = [replace(section, title=titles.get(section.title, section.title),
                        tables=tuple(ReportTable(tuple(headers.get(h, h) for h in table.headers), table.rows)
                                     for table in section.tables)) for section in sections]
    metadata = tuple((headers.get(key, key), value) for key, value in metadata)
    return ReportModel("数据库应急诊断报告", metadata, incident, tuple(sections))


def _map_section(section: ReportSection, *, max_chars: int, drop_sql: bool = False) -> ReportSection:
    blocks = []
    for block in section.code_blocks:
        if drop_sql and block.language.lower() == "sql":
            continue
        content = block.content
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n[{len(content) - max_chars} characters omitted by report limit.]"
        blocks.append(replace(block, content=content))
    fields = tuple((label, value[:max_chars] + f" [{len(value) - max_chars} characters omitted]"
                    if len(value) > max_chars else value)
                   for label, value in section.fields)
    tables = tuple(ReportTable(table.headers, tuple(
        tuple(value[:max_chars] + f" [{len(value) - max_chars} characters omitted]"
              if len(value) > max_chars else value for value in row)
        for row in table.rows)) for table in section.tables)
    return replace(section, title=section.title[:max_chars], summary=section.summary[:max_chars],
                   severity=section.severity[:max_chars], fields=fields, tables=tables,
                   code_blocks=tuple(blocks))


def _bounded_incident(incident: IncidentSummary, max_chars: int) -> IncidentSummary:
    return replace(
        incident, snapshot_time=incident.snapshot_time[:max_chars],
        data_completeness=incident.data_completeness[:max_chars],
        status=incident.status[:max_chars], severity=incident.severity[:max_chars],
        primary_problem=incident.primary_problem[:max_chars], observed_now=incident.observed_now[:max_chars],
        timing={key[:max_chars]: value[:max_chars] for key, value in incident.timing.items()},
        wait=({key[:max_chars]: value[:max_chars] for key, value in incident.wait.items()}
              if incident.wait else None))


def _dropped_evidence(sections: tuple[ReportSection, ...], dropped_ids: set[str]) -> str:
    details = []
    for section in sections:
        if section.section_id not in dropped_ids:
            continue
        displayed = sum(len(table.rows) for table in section.tables)
        match = re.search(r"Total (\d+) · Displayed (\d+) · Omitted (\d+)", section.summary)
        total = int(match.group(1)) if match else displayed
        blocks = len(section.code_blocks)
        details.append(f"{section.title}: total {total}, displayed 0, omitted {total} rows, "
                       f"{blocks} text blocks omitted")
    return "; ".join(details) or "No complete section was omitted."


def render_bounded(model: ReportModel, render: Callable[[ReportModel], str],
                   policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY) -> str:
    """Apply byte budgets outside the renderer while preserving valid HTML."""
    page = render(model)
    if len(page.encode("utf-8")) <= policy.soft_report_bytes:
        return page
    soft_drop = {"appendix", "nonblocked-long-running", "wait-analysis"}
    sections = tuple(_map_section(section, max_chars=max(256, policy.max_sql_chars // 2), drop_sql=True)
                     for section in model.sections if section.section_id not in soft_drop)
    notice = ReportSection("report-budget", "Report Evidence Limit",
                           summary="Report evidence was reduced to stay within the human report size budget. "
                                   + _dropped_evidence(model.sections, soft_drop)
                                   + " See --json for structured diagnostic data.")
    compact = replace(model, sections=(sections[0], notice, *sections[1:]) if sections else (notice,))
    page = render(compact)
    if len(page.encode("utf-8")) <= policy.hard_report_bytes:
        return page

    critical_ids = {"immediate-recovery", "root-cause", "root-blockers", "blocking",
                    "collection-quality", "missing-evidence", "report-budget",
                    "replication-summary", "replication-issues"}
    hard_sections = tuple(_map_section(section, max_chars=1024, drop_sql=True)
                          for section in compact.sections if section.section_id in critical_ids)
    hard_dropped = {section.section_id for section in compact.sections
                    if section.section_id not in critical_ids}
    hard_notice = ReportSection("report-budget", "Report Evidence Limit",
                                summary="Report evidence was reduced because the human report size limit "
                                        "was reached. " + _dropped_evidence(compact.sections, hard_dropped)
                                        + " Use --json for structured diagnostic data.")
    hard = replace(compact, sections=tuple(section for section in hard_sections
                                           if section.section_id != "report-budget") + (hard_notice,))
    page = render(hard)
    if len(page.encode("utf-8")) <= policy.hard_report_bytes:
        return page

    # Defensive final bound for unusually large database-derived labels/summary values.
    char_limit = 4096
    while char_limit > 16:
        bounded = replace(hard, title=hard.title[:char_limit],
                          metadata=tuple((key[:char_limit], value[:char_limit])
                                         for key, value in hard.metadata),
                          incident_summary=_bounded_incident(hard.incident_summary, char_limit),
                          sections=tuple(_map_section(section, max_chars=char_limit, drop_sql=True)
                                         for section in hard.sections))
        page = render(bounded)
        if len(page.encode("utf-8")) <= policy.hard_report_bytes:
            return page
        char_limit //= 2
    emergency = replace(hard, title="DBA Toolkit Diagnostic Report", metadata=(),
                        incident_summary=_bounded_incident(hard.incident_summary, 64),
                        sections=tuple(_map_section(section, max_chars=64, drop_sql=True)
                                       for section in hard.sections
                                       if section.section_id in {"immediate-recovery", "root-cause",
                                                                 "root-blockers", "blocking",
                                                                 "collection-quality", "report-budget"}))
    return render(emergency)
