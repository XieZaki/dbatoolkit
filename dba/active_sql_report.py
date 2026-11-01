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
    ("cpu", "active-sql-cpu", "SQL Ordered by CPU Time", "CPU Time"),
    ("logical-reads", "active-sql-logical-reads", "SQL Ordered by Logical Reads", "Logical Reads"),
    ("elapsed", "active-sql-elapsed", "SQL Ordered by Elapsed Time", "Elapsed Time"),
)


def active_sql_sections(value: Any) -> list[ReportSection]:
    """Build bounded ranking tables and one text block per stable SQL Ref."""
    from dba.reporting.model import ReportCodeBlock, ReportSection, ReportTable

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
        table = (ReportTable(("SQL Ref", "Session ID", "Request ID", metric_header,
                              "Wait", "Wait Time (ms)", "Blocking Session"),
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
        identity_lines = [f"SQL Ref: {sql_ref}",
                          f"Identity Status: {_safe(statement.get('identity_status'))}",
                          f"Query Hash: {', '.join(_safe(item) for item in query_hashes) or 'UNAVAILABLE'}",
                          f"SQL Handle: {_safe(statement.get('sql_handle') or 'UNAVAILABLE')}",
                          f"Statement Offsets: {_safe(statement.get('statement_start_offset'))} / "
                          f"{_safe(statement.get('statement_end_offset'))}",
                          f"Database ID Context: {_safe(statement.get('database_id') or 'UNAVAILABLE')}",
                          "Plan Handle(s): " + (", ".join(_safe(item.get("plan_handle"))
                              for item in plan_identities if isinstance(item, dict)
                              and item.get("plan_handle")) or "UNAVAILABLE"),
                          f"Text Status: {_safe(statement.get('text_status'))}"]
        sql_text = statement.get("statement_text")
        if isinstance(sql_text, str) and sql_text:
            identity_lines.extend(("", sql_text))
        code_blocks.append(ReportCodeBlock(
            f"{sql_ref} — {_safe(statement.get('text_status'))}",
            str(redact("\n".join(identity_lines))), "sql", False,
            anchor_id=f"sql-text-{sql_ref.casefold()}"))
    status = _safe(data.get("collection_status", "UNKNOWN"))
    summary = ("来源：sys.dm_exec_requests / sys.dm_exec_sessions / sys.dm_exec_sql_text。"
               "每条 SQL 文本只在本区出现一次；排名表通过 SQL Ref 跳转。"
               if code_blocks else
               "来源：sys.dm_exec_requests / sys.dm_exec_sessions / sys.dm_exec_sql_text。"
               "没有可关联的 SQL 文本候选。" if status == "COLLECTED" else
               "采集不可用；请检查权限和诊断状态。")
    sections.append(ReportSection("active-sql-text", "SQL Text", summary=summary,
                                  code_blocks=tuple(code_blocks)))
    return sections


def render_active_sql_human(data: Any, report_path: str | None = None) -> str:
    """Show the selected primary ranking and its referenced SQL text once."""
    if not isinstance(data, dict):
        return "Active SQL\n" + "-" * 42 + "\n  Collection: UNKNOWN"
    selected_sort = data.get("sort", "cpu")
    selected = next((section for section in _RANKING_SECTIONS if section[0] == selected_sort),
                    _RANKING_SECTIONS[0])
    ranking_key = selected[0]
    _, metric_name, metric_unit = {
        "cpu": ("cpu_time_ms", "Current request CPU time", "milliseconds"),
        "logical-reads": ("logical_reads", "Current request logical reads", "pages"),
        "elapsed": ("elapsed_time_ms", "Current request elapsed time", "milliseconds"),
    }[ranking_key]
    lines = ["SQL Server Active SQL", "=" * 56,
             f"Collection: {_safe(data.get('collection_status'))} | "
             f"Completeness: {_safe(data.get('completeness'))} | Scope: INSTANCE",
             f"Primary ranking: {metric_name} ({metric_unit}) | Top N: {_safe(data.get('top_n'))}",
             "Rank  SQL Ref   Session  Request  Metric                 Wait / Blocker"]
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
        metric = f"{value} {unit}" if value is not None else "UNAVAILABLE"
        wait = item.get("wait_type") or "—"
        wait_time = item.get("wait_time_ms")
        wait_text = f"{wait} ({wait_time} ms)" if wait_time is not None else str(wait)
        blocker = item.get("blocking_session_id")
        blocker_text = f"blocker {blocker}" if blocker not in (None, 0) else "no blocker"
        lines.append(f"{item.get('rank', '—'):<5} {sql_ref:<9} "
                     f"{str(item.get('session_id', '—')):<8} "
                     f"{str(item.get('request_id', '—')):<8} "
                     f"{redact(metric)}  {redact(wait_text)} / {blocker_text}")
    if not rows:
        lines.append("No matching active request was observed.")
    statements = data.get("sql_texts", [])
    by_ref = {item.get("sql_ref"): item for item in statements
              if isinstance(item, dict) and isinstance(item.get("sql_ref"), str)} \
        if isinstance(statements, list) else {}
    lines.extend(("", "SQL Text"))
    for sql_ref in dict.fromkeys(refs):
        statement = by_ref.get(sql_ref)
        if statement is None:
            continue
        lines.extend((f"[{sql_ref}] status={statement.get('text_status', 'UNKNOWN')}",
                      f"  Query Hash: {', '.join(statement.get('query_hash', [])) or 'UNAVAILABLE'}",
                      f"  SQL Handle: {statement.get('sql_handle') or 'UNAVAILABLE'}",
                      f"  Statement Offsets: {statement.get('statement_start_offset')} / "
                      f"{statement.get('statement_end_offset')}",
                      f"  Plan Handle(s): {', '.join(item.get('plan_handle', '')
                          for item in statement.get('plan_identities', [])
                          if isinstance(item, dict) and item.get('plan_handle')) or 'UNAVAILABLE'}"))
        if isinstance(statement.get("statement_text"), str):
            lines.append(str(redact(statement["statement_text"])))
    if report_path:
        lines.extend(("", "Detailed HTML Report", f"  {report_path}"))
    return _terminal_safe("\n".join(lines))


def _valid_sql_ref(value: str) -> bool:
    return len(value) == 7 and value.startswith("SQL-") and value[4:].isdigit()


def _safe(value: Any) -> str:
    return str(redact(value)) if value not in (None, "") else "UNAVAILABLE"


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
