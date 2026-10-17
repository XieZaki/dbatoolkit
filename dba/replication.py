"""Unified observational replication and HA diagnosis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport, Finding, Severity
from dba.permissions import (
    PermissionStatus,
    assess_permissions,
    privilege_reasons,
    render_sql,
    requirements_for,
)
from dba.replication_collectors import ReplicationEvidence, collect_replication_evidence


@dataclass(frozen=True)
class ReplicationPolicy:
    lag_warning_seconds: int = 30
    lag_high_seconds: int = 300
    lag_critical_seconds: int = 1800
    retained_wal_warning_bytes: int = 1_073_741_824
    relay_backlog_warning_bytes: int = 1_073_741_824
    max_members: int = 50
    max_issues: int = 50


DEFAULT_REPLICATION_POLICY = ReplicationPolicy()


@dataclass(frozen=True)
class ReplicationMember:
    name: str
    local: bool | None
    role: str
    vendor_role: str | None
    status: str
    vendor_status: str | None
    transport_status: str | None = None
    apply_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplicationLag:
    kind: str
    value: int | float | str
    unit: str
    severity: str
    source: str
    note: str | None = None


@dataclass(frozen=True)
class ReplicationIssue:
    code: str
    severity: str
    message: str
    candidate: bool = True
    vendor_message: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplicationAction:
    message: str
    requires_dba_review: bool = True


@dataclass(frozen=True)
class ReplicationSummary:
    snapshot_time: str
    database_id: str
    platform: str
    local_role: str
    vendor_role: str | None
    replication_mode: str
    status: str
    overall: str
    data_completeness: str
    transport_status: str
    apply_status: str
    note: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class ReplicationReport(DiagnosticReport):
    summary: ReplicationSummary = field(default_factory=lambda: ReplicationSummary(
        "", "", "", "UNKNOWN", None, "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN",
        "UNKNOWN", "UNKNOWN"))
    members: tuple[ReplicationMember, ...] = ()
    lag: tuple[ReplicationLag, ...] = ()
    backlog: dict[str, Any] = field(default_factory=dict)
    issues: tuple[ReplicationIssue, ...] = ()
    recommendations: tuple[ReplicationAction, ...] = ()
    reference_commands: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def _value(row: dict[str, Any], *keys: str) -> Any:
    normalized = {str(key).casefold(): value for key, value in row.items()}
    for key in keys:
        if normalized.get(key.casefold()) is not None:
            return normalized[key.casefold()]
    return None


def _rows(evidence: ReplicationEvidence, name: str) -> tuple[dict[str, Any], ...]:
    sample = next((item for item in evidence.samples if item.collector_name == name), None)
    return sample.rows if sample and sample.success else ()


def _sample_ok(evidence: ReplicationEvidence, *names: str) -> bool:
    return any(item.collector_name in names and item.success and not item.truncated
               for item in evidence.samples)


def _sample_payload(evidence: ReplicationEvidence) -> list[dict[str, Any]]:
    return [{"collector": row.collector_name, "success": row.success,
             "row_count": row.row_count, "truncated": row.truncated,
             "error_type": row.error_type, "error_message": row.error_message}
            for row in evidence.samples]


def _bool(value: Any) -> bool | None:
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        if value.casefold() in {"yes", "on", "true", "1", "running"}:
            return True
        if value.casefold() in {"no", "off", "false", "0", "stopped"}:
            return False
    return None


def _integer(value: Any) -> int | None:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _lag_severity(seconds: int, policy: ReplicationPolicy) -> str:
    if seconds >= policy.lag_critical_seconds:
        return "CRITICAL"
    if seconds >= policy.lag_high_seconds:
        return "HIGH"
    if seconds >= policy.lag_warning_seconds:
        return "WARNING"
    return "INFO"


def _time_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\+?(\d+)\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?", value.strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(part) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _issue(code: str, severity: str, message: str, *, vendor_message: Any = None,
           evidence: dict[str, Any] | None = None, candidate: bool = True) -> ReplicationIssue:
    return ReplicationIssue(code, severity, message, candidate,
                            str(vendor_message) if vendor_message else None, evidence or {})


def _mysql(evidence: ReplicationEvidence, policy: ReplicationPolicy) -> dict[str, Any]:
    server = next(iter(_rows(evidence, "replication_server_info")), {})
    channels = list(_rows(evidence, "replication_status")) or list(_rows(evidence, "replication_status_legacy"))
    read_only = _bool(_value(server, "read_only"))
    status_query_ok = _sample_ok(evidence, "replication_status", "replication_status_legacy")
    role = "REPLICA" if channels else "PRIMARY" if read_only is False else "UNKNOWN"
    members: list[ReplicationMember] = []
    lag: list[ReplicationLag] = []
    issues: list[ReplicationIssue] = []
    backlog: dict[str, Any] = {}
    transport = apply = "NOT_CONFIGURED" if not channels and status_query_ok else "UNKNOWN"
    if not channels and status_query_ok:
        if role == "UNKNOWN":
            issues.append(_issue("ROLE_UNKNOWN", "WARNING", "No replication channel was returned and local read-only state is unavailable."))
        return {"role": role, "vendor_role": "REPLICA" if channels else "SOURCE_OR_STANDALONE",
                "mode": "MySQL asynchronous replication", "members": members, "lag": lag,
                "issues": issues, "backlog": backlog, "transport": transport, "apply": apply,
                "not_configured": True}
    if not channels:
        return {"role": role if status_query_ok else "UNKNOWN", "vendor_role": None,
                "mode": "MySQL asynchronous replication", "members": members, "lag": lag,
                "issues": issues, "backlog": backlog, "transport": transport, "apply": apply,
                "not_configured": False}
    for index, row in enumerate(channels, 1):
        channel = str(_value(row, "Channel_Name") or f"channel-{index}")
        io = str(_value(row, "Replica_IO_Running", "Slave_IO_Running") or "UNKNOWN")
        sql = str(_value(row, "Replica_SQL_Running", "Slave_SQL_Running") or "UNKNOWN")
        io_ok, sql_ok = _bool(io), _bool(sql)
        source = _value(row, "Source_Host", "Master_Host")
        transport = "RUNNING" if io_ok else "DISCONNECTED" if io.casefold() == "connecting" else "STOPPED" if io_ok is False else "UNKNOWN"
        apply = "RUNNING" if sql_ok else "STOPPED" if sql_ok is False else "UNKNOWN"
        members.append(ReplicationMember(str(source or channel), False, "REPLICA", "REPLICA",
                                         "RUNNING" if io_ok and sql_ok else "DEGRADED",
                                         f"IO={io}; SQL={sql}", transport, apply,
                                         {"channel": channel, "source_port": _value(row, "Source_Port", "Master_Port")}))
        if io_ok is False:
            error = _value(row, "Last_IO_Error")
            code = "SOURCE_CONNECTION_ERROR" if error else "IO_THREAD_STOPPED"
            issues.append(_issue(code, "HIGH", "Replication receiver thread is not running.",
                                 vendor_message=error, evidence={"channel": channel, "io_state": io}))
        if sql_ok is False:
            error = _value(row, "Last_SQL_Error")
            issues.append(_issue("SQL_APPLY_ERROR" if error else "SQL_THREAD_STOPPED", "CRITICAL",
                                 "Replication applier thread is not running.", vendor_message=error,
                                 evidence={"channel": channel, "sql_state": sql}))
        seconds = _integer(_value(row, "Seconds_Behind_Source", "Seconds_Behind_Master"))
        configured_delay = _integer(_value(row, "SQL_Delay")) or 0
        if seconds is not None:
            severity = _lag_severity(seconds, policy)
            lag.append(ReplicationLag("time", seconds, "seconds", severity,
                                      "Seconds_Behind_Source",
                                      "Receiver/applier indicator; NULL is interpreted with thread state."))
            if seconds >= policy.lag_warning_seconds and configured_delay < seconds:
                issues.append(_issue("REPLICATION_LAG", severity,
                                     "Observed source-to-applier time lag exceeds the configured delay."))
        elif not io_ok or not sql_ok:
            issues.append(_issue("LAG_UNKNOWN", "WARNING",
                                 "Seconds_Behind_Source is NULL while a replication thread is stopped or disconnected."))
        if configured_delay:
            lag.append(ReplicationLag("configured_delay", configured_delay, "seconds", "INFO", "SQL_Delay",
                                      "Configured intentional delay; excluded from unexpected-lag findings."))
        relay_space = _integer(_value(row, "Relay_Log_Space"))
        if relay_space is not None:
            backlog[f"{channel}:relay_log_space_bytes"] = relay_space
            if relay_space >= policy.relay_backlog_warning_bytes:
                issues.append(_issue("RELAY_LOG_BACKLOG", "WARNING",
                                     "Observed relay-log storage is large; one snapshot cannot infer growth trend.",
                                     evidence={"channel": channel, "relay_log_space_bytes": relay_space}))
        for column, label in (("Retrieved_Gtid_Set", "retrieved_gtid_set"),
                              ("Executed_Gtid_Set", "executed_gtid_set")):
            value = _value(row, column)
            if value:
                backlog[f"{channel}:{label}"] = value
        for column, code, label in (("Last_IO_Error", "SOURCE_CONNECTION_ERROR", "receiver"),
                                    ("Last_SQL_Error", "SQL_APPLY_ERROR", "applier")):
            error = _value(row, column)
            if error and not any(item.code == code and item.evidence.get("channel") == channel for item in issues):
                issues.append(_issue(code, "HIGH" if label == "receiver" else "CRITICAL",
                                     f"MySQL {label} reports a vendor error.", vendor_message=error,
                                     evidence={"channel": channel}))
    return {"role": "REPLICA", "vendor_role": "REPLICA", "mode": "MySQL asynchronous replication",
            "members": members, "lag": lag, "issues": issues, "backlog": backlog,
            "transport": transport, "apply": apply, "not_configured": False}


def _postgresql(evidence: ReplicationEvidence, policy: ReplicationPolicy) -> dict[str, Any]:
    node = next(iter(_rows(evidence, "replication_node")), {})
    if not node:
        return {"role": "UNKNOWN", "vendor_role": None, "mode": "Physical streaming replication",
                "members": [], "lag": [], "issues": [], "backlog": {}, "transport": "UNKNOWN",
                "apply": "UNKNOWN", "not_configured": False}
    standby = _bool(_value(node, "in_recovery"))
    role = "STANDBY" if standby else "PRIMARY" if standby is False else "UNKNOWN"
    members: list[ReplicationMember] = []
    lag: list[ReplicationLag] = []
    issues: list[ReplicationIssue] = []
    backlog: dict[str, Any] = {}
    peers = list(_rows(evidence, "replication_members"))
    receiver = next(iter(_rows(evidence, "replication_receiver")), {})
    slots = list(_rows(evidence, "replication_slots"))
    wal_level = str(_value(node, "wal_level") or "unknown").lower()
    max_senders = _integer(_value(node, "max_wal_senders"))
    not_configured = bool(standby is False and not peers and not slots
                          and _sample_ok(evidence, "replication_members")
                          and _sample_ok(evidence, "replication_slots")
                          and (wal_level == "minimal" or max_senders == 0))
    if standby:
        if receiver:
            status = str(_value(receiver, "status") or "UNKNOWN")
            transport = "RUNNING" if status.upper() == "STREAMING" else "CATCHING_UP" if status else "UNKNOWN"
            members.append(ReplicationMember(str(_value(receiver, "sender_host") or "upstream"), False,
                                             "PRIMARY", "WAL sender", transport, status, transport,
                                             "REPLAYING", {"sender_port": _value(receiver, "sender_port"),
                                                            "slot_name": _value(receiver, "slot_name")}))
        elif _sample_ok(evidence, "replication_receiver"):
            transport = "DISCONNECTED"
            issues.append(_issue("DISCONNECTED_REPLICA", "HIGH",
                                 "Standby recovery is active but no WAL receiver is visible."))
        else:
            transport = "UNKNOWN"
        receive_replay = _integer(_value(node, "receive_replay_bytes"))
        if receive_replay is not None:
            lag.append(ReplicationLag("replay", receive_replay, "bytes", "INFO",
                                      "pg_wal_lsn_diff(receive_lsn, replay_lsn)"))
            backlog["receive_replay_bytes"] = receive_replay
        replay_time = _value(node, "last_replay_timestamp")
        if replay_time is not None:
            lag.append(ReplicationLag("replay_timestamp_age", str(replay_time), "timestamp", "INFO",
                                      "pg_last_xact_replay_timestamp",
                                      "An idle primary may make timestamp age look high; evaluate with WAL receiver and LSN evidence."))
        apply = "RUNNING" if receiver else "UNKNOWN"
    else:
        transport = "RUNNING" if peers else "NOT_CONFIGURED" if not_configured else "UNKNOWN"
        apply = "RUNNING" if peers else "NOT_CONFIGURED" if not_configured else "UNKNOWN"
        for row in peers:
            state = str(_value(row, "state") or "UNKNOWN")
            name = str(_value(row, "application_name") or _value(row, "client_addr") or "standby")
            members.append(ReplicationMember(name, False, "STANDBY", state,
                                             "RUNNING" if state == "streaming" else "CATCHING_UP",
                                             state, state.upper(), state.upper(), row))
            for column, kind, unit in (("write_lag_bytes", "write", "bytes"),
                                       ("flush_lag_bytes", "flush", "bytes"),
                                       ("replay_lag_bytes", "replay", "bytes")):
                amount = _integer(_value(row, column))
                if amount is not None:
                    lag.append(ReplicationLag(kind, amount, unit, "INFO", "pg_wal_lsn_diff"))
            for column, kind in (("write_lag", "write_time"), ("flush_lag", "flush_time"),
                                 ("replay_lag", "replay_time")):
                value = _value(row, column)
                if value is not None:
                    lag.append(ReplicationLag(kind, str(value), "interval", "INFO", column,
                                              "Server-reported interval; NULL means no sample."))
        if not peers and not not_configured:
            inactive = [row for row in slots if _bool(_value(row, "active")) is False]
            issues.append(_issue("NO_CONNECTED_STANDBY", "WARNING",
                                 "WAL sender capacity is enabled but no standby is connected; configuration cannot be confirmed from this snapshot.",
                                 evidence={"inactive_slot_count": len(inactive)}))
    for slot in slots:
        name = str(_value(slot, "slot_name") or "unknown")
        active = _bool(_value(slot, "active"))
        slot_type = str(_value(slot, "slot_type") or "unknown")
        retained = _integer(_value(slot, "retained_wal_bytes"))
        backlog[f"slot:{name}:retained_wal_bytes"] = retained
        if slot_type == "physical" and active is False:
            issues.append(_issue("INACTIVE_PHYSICAL_SLOT", "WARNING",
                                 "An inactive physical slot may retain WAL without a connected consumer.",
                                 evidence={"slot_name": name, "retained_wal_bytes": retained,
                                           "wal_status": _value(slot, "wal_status")}))
        status = str(_value(slot, "wal_status") or "").lower()
        if status in {"unreserved", "lost"}:
            issues.append(_issue("WAL_RETENTION", "CRITICAL" if status == "lost" else "HIGH",
                                 "Replication slot reports WAL that is at risk of removal or is no longer available.",
                                 evidence={"slot_name": name, "wal_status": status}))
        elif retained is not None and retained >= policy.retained_wal_warning_bytes:
            issues.append(_issue("LARGE_RETAINED_WAL", "WARNING",
                                 "Observed WAL retained by a slot exceeds the configured policy threshold; trend is unknown.",
                                 evidence={"slot_name": name, "retained_wal_bytes": retained}))
    return {"role": role, "vendor_role": "IN_RECOVERY" if standby else "PRIMARY" if standby is False else None,
            "mode": "PostgreSQL physical streaming replication", "members": members, "lag": lag,
            "issues": issues, "backlog": backlog, "transport": transport, "apply": apply,
            "not_configured": not_configured}


def _sqlserver(evidence: ReplicationEvidence) -> dict[str, Any]:
    rows = list(_rows(evidence, "replication_ag"))
    members: list[ReplicationMember] = []
    issues: list[ReplicationIssue] = []
    lag: list[ReplicationLag] = []
    backlog: dict[str, Any] = {}
    local = next((row for row in rows if _bool(_value(row, "is_local"))), {})
    role_value = str(_value(local, "role_desc") or "UNKNOWN").upper()
    role = "PRIMARY" if role_value == "PRIMARY" else "SECONDARY" if role_value == "SECONDARY" else "UNKNOWN"
    if not rows and _sample_ok(evidence, "replication_ag"):
        return {"role": "UNKNOWN", "vendor_role": None, "mode": "Availability Groups",
                "members": members, "lag": lag, "issues": issues, "backlog": backlog,
                "transport": "NOT_CONFIGURED", "apply": "NOT_CONFIGURED", "not_configured": True}
    if not rows:
        return {"role": "UNKNOWN", "vendor_role": None, "mode": "Availability Groups",
                "members": members, "lag": lag, "issues": issues, "backlog": backlog,
                "transport": "UNKNOWN", "apply": "UNKNOWN", "not_configured": False}
    for row in rows:
        replica = str(_value(row, "replica_server_name") or "unknown replica")
        sync = str(_value(row, "synchronization_state_desc") or "UNKNOWN")
        connected = str(_value(row, "connected_state_desc") or "UNKNOWN")
        operational = str(_value(row, "operational_state_desc") or "UNKNOWN")
        health = str(_value(row, "database_synchronization_health", "replica_synchronization_health") or "UNKNOWN")
        local_flag = _bool(_value(row, "is_local"))
        members.append(ReplicationMember(replica, local_flag,
                                         "PRIMARY" if str(_value(row, "role_desc") or "").upper() == "PRIMARY"
                                         else "SECONDARY" if str(_value(row, "role_desc") or "").upper() == "SECONDARY" else "UNKNOWN",
                                         str(_value(row, "role_desc") or "UNKNOWN"),
                                         "SYNCHRONIZED" if sync == "SYNCHRONIZED" else "SYNCHRONIZING" if sync == "SYNCHRONIZING" else "DEGRADED",
                                         f"{operational}; {connected}; {sync}; {health}", connected,
                                         sync, {"availability_group": _value(row, "ag_name"),
                                                "database": _value(row, "database_name"),
                                                "availability_mode": _value(row, "availability_mode_desc"),
                                                "failover_mode": _value(row, "failover_mode_desc")}))
        for column, label in (("log_send_queue_size", "log_send_queue_kb"),
                              ("redo_queue_size", "redo_queue_kb")):
            amount = _integer(_value(row, column))
            if amount is not None:
                backlog[f"{replica}:{_value(row, 'database_name')}:{label}"] = amount
                lag.append(ReplicationLag(label, amount, "KB", "INFO", column))
        for queue_col, rate_col, label in (("log_send_queue_size", "log_send_rate", "log_send_catchup"),
                                           ("redo_queue_size", "redo_rate", "redo_catchup")):
            queue, rate = _integer(_value(row, queue_col)), _integer(_value(row, rate_col))
            if queue is not None and rate is not None and rate > 0:
                lag.append(ReplicationLag(label, round(queue / rate, 1), "estimated_seconds", "INFO",
                                          f"{queue_col}/{rate_col}", "ESTIMATED from the current reported KB/s rate."))
        if connected.upper() == "DISCONNECTED":
            issues.append(_issue("DISCONNECTED_REPLICA", "HIGH",
                                 "Availability Group replica is disconnected.", evidence={"replica": replica}))
        if sync in {"NOT SYNCHRONIZING", "NOT_SYNCHRONIZING"} or health in {"NOT_HEALTHY", "NOT HEALTHY"}:
            issues.append(_issue("REPLICATION_STOPPED", "CRITICAL",
                                 "Availability Group database is not synchronizing.",
                                 evidence={"replica": replica, "database": _value(row, "database_name"),
                                           "synchronization_state": sync, "health": health}))
    transport = "DISCONNECTED" if any(i.code == "DISCONNECTED_REPLICA" for i in issues) else "RUNNING"
    apply = "STOPPED" if any(i.code == "REPLICATION_STOPPED" for i in issues) else "SYNCHRONIZED"
    return {"role": role, "vendor_role": role_value, "mode": "Always On Availability Groups",
            "members": members, "lag": lag, "issues": issues, "backlog": backlog,
            "transport": transport, "apply": apply, "not_configured": False}


def _oracle(evidence: ReplicationEvidence, policy: ReplicationPolicy) -> dict[str, Any]:
    database = next(iter(_rows(evidence, "replication_database")), {})
    role_value = str(_value(database, "database_role") or "UNKNOWN").upper()
    role = ("PRIMARY" if role_value == "PRIMARY" else "STANDBY" if "STANDBY" in role_value
            else "UNKNOWN")
    metrics = list(_rows(evidence, "replication_metrics"))
    processes = list(_rows(evidence, "replication_processes"))
    destinations = list(_rows(evidence, "replication_destinations"))
    members: list[ReplicationMember] = []
    issues: list[ReplicationIssue] = []
    lag: list[ReplicationLag] = []
    backlog: dict[str, Any] = {}
    for row in metrics:
        name = str(_value(row, "name") or "").lower()
        value = _value(row, "value")
        seconds = _time_seconds(value)
        if name in {"transport lag", "apply lag"} and seconds is not None:
            lag.append(ReplicationLag(name.replace(" ", "_"), seconds, "seconds",
                                      _lag_severity(seconds, policy),
                                      "V$DATAGUARD_STATS", str(_value(row, "time_computed") or "")))
            if seconds >= policy.lag_warning_seconds:
                issues.append(_issue("REPLICATION_LAG", _lag_severity(seconds, policy),
                                     f"Observed Oracle {name} exceeds the configured policy threshold.",
                                     evidence={"metric": name, "value": value,
                                               "time_computed": _value(row, "time_computed")}))
        elif name == "apply finish time" and value is not None:
            lag.append(ReplicationLag("apply_finish_time", str(value), "vendor", "INFO", "V$DATAGUARD_STATS"))
    process_query_ok = _sample_ok(evidence, "replication_processes", "replication_processes_modern")

    def process_name(row: dict[str, Any]) -> str:
        return str(_value(row, "process", "name") or "").upper()

    def process_status(row: dict[str, Any]) -> str:
        return str(_value(row, "status", "action") or "UNKNOWN")

    mrp = [row for row in processes if process_name(row).startswith("MRP")]
    rfs = [row for row in processes if process_name(row) == "RFS"]
    for row in processes:
        process = process_name(row) or "UNKNOWN"
        status = process_status(row)
        members.append(ReplicationMember(process, None, "STANDBY" if process.upper().startswith("MRP") else "PRIMARY",
                                         process, status.upper(), status, None,
                                         status.upper() if process.upper().startswith("MRP") else None, row))
    if role == "STANDBY" and process_query_ok and not mrp:
        issues.append(_issue("APPLY_STOPPED", "HIGH",
                             "No managed recovery process was returned for this standby snapshot."))
    elif any(process_status(row).upper() in {"ERROR", "WAIT_FOR_GAP"} for row in mrp):
        issues.append(_issue("APPLY_FAILURE", "CRITICAL", "Managed recovery reports an error or archive gap.",
                             evidence={"processes": mrp}))
    transport = "RUNNING" if rfs or destinations else "UNKNOWN"
    apply = ("RUNNING" if mrp else "STOPPED" if role == "STANDBY" and process_query_ok
             else "UNKNOWN" if role == "STANDBY" else "NOT_CONFIGURED")
    for row in destinations:
        destination = str(_value(row, "destination") or f"dest-{_value(row, 'dest_id')}")
        error = _value(row, "error")
        status = str(_value(row, "status") or "UNKNOWN")
        members.append(ReplicationMember(destination, False, "STANDBY", str(_value(row, "type") or "UNKNOWN"),
                                         "DEGRADED" if error or status.upper() in {"ERROR", "DISABLED"} else "RUNNING",
                                         status, status,
                                         str(_value(row, "recovery_mode") or "UNKNOWN"), row))
        if error or status.upper() == "ERROR":
            issues.append(_issue("TRANSPORT_FAILURE", "HIGH", "Oracle archive destination reports an error.",
                                 vendor_message=error, evidence={"destination": destination,
                                                                 "status": status,
                                                                 "gap_status": _value(row, "gap_status")}))
        backlog[f"{destination}:archived_sequence"] = _value(row, "archived_seq#")
        backlog[f"{destination}:applied_sequence"] = _value(row, "applied_seq#")
    not_configured = (role == "PRIMARY" and not destinations
                      and _sample_ok(evidence, "replication_destinations"))
    if role == "UNKNOWN":
        issues.append(_issue("ROLE_UNKNOWN", "WARNING", "Oracle database role could not be mapped from V$DATABASE."))
    return {"role": role, "vendor_role": role_value, "mode": "Oracle Data Guard",
            "members": members, "lag": lag, "issues": issues, "backlog": backlog,
            "transport": "NOT_CONFIGURED" if not_configured else transport, "apply": apply,
            "not_configured": not_configured}


def diagnose_replication(adapter: Any, policy: ReplicationPolicy = DEFAULT_REPLICATION_POLICY) -> ReplicationReport:
    """Observe one target instance. Never connects to peers or invokes admin actions."""
    instance: DatabaseInstance = adapter.instance
    evidence = collect_replication_evidence(adapter)
    if not evidence.supported:
        summary = ReplicationSummary(datetime.now(timezone.utc).isoformat(), instance.id,
                                     instance.db_type.value, "UNKNOWN", None,
                                     "UNSUPPORTED", "UNSUPPORTED", "UNKNOWN", "UNKNOWN",
                                     "UNKNOWN", "UNKNOWN", evidence.unsupported_reason)
        return ReplicationReport(instance, datetime.now(timezone.utc), "replication", (), summary=summary,
                                 details={"incomplete": True, "samples": [], "quality": [evidence.unsupported_reason or "Version unsupported"]})

    if evidence.database_type == DatabaseType.MYSQL:
        state = _mysql(evidence, policy)
    elif evidence.database_type == DatabaseType.POSTGRESQL:
        state = _postgresql(evidence, policy)
    elif evidence.database_type == DatabaseType.SQLSERVER:
        state = _sqlserver(evidence)
    else:
        state = _oracle(evidence, policy)
    required_names = {
        DatabaseType.MYSQL: {"replication_status", "replication_status_legacy"},
        DatabaseType.POSTGRESQL: {"replication_node"},
        DatabaseType.SQLSERVER: {"replication_ag"},
        DatabaseType.ORACLE: {"replication_database"},
    }[evidence.database_type]
    required_samples = [sample for sample in evidence.samples if sample.collector_name in required_names]
    required_ok = bool(required_samples) and all(sample.success and not sample.truncated for sample in required_samples)
    all_ok = all(sample.success and not sample.truncated for sample in evidence.samples)
    readiness = assess_permissions(requirements_for(instance.db_type, "replication", evidence.version),
                                   getattr(adapter, "permission_observations", None))
    if readiness["status"] == PermissionStatus.MISSING:
        state["issues"].append(_issue("MISSING_PERMISSION", "WARNING",
                                      "Required replication monitoring permissions are missing.",
                                      evidence={"missing_required": readiness["missing_required"]}))
    if not required_ok:
        state["issues"].append(_issue("REQUIRED_EVIDENCE_MISSING", "WARNING",
                                      "Required replication role or status evidence could not be collected."))
    completeness = "INCOMPLETE" if not required_ok else "COMPLETE" if all_ok and readiness["status"] == PermissionStatus.READY else "PARTIAL"
    issues: list[ReplicationIssue] = state["issues"][:policy.max_issues]
    severity_rank = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}
    max_severity = max((severity_rank.get(issue.severity, 1) for issue in issues), default=0)
    if not required_ok:
        overall = "UNKNOWN"
    elif state["not_configured"] and required_ok:
        overall = "HEALTHY"
    else:
        overall = "CRITICAL" if max_severity >= 3 else "DEGRADED" if max_severity else "HEALTHY"
    not_configured = state["not_configured"] and required_ok
    status = "NOT_CONFIGURED" if not_configured else overall
    members: list[ReplicationMember] = state["members"][:policy.max_members]
    lags: list[ReplicationLag] = state["lag"][:policy.max_members]
    findings = tuple(Finding(issue.code, Severity.CRITICAL if issue.severity == "CRITICAL"
                             else Severity.WARNING if issue.severity in {"WARNING", "HIGH"}
                             else Severity.INFO,
                             {"message": issue.message, "vendor_message": issue.vendor_message,
                              **issue.evidence},
                             "Review the observed replication evidence with the database owner; Toolkit performs no repair.")
                     for issue in issues)
    if not_configured:
        findings = (Finding("REPLICATION_NOT_CONFIGURED", Severity.INFO,
                            {"status": "NOT_CONFIGURED"},
                            "No replication configuration was observed on this instance."), *findings)
    version_label = evidence.version
    if instance.db_type == DatabaseType.POSTGRESQL and version_label and version_label.isdigit():
        version_number = int(version_label)
        if version_number >= 100000:
            version_label = f"{version_number // 10000}.{(version_number // 100) % 100}"
    summary = ReplicationSummary(datetime.now(timezone.utc).isoformat(), instance.id,
                                 instance.db_type.value, state["role"], state["vendor_role"],
                                 state["mode"], status, overall, completeness,
                                 state["transport"], state["apply"], version=version_label)
    action_text = {
        "SOURCE_CONNECTION_ERROR": "Check the network path to the configured source and review the receiver error.",
        "IO_THREAD_STOPPED": "Review the MySQL receiver thread state and source connectivity.",
        "SQL_APPLY_ERROR": "Review the MySQL applier error and affected transaction with the DBA.",
        "SQL_THREAD_STOPPED": "Review the MySQL applier state and error details.",
        "INACTIVE_PHYSICAL_SLOT": "Identify the PostgreSQL consumer for the inactive physical slot; review retained WAL before any manual slot action.",
        "LARGE_RETAINED_WAL": "Review WAL retention, slot ownership, and available storage; this snapshot does not show the growth trend.",
        "DISCONNECTED_REPLICA": "Check connectivity to the upstream and confirm the WAL receiver state.",
        "NO_CONNECTED_STANDBY": "Confirm whether a standby is expected and inspect source-side WAL sender connectivity.",
        "REPLICATION_STOPPED": "Investigate SQL Server synchronization health and the observed log-send or redo queue.",
        "APPLY_STOPPED": "Review Oracle managed recovery process state and recovery logs.",
        "APPLY_FAILURE": "Review Oracle recovery process status and database-reported archive gap evidence.",
        "TRANSPORT_FAILURE": "Review the Oracle archive destination error and the network/service path.",
        "REPLICATION_LAG": "Compare transport and apply evidence with the configured lag policy and workload.",
    }
    actions = tuple(ReplicationAction(action_text.get(issue.code, issue.message)) for issue in issues
                    if issue.severity in {"WARNING", "HIGH", "CRITICAL"})
    requirements = requirements_for(instance.db_type, "replication", evidence.version)
    missing = readiness["missing_required"]
    permission = {"status": readiness["status"].value,
                  "missing_required": missing,
                  "missing_optional": readiness["missing_optional"],
                  "available": readiness["available"],
                  "missing_reasons": privilege_reasons(requirements, missing),
                  "impact": requirements.impact,
                  "suggested_grant": render_sql(instance.db_type, ("replication",),
                                                  getattr(adapter, "current_user", "") or "monitor",
                                                  instance.database, evidence.version) if missing else None,
                  "toolkit_executed": False,
                  "dba_approval_required": bool(missing)}
    details = {"incomplete": completeness == "INCOMPLETE",
               "required_evidence_complete": required_ok,
               "samples": _sample_payload(evidence),
               "permission_readiness": permission,
               "vendor_evidence": {sample.collector_name: list(sample.rows[:policy.max_members])
                                   for sample in evidence.samples if sample.success},
               "quality": ([] if all_ok else ["Optional replication evidence is unavailable or truncated."]),
               "toolkit_executed": "NO"}
    return ReplicationReport(instance, datetime.now(timezone.utc), "replication", findings,
                             summary, tuple(members), tuple(lags), state["backlog"], tuple(issues),
                             actions, (), details)
