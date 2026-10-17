"""Console and Markdown views of a bounded evidence-only plan report."""

import json
from typing import Any

from dba.execution_plan import PlanReport
from dba.reporters import _safe


def _view(value: Any) -> str:
    if value is None or value == []:
        return "unavailable"
    return _safe(json.dumps(value, ensure_ascii=False, default=str))


def sections(report: PlanReport) -> list[tuple[str, list[str]]]:
    details = report.details
    request = details["request"]
    findings = report.findings
    return [
        ("SQL Summary", [
            f"Instance: {report.instance.id}", f"Session: {details['session_id']}",
            f"Source: {_view(details['source'])}", f"SQL text: {_view(request.get('sql_text'))}",
            f"Statement text: {_view(request.get('statement_text'))}",
            f"SQL handle: {_view(request.get('sql_handle'))}",
            f"Plan handle: {_view(request.get('plan_handle'))}",
            _view(details["note"]) if details["note"] else "",
        ]),
        ("Runtime Metrics", [
            f"Scope: {details['metrics_scope']}",
            *[f"{key}: {_view(request.get(key))}" for key in (
                "request_id", "start_time", "elapsed_ms", "cpu_ms", "logical_reads", "reads",
                "writes", "wait_type", "wait_ms", "database_name", "status", "command")],
        ]),
        ("Plan Summary", [
            f"Plan available: {details['plan_available']}",
            f"XML oversized: {details['plan_oversize']}",
            f"Operator count: {details['operator_count']}",
            "Top operators by estimated subtree cost:",
            *[_view(node) for node in details["top_operators"]],
            "Missing Index information is advisory evidence only.",
        ]),
        ("Findings", [f"[{f.severity.value}] {f.title}" for f in findings]
         or ["No configured plan finding in available evidence."]),
        ("Evidence", [f"{f.title}: {_view(f.evidence)}" for f in findings] or ["unavailable"]),
        ("Recommendations", [f.recommendation for f in findings] or ["人工检查 SQL 和运行时指标。"]),
        ("Raw Plan Reference", [
            "execution_plan.xml (sanitized XML copy)" if details["plan_available"] else
            "Execution plan unavailable.",
            "Toolkit did not modify database state.",
        ]),
    ]


def render_console(report: PlanReport) -> str:
    return "SQL Server Execution Plan Analysis\n\n" + _safe("\n\n".join(
        title + "\n" + "\n".join(line for line in lines if line)
        for title, lines in sections(report))) + "\n"


def render_markdown(report: PlanReport) -> str:
    return "# SQL Server Execution Plan Analysis\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join("    " + line for line in _safe("\n".join(lines)).splitlines())
        for title, lines in sections(report)) + "\n"
