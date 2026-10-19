"""Shared, database-neutral presentation of CLI domain results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from dba.core import DiagnosticReport
from dba.incident import IncidentSummary
from dba.logging import redact


class OutputMode(StrEnum):
    HUMAN = "human"
    JSON = "json"


@dataclass(frozen=True)
class CheckResult:
    database: dict[str, Any]
    runtime: dict[str, Any]
    credential: dict[str, Any]
    connection: dict[str, Any]
    permissions: tuple[dict[str, Any], ...]
    status: str
    warnings: tuple[str, ...] = ()
    account_security: dict[str, Any] | None = None
    version_profile: dict[str, Any] | None = None


@dataclass(frozen=True)
class StatusResult:
    database: dict[str, Any]
    version: str | None
    connection: str
    active_sessions: int | None
    active_requests: int | None
    long_running: int | None
    database_states: dict[str, int]
    status: str
    warnings: tuple[str, ...] = ()


def render_json(value: Any) -> str:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.dumps(redact(payload), ensure_ascii=False, indent=2, default=str)


def _line(label: str, value: Any) -> str:
    safe = redact(value)
    return f"  {label:<24}{safe if safe not in (None, '') else 'UNKNOWN'}"


def _first(row: dict[str, Any], *names: str) -> Any:
    return next((row[name] for name in names if row.get(name) is not None), None)


def render_human(command: str, value: Any) -> str:
    """Render common results without coupling domain modules to the terminal."""
    data = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    if isinstance(data, dict) and data.get("error_type"):
        return _render_error(data)
    if command == "check":
        database = data["database"]
        lines = ["DBA Toolkit Check", "-" * 42, "", "Database",
                 _line("ID", database.get("id")), _line("Type", database.get("db_type")),
                 _line("Target", data["connection"].get("target")),
                 _line("Database", data["connection"].get("database")), "", "Runtime",
                 _line("Python", data["runtime"].get("python")),
                 _line("Status", data["runtime"].get("status")),
                 _line("Driver", data["runtime"].get("driver")),
                 _line("Driver Version", data["runtime"].get("driver_version")), "", "Credential",
                 _line("Alias", data["credential"].get("alias")),
                 _line("Backend", data["credential"].get("backend")),
                 _line("Status", data["credential"].get("status")), "", "Connection",
                 _line("Status", data["connection"].get("status")),
                 _line("TLS", data["connection"].get("tls")), "", "Account Security",
                 _line("Classification", (data.get("account_security") or {}).get("classification")),
                 "", "Version",
                 _line("Declared Family", (data.get("version_profile") or {}).get(
                     "declared_version_family")),
                 _line("Detected Version", (data.get("version_profile") or {}).get(
                     "detected_version")),
                 _line("Detected Family", (data.get("version_profile") or {}).get(
                     "detected_version_family")),
                 _line("Version Status", (data.get("version_profile") or {}).get(
                     "version_status")),
                 "", "Permission Readiness",
                 "  Feature             Status", "  " + "-" * 25]
        lines.extend(f"  {row['feature']:<20}{row['status']}" for row in data["permissions"])
        for row in data["permissions"]:
            if row.get("missing_required"):
                lines.append(_line(f"{row['feature']} missing", ", ".join(row["missing_required"])))
            if row.get("missing_optional"):
                lines.append(_line(f"{row['feature']} optional", ", ".join(row["missing_optional"])))
            if row.get("impact"):
                lines.append(_line(f"{row['feature']} impact", row["impact"]))
        lines.extend(["", "Overall", _line("Status", data["status"])])
        if data.get("warnings"):
            lines.extend(["", "Warnings"])
            lines.extend(f"  {i}. {warning}" for i, warning in enumerate(data["warnings"], 1))
        detection_error = (data.get("version_profile") or {}).get("version_detection_error")
        if detection_error:
            lines.extend(["", "Version Detection", _line("Error", detection_error.get("error_type")),
                          _line("Detail", detection_error.get("message"))])
            if detection_error.get("required_detection_privilege"):
                lines.append(_line("Required Privilege",
                                   detection_error["required_detection_privilege"]))
        return "\n".join(lines)
    if command == "status":
        database = data["database"]
        lines = ["Database Status", "-" * 42, "", "Database",
                 _line("ID", database.get("id")), _line("Type", database.get("db_type")),
                 _line("Version", data.get("version")),
                 _line("Target", database.get("target")), "", "Connection",
                 _line("Status", data["connection"]), "", "Sessions",
                 _line("Active Sessions", data.get("active_sessions")),
                 _line("Active Requests", data.get("active_requests")),
                 _line("Long Running", data.get("long_running")), "", "Database States"]
        lines.extend(_line(state, count) for state, count in data["database_states"].items())
        lines.extend(["", "Current Health",
                      _line("Status", data["status"]),
                      ("  No obvious active issue detected." if data["status"] == "READY" else
                       "  Some snapshot data is incomplete; review warnings below.")])
        lines.extend(["", "Suggested Next Step", "  Business reports slow:",
                      f"    dba diagnose {database.get('id')} slow", "",
                      "  Disk/log growth:", f"    dba diagnose {database.get('id')} disk-growth"])
        if data.get("warnings"):
            lines.extend(["", "Warnings", *[f"  - {item}" for item in data["warnings"]]])
        return "\n".join(lines)
    if command == "permissions":
        lines = ["Permission Check", "-" * 42, "", "Database",
                 _line("ID", data.get("database_id")), "", "Monitoring Account"]
        principal = data.get("monitoring_principal")
        if isinstance(principal, dict):
            lines.extend([_line("Credential Username", principal.get("credential_username")
                                or data.get("account")),
                          _line("Database Principal", principal.get("database_principal") or "UNKNOWN"),
                          _line("Principal Verification", principal.get("verification", "UNVERIFIED"))])
        else:
            lines.append(_line("Account", data.get("account")))
        lines.extend(["", "Feature          Status", "-" * 30])
        lines.extend(f"{row['feature']:<17}{row['status']}" for row in data.get("features", []))
        for row in data.get("features", []):
            if row.get("missing_required"):
                lines.append(_line(f"{row['feature']} missing", ", ".join(row["missing_required"])))
            if row.get("required_privileges"):
                lines.extend([f"  {row['feature']} required permissions",
                              *[f"  {item}" for item in row["required_privileges"]]])
            if row.get("missing_optional"):
                lines.append(_line(f"{row['feature']} optional", ", ".join(row["missing_optional"])))
            if row.get("impact") and row.get("status") in {"PARTIAL", "MISSING", "UNKNOWN"}:
                lines.append(_line(f"{row['feature']} impact", row["impact"]))
            if row.get("reference_grant"):
                lines.extend([f"  {row['feature']} reference grants",
                              row["reference_grant"], "Toolkit did NOT execute this script."])
        runtime = data.get("runtime_verification")
        version = data.get("version_profile")
        if runtime:
            lines.extend(["", "Version",
                          _line("Declared Family", (version or {}).get("declared_version_family")),
                          _line("Detected Version", (version or {}).get("detected_version") or "UNKNOWN"),
                          _line("Detected Family", (version or {}).get("detected_version_family") or "UNKNOWN"),
                          _line("Version Status", (version or {}).get("version_status")),
                          "", "Runtime Verification", _line("Status", runtime.get("status")),
                          _line("Classification", runtime.get("classification")),
                          _line("Reason", runtime.get("message"))])
            vendor = runtime.get("vendor_error")
            if isinstance(vendor, dict):
                lines.append(_line("Vendor Error", vendor.get("message") or vendor.get("vendor_code")))
                lines.append(_line("Toolkit Classification", vendor.get("classification", "UNKNOWN")))
                if vendor.get("guidance"):
                    lines.append(_line("Vendor Guidance", vendor["guidance"]))
            if data.get("guidance"):
                lines.extend(["", "Guidance", f"  {data['guidance']}"])
            row = next((item for item in data.get("features", [])
                        if item.get("feature") == "replication"), {})
            if row.get("required_privileges"):
                lines.extend(["", "Required Permissions",
                              *[f"  {item}" for item in row["required_privileges"]]])
            if row.get("reference_grant"):
                lines.extend(["", "Reference Grants", row["reference_grant"],
                              "Toolkit did NOT execute this script."])
        if data.get("generated_sql"):
            lines.extend(["", "Generated SQL", data["generated_sql"],
                          "Toolkit did NOT execute this script."])
        elif all(row.get("status") in {"READY", "UNSUPPORTED"} for row in data.get("features", [])):
            lines.extend(["", "Result", "  Monitoring permissions are ready."])
        else:
            lines.extend(["", "Result", "  Some monitoring permissions need DBA review."])
        if not runtime and data.get("guidance"):
            lines.extend(["", "Guidance", f"  {data['guidance']}"])
        return "\n".join(lines)
    if command == "doctor":
        lines = ["DBA Toolkit Doctor", "-" * 42, "", "Core Runtime",
                 _line("Python", data.get("python", {}).get("status")),
                 _line("Toolkit", data.get("toolkit")),
                 _line("Credential", data.get("credential_store", {}).get("status")),
                 "", "Database Runtimes"]
        credential = data.get("credential_store", {})
        session = credential.get("session", credential.get("credential_session", {}))
        if session:
            lines.extend(["", "Credential Session",
                          _line("Status", session.get("status", "UNKNOWN"))])
        providers = credential.get("providers", {})
        if providers:
            lines.extend(["", "Credential Security"])
            for name, provider in providers.items():
                lines.append(f"  {name:<28} {provider.get('status', 'UNKNOWN')}")
                if name == "Encrypted Local Store":
                    for key, label in (("cipher", "Encryption"), ("kdf", "KDF"),
                                       ("format", "Format"), ("permission_check", "Permission Check")):
                        if key in provider:
                            lines.append(_line(label, provider[key]))
            crypto_packages = credential.get("crypto_packages", {})
            if crypto_packages:
                lines.extend(["", "Credential Crypto Dependencies"])
                for name, package in crypto_packages.items():
                    lines.append(_line(package.get("package", name),
                                       f"{package.get('status')} {package.get('version') or ''}".strip()))
            lines.append(_line("Selected Provider", credential.get("backend")))
        for name, component in data.get("components", {}).items():
            status = component.get("status", "UNKNOWN")
            lines.append(_line(name, status))
            if component.get("selected_native_driver"):
                lines.append(_line("Driver", component["selected_native_driver"]))
        lines.extend(["", "Overall", _line("Status", data.get("overall"))])
        return "\n".join(lines)
    if command == "runtime-info":
        lines = ["DBA Toolkit Runtime", "-" * 42,
                 _line("Toolkit", data.get("toolkit_version")),
                 _line("Python", data.get("python", {}).get("version")),
                 _line("Executable", data.get("python", {}).get("executable")),
                 _line("Platform", data.get("platform", {}).get("platform"))]
        for name, component in data.get("drivers", {}).items():
            lines.append(_line(name, f"{component.get('status')} {component.get('version') or ''}".strip()))
        for name, component in data.get("credential_crypto_packages", {}).items():
            lines.append(_line(name, f"{component.get('status')} {component.get('version') or ''}".strip()))
        store_package = data.get("credential_store_package", {})
        lines.append(_line("keyring", f"{store_package.get('status')} {store_package.get('version') or ''}".strip()))
        selected = data.get("sqlserver_odbc", {}).get("selected")
        lines.append(_line("SQL Server ODBC", selected or "MISSING"))
        if data.get("oracle_mode"):
            lines.append(_line("Oracle Mode", data["oracle_mode"]))
        if data.get("credential_session"):
            lines.append(_line("Credential Session",
                               data["credential_session"].get("transport", "Supported")))
        return "\n".join(lines)
    if command == "driver-info":
        return "Driver Information\n" + "-" * 42 + "\n" + "\n".join(
            _line(str(key), value) for key, value in data.items()
        )
    if command == "incident-summary" and isinstance(data, dict):
        return render_incident_summary(data)
    if command == "list":
        lines = ["Databases", "-" * 64, "ID                   TYPE          ENVIRONMENT   NAME"]
        lines.extend(f"{row.get('id', ''):<21}{row.get('db_type', ''):<14}"
                     f"{row.get('environment', ''):<14}{row.get('name', '')}"
                     for row in data.get("databases", []))
        if not data.get("databases"):
            lines.append("No databases matched.")
        return "\n".join(lines)
    if command == "show":
        return "Inventory Entry\n" + "-" * 42 + "\n" + "\n".join(
            _line(key.replace("_", " ").title(), value)
            for key, value in data.items() if key not in {"connection"}
        ) + "\n\nEffective Connection\n" + "\n".join(
            _line(key.replace("_", " ").title(), value)
            for key, value in data.get("effective_connection", {}).items()
        )
    if command.startswith("credentials"):
        if command in {"credentials status", "credentials unlock", "credentials lock"}:
            session = data.get("session", {}) if isinstance(data.get("session"), dict) else {}
            status = data.get("status", "LOCKED")
            lines = ["Credential Session", "-" * 42, f"Status\n  {status}"]
            for key, label in (("backend", "Backend"), ("created_at", "Created"),
                               ("expires_at", "Expires"), ("expires_in", "Expires In"),
                               ("agent_pid", "Agent PID"), ("scope", "Scope"),
                               ("ttl_seconds", "TTL Seconds"),
                               ("secrets_persisted", "Secrets Persisted")):
                value = data.get(key, session.get(key))
                if value is not None:
                    if key == "expires_in":
                        value = f"{int(value) // 60}m {int(value) % 60}s"
                    lines.extend(["", label, f"  {value}"])
            if command == "credentials lock":
                lines.extend(["", "Already locked." if status == "ALREADY_LOCKED" else
                              "In-memory credentials cleared."])
            return "\n".join(lines)
        if command == "credentials test":
            return "Credential Store Test\n" + "-" * 42 + "\n" + "\n".join(
                _line(label, value) for label, value in
                (("Alias", data.get("alias")), ("Backend", data.get("backend")),
                 ("Status", "READY" if data.get("status") == "READY" else "FAILED"))
            )
        if "credentials" in data:
            return ("Alias".ljust(32) + "Username".ljust(24) + "Backend\n" + "-" * 76 + "\n" +
                    "\n".join(f"{item['alias']:<32}{item.get('username', ''):<24}{item['backend']}"
                              for item in data["credentials"]))
        if command == "credentials backend":
            providers = data.get("providers", {})
            lines = ["Credential Backends", "-" * 42]
            for name, provider in providers.items():
                lines.extend([name, f"  Status  {provider.get('status', 'UNKNOWN')}"])
                for key in ("path", "cipher", "kdf", "format", "permission_check",
                            "dependency_status", "required_packages"):
                    if key in provider:
                        label = {"path": "Path", "cipher": "Encryption", "kdf": "KDF",
                                 "format": "Format", "permission_check": "Permission Check"}.get(key, "")
                        if key == "dependency_status":
                            label = "Dependencies"
                        elif key == "required_packages":
                            label = "Packages"
                        value = ", ".join(provider[key]) if key == "required_packages" else provider[key]
                        lines.append(f"  {label:<16} {value}")
                lines.append("")
            lines.extend([f"Selected Provider  {data.get('backend')}",
                          f"Overall            {data.get('status')}"])
            session = data.get("credential_session", {})
            if isinstance(session, dict):
                lines.extend(["", "Credential Session",
                              f"  {session.get('status', 'LOCKED')}"])
                detail = session.get("session", {})
                if isinstance(detail, dict) and "expires_in" in detail:
                    seconds = int(detail["expires_in"])
                    lines.append(f"  Remaining {seconds // 60}m {seconds % 60}s")
            return "\n".join(lines)
        return "Credential\n" + "-" * 42 + "\n" + "\n".join(
            _line(key.title(), value) for key, value in data.items()
            if key not in {"stored", "updated", "deleted", "readable", "password", "providers",
                           "package", "crypto_packages", "fallback", "stored_aliases"}
        )
    if command == "test-connection":
        connection = data.get("connection", {})
        tls = []
        for key in ("encrypt", "trust_server_certificate"):
            if key in connection:
                value = "yes" if connection[key] is True else "no" if connection[key] is False else connection[key]
                tls.append(f"{key.replace('_', '').title()}={value}")
        lines = ["Connection Successful" if data.get("success") else "Connection Failed",
                 "-" * 42, _line("Database", data.get("database_id")),
                 _line("Version", data.get("version")), _line("Target", connection.get("host")),
                 _line("Port", connection.get("port")), _line("Database", connection.get("database")),
                 _line("Driver", connection.get("driver"))]
        if tls:
            lines.extend(["TLS", "  " + "\n  ".join(tls)])
        cred = data.get("credential", {})
        if cred:
            lines.extend(["", "Credential", _line("Alias", cred.get("alias")),
                          _line("Backend", cred.get("backend"))])
        return "\n".join(lines)
    return _render_error(data) if isinstance(data, dict) and data.get("error_type") else _render_generic(command, data)


def render_incident_summary(value: IncidentSummary | dict[str, Any], verbose: bool = False) -> str:
    """Render the compact DBA response view; full evidence remains in reports."""
    data = asdict(value) if isinstance(value, IncidentSummary) else value
    snapshot = data.get("snapshot_time")
    if isinstance(snapshot, str):
        try:
            snapshot = datetime.fromisoformat(snapshot).astimezone().isoformat(
                sep=" ", timespec="seconds"
            )
        except ValueError:
            pass
    lines = ["Incident Summary", "-" * 42, "", "Snapshot",
             _line("Time", snapshot),
             _line("Data Completeness", data.get("data_completeness"))]
    if data.get("required_evidence_status") != "NOT_APPLICABLE":
        lines.extend([_line("Required Evidence", data.get("required_evidence_status")),
                      _line("Optional Evidence", data.get("optional_evidence_status"))])
        if data.get("unavailable_optional_evidence"):
            lines.append(_line("Unavailable Optional Evidence",
                               ", ".join(data["unavailable_optional_evidence"])))
    if data.get("missing_evidence"):
        lines.append(_line("Missing Evidence", ", ".join(data["missing_evidence"])))
    lines.extend(["", "Overall", _line("Status", data.get("status")),
                  _line("Severity", data.get("severity")),
                  _line("Primary Problem", data.get("primary_problem")),
                  _line("Observed Now", data.get("observed_now"))])
    if data.get("impact"):
        lines.extend(["", "Impact"])
        lines.extend(_line(label.replace("_", " ").title(), count)
                     for label, count in data["impact"].items())
    if data.get("timing"):
        lines.extend(["", "Timing"])
        lines.extend(_line(label.replace("_", " ").title(), value)
                     for label, value in data["timing"].items())
    if data.get("wait"):
        lines.extend(["", "Wait", _line("Category", data["wait"].get("category")),
                      _line("Event", data["wait"].get("event"))])
    root = data.get("root_cause_candidate")
    if root:
        lines.extend(["", "Root Cause Candidate",
                      _line("Session", root.get("session")),
                      _line("Reason", root.get("reason")),
                      _line("Status", root.get("status")),
                      _line("Blocked Sessions", root.get("blocked_sessions")),
                      _line("Open Transaction", root.get("open_transaction"))])
        if root.get("transaction_age_seconds") is not None:
            duration = root["transaction_age_seconds"]
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            lines.append(_line("Transaction Age", f"{hours:02d}:{minutes:02d}:{seconds:02d}"))
    if data.get("source"):
        lines.append("\nSource")
        lines.extend(_line(label.replace("_", " ").title(), val)
                     for label, val in data["source"].items())
    if data.get("immediate_action"):
        action = data["immediate_action"]
        lines.extend(["", "Immediate Action", _line("Priority", action.get("priority")),
                      _line("Action", action.get("action")),
                      _line("DBA Approval Required", "YES" if action.get("approval_required") else "NO")])
    actions = data.get("recovery_actions", [])
    lines.extend(["", "Recovery Action"])
    if actions:
        for action in actions:
            lines.extend([_line("Action", action.get("action_type")),
                          _line("Target Session", action.get("target_session")),
                          _line("Reason", action.get("reason")),
                          "  Reference Command", f"    {action.get('reference_command')}",
                          _line("Toolkit Executed", "NO"),
                          _line("DBA Approval Required", "YES")])
        risk = data.get("termination_risk") or {}
        lines.extend(["", "Termination Risk",
                      _line("Open Transaction", risk.get("open_transaction")),
                      _line("Rollback Risk", risk.get("rollback_risk")),
                      _line("Rollback Duration", risk.get("rollback_duration")),
                      "", "Safety", "  Toolkit did NOT execute this command.",
                      "  DBA approval is required."])
    else:
        if data.get("required_evidence_status") != "NOT_APPLICABLE":
            lines.append("  No automatic or destructive remediation generated.")
        else:
            lines.append("  No session termination command generated.")
        if data.get("required_evidence_status") == "INCOMPLETE":
            lines.append("  Reason: required evidence is incomplete.")
        elif data.get("required_evidence_status") == "NOT_APPLICABLE" \
                and data.get("data_completeness") != "COMPLETE":
            lines.append("  Reason: evidence incomplete.")
        elif data.get("primary_problem") == "LONG_RUNNING_SQL":
            lines.extend(["  No session termination recommended from current evidence.",
                          "  Review SQL and execution-plan evidence in the detailed report."])
        else:
            lines.append("  No confirmed blocking root eligible for a reference command.")
    if verbose:
        lines.extend(["", "Secondary Signals"])
        lines.extend(_line(label.replace("_", " ").title(), count)
                     for label, count in data.get("secondary_signals", {}).items())
        if data.get("collector_summary"):
            lines.extend(["", "Collector Summary"])
            for item in data["collector_summary"]:
                lines.append(f"  {item.get('collector')}: success={item.get('success')}, "
                             f"truncated={item.get('truncated')}, rows={item.get('row_count')}")
    lines.extend(["", "Detailed Report", f"  {data.get('report_path') or 'Unavailable'}"])
    return "\n".join(lines)


def render_emergency_triage(report: DiagnosticReport, candidates: tuple[dict[str, Any], ...] = (),
                           *, capture_started_at: str, slow_threshold: int = 10) -> str:
    """Render the bounded Chinese first-pass response from current evidence."""
    details = getattr(report, "details", {})
    title = "数据库性能应急诊断" if report.scenario == "slow" else "数据库空间应急诊断"
    candidate_labels = {
        "ROOT_BLOCKER": "根阻塞", "IDLE_OPEN_TRANSACTION": "未结束事务",
        "BLOCKED_VICTIM": "被阻塞会话", "LONG_RUNNING_SQL": "长运行 SQL",
        "ACTIVE_WAIT": "主动等待语句",
    }
    finding_labels = {
        "SQLSERVER_DATAFILE_NEAR_LIMIT": "SQL Server 数据文件接近容量上限",
        "SQLSERVER_TEMPDB_HIGH_USAGE": "SQL Server TempDB 使用率较高",
        "SQLSERVER_LOG_HIGH_USAGE": "SQL Server 事务日志使用率较高",
        "SQLSERVER_LOG_REUSE_BLOCKED": "SQL Server 事务日志复用受阻",
        "SQLSERVER_OLD_ACTIVE_TRANSACTION": "SQL Server 存在长期活动事务",
        "PG_WAL_RETENTION_SLOT": "PostgreSQL 复制槽保留 WAL",
        "PG_ARCHIVE_FAILURE": "PostgreSQL WAL 归档失败线索",
        "PG_LONG_TRANSACTION": "PostgreSQL 存在长期事务",
        "BINLOG_TOTAL_SIZE_HIGH": "MySQL Binlog 总量较大",
        "REPLICA_RELAY_LOG_RISK": "MySQL 复制中继日志风险",
        "LONG_TRANSACTION": "MySQL 存在长期事务",
        "ORACLE_UNDO_HIGH_USAGE": "Oracle Undo 使用率较高",
        "ORACLE_TEMP_HIGH_USAGE": "Oracle 临时空间使用率较高",
        "ORACLE_TABLESPACE_HIGH_USAGE": "Oracle 表空间使用率较高",
        "ORACLE_FRA_HIGH_USAGE": "Oracle FRA 使用率较高",
        "ORACLE_FLASHBACK_HIGH_USAGE": "Oracle Flashback 日志使用率较高",
        "ORACLE_ARCHIVE_DEST_FAILURE": "Oracle 归档目标异常",
        "ORACLE_ARCHIVE_ACCUMULATION": "Oracle 归档量较大",
    }
    version_label = (f"Oracle Profile 路由版本：{details.get('version', 'UNKNOWN')}"
                     "（来源 V$INSTANCE.VERSION；产品发行名称未独立核实）"
                     if report.instance.db_type.value == "oracle" else
                     f"数据库版本：{details.get('version', 'UNKNOWN')}")
    lines = [title, "=" * 52, f"实例：{report.instance.id}",
             version_label, f"采集开始：{capture_started_at}"]
    if report.scenario == "slow":
        lines.append(f"慢 SQL 阈值：{slow_threshold} 秒")
        concurrency = details.get("concurrency", {})
        if isinstance(concurrency, dict):
            count = next((concurrency[key] for key in
                          ("connected_sessions", "total_connections", "total_sessions") if key in concurrency), None)
            blocked = next((concurrency[key] for key in
                            ("blocked_requests", "blocked_sessions", "blocked_statements") if key in concurrency), None)
            tx = next((concurrency[key] for key in
                       ("open_transactions", "active_transactions", "idle_transactions") if key in concurrency), None)
            if count is not None:
                lines.append(f"活动/连接会话：{count}")
            if blocked is not None:
                lines.append(f"阻塞会话/请求：{blocked}")
            if tx is not None:
                lines.append(f"事务相关计数：{tx}")
        lines.extend(("", "【现场初步判断】"))
        if details.get("emergency_evidence_complete") is not True:
            lines.append("应急核心证据不完整；当前结论未知，不能据此判断无阻塞或数据库健康。")
        elif details.get("roots"):
            lines.append(f"观测到 {len(details['roots'])} 个阻塞根候选，请核对会话身份及事务上下文。")
        elif details.get("idle_transactions"):
            lines.append("观测到与未结束事务关联的会话，请核对事务归属和持续时间。")
        else:
            lines.append("本次应急采样未观察到阻塞根或已确认的空闲事务；结论仅适用于当前快照。")
        lines.extend(("", "【优先核查对象】"))
        if not candidates:
            lines.append("当前证据没有可列出的会话候选。")
        for item in candidates:
            category_values = item.get("categories", [item.get("category")])
            category = " / ".join(candidate_labels.get(str(value), "待核查会话")
                                  for value in category_values)
            lines.append(f"{item['rank']}. 会话 {item['session_id']}｜{category}")
            lines.append(f"   原因：{item['reason']}")
            if report.instance.db_type.value == "oracle":
                lines.append(f"   INST_ID / SID / SERIAL#：{item.get('oracle_inst_id') or '未知'} / {str(item['session_id']).split(':')[-1]} / {item.get('oracle_serial_number') or '未知'}")
                lines.append(f"   事务开始时间：{item.get('transaction_start_time') or '未知'}")
            lines.append(f"   用户：{item.get('username') or 'N/A'}；主机：{item.get('host') or 'N/A'}；IP：{item.get('client_ip') or 'N/A'}；应用：{item.get('application') or 'N/A'}；状态：{item.get('state') or 'N/A'}")
            if item.get("mysql_connection_id") is not None:
                lines.append(f"   MySQL 连接 ID：{item['mysql_connection_id']}；Performance Schema THREAD_ID：{item.get('performance_schema_thread_id') or 'N/A'}；InnoDB 事务 ID：{item.get('transaction_id') or 'N/A'}")
            else:
                sql_label = "SQL 标识" + (f"（{item.get('sql_id_type')}）" if item.get("sql_id_type") else "")
                lines.append(f"   {sql_label}：{item.get('sql_id') or 'N/A'}；事务标识：{item.get('transaction_id') or 'N/A'}；运行秒数：{item.get('running_seconds') if item.get('running_seconds') is not None else 'N/A'}")
            lines.append(f"   登录时间：{item.get('login_time') or 'N/A'}；事务时长：{item.get('transaction_age_seconds') if item.get('transaction_age_seconds') is not None else 'N/A'} 秒；影响会话：{item.get('blocked_session_count', 0)}")
            if item.get("mysql_connection_id") is not None:
                root_evidence = next((root for root in details.get("roots", [])
                                      if isinstance(root, dict) and str(root.get("processlist_id"))
                                      == str(item["mysql_connection_id"])), {})
                if root_evidence:
                    lines.append(f"   直接影响：{root_evidence.get('direct_blocked_count', 0)}；间接影响：{root_evidence.get('indirect_blocked_count', 0)}；总影响：{root_evidence.get('blocked_count', 0)}")
            lines.append(f"   身份质量：{item.get('identity_quality', 'UNKNOWN')}；{item.get('identity_reuse_note')}")
        victims = details.get("blocked_victims", [])
        if isinstance(victims, list) and victims:
            lines.extend(("", "【被阻塞会话】"))
            for rank, victim in enumerate(victims, 1):
                if not isinstance(victim, dict):
                    continue
                elapsed = victim.get("duration_ms")
                if elapsed is None and type(victim.get("duration_seconds")) in (int, float):
                    elapsed = float(victim["duration_seconds"]) * 1000
                elapsed_text = f"{float(elapsed) / 1000:g} 秒" \
                    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) else "未知"
                if report.instance.db_type.value == "oracle":
                    victim_name = f"{victim.get('inst_id') or '未知'}:{victim.get('sid') or '未知'}"
                    lines.append(f"{rank}. 会话 {victim_name}；直接阻塞者：{victim.get('direct_blocker_id') or '未知'}；根阻塞者：{victim.get('root_blocker_id') or '未知'}；状态：{victim.get('status') or '未知'}；等待事件：{victim.get('event') or '未知'}")
                else:
                    lines.append(f"{rank}. 会话{victim.get('processlist_id')}；直接阻塞者：{victim.get('direct_blocker_id') or '未知'}；根阻塞者：{victim.get('root_blocker_id') or '未知'}；当前请求时长：{elapsed_text}")
        active_waits = details.get("active_waits", [])
        if isinstance(active_waits, list) and active_waits:
            lines.extend(("", "【主动等待语句】"))
            for wait in active_waits:
                if isinstance(wait, dict):
                    duration = wait.get("duration_ms")
                    duration_text = f"{float(duration) / 1000:g}" \
                        if isinstance(duration, (int, float)) and not isinstance(duration, bool) else "未知"
                    pid = (f"{wait.get('inst_id')}:{wait.get('sid')}"
                           if report.instance.db_type.value == "oracle" else
                           _first(wait, "pid", "processlist_id"))
                    state = _first(wait, "state", "state_name") or "未知"
                    event = _first(wait, "wait_event", "wait_event_type")
                    lines.append(f"PID {pid}；状态：{state}；运行时间：{duration_text} 秒；等待事件：{event or '未知'}；未观察到锁阻塞。")
                    if report.instance.db_type.value == "postgresql" and isinstance(wait.get("sql"), str):
                        safe_sql = " ".join(wait["sql"].split())
                        lines.append(f"   SQL：{safe_sql[:200]}{'…（已截断）' if len(safe_sql) > 200 else ''}")
                        lines.append("   说明：明确的主动等待，不代表数据库资源瓶颈。")
                    elif wait.get("request_interpretation"):
                        lines.append(f"   说明：{wait['request_interpretation']}")
        lines.extend(("", "【处置提示】",
                      "人工操作前重新核对会话 ID、登录时间、用户、主机、应用及事务身份。",
                      "终止可能触发回滚、额外 I/O、日志/Undo 压力和应用错误；终止不保证立即恢复。Toolkit 未执行任何处置命令。"))
        decisions = details.get("manual_action_decisions", [])
        if isinstance(decisions, (list, tuple)) and decisions:
            lines.extend(("", "【人工处置参考】"))
            for decision in decisions:
                if not isinstance(decision, dict):
                    continue
                target = decision.get("target_session")
                root = next((item for item in details.get("roots", [])
                             if isinstance(item, dict) and str(
                                 item.get("session_key") if report.instance.db_type.value == "oracle"
                                 else _first(item, "session_id", "pid", "processlist_id", "sid"))
                             == str(target)), {})
                blocked = _first(root, "blocked_count", "blocked_session_count")
                status = _first(root, "status", "command_name", "state", "state_name")
                transaction = (root.get("unfinished_transaction_confirmed") is True
                               or _first(root, "trx_id", "transaction_id") not in (None, ""))
                lines.append(f"根阻塞会话：{target if target is not None else 'UNKNOWN'}")
                if report.instance.db_type.value == "oracle":
                    lines.append(f"INST_ID / SID / SERIAL#：{root.get('inst_id') or '未知'} / {root.get('sid') or '未知'} / {root.get('serial_number') or '未知'}")
                lines.append(f"影响会话：{blocked if blocked is not None else 0}")
                if root.get("direct_blocked_count") is not None:
                    lines.append(f"直接影响：{root['direct_blocked_count']}；间接影响：{root.get('indirect_blocked_count', 0)}；总影响：{blocked or 0}")
                lines.append(f"状态：{status or 'UNKNOWN'}")
                transaction_text = ("是" if transaction else
                                    "否" if root.get("unfinished_transaction_confirmed") is False else
                                    "未确认")
                transaction_prefix = "采样时未结束事务" if report.instance.db_type.value == "postgresql" else "未结束事务"
                lines.append(f"{transaction_prefix}：{transaction_text}")
                if report.instance.db_type.value == "oracle":
                    if root.get("transaction_start_time") is not None:
                        lines.append(f"事务开始时间：{root['transaction_start_time']}")
                    lines.append(f"事务身份质量：{root.get('transaction_identity_quality') or 'UNKNOWN'}")
                elif report.instance.db_type.value == "postgresql":
                    lines.append(f"backend_start：{root.get('backend_start') or '未采集'}；xact_start：{root.get('xact_start') or '未采集'}")
                if _first(root, "transaction_id", "trx_id") is not None:
                    if report.instance.db_type.value == "mysql":
                        lines.append(f"InnoDB 事务 ID：{_first(root, 'transaction_id', 'trx_id')}")
                        lines.append(f"MySQL 连接 ID：{root.get('processlist_id', target)}；Performance Schema THREAD_ID：{root.get('thread_id') or 'N/A'}")
                    else:
                        lines.append(f"事务标识：{_first(root, 'transaction_id', 'trx_id')}")
                identity_quality = _first(root, "identity_quality", "transaction_identity_quality") or "需要人工重新核对"
                lines.append(f"身份状态：{identity_quality}")
                if decision.get("status") == "AVAILABLE":
                    lines.extend(("建议：", "1. 重新核对当前会话身份及未结束事务。",
                                  "2. 确认是否属于需要中断的业务。",
                                  "3. 评估回滚和日志压力。",
                                  "4. DBA 确认后自行决定是否终止。",
                                  "参考 SQL（仅供人工复制，不会自动执行）："))
                    lines.extend(str(command) for command in decision.get("commands", ()))
                else:
                    lines.extend(("具体命令：暂不生成",
                                  f"原因：{decision.get('reason') or '关键身份字段不足'}",
                                  "需要核查：当前会话 ID、登录身份及事务归属。"))
                    if report.instance.db_type.value == "postgresql":
                        lines.append("PostgreSQL 复核：查询 pg_stat_activity，核对 PID、backend_start、xact_start、用户和客户端来源。")
                    elif report.instance.db_type.value == "oracle":
                        lines.append("Oracle 复核：核对当前 INST_ID/SID/SERIAL#/LOGON_TIME、USERNAME、MACHINE、PROGRAM、事务 XID 和开始时间，并确认 RAC 实例范围。")
                        manual_action = root.get("manual_action", {})
                        identity = manual_action.get("identity_evidence", {}) \
                            if isinstance(manual_action, dict) else {}
                        target_template = ("<经实时复核的 SID,SERIAL#,@INST_ID>"
                                           if isinstance(identity, dict)
                                           and identity.get("rac_enabled") else
                                           "<经实时复核的 SID,SERIAL#>")
                        lines.append(f"参考模板：ALTER SYSTEM KILL SESSION '{target_template}' IMMEDIATE;")
                    else:
                        lines.append("参考模板：KILL <经实时复核的会话ID>")
                lines.extend(("命令基于历史采样；Toolkit 未实时锁定会话身份；会话标识可能复用。",
                              "执行可能触发回滚且不保证立即结束。"))
    else:
        findings = [finding_labels.get(finding.title, "未分类空间或日志线索")
                    for finding in report.findings[:5]]
        category_names = {"TRANSACTION_LOG": "事务日志", "WAL": "WAL", "BINLOG": "Binlog",
                          "REDO": "Redo", "ARCHIVE": "归档", "FRA": "FRA", "UNDO": "Undo",
                          "TEMP": "临时空间", "DATA": "数据文件", "TABLE": "表", "INDEX": "索引"}
        space = details.get("space", [])
        categories = sorted({category_names.get(str(row.get("category")), str(row.get("category")))
                             for row in space if isinstance(row, dict) and row.get("category")}) \
            if isinstance(space, list) else []
        lines.extend(("", "【当前主要线索】",
                      "；".join(findings) if findings else
                      "本次未观察到已确认空间风险。" if details.get("emergency_evidence_complete") else
                      "应急必需空间证据不完整，当前结论未知。",
                      f"已观测资源类别：{'、'.join(categories) if categories else 'N/A'}",
                      f"空间记录：{len(space) if isinstance(space, list) else 0} 项",
                      "增长率：无法确定（本次为当前状态采样，无历史快照）。",
                      "当前证据可提示保留因素，但不能单独证明具体会话导致空间增长。",
                      "不得未经人工验证删除复制槽、日志文件或修改数据库配置。Toolkit 未执行任何处置命令。"))
        log = details.get("log", {})
        if isinstance(log, dict) and log:
            reason = log.get("log_reuse_wait_desc", log.get("archive_mode", log.get("last_failed_time")))
            if reason is not None:
                lines.insert(-4, f"日志/归档保留线索：{reason}")
        other = details.get("other", {})
        if isinstance(other, dict):
            slots = other.get("slots")
            if isinstance(slots, list) and slots:
                lines.insert(-4, f"复制槽记录：{len(slots)} 项；需人工确认用途和下游消费状态。")
    pending = bool(details.get("supplemental_pending"))
    lines.extend(("", "【证据状态】",
                  f"应急核心证据：{'完整' if details.get('emergency_evidence_complete') else '不完整 / 未知'}",
                  "补充证据：待采集" if pending else "补充证据：无额外查询",
                  "采样为串行 BEST-EFFORT 快照，不是原子快照。", "=" * 52,
                  "正在采集补充证据…" if pending else "无额外补充查询，正在生成 HTML 报告…"))
    return "\n".join(lines)


def render_replication_summary(report: Any, report_path: str, verbose: bool = False) -> str:
    """Render a compact DBA-facing replication incident panel."""
    data = asdict(report) if hasattr(report, "__dataclass_fields__") else report
    summary = data.get("summary", {})
    members = data.get("members", [])
    lags = data.get("lag", [])
    issues = data.get("issues", [])
    actions = data.get("recommendations", [])
    candidate = next((item for item in issues if item.get("candidate", True)), None)
    lines = ["Replication Health", "-" * 42,
             _line("Snapshot", summary.get("snapshot_time")),
             _line("Database", summary.get("database_id")),
             _line("Platform", summary.get("platform")),
             _line("Version", summary.get("version")),
             _line("Local Role", summary.get("local_role")),
             _line("Vendor Role", summary.get("vendor_role")),
             _line("Replication Mode", summary.get("replication_mode")),
             _line("Overall", summary.get("overall")),
             _line("Status", summary.get("status")),
             _line("Data Completeness", summary.get("data_completeness")),
             _line("Transport", summary.get("transport_status")),
             _line("Apply / Replay", summary.get("apply_status")),
             _line("Members", len(members)), _line("Issues", len(issues)), "", "Lag / Backlog"]
    lines.extend(["", "Root Cause Candidate",
                  f"  {candidate.get('code')}: {candidate.get('message')}" if candidate else
                  "  No fault candidate in the available snapshot.",
                  "  Candidate only; validate against database and application evidence."])
    for item in lags[:6 if not verbose else 15]:
        lines.append(f"  {item.get('kind', 'lag')}: {item.get('value')} {item.get('unit')} "
                     f"[{item.get('severity', 'UNKNOWN')}] — {item.get('source', 'source unknown')}")
    if not lags:
        lines.append("  Not applicable because replication is not configured."
                     if summary.get("status") == "NOT_CONFIGURED"
                     else "  No lag metric was returned.")
    lines.extend(["", "Issues"])
    for issue in issues[:5 if not verbose else 15]:
        lines.append(f"  {issue.get('severity', 'UNKNOWN')} {issue.get('code', 'UNKNOWN')}: "
                     f"{redact(issue.get('message', ''))}")
        if issue.get("vendor_message"):
            lines.append(f"    Vendor: {redact(issue['vendor_message'])}")
    if not issues:
        lines.append("  No replication issue was observed in this snapshot.")
    lines.extend(["", "Recommended DBA Review"])
    for action in actions[:3 if not verbose else 10]:
        lines.append(f"  - {redact(action.get('message', ''))}")
    if not actions:
        lines.append("  No additional action is indicated by this snapshot.")
    lines.extend(["", "Toolkit Executed  NO", "Reference Commands  None",
                  f"HTML Report  {report_path}"])
    permission = data.get("details", {}).get("permission_readiness", {})
    if permission:
        lines.extend(["", "Permission Readiness",
                      _line("Account Security", (permission.get("account_security") or {}).get("classification")),
                      _line("Feature", "replication"),
                      _line("Status", permission.get("status"))])
        if permission.get("missing_required"):
            lines.append(_line("Missing Required", ", ".join(permission["missing_required"])))
        if permission.get("missing_optional"):
            lines.append(_line("Missing Optional", ", ".join(permission["missing_optional"])))
        if permission.get("suggested_grant"):
            lines.extend(["Reference Grant Commands", permission["suggested_grant"],
                          "Toolkit Executed  NO", "DBA Approval Required  YES"])
    if verbose:
        lines.extend(["", "Topology Members"])
        for member in members[:15]:
            lines.append(f"  {member.get('name')}: {member.get('role')} / {member.get('status')} "
                         f"({member.get('vendor_status')})")
    return "\n".join(lines)


def _render_error(data: dict[str, Any]) -> str:
    kind = str(data.get("error_type", "Operation Failed"))
    title = {
        "CredentialNotFound": "Credential Not Found",
        "CredentialStoreUnavailable": "Credential Store Error",
        "CredentialStoreFailed": "Credential Store Error",
        "DriverUnavailable": "Database Runtime Missing",
        "PrivilegedAccount": "Monitoring Account Rejected",
        "MissingPermission": "Permission Missing",
        "ConnectionFailed": "Database Connection Failed",
    }.get(kind, kind.replace("Error", " Failed"))
    lines = [title, "-" * 42]
    for key, label in (("database_id", "Database"), ("account", "Account"),
                       ("target", "Target"),
                       ("driver", "Driver"), ("backend", "Backend"),
                       ("tls", "TLS"), ("alias", "Alias"), ("feature", "Feature"),
                       ("error_message", "Reason")):
        if data.get(key) is not None:
            lines.extend(["", label, f"  {data[key]}"])
    vendor = data.get("error")
    if isinstance(vendor, dict):
        lines.extend(["", "Vendor Error"])
        if vendor.get("vendor_code") is not None:
            lines.append(f"  Code      {vendor['vendor_code']}")
        if vendor.get("sqlstate") is not None:
            lines.append(f"  SQLSTATE  {vendor['sqlstate']}")
        if vendor.get("message"):
            lines.append(f"  Message   {vendor['message']}")
        lines.extend(["", "Toolkit Classification", f"  {vendor.get('classification', 'UNKNOWN')}"])
        for label, key in (("Detail", "detail"), ("Hint", "hint"), ("Context", "context")):
            if vendor.get(key):
                lines.extend(["", label, f"  {vendor[key]}"])
        if vendor.get("guidance"):
            lines.extend(["", "Toolkit Guidance", f"  {vendor['guidance']}"])
    effective = data.get("effective_connection")
    if isinstance(effective, dict):
        lines.extend(["", "Effective Connection"])
        lines.extend(_line(label, effective.get(key)) for key, label in (
            ("host", "Host"), ("port", "Port"), ("database", "Database"),
            ("ssl", "SSL"), ("connect_timeout", "Connect Timeout")))
    missing = data.get("permission_missing")
    readiness = data.get("permission_readiness")
    if isinstance(readiness, dict):
        security = readiness.get("account_security", {})
        lines.extend(["", "Account Security",
                      _line("Classification", security.get("classification")),
                      "", "Permission Readiness",
                      _line("Feature", readiness.get("feature")),
                      _line("Status", getattr(readiness.get("status"), "value", readiness.get("status")))])
        for key, label in (("missing_required", "Missing Required"),
                           ("missing_optional", "Missing Optional"),
                           ("available", "Available Capabilities")):
            values = readiness.get(key, ())
            if values:
                lines.extend(["", label, "  " + ", ".join(str(item) for item in values)])
        if readiness.get("impact"):
            lines.extend(["", "Impact", f"  {readiness['impact']}"])
        for item in readiness.get("missing_reasons", ()):
            lines.extend(["", str(item.get("privilege", "Privilege")),
                          f"  Required: {item.get('reason', '')}"])
        if readiness.get("suggested_grant"):
            lines.extend(["", "Reference Grant Commands", readiness["suggested_grant"],
                          "Toolkit Executed  NO", "DBA Approval Required  YES"])
    if isinstance(missing, dict):
        lines.extend(["", "Missing Permission", f"  {', '.join(missing.get('missing_privileges', [])) or 'See evidence'}",
                      "", "Impact", f"  {missing.get('impact', '')}"])
        grant = missing.get("suggested_grant")
        if grant:
            lines.extend(["", "Suggested Grant", f"  {grant}",
                          "  DBA Toolkit did NOT execute this statement."])
    reason = data.get("trigger_reason")
    if isinstance(reason, dict):
        lines.extend(["", "Privilege Trigger"])
        lines.extend(_line(label, reason.get(key)) for key, label in
                     (("name", "Permission"), ("source", "Source"), ("principal", "Principal"),
                      ("scope", "Scope"), ("object", "Object")))
    if kind == "CredentialNotFound":
        lines.extend(["", "Fix", "  dba credentials set <credential_alias>"])
    elif kind in {"CredentialStoreUnavailable", "CredentialStoreFailed"}:
        lines.extend(["", "Suggested Steps", "  dba credentials test <credential_alias>",
                      "  dba credentials set <credential_alias>"])
    elif kind == "PrivilegedAccount":
        lines.extend(["", "Action", "  Review and reduce the monitoring account grants.",
                      "  Toolkit will not use privileged monitoring accounts."])
    elif kind in {"ConnectionFailed", "ConnectionTLSFailed", "ConnectionTimeout"}:
        lines.extend(["", "What To Check", "  1. Verify the stored monitoring credential.",
                      "  2. Verify the SQL login and approved authentication mode.",
                      "  3. Confirm the target, firewall, driver and certificate settings."])
        if data.get("alias"):
            lines.extend(["", "Credential", f"  dba credentials show {data['alias']}"])
    return "\n".join(lines)


def _render_generic(command: str, data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)
    if command == "collect":
        return "\n".join(["Collection Result", "-" * 42,
                          _line("Collector", data.get("collector_name")),
                          _line("Status", "READY" if data.get("success") else "FAILED"),
                          _line("Rows", data.get("row_count")),
                          _line("Truncated", data.get("truncated")),
                          _line("Elapsed", data.get("elapsed_ms"))])
    if command == "diagnose":
        report = data.get("report", {})
        details = report.get("details", {}) if isinstance(report, dict) else {}
        report_instance = report.get("instance", {}) if isinstance(report, dict) else {}
        return "\n".join(["Diagnosis", "-" * 42,
                          _line("Database", report_instance.get("id")
                                if isinstance(report_instance, dict) else None),
                          _line("Result", "READY" if data.get("success") else "PARTIAL"),
                          _line("Incomplete", details.get("incomplete")),
                          _line("Findings", len(report.get("findings", [])) if isinstance(report, dict) else 0)])
    return json.dumps(redact(data), ensure_ascii=False, indent=2, default=str)

