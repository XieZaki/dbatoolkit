"""Bounded, human-oriented diagnostic report model and evidence presenters."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from dba.active_sql_report import active_sql_sections
from dba.core import DiagnosticReport
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
    postgresql_server_version_num,
    postgresql_version_label,
    report_model_label,
    report_model_value,
    sqlserver_transaction_label,
)


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


def _top_sql_section(value: Any,
                     policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
                     *, integrated: bool = False) -> ReportSection | None:
    """Present cumulative Top SQL separately from current Slow evidence."""
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
    code_blocks: list[ReportCodeBlock] = []
    sqlserver_reason_column = integrated and payload.get("database_engine") == "sqlserver"
    for item in raw_items[:10]:
        if not isinstance(item, dict) or item.get("rank") is None:
            continue
        identity = item.get("sql_identity")
        if isinstance(identity, dict):
            native_fields = identity.get("fields", {})
            if not isinstance(native_fields, dict):
                native_fields = {}
            # Only native identity fields are rendered; SQL text and arbitrary payload keys are excluded.
            native_keys = {"sql_handle", "plan_handle", "query_hash", "query_plan_hash",
                           "statement_start_offset", "statement_end_offset", "database_id",
                           "plan_generation_num", "queryid", "dbid", "userid", "pid",
                           "backend_start", "digest", "toplevel", "schema_name",
                           "processlist_id", "thread_id", "sql_id", "child_number",
                           "inst_id", "plan_hash_value"}
            is_oracle = identity.get("database_engine") == "oracle"
            if is_oracle:
                native_keys.add("con_id")
            native = ", ".join(f"{key}={_human(val)[:100]}" for key, val in native_fields.items()
                                if key in native_keys)
            if is_oracle:
                plan_identity = item.get("plan_identity")
                plan_fields = (plan_identity.get("fields", {})
                               if isinstance(plan_identity, dict)
                               and plan_identity.get("database_engine") == "oracle"
                               and plan_identity.get("kind") == "PLAN" else {})
                if not isinstance(plan_fields, dict):
                    plan_fields = {}
                plan_hash = plan_fields.get("plan_hash_value")
                if type(plan_hash) is not int or plan_hash <= 0:
                    plan_hash = None
                native += f"{', ' if native else ''}plan_hash_value={_human(plan_hash)}"
            identity_text = f"{identity.get('database_engine', 'UNKNOWN')}:{native or '身份不可用'}"[:256]
        else:
            identity_text = "身份不可用"
        database_name = item.get("database_name")
        if not isinstance(database_name, str) or not database_name.strip():
            database_name = "未知"
        primary_identity = identity_text
        if isinstance(identity, dict) and identity.get("database_engine") == "sqlserver":
            primary_identity = (
                f"数据库={database_name}；语句偏移="
                f"{native_fields.get('statement_start_offset', '未知')}-"
                f"{native_fields.get('statement_end_offset', '未知')}"
            )
        elif isinstance(identity, dict) and identity.get("database_engine") == "postgresql":
            primary_identity = f"数据库 ID={native_fields.get('dbid', '未知')}；Query ID={native_fields.get('queryid', '未知')}"
        elif isinstance(identity, dict) and identity.get("database_engine") == "mysql":
            primary_identity = f"Schema={native_fields.get('schema_name') or '未知'}"
        elif isinstance(identity, dict) and identity.get("database_engine") == "oracle":
            primary_identity = (f"SQL ID={native_fields.get('sql_id', '未知')}；"
                                f"Child={native_fields.get('child_number', '未知')}；"
                                f"实例={native_fields.get('inst_id', '未知')}")
        primary_identity = primary_identity[:180]
        if identity_text != "身份不可用":
            code_blocks.append(ReportCodeBlock(
                f"排名 {item.get('rank')} — 原生身份与执行计划标识",
                identity_text, "text", True))
        association = item.get("sql_text_association") if integrated else None
        association_status = (association.get("status") if isinstance(association, dict)
                              else "NOT_ATTEMPTED")
        base_row = (str(item.get("rank")), primary_identity, _human(item.get("metric_value")))
        if sqlserver_reason_column:
            reason_values = ([association.get("reason"), association.get("request_state")]
                             if isinstance(association, dict) else [])
            reason_text = "；".join(display_label(value) for value in reason_values
                                  if isinstance(value, str) and value != association_status)
            rows.append((*base_row, _human(association_status), reason_text or "—"))
        elif integrated:
            rows.append((*base_row, _human(association_status)))
        else:
            rows.append(base_row)
        if integrated and isinstance(association, dict) and association_status == "MATCHED":
            sql_text = association.get("sql_text")
            if isinstance(sql_text, str) and sql_text:
                clipped = sql_text[:policy.max_sql_chars]
                if len(sql_text) > policy.max_sql_chars:
                    clipped += "\n[SQL 文本因人工报告长度上限而省略。]"
                code_blocks.append(ReportCodeBlock(
                    f"Top SQL 排名 {item.get('rank')} — 与慢 SQL 精确匹配",
                    str(redact(clipped)), "sql", True))
    headers = (("Rank", "SQL identity", "Metric value", "SQL text association", "Association reason")
               if sqlserver_reason_column else
               ("Rank", "SQL identity", "Metric value", "SQL text association") if integrated else
               ("Rank", "SQL identity", "Metric value"))
    table = ReportTable(headers, tuple(rows)) if rows else None
    if payload.get("collection_status") == "COLLECTED" and not raw_items:
        summary = "已完成采集，但没有匹配记录；不生成虚假排名。"
    elif not rows:
        summary = "当前没有可用的已排名 Top SQL 记录；状态和限制见契约字段。"
    else:
        summary = _omitted(len(raw_items), len(rows), "Top SQL rows")
    if payload.get("database_engine") == "sqlserver" and rows:
        summary = ("SQL Server 的数据库名称与语句偏移用于快速定位；完整 SQL Handle、Hash、Plan Handle "
                   "保留在下方可展开的原生身份详情中。 " + summary)
    if integrated:
        summary = ("Top SQL 排名反映数据库统计视图中的累计表现，不代表当前故障根因。 "
                   "它与当前活动会话、等待和阻塞证据分开呈现。 "
                   "此处完整性仅描述 Top SQL 附加快照，不改变 Slow 核心证据完整性。 " + summary)
    return ReportSection("top-sql", "Top SQL", summary=summary, fields=fields,
                         tables=(table,) if table else (), code_blocks=tuple(code_blocks))


def _top_sql_payload(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return value if isinstance(value, dict) else None


def _top_sql_quality_row(value: Any, collector: str | None = None) -> dict[str, str] | None:
    payload = _top_sql_payload(value)
    if payload is None:
        return None
    collection_status = str(payload.get("collection_status") or "UNKNOWN")
    completeness = str(payload.get("completeness") or "UNKNOWN")
    visibility = str(payload.get("data_visibility") or "UNKNOWN")
    if collection_status == "COLLECTED":
        if completeness == "COMPLETE" and visibility == "COMPLETE":
            status = "COMPLETE"
        elif completeness == "PARTIAL" or visibility == "PARTIAL":
            status = "PARTIAL"
        else:
            status = "UNKNOWN"
    else:
        status = collection_status
    engine = payload.get("database_engine")
    name = collector or (f"{engine}_top_sql" if isinstance(engine, str) else "top_sql")
    impact = (f"采集状态={_human(collection_status)}；证据完整性={_human(completeness)}；"
              f"数据可见性={_human(visibility)}。")
    limitations = payload.get("limitations")
    if limitations:
        impact += f"限制原因：{_human(limitations)}。"
    return {"collector": name, "status": status, "impact": impact}


def _top_sql_integration_quality_row(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    raw_status = value.get("status")
    if not isinstance(raw_status, str):
        return None
    impact = value.get("reason") or value.get("error_type") or "未采集 Top SQL。"
    return {"collector": "top_sql", "status": raw_status, "impact": _human(impact)}


def _top_sql_integration_section(value: Any) -> ReportSection | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str):
        return None
    reason = value.get("reason")
    error_type = value.get("error_type")
    fields = [("采集状态", _human(status))]
    for key, label in (("database_engine", "数据库类型"),
                       ("database_version", "数据库版本"),
                       ("ranking_metric", "指标")):
        if isinstance(value.get(key), str):
            fields.append((label, value[key][:128]))
    if isinstance(reason, str):
        fields.append(("原因", str(display_message(reason))))
    if isinstance(error_type, str):
        fields.append(("错误类型", error_type[:128]))
    summary = ("Top SQL 作为附加证据未采集；Slow 核心证据保持原样。"
               if status == "NOT_COLLECTED" else
               "Top SQL 采集器返回错误；慢 SQL 核心诊断不受影响。"
               if status == "FAILED" else
               "Top SQL 附加采集未成功；Slow 核心诊断不受影响。")
    return ReportSection("top-sql", "Top SQL", summary=summary, fields=tuple(fields))


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
    sql = _first(row, "sql_text", "query", "sql_fulltext", "current_sql", "previous_sql")
    if not isinstance(sql, str) or not sql:
        return None
    clipped = sql[:policy.max_sql_chars]
    is_truncated = clipped != sql or bool(_first(row, "sql_text_truncated", "query_truncated"))
    title = f"会话 {_session_id(row) or '未知'} {label}"
    if is_truncated:
        title += "（已截断）"
        clipped += "\n[SQL 文本因人工报告长度上限而省略。]" if len(sql) > policy.max_sql_chars else ""
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
                                   ("Longest Block", ("longest",))),
                        row_states=tuple("blocked" for _ in root_rows))
    summaries = [ReportSection(
        "root-blockers", "Root Blockers",
        summary=_omitted(len(roots), len(root_rows), "root blockers"),
        tables=(root_table,) if root_table else ())]
    tree_states = tuple("partial" if row["role"] == "Display summary" and
                        "省略" in str(row.get("elapsed", "")) else "blocked"
                        for row in tree_rows)
    tree_table = _table(tree_rows, (("Root", ("root",)), ("Session", ("session",)),
                                   ("Role", ("role",)), ("Direct Blocker", ("parent",)),
                                   ("Wait", ("wait",)), ("Wait Resource", ("wait_resource",)),
                                   ("Elapsed", ("elapsed",)), ("Login", ("login",))),
                        row_states=tree_states)
    tree = ReportSection("blocking", "Blocking Analysis",
                         summary="当前有界采样观察到的阻塞关系；直接与间接受影响会话分别计数。"
                         if tree_table else "当前采样未观察到阻塞关系。",
                         tables=(tree_table,) if tree_table else ())
    return summaries + [tree], related


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
    blocking_complete, blocking_participants = _blocking_state(report, details)
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
    runtime_states = tuple(
        "blocked" if str(row["session"]) in blocking_participants else
        "clear" if blocking_complete and str(row["session"]) in request_by_id else "partial"
        for row in runtime_rows)
    runtime_table = _table(runtime_rows, tuple(runtime_columns), runtime_states)
    if runtime_table:
        runtime_summary = ("MySQL Performance Schema 提供当前语句耗时和可见等待事件；等待 consumer 未启用时等待详情不可用。"
                           if report.instance.db_type.value == "mysql" else
                           "PostgreSQL pg_stat_activity 提供当前 backend 的语句耗时与 wait event；不表示累计 CPU 或 I/O 计数。"
                           if report.instance.db_type.value == "postgresql" else
                           "Oracle V$SESSION/V$SQL 当前 SQL 与等待信息；耗时为本次执行时长，V$SQL 资源计数在本报告中不投影。"
                           if report.instance.db_type.value == "oracle" else
                           "CPU、logical reads、reads、writes 为 DMV 当前累计值；单次采样为零不代表历史没有资源消耗。")
        current_sql = tuple(
            block for sid in sorted(related, key=session_order)
            if (current_request := request_by_id.get(sid)) is not None
            if (block := _sql_block(current_request, policy, "Current SQL")) is not None)
        sections.append(ReportSection(
            "request-runtime", "Request Runtime",
            summary=runtime_summary,
            tables=(runtime_table,), code_blocks=current_sql))

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
    wait_states = tuple(
        "blocked" if str(row.get("session") or "") in blocking_participants else
        "clear" if blocking_complete and row.get("session") not in (None, "") else "partial"
        for row in shown_waits)
    wait_table = _table(shown_waits, (("Session", ("session",)), ("Wait", ("wait",)),
                                      ("Duration", ("duration",)), ("Blocker", ("blocker",))),
                        wait_states)
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


def _transaction_section(details: dict[str, Any], database_type: str,
                         policy: HumanReportPolicy,
                         report: DiagnosticReport) -> ReportSection | None:
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
                       "begin": _local_time(_first(row, "transaction_start_time",
                                                     "transaction_begin_time", "begin_time",
                                                     "trx_started", "xact_start"))
                                if _first(row, "transaction_start_time", "transaction_begin_time",
                                          "begin_time", "trx_started", "xact_start") is not None else "—",
                       "begin_source": row.get("transaction_start_time_source"),
                       "age": _duration_seconds(age), "type": kind,
                       "state": _human(_first(row, "trx_state")) if database_type == "mysql"
                       else state})
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
                           ("Type", ("type",)), ("State", ("state",))), transaction_states)
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
        impact = (sample.get("error_message") or sample.get("error_type") or
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
    if report.scenario == "slow":
        top_sql_row = _top_sql_quality_row(details.get("top_sql"))
        if top_sql_row is None:
            top_sql_row = _top_sql_integration_quality_row(details.get("top_sql_integration"))
        if top_sql_row is not None and top_sql_row["collector"] not in sampled:
            quality.append(top_sql_row)
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


def _slow_sections(report: DiagnosticReport, incident: IncidentSummary,
                   details: dict[str, Any], policy: HumanReportPolicy) -> list[ReportSection]:
    sections: list[ReportSection] = [_recovery_section(incident, details, report.scenario,
                                                       report.instance.db_type.value, report)]
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
                            tuple("partial" for _ in priority_rows))
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
                        f"[{item.get('integrity', 'UNKNOWN')} @ {item.get('observed_at') or 'unknown'}]"
                        for item in class_refs[:2]) or "source unavailable"
                    evidence_items.append(
                        f"{label} ({conclusion.get('rule_id', 'UNKNOWN')}, "
                        f"{conclusion.get('status', 'UNKNOWN')}): {sources}")
            reference_text = "; ".join(evidence_items) or "; ".join(
                f"{item.get('collector', 'unknown')}:{item.get('source_path', 'unknown')} "
                f"[{item.get('integrity', 'UNKNOWN')} @ {item.get('observed_at') or 'unknown'}]"
                for item in references[:4])
            triage_rows.append({
                "identity": _human(assessment.get("native_identity")),
                "classifications": ", ".join(assessment.get("classifications", [])) or "UNKNOWN",
                "status": assessment.get("classification_status", "UNKNOWN"),
                "rules": ", ".join(assessment.get("rule_ids", [])) or "—",
                "evidence": reference_text or "No evidence reference",
                "uncertainty": "; ".join(assessment.get("uncertainty_reasons", [])) or "—",
            })
        triage_table = _table(triage_rows, (
            ("Native session identity", ("identity",)),
            ("Classification labels", ("classifications",)),
            ("Evidence status", ("status",)), ("Rule IDs", ("rules",)),
            ("Evidence source / time / integrity", ("evidence",)),
            ("Uncertainty", ("uncertainty",)),
        ))
        triage_summary = (f"快照时间：{triage.get('snapshot_time') or '未知'}；"
                          f"总体状态：{_human(triage.get('status', 'UNKNOWN'))}；"
                          f"根因状态：{_human(triage.get('root_cause_status', 'NOT_ESTABLISHED'))}。"
                          "核查排序不等于已确认根因。")
        sections.append(ReportSection(
            "performance-triage", "Performance Triage", summary=triage_summary,
            fields=tuple(("Evidence limitation", _human(reason))
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
        wait_rows = [{"session": (f"{row.get('inst_id')}:{row.get('sid')}"
                                  if is_oracle else _first(row, "pid", "processlist_id")),
                      "state": _first(row, "state", "state_name"),
                      "runtime": (row.get("duration_seconds") if is_oracle else
                                  _duration_ms(row.get("duration_ms"))),
                      "wait": _first(row, "wait_event", "wait_event_type"),
                      "sql": row.get("sql"),
                      "interpretation": row.get("request_interpretation")}
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
        fields = tuple((_label(str(key)), _human(value))
                       for key, value in incident.root_cause_candidate.items())
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
    plan_commands = [(f"Session {_session_id(row)}",
                      f"dba diagnose {report.instance.id} plan --session {_session_id(row)}")
                     for row in _dict_rows(details.get("long_running"))
                     if type(row.get("session_id")) is int and row["session_id"] > 0]
    if report.instance.db_type.value == "sqlserver" and plan_commands:
        sections.append(ReportSection(
            "execution-plan-follow-up", "Execution Plan Follow-up",
            summary="当前 CLI 支持按会话采集执行计划。仅当目标会话仍有可用请求、权限和版本能力满足时才可成功；执行前先重新核对会话。",
            fields=tuple(plan_commands)))
    transaction = _transaction_section(details, report.instance.db_type.value, policy, report)
    if transaction:
        sections.append(transaction)
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


def build_report_model(report: DiagnosticReport, incident: IncidentSummary,
                       policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
                       plan_artifact: str | None = None) -> ReportModel:
    """Create a bounded presentation model; plan XML is never embedded in HTML."""
    instance = report.instance
    details = getattr(report, "details", {})
    raw_version = details.get("version", "UNKNOWN")
    version = (postgresql_version_label(raw_version)
               if instance.db_type.value == "postgresql" else _human(raw_version))
    metadata_items: list[tuple[str, str]] = [("Database ID", instance.id), ("Instance", instance.name),
                ("Database Type", instance.db_type.value), ("Version", version),
                ("Scenario", _label(report.scenario)), ("Generated At", _local_time(report.diagnosed_at)),
                ("Environment", instance.environment or "UNKNOWN"),
                ("Data Completeness", incident.data_completeness),
                ("Capture Started", str(details.get("capture_started_at", "N/A"))),
                ("Triage Completed", str(details.get("triage_completed_at", "N/A"))),
                ("Capture Finished", str(details.get("capture_finished_at", "N/A"))),
                ("Snapshot Consistency", str(details.get("snapshot_consistency", "BEST_EFFORT")))]
    if instance.db_type.value == "postgresql":
        raw_version_num = details.get("server_version_num")
        if raw_version_num is None:
            raw_version_num = postgresql_server_version_num(raw_version)
        if raw_version_num is not None:
            metadata_items.append(("server_version_num (raw)", _human(raw_version_num)))
    metadata: tuple[tuple[str, str], ...] = tuple(metadata_items)
    if report.scenario == "slow":
        sections = _slow_sections(report, incident, details, policy)
    elif report.scenario == "active-sql":
        sections = active_sql_sections(details.get("active_sql"))
    elif report.scenario == "top-sql":
        top_sql_section = _top_sql_section(details.get("top_sql"), policy)
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
    top_sql = None if report.scenario == "top-sql" else _top_sql_section(
        details.get("top_sql"), policy, integrated=report.scenario == "slow")
    if top_sql is None and report.scenario == "slow":
        top_sql = _top_sql_integration_section(details.get("top_sql_integration"))
    if top_sql is not None:
        sections.append(top_sql)
    findings = _finding_section(report)
    if findings:
        sections.append(findings)
    sections.append(_quality_section(report, incident, details))
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
    fields = tuple((label, value[:max_chars] + f" [{len(value) - max_chars} characters omitted]"
                    if len(value) > max_chars else value)
                   for label, value in section.fields)
    tables = tuple(ReportTable(table.headers, tuple(
        tuple(value[:max_chars] + f" [{len(value) - max_chars} characters omitted]"
              if len(value) > max_chars else value for value in row)
        for row in table.rows), getattr(table, "cell_links", ()), table.row_states)
        for table in section.tables)
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

    critical_ids = {"immediate-recovery", "root-cause", "root-blockers", "blocking",
                    "collection-quality", "missing-evidence", "report-budget",
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
                                       if section.section_id in {"immediate-recovery", "root-cause",
                                                                 "root-blockers", "blocking",
                                                                 "collection-quality", "report-budget"}))
    return render(emergency)
