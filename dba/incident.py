"""Database-neutral incident summaries built only from diagnostic evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.core import DiagnosticReport

_TRIAGE_RULE_IDS = {
    "ROOT_BLOCKER": "TRIAGE-ROOT-01",
    "BLOCKING_VICTIM": "TRIAGE-VICTIM-01",
    "INTERMEDIATE_BLOCKER": "TRIAGE-INTERMEDIATE-01",
    "INDEPENDENT_SLOW_SQL": "TRIAGE-INDEPENDENT-SLOW-01",
    "LONG_RUNNING_TRANSACTION": "TRIAGE-LONG-TX-01",
}
_TRIAGE_KIND_RULES = {
    "ROOT_BLOCKER": "ROOT_BLOCKER",
    "BLOCKED_VICTIM": "BLOCKING_VICTIM",
    "INTERMEDIATE_BLOCKER": "INTERMEDIATE_BLOCKER",
    "LONG_RUNNING_SQL": "INDEPENDENT_SLOW_SQL",
    "LONG_RUNNING_TRANSACTION": "LONG_RUNNING_TRANSACTION",
    "IDLE_OPEN_TRANSACTION": "LONG_RUNNING_TRANSACTION",
}


@dataclass(frozen=True)
class RecoveryAction:
    action_type: str
    target_session: str
    reason: str
    reference_command: str
    destructive: bool = True
    approval_required: bool = True
    open_transaction: str = "UNKNOWN"
    rollback_risk: str = "UNKNOWN"
    rollback_duration: str = "UNKNOWN"
    evidence_complete: bool = False
    toolkit_executed: bool = False


@dataclass(frozen=True)
class IncidentSummary:
    snapshot_time: str
    data_completeness: str
    missing_evidence: tuple[str, ...]
    required_evidence_status: str
    optional_evidence_status: str
    unavailable_optional_evidence: tuple[str, ...]
    status: str
    severity: str
    primary_problem: str
    observed_now: str
    impact: dict[str, int]
    timing: dict[str, str]
    wait: dict[str, str] | None
    root_cause_candidate: dict[str, Any] | None
    source: dict[str, Any] | None
    immediate_action: dict[str, Any] | None
    recovery_actions: tuple[RecoveryAction, ...]
    termination_risk: dict[str, str] | None
    report_path: str | None
    secondary_signals: dict[str, int]
    collector_summary: tuple[dict[str, Any], ...]


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _first(row: dict[str, Any], *names: str) -> Any:
    return next((row[name] for name in names if row.get(name) is not None), None)


def _slow_sample_index(details: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("collector")): item for item in details.get("samples", [])
            if isinstance(item, dict) and item.get("collector")}


def _slow_sample_reference(details: dict[str, Any], collector: str, source_path: str,
                           facts: dict[str, Any]) -> dict[str, Any]:
    sample = _slow_sample_index(details).get(collector)
    integrity = ("UNKNOWN" if sample is None else "FAILED" if sample.get("success") is False else
                 "TRUNCATED" if sample.get("truncated") else "COMPLETE")
    return {
        "collector": collector,
        "analyzer": "dba.incident.build_priority_candidates",
        "evidence_type": ("BLOCKING_RELATIONSHIP" if "blocking" in source_path else
                          "TRANSACTION_DURATION" if "transaction" in source_path else
                          "REQUEST_DURATION"),
        "source_path": source_path,
        "observed_at": sample.get("finished_at") if sample else None,
        "sample_started_at": sample.get("started_at") if sample else None,
        "sample_finished_at": sample.get("finished_at") if sample else None,
        "integrity": integrity,
        "facts": facts,
    }


def _duration(value: Any) -> tuple[int, str] | None:
    if type(value) is not int or value < 0:
        return None
    seconds = value // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return value, f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _is_open_transaction(root: dict[str, Any]) -> bool | None:
    if root.get("unfinished_transaction_confirmed") is True:
        return True
    if root.get("unfinished_transaction_confirmed") is False:
        return False
    if "transaction_observed" in root:
        if (root.get("transaction_observed") is True
                and root.get("transaction_identity_verified") is True):
            return True
        # An observed but unassociated transaction is evidence of uncertainty,
        # not evidence that the root session has no open transaction.
        return None
    if root.get("transaction_age_seconds") is not None:
        return True
    if any(root.get(key) not in (None, 0, "") for key in ("trx_id", "used_ublk", "transaction_id")):
        return True
    if root.get("open_transaction_count") == 0:
        return False
    count = root.get("open_transaction_count")
    if type(count) is int:
        return count > 0
    if root.get("transactions"):
        return True
    return None


def _quality(details: dict[str, Any], scenario: str) -> tuple[str, tuple[str, ...]]:
    if scenario == "replication":
        if not details.get("required_evidence_complete", False):
            return "INCOMPLETE", ("Replication role and status evidence",)
        samples = details.get("samples", [])
        bad = [item for item in samples if isinstance(item, dict)
               and (not item.get("success", True) or item.get("truncated"))]
        permission = details.get("permission_readiness", {})
        if bad or isinstance(permission, dict) and permission.get("status") in {"PARTIAL", "MISSING", "UNKNOWN"}:
            return "PARTIAL", tuple(str(item.get("collector", "replication evidence")) for item in bad) or (
                "Replication monitoring permissions or optional evidence",)
        return "COMPLETE", ()
    if details.get("plan_available") is False:
        return "PARTIAL", ("Execution plan",)
    if scenario == "plan" and details.get("plan_available") is True:
        return "COMPLETE", ()
    if scenario == "disk-growth":
        required_missing = tuple(str(item) for item in details.get("required_unavailable", []) if item)
        if details.get("incomplete") or not details.get("required_evidence_complete", True):
            return "INCOMPLETE", required_missing or ("Required disk-growth evidence",)
        if details.get("optional_unavailable"):
            return "PARTIAL", ()
        return "COMPLETE", ()
    samples = details.get("samples", [])
    bad_samples = any(not item.get("success", True) or item.get("truncated")
                      for item in samples if isinstance(item, dict))
    if (details.get("data_visibility") == "PARTIAL"
            or details.get("full_profile_evidence_status") == "PARTIAL"):
        return "PARTIAL", ()
    graph = details.get("blocking_graph", {})
    graph_anomaly = isinstance(graph, dict) and bool(graph.get("anomalies"))
    if not details.get("incomplete") and not bad_samples and not graph_anomaly:
        return "COMPLETE", ()
    if not samples:
        return "INCOMPLETE", ("Required diagnostic samples",)
    missing: list[str] = []
    required_tokens = ("active_sessions", "active_requests", "blocking", "transactions")
    for token in required_tokens:
        sample = next((item for item in samples
                       if token in str(item.get("collector", item.get("source", ""))).lower()), None)
        if sample is None or not sample.get("success", True) or sample.get("truncated"):
            missing.append(token.replace("_", " ").title())
    return "INCOMPLETE" if len(missing) >= 3 else "PARTIAL", tuple(missing or ["Diagnostic evidence"])


def _sessions(details: dict[str, Any]) -> list[dict[str, Any]]:
    request = details.get("request")
    if isinstance(request, dict):
        return [request]
    for key in ("top_requests", "top_queries", "long_running"):
        value = details.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _wait_summary(details: dict[str, Any]) -> dict[str, str] | None:
    rows = _sessions(details)
    for row in rows:
        event = _first(row, "wait_type", "wait_event", "event", "current_wait")
        category = _first(row, "wait_event_type", "wait_class", "wait_category")
        if not event and not category:
            continue
        event_text = str(event) if event else "UNKNOWN"
        category_text = str(category) if category else "UNKNOWN"
        upper = event_text.upper()
        if category_text == "UNKNOWN":
            category_text = ("LOCK" if upper.startswith("LCK_M_") or "LOCK" in upper else
                             "IO" if "IO" in upper or "READ" in upper or "WRITELOG" in upper else
                             "CPU_PARALLELISM" if upper.startswith("CX") else "UNKNOWN")
        return {"category": category_text, "event": event_text}
    return None


def build_incident_summary(report: DiagnosticReport, report_path: str | None = None) -> IncidentSummary:
    """Normalize slow and disk-growth evidence without inferring missing facts."""
    details = getattr(report, "details", {})
    incomplete = bool(details.get("incomplete"))
    completeness, missing = _quality(details, report.scenario)
    findings = list(report.findings)
    title_map = {
        "BLOCKING": "BLOCKING", "ROOT_BLOCKER": "BLOCKING",
        "IDLE_TRANSACTION_BLOCKER": "IDLE_TRANSACTION", "IDLE_TRANSACTION": "IDLE_TRANSACTION",
        "LONG_RUNNING_SQL": "LONG_RUNNING_SQL", "HIGH_CONCURRENCY": "HIGH_CONCURRENCY",
        "REPLICATION_LAG": "REPLICATION_LAG", "REPLICATION_STOPPED": "REPLICATION_LAG",
        "APPLY_STOPPED": "REPLICATION_LAG", "APPLY_FAILURE": "REPLICATION_LAG",
        "TRANSPORT_FAILURE": "REPLICATION_LAG", "DISCONNECTED_REPLICA": "REPLICATION_LAG",
        "SOURCE_CONNECTION_ERROR": "REPLICATION_LAG", "IO_THREAD_STOPPED": "REPLICATION_LAG",
        "SQL_THREAD_STOPPED": "REPLICATION_LAG", "SQL_APPLY_ERROR": "REPLICATION_LAG",
        "WAL_RETENTION": "REPLICATION_LAG", "LARGE_RETAINED_WAL": "REPLICATION_LAG",
        "RELAY_LOG_BACKLOG": "REPLICATION_LAG", "INACTIVE_PHYSICAL_SLOT": "REPLICATION_LAG",
    }
    problem_candidates = [title_map[f.title] for f in findings if f.title in title_map]
    if report.scenario == "disk-growth":
        for finding in findings:
            title = finding.title.upper()
            if any(token in title for token in ("LOG", "WAL", "BINLOG", "REDO", "ARCHIVE", "FRA",
                                                "TRANSACTION")):
                problem_candidates.append("LOG_PRESSURE")
            elif any(token in title for token in ("SPACE", "DATAFILE", "TABLESPACE", "TEMP", "UNDO")):
                problem_candidates.append("SPACE_PRESSURE")
            elif "REPLICA" in title or "REPLICATION" in title:
                problem_candidates.append("REPLICATION_LAG")
    primary = next((name for name in ("BLOCKING", "LONG_RUNNING_SQL", "HIGH_CONCURRENCY",
                                      "IDLE_TRANSACTION", "LOG_PRESSURE", "SPACE_PRESSURE",
                                      "REPLICATION_LAG") if name in problem_candidates), "UNKNOWN")
    if report.scenario == "disk-growth" and not incomplete:
        primary = "NONE" if primary == "UNKNOWN" else primary
    active_now = (primary not in {"UNKNOWN", "NONE"} or
                  report.scenario == "plan" and details.get("metrics_scope") == "current_request")
    has_warning_finding = any(f.severity.value in {"WARNING", "CRITICAL"} for f in findings)
    severity = ("CRITICAL" if report.scenario == "disk-growth" and
                any(f.severity.value == "CRITICAL" for f in findings) else
                "WARNING" if report.scenario == "disk-growth" and
                any(f.severity.value == "WARNING" for f in findings) else
                "WARNING" if report.scenario == "disk-growth" and incomplete else
                "CRITICAL" if report.scenario == "replication" and
                any(f.severity.value == "CRITICAL" for f in findings) else
                "HIGH" if report.scenario == "replication" and
                any(f.severity.value == "HIGH" for f in findings) else
                "CRITICAL" if any(f.title in {"IDLE_TRANSACTION_BLOCKER", "IDLE_TRANSACTION"}
                                  and f.severity.value == "CRITICAL" for f in findings) else
                "HIGH" if primary == "BLOCKING" else
                "INFO" if report.scenario == "disk-growth" else
                "WARNING" if primary != "UNKNOWN" or incomplete or has_warning_finding else "INFO")
    status = ("UNKNOWN" if completeness == "INCOMPLETE" else
              "CRITICAL" if severity == "CRITICAL" else
              "DEGRADED" if active_now or has_warning_finding or severity in {"WARNING", "HIGH"}
              or report.scenario != "disk-growth" and completeness == "PARTIAL" else "HEALTHY")

    concurrency = details.get("concurrency", {})
    impact: dict[str, int] = {}
    for label, names in (
        ("active_requests", ("active_requests", "active_queries", "active_statements", "active_sql_sessions")),
        ("blocked_requests", ("blocked_requests", "blocked_sessions", "blocked_statements")),
        ("root_blockers", ()),
    ):
        value = _first(concurrency, *names) if names else None
        if label == "root_blockers":
            value = len(details.get("roots", [])) if isinstance(details.get("roots"), list) else None
        metric_count = _int(value)
        if metric_count is not None:
            impact[label] = metric_count
    if report.scenario == "plan" and details.get("metrics_scope") == "current_request":
        impact["active_requests"] = 1
    long_rows = details.get("long_running", [])
    if "long_running" in details and isinstance(long_rows, list):
        long_finding = next((f for f in findings if f.title == "LONG_RUNNING_SQL"), None)
        count = _int(long_finding.evidence.get("count")) if long_finding else None
        impact["long_running"] = count if count is not None else len(long_rows)

    roots = details.get("roots", [])
    root = max((r for r in roots if isinstance(r, dict)),
               key=lambda r: _int(_first(r, "blocked_count", "blocked_session_count")) or 0,
               default=None) if isinstance(roots, list) else None
    root_count = _int(_first(root or {}, "blocked_count", "blocked_session_count")) or 0
    root_session = _first(root or {}, "session_id", "pid", "processlist_id", "sid")
    transaction = _is_open_transaction(root or {})
    root_candidate: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    if root and root_count > 0:
        root_candidate = {
            "session": root_session,
            "reason": "Root blocker with confirmed downstream blocked sessions" +
                      (" and verified open transaction" if transaction else
                       " and observed transaction with unverified identity"
                       if root.get("transaction_observed") is True else ""),
            "status": _first(root, "status", "state", "state_name", "command_name"),
            "blocked_sessions": root_count,
            "open_transaction": "YES" if transaction else "NO" if transaction is False else "UNKNOWN",
            "transaction_observed": root.get("transaction_observed", "UNKNOWN"),
            "transaction_identity_verified": root.get("transaction_identity_verified", "UNKNOWN"),
            "transaction_age_seconds": _int(root.get("transaction_age_seconds")),
            "transaction_id": _first(root, "transaction_id", "trx_id"),
            "transaction_start_time": root.get("transaction_start_time"),
            "transaction_start_time_source": root.get("transaction_start_time_source"),
            "transaction_identity_quality": root.get("transaction_identity_quality", "UNKNOWN"),
        }
        source = {key: value for key, value in {
            "login": _first(root, "login_name", "usename", "user_name", "username"),
            "host": _first(root, "host_name", "client_hostname", "host_raw", "machine"),
            "client_ip": _first(root, "client_net_address", "client_addr", "host_ip", "client_ip"),
            "program": _first(root, "program_name", "application_name", "program", "module"),
        }.items() if value not in (None, "")}

    requests = _sessions(details)
    duration_values: list[tuple[int, str]] = []
    for row in requests:
        duration_input: Any = _first(row, "elapsed_ms", "duration_ms")
        if duration_input is None and type(row.get("duration_seconds")) is int:
            duration_input = row["duration_seconds"] * 1000
        duration_result = _duration(duration_input)
        if duration_result:
            duration_values.append(duration_result)
    timing: dict[str, str] = {}
    transaction_rows = details.get("transactions", details.get("idle_transactions", []))
    ages: list[int | None] = [_int(row.get("transaction_age_seconds")) for row in transaction_rows
                              if isinstance(row, dict)] if isinstance(transaction_rows, list) else []
    root_age = _int((root or {}).get("transaction_age_seconds"))
    valid_ages = [age for age in [*ages, root_age] if age is not None]
    if valid_ages:
        oldest_tx_duration = _duration(max(valid_ages) * 1000)
        if oldest_tx_duration:
            timing["oldest_transaction"] = oldest_tx_duration[1]
    if duration_values:
        timing["longest_request"] = max(duration_values, key=lambda item: item[0])[1]
    blocked_durations = []
    for row in requests:
        blocker = _first(row, "blocking_session_id", "blocking_pid", "blocking_pids")
        blocked = _int(blocker) not in (None, 0) or isinstance(blocker, list) and bool(blocker)
        value = _duration(_first(row, "wait_time_ms", "wait_ms", "wait_time")) if blocked else None
        if value:
            blocked_durations.append(value)
    if blocked_durations:
        timing["longest_block"] = max(blocked_durations, key=lambda item: item[0])[1]
    wait = _wait_summary(details)

    actions: list[RecoveryAction] = []
    for decision in manual_action_decisions(report):
        if decision.get("status") != "AVAILABLE":
            continue
        finding_session = decision.get("target_session")
        root_finding = next((item for item in findings if item.title == "ROOT_BLOCKER"
                             and str(item.session or _first(item.evidence, "session_id", "pid",
                                                             "processlist_id", "sid")) ==
                             str(finding_session)), None)
        if root_finding is None:
            continue
        finding_root = root_finding.evidence
        finding_impact = _int(_first(finding_root, "blocked_count", "blocked_session_count")) or 0
        if not finding_impact or finding_session in (None, ""):
            continue
        allowed_commands = set(decision.get("commands", ()))
        open_transaction = _is_open_transaction(finding_root)
        for command in root_finding.reference_commands:
            if command.text not in allowed_commands:
                continue
            command_upper = command.text.upper()
            action_type = ("CANCEL_QUERY" if "PG_CANCEL_BACKEND" in command_upper
                           or "KILL QUERY" in command_upper else "TERMINATE_SESSION")
            actions.append(RecoveryAction(
                action_type=action_type, target_session=str(finding_session),
                reason=f"Root blocker affecting {finding_impact} sessions",
                reference_command=command.text,
                open_transaction="YES" if open_transaction else
                "NO" if open_transaction is False else "UNKNOWN",
                rollback_risk="UNKNOWN", evidence_complete=True))

    immediate = None
    if active_now:
        immediate = {"priority": severity,
                     "action": ("Review the execution plan and runtime metrics in the detailed report."
                                if report.scenario == "plan" else
                                "Review replication transport, apply state, and vendor evidence with the database owner."
                                if report.scenario == "replication" else
                                f"Contact the owning application and review blocker session {root_session}."
                                if primary == "BLOCKING" and root_session is not None else
                                "Review the detailed report and validate the observed database impact."),
                     "approval_required": True}
    elif completeness == "COMPLETE":
        immediate = {"priority": "INFO",
                     "action": "No obvious active database-side issue detected in this snapshot.",
                     "approval_required": False}

    risk = None
    if actions:
        risk = {"open_transaction": actions[0].open_transaction,
                "rollback_risk": actions[0].rollback_risk,
                "rollback_duration": "UNKNOWN"}
    long_finding = next((f for f in findings if f.title == "LONG_RUNNING_SQL"), None)
    long_count = _int(long_finding.evidence.get("count")) if long_finding else None
    secondary: dict[str, int] = {}
    if "long_running" in details:
        secondary["long_running"] = (long_count if long_count is not None else
                                      len(long_rows) if isinstance(long_rows, list) else 0)
    idle_rows = details.get("idle_transactions")
    if isinstance(idle_rows, list):
        secondary["idle_transactions"] = len(idle_rows)
    collector_summary = tuple({
        "collector": item.get("collector", item.get("source")),
        "success": item.get("success", True),
        "truncated": item.get("truncated", False),
        "row_count": item.get("row_count"),
        "error_type": item.get("error_type"),
    } for item in details.get("samples", []) if isinstance(item, dict))
    return IncidentSummary(
        snapshot_time=report.diagnosed_at.isoformat(), data_completeness=completeness,
        missing_evidence=missing,
        required_evidence_status=("COMPLETE" if details.get("required_evidence_complete", not incomplete)
                                  else "INCOMPLETE") if report.scenario == "disk-growth" else "NOT_APPLICABLE",
        optional_evidence_status=(str(details.get("optional_evidence_status", "COMPLETE"))
                                  if report.scenario == "disk-growth" else "NOT_APPLICABLE"),
        unavailable_optional_evidence=tuple(str(item) for item in details.get("optional_unavailable", []))
        if report.scenario == "disk-growth" else (),
        status=status, severity=severity,
        primary_problem=primary,
        observed_now=("ACTIVE" if active_now else
                      "UNKNOWN" if completeness == "INCOMPLETE" else
                      "No core storage risk observed in available required evidence."
                      if report.scenario == "disk-growth" else
                      "NOT_DETECTED" if completeness == "COMPLETE" else "UNKNOWN"),
        impact=impact, timing=timing, wait=wait, root_cause_candidate=root_candidate,
        source=source, immediate_action=immediate, recovery_actions=tuple(actions),
        termination_risk=risk, report_path=report_path, secondary_signals=secondary,
        collector_summary=collector_summary)


def manual_action_decisions(report: DiagnosticReport) -> tuple[dict[str, Any], ...]:
    """Expose the same evidence-backed manual-action decision to every presenter."""
    decisions: list[dict[str, Any]] = []
    for finding in report.findings:
        if finding.title != "ROOT_BLOCKER":
            continue
        evidence = finding.evidence
        detail = evidence.get("manual_action", {})
        if not isinstance(detail, dict):
            detail = {}
        commands = tuple(command.text for command in finding.reference_commands)
        producer_approved = detail.get("status") == "AVAILABLE"
        report_details = getattr(report, "details", {})
        emergency_complete = report_details.get(
            "emergency_evidence_complete", not report_details.get("incomplete", False))
        producer_gated = report.instance.db_type.value in {"postgresql", "oracle"}
        graph = report_details.get("blocking_graph", {})
        graph_reliable = not isinstance(graph, dict) or not graph.get("anomalies")
        # PostgreSQL and Oracle producers validate session/transaction identity;
        # snapshot completeness must never override an explicit suppression.
        available = (bool(commands) and producer_approved and graph_reliable if producer_gated else
                     bool(commands) and (producer_approved or emergency_complete) and graph_reliable)
        unavailable_reason = ("blocking graph contains invalid or cyclic edges" if not graph_reliable else
                              detail.get("reason",
                                         "Emergency root-blocker evidence is incomplete or identity evidence is insufficient."))
        decisions.append({
            "status": "AVAILABLE" if available else "SUPPRESSED",
            "target_session": finding.session or _first(
                evidence, "session_id", "pid", "processlist_id", "sid"),
            "target_type": detail.get("target_type", "SESSION_ID"),
            "commands": commands if available else (),
            "reason": None if available else unavailable_reason,
            "identity_evidence": detail.get("identity_evidence", evidence),
            "action_type": detail.get("action_type"),
            "transaction_confirmed": detail.get("transaction_confirmed"),
            "recheck_required": True,
            "toolkit_executed": False,
        })
    return tuple(decisions)


def build_priority_candidates(report: DiagnosticReport, *, include_blocking_victims: bool = False
                              ) -> tuple[dict[str, Any], ...]:
    """Create a deterministic five-row review queue from captured slow evidence."""
    if report.scenario != "slow":
        return ()
    details = getattr(report, "details", {})
    db_type = report.instance.db_type.value
    samples = _slow_sample_index(details)
    graph = details.get("blocking_graph", {})
    graph = graph if isinstance(graph, dict) else {}
    graph_source = ("active_requests" if db_type == "sqlserver" else
                    "active_sessions" if db_type == "oracle" else "blocking")
    graph_sample = samples.get(graph_source)
    graph_complete = (graph_sample is not None and graph_sample.get("success") is True
                      and not graph_sample.get("truncated") and not graph.get("anomalies")
                      and not graph.get("unresolved") and not graph.get("identity_conflicts"))
    graph_children = graph.get("children", {})
    graph_children = graph_children if isinstance(graph_children, dict) else {}
    graph_roots = {str(value) for value in graph.get("roots", [])}
    parent_map: dict[str, set[str]] = {}
    child_map: dict[str, set[str]] = {}
    for parent, children in graph_children.items():
        if not isinstance(children, list):
            continue
        parent_text = str(parent)
        child_map.setdefault(parent_text, set())
        for child in children:
            child_text = str(child)
            child_map[parent_text].add(child_text)
            parent_map.setdefault(child_text, set()).add(parent_text)
    if isinstance(graph.get("parents"), dict):
        for child, parents in graph["parents"].items():
            if isinstance(parents, list):
                parent_map[str(child)] = {str(parent) for parent in parents}
    sample_rows = [item for item in details.get("samples", []) if isinstance(item, dict)]
    timestamps = [str(item.get("finished_at")) for item in sample_rows if item.get("finished_at")]
    captured = max(timestamps, default=report.diagnosed_at.isoformat())
    sessions = details.get("all_sessions", details.get("sessions", []))
    session_map: dict[str, dict[str, Any]] = {}
    session_id_counts: dict[str, int] = {}
    for row in sessions if isinstance(sessions, list) else []:
        if isinstance(row, dict):
            sid = _first(row, "session_id", "pid", "processlist_id", "sid")
            if sid is not None:
                sid_text = str(sid)
                session_id_counts[sid_text] = session_id_counts.get(sid_text, 0) + 1
                inst_id = row.get("inst_id")
                key = f"{inst_id}:{sid_text}" if inst_id is not None else sid_text
                session_map[key] = row
    selected: dict[str, dict[str, Any]] = {}

    def record_classification(target: dict[str, Any], kind: str, label: str | None,
                              references: tuple[dict[str, Any], ...],
                              uncertainty: tuple[str, ...]) -> None:
        rule_label = label or (_TRIAGE_KIND_RULES.get(kind) if uncertainty else None)
        if not rule_label or rule_label not in _TRIAGE_RULE_IDS:
            return
        evidence = target.setdefault("classification_evidence", {})
        entry = evidence.setdefault(rule_label, {
            "rule_id": _TRIAGE_RULE_IDS[rule_label],
            "status": "UNKNOWN" if label is None else "CONFIRMED",
            "evidence_references": [],
            "uncertainty_reasons": [],
        })
        entry["evidence_references"].extend(
            reference for reference in references if reference not in entry["evidence_references"])
        entry["uncertainty_reasons"].extend(
            reason for reason in uncertainty if reason not in entry["uncertainty_reasons"])
        if entry["uncertainty_reasons"]:
            entry["status"] = ("PARTIAL" if label is not None or entry["status"] == "CONFIRMED"
                               else "UNKNOWN")
        elif label is not None:
            entry["status"] = "CONFIRMED"

    def add(row: dict[str, Any], kind: str, reason: str, *, label: str | None = None,
            evidence_references: tuple[dict[str, Any], ...] = (),
            uncertainty_reasons: tuple[str, ...] = (), add_signal: bool = True) -> None:
        sid = _first(row, "session_id", "pid", "processlist_id", "sid")
        if sid is None:
            return
        inst_id = row.get("inst_id")
        identity_key = f"{inst_id}:{sid}" if inst_id is not None else str(sid)
        previous = selected.get(identity_key)
        if previous is not None:
            categories = list(previous.get("categories", (previous.get("category"),)))
            if add_signal and kind not in categories:
                categories.append(kind)
            reasons = list(previous.get("reasons", (previous.get("reason", ""),)))
            if add_signal and reason not in reasons:
                reasons.append(reason)
            previous["categories"] = categories
            previous["reasons"] = reasons
            previous["reason"] = "；".join(item.rstrip("。；; ") for item in reasons)
            labels = list(previous.get("classification_labels", []))
            if label and label not in labels:
                labels.append(label)
            previous["classification_labels"] = labels
            refs = list(previous.get("evidence_references", []))
            refs.extend(ref for ref in evidence_references if ref not in refs)
            previous["evidence_references"] = refs
            uncertainties = list(previous.get("uncertainty_reasons", []))
            uncertainties.extend(item for item in uncertainty_reasons if item not in uncertainties)
            previous["uncertainty_reasons"] = uncertainties
            previous["classification_status"] = (
                "UNKNOWN" if not labels else "PARTIAL" if uncertainties else "CONFIRMED")
            record_classification(previous, kind, label, evidence_references, uncertainty_reasons)
            return
        session_key = f"{inst_id}:{sid}" if inst_id is not None else str(sid)
        session = session_map.get(session_key, {})
        if not session and inst_id is None and session_id_counts.get(str(sid)) == 1:
            # A single unqualified session row can safely enrich a row whose
            # source omits instance identity. Multiple matches are ambiguous.
            session = next((item for key, item in session_map.items()
                            if key.endswith(f":{sid}") or key == str(sid)), {})
        request_rows = details.get("all_requests", details.get("active_requests", details.get("top_queries", [])))
        request = next((item for item in request_rows if isinstance(item, dict)
                        and str(_first(item, "session_id", "pid", "processlist_id", "sid")) == str(sid)
                        and (item.get("inst_id") == inst_id if inst_id is not None
                             else item.get("inst_id") is None)), {}) \
            if isinstance(request_rows, list) else {}
        blocked = row.get("blocked_count", row.get("blocked_session_count", 0))
        running = _first(row, "duration_seconds", "time_seconds")
        if running is None:
            elapsed = _first(row, "elapsed_ms", "duration_ms")
            running = round(elapsed / 1000, 1) if type(elapsed) in (int, float) else None
        transaction_quality = row.get("transaction_identity_quality")
        if transaction_quality in {"AMBIGUOUS", "RECHECK_REQUIRED"}:
            reason = f"{reason} 事务身份：{transaction_quality}，需重新核验。"
        login_identity = (_first(row, "login_time", "logon_time", "backend_start", "connect_time") or
                          _first(session, "login_time", "logon_time", "backend_start", "connect_time"))
        manual_action = row.get("manual_action")
        identity_evidence = (manual_action.get("identity_evidence")
                             if isinstance(manual_action, dict) else None)
        session_checks = (identity_evidence.get("session_checks")
                          if isinstance(identity_evidence, dict) else None)
        sample_session_identity_verified = (
            report.instance.db_type.value == "oracle"
            and isinstance(session_checks, dict) and bool(session_checks)
            and all(value is True for value in session_checks.values())
        )
        if report.instance.db_type.value == "mysql" and transaction_quality == "VERIFIED":
            identity_quality = "VERIFIED"
        elif sample_session_identity_verified:
            identity_quality = "VERIFIED_AT_SAMPLE"
        elif transaction_quality in {"AMBIGUOUS", "IDENTITY_INCOMPLETE", "RECHECK_REQUIRED"}:
            identity_quality = str(transaction_quality)
        else:
            identity_quality = "IDENTITY_INCOMPLETE" if not login_identity else "RECHECK_REQUIRED"
        sql_id = _first(row, "sql_id", "sql_handle", "query_id", "digest") or \
            _first(request, "sql_id", "sql_handle", "query_id", "digest")
        sql_id_type = next((key for key in ("sql_id", "sql_handle", "query_id", "digest")
                            if row.get(key) is not None), None) or \
            next((key for key in ("sql_id", "sql_handle", "query_id", "digest")
                  if request.get(key) is not None), None)
        selected[identity_key] = {
            "session_id": (f"{inst_id}:{sid}" if report.instance.db_type.value == "oracle"
                           and inst_id is not None else sid),
            "category": kind, "categories": [kind],
            "classification_labels": [label] if label else [],
            "classification_evidence": {},
            "classification_status": "PARTIAL" if label and uncertainty_reasons else
                                     "CONFIRMED" if label else "UNKNOWN",
            "evidence_references": list(evidence_references),
            "uncertainty_reasons": list(uncertainty_reasons),
            "root_cause_status": "NOT_ESTABLISHED",
            "reason": reason, "reasons": [reason],
            "session_key": identity_key,
            "blocked_session_count": blocked if type(blocked) is int else 0,
            "sql_id": sql_id,
            "sql_id_type": sql_id_type,
            "request_id": _first(row, "request_id") or _first(request, "request_id"),
            "mysql_connection_id": _first(row, "processlist_id") or
                                   _first(request, "processlist_id")
                                   if report.instance.db_type.value == "mysql" else None,
            "performance_schema_thread_id": _first(row, "thread_id") or
                                             _first(request, "thread_id")
                                             if report.instance.db_type.value == "mysql" else None,
            "transaction_id": _first(row, "transaction_id", "trx_id", "xid"),
            "username": _first(row, "login_name", "usename", "user_name", "username") or
                        _first(session, "login_name", "usename", "user_name", "username"),
            "database": _first(row, "database_name", "datname") or
                        _first(session, "database_name", "datname"),
            "host": _first(row, "host_name", "client_hostname", "host_raw", "machine") or
                    _first(session, "host_name", "client_hostname", "host_raw", "machine"),
            "client_ip": _first(row, "client_net_address", "client_addr", "host_ip") or
                         _first(session, "client_net_address", "client_addr", "host_ip"),
            "application": _first(row, "program_name", "application_name", "program", "module") or
                           _first(session, "program_name", "application_name", "program", "module"),
            "state": _first(row, "status", "state", "state_name", "command_name") or
                     _first(session, "status", "state", "state_name", "command_name"),
            "running_seconds": running,
            "transaction_age_seconds": row.get("transaction_age_seconds"),
            "transaction_start_time_source": row.get("transaction_start_time_source"),
            "transaction_identity_quality": row.get("transaction_identity_quality"),
            "manual_action_status": (manual_action.get("status")
                                     if isinstance(manual_action, dict) else None),
            "oracle_inst_id": (_first(row, "inst_id") or _first(session, "inst_id")
                               if report.instance.db_type.value == "oracle" else None),
            "oracle_serial_number": (_first(row, "serial_number") or _first(session, "serial_number")
                                     if report.instance.db_type.value == "oracle" else None),
            "transaction_start_time": row.get("transaction_start_time")
            if report.instance.db_type.value == "oracle" else None,
            "transaction_start_time_text": row.get("transaction_start_time_text")
            if report.instance.db_type.value == "oracle" else None,
            "login_time": _first(row, "login_time", "logon_time", "backend_start", "connect_time") or
                          _first(session, "login_time", "logon_time", "backend_start", "connect_time"),
            "evidence_timestamp": captured,
            "evidence_quality": details.get("emergency_evidence_status", "UNKNOWN"),
            "identity_recheck": "REQUIRED_BEFORE_MANUAL_ACTION",
            "identity_quality": identity_quality,
            "identity_reuse_note": "会话 ID 可能已复用；必须在人工操作前重新核对当前会话身份。",
        }
        record_classification(selected[identity_key], kind, label,
                              evidence_references, uncertainty_reasons)

    roots = [row for row in details.get("roots", []) if isinstance(row, dict)]
    sessions_list = details.get("all_sessions", details.get("sessions", []))
    sessions_list = [row for row in sessions_list if isinstance(row, dict)] \
        if isinstance(sessions_list, list) else []
    request_rows = details.get("all_requests", details.get("active_requests", []))
    request_rows = [row for row in request_rows if isinstance(row, dict)] \
        if isinstance(request_rows, list) else []

    oracle_serials: dict[str, set[str]] = {}
    if db_type == "oracle":
        for source_rows in (sessions_list, request_rows, roots,
                            details.get("idle_transactions", [])):
            if not isinstance(source_rows, list):
                continue
            for source_row in source_rows:
                if not isinstance(source_row, dict):
                    continue
                if (source_row.get("inst_id") is None or source_row.get("sid") is None
                        or source_row.get("serial_number") is None):
                    continue
                source_key = f"{source_row['inst_id']}:{source_row['sid']}"
                oracle_serials.setdefault(source_key, set()).add(str(source_row["serial_number"]))
    oracle_identity_conflicts = {key for key, serials in oracle_serials.items() if len(serials) > 1}

    def oracle_identity_complete(key: str, row: dict[str, Any]) -> bool:
        if db_type != "oracle":
            return True
        session = session_map.get(key, {})
        return (row.get("inst_id") is not None and row.get("sid") is not None
                and (row.get("serial_number") is not None
                     or session.get("serial_number") is not None))

    def native_key(row: dict[str, Any]) -> str | None:
        if db_type == "oracle":
            if row.get("session_key") is not None:
                return str(row["session_key"])
            if row.get("inst_id") is not None and row.get("sid") is not None:
                return f"{row['inst_id']}:{row['sid']}"
        sid = _first(row, "session_id", "pid", "processlist_id", "sid")
        return str(sid) if sid is not None else None

    tx_titles = {"IDLE_TRANSACTION", "IDLE_IN_TRANSACTION", "IDLE_TRANSACTION_BLOCKER",
                 "LONG_TRANSACTION", "LONG_RUNNING_TRANSACTION"}
    tx_findings = [item for item in report.findings if item.title in tx_titles]

    def has_transaction_finding(row: dict[str, Any]) -> bool:
        key = native_key(row)
        if key is None:
            return False
        for finding in tx_findings:
            evidence = finding.evidence
            candidate_key = native_key(evidence)
            if candidate_key == key:
                return True
            if str(finding.session or "") in {key, str(_first(row, "session_id", "pid",
                                                                 "processlist_id", "sid"))}:
                return True
        return False

    for row in roots:
        root_key = native_key(row)
        if root_key is None:
            continue
        downstream = len(child_map.get(root_key, set()))
        identity_conflict = db_type == "oracle" and root_key in oracle_identity_conflicts
        root_complete = oracle_identity_complete(root_key, row)
        root_confirmed = bool(graph_complete and root_complete and not identity_conflict
                              and root_key in graph_roots and
                              (downstream > 0 or row.get("blocked_count", row.get(
                                  "blocked_session_count", 0)) > 0))
        root_ref = _slow_sample_reference(details, graph_source, "details.blocking_graph.roots",
                                          {"session_key": root_key, "downstream_count": row.get(
                                              "blocked_count", row.get("blocked_session_count"))})
        root_uncertainty = (() if root_confirmed else
                            (("Oracle INST_ID/SID maps to conflicting SERIAL# values in this snapshot.",)
                             if identity_conflict else
                             ("Oracle session identity is incomplete (INST_ID/SID/SERIAL#).",)
                             if not root_complete else
                             ("Root attribution is incomplete, anomalous, or not backed by a complete blocking sample.",)))
        root_refs = [root_ref]
        age = row.get("transaction_age_seconds")
        tx_label = (root_complete and not identity_conflict and has_transaction_finding(row)
                    and type(age) is int and age > 0
                    and row.get("unfinished_transaction_confirmed") is not False)
        if tx_label:
            tx_source = "transactions"
            root_refs.append(_slow_sample_reference(
                details, tx_source, "details.roots[session].transaction_age_seconds",
                {"transaction_age_seconds": age,
                 "transaction_id": _first(row, "transaction_id", "trx_id", "xid")}))
        add(row, "ROOT_BLOCKER", "已采样阻塞关系表明该会话为根阻塞候选。",
            label="ROOT_BLOCKER" if root_confirmed else None,
            evidence_references=tuple(root_refs), uncertainty_reasons=root_uncertainty)
        if tx_label:
            add(row, "LONG_RUNNING_TRANSACTION", "现有事务分析已按事务持续时间/阻塞语义标记，且有事务时长证据。",
                label="LONG_RUNNING_TRANSACTION",
                evidence_references=tuple(root_refs[1:]), add_signal=False)
        elif (row.get("unfinished_transaction_confirmed") is not False
              and (identity_conflict or not root_complete or age is None
                   or not has_transaction_finding(row))):
            tx_ref = _slow_sample_reference(
                details, "transactions", "details.roots[session].transaction_age_seconds",
                {"transaction_age_seconds": age,
                 "transaction_id": _first(row, "transaction_id", "trx_id", "xid")})
            add(row, "LONG_RUNNING_TRANSACTION", "根会话的事务持续时间/身份证据不足，不能判断事务是否长期。",
                evidence_references=(tx_ref,),
                uncertainty_reasons=("Transaction age or verified session-to-transaction evidence is unavailable.",),
                add_signal=False)
    for row in details.get("idle_transactions", []):
        if not isinstance(row, dict):
            continue
        age = row.get("transaction_age_seconds")
        tx_label = has_transaction_finding(row) and type(age) is int and age > 0
        add(row, "IDLE_OPEN_TRANSACTION", "会话与未结束事务证据关联。",
            label="LONG_RUNNING_TRANSACTION" if tx_label else None,
            evidence_references=(_slow_sample_reference(
                details, "transactions", "details.idle_transactions[session].transaction_age_seconds",
                {"transaction_age_seconds": age,
                 "transaction_id_present": _first(row, "transaction_id", "trx_id", "xid") is not None}),),
            uncertainty_reasons=() if tx_label else
            ("Transaction duration/identity evidence did not meet the existing engine-specific finding contract.",))

    # Every observed child edge can identify a victim; a node with both an
    # upstream parent and downstream children is also an intermediate blocker.
    for victim_key, direct_parents in (sorted(parent_map.items()) if include_blocking_victims else ()):
        source_row = next((row for row in [*request_rows, *sessions_list]
                           if native_key(row) == victim_key), None)
        if source_row is None and db_type == "oracle":
            source_row = next((row for row in [*request_rows, *sessions_list]
                               if (candidate_key := native_key(row)) is not None
                               and candidate_key.endswith(f":{victim_key}")), None)
        if source_row is None:
            source_row = {"session_id": victim_key}
            if db_type == "oracle" and ":" in victim_key:
                inst_id, sid = victim_key.split(":", 1)
                source_row = {"inst_id": inst_id, "sid": sid, "session_key": victim_key}
            elif db_type == "mysql":
                source_row = {"processlist_id": int(victim_key)} if victim_key.isdigit() else source_row
            elif db_type == "postgresql":
                source_row = {"pid": int(victim_key)} if victim_key.isdigit() else source_row
            elif db_type == "sqlserver":
                source_row = {"session_id": int(victim_key)} if victim_key.isdigit() else source_row
        parents_observed = sorted(direct_parents)
        is_intermediate = bool(child_map.get(victim_key))
        identity_conflict = db_type == "oracle" and victim_key in oracle_identity_conflicts
        victim_identity_complete = (source_row is not None
                                    and oracle_identity_complete(victim_key, source_row))
        edge_integrity = "COMPLETE" if graph_complete else "PARTIAL"
        uncertainty = (("Oracle INST_ID/SID maps to conflicting SERIAL# values in this snapshot.",)
                       if identity_conflict else () if graph_complete else
                       ("Blocking evidence is incomplete; root attribution and independence are withheld.",))
        if db_type == "oracle" and not victim_identity_complete:
            uncertainty = (*uncertainty, "Oracle session identity is incomplete (INST_ID/SID/SERIAL#).")
        edge_refs = (_slow_sample_reference(details, graph_source,
                                            f"details.blocking_graph.parents[{victim_key}]",
                                            {"direct_blocker_ids": parents_observed,
                                             "downstream_count": len(child_map.get(victim_key, set())),
                                             "graph_integrity": edge_integrity}),)
        add(source_row, "BLOCKED_VICTIM", "阻塞图中存在指向该会话的上游边。",
            label="BLOCKING_VICTIM" if victim_identity_complete and not identity_conflict else None,
            evidence_references=edge_refs,
            uncertainty_reasons=uncertainty)
        if is_intermediate:
            add(source_row, "INTERMEDIATE_BLOCKER", "该会话同时有上游阻塞边和下游受阻会话。",
                label="INTERMEDIATE_BLOCKER" if victim_identity_complete and not identity_conflict else None,
                evidence_references=edge_refs,
                uncertainty_reasons=uncertainty)
        slow_row = next((item for item in details.get("long_running", [])
                         if isinstance(item, dict) and native_key(item) == victim_key), None) \
            if isinstance(details.get("long_running", []), list) else None
        if slow_row is not None:
            slow_ref = _slow_sample_reference(
                details, "long_running", "details.long_running[session].duration",
                {"duration_ms": _first(slow_row, "elapsed_ms", "duration_ms"),
                 "wait_event": _first(slow_row, "wait_event", "wait_type", "current_wait")})
            add(source_row, "LONG_RUNNING_SQL", "该会话同时有现有慢 SQL/长请求证据，但属于已知阻塞链。",
                evidence_references=(slow_ref,))
    requests = details.get("all_requests", details.get("active_requests", details.get("top_queries", [])))
    long_finding = next((item for item in report.findings if item.title == "LONG_RUNNING_SQL"), None)
    threshold = long_finding.evidence.get("threshold_seconds") if long_finding else None
    graph = details.get("blocking_graph", {})
    blocked_ids: set[str] = set()
    if isinstance(graph, dict) and isinstance(graph.get("children"), dict):
        blocked_ids = {str(child) for children in graph["children"].values()
                       if isinstance(children, list) for child in children}
    configured_long = details.get("long_running", [])
    long_rows = [item for item in configured_long if isinstance(item, dict)] \
        if isinstance(configured_long, list) else []
    if not long_rows and isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        candidates = requests if isinstance(requests, list) else []
        for request in candidates:
            if not isinstance(request, dict):
                continue
            elapsed = _first(request, "elapsed_ms", "duration_ms")
            if elapsed is None:
                seconds = _first(request, "duration_seconds", "time_seconds")
                elapsed = seconds * 1000 if isinstance(seconds, (int, float)) else None
            if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) \
                    and elapsed >= float(threshold) * 1000:
                long_rows.append(request)
    for row in long_rows:
        if not isinstance(row, dict):
            continue
        sid = _first(row, "session_id", "pid", "processlist_id", "sid")
        if sid is None or str(sid) in blocked_ids:
            continue
        blocker = _first(row, "blocking_session_id", "blocking_pid", "blocker_pid")
        if blocker not in (None, 0, "0", ""):
            continue
        if row.get("request_kind") == "ACTIVE_WAIT":
            continue
        sid_key = native_key(row)
        in_blocking_chain = (sid_key in parent_map or sid_key in child_map or sid_key in graph_roots)
        independent = bool(graph_complete and not in_blocking_chain and
                           (db_type != "oracle" or oracle_identity_complete(sid_key or "", row)) and
                           not (db_type == "oracle" and sid_key in oracle_identity_conflicts) and
                           _first(row, "blocking_session_id", "blocking_pid", "blocker_pid")
                           in (None, 0, "0", ""))
        long_ref = _slow_sample_reference(
            details, "active_requests" if db_type == "sqlserver" else "long_running",
            "details.long_running[session].duration", {
                "duration_ms": _first(row, "elapsed_ms", "duration_ms"),
                "duration_seconds": row.get("duration_seconds"),
                "wait_type": _first(row, "wait_type", "wait_event", "current_wait")})
        uncertainty = (() if independent else
                       ("Blocking evidence does not prove this slow request is independent.",))
        add(row, "LONG_RUNNING_SQL", "达到现有请求时长阈值；耗时不证明资源根因。",
            label="INDEPENDENT_SLOW_SQL" if independent else None,
            evidence_references=(long_ref,), uncertainty_reasons=uncertainty)
    ordered = sorted(selected.values(), key=lambda row: (
        -int(row["blocked_session_count"] or 0),
        -int(row["transaction_age_seconds"] or 0)
        if type(row.get("transaction_age_seconds")) is int else 0,
        -float(row["running_seconds"] or 0)
        if type(row.get("running_seconds")) in (int, float) else 0,
        -len(row.get("classification_labels", [])), str(row["session_key"])))[:5]
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return tuple(ordered)


def build_performance_triage(report: DiagnosticReport) -> dict[str, Any]:
    """Expose bounded multi-label slow triage with source and uncertainty references.

    This is a presentation contract over existing Slow evidence. It does not
    execute SQL, infer a root cause, or turn a missing sample into a clean bill
    of health.
    """
    if report.scenario != "slow":
        return {"contract_version": 1, "status": "UNKNOWN", "assessments": [],
                "unknown_reasons": ["Performance triage is only defined for the Slow snapshot."],
                "root_cause_status": "NOT_ESTABLISHED"}

    details = getattr(report, "details", {})
    candidates = build_priority_candidates(report, include_blocking_victims=True)
    assessments: list[dict[str, Any]] = []
    for candidate in candidates:
        identity: dict[str, Any] = {}
        if report.instance.db_type.value == "oracle":
            oracle_session = str(candidate.get("session_key") or candidate.get("session_id") or "")
            oracle_sid = oracle_session.split(":", 1)[-1]
            identity = {key: candidate.get(source) for key, source in (
                ("inst_id", "oracle_inst_id"), ("sid", "session_id"),
                ("serial_number", "oracle_serial_number"), ("sql_id", "sql_id"),
                ("transaction_id", "transaction_id")) if candidate.get(source) is not None}
            identity["sid"] = oracle_sid or None
            if candidate.get("session_key"):
                identity["session_key"] = candidate["session_key"]
        elif report.instance.db_type.value == "mysql":
            identity = {key: candidate.get(source) for key, source in (
                ("processlist_id", "mysql_connection_id"),
                ("thread_id", "performance_schema_thread_id"),
                ("trx_id", "transaction_id")) if candidate.get(source) is not None}
        elif report.instance.db_type.value == "postgresql":
            identity = {"pid": candidate.get("session_id")}
            if candidate.get("login_time") is not None:
                identity["backend_start"] = candidate["login_time"]
        elif report.instance.db_type.value == "sqlserver":
            identity = {"session_id": candidate.get("session_id")}
            if candidate.get("request_id") is not None:
                identity["request_id"] = candidate["request_id"]

        labels = list(candidate.get("classification_labels", []))
        assessments.append({
            "database_type": report.instance.db_type.value,
            "native_identity": identity,
            "classification_status": candidate.get("classification_status", "UNKNOWN"),
            "classifications": labels,
            "rule_ids": [_TRIAGE_RULE_IDS[label] for label in labels if label in _TRIAGE_RULE_IDS],
            "classification_evidence": dict(candidate.get("classification_evidence", {})),
            "observed_signals": list(candidate.get("categories", [])),
            "review_rank": candidate.get("rank"),
            "priority_basis": {
                "blocked_session_count": candidate.get("blocked_session_count", 0),
                "transaction_age_seconds": candidate.get("transaction_age_seconds"),
                "runtime_seconds": candidate.get("running_seconds"),
                "evidence_references_count": len(candidate.get("evidence_references", [])),
            },
            "evidence_references": list(candidate.get("evidence_references", [])),
            "uncertainty_reasons": list(candidate.get("uncertainty_reasons", [])),
            "root_cause_status": "NOT_ESTABLISHED",
            "next_safe_check": "复核同一采样窗口内的数据库原生会话、阻塞和事务证据；不执行处置命令。",
        })

    samples = _slow_sample_index(details)
    db_type = report.instance.db_type.value
    graph_source = ("active_requests" if db_type == "sqlserver" else
                    "active_sessions" if db_type == "oracle" else "blocking")
    graph_sample = samples.get(graph_source)
    graph = details.get("blocking_graph", {})
    graph_complete = (graph_sample is not None and graph_sample.get("success") is True
                      and not graph_sample.get("truncated") and isinstance(graph, dict)
                      and not graph.get("anomalies") and not graph.get("unresolved")
                      and not graph.get("identity_conflicts"))
    unknown_reasons: list[str] = []
    if not graph_complete:
        if graph_sample is None:
            unknown_reasons.append(f"Blocking evidence source {graph_source} is missing.")
        elif graph_sample.get("success") is False:
            unknown_reasons.append(f"Blocking evidence source {graph_source} failed.")
        elif graph_sample.get("truncated"):
            unknown_reasons.append(f"Blocking evidence source {graph_source} was truncated.")
        if isinstance(graph, dict) and graph.get("anomalies"):
            unknown_reasons.append("Blocking graph has cycles, conflicting edges, or identity anomalies.")
        if isinstance(graph, dict) and (graph.get("unresolved") or graph.get("identity_conflicts")):
            unknown_reasons.append("Blocking graph has unresolved sessions or identity conflicts.")
    permission = details.get("permission_readiness", {})
    permission_status = permission.get("status") if isinstance(permission, dict) else None
    if permission_status in {"PARTIAL", "MISSING", "UNKNOWN", "UNSUPPORTED"}:
        unknown_reasons.append(f"Permission readiness is {permission_status}; affected evidence may be incomplete.")
    if not assessments:
        unknown_reasons.append("No triage candidate was observed in this snapshot; this is not a health assertion.")
    assessment_states = {item["classification_status"] for item in assessments}
    status = ("UNKNOWN" if not assessments or assessment_states == {"UNKNOWN"} else
              "PARTIAL" if unknown_reasons or assessment_states != {"CONFIRMED"} else "COMPLETE")
    if details.get("data_visibility") == "PARTIAL" and status == "COMPLETE":
        status = "PARTIAL"
        unknown_reasons.append("Optional diagnostic evidence is unavailable or partial.")
    profile = details.get("diagnostic_profile", {})
    return {
        "contract_version": 1,
        "database_id": report.instance.id,
        "database_type": db_type,
        "server_version": details.get("version"),
        "profile": {key: profile.get(key) for key in (
            "sql_profile_id", "permission_profile_id", "parser_contract_id", "runtime_variant")
                    if isinstance(profile, dict) and profile.get(key) is not None},
        "permission_status": permission_status or "UNKNOWN",
        "snapshot_time": max((str(item.get("finished_at")) for item in samples.values()
                               if item.get("finished_at")), default=report.diagnosed_at.isoformat()),
        "status": status,
        "assessments": assessments,
        "unknown_reasons": list(dict.fromkeys(unknown_reasons)),
        "root_cause_status": "NOT_ESTABLISHED",
    }
