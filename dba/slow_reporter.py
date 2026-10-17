import json
from typing import Any

from dba.reporters import _safe
from dba.slow import SlowReport


def _display(value: Any) -> str:
    if value is None or value == []:
        return "unavailable"
    return _safe(json.dumps(value, ensure_ascii=False, default=str))


def sections(report: SlowReport) -> list[tuple[str, list[str]]]:
    data = report.details
    graph = data["blocking_graph"]
    tree: list[str] = []
    # A global expansion set bounds output even with MARS multiple-parent graphs.
    expanded: set[int] = set()
    for root in graph["roots"]:
        pending = [(root, 0)]
        while pending:
            node, depth = pending.pop()
            repeated = node in expanded
            tree.append(
                f"{'  ' * min(depth, 30)}{'└─ ' if depth else ''}{node}"
                + (" [already shown]" if repeated else "")
                + (f" [depth={depth}]" if depth > 30 else "")
            )
            if not repeated:
                expanded.add(node)
                pending.extend(
                    (child, depth + 1) for child in reversed(graph["children"].get(str(node), []))
                )
    if graph["unresolved"]:
        tree.append("Cycle/unresolved sessions: " + _display(graph["unresolved"]))
    tree.extend(_display(item) for item in graph["anomalies"])
    roots: list[str] = []
    for root in data["roots"]:
        roots.append(f"Root Blocker: {root['session_id']}")
        roots.extend(
            f"{key}: {_display(value)}"
            for key, value in root.items()
            if key not in {"session_id", "connections", "transactions"}
        )
    top = []
    for row in data["top_requests"]:
        top.append(
            " | ".join(
                f"{key}={_display(row.get(key))}"
                for key in (
                    "session_id",
                    "elapsed_ms",
                    "database_name",
                    "login_name",
                    "host_name",
                    "wait_type",
                    "blocking_session_id",
                )
            )
        )
        text = row.get("sql_text")
        top.append("sql_text summary: " + (_safe(str(text))[:500] if text else "unavailable"))
        if row in data["long_running"] and type(row.get("session_id")) is int:
            top.append(f"Execution plan analysis available. Next: dba diagnose {report.instance.id} plan --session {row['session_id']}")
    findings = [
        f"[{f.severity.value}] {f.title}\nEvidence: {_display(f.evidence)}" for f in report.findings
    ]
    commands = [
        f"{command.text}\n{command.description}"
        for f in report.findings
        for command in f.reference_commands
    ]
    return [
        (
            "Summary",
            [
                "SQL Server Slow Diagnose",
                f"Instance: {report.instance.id} ({report.instance.name})",
                f"Version: {data['version']}",
                f"Time: {report.diagnosed_at.isoformat()}",
                "Primary Finding: "
                + (
                    report.findings[0].title
                    if report.findings
                    else "No configured finding in this sample"
                ),
                *data["quality"],
                data["compatibility"],
            ],
        ),
        ("Concurrency", [f"{key}: {value}" for key, value in data["concurrency"].items()]),
        (
            "Long Running SQL",
            [
                "Current requests ranked by elapsed_ms DESC (configured Top N):",
                *top,
                "Threshold matches: " + _display(data["long_running"]),
            ],
        ),
        ("Blocking Tree", tree or ["No positive blocking edge observed in this sample."]),
        ("Root Blocker Details", roots or ["unavailable / no root observed"]),
        ("All Sessions", [_display(row) for row in data.get("all_sessions", [])]
         or ["No session rows available."]),
        ("All Active Requests", [_display(row) for row in data.get("all_requests", [])]
         or ["No request rows available."]),
        (
            "Transaction Details",
            [_display(row) for row in data["transactions"]]
            or ["unavailable / no transaction observed"],
        ),
        ("Session Source", [_display(row) for row in data["sources"]] or ["unavailable"]),
        ("Findings", findings or ["No configured finding in this sample."]),
        ("Recommendations", [f.recommendation for f in report.findings]),
        (
            "Reference Commands",
            [*commands, "Toolkit 未执行任何 KILL 操作。", "Toolkit did NOT execute this command."],
        ),
    ]


def render_console(report: SlowReport) -> str:
    return (
        _safe("\n\n".join(title + "\n" + "\n".join(lines) for title, lines in sections(report)))
        + "\n"
    )


def render_markdown(report: SlowReport) -> str:
    # Indented text protects SQL/host names containing fences, HTML or headings.
    return (
        "# SQL Server Slow Diagnose\n\n"
        + "\n\n".join(
            f"## {title}\n\n"
            + "\n".join("    " + line for line in _safe("\n".join(lines)).splitlines())
            for title, lines in sections(report)
        )
        + "\n"
    )
