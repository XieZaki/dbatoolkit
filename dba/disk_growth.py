"""Bounded, current-state disk and log diagnosis for four database engines."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport, Finding, Severity
from dba.logging import redact
from dba.mysql import MySQLAdapter
from dba.oracle import OracleAdapter
from dba.postgresql import PostgreSQLAdapter
from dba.sqlserver import SQLServerAdapter

DiskAdapter = SQLServerAdapter | PostgreSQLAdapter | MySQLAdapter | OracleAdapter

SCRIPTS: dict[DatabaseType, tuple[str, ...]] = {
    DatabaseType.SQLSERVER: ("disk_files", "disk_log", "disk_tempdb", "disk_temp_usage", "transactions",
                             "disk_log_backup", "disk_ag"),
    DatabaseType.POSTGRESQL: ("disk_database", "disk_slots", "disk_archiver",
                              "disk_replicas", "transactions"),
    DatabaseType.MYSQL: ("disk_schemas", "disk_binlog_config", "disk_replica",
                         "disk_innodb", "transactions", "disk_binlogs"),
    DatabaseType.ORACLE: ("disk_tablespaces", "disk_files", "disk_temp", "disk_fra",
                          "disk_fra_usage", "disk_archive", "disk_archive_volume",
                          "disk_redo", "transactions"),
}

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


def build_disk_report(instance: DatabaseInstance, collected: tuple[CollectorResult, ...],
                      version: str) -> DiskGrowthReport:
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
                          **row, "source": "disk_files"})
            size, used = _number(row.get("size_bytes")), _number(row.get("used_bytes"))
            max_pages = _number(row.get("max_size"))
            near_limit = bool(size and (
                max_pages is not None and max_pages > 0 and size / (max_pages * 8192) >= .85
                or max_pages == 0 and used is not None and used / size >= .85))
            if row.get("file_type") != "LOG" and near_limit:
                add("SQLSERVER_DATAFILE_NEAR_LIMIT", Severity.WARNING, "disk_files",
                    {"file_name": row.get("file_name"), "size_bytes": size,
                     "max_size_pages": max_pages, "used_bytes": used},
                    "Review file capacity and underlying volume with the DBA; no resize was attempted.")
        for row in _rows(results, "disk_tempdb"):
            space.append({"category": "TEMP", **row, "source": "disk_tempdb"})
        for row in _rows(results, "disk_temp_usage"):
            total, free = _number(row.get("total_pages")), _number(row.get("free_pages"))
            if total and free is not None and (total - free) / total >= .85:
                add("SQLSERVER_TEMPDB_HIGH_USAGE", Severity.WARNING, "disk_temp_usage",
                    {"file_id": row.get("file_id"),
                     "used_percent": round((total - free) / total * 100, 1)},
                    "Review TempDB consumers and file capacity; do not shrink automatically.")
        rows = _rows(results, "disk_log")
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
        for row in _rows(results, "transactions"):
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
        slots = _rows(results, "disk_slots")
        other["slots"] = slots
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
            failed = log.get("last_failed_time")
            success = log.get("last_archived_time")
            if failed is not None and (success is None or str(failed) > str(success)):
                add("PG_ARCHIVE_FAILURE", Severity.WARNING, "disk_archiver",
                    {"last_failed_wal": log.get("last_failed_wal"),
                     "last_failed_time": failed, "failed_count": log.get("failed_count")},
                    "Review archiver destination and recent error evidence.")
        other["replicas"] = _rows(results, "disk_replicas")
        other["wal_filesystem"] = WAL_NOTE
        for row in _rows(results, "transactions"):
            age = _number(row.get("transaction_age_seconds"))
            if age is not None and age >= 3600:
                add("PG_LONG_TRANSACTION", Severity.WARNING, "transactions",
                    {"pid": row.get("pid"), "age_seconds": age, "state": row.get("state")},
                    "Review transaction owner and WAL retention conditions.")

    elif instance.db_type == DatabaseType.MYSQL:
        for row in _rows(results, "disk_schemas"):
            space.append({"category": "DATA", **row, "source": "disk_schemas"})
        config = _rows(results, "disk_binlog_config")
        log = {**config[0], "category": "BINLOG", "source": "disk_binlog_config"} if config else {}
        if config and not config[0].get("binary_log_enabled"):
            log["binlog_status"] = "Not configured"
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
        other["fra_status"] = "Configured" if fra else "Not configured"
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

    skipped = (instance.db_type == DatabaseType.MYSQL
               and _rows(results, "disk_binlog_config")
               and not _rows(results, "disk_binlog_config")[0].get("binary_log_enabled"))
    missing = [name for name in SCRIPTS[instance.db_type] if name not in results
               and not (name == "disk_binlogs" and skipped)]
    incomplete = bool(missing) or any(not result.success or result.truncated for result in collected)
    other["transactions"] = _rows(results, "transactions")
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
    details = {"version": version, "space": space, "log": log, "other": other,
               "samples": samples, "incomplete": incomplete,
               "dominant_space_category": dominant,
               "history_note": HISTORY_NOTE, "next_diagnostic_step": next_step,
               "unavailable": missing + [result.collector_name for result in collected
                                         if not result.success or result.truncated]}
    return DiskGrowthReport(instance, datetime.now(timezone.utc), "disk-growth",
                            tuple(findings), redact(details))


def _collect_disk_growth(adapter: DiskAdapter) -> DiskGrowthReport:
    version = adapter.get_server_version()
    results: list[CollectorResult] = []
    for name in SCRIPTS[adapter.instance.db_type]:
        if (name == "disk_binlogs" and results
                and any(result.collector_name == "disk_binlog_config" and result.rows
                        and not result.rows[0].get("binary_log_enabled") for result in results)):
            continue
        result = adapter.execute_readonly(adapter.registry.get(name, adapter.instance.db_type))
        results.append(result)
        if not result.success:
            break  # Driver adapters close a failed connection; never reuse it.
    return build_disk_report(adapter.instance, tuple(results), version)


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
