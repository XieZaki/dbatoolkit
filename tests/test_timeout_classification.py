from __future__ import annotations

from types import SimpleNamespace

import pytest

from dba.database_errors import (
    DatabaseErrorDetail,
    normalize_mysql_error,
    normalize_oracle_error,
    normalize_postgresql_error,
    normalize_sqlserver_error,
    with_connection_status,
    with_timeout_evidence,
)
from dba.errors import ToolkitError

SECRET = "dba-test-secret-671234"


def _error(engine: str, code: str | int, message: str) -> Exception:
    if engine == "sqlserver":
        state = str(code)
        return Exception(state, f"[{state}] [Microsoft][SQL Server] {message}")
    if engine == "postgresql":
        exc = Exception(message)
        exc.sqlstate = str(code)  # type: ignore[attr-defined]
        exc.diag = SimpleNamespace(message_primary=message)  # type: ignore[attr-defined]
        return exc
    if engine == "mysql":
        return Exception(int(code), message)
    number = int(code) if isinstance(code, int) else 0
    return Exception(SimpleNamespace(code=number, full_code=str(code), message=message))


def _normalize(engine: str, exc: Exception) -> DatabaseErrorDetail:
    return {
        "sqlserver": normalize_sqlserver_error,
        "postgresql": normalize_postgresql_error,
        "mysql": normalize_mysql_error,
        "oracle": normalize_oracle_error,
    }[engine](exc, (SECRET,))


@pytest.mark.parametrize(
    ("engine", "code", "message", "category", "cancel_status", "mechanism"),
    [
        ("sqlserver", "HYT00", "Query timeout expired", "TIMEOUT", "DRIVER_REPORTED",
         "ODBC_QUERY_TIMEOUT_ATTENTION"),
        ("sqlserver", "HY008", "Operation canceled", "CANCEL_STATUS_UNKNOWN",
         "CANCEL_STATUS_UNKNOWN", "ODBC_OPERATION_CANCELLED"),
        ("sqlserver", "08S01", "Communication link failure", "CONNECTION_LOST",
         "CANCEL_STATUS_UNKNOWN", "ODBC_CONNECTION_LOST"),
        ("sqlserver", "42S22", "Invalid column name (207)", "SQL_EXECUTION_ERROR",
         "NOT_APPLICABLE", None),
        ("sqlserver", "42000", "SELECT permission denied (229)", "PERMISSION_DENIED",
         "NOT_APPLICABLE", None),
        ("postgresql", "57014", "canceling statement due to statement timeout", "TIMEOUT",
         "CANCELLED_CONFIRMED", "POSTGRESQL_STATEMENT_TIMEOUT"),
        ("postgresql", "57014", "canceling statement due to user request", "CANCELLED_CONFIRMED",
         "CANCELLED_CONFIRMED", "POSTGRESQL_QUERY_CANCEL"),
        ("postgresql", "57014", "query canceled by recovery conflict", "CANCELLED_CONFIRMED",
         "CANCELLED_CONFIRMED", "POSTGRESQL_QUERY_CANCEL"),
        ("postgresql", "08006", "connection to server was lost", "CONNECTION_LOST",
         "CANCEL_STATUS_UNKNOWN", "POSTGRESQL_CONNECTION_LOST"),
        ("postgresql", "42601", "syntax error at or near SELECT", "SQL_EXECUTION_ERROR",
         "NOT_APPLICABLE", None),
        ("postgresql", "42501", "permission denied for view", "PERMISSION_DENIED",
         "NOT_APPLICABLE", None),
        ("mysql", 3024, "Query execution interrupted; maximum statement time exceeded", "TIMEOUT",
         "CANCELLED_CONFIRMED", "MYSQL_MAX_EXECUTION_TIME"),
        ("mysql", 1317, "Query execution was interrupted", "CANCELLED_CONFIRMED",
         "CANCELLED_CONFIRMED", "MYSQL_QUERY_INTERRUPTED"),
        ("mysql", 2013, "Lost connection to MySQL server during query", "CONNECTION_LOST",
         "CANCEL_STATUS_UNKNOWN", "MYSQL_SOCKET_READ_CONNECTION_LOST"),
        ("mysql", 1064, "You have an error in your SQL syntax", "SQL_EXECUTION_ERROR",
         "NOT_APPLICABLE", None),
        ("mysql", 1142, "SELECT command denied", "PERMISSION_DENIED", "NOT_APPLICABLE", None),
        ("oracle", "DPY-4024", "DPY-4024: call timeout exceeded", "TIMEOUT",
         "DRIVER_REPORTED", "ORACLE_CALL_TIMEOUT"),
        ("oracle", "DPY-4011", "DPY-4011: the database or network closed the connection",
         "CONNECTION_LOST", "CANCEL_STATUS_UNKNOWN", "ORACLE_CONNECTION_LOST"),
        ("oracle", "ORA-01013", "ORA-01013: user requested cancel", "CANCELLED_CONFIRMED",
         "CANCELLED_CONFIRMED", "ORACLE_CANCEL_RESPONSE"),
        ("oracle", "ORA-00933", "ORA-00933: SQL command not properly ended", "SQL_EXECUTION_ERROR",
         "NOT_APPLICABLE", None),
        ("oracle", "ORA-01031", "ORA-01031: insufficient privileges", "PERMISSION_DENIED",
         "NOT_APPLICABLE", None),
    ],
)
def test_four_engine_errors_have_distinct_categories_and_timeout_evidence(
    engine: str,
    code: str | int,
    message: str,
    category: str,
    cancel_status: str,
    mechanism: str | None,
) -> None:
    exc = _error(engine, code, f"{message}; PWD={SECRET}")
    detail = _normalize(engine, exc)
    detail = with_timeout_evidence(
        detail,
        execution_phase="Fetch",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=5012,
        script_path=f"{engine}/active_requests.sql",
    )
    payload = detail.to_dict()
    evidence = payload["timeout"]

    assert payload["error_category"] == category
    assert payload["sqlstate"] == (str(code) if engine in {"sqlserver", "postgresql"} else None)
    assert evidence["execution_phase"] == "Fetch"
    assert evidence["timeout_seconds"] == 5
    assert evidence["effective_timeout_seconds"] == 5
    assert evidence["script_timeout_seconds"] == 15
    assert evidence["elapsed_ms"] == 5012
    assert evidence["cancellation_status"] == cancel_status
    assert evidence["connection_status"] == "UNKNOWN"
    assert evidence["script_path"] == f"{engine}/active_requests.sql"
    assert evidence["mechanism"] == mechanism
    if engine == "oracle" and code == "ORA-01013":
        assert evidence["cancellation_reason"] == "cancellation_source_unknown"
    if engine == "mysql" and code == 1317:
        assert evidence["cancellation_reason"] == "server_query_interrupted_source_unknown"
        assert "KILL" not in str(evidence["cancellation_reason"])
    assert SECRET not in repr(payload)
    assert "[REDACTED]" in payload["message"]


