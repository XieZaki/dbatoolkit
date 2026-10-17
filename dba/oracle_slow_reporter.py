"""Oracle slow diagnosis Console and Markdown views."""

import json
from typing import Any

from dba.oracle_slow import OracleSlowReport
from dba.reporters import _safe


def _view(value: Any) -> str:
    return "unavailable" if value is None or value == [] else _safe(
        json.dumps(value, ensure_ascii=False, default=str))


def sections(report: OracleSlowReport) -> list[tuple[str, list[str]]]:
    data = report.details
    graph = data["blocking_graph"]
    tree: list[str] = []
    expanded: set[str] = set()
    for root in graph["roots"]:
        pending = [(root, 0)]
        while pending:
            key, depth = pending.pop()
            repeated = key in expanded
            tree.append(f"{'  ' * min(depth, 30)}{'└─ ' if depth else ''}{key}"
                        + (" [already shown]" if repeated else ""))
            if not repeated:
                expanded.add(key)
                pending.extend((child, depth + 1) for child in
                               reversed(graph["children"].get(key, [])))
    if graph["unresolved"]:
        tree.append("Missing or remote sessions: " + _view(graph["unresolved"]))
    if graph["cycles"]:
        tree.append("Cycles: " + _view(graph["cycles"]))
    top = []
    for row in data["top_queries"]:
        top.append(" | ".join(f"{key}={_view(row.get(key))}" for key in (
            "inst_id", "sid", "serial_number", "sql_id", "username", "machine",
            "program", "module", "duration_seconds", "event", "wait_class",
            "blocking_instance", "blocking_session")))
        top.append("SQL summary: " + _view(row.get("sql_summary")))
    findings = [f"[{item.severity.value}] {item.title}: {_view(item.evidence)}"
                for item in report.findings]
    commands = [f"{cmd.text}  {cmd.description}"
                for item in report.findings for cmd in item.reference_commands]
    sources = [{key: row.get(key) for key in
                ("inst_id", "sid", "serial_number", "username", "osuser", "machine",
                 "program", "module", "action", "client_identifier", "service_name")}
               | {"client_ip": None,
                  "client_ip_reason": "unavailable: V$SESSION does not reliably expose client IP"}
               for row in data["sessions"]]
    return [
        ("Summary", ["Oracle Slow Diagnose", f"Instance: {report.instance.id}",
                     f"Version: {data['version']}", f"Time: {report.diagnosed_at.isoformat()}",
                     *data["quality"]]),
        ("Concurrency", [f"{key}: {value}" for key, value in data["concurrency"].items()]),
        ("Long Running SQL", ["Current SQL ranked by current execution duration (Top N):",
                              *(top or ["No active SQL in available sample."]),
                              "Threshold matches: " + _view(data["long_running"]),
                              "V$SQL elapsed/cpu/read counters are cumulative across executions."]),
        ("Blocking Tree", tree or ["No blocking observed in available sample."]),
        ("Root Blocker", [_view(row) for row in data["roots"]] or ["unavailable"]),
        ("Transactions", [_view(row) for row in data["transactions"]] or ["No transaction in sample."]),
        ("Idle Transactions", [_view(row) for row in data["idle_transactions"]]
         or ["No confirmed inactive transaction in sample."]),
        ("Session Source", [_view(row) for row in sources] or ["unavailable"]),
        ("All Sessions", [_view(row) for row in data["sessions"]] or ["No session rows available."]),
        ("All Active Requests", [_view(row) for row in data.get("active_requests", [])]
         or ["No request rows available."]),
        ("Findings", findings or ["No configured finding in available sample."]),
        ("Recommendations", [item.recommendation for item in report.findings]
         or ["Review current activity and application baseline."]),
        ("Reference Commands", [*commands, "Toolkit did NOT execute this command."]),
    ]


def render_console(report: OracleSlowReport) -> str:
    return _safe("\n\n".join(title + "\n" + "\n".join(lines)
                           for title, lines in sections(report))) + "\n"


def render_markdown(report: OracleSlowReport) -> str:
    return "# Oracle Slow Diagnose\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join("    " + line for line in
                                        _safe("\n".join(lines)).splitlines())
        for title, lines in sections(report)) + "\n"
