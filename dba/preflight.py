from typing import TYPE_CHECKING

from dba.capabilities import ServerCapabilities
from dba.errors import ToolkitError

if TYPE_CHECKING:
    from dba.sqlserver import SQLServerAdapter


READ_PERMISSIONS = frozenset(
    {
        "CONNECT SQL",
        "CONNECT",
        "VIEW ANY DATABASE",
        "VIEW SERVER STATE",
        "VIEW SERVER PERFORMANCE STATE",
        "VIEW SERVER SECURITY STATE",
        "VIEW ANY DEFINITION",
        "VIEW DEFINITION",
        "VIEW DATABASE STATE",
        "VIEW DATABASE PERFORMANCE STATE",
        "VIEW DATABASE SECURITY STATE",
        "SELECT",
        "REFERENCES",
        "VIEW CHANGE TRACKING",
        "VIEW ANY ERROR LOG",
        "VIEW SECURITY DEFINITION",
        "VIEW PERFORMANCE DEFINITION",
        "VIEW ANY SECURITY DEFINITION",
        "VIEW ANY PERFORMANCE DEFINITION",
        "VIEW ANY COLUMN ENCRYPTION KEY DEFINITION",
        "VIEW ANY COLUMN MASTER KEY DEFINITION",
    }
)


class SQLServerMonitorPreflight:
    """Checks effective rights in the connected database, then real read probes.

    This cannot certify rights in every other database. DBA provisioning must
    enforce a dedicated monitoring login across the instance.
    """

    def check(self, adapter: "SQLServerAdapter") -> None:
        def probe(name: str) -> tuple[dict[str, object], ...]:
            result = adapter._run(adapter.script_for(name))
            adapter.preflight_checks[name] = result.success
            if not result.success:
                raise ToolkitError(
                    result.error_type or "PreflightFailed",
                    f"Preflight capability failed: {name}. {result.error_message}",
                )
            if name.startswith("monitor_") and result.truncated:
                raise ToolkitError(
                    "PreflightIncomplete", "Account preflight results reached the row limit."
                )
            return result.rows

        capabilities = probe("monitor_capabilities")
        if len(capabilities) != 1:
            raise ToolkitError(
                "PreflightIncomplete", "Account capability probe returned invalid data."
            )
        account = capabilities[0]
        high_role_fields = (
            ("is_sysadmin", "sysadmin", "server_role"),
            ("is_securityadmin", "securityadmin", "server_role"),
            ("is_serveradmin", "serveradmin", "server_role"),
            ("is_db_owner", "db_owner", "database_role"),
            ("is_database_owner", "current database owner", "database_ownership"),
        )
        unknown_roles: list[str] = []
        for field, name, reason_type in high_role_fields:
            value = account.get(field)
            if value == 1:
                raise ToolkitError(
                    "PrivilegedAccount",
                    f"High-risk membership detected: {name}.",
                    trigger_reason={"type": reason_type, "name": name},
                )
            if value not in (0, 1):
                unknown_roles.append(name)
        permissions = probe("monitor_permissions")
        high_risk = {
            ("SERVER", "CONTROL SERVER"): "CONTROL SERVER",
            ("SERVER", "ALTER ANY LOGIN"): "ALTER ANY LOGIN",
            ("SERVER", "IMPERSONATE ANY LOGIN"): "IMPERSONATE ANY LOGIN",
            ("SERVER", "TAKE OWNERSHIP"): "TAKE OWNERSHIP",
            ("DATABASE", "CONTROL"): "CONTROL DATABASE",
            ("DATABASE", "CONTROL DATABASE"): "CONTROL DATABASE",
            ("DATABASE", "TAKE OWNERSHIP"): "TAKE OWNERSHIP",
            ("DATABASE", "IMPERSONATE"): "IMPERSONATE",
        }
        permission_warnings: list[dict[str, str]] = []
        for row in permissions:
            scope = str(row.get("scope", "")).upper()
            name = str(row.get("permission_name", "")).upper()
            source_type = row.get("source_type")
            source_name = str(row.get("source_name", "unknown source"))
            object_name = str(row.get("object_name") or "")
            object_type = str(row.get("object_type") or "")
            principal = source_name.casefold()
            object_key = object_name.replace("[", "").replace("]", "").casefold()
            public_inherited = source_type == "INHERITED" and principal == "public"
            execute_risk = False
            if name == "EXECUTE" and source_type in {"EXPLICIT", "INHERITED"}:
                execute_risk = scope == "DATABASE" and not public_inherited
                if scope == "OBJECT":
                    dangerous_modules = {
                        "sys.sp_addsrvrolemember", "sys.sp_droprolemember",
                        "sys.sp_dropsrvrolemember", "sys.sp_addrolemember",
                        "sys.sp_addlogin", "sys.sp_droplogin",
                        "sys.sp_adduser", "sys.sp_grantdbaccess",
                    }
                    execute_as = row.get("execute_as_principal_id")
                    execute_risk = (
                        (object_key in dangerous_modules and not public_inherited)
                        or execute_as is not None
                        or (not object_key.startswith("sys.") and not public_inherited)
                    )
            clearly_risky = (
                name in {"INSERT", "UPDATE", "DELETE", "ALTER", "DELEGATION"}
                or name.startswith(("ALTER ", "CREATE ", "DROP "))
            )
            if source_type in {"EXPLICIT", "INHERITED"} and (
                (scope, name) in high_risk or clearly_risky or execute_risk
            ):
                display_name = high_risk.get((scope, name), name)
                trigger_reason = {
                    "type": "server_permission" if scope == "SERVER" else "database_permission",
                    "name": display_name,
                    "source": "explicit" if source_type == "EXPLICIT" else "inherited",
                    "principal": source_name,
                    "scope": scope,
                }
                if object_name:
                    trigger_reason["object"] = object_name
                if object_type:
                    trigger_reason["object_type"] = object_type
                raise ToolkitError(
                    "PrivilegedAccount",
                    f"Explicit high-risk permission detected: {display_name}.",
                    trigger_reason=trigger_reason,
                )
            if (source_type in {"EXPLICIT", "INHERITED"}
                    and row.get("permission_name") not in READ_PERMISSIONS):
                permission_warnings.append({"name": name, "scope": scope,
                                            "object": object_name or "unspecified",
                                            "source": source_name,
                                            "source_type": str(source_type)})
        effective = sorted({
            str(row.get("permission_name"))
            for row in permissions
            if row.get("source_type") == "EFFECTIVE"
            and row.get("permission_name") in READ_PERMISSIONS
        })
        unknown_effective = sorted({
            str(row.get("permission_name"))
            for row in permissions
            if row.get("source_type") == "EFFECTIVE"
            and row.get("permission_name") not in READ_PERMISSIONS
        })
        explicit = sorted({
            str(row.get("permission_name"))
            for row in permissions
            if row.get("source_type") == "EXPLICIT"
        })
        adapter.privilege_classification = {
            "high_risk_server_roles": [],
            "high_risk_server_permissions": [],
            "high_risk_database_roles": [],
            "database_ownership": [],
            "effective_monitoring_permissions": effective,
            "explicit_permissions": explicit,
            "unknown_effective_permissions": unknown_effective,
            "unknown_membership_checks": unknown_roles,
            "unclassified_permissions": permission_warnings,
            "classification": "UNKNOWN" if unknown_roles or unknown_effective or permission_warnings else "MONITORING_ACCOUNT",
            "warning": bool(unknown_roles or unknown_effective or permission_warnings),
        }
        major = account.get("major_version")
        if not isinstance(major, int) or major < 10:
            raise ToolkitError("UnsupportedVersion", "SQL Server 2008 R2 or later is required.")
        permission = "view_server_performance_state" if major >= 16 else "view_server_state"
        adapter.permission_observations.clear()
        if account.get("view_server_state") == 1:
            adapter.permission_observations.add("VIEW SERVER STATE")
        if account.get("view_server_performance_state") == 1:
            adapter.permission_observations.add("VIEW SERVER PERFORMANCE STATE")
        if account.get("view_any_database") == 1:
            adapter.permission_observations.add("VIEW ANY DATABASE")
        if account.get("backup_history_select") == 1:
            adapter.permission_observations.add("SELECT msdb.dbo.backupset")
        if account.get(permission) != 1:
            raise ToolkitError(
                "MissingPermission",
                f"Missing cross-session visibility: {permission.upper().replace('_', ' ')}. DBA review required; no grants are executed.",
            )
        if account.get("view_any_database") != 1:
            raise ToolkitError(
                "MissingPermission",
                "Missing database inventory visibility: VIEW ANY DATABASE. DBA review required.",
            )
        info = probe("server_info")
        if not info or not isinstance(info[0].get("product_version"), str):
            raise ToolkitError("PreflightIncomplete", "Basic instance information is unavailable.")
        adapter._version = str(info[0]["product_version"])
        for field in ("sessions_database_id", "sessions_open_transaction_count"):
            if account.get(field) not in (0, 1):
                raise ToolkitError(
                    "PreflightIncomplete", "Session column capability information is unavailable."
                )
        adapter.capabilities = ServerCapabilities(
            adapter._version,
            bool(account["sessions_database_id"]),
            bool(account["sessions_open_transaction_count"]),
        )
        for name in ("database_status", "active_sessions", "active_requests"):
            probe(name)
