"""Shared report payload and human renderer for current SQL Server requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dba.core import DiagnosticReport
from dba.logging import redact

if TYPE_CHECKING:
    from dba.reporting.model import ReportSection


@dataclass(frozen=True)
class ActiveSQLDiagnosticReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


_RANKING_SECTIONS = (
    ("cpu", "active-sql-cpu", "按 CPU 时间排序的当前 SQL", "CPU 时间"),
    ("logical-reads", "active-sql-logical-reads", "按逻辑读排序的当前 SQL", "逻辑读"),
    ("elapsed", "active-sql-elapsed", "按运行耗时排序的当前 SQL", "运行耗时"),
)


def active_sql_sections(value: Any) -> list[ReportSection]:
    """Build bounded ranking tables and one text block per stable SQL Ref."""
    from dba.reporting.model import ReportCodeBlock, ReportSection, ReportTable
    from dba.reporting.normalizers import display_value

    data = value if isinstance(value, dict) else {}
    rankings = data.get("rankings", {})
    statements = data.get("sql_texts", [])
    sections: list[ReportSection] = []
    for sort, section_id, title, metric_header in _RANKING_SECTIONS:
        raw_rows = rankings.get(sort, []) if isinstance(rankings, dict) else []
        table_rows: list[tuple[str, ...]] = []
        links: list[tuple[int, int, str]] = []
        if isinstance(raw_rows, list):
            for item in raw_rows:
                if not isinstance(item, dict):
                    continue
                sql_ref = item.get("sql_ref")
                metric_value = item.get("metric_value")
                metric_unit = item.get("metric_unit")
                metric = (f"{_safe(metric_value)} {metric_unit}" if metric_value is not None
                          and isinstance(metric_unit, str) else "UNAVAILABLE")
                row = (str(sql_ref or "UNAVAILABLE"), _safe(item.get("session_id")),
                       _safe(item.get("request_id")), metric,
                       _safe(item.get("wait_type") or "—"),
                       _safe(item.get("wait_time_ms")),
                       _safe(item.get("blocking_session_id") or "—"))
                row_index = len(table_rows)
                table_rows.append(row)
                if isinstance(sql_ref, str) and _valid_sql_ref(sql_ref):
                    links.append((row_index, 0, f"#sql-text-{sql_ref.casefold()}"))
        table = (ReportTable(("SQL 引用", "会话 ID", "请求 ID", metric_header,
                              "等待事件", "等待时长（毫秒）", "阻塞会话"),
                             tuple(table_rows), tuple(links))
                 if table_rows else None)
        summary = (f"实例级当前请求快照：显示 {len(table_rows)} 个候选；"
                   "指标为单个请求当前累计值；本表不重复展开 SQL 文本。"
                   if table_rows else "实例级快照当前没有匹配请求；不生成空值排名。")
        sections.append(ReportSection(section_id, title, summary=summary,
                                      tables=(table,) if table else ()))

    code_blocks: list[ReportCodeBlock] = []
    for statement in statements if isinstance(statements, list) else []:
        if not isinstance(statement, dict):
            continue
        sql_ref = statement.get("sql_ref")
        if not isinstance(sql_ref, str) or not _valid_sql_ref(sql_ref):
            continue
        query_hashes = statement.get("query_hash", [])
        if not isinstance(query_hashes, list):
            query_hashes = []
        plan_identities = statement.get("plan_identities", [])
        if not isinstance(plan_identities, list):
            plan_identities = []
        identity_lines = [f"SQL 引用：{sql_ref}",
                          f"身份状态：{display_value(_safe(statement.get('identity_status')))}",
                          f"Query Hash：{', '.join(_safe(item) for item in query_hashes) or '不可用'}",
                          f"SQL Handle：{_safe(statement.get('sql_handle') or '不可用')}",
                          f"语句偏移量：{_safe(statement.get('statement_start_offset'))} / "
                          f"{_safe(statement.get('statement_end_offset'))}",
                          f"数据库 ID 上下文：{_safe(statement.get('database_id') or '不可用')}",
                          "计划句柄：" + (", ".join(_safe(item.get("plan_handle"))
                              for item in plan_identities if isinstance(item, dict)
                              and item.get("plan_handle")) or "不可用"),
                          f"文本状态：{display_value(_safe(statement.get('text_status')))}"]
        sql_text = statement.get("statement_text")
        if isinstance(sql_text, str) and sql_text:
            identity_lines.extend(("", sql_text))
        code_blocks.append(ReportCodeBlock(
            f"{sql_ref} — {_safe(statement.get('text_status'))}",
            str(redact("\n".join(identity_lines))), "sql", False,
            anchor_id=f"sql-text-{sql_ref.casefold()}"))
    summary = ("来源：sys.dm_exec_requests / sys.dm_exec_sessions / sys.dm_exec_sql_text。"
               "每条 SQL 文本只在本区出现一次；排名表通过 SQL 引用跳转。"
               if code_blocks else
               "来源：sys.dm_exec_requests / sys.dm_exec_sessions / sys.dm_exec_sql_text。"
               "没有可关联的 SQL 文本候选。" if data.get("collection_status") == "COLLECTED" else
               "采集不可用；请检查权限和诊断状态。")
    sections.append(ReportSection("active-sql-text", "SQL 文本", summary=summary,
                                  code_blocks=tuple(code_blocks)))
    return sections


def render_active_sql_human(data: Any, report_path: str | None = None) -> str:
    """Show the selected primary ranking and its referenced SQL text once."""
    from dba.reporting.normalizers import display_value

    if not isinstance(data, dict):
        return "活动 SQL\n" + "-" * 42 + "\n  采集状态：未知"
    selected_sort = data.get("sort", "cpu")
    selected = next((section for section in _RANKING_SECTIONS if section[0] == selected_sort),
                    _RANKING_SECTIONS[0])
    ranking_key = selected[0]
    _, metric_name, metric_unit = {
        "cpu": ("cpu_time_ms", "当前请求 CPU 时间", "毫秒"),
        "logical-reads": ("logical_reads", "当前请求逻辑读", "页"),
        "elapsed": ("elapsed_time_ms", "当前请求运行耗时", "毫秒"),
    }[ranking_key]
    lines = ["SQL Server 活动 SQL", "=" * 56,
             f"采集状态：{display_value(_safe(data.get('collection_status')))} | "
             f"数据完整性：{display_value(_safe(data.get('completeness')))} | 采集范围：实例",
             f"主要排序指标：{metric_name}（{metric_unit}）| 排名数量：{_safe(data.get('top_n'))}",
             "排名  SQL 引用  会话  请求  指标  等待事件 / 阻塞者"]
    rankings = data.get("rankings", {})
    rows = rankings.get(ranking_key, []) if isinstance(rankings, dict) else []
    refs: list[str] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        raw_sql_ref = item.get("sql_ref")
        sql_ref = raw_sql_ref if isinstance(raw_sql_ref, str) else "UNAVAILABLE"
        refs.append(sql_ref)
        value = item.get("metric_value")
        unit = item.get("metric_unit") or ""
        metric = f"{value} {unit}" if value is not None else "不可用"
        wait = item.get("wait_type") or "—"
        wait_time = item.get("wait_time_ms")
        wait_text = f"{wait} ({wait_time} ms)" if wait_time is not None else str(wait)
        blocker = item.get("blocking_session_id")
        blocker_text = f"阻塞者 {blocker}" if blocker not in (None, 0) else "无阻塞者"
        lines.append(f"{item.get('rank', '—'):<5} {sql_ref:<9} "
                     f"{str(item.get('session_id', '—')):<8} "
                     f"{str(item.get('request_id', '—')):<8} "
                     f"{redact(metric)}  {redact(wait_text)} / {blocker_text}")
    if not rows:
        lines.append("未观测到匹配的活动请求。")
    statements = data.get("sql_texts", [])
    by_ref = {item.get("sql_ref"): item for item in statements
              if isinstance(item, dict) and isinstance(item.get("sql_ref"), str)} \
        if isinstance(statements, list) else {}
    lines.extend(("", "SQL 文本"))
    for sql_ref in dict.fromkeys(refs):
        statement = by_ref.get(sql_ref)
        if statement is None:
            continue
        lines.extend((f"[{sql_ref}] 文本状态：{display_value(statement.get('text_status', 'UNKNOWN'))}",
                      f"  Query Hash：{', '.join(statement.get('query_hash', [])) or '不可用'}",
                      f"  SQL Handle：{statement.get('sql_handle') or '不可用'}",
                      f"  语句偏移量：{statement.get('statement_start_offset')} / "
                      f"{statement.get('statement_end_offset')}",
                      f"  计划句柄：{', '.join(item.get('plan_handle', '')
                          for item in statement.get('plan_identities', [])
                          if isinstance(item, dict) and item.get('plan_handle')) or '不可用'}"))
        if isinstance(statement.get("statement_text"), str):
            lines.append(str(redact(statement["statement_text"])))
    if report_path:
        lines.extend(("", "详细 HTML 报告", f"  {report_path}"))
    return _terminal_safe("\n".join(lines))


def _valid_sql_ref(value: str) -> bool:
    return len(value) == 7 and value.startswith("SQL-") and value[4:].isdigit()


def _safe(value: Any) -> str:
    return str(redact(value)) if value not in (None, "") else "不可用"


def _terminal_safe(value: str) -> str:
    """Render database-derived terminal controls visibly without executing them."""
    safe: list[str] = []
    for char in value:
        codepoint = ord(char)
        if char in "\n\r\t" or codepoint >= 32 and codepoint != 127 and not 0x80 <= codepoint <= 0x9F:
            safe.append(char)
        else:
            safe.append(f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}")
    return "".join(safe)