def test_mysql_client_socket_timeout_does_not_claim_server_cancellation() -> None:
    detail = normalize_mysql_error(TimeoutError(f"read timed out PWD={SECRET}"), (SECRET,))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Fetch",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=5,
        elapsed_ms=5000,
        script_path="mysql/top_sql.sql",
    )
    assert detail.error_category == "TIMEOUT"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCEL_STATUS_UNKNOWN"
    assert detail.timeout.mechanism == "CLIENT_WAIT_TIMEOUT"
    assert "server_stop_unconfirmed" in detail.timeout.cancellation_reason or (
        "database_stop_unconfirmed" in (detail.timeout.cancellation_reason or "")
    )


def test_postgresql_client_network_timeout_has_unknown_cancellation_status() -> None:
    detail = normalize_postgresql_error(TimeoutError(f"network read timed out PWD={SECRET}"), (SECRET,))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Fetch",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=5,
        elapsed_ms=5000,
        script_path="postgresql/top_sql.sql",
    )
    assert detail.error_category == "TIMEOUT"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCEL_STATUS_UNKNOWN"
    assert detail.timeout.mechanism == "CLIENT_WAIT_TIMEOUT"
    assert "database_stop_unconfirmed" in (detail.timeout.cancellation_reason or "")
    assert SECRET not in repr(detail.to_dict())


