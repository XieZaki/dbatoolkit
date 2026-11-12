"""Bounded, human-oriented diagnostic report model and evidence presenters."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from math import isfinite
from typing import Any, Callable

from dba.active_sql_report import active_sql_sections
from dba.core import DiagnosticReport, Finding
from dba.incident import IncidentSummary
from dba.logging import redact
from dba.memory_grant_report import memory_grant_sections
from dba.reporting.model import (
    DEFAULT_HUMAN_REPORT_POLICY,
    HumanReportPolicy,
    ReportCodeBlock,
    ReportModel,
    ReportSection,
    ReportTable,
)
from dba.reporting.normalizers import (
    display_finding,
    display_label,
    display_message,
    display_quality_impact,
    display_report_summary,
    display_report_value,
    display_request_status,
    display_slow_collector,
    display_slow_label,
    display_slow_value,
    display_slow_wait,
    postgresql_server_version_num,
    postgresql_version_label,
    report_model_label,
    report_model_value,
    sqlserver_transaction_label,
)


def _local_time(value: Any) -> str:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            return value.isoformat(sep=" ", timespec="seconds") + " (时区未知)"
        moment = value.astimezone()
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
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
        return str(report_model_value(value))
    if isinstance(value, datetime):
        return _local_time(value)
    if isinstance(value, dict):
        return "; ".join(f"{_label(str(key))}: {_human(item)}" for key, item in value.items())
    if isinstance(value, (tuple, list, set)):
        return ", ".join(_human(item) for item in value) if value else "—"
    if isinstance(value, str):
        value = report_model_value(value)
    return str(redact(value))


def _label(value: str) -> str:
    return report_model_label(value)


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
                 , row_states: tuple[str, ...] = ()) -> ReportTable | None:
    if not rows:
        return None
    visible = [(label, keys, formatter) for label, keys, formatter in columns
               if any(_first(row, *keys) not in (None, "") for row in rows)]
    if not visible:
        return None
    return ReportTable(tuple(label for label, _, _ in visible), tuple(
        tuple(formatter(_first(row, *keys)) for _, keys, formatter in visible) for row in rows),
        row_states=row_states)


def _table(rows: list[dict[str, Any]], columns: tuple[tuple[str, tuple[str, ...]], ...]
           , row_states: tuple[str, ...] = ()) -> ReportTable | None:
    return _fixed_table(rows, tuple((label, keys, _human) for label, keys in columns), row_states)


def _omitted(total: int, displayed: int, noun: str) -> str:
    omitted = max(0, total - displayed)
    text = f"共{total}条，显示{displayed}条，省略{omitted}条。"
    if omitted:
        text += f" 人工报告省略其余{noun}；结构化数据请使用 --json。"
    return text


def _top_sql_section(value: Any) -> ReportSection | None:
    """Present the standalone cumulative Top SQL snapshot."""
    payload = _top_sql_payload(value)
    if payload is None:
        return None
    raw_items: list[Any] = payload.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    metadata = ("collection_status", "completeness", "data_visibility",
                "database_engine", "database_version", "profile_id", "collection_timestamp",
                "ranking_metric", "ranking_direction", "metric_name", "metric_unit",
                "metric_semantics", "metric_scope", "collection_scope", "evidence_source", "limitations",
                "source_metadata", "returned_records")
    fields_list: list[tuple[str, str]] = []
    for key in metadata:
        if key == "returned_records":
            fields_list.append((_label(key), str(len(raw_items))))
        elif payload.get(key) is not None:
            field_value = payload.get(key)
            if key == "database_engine" and isinstance(field_value, str):
                field_value = {"mysql": "MySQL", "postgresql": "PostgreSQL",
                               "sqlserver": "SQL Server", "oracle": "Oracle"}.get(
                                   field_value.lower(), field_value)
            elif key == "database_version" and payload.get("database_engine") == "postgresql":
                field_value = postgresql_version_label(field_value)
            fields_list.append((_label(key), _human(field_value)[:256]))
    fields = tuple(fields_list)
    rows: list[tuple[str, ...]] = []
    for item in raw_items[:10]:
        if not isinstance(item, dict) or item.get("rank") is None:
            continue
        identity = item.get("sql_identity")
        database_name = item.get("database_name")
        identity_fields = identity.get("fields", {}) if isinstance(identity, dict) else {}
        if not isinstance(database_name, str) or not database_name.strip():
            # MySQL's schema_name is its collected database name; numeric DBIDs
            # and other native statement identifiers are never name fallbacks.
            schema_name = (identity_fields.get("schema_name")
                           if isinstance(identity_fields, dict)
                           and isinstance(identity, dict)
                           and identity.get("database_engine") == "mysql" else None)
            database_name = (
                schema_name if isinstance(schema_name, str) and schema_name.strip()
                else "名称未采集"
            )
        rows.append((str(item["rank"]), database_name[:180], _human(item.get("metric_value"))))
    headers = ("Rank", "Database Name", "Metric value")
    table = ReportTable(headers, tuple(rows)) if rows else None
    if payload.get("collection_status") == "COLLECTED" and not raw_items:
        summary = "已完成采集，但没有匹配记录；不生成虚假排名。"
    elif not rows:
        summary = "当前没有可用的已排名 Top SQL 记录；状态和限制见契约字段。"
    else:
        summary = _omitted(len(raw_items), len(rows), "Top SQL rows")
    return ReportSection("top-sql", "Top SQL", summary=summary, fields=fields,
                         tables=(table,) if table else ())


def _top_sql_payload(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return value if isinstance(value, dict) else None


def render_report_section_text(section: ReportSection) -> str:
    """Render one already-bounded ReportModel section for the CLI console."""
    lines = [display_label(section.title)]
    if section.summary:
        lines.append(display_report_summary(section.summary))
    lines.extend(f"{display_label(label)}: {display_report_value(label, value)}"
                 for label, value in section.fields)
    for table in section.tables:
        lines.append(" | ".join(display_label(header) for header in table.headers))
        for row in table.rows:
            lines.append(" | ".join(
                display_quality_impact(value)
                if section.section_id == "collection-quality"
                and index < len(table.headers) and table.headers[index] == "Impact" else
                display_report_value(table.headers[index], value)
                if index < len(table.headers) else value
                for index, value in enumerate(row)))
    for block in section.code_blocks:
        lines.extend((display_label(block.title), block.content))
    return "\n".join(lines)


def _sql_block(row: dict[str, Any], policy: HumanReportPolicy, label: str = "SQL") -> ReportCodeBlock | None:
    sql = (row.get("statement_sql_text") if "statement_sql_text" in row else
           _first(row, "sql_text", "query", "sql_fulltext", "current_sql", "previous_sql"))
    if not isinstance(sql, str) or not sql:
        return None
    clipped = sql[:policy.max_sql_chars]
    truncated_key = ("statement_sql_text_truncated" if "statement_sql_text" in row else
                     "sql_text_truncated")
    is_truncated = clipped != sql or bool(_first(row, truncated_key, "query_truncated"))
    title = f"会话 {_session_id(row) or '未知'} {label}"
    if is_truncated:
        title += "（已截断）"
        clipped += "\n[SQL 文本因人工报告长度上限而省略。]" if len(sql) > policy.max_sql_chars else ""
    return ReportCodeBlock(title, str(redact(clipped)), "sql", True)


def _current_request_sections(
    report: DiagnosticReport, details: dict[str, Any], requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]], policy: HumanReportPolicy,
) -> list[ReportSection]:
    """Present each active request with its exact session source and request evidence."""
    database_type = report.instance.db_type.value
    session_rows: dict[str, list[dict[str, Any]]] = {}
    for row in sessions:
        sid = _session_id(row)
        if sid is not None:
            session_rows.setdefault(sid, []).append(row)
    ambiguous = {sid for sid, rows in session_rows.items() if len(rows) != 1}
    session_by_id = {sid: rows[0] for sid, rows in session_rows.items() if len(rows) == 1}

    samples = _dict_rows(details.get("samples"))
    names = (("active_sessions", "long_running") if database_type == "postgresql" else
             ("active_requests",))
    matching_samples = [row for row in samples if row.get("collector") in names]
    sample = next((row for row in matching_samples if row.get("success") is True),
                  matching_samples[0] if matching_samples else None)
    if requests:
        summary = (f"当前采集到 {len(requests)} 条活动请求。"
                   "各采集器为顺序采样，不构成原子快照。")
    elif sample is not None and sample.get("success") is False:
        summary = "活动请求采集失败；本次不能据此判断实例没有活动 SQL。"
    elif sample is not None and sample.get("success") is True and not sample.get("truncated"):
        summary = "本次采样没有活动请求。"
    else:
        summary = "没有活动请求行，且采集状态未知；不能据此判断实例没有活动 SQL。"

    visible = requests[:policy.max_related_sessions]
    summary += _omitted(len(requests), len(visible), "active requests")
    sections = [ReportSection("request-runtime", "当前活动 SQL（请求运行信息）", summary=summary)]

    def missing_reason(sid: str | None, label: str) -> str:
        if sid is None:
            return "会话 ID 未采集，无法关联会话来源"
        if sid in ambiguous:
            return "会话快照中的会话 ID 不唯一，未关联身份"
        if sid not in session_by_id:
            return "没有找到相同会话 ID 的会话采样"
        return f"{label} 在会话采样中为空或不可见"

    def field(
        request: dict[str, Any], session: dict[str, Any], sid: str | None,
        label: str, keys: tuple[str, ...], *, required: bool = False,
        reason: str | None = None, request_only: bool = False,
    ) -> tuple[str, str] | None:
        value = _first(request, *keys)
        if value in (None, "") and sid not in ambiguous and not request_only:
            value = _first(session, *keys)
        if value in (None, ""):
            if not required:
                return None
            return label, f"未采集（原因：{reason or missing_reason(sid, label)}）"
        return label, _human(value)

    source_fields: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
        "sqlserver": (
            ("登录账号", ("login_name",)), ("原始登录账号", ("original_login_name",)),
            ("Windows 用户", ("nt_user_name",)), ("客户端主机", ("host_name",)),
            ("客户端 IP", ("client_net_address",)), ("应用程序", ("program_name",)),
            ("客户端接口", ("client_interface_name",)), ("客户端进程 ID", ("host_process_id",)),
        ),
        "mysql": (("登录账号", ("user_name", "processlist_user")),
                  ("客户端主机", ("host_raw", "processlist_host", "host_name")),
                  ("应用程序", ("application_name", "program_name"))),
        "postgresql": (("登录账号", ("usename", "user_name")),
                       ("客户端主机", ("client_hostname",)),
                       ("客户端地址", ("client_addr",)),
                       ("应用程序", ("application_name",))),
        "oracle": (("登录账号", ("username", "user_name")), ("操作系统用户", ("osuser",)),
                   ("客户端主机", ("machine",)), ("应用模块", ("module",)),
                   ("客户端程序", ("program",)), ("客户端标识", ("client_identifier",)),
                   ("会话序列号", ("serial_number", "serial_num", "serial#"))),
    }
    for index, request in enumerate(visible, start=1):
        sid = _session_id(request)
        session = session_by_id.get(sid or "", {})
        fields: list[tuple[str, str]] = []
        detail_fields: list[tuple[str, str]] = []
        db = field(request, session, sid, "数据库", ("database_name", "datname", "processlist_db"),
                   required=True,
                   reason="请求数据库名称不可见；未使用 database_id 代替数据库名称",
                   request_only=database_type == "sqlserver")
        if db is not None:
            fields.append(db)
        fields.append(("会话 ID", sid or "未采集（原因：当前请求没有会话标识）"))
        request_id = _first(request, "request_id", "request")
        if request_id is not None:
            fields.append(("请求 ID", _human(request_id)))
        status = _first(request, "status", "session_status", "state", "state_name")
        if status in (None, ""):
            status_display = "未采集（原因：当前请求没有返回状态）"
        else:
            status_display = str(status) if database_type == "sqlserver" else display_slow_label(_human(status))
        fields.append(("执行状态", status_display))
        started = (_first(request, "start_time", "query_start", "sql_exec_start") or
                   (_first(session, "start_time", "query_start", "sql_exec_start")
                    if sid not in ambiguous else None))
        if started is not None:
            detail_fields.append(("请求开始时间", _local_time(started)))
        elapsed_ms = _first(request, "elapsed_ms", "duration_ms")
        elapsed_seconds = request.get("duration_seconds")
        if type(elapsed_ms) in (int, float) and elapsed_ms >= 0:
            fields.append(("已执行时间", f"{_duration_ms(elapsed_ms)}（{_human(elapsed_ms)} 毫秒）"))
        elif isinstance(elapsed_seconds, (int, float)) and not isinstance(elapsed_seconds, bool) \
                and elapsed_seconds >= 0:
            scope = "活动调用时长，" if request.get("duration_source") == "LAST_CALL_ET_ACTIVE" else ""
            fields.append(("运行时长估计" if database_type == "oracle" else "已执行时间",
                           f"{_duration_seconds(elapsed_seconds)}（{scope}{_human(elapsed_seconds)} 秒）"))
        else:
            fields.append(("已执行时间", "未采集（原因：当前请求没有返回耗时）"))

        for label, keys in source_fields.get(database_type, ()):
            primary = label == "登录账号"
            item = field(request, session, sid, label, keys, required=primary)
            if item is not None:
                (fields if primary else detail_fields).append(item)

        if database_type == "oracle":
            item = field(request, session, sid, "SQL_ID", ("sql_id",))
            if item is not None:
                fields.append(item)
        has_sqlserver_offsets = (database_type == "sqlserver" and
                                 any(key in request for key in
                                     ("statement_start_offset", "statement_end_offset")))
        sqlserver_statement_missing = (has_sqlserver_offsets and "statement_sql_text" not in request)
        sql_text = (request.get("statement_sql_text") if "statement_sql_text" in request else
                    None if sqlserver_statement_missing else
                    _first(request, "sql_text", "query", "sql_fulltext", "current_sql"))
        if isinstance(sql_text, str) and sql_text:
            sql_truncated = bool(request.get(
                "statement_sql_text_truncated" if "statement_sql_text" in request else
                "sql_text_truncated")) or len(sql_text) > policy.max_sql_chars
            if sql_truncated:
                fields.append(("SQL 文本", "当前语句文本已截断。"))
        else:
            offset_reason = request.get("statement_sql_text_unavailable_reason")
            if isinstance(offset_reason, str) and "offset" in offset_reason.lower():
                reason = "当前语句偏移不可用或无效，无法确认正在执行的语句"
            elif isinstance(offset_reason, str) and "no text" in offset_reason.lower():
                reason = "采样偏移位置没有可用 SQL 文本"
            elif sqlserver_statement_missing:
                reason = "采集器未返回按当前语句偏移提取的 SQL 文本"
            else:
                reason = "采集器未返回当前语句文本"
            fields.append(("SQL 文本", f"未采集（原因：{reason}）"))

        sql_row = dict(request)
        if "statement_sql_text" in request:
            sql_row["sql_text"] = request.get("statement_sql_text")
            sql_row["sql_text_truncated"] = request.get("statement_sql_text_truncated")
        elif sqlserver_statement_missing:
            sql_row["statement_sql_text"] = None
        block = _sql_block(sql_row, policy, "当前执行语句")
        if block is not None:
            suffix = "（已截断）" if "已截断" in block.title else ""
            block = replace(block, title=f"会话 {sid or '未知'} / 请求 "
                            f"{request_id if request_id is not None else '未知'} · 当前执行语句{suffix}",
                            collapsed=False)
        title = f"会话 {sid or '未知'}"
        if request_id is not None:
            title += f" / 请求 {request_id}"
        if database_type == "oracle" and request.get("sql_id"):
            title += f" / SQL_ID {request['sql_id']}"
        sections.append(ReportSection(
            f"current-request-{index:03d}", title,
            fields=tuple(fields), detail_fields=tuple(detail_fields),
            code_blocks=(block,) if block is not None else (), collapsed=index > 3))
    return sections


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


def _collector_complete(details: dict[str, Any], collector_name: str) -> bool:
    if collector_name in details.get("required_unavailable", []) or \
            collector_name in details.get("optional_unavailable", []):
        return False
    samples = details.get("samples", [])
    if not isinstance(samples, list):
        return False
    matches = [sample for sample in samples if isinstance(sample, dict) and
               str(sample.get("collector", sample.get("source", ""))) == collector_name]
    return bool(matches) and all(sample.get("success", True) and not sample.get("truncated", False)
                                 for sample in matches)


def _blocking_state(report: DiagnosticReport, details: dict[str, Any]) -> tuple[bool, set[str]]:
    """Return whether a clear state can be asserted and IDs proven in the blocking graph."""
    blocking_collector = "active_requests" if report.instance.db_type.value == "sqlserver" else "blocking"
    complete = (_collector_complete(details, blocking_collector) and
                _collector_complete(details, "active_sessions"))
    graph_value = details.get("blocking_graph")
    if not isinstance(graph_value, dict):
        return False, set()
    roots = graph_value.get("roots")
    children = graph_value.get("children")
    anomalies = graph_value.get("anomalies", [])
    unresolved = graph_value.get("unresolved", [])
    if (not isinstance(roots, list) or not isinstance(children, dict) or anomalies or unresolved or
            details.get("blocking_graph_reliable") is False):
        complete = False
        roots = roots if isinstance(roots, list) else []
        children = children if isinstance(children, dict) else {}
    blocked = {str(root) for root in roots}
    for parent, descendants in children.items():
        if isinstance(descendants, list):
            blocked.add(str(parent))
            blocked.update(str(child) for child in descendants)
    return complete, blocked


def _long_running_rows(report: DiagnosticReport, details: dict[str, Any]) -> list[dict[str, Any]]:
    configured = _dict_rows(details.get("long_running"))
    sqlserver = report.instance.db_type.value == "sqlserver"
    if sqlserver:
        configured = [row for row in configured if row.get("request_kind") != "ACTIVE_WAIT"]
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
        if sqlserver and row.get("request_kind") == "ACTIVE_WAIT":
            continue
        elapsed = _first(row, "elapsed_ms", "duration_ms")
        duration_seconds = row.get("duration_seconds")
        if elapsed is None and isinstance(duration_seconds, (int, float)) \
                and not isinstance(duration_seconds, bool):
            elapsed = duration_seconds * 1000
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) \
                and elapsed >= float(threshold) * 1000:
            long_rows.append(row)
    return long_rows or configured


def _active_request_rows(report: DiagnosticReport, details: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _dict_rows(details.get("all_requests", details.get("active_requests")))
    if report.instance.db_type.value == "postgresql":
        shown = {_session_id(row) for row in rows}
        rows.extend({**row, "query": row.get("sql"), "request_interpretation": "已识别独立主动等待。"}
                    for row in _dict_rows(details.get("active_waits"))
                    if _session_id(row) not in shown)
    return rows


def _blocking_sections(
    details: dict[str, Any], roots: list[Any], children: dict[str, list[Any]],
    sessions: dict[str, dict[str, Any]], requests: list[dict[str, Any]],
    complete: bool, policy: HumanReportPolicy, *, sqlserver: bool,
) -> tuple[list[ReportSection], set[str]]:
    edges = [(parent, str(victim)) for parent, victims in children.items() for victim in victims]
    parents: dict[str, list[str]] = {}
    for parent, victim in edges:
        parents.setdefault(victim, []).append(parent)
    root_ids = {str(root) for root in roots}
    related = root_ids | set(children) | set(parents)
    requests_by_id: dict[str, list[dict[str, Any]]] = {}
    anchors: dict[int, str] = {}
    for index, row in enumerate(requests, 1):
        if (request_sid := _session_id(row)) is not None:
            requests_by_id.setdefault(request_sid, []).append(row)
            if index <= policy.max_related_sessions:
                anchors[id(row)] = f"#current-request-{index:03d}"
    rows: list[dict[str, Any]] = []
    visited: set[str] = set()
    for start in [*(str(root) for root in roots[:policy.max_root_blockers]),
                  *(sorted(related - root_ids) if not complete else ())]:
        pending = deque([(start, 0)])
        branch_rows = 0
        while pending and len(rows) < policy.max_root_blockers * policy.max_blocking_nodes_per_root:
            sid, depth = pending.popleft()
            if sid in visited:
                continue
            visited.add(sid)
            pending.extend((str(child), depth + 1) for child in children.get(sid, []))
            if branch_rows >= policy.max_blocking_nodes_per_root:
                continue
            branch_rows += 1
            candidates = requests_by_id.get(sid, [])
            request = candidates[0] if len(candidates) == 1 else {}
            if sqlserver and sid in parents:
                victim_requests = [row for row in candidates
                                   if str(row.get("blocking_session_id")) in parents[sid]]
                request = max(victim_requests, key=lambda row: (
                    str(row.get("wait_type") or "").startswith("LCK_M_"),
                    _first(row, "wait_time_ms", "wait_ms") or 0), default={})
            anchor = anchors.get(id(request))
            rows.append({"session": f"{'  ' * depth}{'└─ ' if depth else ''}{sid}", "anchor": anchor,
                         "role": "Root Blocker" if sid in root_ids else "Blocked" if sid in parents else "上游会话待核验",
                         "parent": "、".join(parents.get(sid, [])) or "—", "depth": depth,
                         "status": (_first(request, "status", "state", "session_status", "state_name") or
                                    _first(sessions.get(sid, {}), "status", "state", "session_status", "state_name")),
                         "sql": "查看当前活动 SQL" if anchor else "无可定位的当前请求"})
    table = _table(rows, (("Session", ("session",)), ("Role", ("role",)), ("Direct Blocker", ("parent",)),
                          ("层级", ("depth",)), ("Status", ("status",)), ("当前 SQL", ("sql",))),
                   tuple("blocked" if complete else "observed-blocking" for _ in rows))
    if table is not None:
        table = replace(table, cell_links=tuple((i, len(table.headers) - 1, row["anchor"])
                                               for i, row in enumerate(rows) if row["anchor"]))
    summary = ("当前有界采样观察到的阻塞关系。" if edges and complete else
               "本次采样未观测到阻塞关系。" if complete else
               "阻塞证据不完整。仅展示实际采样的关系，根节点或层级仍可能需要核验。")
    summary += _omitted(len(related), len(rows), "blocking nodes")
    evidence = _table([{"parent": parent, "victim": victim} for parent, victim in
                       edges[:policy.max_related_sessions]],
                      (("Direct Blocker", ("parent",)), ("被阻塞会话", ("victim",))))
    graph = details.get("blocking_graph") or {}
    limitations = tuple(("不确定因素", _human(row)) for row in
                        _dict_rows(graph.get("anomalies"))[:policy.max_wait_rows])
    return [ReportSection("blocking", "Blocking Analysis", summary=summary,
                          tables=(table,) if table else ()),
            ReportSection("blocking-evidence", "原始阻塞边", fields=limitations,
                          summary=_omitted(len(edges), min(len(edges), policy.max_related_sessions), "阻塞边"),
                          tables=(evidence,) if evidence else ())], related


def _session_and_request_sections(report: DiagnosticReport, details: dict[str, Any],
                                  policy: HumanReportPolicy
                                  ) -> tuple[list[ReportSection], set[str]]:
    sessions_list = _dict_rows(details.get("all_sessions", details.get("sessions")))
    requests_list = _active_request_rows(report, details)
    long_rows = _long_running_rows(report, details)
    graph_value = details.get("blocking_graph")
    graph = graph_value if isinstance(graph_value, dict) else {}
    roots_value = graph.get("roots")
    roots: list[Any] = roots_value if isinstance(roots_value, list) else []
    raw_children = graph.get("children")
    raw_children_dict: dict[Any, Any] = raw_children if isinstance(raw_children, dict) else {}
    children: dict[str, list[Any]] = {
        str(key): value for key, value in raw_children_dict.items() if isinstance(value, list)}
    session_rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in sessions_list:
        sid = _session_id(row)
        if sid is not None:
            session_rows_by_id.setdefault(sid, []).append(row)
    session_by_id = {sid: rows[0] for sid, rows in session_rows_by_id.items() if len(rows) == 1}
    request_by_id = {(_session_id(row) or ""): row for row in requests_list}
    blocking_complete, blocking_participants = _blocking_state(report, details)
    sections, related = _blocking_sections(
        details, roots, children, session_by_id, requests_list, blocking_complete, policy,
        sqlserver=report.instance.db_type.value == "sqlserver")
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
                             "open_txn": _first(session, "open_transaction_count", "open_tran"),
                             "mysql_processlist_id": _first(session, "processlist_id", "session_id") or
                                                     _first(request, "processlist_id", "session_id"),
                             "mysql_thread_id": _first(session, "thread_id", "performance_schema_thread_id") or
                                                _first(request, "thread_id", "performance_schema_thread_id"),
                             "oracle_inst_id": _first(session, "inst_id") or _first(request, "inst_id"),
                             "oracle_sid": _first(session, "sid") or _first(request, "sid"),
                             "oracle_serial": _first(session, "serial_number", "serial_num", "serial#") or
                                              _first(request, "serial_number", "serial_num", "serial#"),
                             "postgresql_pid": _first(session, "pid", "process_id") or
                                               _first(request, "pid", "process_id")})
    related_total = len(related_rows)
    related_rows = related_rows[:policy.max_related_sessions]
    identity_columns: tuple[tuple[str, tuple[str, ...]], ...] = (("Session", ("session",)), ("Role", ("role",)),
                        ("Database", ("database",)), ("Status", ("status",)),
                        ("Login", ("login",)), ("Host", ("host",)),
                        ("Program", ("program",)), ("Client IP", ("client_ip",)))
    if report.instance.db_type.value == "mysql":
        identity_columns += (("MySQL PROCESSLIST_ID", ("mysql_processlist_id",)),
                             ("Performance Schema THREAD_ID", ("mysql_thread_id",)))
    elif report.instance.db_type.value == "postgresql":
        identity_columns += (("PostgreSQL PID", ("postgresql_pid",)),)
    elif report.instance.db_type.value == "oracle":
        identity_columns += (("Oracle INST_ID", ("oracle_inst_id",)),
                             ("Oracle SID", ("oracle_sid",)),
                             ("Oracle SERIAL#", ("oracle_serial",)))
    if report.instance.db_type.value == "sqlserver":
        identity_columns += (("Session Open Transaction Count", ("open_txn",)),)
    identity_states = tuple(
        "blocked" if str(row["session"]) in blocking_participants else
        "partial" if (not blocking_complete or
                      (str(row["session"]) not in session_by_id and
                       str(row["session"]) not in request_by_id)) else "clear"
        for row in related_rows)
    identity_table = _table(related_rows, identity_columns, identity_states)
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

    sections.extend(_current_request_sections(
        report, details, requests_list, sessions_list, policy))
    visible_sql = {(_session_id(row), block.content)
                   for row in requests_list[:policy.max_related_sessions]
                   if (block := _sql_block(row, policy)) is not None}
    extra_sql: list[ReportCodeBlock] = []

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
                     "interpretation": (row.get("request_interpretation")
                         if report.instance.db_type.value == "sqlserver" else
                         "明确的主动等待语句，不代表数据库资源瓶颈" if
                         row.get("request_kind") == "ACTIVE_WAIT" else
                         "长时间主动等待（WAITFOR）" if
                         str(row.get("wait_type", "")).upper() == "WAITFOR" and
                         isinstance(row.get("sql_text"), str) and
                         re.search(r"\bWAITFOR\s+(DELAY|TIME)\b", row["sql_text"], re.IGNORECASE)
                         else "WAITFOR 等待事件；SQL 文本不可用，语义需复核" if
                         str(row.get("wait_type", "")).upper() == "WAITFOR" else None),
                     "database": _first(row, "database_name", "datname")} for row in visible]
            row_states = tuple(
                "blocked" if (str(_session_id(row) or "") in blocking_participants or
                              _first(row, "blocking_session_id", "blocking_pid", "blocking_sid")
                              not in (None, "", 0, "0")) else
                "clear" if blocking_complete and _session_id(row) is not None else "partial"
                for row in visible)
            table = _table(rows, (("Session", ("session",)), ("Status", ("status",)),
                                  ("Elapsed", ("elapsed",)), ("Wait", ("wait",)),
                                  ("Wait Resource", ("wait_resource",)),
                                  ("Direct Blocker", ("blocker",)),
                                  ("Root Blocker", ("root_blocker",)),
                                  ("Observation", ("interpretation",)),
                                  ("Database", ("database",))), row_states)
            if values:
                sections.append(ReportSection(section_id, title,
                                              summary=_omitted(len(values), len(visible), noun),
                                              tables=(table,) if table else ()))
            for row in visible:
                block = _sql_block(row, policy, "长运行采样语句")
                if block is not None and (key := (_session_id(row), block.content)) not in visible_sql:
                    visible_sql.add(key)
                    extra_sql.append(block)
        if extra_sql:
            sections.append(ReportSection("additional-request-sql", "补充 SQL 采样",
                                          summary="下列文本未在当前活动请求卡中展示，采样时点或展示范围可能不同。",
                                          code_blocks=tuple(extra_sql)))

    wait_source = requests_list
    wait_rows = [{"session": _session_id(row),
                  "wait": _first(row, "wait_type", "wait_event", "current_wait"),
                  "duration": _duration_ms(_first(row, "wait_time_ms", "wait_time", "wait_ms")),
                  "blocker": _first(row, "blocking_session_id", "blocking_pid", "blocking_sid"),
                  "resource": row.get("wait_resource")}
                 for row in wait_source if _first(row, "wait_type", "wait_event", "current_wait")]
    shown_waits = wait_rows[:policy.max_wait_rows]
    wait_states = tuple(
        "blocked" if str(row.get("session") or "") in blocking_participants else
        "clear" if blocking_complete and row.get("session") not in (None, "") else "partial"
        for row in shown_waits)
    wait_columns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Session", ("session",)), ("Wait", ("wait",)),
        ("Duration", ("duration",)), ("Blocker", ("blocker",)),
    )
    if any(row.get("resource") for row in shown_waits):
        wait_columns += (("Wait Resource", ("resource",)),)
    wait_table = _table(shown_waits, wait_columns, wait_states)
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
    appendix_states = tuple(
        "blocked" if str(row["session"]) in blocking_participants else
        "clear" if blocking_complete and row.get("session") not in (None, "") and
        str(row["session"]) in session_by_id else "partial"
        for row in appendix_rows)
    appendix_table = _table(appendix_rows, (("Session", ("session",)), ("Database", ("database",)),
                                           ("Status", ("status",)), ("Login", ("login",)),
                                            ("Host", ("host",)), ("Program", ("program",))),
                            appendix_states)
    if unrelated:
        sections.append(ReportSection("appendix", "Appendix — Other Sessions",
                                      summary=_omitted(len(unrelated), len(appendix_rows), "other sessions"),
                                      tables=(appendix_table,) if appendix_table else (), collapsed=True))
    return sections, related


_SQLSERVER_TRANSACTION_BEGIN_SOURCE = "sys.dm_tran_active_transactions.transaction_begin_time"
_SQLSERVER_TRANSACTION_BEGIN_LABEL = "SQL Server 事务 DMV"


def _transaction_sections(details: dict[str, Any], database_type: str,
                          policy: HumanReportPolicy,
                          report: DiagnosticReport, incident: IncidentSummary) -> list[ReportSection]:
    rows = _dict_rows(details.get("transactions")) or _dict_rows(details.get("idle_transactions"))
    count = incident.impact.get("open_transactions")
    summary = (f"未结束事务数：{count}。" if count is not None else
               "未结束事务总数未知；明细仅展示实际采样的事务。")
    requests = _active_request_rows(report, details)
    request_anchors: dict[str, str] = {}
    for index, request in enumerate(requests[:policy.max_related_sessions], 1):
        if (sid := _session_id(request)) is not None:
            request_anchors.setdefault(sid, f"#current-request-{index:03d}")
    roots = _dict_rows(details.get("roots"))
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
        txid = _first(row, "transaction_id", "trx_id", "xid")
        started = _first(row, "transaction_start_time", "transaction_begin_time", "begin_time", "trx_started", "xact_start")
        source = row.get("transaction_start_time_source")
        if database_type == "sqlserver":
            if not source and started is not None:
                source = _SQLSERVER_TRANSACTION_BEGIN_SOURCE
            if source == _SQLSERVER_TRANSACTION_BEGIN_SOURCE:
                source = _SQLSERVER_TRANSACTION_BEGIN_LABEL
        verified = any(txid is not None and str(session) == _session_id(root)
                       and txid == _first(root, "transaction_id", "trx_id", "xid")
                       and (root.get("transaction_identity_verified") is True or
                            root.get("transaction_identity_quality") == "VERIFIED") for root in roots)
        if database_type == "postgresql" and started is not None:
            verified = any(str(session) == _session_id(root) and started == root.get("xact_start")
                           and root.get("unfinished_transaction_confirmed") is True for root in roots)
        output.append({"session": session,
                       "transaction_id": txid,
                       "begin": _local_time(started) if started is not None else "未采集（事务记录缺少开始时间）",
                       "begin_source": source,
                       "age": _duration_seconds(age), "type": kind,
                       "state": _human(_first(row, "trx_state")) if database_type == "mysql"
                       else state,
                       "verification": ("已确认" if verified else "部分确认（已观测，关联身份未核验）"
                                        if session not in (None, "") and (txid is not None or
                                                                           database_type == "postgresql" and started)
                                        else
                                        "无法确认（缺少会话或事务标识）"),
                       "current_sql": ("查看该会话的当前活动 SQL" if verified else "查看同号会话的 SQL（关联待核验）")
                       if str(session) in request_anchors else "当前活动 SQL 未展示或未采集"})
    shown = output[:policy.max_transactions]
    blocking_complete, blocking_participants = _blocking_state(report, details)
    transaction_states = tuple(
        "blocked" if row.get("session") not in (None, "") and
        str(row.get("session")) in blocking_participants else
        "clear" if blocking_complete and row.get("session") not in (None, "") else "partial"
        for row in shown)
    table = _table(shown, (("Session", ("session",)), ("Transaction ID", ("transaction_id",)),
                           ("Begin Time", ("begin",)), ("Begin Time Source", ("begin_source",)),
                           ("Age", ("age",)),
                           ("Type", ("type",)), ("State", ("state",)),
                           ("事务关联核验", ("verification",)), ("当前 SQL", ("current_sql",))), transaction_states)
    dmv_rows = [index for index, row in enumerate(shown)
                if database_type == "sqlserver" and row["begin_source"] == _SQLSERVER_TRANSACTION_BEGIN_LABEL]
    if table is not None:
        links = tuple(
            (index, len(table.headers) - 1, request_anchors[str(row["session"])])
            for index, row in enumerate(shown) if str(row["session"]) in request_anchors)
        if dmv_rows:
            source_column = table.headers.index("Begin Time Source")
            links += tuple((index, source_column, "#transaction-start-source") for index in dmv_rows)
        table = replace(table, cell_links=links)
    sections = [ReportSection("transaction-analysis", "Transaction Analysis",
                              summary=summary + _omitted(len(output), len(shown), "transactions"),
                              tables=(table,) if table else ())]
    if dmv_rows:
        sections.append(ReportSection("transaction-start-source", "事务开始时间来源",
                                      fields=((_SQLSERVER_TRANSACTION_BEGIN_LABEL, _SQLSERVER_TRANSACTION_BEGIN_SOURCE),)))
    return sections


def _recovery_section(incident: IncidentSummary, details: dict[str, Any], scenario: str,
                      database_type: str = "", report: DiagnosticReport | None = None) -> ReportSection:
    identity = {
        "sqlserver": "session_id、login_time、login_name、host_name、program_name、事务标识",
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
            reason = f"未生成具体命令：{display_slow_label(str(explicit_reason)) if scenario == 'slow' else explicit_reason}"
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
        status = ("FAILED" if report.scenario == "slow" and not success else
                  "MISSING_REQUIRED" if required else
                  "OPTIONAL_UNAVAILABLE" if optional else
                  "FAILED" if not success else "TRUNCATED" if truncated else "COMPLETE")
        impact = (sample.get("error_message") or sample.get("error_type") or
                  ("Collector row limit reached" if truncated else "—"))
        if report.scenario == "slow" and str(impact).casefold() in {"—", "unknown", "n/a"}:
            impact = ("采集失败，影响范围尚未评估" if not success else
                      "已达到采集行数上限" if truncated else "尚未评估")
        database_error = sample.get("database_error")
        if report.scenario == "slow" and not success and isinstance(database_error, dict):
            vendor_fields = [
                f"{label}={database_error[key]}" for key, label in (
                    ("driver", "驱动"), ("sqlstate", "SQLSTATE"), ("vendor_code", "数据库错误码"),
                ) if database_error.get(key) is not None
            ]
            message = database_error.get("message")
            if isinstance(message, str) and message and message not in str(impact):
                vendor_fields.append(message)
            if vendor_fields:
                impact = f"{impact} {' · '.join(vendor_fields)}"
        quality.append({"collector": name, "status": status,
                        "impact": display_slow_label(str(impact)) if report.scenario == "slow" else impact,
                        "started": _local_time(sample.get("started_at")),
                        "finished": _local_time(sample.get("finished_at")), "rows": sample.get("row_count")})
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
            quality.append({"collector": "快照" if report.scenario == "slow" else "Snapshot", "status": "NOTE",
                            "impact": display_slow_label(note) if report.scenario == "slow" else note})
    if report.scenario == "slow":
        if report.instance.db_type.value == "mysql":
            wait_status = details.get("wait_detail_status")
            if isinstance(wait_status, str) and wait_status not in {"", "AVAILABLE"}:
                wait_reason = details.get("wait_detail_reason")
                quality.append({
                    "collector": "current_wait_detail",
                    "status": wait_status,
                    "impact": _human(wait_reason) if wait_reason else
                              "当前等待详情不可见；基础活动请求采集状态单独列示。",
                })
    if not quality:
        quality = [{"collector": item, "status": "MISSING", "impact": "必需证据不可用"}
                   for item in incident.missing_evidence]
    columns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Collector", ("collector",)), ("Status", ("status",)), ("Impact", ("impact",)))
    if report.scenario == "slow":
        columns += (("Capture Started", ("started",)), ("Capture Finished", ("finished",)), ("采样行数", ("rows",)))
        for row in quality:
            row["collector"] = display_slow_collector(str(row["collector"]))
            row["impact"] = display_slow_label(str(row["impact"]))
    table = _table(quality, columns)
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
        title = display_finding(item.title)
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


def _slow_problem_sections(report: DiagnosticReport, incident: IncidentSummary,
                           policy: HumanReportPolicy) -> list[ReportSection]:
    details = getattr(report, "details", {})
    sqlserver = report.instance.db_type.value == "sqlserver"
    graph = details.get("blocking_graph") or {}
    children = graph.get("children") or {}
    edges = [(str(parent), str(victim)) for parent, victims in children.items()
             if isinstance(victims, list) for victim in victims]
    participants = {sid for edge in edges for sid in edge}
    classified = {finding.session for finding in report.findings
                  if finding.title in {"CPU_PRESSURE", "IO_PRESSURE"} and finding.session is not None}
    findings: list[Finding] = []
    for finding in report.findings:
        if sqlserver and participants and finding.title in {"LONG_RUNNING_SQL", "WAITING_SQL"}:
            rows = _dict_rows(finding.evidence.get("requests"))
            independent = [row for row in rows if (sid := _session_id(row)) is not None
                           and sid not in participants and sid not in classified
                           and row.get("blocking_session_id") in (0, "0")
                           and row.get("request_kind") != "ACTIVE_WAIT"]
            if independent:
                findings.append(replace(finding, evidence={**finding.evidence, "requests": independent,
                                                            "count": len(independent)}))
            continue
        if sqlserver and finding.title == "IDLE_TRANSACTION_BLOCKER" and str(finding.session) in participants:
            continue
        findings.append(finding)
    has_blocking = any(finding.title == "BLOCKING" for finding in findings)
    findings = [finding for finding in findings if not (has_blocking and finding.title == "ROOT_BLOCKER")]
    if not findings:
        return [ReportSection("diagnosis-result", "本次诊断结论", summary=(
            "现有采样未识别出性能异常。" if incident.status == "HEALTHY" and
            incident.data_completeness == "COMPLETE" else "现有证据不足，无法确认当前性能问题。"))]
    sections = []
    for index, finding in enumerate(findings[:policy.max_wait_rows], 1):
        title = display_finding(finding.title)
        impact = f"会话 {finding.session}" if finding.session else "影响范围未确认"
        conclusion = f"本次采样记录了{title}。"
        cause_label = "发生原因"
        cause = "现有证据尚不能确定根因。"
        evidence = "见对应的活动请求、事务或采集质量记录。"
        uncertainty = "单次采样不能说明问题的持续趋势。"
        confidence = "部分确认" if incident.data_completeness != "COMPLETE" else "已确认观测结果"
        if finding.title in {"BLOCKING", "ROOT_BLOCKER"}:
            root = incident.root_cause_candidate or {}
            root_position_confirmed = False
            if sqlserver:
                cause_label = "根阻塞关联证据"
                cause = "当前证据尚不能核验根会话与未结束事务的关联。"
            roots = _dict_rows(details.get("roots"))
            roots_shown = [sid for row in roots[:policy.max_root_blockers]
                           if (sid := _session_id(row)) is not None]
            for key, unit in (("blocked_requests", "条受阻请求"), ("blocked_sessions", "个受阻会话"),
                              ("blocked_statements", "条受阻语句")):
                if key in incident.impact:
                    impact = f"本次样本观测到 {incident.impact[key]} {unit}"
                    break
            if sqlserver and edges:
                sessions = list(dict.fromkeys(sid for edge in edges for sid in edge))
                impact += "；涉及会话 " + "、".join(sessions[:policy.max_related_sessions])
                if len(sessions) > policy.max_related_sessions:
                    impact += _omitted(len(sessions), policy.max_related_sessions, "阻塞会话")
            elif roots_shown:
                impact += "；根阻塞候选会话 " + "、".join(roots_shown)
            conclusion = "本次采样观察到请求被其他会话阻塞。"
            event = (incident.wait or {}).get("event")
            evidence = f"阻塞关系图；当前等待事件 {event}。" if event and event != "UNKNOWN" else "见阻塞关系图。"
            if sqlserver and edges:
                graph_complete, _ = _blocking_state(report, details)
                confidence = "阻塞关系已确认" if graph_complete else "已观测阻塞边，完整链路待核验"
                root_position_confirmed = (graph_complete and str(root.get("session")) in
                                           {str(sid) for sid in graph.get("roots", [])})
                observed_edges = set(edges)
                locks = list(dict.fromkeys(
                    f"会话 {_session_id(row)} 已观测到 {display_slow_wait(row['wait_type'])}"
                    for row in _active_request_rows(report, details)
                    if (str(row.get("blocking_session_id")), str(_session_id(row))) in observed_edges
                    and str(row.get("wait_type") or "").startswith("LCK_M_")))
                if locks:
                    evidence = "阻塞关系图。" + "；".join(locks[:5]) + "。"
            if sqlserver and root.get("transaction_identity_verified") is True:
                session_id = root.get("session")
                position = (f"会话 {session_id} 已确认处于阻塞链根节点" if root_position_confirmed else
                            f"会话 {session_id} 是根阻塞候选")
                cause = (f"{position}，并关联到已核验的未结束事务 {root.get('transaction_id')}。"
                         "事务内实际取得锁的具体语句尚无法确认。")
                requests = _dict_rows(details.get("all_requests"))
                if any(row.get("session_id") == root.get("session") and row.get("command") == "WAITFOR"
                       for row in requests):
                    uncertainty = "根会话当前语句为 WAITFOR，事务内获取锁的具体语句暂无法确认。"
                else:
                    uncertainty = "当前请求与事务属于现场证据，获取锁的具体语句暂无法确认。"
        elif finding.title in {"LONG_RUNNING_SQL", "WAITING_SQL"}:
            rows = _dict_rows(_first(finding.evidence, "requests", "queries", "sessions"))
            sessions = list(dict.fromkeys(sid for row in rows if (sid := _session_id(row)) is not None))
            events = list(dict.fromkeys(str(event) for row in rows
                          if (event := _first(row, "wait_type", "wait_event", "event"))))
            count = finding.evidence.get("count")
            if type(count) is int:
                impact = f"涉及 {count} 条采样请求"
            if sessions:
                impact += "；样例会话 " + "、".join(sessions[:5])
            if events:
                evidence = "当前等待事件：" + "、".join(display_slow_wait(event) for event in events[:5])
        elif finding.title in {"INCOMPLETE_SNAPSHOT", "BLOCKING_GRAPH_ANOMALY"}:
            confidence = "无法确认"
            uncertainty = "采集或关联证据不完整，详细原因见高级诊断附录。"
        severity = {"CRITICAL": "高", "HIGH": "高", "WARNING": "中", "INFO": "信息"}.get(
            finding.severity.value, "低")
        sections.append(ReportSection(
            f"diagnosis-problem-{index:03d}", title, severity=finding.severity.value,
            fields=(("严重程度", severity), ("诊断结论", conclusion), (cause_label, cause),
                    ("影响范围", impact), ("诊断依据", evidence), ("可信程度", confidence),
                    ("不确定因素", uncertainty), ("建议操作", display_slow_label(finding.recommendation)))))
    if len(findings) > len(sections):
        sections.append(ReportSection("diagnosis-result", "诊断展示范围",
                                      summary=_omitted(len(findings), len(sections), "findings")))
    return sections


_REQUEST_RESOURCE_METRICS = {
    "sqlserver": (("当前请求累计 CPU 时间", "cpu_ms", "ms"),
                  ("当前请求累计逻辑读取", "logical_reads", "页"), ("当前请求累计物理读取", "reads", "页"),
                  ("当前请求累计写入计数", "writes", "次")),
    "mysql": (("检查行数", "rows_examined", "行"), ("影响行数", "rows_affected", "行")),
    "oracle": (("子游标累计 CPU", "sql_cumulative_cpu_us", "µs"),
               ("子游标累计耗时", "sql_cumulative_elapsed_us", "µs"),
               ("子游标累计缓冲区获取", "sql_cumulative_buffer_gets", "次"),
               ("子游标累计磁盘读取", "sql_cumulative_disk_reads", "次"),
               ("子游标累计执行", "sql_cumulative_executions", "次"),
               ("子游标累计处理行数", "sql_cumulative_rows_processed", "行")),
}
_REQUEST_WAIT_FIELDS = {"sqlserver": "wait_type", "mysql": "current_wait", "postgresql": "wait_event", "oracle": "event"}


def _resource_counter(value: object) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0:
        return value
    return None


def _sqlserver_statement_key(row: dict[str, Any]) -> tuple[int, str, int, int] | None:
    dbid, handle = row.get("database_id"), row.get("sql_handle")
    start, end = row.get("statement_start_offset"), row.get("statement_end_offset")
    if (type(dbid) is not int or dbid <= 0 or not isinstance(handle, str)
            or re.fullmatch(r"0x(?:[0-9a-f]{2}){1,64}", handle, re.IGNORECASE) is None
            or not int(handle, 16) or type(start) is not int or start < 0 or start % 2
            or type(end) is not int or (end != -1 and (end < start or end % 2))
            or row.get("statement_sql_text_unavailable_reason") ==
            "Current statement offsets are unavailable or invalid"):
        return None
    return dbid, handle.lower(), start, end


def _sqlserver_sql_review_clues(sql: str) -> str:
    # Inspect bounded statement text, leaving quoted names, literals and comments out of the lexical hints.
    syntax = re.sub(r"/\*.*?\*/|--[^\r\n]*|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\[(?:\]\]|[^\]])*\]",
                    " ", sql, flags=re.DOTALL)
    if "*/" in syntax:
        return "—"
    hints = []
    if re.search(r"\bINDEX\s*\(\s*0\s*\)", syntax, re.IGNORECASE):
        hints.append("INDEX(0) 访问路径限制")
    query = re.split(r"\bOPTION\s*\(", syntax, maxsplit=1, flags=re.IGNORECASE)[0]
    joins = re.split(r"\bJOIN\b", query, flags=re.IGNORECASE)[1:]
    if any(re.search(r"\bON\b(?:(?!\b(?:WHERE|GROUP|ORDER|OPTION)\b).)*?\bCONVERT\s*\(",
                     join, re.IGNORECASE | re.DOTALL) for join in joins):
        hints.append("JOIN 条件中的 CONVERT")
    if len(joins) > 1:
        hints.append("多表 JOIN")
    for keyword in ("GROUP", "ORDER"):
        if re.search(rf"\b{keyword}\s+BY\b", syntax, re.IGNORECASE):
            hints.append(f"{keyword} BY")
    return "、".join(hints) or "—"


def _sqlserver_concurrency_section(details: dict[str, Any], policy: HumanReportPolicy) -> ReportSection | None:
    requests = _dict_rows(details.get("all_requests"))
    if not requests:
        return None
    identified: dict[tuple[int, str, int, int], list[tuple[int, dict[str, Any]]]] = {}
    independent: list[list[tuple[int, dict[str, Any]]]] = []
    for index, row in enumerate(requests, 1):
        key = _sqlserver_statement_key(row)
        if key is None:
            independent.append([(index, row)])
        else:
            identified.setdefault(key, []).append((index, row))
    groups = sorted(identified.values(), key=len, reverse=True) + independent
    complete = _collector_complete(details, "active_requests")
    status = [str(row.get("status") or "").strip().upper() for row in requests]
    runnable, running = status.count("RUNNABLE"), status.count("RUNNING")
    yields = sum(str(row.get("wait_type") or "").upper() == "SOS_SCHEDULER_YIELD" for row in requests)

    def total(values: list[int | float | None], unit: str) -> str:
        present = [value for value in values if value is not None]
        if not present:
            return "未采集"
        coverage = f"（{len(present)}/{len(values)} 条已采集）" if len(present) != len(values) else ""
        return f"{sum(present):,} {unit}{coverage}"

    cpu = [[_resource_counter(_first(row, "cpu_ms", "cpu_time")) for _, row in members] for members in groups]
    reads = [[_resource_counter(row.get("logical_reads")) for _, row in members] for members in groups]
    leaders = []
    if identified:
        leaders.append(("可核验组中并发最多", 0))
    if complete and not independent:
        for label, totals in (("累计 CPU 最高", cpu), ("累计逻辑读最高", reads)):
            if all(value is not None for values in totals for value in values):
                leaders.append((label, max(range(len(groups)), key=lambda i: sum(
                    value for value in totals[i] if value is not None))))
    limit = min(10, policy.max_wait_rows, len(groups))
    visible = list(dict.fromkeys([*(i for _, i in leaders), *range(limit)]))[:limit]
    rows, links = [], []
    for index in visible:
        members = groups[index]
        request_index, representative = members[0]
        source_text = representative.get("statement_sql_text")
        block = _sql_block(representative, policy) if isinstance(source_text, str) and source_text else None
        excerpt = " ".join(block.content.split()) if block else ""
        database = representative.get("database_name")
        label = f"SQL {index + 1}" if index < len(identified) else f"请求 {request_index}（身份未核验）"
        if database:
            label += f" · {_human(database)}"
        if excerpt:
            label += f" · {excerpt[:120]}" + ("…" if len(excerpt) > 120 else "")
        clues = (_sqlserver_sql_review_clues(block.content)
                 if block and isinstance(source_text, str) and not representative.get("statement_sql_text_truncated")
                 and len(source_text) <= policy.max_sql_chars else "—")
        rows.append((label, str(len(members)), total(cpu[index], "ms"), total(reads[index], "页"),
                     str(sum(str(row.get("status") or "").strip().upper() == "RUNNABLE" for _, row in members)),
                     _session_id(representative) or "未知", clues))
        if request_index <= policy.max_related_sessions:
            links.append((len(rows) - 1, 5, f"#current-request-{request_index:03d}"))
    runnable_label = display_request_status("RUNNABLE")
    running_label = display_request_status("RUNNING")
    observed = f"本次活动请求样本 {len(requests)} 条，{runnable_label} {runnable} 条，{running_label} {running} 条。"
    other = len(requests) - runnable - running
    if other:
        observed += f" 其他或状态未采集 {other} 条。"
    if yields:
        observed += f" {yields} 条记录了 SOS_SCHEDULER_YIELD。"
    scheduling = (f"{runnable_label}表示请求等待 CPU 调度，可能存在调度竞争。" if runnable else
                  f"本次样本未观测到{runnable_label}请求。")
    scheduling += " 尚未采集实例 CPU 使用率或调度器运行队列，持续 CPU 瓶颈待核验。"
    findings = "；".join(f"{label}：SQL {i + 1}（{len(groups[i])} 条请求）" for label, i in leaders if i in visible)
    if findings:
        findings += "。优先复核代表会话的执行计划、索引访问路径和 JOIN 条件。"
    quality = "活动请求采样不完整，以下统计仅覆盖可见记录。" if not complete else ""
    if independent:
        quality += f" {len(independent)} 条请求缺少可核验的语句身份，独立列示。"
    if len(leaders) < 3:
        quality += " 资源排名仅在请求采样、语句身份和对应指标完整时给出。"
    summary = (observed + " CPU 和逻辑读为样本中各请求截至采集时的累计值加总。"
               "逻辑读以页计。分组按数据库、批次句柄和语句偏移。"
               + quality + " " + _omitted(len(groups), len(visible), "SQL 分组"))
    table = ReportTable(("SQL 类别", "活动请求", "CPU 累计", "逻辑读累计", runnable_label, "代表会话", "SQL 文本核查线索"),
                        tuple(rows), tuple(links))
    fields: tuple[tuple[str, str], ...] = (("调度证据", scheduling),)
    if findings:
        fields += (("优先核查", findings),)
    return ReportSection("diagnosis-sql-concurrency", "高并发 SQL 聚合分析", summary=summary,
                         fields=fields, tables=(table,))


def _sqlserver_resource_observation(row: dict[str, Any], event: str, blockers: list[str],
                                    root: dict[str, Any] | None) -> str:
    elapsed = _resource_counter(row.get("elapsed_ms"))
    cpu = _resource_counter(_first(row, "cpu_ms", "cpu_time"))
    wait = _resource_counter(_first(row, "wait_ms", "wait_time_ms"))
    logical = _resource_counter(row.get("logical_reads"))
    physical = _resource_counter(row.get("reads"))
    writes = _resource_counter(row.get("writes"))
    if event.startswith("LCK_M_"):
        result = "当前锁等待占请求运行时间的大部分。" if elapsed and wait is not None and elapsed / 2 < wait <= elapsed else (
            "当前正在等待锁。" if wait is not None else "已观测到锁等待，当前等待时长未采集。")
        if blockers:
            result += f"请求正在等待会话 {'、'.join(blockers)} 释放锁。"
    elif event == "WAITFOR" and (
            str(row.get("command") or "").upper() == "WAITFOR" or row.get("request_kind") == "ACTIVE_WAIT"):
        result = "当前 SQL 正在执行 WAITFOR 主动等待。"
        if root is not None and root.get("transaction_identity_verified") is True:
            result += ("该会话是已确认的根阻塞节点，且持有已核验的未结束事务。"
                       "取得锁的具体语句仍待核查。")
    elif event == "RESOURCE_SEMAPHORE":
        result = "当前正在等待查询内存授予。授予量和实例内存压力仍需核查。"
    elif event.startswith("PAGEIOLATCH_") or event in {"IO_COMPLETION", "ASYNC_IO_COMPLETION", "WRITELOG"}:
        result = "当前观测到 I/O 相关等待。单次等待无法确认存储瓶颈。"
    elif event:
        result = "已采集当前等待事件，单次采样无法确定请求的主要耗时来源。"
    elif elapsed and cpu is not None and elapsed / 2 < cpu <= elapsed:
        result = "截至采集时，累计 CPU 时间超过已运行时间的一半，计算开销值得优先核查。"
    elif elapsed and cpu is not None and cpu > elapsed:
        result = "截至采集时，累计 CPU 时间超过已运行时间，可能涉及并行执行，无法直接比较两者占比。"
    elif writes:
        result = f"截至采集时，该请求累计写入 {writes:,} 次。单次观测无法判断写入是否主导耗时。"
    elif all(value is None for value in (cpu, logical, physical, writes)):
        result = "本次未取得请求资源计数，暂无法判断主要资源消耗。"
    else:
        result = "本次未观测到明确的主导等待或资源异常，判断仍限于已采集的请求指标。"
    if blockers and not event.startswith("LCK_M_"):
        result += f"本次请求采样记录的阻塞者为会话 {'、'.join(blockers)}。"
    if event.startswith("LCK_M_") or event == "WAITFOR":
        if cpu == 0:
            result += "截至采集时，当前请求累计 CPU 时间为零。"
        elif elapsed and cpu is not None and cpu <= elapsed:
            result += f"截至采集时，累计 CPU 时间相当于已运行时间的 {100 * cpu / elapsed:.3g}%。"
        if physical == 0:
            result += "截至采集时，当前请求累计物理读取为零。"
    return result


def _slow_resource_sections(report: DiagnosticReport, details: dict[str, Any],
                            policy: HumanReportPolicy) -> list[ReportSection]:
    engine = report.instance.db_type.value
    requests = _active_request_rows(report, details)
    visible = requests[:policy.max_related_sessions]
    graph = details.get("blocking_graph")
    graph = graph if isinstance(graph, dict) else {}
    children = graph.get("children")
    parents: dict[str, list[str]] = {}
    if isinstance(children, dict):
        for parent, victims in children.items():
            if isinstance(victims, list):
                for victim in victims:
                    parents.setdefault(str(victim), []).append(str(parent))
    complete, _ = _blocking_state(report, details)
    root_ids = {str(sid) for sid in graph.get("roots", [])} if complete else set()
    roots = {_session_id(row): row for row in _dict_rows(details.get("roots")) if _session_id(row) in root_ids}
    active_waits = {_session_id(row) for row in _dict_rows(details.get("active_waits"))}
    sections = []
    for index, row in enumerate(visible, 1):
        sid = _session_id(row)
        title = f"会话 {sid or '未知'}"
        request_id = _first(row, "request_id", "request")
        if request_id is not None:
            title += f" / 请求 {request_id}"
        if engine == "oracle" and row.get("sql_id"):
            title += f" / SQL_ID {row['sql_id']}"
        # Match section 03's statement-vs-batch boundary and use its request index.
        statement_missing = (engine == "sqlserver" and "statement_sql_text" not in row and
                             any(key in row for key in ("statement_start_offset", "statement_end_offset")))
        current_sql = (row.get("statement_sql_text") if "statement_sql_text" in row else
                       _first(row, "sql_text", "query", "sql_fulltext", "current_sql"))
        block = _sql_block(row, policy) if not statement_missing and current_sql else None
        excerpt = " ".join(block.content.split()) if block else ""
        excerpt = excerpt[:96] + "…" if len(excerpt) > 96 else excerpt
        rows: list[tuple[str, str]] = []
        for label, key, unit in _REQUEST_RESOURCE_METRICS.get(engine, ()):
            value = _resource_counter(_first(row, "cpu_ms", "cpu_time") if key == "cpu_ms" else row.get(key))
            display = f"{value:,} {unit}" if value is not None else "未采集"
            if key == "logical_reads" and value is not None:
                display += f"（按 8 KiB/页折合 {value * 8:,} KiB）"
            rows.append((label, display))
        event = str(row.get(_REQUEST_WAIT_FIELDS.get(engine, "")) or "")
        blockers = parents.get(sid or "", []) if engine != "sqlserver" else []
        if engine == "sqlserver":
            native_blocker = row.get("blocking_session_id")
            if type(native_blocker) is int and native_blocker > 0:
                blockers = [str(native_blocker)]
            elif type(native_blocker) is int and native_blocker < 0:
                rows.append(("特殊阻塞标识", str(native_blocker)))
        event_label = "当前等待事件"
        if engine == "oracle" and event and row.get("state") != "WAITING":
            event_label = "采样事件（当前等待未确认）"
        rows.append((event_label, display_slow_wait(event) if event else "未取得等待事件"))
        if engine == "sqlserver" or event_label == "当前等待事件" and event:
            wait = _resource_counter(_first(row, "wait_ms", "wait_time_ms")) if engine == "sqlserver" else None
            rows.append(("当前等待时长", f"{wait:,} ms（{_duration_ms(wait)}）" if wait is not None else "未采集"))
        if engine == "sqlserver":
            analysis = _sqlserver_resource_observation(row, event.upper(), blockers, roots.get(sid))
        elif row.get("request_kind") == "ACTIVE_WAIT" or sid is not None and sid in active_waits:
            analysis = "当前语句已识别为主动等待语句。"
        elif blockers:
            analysis = f"采样阻塞关系显示请求正在等待会话 {'、'.join(blockers)}。"
        elif event and event_label == "当前等待事件":
            io_wait = ((engine == "mysql" and event.startswith("wait/io/")) or
                       (engine == "postgresql" and row.get("wait_event_type") == "IO") or
                       (engine == "oracle" and row.get("wait_class") in {"User I/O", "System I/O"}))
            observed = "已观测 I/O 相关等待。" if io_wait else "已观测当前等待事件。"
            analysis = observed + "等待时长未采集，无法判断该事件占本次执行耗时的比例。"
        else:
            analysis = "本次样本展示已采集的运行和资源指标，未确认主导等待。"
        if engine == "oracle":
            analysis += "资源计数来自 V$SQL 子游标累计统计，无法归到这一次执行。"
        rows.append(("性能分析", analysis))
        sections.append(ReportSection(f"sql-resource-{index:03d}", title,
                                      summary=f"SQL 摘要：{excerpt}" if excerpt else "查看当前 SQL 采样详情",
                                      summary_link=f"#current-request-{index:03d}",
                                      fields=tuple(rows), collapsed=index > 3))
    return sections


def _slow_sections(report: DiagnosticReport, incident: IncidentSummary,
                   details: dict[str, Any], policy: HumanReportPolicy) -> list[ReportSection]:
    sections: list[ReportSection] = [_recovery_section(incident, details, report.scenario,
                                                       report.instance.db_type.value, report)]
    sections.extend(_slow_problem_sections(report, incident, policy))
    sqlserver = report.instance.db_type.value == "sqlserver"
    if sqlserver and any(finding.title == "HIGH_CONCURRENCY" for finding in report.findings):
        concurrency = _sqlserver_concurrency_section(details, policy)
        if concurrency is not None:
            sections.append(concurrency)
    sections.extend(_slow_resource_sections(report, details, policy))
    blocking_complete, blocking_participants = _blocking_state(report, details)
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
                      "transaction_start_time_source": item.get("transaction_start_time_source"),
                      "transaction_identity_quality": _human(item.get("transaction_identity_quality")),
                      "manual_action_status": item.get("manual_action_status"),
                      "oracle_inst_id": item.get("oracle_inst_id"),
                      "oracle_serial_number": item.get("oracle_serial_number"),
                      "transaction_start_time": item.get("transaction_start_time"),
                      "blocked_count": item.get("blocked_session_count"),
                      "evidence_timestamp": item.get("evidence_timestamp"),
                      "identity_quality": _human(item.get("identity_quality")),
                      "identity_reuse_note": item.get("identity_reuse_note"),
                      "identity_recheck": ("人工处置前重新核对 session_id、login_time、transaction_id"
                                           if sqlserver else "人工处置前必须重新核对会话身份")}
                     for item in priority[:5]]
    priority_states = tuple(
        ("blocked" if blocking_complete else "observed-blocking")
        if str(row.get("session")) in blocking_participants else
        "clear" if blocking_complete and row.get("session") not in (None, "") else "partial"
        for row in priority_rows
    ) if sqlserver else tuple("partial" for _ in priority_rows)
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
                                           ("Transaction start time source", ("transaction_start_time_source",)),
                                           ("Transaction identity quality", ("transaction_identity_quality",)),
                                           ("Manual action status", ("manual_action_status",)),
                                           ("Oracle INST_ID", ("oracle_inst_id",)),
                                           ("Oracle SERIAL#", ("oracle_serial_number",)),
                                           ("Transaction start time", ("transaction_start_time",)),
                                           ("Blocked session count", ("blocked_count",)),
                                           ("Evidence timestamp", ("evidence_timestamp",)),
                                           ("Identity quality", ("identity_quality",)),
                                           ("Identity reuse note", ("identity_reuse_note",)),
                                            ("Identity recheck", ("identity_recheck",))),
                            priority_states)
    sections.append(ReportSection("priority-candidates", "Priority Review Candidates",
                                  summary="候选仅用于人工核查，不代表 Toolkit 已验证当前会话身份或建议自动终止。",
                                  tables=(priority_table,) if priority_table else ()))
    triage = details.get("performance_triage")
    if isinstance(triage, dict):
        triage_rows = []
        for assessment in triage.get("assessments", [])[:5]:
            if not isinstance(assessment, dict):
                continue
            references = [item for item in assessment.get("evidence_references", [])
                          if isinstance(item, dict)]
            # Render only stable source metadata and bounded scalar facts. The
            # triage evidence contract intentionally does not carry SQL text.
            classification_evidence = assessment.get("classification_evidence", {})
            evidence_items = []
            if isinstance(classification_evidence, dict):
                for label, conclusion in list(classification_evidence.items())[:5]:
                    if not isinstance(conclusion, dict):
                        continue
                    class_refs = [item for item in conclusion.get("evidence_references", [])
                                  if isinstance(item, dict)]
                    sources = ", ".join(
                        f"{item.get('collector', 'unknown')}:{item.get('source_path', 'unknown')} "
                        f"[{display_slow_label(item.get('integrity') or 'UNKNOWN')} @ {item.get('observed_at') or '未知'}]"
                        for item in class_refs[:2]) or "证据来源不可用"
                    duration = ""
                    if label == "LONG_RUNNING_TRANSACTION" and conclusion.get("status") in {"CONFIRMED", "NOT_TRIGGERED"}:
                        facts = class_refs[0].get("facts", {}) if class_refs else {}
                        age, threshold = facts.get("transaction_age_seconds"), facts.get("threshold_seconds")
                        if type(age) is int and type(threshold) is int:
                            duration = f"（持续 {age} 秒，阈值 {threshold} 秒）"
                    evidence_items.append(
                        f"{display_slow_label(label)} ({conclusion.get('rule_id') or '未知'}, "
                        f"{display_slow_label(conclusion.get('status') or 'UNKNOWN')}){duration}：{sources}")
            reference_text = "; ".join(evidence_items) or "; ".join(
                f"{item.get('collector', 'unknown')}:{item.get('source_path', 'unknown')} "
                f"[{display_slow_label(item.get('integrity') or 'UNKNOWN')} @ {item.get('observed_at') or '未知'}]"
                for item in references[:4])
            triage_rows.append({
                "identity": "；".join(f"{display_slow_label(_label(str(key)))}：{_human(value)}"
                                     for key, value in (assessment.get("native_identity") or {}).items()),
                "classifications": "、".join(display_slow_label(item) for item in assessment.get("classifications", [])) or "未知",
                "status": assessment.get("classification_status", "UNKNOWN"),
                "rules": ", ".join(assessment.get("rule_ids", [])) or "—",
                "evidence": reference_text or "没有可用的证据引用",
                "uncertainty": "；".join(display_slow_label(reason) for reason in
                                        assessment.get("uncertainty_reasons", [])) or "—",
            })
        triage_table = _table(triage_rows, (
            ("Native session identity", ("identity",)),
            ("Classification labels", ("classifications",)),
            ("Evidence status", ("status",)), ("Rule IDs", ("rules",)),
            ("Evidence source / time / integrity", ("evidence",)),
            ("Uncertainty", ("uncertainty",)),
        ))
        triage_summary = (f"快照时间：{triage.get('snapshot_time') or '未知'}；"
                          f"总体状态：{display_slow_label(triage.get('status') or 'UNKNOWN')}；"
                          f"根因状态：{display_slow_label(triage.get('root_cause_status') or 'NOT_ESTABLISHED')}。"
                          "核查排序不等于已确认根因。")
        sections.append(ReportSection(
            "performance-triage", "Performance Triage", summary=triage_summary,
            fields=tuple(("Evidence limitation", display_slow_label(str(reason)))
                         for reason in triage.get("unknown_reasons", [])[:8]),
            tables=(triage_table,) if triage_table else ()))
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
        table = _table(victim_rows, tuple(victim_columns),
                       tuple("blocked" for _ in victim_rows))
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
        visible_requests = _active_request_rows(report, details)[:policy.max_related_sessions]
        activity_sql = {(_session_id(row), str(_first(row, "sql_text", "query"))) for row in visible_requests}
        wait_rows = [{"session": (f"{row.get('inst_id')}:{row.get('sid')}"
                                  if is_oracle else _first(row, "pid", "processlist_id")),
                      "state": _first(row, "state", "state_name"),
                      "runtime": (row.get("duration_seconds") if is_oracle else
                                  _duration_ms(row.get("duration_ms"))),
                      "wait": _first(row, "wait_event", "wait_event_type"),
                      "sql": ("见当前活动 SQL" if (_session_id(row), str(row.get("sql"))) in activity_sql else
                              row.get("sql")),
                      "interpretation": ("已识别独立主动等待。" if is_postgresql else
                                         row.get("request_interpretation"))}
                     for row in active_waits]
        wait_states = tuple(
            "blocked" if str(row.get("session") or "") in blocking_participants else
            "clear" if blocking_complete and row.get("session") not in (None, "") else "partial"
            for row in wait_rows)
        table = _table(wait_rows, (("Session", ("session",)), ("Status", ("state",)),
                                   ("Request Runtime", ("runtime",)),
                                   *(((("Wait Event", ("wait",)), ("SQL", ("sql",)))
                                      if is_postgresql or is_oracle else ())),
                                   ("Interpretation", ("interpretation",))), wait_states)
        sections.append(ReportSection("active-waits", "Active Wait Statements",
                                      summary=("仅对无锁阻塞且精确匹配 pg_sleep(...) 的 PostgreSQL 查询分类；SQL 来自已脱敏采样。"
                                               if is_postgresql else
                                               "仅对无锁阻塞、完整 SQL 精确匹配 DBMS_SESSION.SLEEP(:1)，且等待事件为 PL/SQL lock timer 的请求分类。"
                                               if is_oracle else
                                               "仅对明确独立 SELECT SLEEP(...) 且状态为 User sleep 的语句分类。"),
                                      tables=(table,) if table else ()))
    if incident.root_cause_candidate:
        root_candidate = dict(incident.root_cause_candidate)
        if sqlserver and root_candidate.get("transaction_identity_verified") is True:
            root = next((row for row in _dict_rows(details.get("roots"))
                         if row.get("session_id") == root_candidate.get("session")), {})
            started = root.get("transaction_begin_time")
            if started is not None:
                root_candidate["transaction_start_time"] = _local_time(started)
                root_candidate["transaction_start_time_source"] = _SQLSERVER_TRANSACTION_BEGIN_SOURCE
        if sqlserver:
            root_candidate["reason"] = "候选会话来自当前采样的阻塞关系。"
            if root_candidate.get("status") is None:
                active = [row for row in _active_request_rows(report, details)
                          if row.get("session_id") == root_candidate.get("session")]
                if len(active) == 1:
                    root_candidate["status"] = active[0].get("status")
            root_candidate["root_cause_status"] = "NOT_ESTABLISHED"
            root_candidate["lock_acquiring_statement"] = "UNKNOWN"
        fields = tuple((_label(str(key)), display_slow_value(key, _human(value)) if sqlserver else _human(value))
                       for key, value in root_candidate.items())
        sections.append(ReportSection("root-cause", "Root Cause Candidate",
                                      severity=incident.severity, fields=fields))
    roots = _dict_rows(details.get("roots"))
    if report.instance.db_type.value == "oracle" and roots:
        context_rows = []
        for root in roots[:5]:
            context_rows.append({
                "session": root.get("session_key"),
                "current_sql_id": root.get("current_sql_id"),
                "current_sql": root.get("current_sql") or root.get("current_sql_reason"),
                "previous_sql_id": root.get("prev_sql_id"),
                "previous_sql": root.get("previous_sql") or root.get("previous_sql_reason"),
                "transaction": ("verified" if root.get("transaction_identity_verified") is True else
                                "observed but identity unverified" if
                                root.get("transaction_observed") is True else "unconfirmed"),
                "transaction_id": root.get("transaction_id"),
                "transaction_started": root.get("transaction_start_time") or "unknown",
                "transaction_start_source": root.get("transaction_start_time_source"),
                "transaction_start_time_text": root.get("transaction_start_time_text"),
                "transaction_age_seconds": root.get("transaction_age_seconds"),
                "action_status": (root.get("manual_action", {}).get("status")
                                  if isinstance(root.get("manual_action"), dict) else None),
                "action_reason": (root.get("manual_action", {}).get("reason")
                                  if isinstance(root.get("manual_action"), dict) else None),
                "sql_note": root.get("previous_sql_cause_note"),
            })
        table = _table(context_rows, (
            ("Root Session", ("session",)), ("Current SQL_ID", ("current_sql_id",)),
            ("Current SQL / Availability", ("current_sql",)),
            ("PREV_SQL_ID", ("previous_sql_id",)),
            ("Previous SQL / Availability", ("previous_sql",)),
            ("Transaction", ("transaction",)), ("XID", ("transaction_id",)),
            ("Start Time", ("transaction_started",)),
            ("Start Time Source", ("transaction_start_source",)),
            ("START_TIME Raw", ("transaction_start_time_text",)),
            ("Duration Seconds", ("transaction_age_seconds",)),
            ("Manual Action", ("action_status",)), ("Suppression Reason", ("action_reason",)),
            ("Context Note", ("sql_note",)),
        ), tuple("blocked" for _ in context_rows))
        sections.append(ReportSection(
            "oracle-root-sql-context", "Oracle Root Blocker SQL Context",
            summary="上一条 SQL 仅为会话上下文，不能证明其取得了当前锁。",
            tables=(table,) if table else ()))
    session_sections, _ = _session_and_request_sections(report, details, policy)
    sections.extend(session_sections)
    plan_sessions = {finding.session for finding in report.findings
                     if finding.title in {"CPU_PRESSURE", "IO_PRESSURE"}}
    plan_targets = dict.fromkeys(row["session_id"] for row in _active_request_rows(report, details)
                                 if sqlserver and blocking_complete and type(row.get("session_id")) is int
                                 and row["session_id"] > 0
                                 and _session_id(row) in plan_sessions and _session_id(row) not in blocking_participants
                                 and row.get("blocking_session_id") == 0 and row.get("plan_handle")
                                 and str(row.get("command") or "").upper() != "WAITFOR"
                                 and row.get("request_kind") != "ACTIVE_WAIT")
    if plan_targets:
        sections.append(ReportSection(
            "execution-plan-follow-up", "Execution Plan Follow-up",
            summary="下列独立请求已有 CPU 或 I/O 异常证据及计划标识。请求仍在运行时，可先复核会话再只读查看计划。",
            fields=tuple((f"会话 {sid}", f"dba diagnose {report.instance.id} plan --session {sid}")
                         for sid in plan_targets)))
    sections.extend(_transaction_sections(details, report.instance.db_type.value, policy, report, incident))
    return sections


def _mysql_recent_statements_section(report: DiagnosticReport,
                                     details: dict[str, Any]) -> ReportSection | None:
    recent = _dict_rows(details.get("recent_statements"))
    if report.instance.db_type.value != "mysql":
        return None
    samples = [sample for sample in details.get("samples", [])
               if isinstance(sample, dict)
               and sample.get("collector", sample.get("source")) == "recent_statements"]
    failed_sample = next((sample for sample in samples if sample.get("success") is False), None)
    if not recent and not samples:
        return None
    fields: list[tuple[str, str]] = []
    blocks: list[ReportCodeBlock] = []
    elapsed = details.get("recent_statements_elapsed_ms")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0:
        elapsed_text = "<0.1" if elapsed < 0.1 else f"{elapsed:.1f}"
    else:
        elapsed_text = "未记录"
    fields.append(("采集耗时（毫秒）", elapsed_text))
    if failed_sample is not None:
        fields.append(("采集状态", "FAILED"))
        error = failed_sample.get("error_message") or failed_sample.get("error_type")
        fields.append(("采集说明", str(error or "最近执行 SQL 采集失败；核心 Slow 证据保持独立。")))
    elif not recent:
        fields.append(("采集状态", "COLLECTED"))
        fields.append(("最近执行 SQL", "当前快照未发现可展示的最近执行 SQL。"))
    for target in recent[:5]:
        status = str(target.get("evidence_status", target.get("status", "UNKNOWN")))
        if status == "NOT_APPLICABLE":
            fields.append(("采集状态", "COLLECTED"))
            fields.append(("最近执行 SQL", "当前快照未发现可展示的最近执行 SQL。"))
            continue
        if status == "NOT_COLLECTED_CORE_INCOMPLETE":
            fields.append(("采集状态", "NOT_COLLECTED"))
            fields.append(("最近执行 SQL", "核心证据不完整，因此本次未采集最近执行 SQL。"))
            if target.get("reason"):
                fields.append(("采集说明", str(target["reason"])))
            continue
        session_id = target.get("session_id")
        status_label = (f"连接 {session_id} 最近 SQL 状态" if session_id is not None
                        else "最近 SQL 状态")
        fields.append((status_label, status))
        if status in {"NO_HISTORY_ROWS", "THREAD_NOT_CURRENT"}:
            fields.append(("最近执行 SQL", "当前快照未发现可展示的最近执行 SQL。"))
            continue
        if target.get("limit_reached"):
            fields.append((f"连接 {session_id} 采集上限" if session_id is not None else "采集上限",
                           "已达到每连接5条记录上限；是否还有更早事件未知"))
        elif target.get("truncated"):
            fields.append((f"连接 {session_id} 采集上限" if session_id is not None else "采集上限",
                           "存在未显示的历史记录"))
        if target.get("reason"):
            fields.append((f"连接 {session_id} 说明" if session_id is not None else "最近 SQL 说明",
                           str(target["reason"])))
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
    summary = ("最近执行 SQL；不保证属于当前事务，也不保证是持锁 SQL。SQL 文本已脱敏并折叠显示。"
               if blocks else "该区域只反映最近 SQL 证据的独立采集结果；空结果不表示连接失败。")
    return ReportSection(
        "mysql-recent-statements", "Recent SQL Evidence",
        summary=summary,
        fields=tuple(fields), code_blocks=tuple(blocks))


def _disk_growth_sections(details: dict[str, Any]) -> list[ReportSection]:
    sections = []
    for key, title in (("space", "Database Space"), ("log", "Transaction Log"),
                       ("other", "Other Storage Evidence")):
        value = details.get(key)
        if isinstance(value, dict):
            rows = [{"area": label, "value": _human(item)} for label, item in value.items()
                    if label != "wal_retention"]
        elif isinstance(value, list):
            rows = [{"area": _human(_first(row, "area", "category", "name", "database", "file")),
                     "value": _human(_first(row, "size_mb", "size_bytes", "value", "status"))}
                    for row in _dict_rows(value)]
        else:
            rows = []
        table = _table(rows, (("Area", ("area",)), ("Value", ("value",))))
        if table:
            sections.append(ReportSection(key, title, tables=(table,)))
    other = details.get("other")
    wal = other.get("wal_retention") if isinstance(other, dict) else None
    if isinstance(wal, dict):
        fields = tuple((label, _human(wal.get(key))) for key, label in (
            ("version", "Version"), ("in_recovery", "In recovery"),
            ("current_lsn", "Current LSN"), ("lsn_source", "LSN source"),
            ("slot_count_observed", "Observed slot count"),
            ("wal_keep_size", "WAL keep size"), ("max_slot_wal_keep_size", "Max slot WAL keep size"),
            ("max_wal_size", "Max WAL size")))
        for key, label in (("slots_evidence", "Slot collection"),
                           ("settings_evidence", "Settings collection"),
                           ("archive_evidence", "Archive collection")):
            evidence = wal.get(key)
            fields += ((label, _human(evidence.get("status")) if isinstance(evidence, dict) else "—"),)
            if isinstance(evidence, dict):
                fields += ((label + " started", _human(evidence.get("started_at"))),
                           (label + " finished", _human(evidence.get("finished_at"))))
        archive = wal.get("archive")
        if isinstance(archive, dict):
            fields += tuple((label, _human(archive.get(key))) for key, label in (
                ("archive_mode", "Archive mode"), ("archived_count", "Archived count"),
                ("failed_count", "Failed count"), ("last_archived_time", "Last archive"),
                ("last_failed_time", "Last failure"), ("stats_reset", "Archive stats reset")))
        slots = _dict_rows(wal.get("slots"))
        slot_evidence = wal.get("slots_evidence")
        incomplete_slots = (isinstance(slot_evidence, dict)
                            and slot_evidence.get("status") == "TRUNCATED")
        slots_collected = (isinstance(slot_evidence, dict)
                           and slot_evidence.get("status") in {"AVAILABLE", "EMPTY", "TRUNCATED"})
        slot_columns = (("Slot", "slot_name"), ("Type", "slot_type"), ("Active", "active"),
                        ("PID", "active_pid"), ("Restart LSN", "restart_lsn"),
                        ("Confirmed LSN", "confirmed_flush_lsn"), ("WAL status", "wal_status"),
                        ("Safe WAL bytes", "safe_wal_size"),
                        ("LSN distance bytes", "retained_wal_estimate_bytes"))
        slot_table = (ReportTable(tuple(label for label, _ in slot_columns),
                                  tuple(tuple(_human(row.get(key)) for _, key in slot_columns)
                                        for row in slots[:50])) if slots else None)
        sections.append(ReportSection("wal-retention", "WAL Retention and Replication Slots",
            summary=(str(wal.get("limitations", "")) + " " + str(wal.get("settings_units_note", ""))
                     + " " + str(wal.get("safe_wal_size_note", "")) + " "
                     + ("Slot result truncated; total slot count is unknown. " if incomplete_slots else "")
                     + (_omitted(len(slots), min(len(slots), 50), "slots") if slots_collected
                        else "Slot count unknown; collection unavailable.")),
            fields=fields, tables=(slot_table,) if slot_table else ()))
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
                rendered = rendered[:policy.max_sql_chars] + " [语句因报告长度上限而省略]"
            summary_fields.append((label, rendered))
    if details.get("plan_available") is False:
        sections.append(ReportSection(
            "plan-availability", "Execution Plan Availability", severity="WARNING",
            summary=str(display_message(details.get("note") or "Execution plan unavailable; no plan evidence was returned."))))
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
                                      summary="请使用 SQL Server Management Studio 打开 SQLPLAN 计划文件。",
                                      fields=(("Artifact", plan_artifact),)))
    return sections


def _oracle_plan_sections(details: dict[str, Any], policy: HumanReportPolicy) -> list[ReportSection]:
    request_value = details.get("request")
    request = request_value if isinstance(request_value, dict) else {}
    identity_fields = (("SQL_ID", "sql_id"), ("Child Number", "child_number"),
                       ("Instance ID", "inst_id"), ("Container ID", "con_id"),
                       ("Requested Plan Hash", "requested_plan_hash_value"),
                       ("Current Plan Hash", "current_plan_hash_value"))
    fields = tuple((label, _human(request[key])) for label, key in identity_fields
                   if key in request)
    fields += tuple((label, _human(details[key])) for label, key in (
        ("Plan Source", "source"), ("Plan Status", "plan_status"),
        ("Identity Validation", "identity_validation"), ("Completeness", "completeness"),
        ("Data Visibility", "data_visibility"),
        ("Operator Count", "operator_count"), ("Maximum Operators", "max_operators"),
    ) if key in details)
    sections = [ReportSection("oracle-plan-identity", "Oracle Cached Plan Identity", fields=fields)]
    note_rows: list[dict[str, Any]] = [{"item": "Estimate semantics", "value": details.get("statistics_note")},
                 {"item": "Adaptive plan", "value": details.get("adaptive_plan_note")},
                 {"item": "Cursor generation", "value": "仅能确认当前共享池中的游标身份；未采集载入代次。"},
                 {"item": "Plan source", "value": "证据来自当前 V$SQL_PLAN 缓存记录；未执行业务 SQL 或 EXPLAIN。"}]
    note_rows.extend({"item": "Limitation", "value": item}
                     for item in details.get("limitations", []) if isinstance(item, str))
    notes = _table(note_rows, (("Item", ("item",)), ("Details", ("value",))))
    if notes:
        sections.append(ReportSection("oracle-plan-limitations", "Plan Semantics and Limitations",
                                      severity="WARNING", tables=(notes,)))
    if details.get("plan_available") is False:
        sections.append(ReportSection("oracle-plan-availability", "Cached Plan Availability",
            severity="WARNING", summary=str(display_message(details.get("note") or "A complete cached plan is unavailable."))))
    nodes = _dict_rows(details.get("top_operators"))
    shown = nodes[:500]
    rows = [{"id": row.get("id"), "parent": row.get("parent_id"), "depth": row.get("depth"),
             "operation": ("  " * min(row.get("depth", 0), 20)) +
                          " ".join(str(value) for value in (row.get("operation"), row.get("options")) if value),
             "object": ".".join(str(row[key]) for key in ("object_owner", "object_name")
                                if row.get(key) is not None),
             "cardinality": row.get("estimated_rows"), "cost": row.get("estimated_cost"),
             "bytes": row.get("estimated_bytes")} for row in shown]
    table = _table(rows, (("ID", ("id",)), ("Parent ID", ("parent",)), ("Depth", ("depth",)),
                          ("Operation / Options", ("operation",)), ("Object", ("object",)),
                          ("Estimated Rows", ("cardinality",)), ("Estimated Cost", ("cost",)),
                          ("Estimated Bytes", ("bytes",))))
    if table:
        sections.append(ReportSection("oracle-plan-tree", "Cached Plan Tree",
            summary=_omitted(len(nodes), len(shown), "plan nodes"), tables=(table,)))
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
                                  summary=("未配置复制，不适用。"
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
                   "candidate": "候选" if item.candidate else "证据",
                   "message": item.message,
                   "vendor_message": item.vendor_message or "—"} for item in visible_issues]
    issue_table = _table(issue_rows, (("Issue", ("code",)), ("Severity", ("severity",)),
                                      ("Classification", ("candidate",)),
                                      ("Finding", ("message",)), ("Vendor Error", ("vendor_message",))))
    sections.append(ReportSection("replication-issues", "Issues and Vendor Evidence",
                                  summary=_omitted(len(issues), len(visible_issues), "issues"),
                                  tables=(issue_table,) if issue_table else ()))
    actions_rows = [{"recommendation": item.message,
                     "approval": "需要 DBA 复核" if item.requires_dba_review else "否"}
                    for item in actions[:policy.max_replication_rows]]
    actions_table = _table(actions_rows, (("Recommended DBA Action", ("recommendation",)),
                                         ("Review", ("approval",))))
    sections.append(ReportSection("replication-actions", "Recommended Actions",
                                  summary="Toolkit 未执行操作；不会自动切换、提升、重启、重置或修复。",
                                  tables=(actions_table,) if actions_table else ()))
    return sections


def _diagnostic_timing_section(timings: Mapping[str, object], placeholders: Mapping[str, str]) -> ReportSection:
    fields = []
    for key, label in (
        ("time_to_first_summary_ms", "首次终端摘要耗时"),
        ("collection_ms", "采集阶段耗时"),
        ("full_collection_ms", "初始化至诊断组装耗时"),
        ("report_write_duration_ms", "HTML 文件写入耗时"),
        ("total_report_ms", "诊断至 HTML 写入总耗时"),
    ):
        value = timings.get(key, timings.get("report_write_ms") if key == "report_write_duration_ms" else None)
        fields.append((label, placeholders.get(key, f"{value} ms" if type(value) is int else "未测量")))
    return ReportSection(
        "diagnostic-timing", "诊断流程耗时", collapsed=True, fields=tuple(fields),
        summary="采集阶段从应急采集开始计至采集结束，含期间交错的数据处理和分诊。"
                "完整流程从加载 Inventory 前计至诊断组装结束，含连接准备和版本探测。"
                "首次终端摘要也从加载 Inventory 前起算。写入从打开 HTML 文件计至正文刷新完成。"
                "总耗时包含完整流程、HTML 组装和写入，截至同一刷新点。正耗时不足 1 毫秒时显示为 1 毫秒。"
                "计时回填、文件关闭和 JSON 输出在计时终点之后。",
    )


def build_report_model(report: DiagnosticReport, incident: IncidentSummary,
                       policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
                       plan_artifact: str | None = None, *,
                       timing_placeholders: Mapping[str, str] | None = None) -> ReportModel:
    """Create a bounded presentation model; plan XML is never embedded in HTML."""
    instance = report.instance
    details = getattr(report, "details", {})
    raw_version = details.get("version", "UNKNOWN")
    version = (postgresql_version_label(raw_version)
               if instance.db_type.value == "postgresql" else _human(raw_version))
    format_capture = _local_time if report.scenario == "slow" else str
    metadata_items: list[tuple[str, str]] = [("Database ID", instance.id), ("Instance", instance.name),
                ("Database Type", instance.db_type.value), ("Version", version),
                ("Scenario", _label(report.scenario)), ("Generated At", _local_time(report.diagnosed_at)),
                ("Environment", instance.environment or "UNKNOWN"),
                ("Data Completeness", incident.data_completeness),
                ("Capture Started", format_capture(details.get("capture_started_at", "N/A"))),
                ("Triage Completed", format_capture(details.get("triage_completed_at", "N/A"))),
                ("Capture Finished", format_capture(details.get("capture_finished_at", "N/A"))),
                ("Snapshot Consistency", str(details.get("snapshot_consistency", "BEST_EFFORT")))]
    if instance.db_type.value == "postgresql":
        raw_version_num = details.get("server_version_num")
        if raw_version_num is None:
            raw_version_num = postgresql_server_version_num(raw_version)
        if raw_version_num is not None:
            metadata_items.append(("server_version_num (raw)", _human(raw_version_num)))
    metadata: tuple[tuple[str, str], ...] = tuple(metadata_items)
    if report.scenario == "slow":
        # Keep the native counting unit when the shared incident contract aliases a metric.
        concurrency = details.get("concurrency") or {}
        impact = dict(incident.impact)
        for canonical, native_keys in (("blocked_requests", ("blocked_sessions", "blocked_statements")),
                                       ("active_requests", ("active_sql_sessions", "active_statements"))):
            if canonical in impact and canonical not in concurrency:
                native = next((key for key in native_keys if concurrency.get(key) == impact[canonical]), None)
                if native is not None:
                    impact[native] = impact.pop(canonical)
        if instance.db_type.value != "sqlserver" and _collector_complete(details, "transactions"):
            rows = details.get("transactions")
            if isinstance(rows, list):
                impact["open_transactions"] = len(rows)
        incident = replace(incident, impact=impact)
        sections = _slow_sections(report, incident, details, policy)
    elif report.scenario == "active-sql":
        sections = active_sql_sections(details.get("active_sql"))
    elif report.scenario == "top-sql":
        top_sql_section = _top_sql_section(details.get("top_sql"))
        sections = [top_sql_section] if top_sql_section is not None else []
    elif report.scenario == "memory-grant":
        sections = memory_grant_sections(details.get("memory_grant"))
    elif report.scenario == "plan":
        if instance.db_type.value == "oracle":
            sections = _oracle_plan_sections(details, policy)
        else:
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
    timings = details.get("diagnostic_timings_ms")
    if isinstance(timings, Mapping):
        sections.append(_diagnostic_timing_section(timings, timing_placeholders or {}))
    return ReportModel("数据库应急诊断报告", metadata, incident, tuple(sections), report.scenario)


def _map_section(section: ReportSection, *, max_chars: int, drop_sql: bool = False) -> ReportSection:
    blocks = []
    for block in section.code_blocks:
        if drop_sql and block.language.lower() == "sql":
            continue
        content = block.content
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n[{len(content) - max_chars} characters omitted by report limit.]"
        blocks.append(replace(block, content=content))
    def clip_fields(fields: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        return tuple((label, value[:max_chars] + f" [{len(value) - max_chars} 字符已省略]"
                      if len(value) > max_chars else value) for label, value in fields)
    tables = tuple(ReportTable(table.headers, tuple(
        tuple(value[:max_chars] + f" [{len(value) - max_chars} characters omitted]"
              if len(value) > max_chars else value for value in row)
        for row in table.rows), getattr(table, "cell_links", ()), table.row_states)
        for table in section.tables)
    return replace(section, title=section.title[:max_chars], summary=section.summary[:max_chars],
                   severity=section.severity[:max_chars], fields=clip_fields(section.fields), tables=tables,
                   detail_fields=clip_fields(section.detail_fields),
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
        match = re.search(r"共(\d+)条，显示(\d+)条，省略(\d+)条。", section.summary)
        total = int(match.group(1)) if match else displayed
        blocks = len(section.code_blocks)
        details.append(f"{display_label(section.title)}：共 {total} 行，显示 0 行，省略 {total} 行，另省略 {blocks} 个文本块")
    return "；".join(details) or "未省略完整报告区块。"


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
                           summary="报告内容已按人工报告大小上限收敛。"
                                   + _dropped_evidence(model.sections, soft_drop)
                                   + "结构化诊断数据请使用 --json 查看。")
    compact = replace(model, sections=(sections[0], notice, *sections[1:]) if sections else (notice,))
    page = render(compact)
    if len(page.encode("utf-8")) <= policy.hard_report_bytes:
        return page

    critical_ids = {"immediate-recovery", "root-cause", "blocking",
                    "collection-quality", "diagnostic-timing", "missing-evidence", "report-budget",
                    "replication-summary", "replication-issues"}
    hard_sections = tuple(_map_section(section, max_chars=1024, drop_sql=True)
                          for section in compact.sections if section.section_id in critical_ids)
    hard_dropped = {section.section_id for section in compact.sections
                    if section.section_id not in critical_ids}
    hard_notice = ReportSection("report-budget", "Report Evidence Limit",
                                summary="报告达到大小上限，已收敛部分证据内容。"
                                        + _dropped_evidence(compact.sections, hard_dropped)
                                        + "结构化诊断数据请使用 --json 查看。")
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
    emergency = replace(hard, title="DBA Toolkit 诊断报告", metadata=(),
                        incident_summary=_bounded_incident(hard.incident_summary, 64),
                        sections=tuple(_map_section(section, max_chars=64, drop_sql=True)
                                       for section in hard.sections
                                       if section.section_id in {"immediate-recovery", "root-cause", "blocking",
                                                                 "collection-quality", "diagnostic-timing",
                                                                 "report-budget"}))
    return render(emergency)
