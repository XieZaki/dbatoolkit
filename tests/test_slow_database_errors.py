from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from test_mysql import FakeDriver as MySQLDriver
from test_oracle import fixture as oracle_fixture
from test_postgresql import FakeDriver as PostgreSQLDriver
from test_sqlserver import ROOT, TEST_CREDENTIAL, FakeDriver, MemoryCredentialProvider

from dba import cli
from dba.errors import ToolkitError
from dba.logging import SafeFormatter
from dba.presentation import render_human
from dba.sql_timeout import SqlDeadlineExpired


def _command(tmp_path: Path, engine: str, *, json_mode: bool = True) -> list[str]:
    inventory = tmp_path / "inventory.yaml"
    families = {"sqlserver": "2022", "mysql": "8.0", "postgresql": "16", "oracle": "19c"}
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "fixture", "name": "Synthetic", "db_type": engine, "host": "test.invalid",
        "port": 10000, "credential_alias": "fixture-alias", "environment": "test",
        "version_family": families[engine],
        **({"service_name": "FREEPDB1"} if engine == "oracle" else {"database": "test"}),
    }]}), encoding="utf-8")
    return ["--inventory", str(inventory), "--sql-root", str(ROOT / "sql"),
            "diagnose", "fixture", "slow", "--report", str(tmp_path / "slow.html"),
            *(["--json"] if json_mode else [])]


@pytest.fixture
def audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "dba.jsonl"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(SafeFormatter())
    monkeypatch.setattr(logging.getLogger("dba"), "handlers", [handler])
    try:
        yield path
    finally:
        handler.close()