def test_postgresql_57014_without_confirmed_cause_keeps_unknown_reason() -> None:
    detail = normalize_postgresql_error(_error("postgresql", "57014", "cancelled"))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Execute",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=5012,
        script_path="postgresql/active_requests.sql",
    )
    assert detail.error_category == "CANCELLED_CONFIRMED"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCELLED_CONFIRMED"
    assert detail.timeout.cancellation_reason == "query_cancelled_cause_unspecified"
    assert detail.message == "cancelled"


def test_postgresql_57014_without_timeout_message_keeps_unknown_cause() -> None:
    detail = normalize_postgresql_error(_error("postgresql", "57014", "consulta interrompida"))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Execute",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=5012,
        script_path="postgresql/active_requests.sql",
    )
    assert detail.error_category == "CANCELLED_CONFIRMED"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCELLED_CONFIRMED"
    assert detail.timeout.cancellation_reason == "query_cancelled_cause_unspecified"


def test_postgresql_57014_server_timeout_message_confirms_cause_without_deadline_evidence() -> None:
    detail = normalize_postgresql_error(
        _error("postgresql", "57014", "canceling statement due to statement timeout")
    )
    detail = with_timeout_evidence(
        detail,
        execution_phase="Execute",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=1200,
        script_path="postgresql/active_requests.sql",
    )
    assert detail.error_category == "TIMEOUT"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCELLED_CONFIRMED"
    assert detail.timeout.cancellation_reason == "statement_timeout_reported_by_server"
    assert detail.message == "canceling statement due to statement timeout"


def test_sqlserver_hyt01_is_a_connection_timeout() -> None:
    detail = normalize_sqlserver_error(_error("sqlserver", "HYT01", "Connection timeout expired"), (SECRET,))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Connection",
        timeout_seconds=5,
        effective_timeout_seconds=None,
        script_timeout_seconds=None,
        elapsed_ms=5000,
        script_path=None,
    )
    assert detail.error_category == "TIMEOUT"
    assert detail.timeout is not None
    assert detail.timeout.execution_phase == "Connection"
    assert detail.timeout.cancellation_status == "NOT_APPLICABLE"
    assert detail.timeout.mechanism == "ODBC_CONNECTION_TIMEOUT"
    assert detail.timeout.script_path is None


def test_oracle_code_zero_does_not_turn_unknown_error_into_timeout_or_cancel() -> None:
    detail = normalize_oracle_error(
        Exception(SimpleNamespace(code=0, full_code="", message=f"driver output PWD={SECRET}")),
        (SECRET,),
    )
    assert detail.vendor_code is None
    assert detail.error_category == "SQL_EXECUTION_ERROR"
    assert detail.classification == "UNKNOWN"


def test_oracle_dpy_1001_is_connection_loss() -> None:
    detail = normalize_oracle_error(
        _error("oracle", "DPY-1001", "DPY-1001: not connected to database"), (SECRET,)
    )
    detail = with_timeout_evidence(
        detail,
        execution_phase="Fetch",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=100,
        script_path="oracle/active_requests.sql",
    )
    assert detail.error_category == "CONNECTION_LOST"
    assert detail.timeout is not None
    assert detail.timeout.cancellation_status == "CANCEL_STATUS_UNKNOWN"
    assert detail.timeout.cancellation_reason == "database_stop_unconfirmed"
    assert SECRET not in repr(detail.to_dict())


def test_connection_disposition_updates_only_the_existing_timeout_payload() -> None:
    detail = normalize_postgresql_error(_error("postgresql", "57014", "statement timeout"))
    detail = with_timeout_evidence(
        detail,
        execution_phase="Execute",
        timeout_seconds=5,
        effective_timeout_seconds=5,
        script_timeout_seconds=15,
        elapsed_ms=5000,
        script_path="postgresql/active_requests.sql",
    )
    discarded = with_connection_status(detail, "DISCARDED")
    assert detail.timeout is not None and discarded.timeout is not None
    assert detail.timeout.connection_status == "UNKNOWN"
    assert discarded.timeout.connection_status == "DISCARDED"
    assert discarded.error_category == "TIMEOUT"
    assert discarded.sqlstate == "57014"


def test_unsupported_version_has_its_own_category() -> None:
    assert ToolkitError("UnsupportedVersion", "unsupported").error_category == "VERSION_UNSUPPORTED"
