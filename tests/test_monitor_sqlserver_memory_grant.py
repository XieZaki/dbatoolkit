from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from dba.core import DatabaseInstance, DatabaseType
from scripts.acceptance import monitor_sqlserver_memory_grant as helper


def _instance() -> DatabaseInstance:
    return DatabaseInstance("sqlserver2022", "lab", DatabaseType.SQLSERVER,
                            "127.0.0.1", 1433, "monitor-alias", database="master",
                            environment="test", enabled=True)


def _row() -> dict[str, Any]:
    return {
        "session_id": 67, "request_id": 0, "grant_time": "2026-10-28T01:00:01",
        "requested_memory_kb": 2048, "required_memory_kb": 1024,
        "granted_memory_kb": 2048, "used_memory_kb": 0, "max_used_memory_kb": None,
        "sql_handle": "0x01", "request_sql_handle": "0x01",
        "plan_handle": None, "request_plan_handle": None,
        "request_identity": {"request_context_status": "MATCHED_AT_SAMPLE",
                             "request_start_time": "2026-10-28T01:00:01",
                             "request_status": "running", "wait_type": None,
                             "database_id": 5},
    }


def _data(row: dict[str, Any] | None) -> dict[str, Any]:
    return {"granted_sample": {"success": True, "rows": [] if row is None else [row]}}


def test_monitor_target_rejects_nonlab_and_invalid_inventory() -> None:
    for changed in ({"environment": "production"}, {"host": "192.0.2.1"},
                    {"port": 1434}, {"enabled": False}, {"database": "other"}):
        with pytest.raises(helper.Blocked, match="INVENTORY_TARGET_MISMATCH"):
            helper._target(replace(_instance(), **changed))


def test_sa_session_requires_unique_visible_identity_and_stable_login_time() -> None:
    baseline = {"session_id": 67, "login_name": "sa", "database_name": "AdventureWorks2022",
                "login_time": "2026-10-28T01:00:00"}
    payload = {"success": True, "truncated": False, "rows": [baseline]}
    assert helper._session(payload, 67) == baseline
    assert helper._stable_session(baseline, dict(baseline))
    assert not helper._stable_session(baseline, {**baseline, "login_time": "2026-10-28T01:02:00"})
    with pytest.raises(helper.Blocked, match="SESSION_VISIBILITY_INCOMPLETE"):
        helper._session({**payload, "truncated": True}, 67)
    with pytest.raises(helper.Blocked, match="WORKLOAD_SESSION_IDENTITY_UNVERIFIED"):
        helper._session({**payload, "rows": [{**baseline, "login_name": "other"}]}, 67)


def test_new_request_is_checked_against_previous_session_snapshot() -> None:
    before = {"last_request_start_time": "2026-10-28T01:00:00"}
    assert helper._request_newer_than_baseline(_row(), before)
    assert not helper._request_newer_than_baseline(_row(), {})
    assert not helper._request_newer_than_baseline(_row(), {"last_request_start_time": "bad"})
    assert not helper._request_newer_than_baseline(
        _row(), {"last_request_start_time": "2026-10-28T01:00:02"})


def test_candidate_requires_grant_and_does_not_infer_missing_handles() -> None:
    row = _row()
    assert helper._candidate(_data(None), 67, 5) is None
    assert helper._candidate(_data(row), 67, 5) == row
    assert helper._handle_status(row["plan_handle"], row["request_plan_handle"]) == "MISSING"
    assert helper._handle_status(row["sql_handle"], row["request_sql_handle"]) == "MATCH"
    assert helper._handle_status("0x0000", "0x0000") == "MISSING"
    assert helper._handle_status("not-a-handle", "not-a-handle") == "INVALID"
    with pytest.raises(helper.Blocked, match="HANDLE_CONFLICT_OR_INVALID"):
        helper._candidate(_data({**row, "request_sql_handle": "0x02"}), 67, 5)
    with pytest.raises(helper.Blocked, match="GRANTED_ROW_INCOMPLETE"):
        helper._candidate(_data({**row, "grant_time": None}), 67, 5)
    with pytest.raises(helper.Blocked, match="REQUEST_ASSOCIATION_UNVERIFIED"):
        helper._candidate(_data({**row, "request_identity": {}}), 67, 5)
    with pytest.raises(helper.Blocked, match="REQUEST_DATABASE_UNVERIFIED"):
        helper._candidate(_data(row), 67, 6)
    with pytest.raises(helper.Blocked, match="MULTIPLE_REQUESTS_AMBIGUOUS"):
        helper._candidate({"granted_sample": {"success": True, "rows": [row, row]}}, 67, 5)


def test_reviewed_handles_are_optional_but_strictly_validated() -> None:
    assert helper._optional_handle("") is None
    assert helper._optional_handle("0xAb10") == "0xab10"
    for invalid in ("0x", "0x1", "0x0000", "0x01;DROP", "0x" + "ab" * 65):
        with pytest.raises(helper.Blocked, match="INVALID_REVIEWED_HANDLE"):
            helper._optional_handle(invalid)


def test_html_parity_checks_every_shared_field_not_json_only_fields() -> None:
    row = _row()
    cells = helper._shared_projection(row)
    html = '<section id="memory-grant-granted"><table><tbody><tr>' + "".join(
        f"<td>{cell}</td>" for cell in cells) + "</tr></tbody></table></section>"
    assert helper._html_matches(html, [row])
    assert not helper._html_matches(html, [{**row, "granted_memory_kb": 999}])
    assert not helper._html_matches("<html></html>", [])


