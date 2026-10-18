"""Bounded, current-state disk and log diagnosis for four database engines."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport, Finding, Severity
from dba.disk_growth_profiles import profile_for_disk, sql_profile_for_disk
from dba.errors import ToolkitError
from dba.logging import redact
from dba.mysql import MySQLAdapter
from dba.oracle import OracleAdapter
from dba.postgresql import PostgreSQLAdapter
from dba.sqlserver import SQLServerAdapter

DiskAdapter = SQLServerAdapter | PostgreSQLAdapter | MySQLAdapter | OracleAdapter

HISTORY_NOTE = "Current-state diagnosis only; growth rate unavailable without historical snapshot."
WAL_NOTE = "pg_wal filesystem size unavailable from database protocol"


@dataclass(frozen=True)
class DiskGrowthReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


def _number(value: Any) -> float | None:
    if type(value) in (int, float):
        return float(value)
    return None


def _rows(results: dict[str, CollectorResult], name: str) -> list[dict[str, Any]]:
    result = results.get(name)
    return [dict(row) for row in result.rows] if result and result.success else []


def _sqlserver_file_growth(row: dict[str, Any]) -> dict[str, Any]:
    growth = _number(row.get("growth"))
    is_percent = row.get("is_percent_growth") is True or row.get("is_percent_growth") == 1
    mode: str
    increment: float | None
    if growth is None:
        mode, increment = "UNKNOWN", None
    elif growth == 0:
        mode, increment = "DISABLED", 0.0
    elif is_percent:
        mode, increment = "PERCENT", growth
    else:
        mode, increment = "FIXED_PAGES", growth * 8192
    maximum = _number(row.get("max_size"))
    max_state: str
    max_bytes: float | None
    if maximum == -1:
        max_state, max_bytes = "UNLIMITED", None
    elif maximum == 0:
        max_state, max_bytes = "NO_GROWTH", 0
    elif maximum is not None:
        max_state, max_bytes = "LIMITED", maximum * 8192
    else:
        max_state, max_bytes = "UNKNOWN", None
    return {"growth_mode": mode, "growth_increment": increment,
            "growth_increment_unit": "percent" if is_percent else "bytes",
            "max_size_state": max_state, "max_size_bytes": max_bytes}


def build_disk_report(instance: DatabaseInstance, collected: tuple[CollectorResult, ...],
                      version: str, *, required_collectors: tuple[str, ...],
                      optional_collectors: tuple[str, ...], profile_id: str) -> DiskGrowthReport:
    """Analyze only observed values; a failed/truncated source cannot prove absence."""
    results = {result.collector_name: result for result in collected}
    findings: list[Finding] = []
    space: list[dict[str, Any]] = []
    log: dict[str, Any] = {}
    other: dict[str, Any] = {}
    next_step: str | None = None

    def add(title: str, severity: Severity, source: str, evidence: dict[str, Any],
            recommendation: str) -> None:
        findings.append(Finding(title, severity, {"source": source, **evidence}, recommendation))

    if instance.db_type == DatabaseType.SQLSERVER:
        for row in _rows(results, "disk_files"):
            space.append({"category": "TRANSACTION_LOG" if row.get("file_type") == "LOG" else "DATA",
                          **row, **_sqlserver_file_growth(row), "source": "disk_files"})
            size, used = _number(row.get("size_bytes")), _number(row.get("used_bytes"))
            max_pages = _number(row.get("max_size"))
            near_limit = bool(size and max_pages is not None and (
                max_pages > 0 and size / (max_pages * 8192) >= .85
                or max_pages == 0 and used is not None and used / size >= .85))
            if row.get("file_type") != "LOG" and near_limit:
                add("SQLSERVER_DATAFILE_NEAR_LIMIT", Severity.WARNING, "disk_files",
                    {"file_name": row.get("file_name"), "size_bytes": size,
                     "max_size_pages": max_pages, "used_bytes": used},
                    "Review file capacity and underlying volume with the DBA; no resize was attempted.")
        for row in _rows(results, "disk_tempdb"):
            space.append({"category": "TEMP", **row, **_sqlserver_file_growth(row),
                          "source": "disk_tempdb"})
        for row in _rows(results, "disk_temp_usage"):
            total, free = _number(row.get("total_pages")), _number(row.get("free_pages"))
            if total and free is not None and (total - free) / total >= .85:
                add("SQLSERVER_TEMPDB_HIGH_USAGE", Severity.WARNING, "disk_temp_usage",
                    {"file_id": row.get("file_id"),
                     "used_percent": round((total - free) / total * 100, 1)},
                    "Review TempDB consumers and file capacity; do not shrink automatically.")
        rows = _rows(results, "disk_log") or _rows(results, "disk_log_legacy")
        if rows:
            log = {**rows[0], "category": "TRANSACTION_LOG", "source": "disk_log"}
            percent = _number(log.get("log_used_percent"))
            reason = log.get("log_reuse_wait_desc")
            if percent is not None and percent >= 85:
                add("SQLSERVER_LOG_HIGH_USAGE", Severity.WARNING, "disk_log",
                    {"log_used_percent": percent, "log_reuse_wait_desc": reason},
                    "Review log capacity and reuse reason; do not shrink automatically.")
            if reason not in (None, "NOTHING", "CHECKPOINT"):
                add("SQLSERVER_LOG_REUSE_BLOCKED", Severity.WARNING, "disk_log",
                    {"log_reuse_wait_desc": reason},
                    "Investigate the reported log reuse condition before any manual action.")
                next_step = "transaction-log"
        backups = _rows(results, "disk_log_backup")
        if backups:
            log["last_log_backup_time"] = backups[0].get("last_log_backup_time")
        for row in (_rows(results, "disk_transactions") or _rows(results, "transactions")):
            age = _number(row.get("transaction_age_seconds"))
            if age is not None and age >= 3600:
                add("SQLSERVER_OLD_ACTIVE_TRANSACTION", Severity.WARNING, "transactions",
                    {"transaction_id": row.get("transaction_id"), "age_seconds": age},
                    "Review the owning transaction and log reuse evidence.")
        other["availability_groups"] = _rows(results, "disk_ag")

    elif instance.db_type == DatabaseType.POSTGRESQL:
        for row in _rows(results, "disk_database"):
            space.append({"category": "DATA", **row, "source": "disk_database"})
            other["temp_cumulative"] = {"temp_bytes": row.get("temp_bytes"),
                                        "temp_files": row.get("temp_files"),
                                        "stats_reset": row.get("stats_reset"),
                                        "note": "Cumulative counters; no current temp footprint or rate."}
        other["largest_relations"] = _rows(results, "disk_relations")
        slots = _rows(results, "disk_slots")
        slots_result = results.get("disk_slots")
        other["slots"] = slots
        other["slot_status"] = ("UNAVAILABLE" if slots_result is None or not slots_result.success
                                 or slots_result.truncated else
                                 "NOT_CONFIGURED" if not slots else "CONFIGURED")
        for row in slots:
            retained = _number(row.get("retained_wal_estimate_bytes"))
            if row.get("active") is False and retained is not None and retained > 0:
                add("PG_WAL_RETENTION_SLOT", Severity.WARNING, "disk_slots",
                    {"slot_name": row.get("slot_name"), "slot_type": row.get("slot_type"),
                     "active": False, "retained_wal_estimate_bytes": retained},
                    "Confirm consumer state; ask the DBA to review slot retention manually.")
                next_step = "wal-growth"
        archiver = _rows(results, "disk_archiver")
        if archiver:
            log = {**archiver[0], "category": "WAL", "source": "disk_archiver"}
            other["archive_status"] = ("NOT_CONFIGURED" if log.get("archive_mode") == "off"
                                       else "CONFIGURED")
            failed = log.get("last_failed_time")
            success = log.get("last_archived_time")
            if failed is not None and (success is None or str(failed) > str(success)):
                add("PG_ARCHIVE_FAILURE", Severity.WARNING, "disk_archiver",
                    {"last_failed_wal": log.get("last_failed_wal"),
                     "last_failed_time": failed, "failed_count": log.get("failed_count")},
                    "Review archiver destination and recent error evidence.")
        elif results.get("disk_archiver") is None or not results["disk_archiver"].success:
            other["archive_status"] = "UNAVAILABLE"
        other["replicas"] = _rows(results, "disk_replicas")
        other["wal_filesystem"] = WAL_NOTE
        for row in (_rows(results, "disk_transactions") or _rows(results, "transactions")):
            age = _number(row.get("transaction_age_seconds"))
            if age is not None and age >= 3600:
                add("PG_LONG_TRANSACTION", Severity.WARNING, "transactions",
                    {"pid": row.get("pid"), "age_seconds": age, "state": row.get("state")},
                    "Review transaction owner and WAL retention conditions.")

    elif instance.db_type == DatabaseType.MYSQL:
        for row in _rows(results, "disk_schemas"):
            space.append({"category": "DATA", **row, "source": "disk_schemas"})
        config = (_rows(results, "disk_binlog_config")
                  or _rows(results, "disk_binlog_config_legacy"))
        log = {**config[0], "category": "BINLOG", "source": "disk_binlog_config"} if config else {}
        config_result = results.get("disk_binlog_config") or results.get("disk_binlog_config_legacy")
        if config and not config[0].get("binary_log_enabled"):
            log["binlog_status"] = "Not configured"
        elif config:
            log["binlog_status"] = "CONFIGURED"
        elif config_result is None or not config_result.success or config_result.truncated:
            log["binlog_status"] = "UNAVAILABLE"
        other["largest_tables"] = _rows(results, "disk_tables")
        other["innodb_tablespaces"] = _rows(results, "disk_tablespaces")
        other["data_free_semantics"] = (
            "DATA_FREE is engine-reported free/unused space evidence; it does not prove that bytes can be reclaimed by the operating system."
        )
        binlog_result = results.get("disk_binlogs")
        binlogs = _rows(results, "disk_binlogs")
        if binlog_result and binlog_result.success and not binlog_result.truncated:
            sizes = [_number(row.get("file_size")) for row in binlogs]
            if all(size is not None for size in sizes):
                total = sum(size for size in sizes if size is not None)
                log["binlog_total_bytes"] = total
                log["binlog_file_count"] = len(binlogs)
                log["largest_binlogs"] = sorted(binlogs, key=lambda row: row["file_size"],
                                                reverse=True)[:5]
                if total >= 10 * 1024**3:
                    add("BINLOG_TOTAL_SIZE_HIGH", Severity.WARNING, "disk_binlogs",
                        {"total_bytes": total, "file_count": len(binlogs)},
                        "Review retention settings and backup/replication requirements; do not purge automatically.")
                    next_step = "mysql-binlog"
        other["replica"] = _rows(results, "disk_replica")
        other["replica_status"] = "Not configured" if not other["replica"] else "Configured"
        other["relay_log_size"] = "Unavailable from configured read-only SQL"
        for row in other["replica"]:
            if row.get("service_state") != "ON":
                add("REPLICA_RELAY_LOG_RISK", Severity.WARNING, "disk_replica",
                    {"channel_name": row.get("channel_name"),
                     "service_state": row.get("service_state"),
                     "last_error_number": row.get("last_error_number")},
                    "Review receiver state and relay-log files manually; size is unavailable here.")
        other["innodb"] = _rows(results, "disk_innodb")
        for row in _rows(results, "transactions"):
            age = _number(row.get("transaction_age_seconds"))
            if age is not None and age >= 3600:
                add("LONG_TRANSACTION", Severity.WARNING, "transactions",
                    {"trx_id": row.get("trx_id"), "age_seconds": age},
                    "Review transaction owner and log impact.")

    elif instance.db_type == DatabaseType.ORACLE:
        for row in _rows(results, "disk_tablespaces"):
            category = "UNDO" if row.get("contents") == "UNDO" else (
                "TEMP" if row.get("contents") == "TEMPORARY" else "DATA")
            space.append({"category": category, **row, "source": "disk_tablespaces"})
            percent = _number(row.get("used_percent"))
            if percent is not None and percent >= 85:
                title = ("ORACLE_UNDO_HIGH_USAGE" if category == "UNDO" else
                         "ORACLE_TEMP_HIGH_USAGE" if category == "TEMP" else
                         "ORACLE_TABLESPACE_HIGH_USAGE")
                add(title, Severity.WARNING, "disk_tablespaces",
                    {"tablespace_name": row.get("tablespace_name"), "used_percent": percent},
                    "Review tablespace allocation and underlying storage manually.")
        other["datafiles"] = _rows(results, "disk_files")
        for row in _rows(results, "disk_temp"):
            space.append({"category": "TEMP", **row, "source": "disk_temp"})
            size, free = _number(row.get("size_bytes")), _number(row.get("free_bytes"))
            if size and free is not None and (size - free) / size >= .85:
                add("ORACLE_TEMP_HIGH_USAGE", Severity.WARNING, "disk_temp",
                    {"tablespace_name": row.get("tablespace_name"),
                     "used_percent": round((size - free) / size * 100, 1)},
                    "Review temporary-space consumers and capacity manually.")
        fra = _rows(results, "disk_fra")
        fra_result = results.get("disk_fra")
        other["fra_status"] = ("UNAVAILABLE" if fra_result is None or not fra_result.success
                                or fra_result.truncated else
                                "CONFIGURED" if fra else "NOT_CONFIGURED")
        if fra:
            log = {**fra[0], "category": "FRA", "source": "disk_fra"}
            limit, used = _number(log.get("space_limit")), _number(log.get("space_used"))
            if limit and used is not None and used / limit >= .85:
                add("ORACLE_FRA_HIGH_USAGE", Severity.WARNING, "disk_fra",
                    {"space_limit": limit, "space_used": used,
                     "space_reclaimable": log.get("space_reclaimable")},
                    "Review FRA components and retention with the DBA; do not delete files automatically.")
                next_step = "fra-archive"
        other["fra_usage"] = _rows(results, "disk_fra_usage")
        for row in other["fra_usage"]:
            if row.get("file_type") == "FLASHBACK LOG" and (
                    percent := _number(row.get("percent_space_used"))) is not None and percent >= 50:
                add("ORACLE_FLASHBACK_HIGH_USAGE", Severity.WARNING, "disk_fra_usage",
                    {"percent_space_used": percent}, "Review flashback retention manually.")
        other["archive_destinations"] = _rows(results, "disk_archive")
        for row in other["archive_destinations"]:
            if row.get("status") in ("ERROR", "FULL", "BAD PARAM"):
                add("ORACLE_ARCHIVE_DEST_FAILURE", Severity.WARNING, "disk_archive",
                    {"dest_id": row.get("dest_id"), "status": row.get("status")},
                    "Review archive destination status manually.")
        redo = _rows(results, "disk_redo")
        other["log_mode"] = redo[0].get("log_mode") if redo else None
        if redo and redo[0].get("log_mode") == "NOARCHIVELOG":
            other["archive_status"] = "NOT_CONFIGURED"
        elif redo:
            other["archive_status"] = "CONFIGURED" if other["archive_destinations"] else "UNKNOWN"
        archived = _rows(results, "disk_archive_volume")
        other["archive_24h"] = archived[0] if archived else None
        if archived:
            bytes_24h = _number(archived[0].get("archived_bytes_24h"))
            switches = _number(archived[0].get("redo_switches_24h"))
            if bytes_24h is not None and bytes_24h >= 10 * 1024**3:
                add("ORACLE_ARCHIVE_ACCUMULATION", Severity.WARNING, "disk_archive_volume",
                    {"archived_bytes_24h": bytes_24h, "redo_switches_24h": switches},
                    "Compare archive generation with backup and standby consumption; do not delete logs.")
                next_step = "fra-archive"

    mysql_binlog_configuration = (_rows(results, "disk_binlog_config")
                                  or _rows(results, "disk_binlog_config_legacy"))
    mysql_binlog_disabled = (instance.db_type == DatabaseType.MYSQL
                             and mysql_binlog_configuration
                             and not mysql_binlog_configuration[0].get("binary_log_enabled"))
    not_applicable = ["disk_binlogs"] if mysql_binlog_disabled else []
    if instance.db_type == DatabaseType.ORACLE:
        fra_result = results.get("disk_fra")
        if fra_result and fra_result.success and not fra_result.truncated and not fra_result.rows:
            not_applicable.extend(("disk_fra", "disk_fra_usage"))
        redo_rows = _rows(results, "disk_redo")
        if redo_rows and redo_rows[0].get("log_mode") == "NOARCHIVELOG":
            not_applicable.extend(("disk_archive", "disk_archive_volume"))
    if instance.db_type == DatabaseType.POSTGRESQL:
        slots_result = results.get("disk_slots")
        if slots_result and slots_result.success and not slots_result.truncated and not slots_result.rows:
            not_applicable.append("disk_slots")
    missing_required = [name for name in required_collectors if name not in results]
    failed_required = [name for name in required_collectors if name in results
                       and (not results[name].success or results[name].truncated)]
    unavailable_optional = [name for name in optional_collectors if name not in results
                            and name not in not_applicable]
    failed_optional = [name for name in optional_collectors if name in results
                       and (not results[name].success or results[name].truncated)
                       and name not in not_applicable]
    incomplete = bool(missing_required or failed_required)
    transaction_result = results.get("disk_transactions") or results.get("transactions")
    transaction_rows = list(transaction_result.rows) if transaction_result and transaction_result.success else []
    other["transactions"] = transaction_rows
    other["active_transaction_count"] = (len(transaction_rows)
                                          if transaction_result and transaction_result.success
                                          and not transaction_result.truncated else None)
    samples = [{"source": result.collector_name, "row_count": result.row_count,
                "success": result.success, "truncated": result.truncated,
                "error_type": result.error_type} for result in collected]
    totals: dict[str, float] = {}
    for row in space:
        size = (_number(row.get("size_bytes")) or _number(row.get("allocated_bytes"))
                or (_number(row.get("data_bytes")) or 0) + (_number(row.get("index_bytes")) or 0))
        if size and row.get("source") != "disk_temp":
            category = str(row["category"])
            totals[category] = totals.get(category, 0) + size
    dominant = max(totals, key=totals.get) if totals and not incomplete else None  # type: ignore[arg-type]
    optional_missing = list(dict.fromkeys(unavailable_optional + failed_optional))
    details = {"version": version, "version_profile": profile_id, "space": space, "log": log, "other": other,
               "samples": samples, "incomplete": incomplete,
               "required_evidence_complete": not incomplete,
               "optional_evidence_status": "PARTIAL" if optional_missing else "COMPLETE",
               "data_visibility": "PARTIAL" if optional_missing else "FULL",
               "dominant_space_category": dominant,
               "history_note": HISTORY_NOTE, "next_diagnostic_step": next_step,
               "required_unavailable": missing_required + failed_required,
               "optional_unavailable": optional_missing,
               "not_applicable": not_applicable,
               "unavailable": missing_required + failed_required}
    return DiskGrowthReport(instance, datetime.now(timezone.utc), "disk-growth",
                            tuple(findings), redact(details))


def _collect_disk_growth(adapter: DiskAdapter) -> DiskGrowthReport:
    profile = profile_for_disk(adapter)
    if profile is None:
        raise ToolkitError("VersionVerificationRequired",
                           "Disk-growth diagnosis requires a verified version-aware diagnostic profile.")
    if profile.version_status.value != "VERIFIED":
        raise ToolkitError("VersionVerificationRequired",
                           "Disk-growth diagnosis requires a declared version family matching the detected server.",
                           trigger_reason={
                               "declared_family": profile.declared_version_family or "",
                               "detected_family": profile.detected_version_family or "",
                               "version_status": profile.version_status.value,
                           })
    if profile.support_state.value != "SUPPORTED" or not profile.sql_profile_id:
        raise ToolkitError("FeatureUnsupported", "No reviewed disk-growth profile is available for this version.",
                           trigger_reason={
                               "feature": "disk-growth",
                               "version_family": profile.detected_version_family or "UNKNOWN",
                               "support_state": profile.support_state.value,
                           })
    sql_profile = sql_profile_for_disk(profile.sql_profile_id)
    version = profile.detected_raw_version or adapter.get_server_version()
    results: list[CollectorResult] = []
    for name in sql_profile.ordered_scripts:
        if (name == "disk_binlogs" and results
                and any(result.collector_name in {"disk_binlog_config", "disk_binlog_config_legacy"}
                        and result.rows and not result.rows[0].get("binary_log_enabled")
                        for result in results)):
            continue
        result = adapter.execute_readonly(adapter.registry.get(name, adapter.instance.db_type))
        if name == "disk_log_legacy":
            target = adapter.instance.database or "master"
            normalized: list[dict[str, Any]] = []
            for row in result.rows:
                source = {str(key).casefold(): value for key, value in row.items()}
                database_name = source.get("database name")
                if str(database_name).casefold() != target.casefold():
                    continue
                size_mb = _number(source.get("log size (mb)"))
                used_pct = _number(source.get("log space used (%)"))
                normalized.append({"database_name": database_name,
                                   "log_size_bytes": size_mb * 1024 * 1024 if size_mb is not None else None,
                                   "log_used_percent": used_pct,
                                   "log_used_bytes": (size_mb * 1024 * 1024 * used_pct / 100
                                                      if size_mb is not None and used_pct is not None else None),
                                   "source": "DBCC SQLPERF(LOGSPACE)"})
            result = CollectorResult(result.database_id, "disk_log_legacy", result.started_at,
                                     result.finished_at, result.elapsed_ms, tuple(normalized),
                                     len(normalized), result.truncated, result.success,
                                     result.error_type, result.error_message)
        results.append(result)
        if not result.success:
            break  # Driver adapters close a failed connection; never reuse it.
    return build_disk_report(adapter.instance, tuple(results), version,
                             required_collectors=sql_profile.required,
                             optional_collectors=sql_profile.optional,
                             profile_id=profile.sql_profile_id)


class SQLServerDiskGrowthAnalyzer:
    def diagnose(self, adapter: SQLServerAdapter) -> DiskGrowthReport:
        return _collect_disk_growth(adapter)


class PostgreSQLDiskGrowthAnalyzer:
    def diagnose(self, adapter: PostgreSQLAdapter) -> DiskGrowthReport:
        return _collect_disk_growth(adapter)


class MySQLDiskGrowthAnalyzer:
    def diagnose(self, adapter: MySQLAdapter) -> DiskGrowthReport:
        return _collect_disk_growth(adapter)


class OracleDiskGrowthAnalyzer:
    def diagnose(self, adapter: OracleAdapter) -> DiskGrowthReport:
        return _collect_disk_growth(adapter)


def diagnose_disk_growth(adapter: DiskAdapter) -> DiskGrowthReport:
    """Dispatch by Inventory database type without altering existing slow analyzers."""
    kind = adapter.instance.db_type
    if kind == DatabaseType.SQLSERVER:
        return SQLServerDiskGrowthAnalyzer().diagnose(adapter)  # type: ignore[arg-type]
    if kind == DatabaseType.POSTGRESQL:
        return PostgreSQLDiskGrowthAnalyzer().diagnose(adapter)  # type: ignore[arg-type]
    if kind == DatabaseType.MYSQL:
        return MySQLDiskGrowthAnalyzer().diagnose(adapter)  # type: ignore[arg-type]
    return OracleDiskGrowthAnalyzer().diagnose(adapter)  # type: ignore[arg-type]
