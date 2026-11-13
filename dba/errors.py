from typing import Any

from dba.database_errors import (
    DatabaseErrorDetail,
    normalize_sqlserver_error,
    with_connection_status,
    with_timeout_evidence,
)


class ToolkitError(RuntimeError):
    """Only fixed, application-owned messages cross the driver boundary."""

    def __init__(
        self, error_type: str, message: str, *, trigger_reason: dict[str, str] | None = None,
        database_error: object | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.trigger_reason = trigger_reason
        self.database_error = database_error
        detail = database_error
        self.error_category: str | None
        if isinstance(detail, DatabaseErrorDetail):
            self.error_category = detail.error_category
        elif isinstance(detail, dict) and isinstance(detail.get("error_category"), str):
            self.error_category = detail["error_category"]
        else:
            self.error_category = _error_category_for_type(error_type)

    def database_error_payload(self) -> dict[str, Any] | None:
        detail = self.database_error
        if isinstance(detail, DatabaseErrorDetail):
            return {"error_type": self.error_type, **detail.to_dict()}
        if isinstance(detail, dict):
            payload = {"error_type": self.error_type, **detail}
            if self.error_category is not None:
                payload.setdefault("error_category", self.error_category)
            return payload
        return None


def client_sql_deadline_error(
    database_type: str,
    driver: str,
    *,
    execution_phase: str,
    timeout_seconds: int,
    script_timeout_seconds: int,
    elapsed_ms: int,
    script_path: str,
) -> ToolkitError:
    """Represent a local deadline without claiming the server stopped the SQL."""
    detail = DatabaseErrorDetail(
        database_type=database_type,
        driver=driver,
        vendor_code=None,
        sqlstate=None,
        message="Client SQL deadline expired; database-side stop is unconfirmed.",
        classification="TIMEOUT",
        error_category="TIMEOUT",
    )
    detail = with_timeout_evidence(
        detail,
        execution_phase=execution_phase,
        timeout_seconds=timeout_seconds,
        effective_timeout_seconds=timeout_seconds,
        script_timeout_seconds=script_timeout_seconds,
        elapsed_ms=elapsed_ms,
        script_path=script_path,
    )
    return ToolkitError(
        f"{execution_phase}Timeout",
        "Client SQL deadline expired; database-side stop is unconfirmed.",
        database_error=detail,
    )


def _error_category_for_type(error_type: str) -> str | None:
    if error_type == "UnsupportedVersion":
        return "VERSION_UNSUPPORTED"
    if error_type.endswith("Denied") or error_type == "QueryDenied":
        return "PERMISSION_DENIED"
    if "Timeout" in error_type:
        return "TIMEOUT"
    if "ConnectionLost" in error_type:
        return "CONNECTION_LOST"
    if error_type in {"ConnectionFailed", "ConnectionTLSFailed"}:
        return "CONNECTION_FAILED"
    if "CancelStatusUnknown" in error_type:
        return "CANCEL_STATUS_UNKNOWN"
    if "Cancelled" in error_type or "Canceled" in error_type:
        return "CANCELLED_CONFIRMED"
    if error_type in {"QueryFailed", "ExecuteFailed", "FetchFailed"}:
        return "SQL_EXECUTION_ERROR"
    return None


def database_error_log_fields(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Project sanitized driver diagnostics onto the JSONL event contract."""
    if detail is None:
        return {}
    fields = {key: detail.get(key) for key in (
        "database_type", "driver", "vendor_code", "sqlstate", "classification", "error_category",
    )}
    timeout = detail.get("timeout")
    if isinstance(timeout, dict):
        fields.update({key: timeout.get(key) for key in (
            "execution_phase", "timeout_seconds", "effective_timeout_seconds",
            "cancellation_status", "connection_status", "script_path",
        )})
    return {**fields, "sanitized_message": detail.get("message")}


def driver_error(
    exc: Exception,
    phase: str,
    *,
    secret: str = "",
    driver: str = "pyodbc",
    execution_phase: str | None = None,
    timeout_seconds: int | None = None,
    effective_timeout_seconds: int | None = None,
    script_timeout_seconds: int | None = None,
    elapsed_ms: int | None = None,
    script_path: str | None = None,
) -> ToolkitError:
    detail = normalize_sqlserver_error(exc, (secret,), driver)
    if script_path is not None and execution_phase is not None:
        detail = with_timeout_evidence(
            detail,
            execution_phase=execution_phase,
            timeout_seconds=timeout_seconds,
            effective_timeout_seconds=effective_timeout_seconds,
            script_timeout_seconds=script_timeout_seconds,
            elapsed_ms=elapsed_ms,
            script_path=script_path,
        )
    if detail.error_category == "TIMEOUT":
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.",
                            database_error=detail)
    if detail.error_category == "PERMISSION_DENIED":
        return ToolkitError(
            "QueryDenied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this SQL Server version and denied operation.",
            database_error=detail,
        )
    if detail.error_category == "CONNECTION_LOST":
        error_type = "ConnectionLost" if phase == "Connection" else "QueryConnectionLost"
        return ToolkitError(error_type, "Connection was lost during database operation.", database_error=detail)
    if detail.error_category == "CANCELLED_CONFIRMED":
        return ToolkitError("QueryCancelled", "The database confirmed that the query was cancelled.",
                            database_error=detail)
    if detail.error_category == "CANCEL_STATUS_UNKNOWN":
        return ToolkitError("QueryCancelStatusUnknown", "Query cancellation status is unknown; connection will be discarded.",
                            database_error=detail)
    return ToolkitError(
        f"{phase}Failed",
        f"{phase} failed; vendor error details are available below.",
        database_error=detail,
    )


def set_database_error_connection_status(error: ToolkitError, status: str) -> None:
    """Record whether a failed query connection was discarded successfully."""
    detail = error.database_error
    if isinstance(detail, DatabaseErrorDetail):
        error.database_error = with_connection_status(detail, status)
    elif isinstance(detail, dict):
        timeout = detail.get("timeout")
        if isinstance(timeout, dict):
            error.database_error = {**detail, "timeout": {**timeout, "connection_status": status}}


def sqlserver_connection_error(
    exc: Exception,
    *,
    driver: str,
    encrypt: bool,
    trust_server_certificate: bool,
    secret: str = "",
    connection_timeout_seconds: int | None = None,
    elapsed_ms: int | None = None,
) -> ToolkitError:
    """Map an ODBC connection error without returning driver-controlled text."""
    error = driver_error(exc, "Connection", secret=secret, driver=driver)
    if error.error_category == "TIMEOUT" and isinstance(error.database_error, DatabaseErrorDetail):
        detail = with_timeout_evidence(
            error.database_error,
            execution_phase="Connection",
            timeout_seconds=connection_timeout_seconds,
            effective_timeout_seconds=None,
            script_timeout_seconds=None,
            elapsed_ms=elapsed_ms,
            script_path=None,
        )
        error = ToolkitError(error.error_type, str(error), database_error=detail)
    if error.error_type == "ConnectionTimeout":
        return error

    # pyodbc commonly reports TLS failures as 08001 with the useful evidence
    # embedded in a later argument. Inspect it only for classification; never
    # return the raw text because it may contain connection-string values.
    evidence = " ".join(str(item) for item in exc.args).casefold()
    tls_markers = (
        "certificate",
        "ssl provider",
        "tls",
        "encryption",
        "trustservercertificate",
    )
    error_type = (
        "ConnectionTLSFailed"
        if any(marker in evidence for marker in tls_markers)
        else error.error_type
    )
    return ToolkitError(
        error_type,
        "SQL Server connection failed. "
        f"Selected driver: {driver}. "
        f"Effective TLS settings: Encrypt={'yes' if encrypt else 'no'}, "
        f"TrustServerCertificate={'yes' if trust_server_certificate else 'no'}. "
        "Check network, account and certificate settings; pass an approved --settings file "
        "when the target requires different explicit TLS settings. Toolkit did not retry with "
        "weaker TLS settings.",
        database_error=error.database_error,
    )
