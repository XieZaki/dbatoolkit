"""Database-neutral incident summaries built only from diagnostic evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dba.core import DiagnosticReport


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


def build_priority_candidates(report: DiagnosticReport) -> tuple[dict[str, Any], ...]:
    """Create a deterministic five-row review queue from captured slow evidence."""
    if report.scenario != "slow":
        return ()
    details = getattr(report, "details", {})
    samples = [item for item in details.get("samples", []) if isinstance(item, dict)]
    timestamps = [str(item.get("finished_at")) for item in samples if item.get("finished_at")]
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

    def add(row: dict[str, Any], kind: str, priority: int, reason: str) -> None:
        sid = _first(row, "session_id", "pid", "processlist_id", "sid")
        if sid is None:
            return
        inst_id = row.get("inst_id")
        identity_key = f"{inst_id}:{sid}" if inst_id is not None else str(sid)
        previous = selected.get(identity_key)
        if previous is not None:
            categories = list(previous.get("categories", (previous.get("category"),)))
            if kind not in categories:
                categories.append(kind)
            reasons = list(previous.get("reasons", (previous.get("reason", ""),)))
            if reason not in reasons:
                reasons.append(reason)
            previous["categories"] = categories
            previous["reasons"] = reasons
            previous["reason"] = "；".join(reasons)
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
        login_identity = (_first(row, "login_time", "backend_start", "connect_time") or
                          _first(session, "login_time", "backend_start", "connect_time"))
        if report.instance.db_type.value == "mysql" and transaction_quality == "VERIFIED":
            identity_quality = "VERIFIED"
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
            "reason": reason, "reasons": [reason],
            "session_key": identity_key,
            "blocked_session_count": blocked if type(blocked) is int else 0,
            "sql_id": sql_id,
            "sql_id_type": sql_id_type,
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
            "oracle_inst_id": row.get("inst_id") if report.instance.db_type.value == "oracle" else None,
            "oracle_serial_number": row.get("serial_number")
            if report.instance.db_type.value == "oracle" else None,
            "transaction_start_time": row.get("transaction_start_time")
            if report.instance.db_type.value == "oracle" else None,
            "login_time": _first(row, "login_time", "backend_start", "connect_time") or
                          _first(session, "login_time", "backend_start", "connect_time"),
            "selection_priority": priority, "evidence_timestamp": captured,
            "evidence_quality": details.get("emergency_evidence_status", "UNKNOWN"),
            "identity_recheck": "REQUIRED_BEFORE_MANUAL_ACTION",
            "identity_quality": identity_quality,
            "identity_reuse_note": "会话 ID 可能已复用；必须在人工操作前重新核对当前会话身份。",
        }

    for row in details.get("roots", []):
        if isinstance(row, dict):
            add(row, "ROOT_BLOCKER", 0, "已采样阻塞关系表明该会话为根阻塞候选。")
    for row in details.get("idle_transactions", []):
        if isinstance(row, dict):
            add(row, "IDLE_OPEN_TRANSACTION", 1, "会话与未结束事务证据关联。")
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
        add(row, "LONG_RUNNING_SQL", 3,
            "已达到配置的长运行阈值；仅作为人工核查线索。")
    ordered = sorted(selected.values(), key=lambda row: (
        int(row["selection_priority"]), -int(row["blocked_session_count"]),
        -int(row["transaction_age_seconds"] or 0) if type(row["transaction_age_seconds"]) is int else 0,
        str(row["session_key"])))[:5]
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return tuple(ordered)
