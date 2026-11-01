"""Shared JSON-derived HTML and terminal presentation for Memory Grant evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dba.logging import redact

if TYPE_CHECKING:
    from dba.reporting.model import ReportSection

_HUMAN_ROW_LIMIT = 20


def memory_grant_sections(value: Any) -> list[ReportSection]:
    from dba.reporting.model import ReportSection

    data = value if isinstance(value, dict) else {}
    waiting = data.get("waiting_sample", {})
    granted = data.get("granted_sample", {})
    waiting = waiting if isinstance(waiting, dict) else {}
    granted = granted if isinstance(granted, dict) else {}
    return [
        _sample_section("memory-grant-waiting", "Waiting for Memory Grant", waiting, waiting=True),
        _sample_section("memory-grant-granted", "Granted Memory", granted, waiting=False),
        ReportSection(
            "memory-grant-evidence",
            "Collection Quality",
            summary=("Waiting and granted rows were sampled using separate sequential read-only queries. "
                     "The samples are not an atomic server snapshot. A missing request join preserves the "
                     "grant row while marking request context unavailable. This scenario does not assess "
                     "overall database health. Elapsed time is client end-to-end, including driver execution, "
                     "network fetch, and row parsing; server-side SQL execution duration is not separately measured."),
            fields=(
                ("Completeness", _safe(data.get("completeness"))),
                ("Snapshot Consistency", _safe(data.get("snapshot_consistency"))),
                ("Health Assessment", _safe(data.get("health_assessment"))),
                ("Identity Conflicts", _safe(data.get("identity_conflicts"))),
            ),
        ),
    ]


def _sample_section(section_id: str, title: str, sample: dict[str, Any], *, waiting: bool) -> ReportSection:
    from dba.reporting.model import ReportSection, ReportTable

    raw_rows = sample.get("rows", [])
    rows: list[tuple[str, ...]] = []
    if isinstance(raw_rows, list):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            identity = raw.get("request_identity", {})
            identity = identity if isinstance(identity, dict) else {}
            if waiting:
                row = (
                    _safe(raw.get("session_id")), _safe(raw.get("request_id")),
                    _safe(identity.get("request_status")), _safe(identity.get("wait_type")),
                    _safe(raw.get("wait_time_ms")), _safe(raw.get("requested_memory_kb")),
                    _safe(raw.get("required_memory_kb")), _safe(raw.get("queue_id")),
                    _safe(raw.get("wait_order")), _safe(identity.get("request_context_status")),
                )
            else:
                row = (
                    _safe(raw.get("session_id")), _safe(raw.get("request_id")),
                    _safe(identity.get("request_status")), _safe(raw.get("requested_memory_kb")),
                    _safe(raw.get("required_memory_kb")), _safe(raw.get("granted_memory_kb")),
                    _safe(raw.get("used_memory_kb")), _safe(raw.get("max_used_memory_kb")),
                    _safe(identity.get("wait_type")), _safe(identity.get("request_context_status")),
                )
            rows.append(row)
    headers = (("Session ID", "Request ID", "Request Status", "Wait Type", "Wait Time (ms)",
                "Requested (KB)", "Required (KB)", "Queue ID", "Wait Order", "Request Identity")
               if waiting else
               ("Session ID", "Request ID", "Request Status", "Requested (KB)", "Required (KB)",
                "Granted (KB)", "Used (KB)", "Max Used (KB)", "Wait Type", "Request Identity"))
    table = ReportTable(headers, tuple(rows)) if rows else None
    sample_label = "Waiting" if waiting else "Granted"
    summary = (
        f"{sample_label} rows: {len(rows)}; source query returned {sample.get('row_count', 0)}; "
        f"sample: {_safe(sample.get('started_at'))} to {_safe(sample.get('finished_at'))}; "
        f"client elapsed: {_elapsed_label(sample)}; "
        f"truncated: {_safe(sample.get('truncated'))}; status: {_safe(sample.get('success'))}."
    )
    fields = () if sample.get("success") else (
        ("Error Type", _safe(sample.get("error_type"))),
        ("Error", _safe(sample.get("error_message"))),
    )
    return ReportSection(section_id, title, summary=summary, fields=fields,
                         tables=(table,) if table else ())


def render_memory_grant_human(value: Any, report_path: str | None = None) -> str:
    data = value if isinstance(value, dict) else {}
    waiting = data.get("waiting_sample", {})
    granted = data.get("granted_sample", {})
    waiting = waiting if isinstance(waiting, dict) else {}
    granted = granted if isinstance(granted, dict) else {}
    lines = [
        "SQL Server Memory Grant Queue",
        "=" * 64,
        f"Database: {_safe(data.get('database_id'))} | Version: {_safe(data.get('database_version'))}",
        f"Collection: {_safe(data.get('collection_status'))} | "
        f"Completeness: {_safe(data.get('completeness'))}",
        "Overall database health: NOT ASSESSED",
        "Waiting and granted queries are separate sequential samples, not an atomic snapshot.",
        "Elapsed time is client end-to-end, including driver execution, network fetch, and row parsing; "
        "server-side SQL execution duration is not separately measured.",
    ]
    for sample_name, sample, is_waiting in (("Waiting", waiting, True), ("Granted", granted, False)):
        raw_rows = sample.get("rows", [])
        raw_rows = raw_rows if isinstance(raw_rows, list) else []
        lines.extend(("", f"{sample_name}: {len(raw_rows)} | "
                      f"Sampled: {_safe(sample.get('started_at'))} to {_safe(sample.get('finished_at'))} | "
                      f"Client elapsed: {_elapsed_label(sample)} | "
                      f"Truncated: {_safe(sample.get('truncated'))}"))
        if not sample.get("success"):
            lines.append(f"  Unavailable: {_safe(sample.get('error_type'))} — "
                         f"{_safe(sample.get('error_message'))}")
        for raw in raw_rows[:_HUMAN_ROW_LIMIT]:
            if not isinstance(raw, dict):
                continue
            identity = raw.get("request_identity", {})
            identity = identity if isinstance(identity, dict) else {}
            if is_waiting:
                text = (f"Session={_safe(raw.get('session_id'))} Request={_safe(raw.get('request_id'))} "
                        f"Wait={_safe(identity.get('wait_type'))}/{_safe(raw.get('wait_time_ms'))}ms "
                        f"Requested={_safe(raw.get('requested_memory_kb'))}KB "
                        f"Required={_safe(raw.get('required_memory_kb'))}KB")
            else:
                text = (f"Session={_safe(raw.get('session_id'))} Request={_safe(raw.get('request_id'))} "
                        f"Granted={_safe(raw.get('granted_memory_kb'))}KB "
                        f"Used={_safe(raw.get('used_memory_kb'))}KB "
                        f"MaxUsed={_safe(raw.get('max_used_memory_kb'))}KB")
            lines.append(f"  {text} | Request identity: {_safe(identity.get('request_context_status'))}")
        if len(raw_rows) > _HUMAN_ROW_LIMIT:
            lines.append(f"  ... {len(raw_rows) - _HUMAN_ROW_LIMIT} more rows in the HTML/JSON report.")
    lines.extend(("", "Interpretation: a waiting request is evidence of a memory grant wait only; "
                  "granted memory is distinct from current memory use. A granted request is not "
                  "identified as a unique cause."))
    if report_path:
        lines.extend(("", "Detailed HTML Report", f"  {report_path}"))
    return str(redact("\n".join(lines)))


def _safe(value: Any) -> str:
    if value is None:
        return "—"
    text = str(redact(value))
    return "".join(char if char in "\t " or ord(char) >= 32 and ord(char) != 127
                   and not 0x80 <= ord(char) <= 0x9F else " " for char in text)


def _elapsed_label(sample: dict[str, Any]) -> str:
    elapsed = sample.get("elapsed_ms")
    if sample.get("elapsed_scope") != "CLIENT_END_TO_END" or not isinstance(elapsed, (int, float)):
        return "not recorded"
    return f"{elapsed:.3f} ms (client end-to-end)"
