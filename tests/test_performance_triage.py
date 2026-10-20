from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport, Finding, Severity
from dba.incident import build_incident_summary, build_performance_triage, build_priority_candidates
from dba.presentation import render_emergency_triage, render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.slow import SlowReport

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def _report(db_type: DatabaseType, details: dict, findings: tuple[Finding, ...] = ()) -> DiagnosticReport:
    instance = DatabaseInstance("lab", "Lab", db_type, "192.0.2.1", 1, "monitor")
    return SlowReport(instance, NOW, "slow", findings, details)


def _samples(db_type: DatabaseType, *, truncated: bool = False) -> list[dict]:
    graph_source = {DatabaseType.SQLSERVER: "active_requests", DatabaseType.ORACLE: "active_sessions"}.get(
        db_type, "blocking")
    names = {graph_source, "transactions", "long_running"}
    return [{"collector": name, "success": True, "truncated": truncated if name == graph_source else False,
             "started_at": NOW.isoformat(), "finished_at": NOW.isoformat()} for name in names]


def _chain_report(*, truncated: bool = False) -> DiagnosticReport:
    findings = (Finding("LONG_TRANSACTION", Severity.WARNING,
                        {"session_id": 10, "transaction_id": "tx-a", "transaction_age_seconds": 300},
                        "Review transaction age."),)
    details = {
        "version": "16.0",
        "samples": _samples(DatabaseType.SQLSERVER, truncated=truncated),
        "blocking_graph": {"roots": [10], "children": {10: [20], 20: [30]}, "anomalies": []},
        "roots": [{"session_id": 10, "blocked_count": 2, "transaction_age_seconds": 300,
                   "transaction_id": "tx-a", "elapsed_ms": 17000}],
        "all_sessions": [{"session_id": sid} for sid in (10, 20, 30, 40)],
        "all_requests": [
            {"session_id": 20, "request_id": 1, "blocking_session_id": 10, "elapsed_ms": 90000},
            {"session_id": 30, "request_id": 1, "blocking_session_id": 20, "elapsed_ms": 80000},
            {"session_id": 40, "request_id": 1, "elapsed_ms": 14000},
        ],
        "long_running": [
            {"session_id": 10, "elapsed_ms": 17000},
            {"session_id": 20, "blocking_session_id": 10, "elapsed_ms": 90000},
            {"session_id": 40, "elapsed_ms": 14000},
        ],
        "permission_readiness": {"status": "READY"},
    }
    return _report(DatabaseType.SQLSERVER, details, findings)


def test_three_level_chain_preserves_victim_intermediate_root_and_independent_labels() -> None:
    report = _chain_report()
    triage = build_performance_triage(report)
    by_sid = {item["native_identity"]["session_id"]: item for item in triage["assessments"]}

    assert by_sid[10]["classifications"] == ["ROOT_BLOCKER", "LONG_RUNNING_TRANSACTION"]
    assert by_sid[10]["priority_basis"]["transaction_age_seconds"] == 300
    assert by_sid[10]["priority_basis"]["runtime_seconds"] == 17.0
    assert "INDEPENDENT_SLOW_SQL" not in by_sid[10]["classifications"]
    assert by_sid[10]["classification_evidence"]["ROOT_BLOCKER"]["rule_id"] == "TRIAGE-ROOT-01"
    assert by_sid[10]["classification_evidence"]["LONG_RUNNING_TRANSACTION"]["evidence_references"]
    assert by_sid[20]["classifications"] == ["BLOCKING_VICTIM", "INTERMEDIATE_BLOCKER"]
    assert by_sid[20]["classification_evidence"]["INTERMEDIATE_BLOCKER"]["rule_id"] == "TRIAGE-INTERMEDIATE-01"
    assert "LONG_RUNNING_SQL" in by_sid[20]["observed_signals"]
    assert by_sid[30]["classifications"] == ["BLOCKING_VICTIM"]
    assert by_sid[40]["classifications"] == ["INDEPENDENT_SLOW_SQL"]
    assert all(item["root_cause_status"] == "NOT_ESTABLISHED" for item in triage["assessments"])
    assert all(item["evidence_references"] for item in triage["assessments"])
    assert all(ref["collector"] and ref["source_path"] and ref["integrity"]
               for item in triage["assessments"] for ref in item["evidence_references"])


