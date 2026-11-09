from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from dba.database_errors import (
    normalize_mysql_error,
    normalize_oracle_error,
    normalize_postgresql_error,
    normalize_sqlserver_error,
    sanitize_vendor_text,
)
from dba.logging import SafeFormatter
from dba.presentation import render_human, render_json

SECRET = "DBA-Test-Secret-987654"


def test_mysql_structured_errors_retain_code_message_and_classification() -> None:
    detail = normalize_mysql_error(Exception(1049, "Unknown database 'missing_db'"))
    assert detail.vendor_code == 1049
    assert detail.message == "Unknown database 'missing_db'"
    assert detail.classification == "DATABASE_NOT_FOUND"
    assert "configured database" in (detail.guidance or "")


def test_mysql_known_codes_and_unknown_are_not_guessed() -> None:
    cases = {
        1045: "AUTHENTICATION_FAILED", 1049: "DATABASE_NOT_FOUND",
        2003: "CONNECTION_FAILED", 2005: "DNS_RESOLUTION_FAILED",
        2006: "SERVER_GONE_AWAY", 2013: "CONNECTION_LOST", 7777: "UNKNOWN",
    }
    for code, classification in cases.items():
        assert normalize_mysql_error(Exception(code, "vendor evidence")).classification == classification


@pytest.mark.regression
def test_vendor_error_secret_redaction_preserves_nonsecret_evidence() -> None:
    detail = normalize_mysql_error(
        Exception(1045, f"Access denied for user 'monitor' (PWD={SECRET})"), (SECRET,)
    )
    assert detail.vendor_code == 1045
    assert "Access denied for user 'monitor'" in detail.message
    assert SECRET not in detail.message
    assert "[REDACTED]" in detail.message


def test_mysql_using_password_yes_is_not_treated_as_a_secret_value() -> None:
    detail = normalize_mysql_error(Exception(1045, "Access denied (using password: YES)"))
    assert "using password: YES" in detail.message


def test_sqlserver_error_extracts_sqlstate_and_native_code() -> None:
    detail = normalize_sqlserver_error(
        Exception("28000", "[ODBC Driver 17][SQL Server] Login failed (18456)")
    )
    assert detail.driver == "pyodbc"
    assert detail.sqlstate == "28000"
    assert detail.vendor_code == 18456
    assert "Login failed" in detail.message
    assert detail.classification == "AUTHENTICATION_FAILED"


def test_sqlserver_combined_odbc_diagnostic_keeps_native_207() -> None:
    detail = normalize_sqlserver_error(
        Exception("[42S22] [Microsoft][SQL Server]Invalid column name 'client_interface_name'. (207) "
                  "(SQLExecDirectW)"), driver="ODBC Driver 17 for SQL Server")
    assert (detail.sqlstate, detail.vendor_code) == ("42S22", 207)
    assert detail.driver == "ODBC Driver 17 for SQL Server"
    assert "Invalid column name 'client_interface_name'." in detail.message
    assert normalize_sqlserver_error(Exception("an error on object (123)")).vendor_code is None


def test_vendor_error_sanitizes_quoted_and_escaped_connection_credentials() -> None:
    message = (
        """connection failed; UID={SYNTHETIC_USER}; PWD={SYNTHETIC_A}};SYNTHETIC_B}; """
        """passwd='SYNTHETIC_C SYNTHETIC_D'; "token": "SYNTHETIC_E SYNTHETIC_F"; invalid column"""
    )
    safe = sanitize_vendor_text(message)
    assert "invalid column" in safe
    assert "SYNTHETIC_" not in safe


def test_postgresql_error_uses_diag_fields() -> None:
    diag = SimpleNamespace(message_primary="authentication failed", message_detail="role monitor",
                           message_hint="check pg_hba", context=None)
    exc = Exception("driver generated output")
    exc.sqlstate = "28P01"  # type: ignore[attr-defined]
    exc.diag = diag  # type: ignore[attr-defined]
    detail = normalize_postgresql_error(exc)
    assert detail.sqlstate == "28P01"
    assert detail.message == "authentication failed"
    assert detail.detail == "role monitor"
    assert detail.hint == "check pg_hba"
    assert detail.classification == "AUTHENTICATION_FAILED"


def test_postgresql_diag_sqlstate_and_oracle_driver_code_remain_distinct() -> None:
    exc = Exception("error")
    exc.diag = SimpleNamespace(sqlstate="42703")  # type: ignore[attr-defined]
    pg_error = normalize_postgresql_error(exc)
    assert pg_error.sqlstate == "42703" and pg_error.vendor_code is None

    oracle_error = normalize_oracle_error(Exception(
        SimpleNamespace(code=0, full_code="DPY-4011", message="DPY-4011: connection closed")))
    assert oracle_error.vendor_code == "DPY-4011" and oracle_error.sqlstate is None
    assert normalize_oracle_error(Exception(SimpleNamespace(code=0, message="unknown"))).vendor_code is None


def test_oracle_error_extracts_ora_code_and_message() -> None:
    error_obj = SimpleNamespace(code=1017, message="ORA-01017: invalid username/password; logon denied")
    detail = normalize_oracle_error(Exception(error_obj))
    assert detail.vendor_code == "ORA-01017"
    assert detail.classification == "AUTHENTICATION_FAILED"
    assert "invalid username/password" in detail.message


def test_oracle_missing_create_session_keeps_vendor_error_and_points_to_guide() -> None:
    error_obj = SimpleNamespace(
        code=1045,
        message="ORA-01045: user lacks CREATE SESSION privilege; logon denied",
    )
    detail = normalize_oracle_error(Exception(error_obj))
    assert detail.vendor_code == "ORA-01045"
    assert detail.classification == "ACCESS_DENIED"
    assert "CREATE SESSION" in detail.message
    assert "docs/DATABASE_PERMISSIONS.md" in (detail.guidance or "")


@pytest.mark.integration
@pytest.mark.regression
def test_human_and_json_output_preserve_vendor_message_and_redact_secret() -> None:
    data = {
        "success": False, "error_type": "ConnectionFailed", "database_id": "mysql84",
        "target": "127.0.0.1:3306", "driver": "PyMySQL",
        "error_message": "Connection failed; vendor error details are available below.",
        "error": normalize_mysql_error(Exception(1049, f"Unknown database 'missing' {SECRET}"),
                                       (SECRET,)).to_dict(),
        "effective_connection": {"host": "127.0.0.1", "port": 3306,
                                 "database": "missing", "ssl": "ENABLED",
                                 "connect_timeout": 5},
    }
    human = render_human("check", data)
    payload = render_json(data)
    assert "Unknown database 'missing'" in human and "DATABASE_NOT_FOUND" in human
    assert "Unknown database 'missing'" in payload and "DATABASE_NOT_FOUND" in payload
    assert SECRET not in human + payload
    assert "数据库" in human and "missing" in human


def test_safe_formatter_logs_sanitized_database_error_fields() -> None:
    record = logging.LogRecord("dba", logging.ERROR, __file__, 1, "event", (), None)
    record.event = {
        "module": "cli", "result": "failed", "database_type": "mysql", "driver": "PyMySQL",
        "vendor_code": 1045, "sqlstate": None, "classification": "AUTHENTICATION_FAILED",
        "sanitized_message": f"Access denied; password={SECRET}",
    }
    output = SafeFormatter().format(record)
    parsed = json.loads(output)
    assert parsed["vendor_code"] == 1045
    assert parsed["classification"] == "AUTHENTICATION_FAILED"
    assert "[REDACTED]" in parsed["sanitized_message"]
    assert SECRET not in output