@pytest.mark.parametrize("case", [
    "sqlserver", "sqlserver_permission", "mysql", "mysql_permission", "postgresql",
    "postgresql_permission", "oracle", "oracle_permission", "sqlserver_timeout",
    "mysql_timeout", "mysql_server_timeout", "postgresql_timeout", "oracle_timeout",
])
def test_slow_preserves_dbapi_error_in_report_and_jsonl(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], audit: Path,
) -> None:
    timeout_case = case.endswith("_timeout")
    server_timeout = case == "mysql_server_timeout"
    base_case = "mysql" if server_timeout else case.removesuffix("_permission").removesuffix("_timeout")
    engine = "sqlserver" if base_case == "sqlserver" else base_case
    driver: Any
    secret = TEST_CREDENTIAL
    provider = MemoryCredentialProvider()
    failed_collector = "active_sessions"
    error_type = "QueryFailed"
    if engine == "sqlserver":
        driver = FakeDriver()
        driver_name = "ODBC Driver 18 for SQL Server"
        if timeout_case:
            code, state, message = None, None, "Client SQL deadline expired; database-side stop is unconfirmed."
            error_type = "ExecuteTimeout"
        elif case == "sqlserver":
            code, state, message = 207, "42S22", "Invalid column name 'client_interface_name'."
        else:
            code, state, message = 229, "42000", "The SELECT permission was denied."
            error_type = "QueryDenied"
        driver.connection.failures[failed_collector] = (
            SqlDeadlineExpired("Execute") if timeout_case else Exception(
                state, f"[{state}] [Microsoft][SQL Server]{message} {secret} ({code}) (SQLExecDirectW)")
        )
    elif engine == "mysql":
        driver = MySQLDriver()
        driver_name = "PyMySQL"
        if server_timeout:
            code, state, message = 3024, None, "Maximum statement execution time exceeded"
            error_type = "QueryTimeout"
        elif timeout_case:
            code, state, message = None, None, "Client SQL deadline expired; database-side stop is unconfirmed."
            error_type = "ExecuteTimeout"
        elif case == "mysql_permission":
            code, state, message = 1142, None, "SELECT command denied"
            error_type = "QueryDenied"
        else:
            code, state, message = 1054, None, "Unknown column 'test_column'"
            error_type = "CapabilityUnavailable"
        driver.connection.results["active_sessions"] = [{"processlist_id": 23, "user_name": "retained-user"}]
        failed_collector = "active_requests"
        driver.connection.failures[failed_collector] = (
            SqlDeadlineExpired("Execute") if timeout_case and not server_timeout
            else OSError(code, f"{message} {secret}")
        )
    elif engine == "postgresql":
        driver = PostgreSQLDriver()
        driver_name = "psycopg"
        if timeout_case:
            code, state, message = None, None, "Client SQL deadline expired; database-side stop is unconfirmed."
            error_type = "ExecuteTimeout"
        elif case == "postgresql_permission":
            code, state, message = None, "42501", "permission denied for view pg_stat_activity"
            error_type = "QueryDenied"
        else:
            code, state, message = None, "42703", "column test_column does not exist"
        driver.connection.results["active_sessions"] = [{"pid": 23, "usename": "retained-user", "state": "active"}]
        failed_collector = "transactions"
        class DatabaseFailure(Exception):
            pass
        if timeout_case:
            failure: Exception = SqlDeadlineExpired("Execute")
        else:
            failure = DatabaseFailure()
            failure.sqlstate = state  # type: ignore[attr-defined]
            failure.diag = SimpleNamespace(  # type: ignore[attr-defined]
                message_primary=f"{message} {secret}", message_detail="detail sentinel")
        driver.connection.failures[failed_collector] = failure
    else:
        _, _, _, driver = oracle_fixture()
        driver_name = "python-oracledb"
        if timeout_case:
            code, state, message = None, None, "Client SQL deadline expired; database-side stop is unconfirmed."
            error_type = "ExecuteTimeout"
        elif case == "oracle_permission":
            code, state, message = "ORA-01031", None, "insufficient privileges"
            error_type = "QueryDenied"
        else:
            code, state, message = "ORA-00904", None, "invalid identifier"
            error_type = "CapabilityUnavailable"
        driver.connection.data["active_sessions"][0]["username"] = "retained-user"
        failed_collector = "active_requests"
        oracle_number = 1031 if case == "oracle_permission" else 904
        driver.connection.fail[failed_collector] = (
            SqlDeadlineExpired("Execute") if timeout_case else Exception(
                SimpleNamespace(code=oracle_number, full_code=code, message=f"{code}: {message} {secret}"))
        )
        provider = MemoryCredentialProvider("MONITOR")

    assert cli.main(_command(tmp_path, engine), provider=provider, driver=driver) == 1
    output = capsys.readouterr().out
    document = json.loads(output)
    assert document["success"] is False and document["collector_name"] == failed_collector
    assert document["error_type"] == error_type and "PreflightRequired" not in output
    report = document["report"]
    assert report["instance"]["credential_alias"] == "[REDACTED]"
    assert report["details"]["incomplete"] is True
    assert document["incident_summary"]["data_completeness"] != "COMPLETE"
    sample = next(s for s in report["details"]["samples"] if s["collector"] == failed_collector)
    assert sample["success"] is False
    error = sample["database_error"]
    assert document["error"] == error
    expected_category = ("TIMEOUT" if timeout_case else
                         "PERMISSION_DENIED" if case.endswith("_permission") else "SQL_EXECUTION_ERROR")
    assert error["error_category"] == expected_category
    execution = error["timeout"]
    assert execution["execution_phase"] == "Execute"
    assert execution["script_timeout_seconds"] > 0
    assert execution["effective_timeout_seconds"] > 0
    assert execution["timeout_seconds"] > 0
    assert execution["script_path"] == f"{engine}/{failed_collector}.sql"
    expected_cancellation = ("CANCELLED_CONFIRMED" if server_timeout
                             else "CANCEL_STATUS_UNKNOWN" if timeout_case else "NOT_APPLICABLE")
    assert execution["cancellation_status"] == expected_cancellation
    assert execution["connection_status"] == "DISCARDED"
    human = render_human("check", {
        "success": False, "error_type": error_type, "error_message": document["error_message"],
        "error": error,
    })
    expected_human_category = "权限不足" if expected_category == "PERMISSION_DENIED" else expected_category
    assert expected_human_category in human
    assert execution["script_path"] in human
    expected_human_cancellation = (
        "不适用" if execution["cancellation_status"] == "NOT_APPLICABLE"
        else "max_execution_time" if server_timeout
        else "client_wait_stopped_database_stop_unconfirmed" if timeout_case
        else execution["cancellation_status"]
    )
    assert expected_human_cancellation in human
    assert {key: error[key] for key in ("error_type", "database_type", "driver", "sqlstate", "vendor_code")} == {
        "error_type": error_type, "database_type": engine, "driver": driver_name,
        "sqlstate": state, "vendor_code": code,
    }
    assert message in error["message"]
    if engine != "sqlserver":
        assert "retained-user" in json.dumps(report["details"]["sessions"])
    if engine == "postgresql" and not timeout_case:
        assert error["detail"] == "detail sentinel"
    html = Path(document["report_path"]).read_text(encoding="utf-8")
    assert "失败" in html and driver_name in html and expected_category in html
    assert execution["script_path"] in html
    assert message.replace("'", "&#x27;") in html
    audit_text = audit.read_text(encoding="utf-8")
    event = next(entry for entry in map(json.loads, audit_text.splitlines())
                 if entry["sql_script"] == failed_collector and entry["result"] == "failed")
    assert event["error_type"] == error_type and event["database_type"] == engine
    assert event["driver"] == driver_name and event["sqlstate"] == state and event["vendor_code"] == code
    assert event["error_category"] == expected_category
    assert event["execution_phase"] == "Execute" and event["script_path"] == execution["script_path"]
    assert event["cancellation_status"] == expected_cancellation
    assert message in event["sanitized_message"]
    assert secret not in output + human + html + audit_text
    assert "DBA-Other-Test-Secret" not in output + html + audit_text
    assert driver.connection.closed and all(cursor.closed for cursor in driver.connection.cursors)


