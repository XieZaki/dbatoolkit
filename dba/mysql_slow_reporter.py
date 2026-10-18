"""MySQL slow diagnosis Console and Markdown views."""

import json
import re
import unicodedata
from typing import Any

from dba.mysql_slow import MySQLSlowReport
from dba.reporters import _safe

_ANSI_ESCAPE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\)|[@-_])"
)


def _terminal_sql_summary(value: str) -> tuple[str, bool]:
    """Normalize already-redacted SQL for a bounded, safe terminal preview."""
    text = _ANSI_ESCAPE.sub("", value)
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char
                   for char in text)
    text = " ".join(text.split())
    return text[:200], len(text) > 200


def _elapsed_display(elapsed_ms: object) -> str:
    if not isinstance(elapsed_ms, (int, float)) or isinstance(elapsed_ms, bool):
        return "未记录"
    if elapsed_ms < 0:
        return "未记录"
    if elapsed_ms < 0.1:
        return "<0.1 毫秒"
    return f"{elapsed_ms:.1f} 毫秒"


def _view(value: Any) -> str:
    return "unavailable" if value is None or value == [] else _safe(
        json.dumps(value, ensure_ascii=False, default=str))


def sections(report: MySQLSlowReport) -> list[tuple[str, list[str]]]:
    data = report.details
    graph = data["blocking_graph"]
    tree: list[str] = []
    expanded: set[int] = set()
    for root in graph["roots"]:
        pending = [(root, 0)]
        while pending:
            pid, depth = pending.pop()
            repeated = pid in expanded
            tree.append(f"{'  ' * min(depth, 30)}{'└─ ' if depth else ''}{pid}"
                        + (" [already shown]" if repeated else ""))
            if not repeated:
                expanded.add(pid)
                pending.extend((child, depth + 1) for child in
                               reversed(graph["children"].get(str(pid), [])))
    if graph["unresolved"]:
        tree.append("Cycle/unresolved process IDs: " + _view(graph["unresolved"]))
    tree.extend(_view(item) for item in graph["anomalies"])
    top = []
    for row in data["top_queries"]:
        top.append(" | ".join(f"{key}={_view(row.get(key))}" for key in (
            "processlist_id", "user_name", "host_raw", "database_name",
            "duration_ms", "state_name", "rows_examined", "current_wait", "blocking_pids")))
        top.append("SQL summary: " + _safe(str(row.get("sql_summary") or "unavailable")))
    findings = [f"[{item.severity.value}] {item.title}: {_view(item.evidence)}"
                for item in report.findings]
    commands = [f"{cmd.text}  {cmd.description}"
                for item in report.findings for cmd in item.reference_commands]
    return [
        ("Summary", ["MySQL Slow Diagnose", f"Instance: {report.instance.id}",
                     f"Version: {data['version']}", f"Time: {report.diagnosed_at.isoformat()}",
                     *data["quality"]]),
        ("Concurrency", [f"{key}: {value}" for key, value in data["concurrency"].items()]),
        ("Long Running SQL", ["Active SQL ranked by duration (Top N):",
                              *(top or ["No active query in available sample."]),
                              "Threshold matches: " + _view(data["long_running"])]),
        ("Blocking Tree", tree or ["No lock wait observed in available sample."]),
        ("Root Blocker", [_view(row) for row in data["roots"]] or ["unavailable"]),
        ("Transactions", [_view(row) for row in data["transactions"]]
         or ["No InnoDB transaction in available sample."]),
        ("Idle Transactions", [_view(row) for row in data["idle_transactions"]]
         or ["No confirmed idle transaction in available sample."]),
        ("Session Source", [_view({key: row.get(key) for key in (
            "processlist_id", "thread_id", "user_name", "host_raw", "host_ip", "database_name")})
         for row in data["sessions"]] or ["unavailable"]),
        ("All Sessions", [_view(row) for row in data["sessions"]] or ["No session rows available."]),
        ("All Active Requests", [_view(row) for row in data.get("active_requests", [])]
         or ["No request rows available."]),
        ("Findings", findings or ["No configured finding in available sample."]),
        ("Recommendations", [item.recommendation for item in report.findings]
         or ["Review current activity and workload baseline."]),
        ("Reference Commands", [*commands, "Toolkit did not execute KILL or KILL QUERY."]),
    ]


def render_console(report: MySQLSlowReport) -> str:
    return _safe("\n\n".join(title + "\n" + "\n".join(lines)
                           for title, lines in sections(report))) + "\n"


def render_recent_statements(rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
                             elapsed_ms: float | None = None) -> str:
    """Render only collector-sanitized recent statement evidence for a human CLI."""
    if len(rows) == 1 and rows[0].get("evidence_status") == "NOT_APPLICABLE":
        return "【最近执行 SQL】\n当前没有已确认的根阻塞或空闲事务候选。\n"
    lines = ["【根阻塞最近执行 SQL】", "说明：最近执行 SQL，不保证属于当前事务，也不保证是持锁 SQL。"]
    lines.append("采集耗时：" + _elapsed_display(elapsed_ms))
    for target in rows[:5]:
        if not isinstance(target, dict):
            continue
        pid = target.get("session_id", "未知")
        status = target.get("evidence_status", "UNKNOWN")
        lines.append(f"\n连接 {pid}｜状态：{status}")
        reason = target.get("reason")
        if isinstance(reason, str):
            lines.append("原因：" + _safe(reason))
        if target.get("limit_reached"):
            lines.append("已达到每连接5条展示上限；是否还有更早事件未知。")
        elif target.get("truncated"):
            lines.append("历史事件已截断，存在未显示记录。")
        statements = target.get("statements", [])
        if isinstance(statements, list) and statements:
            lines.append(f"来源：performance_schema.events_statements_history｜数量：{len(statements)}条（最多5条）")
            for index, statement in enumerate(statements[:5], 1):
                sql = statement.get("sql_text_redacted") if isinstance(statement, dict) else None
                if not isinstance(sql, str) or not sql:
                    lines.append(f"{index}. SQL 文本不可用")
                    continue
                summary, display_truncated = _terminal_sql_summary(sql)
                lines.append(f"{index}. {_safe(summary)}")
                if display_truncated:
                    lines.append("   [终端仅显示前200字符，详情见HTML]")
                elif isinstance(statement, dict) and statement.get("truncated"):
                    lines.append("   [SQL 文本已截断]")
    return "\n".join(lines) + "\n"


def render_markdown(report: MySQLSlowReport) -> str:
    return "# MySQL Slow Diagnose\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join("    " + line for line in
                                        _safe("\n".join(lines)).splitlines())
        for title, lines in sections(report)) + "\n"
