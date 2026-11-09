from typing import Any

from dba.database_errors import DatabaseErrorDetail, normalize_sqlserver_error


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

    def database_error_payload(self) -> dict[str, Any] | None:
        detail = self.database_error
        if isinstance(detail, DatabaseErrorDetail):
            return {"error_type": self.error_type, **detail.to_dict()}
        if isinstance(detail, dict):
            return {"error_type": self.error_type, **detail}
        return None


def database_error_log_fields(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Project sanitized driver diagnostics onto the JSONL event contract."""
    if detail is None:
        return {}
    return {**{key: detail.get(key) for key in (
        "database_type", "driver", "vendor_code", "sqlstate", "classification",
    )}, "sanitized_message": detail.get("message")}


def driver_error(exc: Exception, phase: str, *, secret: str = "", driver: str = "pyodbc") -> ToolkitError:
    detail = normalize_sqlserver_error(exc, (secret,), driver)
    if detail.classification == "TIMEOUT":
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.",
                            database_error=detail)
    if detail.classification == "ACCESS_DENIED":
        return ToolkitError(
            "QueryDenied",
            f"{detail.message} Review docs/DATABASE_PERMISSIONS.md for this SQL Server version and denied operation.",
            database_error=detail,
        )
    return ToolkitError(
        f"{phase}Failed",
        f"{phase} failed; vendor error details are available below.",
        database_error=detail,
    )


def sqlserver_connection_error(
    exc: Exception,
    *,
    driver: str,
    encrypt: bool,
    trust_server_certificate: bool,
    secret: str = "",
) -> ToolkitError:
    """Map an ODBC connection error without returning driver-controlled text."""
    error = driver_error(exc, "Connection", secret=secret, driver=driver)
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