def test_blocked_slow_request_is_not_reclassified_as_independent_slow_sql() -> None:
    triage = build_performance_triage(_chain_report())
    intermediate = next(item for item in triage["assessments"]
                        if item["native_identity"].get("session_id") == 20)
    assert "INDEPENDENT_SLOW_SQL" not in intermediate["classifications"]
    assert "BLOCKING_VICTIM" in intermediate["classifications"]


def test_truncated_block_graph_withholds_root_and_independence_conclusions() -> None:
    triage = build_performance_triage(_chain_report(truncated=True))
    assert triage["status"] == "PARTIAL"
    assert triage["root_cause_status"] == "NOT_ESTABLISHED"
    assert any("truncated" in reason for reason in triage["unknown_reasons"])
    by_sid = {item["native_identity"]["session_id"]: item for item in triage["assessments"]}
    assert "ROOT_BLOCKER" not in by_sid[10]["classifications"]
    assert "LONG_RUNNING_TRANSACTION" in by_sid[10]["classifications"]
    assert "INDEPENDENT_SLOW_SQL" not in by_sid[40]["classifications"]
    assert by_sid[40]["classification_status"] == "UNKNOWN"


@pytest.mark.parametrize("db_type,source,identity", [
    (DatabaseType.SQLSERVER, "active_requests", {"session_id": 52, "request_id": 7}),
    (DatabaseType.POSTGRESQL, "blocking", {"pid": 52, "backend_start": "2026-10-20T09:00:00Z"}),
    (DatabaseType.MYSQL, "blocking", {"processlist_id": 52, "thread_id": 900, "trx_id": "trx-1"}),
    (DatabaseType.ORACLE, "active_sessions", {"inst_id": 2, "sid": "52", "serial_number": 8,
                                                "sql_id": "abc123", "transaction_id": "xid-1"}),
])
def test_native_database_identity_is_not_collapsed(db_type: DatabaseType, source: str,
                                                   identity: dict) -> None:
    row = {**identity, "elapsed_ms": 30000}
    details = {
        "version": "test",
        "samples": [{"collector": source, "success": True, "truncated": False,
                     "finished_at": NOW.isoformat()},
                    {"collector": "long_running", "success": True, "truncated": False,
                     "finished_at": NOW.isoformat()}],
        "blocking_graph": {"roots": [], "children": {}, "anomalies": []},
        "all_requests": [row], "long_running": [row],
        "permission_readiness": {"status": "READY"},
    }
    assessment = build_performance_triage(_report(db_type, details))["assessments"][0]
    native = assessment["native_identity"]
    for name, value in identity.items():
        assert native[name] == value
    if db_type == DatabaseType.MYSQL:
        assert native["processlist_id"] != native["thread_id"]
        assert native["trx_id"] == "trx-1"
    if db_type == DatabaseType.ORACLE:
        assert native["inst_id"] == 2 and native["serial_number"] == 8


def test_missing_permission_and_empty_snapshot_are_unknown_not_healthy() -> None:
    report = _report(DatabaseType.POSTGRESQL, {
        "samples": [{"collector": "blocking", "success": False, "truncated": False}],
        "permission_readiness": {"status": "MISSING", "missing_optional": ["blocking"]},
    })
    triage = build_performance_triage(report)
    assert triage["status"] == "UNKNOWN"
    assert triage["assessments"] == []
    assert any("not a health assertion" in reason for reason in triage["unknown_reasons"])
    assert any("Permission readiness is MISSING" in reason for reason in triage["unknown_reasons"])


