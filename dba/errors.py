from dba.database_errors import normalize_sqlserver_error


class ToolkitError(RuntimeError):
    """Only fixed, application-owned messages cross the driver boundary."""

    def __init__(
        self, error_type: str, message: str, *, trigger_reason: dict[str, str] | None = None,
        database_error: object | None = None,
        permission_readiness: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.trigger_reason = trigger_reason
        self.database_error = database_error
        self.permission_readiness = permission_readiness


def driver_error(exc: Exception, phase: str) -> ToolkitError:
    state = exc.args[0] if exc.args else None
    if isinstance(exc, TimeoutError) or state in ("HYT00", "HYT01"):
        return ToolkitError(f"{phase}Timeout", f"{phase} timed out; check configured limits.")
    if state == "42000":
        return ToolkitError(
            "QueryDenied",
            "Query denied or unsupported; check monitoring permissions and SQL Server version.",
        )
    return ToolkitError(
        f"{phase}Failed",
        f"{phase} failed; check connectivity, driver, certificate and monitoring account configuration.",
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
    error = driver_error(exc, "Connection")
    detail = normalize_sqlserver_error(exc, (secret,), driver)
    if error.error_type == "ConnectionTimeout":
        error.database_error = detail
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
        database_error=detail,
    )