def test_html_parity_with_real_report_renderer() -> None:
    from dba.incident import build_incident_summary
    from dba.memory_grant import MemoryGrantDiagnosticReport
    from dba.reporting import HtmlReportRenderer, build_report_model

    row = _row()
    waiting = {"success": True, "rows": [], "truncated": False, "row_count": 0}
    granted = {"success": True, "rows": [row], "truncated": False, "row_count": 1}
    report = MemoryGrantDiagnosticReport(
        _instance(), datetime.now(timezone.utc), "memory-grant", (),
        {"memory_grant": {"waiting_sample": waiting, "granted_sample": granted,
                          "completeness": "COMPLETE",
                          "health_assessment": "NOT_ASSESSED"},
         "samples": [], "incomplete": False},
    )
    html = HtmlReportRenderer().render(build_report_model(report, build_incident_summary(report)))
    assert helper._html_matches(html, [row])


@pytest.mark.parametrize(("has_row", "reviewed_sql", "conflict"),
                         [(True, "0x01", False), (False, "0x01", False),
                          (True, "0x02", True)])
def test_bounded_monitor_uses_existing_cli_and_keeps_results_reviewable(
    has_row: bool, reviewed_sql: str, conflict: bool,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dba.credentials import Credential

    row = _row()
    baseline = {"session_id": 67, "login_name": "sa", "database_name": "AdventureWorks2022",
                "login_time": "2026-10-28T00:59:00", "last_request_start_time": "2026-10-28T01:00:00",
                "open_transaction_count": 0}
    calls: list[list[str]] = []

    def fake_cli(arguments: list[str], timeout: float, secret: str) -> dict[str, Any]:
        calls.append(arguments)
        assert secret == "test-only-monitor-secret"
        assert timeout > 0
        if arguments[0] == "test-connection":
            return {"success": True, "version": helper.EXPECTED_VERSION}
        if arguments[0] == "collect":
            return {"success": True, "truncated": False, "rows": [baseline]}
        assert arguments[:3] == ["diagnose", "sqlserver2022", "memory-grant"]
        report_path = Path(arguments[arguments.index("--report") + 1])
        selected = [row] if has_row else []
        table_rows = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in helper._shared_projection(item)) + "</tr>"
            for item in selected
        )
        report_path.write_text(
            '<section id="memory-grant-granted"><table>' + table_rows + "</table></section>",
            encoding="utf-8",
        )
        return {"success": True, "report": {"instance": {"credential_alias": "[REDACTED]"}},
                "memory_grant": {
                    "database_version": helper.EXPECTED_VERSION, "completeness": "COMPLETE",
                    "collection_status": "COLLECTED",
                    "granted_sample": {"success": True, "rows": selected, "truncated": False,
                                       "started_at": "2026-10-28T01:00:02Z",
                                       "finished_at": "2026-10-28T01:00:03Z"}}}

    monkeypatch.setattr(helper, "ROOT", tmp_path)
    monkeypatch.setattr(helper, "_run_cli", fake_cli)
    monkeypatch.setattr(helper.time, "sleep", lambda _: None)
    if conflict:
        with pytest.raises(helper.Blocked, match="REVIEWED_PLAN_IDENTITY_CONFLICT"):
            helper.run(67, 5, _instance(), Credential("dba_monitor", "test-only-monitor-secret"),
                       reviewed_sql, "0x02")
        assert len([call for call in calls if call[0] == "diagnose"]) == 1
        return
    result = helper.run(67, 5, _instance(), Credential("dba_monitor", "test-only-monitor-secret"),
                        reviewed_sql, "0x02")
    attempts = [call for call in calls if call[0] == "diagnose"]
    assert len(attempts) == (1 if has_row else 3)
    assert len(result["artifacts"]) == len(attempts)
    assert all(Path(item["json"]).exists() and Path(item["html"]).exists()
               for item in result["artifacts"])
    if has_row:
        assert result["result"] == "GRANTED_OBSERVED_REVIEW_REQUIRED"
        assert result["e2e"] == "NOT_VERIFIED"
        assert result["sql_handle_comparison"] == "MATCH"
        assert result["plan_handle_comparison"] == "MISSING"
        assert result["reviewed_sql_handle_comparison"] == "MATCH"
        assert result["reviewed_plan_handle_comparison"] == "MISSING"
        assert result["plan_and_actual_access_range"] == "NOT_VERIFIED"
    else:
        assert result["result"] == "BLOCKED"
        assert result["reason"] == "NO_MATCHING_GRANTED_SAMPLE"


def test_cli_subprocess_uses_no_actor_credential_and_hides_sensitive_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> Any:
        assert "--password" not in args and "sa" not in args
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(args, 1, "secret-value", "")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    with pytest.raises(helper.Blocked, match="CREDENTIAL_DISCLOSURE_DETECTED"):
        helper._run_cli(["test-connection", "sqlserver2022"], 1, "secret-value")


def test_only_existing_monitor_alias_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba.credentials import Credential

    class Store:
        def available(self) -> bool:
            return True

        def get(self, alias: str) -> Credential:
            assert alias == "monitor-alias"
            return Credential("dba_monitor", "test-only-monitor-secret")

    monkeypatch.setattr(helper, "KeyringCredentialStore", Store)
    credential = helper._monitor_credential("monitor-alias")
    assert credential.username == "dba_monitor"


def test_monitor_rejects_password_arguments_before_any_lookup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_gate() -> None:
        raise AssertionError("Runtime gate should not be reached")

    monkeypatch.setattr(helper, "_runtime_gate", forbidden_gate)
    assert helper.main(["--password", "test-only-secret"]) == 2
    output = capsys.readouterr().out
    assert "COMMAND_ARGUMENTS_NOT_ACCEPTED" in output
    assert "test-only-secret" not in output
