"""MySQL slow diagnosis Console and Markdown views."""

import json
from typing import Any

from dba.mysql_slow import MySQLSlowReport
from dba.reporters import _safe


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


def render_markdown(report: MySQLSlowReport) -> str:
    return "# MySQL Slow Diagnose\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join("    " + line for line in
                                        _safe("\n".join(lines)).splitlines())
        for title, lines in sections(report)) + "\n"
