"""Readable current-state disk and log reports."""

import json
from typing import Any

from dba.disk_growth import DiskGrowthReport
from dba.logging import redact
from dba.reporters import _safe


def _view(value: Any) -> str:
    return _safe(json.dumps(redact(value), ensure_ascii=False, default=str))


def sections(report: DiskGrowthReport) -> list[tuple[str, list[str]]]:
    details = report.details
    findings = [f"[{item.severity.value}] {item.title}: {_view(item.evidence)}"
                for item in report.findings]
    return [
        ("Summary", [f"Instance: {report.instance.id}",
                     f"Database type: {report.instance.db_type.value}",
                     f"Version: {details['version']}", details["history_note"],
                     f"Dominant observed category: {details['dominant_space_category'] or 'unavailable'}",
                     f"Required evidence: {'COMPLETE' if details.get('required_evidence_complete') else 'INCOMPLETE'}",
                     f"Optional evidence: {details.get('optional_evidence_status', 'UNKNOWN')}",
                     f"Data visibility: {details.get('data_visibility', 'UNKNOWN')}",
                     f"Incomplete: {details['incomplete']}" ]),
        ("Current Space Usage", [_view(row) for row in details["space"]] or ["Unavailable."]),
        ("Log/WAL/Binlog/Archive", [_view(details["log"])]),
        ("Transactions", ([f"Active transaction count: {details['other']['active_transaction_count']}"]
                           if details["other"].get("active_transaction_count") == 0 else []) +
         [_view(row) for row in details["other"]["transactions"]]
         or ["Transaction evidence unavailable."]),
        ("Replication/Archive Evidence", [_view(details["other"])]),
        ("Temp/Undo Evidence", [_view(row) for row in details["space"]
                                if row["category"] in {"TEMP", "UNDO"}]
         or [_view(details["other"].get("temp_cumulative", "Unavailable."))]),
        ("Findings", findings or ["No finding supported by this sample."]),
        ("Recommendations", [item.recommendation for item in report.findings]
         or ["Review unavailable sources and compare with historical capacity snapshots."]),
        ("Next Diagnostic Step", [f"Suggested next analyzer: {details['next_diagnostic_step']}"
                                  if details["next_diagnostic_step"] else "No specific analyzer suggested."]),
        ("Source Quality", [_view(row) for row in details["samples"]]),
    ]


def render_console(report: DiskGrowthReport) -> str:
    return _safe("\n\n".join(title + "\n" + "\n".join(lines)
                           for title, lines in sections(report))) + "\n"


def render_markdown(report: DiskGrowthReport) -> str:
    return "# Disk and Log Growth Diagnosis\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join("    " + line for line in lines)
        for title, lines in sections(report)) + "\n"