@pytest.mark.parametrize("failure_stage", [None, "render", "assemble"])
def test_sqlserver_late_error_keeps_prior_session_evidence(
    failure_stage: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, audit: Path,
) -> None:
    driver = FakeDriver()
    driver.connection.failures["active_requests"] = Exception("42S22", "Bad column (207)")
    if failure_stage:
        def fail_model(*args: Any, **kwargs: Any) -> None:
            raise ToolkitError("ReportRenderFailed", "Synthetic rendering failure.")
        monkeypatch.setattr(cli, "build_report_model" if failure_stage == "render"
                            else "build_sqlserver_slow_phase_report", fail_model)
    command = _command(tmp_path, "sqlserver", json_mode=failure_stage is not None)
    assert cli.main(command, provider=MemoryCredentialProvider(), driver=driver) == 1
    output = capsys.readouterr().out
    if failure_stage:
        document = json.loads(output)
        assert document["error_type"] == "QueryFailed" and document["error"]["vendor_code"] == 207
        assert document["report_error"]["error_type"] == "ReportRenderFailed"
        if failure_stage == "render":
            assert document["report"]["instance"]["credential_alias"] == "[REDACTED]"
            assert document["report"]["details"]["all_sessions"][0]["session_id"] == 51
        else:
            assert document["samples"][0]["rows"][0]["session_id"] == 51
        event = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
        assert event["module"] == "cli" and event["vendor_code"] == 207 and event["sqlstate"] == "42S22"
    else:
        assert "Bad column" in output and "42S22" in output and "207" in output
        assert "active_requests" in output or "活动请求" in output
        assert "PreflightRequired" not in output
        assert (tmp_path / "slow.html").is_file()


def test_sqlserver_2022_session_fields_stay_with_the_right_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], audit: Path,
) -> None:
    driver = FakeDriver()
    driver.connection.results["server_info"][0]["product_version"] = "16.0.4305.5"
    driver.connection.results["version_detect"][0]["raw_version"] = "16.0.4305.5"
    sessions = [
        {"session_id": sid, "login_name": f"login-{sid}", "original_login_name": f"original-{sid}",
         "nt_user_name": f"windows-{sid}", "host_name": f"host-{sid}", "program_name": f"program-{sid}",
         "client_interface_name": f"interface-{sid}", "host_process_id": sid * 10,
         "client_net_address": f"192.0.2.{sid}", "database_name": f"database-{sid}"}
        for sid in (51, 52)
    ]
    driver.connection.results["active_sessions"] = sessions
    driver.connection.results["transactions"] = []
    driver.connection.results["connections"] = []
    driver.connection.results["active_requests"] = [
        {"session_id": sid, "request_id": 0, "elapsed_ms": 100, "database_name": f"database-{sid}",
         "statement_sql_text": f"SELECT {sid}"}
        for sid in (52, 51)
    ]
    assert cli.main(_command(tmp_path, "sqlserver"), provider=MemoryCredentialProvider(), driver=driver) == 0
    document = json.loads(capsys.readouterr().out)
    details = document["report"]["details"]
    assert details["version"] == "16.0.4305.5"
    assert "active_sessions" in driver.connection.executed
    for session in sessions:
        sid = session["session_id"]
        row = next(row for row in details["all_sessions"] if row["session_id"] == sid)
        request = next(row for row in details["all_requests"] if row["session_id"] == sid)
        assert {key: row[key] for key in session} == session
        assert {key: request[key] for key in session} == session
        assert request["statement_sql_text"] == f"SELECT {sid}"
    html = Path(document["report_path"]).read_text(encoding="utf-8")
    for session in sessions:
        for key in ("login_name", "original_login_name", "nt_user_name", "host_name", "program_name",
                    "client_interface_name", "client_net_address", "database_name"):
            assert session[key] in html