def test_identity_ambiguity_and_graph_anomaly_degrade_classification() -> None:
    report = _chain_report()
    report.details["blocking_graph"]["anomalies"] = ["identity_conflict"]
    triage = build_performance_triage(report)
    assert triage["status"] == "PARTIAL"
    assert any("identity anomalies" in reason for reason in triage["unknown_reasons"])
    assert all("ROOT_BLOCKER" not in item["classifications"] for item in triage["assessments"])
    assert any("LONG_RUNNING_TRANSACTION" in item["classifications"] for item in triage["assessments"])


def test_oracle_serial_number_conflict_suppresses_sid_only_root_attribution() -> None:
    report = _report(DatabaseType.ORACLE, {
        "samples": [{"collector": "active_sessions", "success": True, "truncated": False,
                     "finished_at": NOW.isoformat()}],
        "blocking_graph": {"roots": ["1:52"], "children": {"1:52": ["1:53"]},
                           "anomalies": []},
        "roots": [{"inst_id": 1, "sid": 52, "serial_number": 8, "blocked_count": 1,
                   "transaction_age_seconds": 200, "transaction_id": "xid-1"}],
        "all_sessions": [{"inst_id": 1, "sid": 52, "serial_number": 9}],
        "all_requests": [],
        "permission_readiness": {"status": "READY"},
    }, (Finding("LONG_TRANSACTION", Severity.WARNING,
                {"inst_id": 1, "sid": 52, "serial_number": 8,
                 "transaction_age_seconds": 200, "transaction_id": "xid-1"},
                "Review transaction."),))
    triage = build_performance_triage(report)
    root = next(item for item in triage["assessments"] if item["native_identity"].get("sid") == "52")
    assert "ROOT_BLOCKER" not in root["classifications"]
    assert root["classification_status"] == "UNKNOWN"
    assert any("SERIAL#" in reason for reason in root["uncertainty_reasons"])
    assert root["classification_evidence"]["ROOT_BLOCKER"]["status"] == "UNKNOWN"
    assert root["classification_evidence"]["ROOT_BLOCKER"]["evidence_references"]
    assert root["classification_evidence"]["LONG_RUNNING_TRANSACTION"]["status"] == "UNKNOWN"


def test_cli_and_html_share_labels_sources_and_uncertainty_without_sql_text() -> None:
    report = _chain_report()
    triage = build_performance_triage(report)
    report.details["performance_triage"] = triage
    report.details["priority_candidates"] = build_priority_candidates(
        report, include_blocking_victims=True)
    console = render_emergency_triage(report, build_priority_candidates(
        report, include_blocking_victims=True),
                                      capture_started_at=NOW.isoformat())
    json_result = json.loads(render_json({"report": asdict(report)}))
    serialized_triage = json_result["report"]["details"]["performance_triage"]
    html = HtmlReportRenderer().render(build_report_model(
        report, build_incident_summary(report, "reports/lab_slow.html")))
    assert serialized_triage["assessments"] == triage["assessments"]
    assert "依据 ROOT_BLOCKER" in console
    assert "ROOT_BLOCKER (TRIAGE-ROOT-01" in html
    for output in (console, html):
        assert "INTERMEDIATE_BLOCKER" in output
        assert "TRIAGE-INTERMEDIATE-01" in output or "TRIAGE-ROOT-01" in output
        assert "active_requests" in output or "blocking" in output
    assert "NOT_ESTABLISHED" in html
    assert "blocking" in html or "active_requests" in html
    assert triage["database_id"] == "lab"
    assert triage["server_version"] == "16.0"


def test_evidence_references_never_copy_captured_sql_or_credentials() -> None:
    report = _chain_report()
    report.details["all_requests"].append({"session_id": 50,
                                           "sql_text": "SELECT 'private-sensitive-text'",
                                           "password": "must-not-appear"})
    triage = build_performance_triage(report)
    serialized = repr(triage)
    assert "private-sensitive-text" not in serialized
    assert "must-not-appear" not in serialized
