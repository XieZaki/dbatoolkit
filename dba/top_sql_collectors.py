"""Bounded collectors for cache-resident Top SQL statistics."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.mysql import MYSQL_TOP_SQL_METRICS, MySQLAdapter
from dba.oracle import ORACLE_TOP_SQL_METRICS, OracleAdapter
from dba.postgresql import POSTGRESQL_TOP_SQL_METRICS, PostgreSQLAdapter
from dba.sqlserver import SQLServerAdapter
from dba.top_sql import (
    TOP_LIMIT,
    CollectionScope,
    CollectionStatus,
    Completeness,
    DataVisibility,
    IdentityKind,
    Limitation,
    MetricScope,
    MetricSemantics,
    NativeSQLIdentity,
    RankingDirection,
    TopSQLItem,
    TopSQLResult,
)
from dba.top_sql_profiles import (
    MYSQL_PARSER_CONTRACT_ID,
    MYSQL_SQL_PROFILE_ID,
    ORACLE_PARSER_CONTRACT_ID,
    ORACLE_SQL_PROFILE_ID,
    PARSER_CONTRACT_ID,
    POSTGRESQL_PARSER_CONTRACT_ID,
    POSTGRESQL_SQL_PROFILE_ID,
    SQL_PROFILE_ID,
)
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus

METRICS: dict[str, tuple[str, str]] = {
    "cpu_time": ("total_cpu_time", "microseconds"),
    "logical_reads": ("total_logical_reads", "pages"),
    "physical_reads": ("total_physical_reads", "pages"),
    "execution_count": ("execution_count", "executions"),
    "elapsed_time": ("total_elapsed_time", "microseconds"),
    "average_cpu_time": ("average_cpu_time", "microseconds/execution"),
    "average_elapsed_time": ("average_elapsed_time", "microseconds/execution"),
}
SQL_TEXT_LIMIT = 4096
POSTGRESQL_METRICS: dict[str, tuple[str, str]] = {
    "total_exec_time": ("total_exec_time", "milliseconds"),
    "mean_exec_time": ("mean_exec_time", "milliseconds/execution"),
    "calls": ("calls", "executions"),
    "shared_blks_read": ("shared_blks_read", "blocks"),
    "shared_blks_hit": ("shared_blks_hit", "blocks"),
    "temp_blks_written": ("temp_blks_written", "blocks"),
    "wal_bytes": ("wal_bytes", "bytes"),
}
MYSQL_METRICS: dict[str, tuple[str, str]] = {
    "sum_timer_wait": ("sum_timer_wait", "milliseconds"),
    "avg_timer_wait": ("avg_timer_wait", "milliseconds/execution"),
    "count_star": ("count_star", "executions"),
    "sum_rows_examined": ("sum_rows_examined", "rows"),
    "sum_rows_sent": ("sum_rows_sent", "rows"),
    "sum_created_tmp_disk_tables": ("sum_created_tmp_disk_tables", "tables"),
    "sum_no_index_used": ("sum_no_index_used", "executions"),
}
MYSQL_TIMER_METRICS = frozenset({"sum_timer_wait", "avg_timer_wait"})
ORACLE_METRICS: dict[str, tuple[str, str]] = {
    "cpu_time": ("cpu_time", "microseconds"),
    "elapsed_time": ("elapsed_time", "microseconds"),
    "buffer_gets": ("buffer_gets", "gets"),
    "disk_reads": ("disk_reads", "reads"),
    "executions": ("executions", "executions"),
}


def _numeric(value: Any) -> int | float:
    if type(value) is int:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolkitError("InvalidTopSQLResult", "SQL Server returned an invalid ranking value.") from exc
    if not number.is_finite():
        raise ToolkitError("InvalidTopSQLResult", "SQL Server returned a non-finite ranking value.")
    return int(number) if number == number.to_integral_value() else float(number)


def _mysql_decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise InvalidOperation
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise InvalidOperation
    return number


def _mysql_numeric(value: Any, metric: str) -> tuple[Decimal, int | float]:
    try:
        raw = _mysql_decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ToolkitError("InvalidTopSQLResult", "MySQL returned an invalid ranking value.") from exc
    displayed = raw / Decimal(1_000_000_000) if metric in MYSQL_TIMER_METRICS else raw
    if metric not in MYSQL_TIMER_METRICS and displayed != displayed.to_integral_value():
        raise ToolkitError("InvalidTopSQLResult", "MySQL returned a non-integral counter value.")
    result: int | float = int(displayed) if displayed == displayed.to_integral_value() else float(displayed)
    if type(result) is float and not math.isfinite(result):
        raise ToolkitError("InvalidTopSQLResult", "MySQL returned a non-finite ranking value.")
    return raw, result


def _mysql_flag(value: Any) -> bool | None:
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    return None


def _mysql_counter(value: Any) -> int | None:
    try:
        number = _mysql_decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


class SQLServerTopSQLCollector:
    def __init__(self, adapter: SQLServerAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.SQLSERVER or profile.feature != "top-sql"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or profile.sql_profile_id != SQL_PROFILE_ID
                or profile.parser_contract_id != PARSER_CONTRACT_ID):
            raise ToolkitError("VersionVerificationRequired",
                               "Top SQL collection requires a verified supported SQL Server profile.")
        self.profile = profile

    def collect(self, instance: DatabaseInstance, metric: str = "cpu_time", top_n: int = 10) -> TopSQLResult:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        if metric not in METRICS:
            raise ToolkitError("InvalidMetric", "Unsupported SQL Server Top SQL ranking metric.")
        if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
            raise ToolkitError("InvalidTopN", f"Top N must be between 1 and {TOP_LIMIT}.")
        script = self.adapter.registry.get("top_sql", DatabaseType.SQLSERVER)
        metadata = script.metadata
        if (metadata.feature != "top-sql" or self.profile.detected_version_family not in metadata.version_families
                or self.profile.runtime_variant not in metadata.runtime_variants):
            raise ToolkitError("ProfileRegistryMismatch", "Top SQL Registry route does not match the resolved profile.")
        result = self.adapter.execute_readonly(script, top_sql_parameters=(top_n, metric))
        now = result.finished_at or datetime.now(timezone.utc)
        metric_name, unit = METRICS[metric]
        limitations: list[Limitation] = [Limitation.COUNTER_EPOCH_UNKNOWN,
                                         Limitation.CACHE_EVICTED,
                                         Limitation.RECOMPILE_RESETS_COUNTERS]
        if metric in {"cpu_time", "elapsed_time", "average_cpu_time", "average_elapsed_time"}:
            limitations.append(Limitation.METRIC_RESOLUTION_LIMITED)
        if metric == "physical_reads":
            limitations.append(Limitation.NOT_DEVICE_IO)
        if not result.success:
            error = (result.error_type or "").lower()
            permission_failure = "permission" in error or "denied" in error
            limitation = (Limitation.PERMISSION_DENIED if permission_failure else
                          Limitation.COLLECTOR_TIMEOUT if "timeout" in error else
                          Limitation.COLLECTION_FAILED)
            return TopSQLResult(instance.id, DatabaseType.SQLSERVER,
                self.profile.detected_raw_version or "UNKNOWN", SQL_PROFILE_ID, now, metric,
                RankingDirection.DESCENDING, metric_name, unit, MetricSemantics.CUMULATIVE,
                MetricScope.SQL_STATEMENT, "sys.dm_exec_query_stats",
                DataVisibility.UNAVAILABLE, Completeness.UNKNOWN,
                CollectionStatus.PERMISSION_DENIED if permission_failure else CollectionStatus.FAILED,
                limitations=(limitation,), collection_scope=CollectionScope.INSTANCE,
                database_error=result.database_error)
        rows = result.rows
        partial = bool(result.truncated)
        items: list[TopSQLItem] = []
        previous_value: int | float | None = None
        for row in rows:
            if row.get("sql_text_available") != 1:
                partial = True
                if Limitation.SOURCE_UNAVAILABLE not in limitations:
                    limitations.append(Limitation.SOURCE_UNAVAILABLE)
            handle = row.get("sql_handle")
            start = row.get("statement_start_offset")
            end = row.get("statement_end_offset")
            if not isinstance(handle, str) or type(start) is not int or type(end) is not int:
                partial = True
                if Limitation.IDENTITY_AMBIGUOUS not in limitations:
                    limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                continue
            fields: dict[str, str | int | None] = {"sql_handle": handle,
                                                   "statement_start_offset": start,
                                                   "statement_end_offset": end}
            if type(row.get("database_id")) is int:
                fields["database_id"] = row["database_id"]
            for key in ("query_hash",):
                if isinstance(row.get(key), str) and row[key]:
                    fields[key] = row[key]
            sql_identity = NativeSQLIdentity(DatabaseType.SQLSERVER, IdentityKind.STATEMENT, fields)
            plan_fields: dict[str, str | int | None] = {}
            if isinstance(row.get("plan_handle"), str) and row["plan_handle"]:
                plan_fields["plan_handle"] = row["plan_handle"]
            if isinstance(row.get("query_plan_hash"), str) and row["query_plan_hash"]:
                plan_fields["query_plan_hash"] = row["query_plan_hash"]
            if type(row.get("plan_generation_num")) is int:
                plan_fields["plan_generation_num"] = row["plan_generation_num"]
            plan_identity = (NativeSQLIdentity(DatabaseType.SQLSERVER, IdentityKind.PLAN, plan_fields)
                             if plan_fields else None)
            value = _numeric(row.get("ranking_value"))
            if previous_value is not None and value > previous_value:
                raise ToolkitError("InvalidTopSQLOrder", "SQL Server Top SQL rows are not in declared rank order.")
            previous_value = value
            items.append(TopSQLItem(sql_identity, value, rank=len(items) + 1, plan_identity=plan_identity))
        if result.truncated:
            limitations.append(Limitation.TRUNCATED)
        return TopSQLResult(instance.id, DatabaseType.SQLSERVER,
            self.profile.detected_raw_version or "UNKNOWN", SQL_PROFILE_ID, now, metric,
            RankingDirection.DESCENDING, metric_name, unit, MetricSemantics.CUMULATIVE,
            MetricScope.SQL_STATEMENT, "sys.dm_exec_query_stats",
            DataVisibility.PARTIAL if partial else DataVisibility.COMPLETE,
            Completeness.PARTIAL if partial else Completeness.COMPLETE, CollectionStatus.COLLECTED,
            tuple(items), tuple(dict.fromkeys(limitations)), CollectionScope.INSTANCE)


def _postgresql_numeric(value: Any, metric: str) -> int | float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolkitError("InvalidTopSQLResult", "PostgreSQL returned an invalid ranking value.") from exc
    if not number.is_finite() or number < 0:
        raise ToolkitError("InvalidTopSQLResult", "PostgreSQL returned a non-finite or negative ranking value.")
    integral_metric = metric in {
        "calls", "shared_blks_read", "shared_blks_hit", "temp_blks_written", "wal_bytes",
    }
    if integral_metric:
        if number != number.to_integral_value():
            raise ToolkitError("InvalidTopSQLResult", "PostgreSQL returned a fractional count or byte total.")
        return int(number)
    try:
        value_as_float = float(number)
    except OverflowError as exc:
        raise ToolkitError("InvalidTopSQLResult", "PostgreSQL returned an out-of-range execution time.") from exc
    if not math.isfinite(value_as_float):
        raise ToolkitError("InvalidTopSQLResult", "PostgreSQL returned a non-finite execution time.")
    return value_as_float


class PostgreSQLTopSQLCollector:
    """Read bounded cumulative statement aggregates from optional pg_stat_statements."""

    def __init__(self, adapter: PostgreSQLAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.POSTGRESQL or profile.feature != "top-sql"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or profile.sql_profile_id != POSTGRESQL_SQL_PROFILE_ID
                or profile.parser_contract_id != POSTGRESQL_PARSER_CONTRACT_ID):
            raise ToolkitError("VersionVerificationRequired",
                               "PostgreSQL Top SQL requires a verified PostgreSQL 14-18 profile.")
        self.profile = profile

    def _result(self, instance: DatabaseInstance, metric: str, status: CollectionStatus,
                visibility: DataVisibility,
                limitations: tuple[Limitation, ...], *, metadata: dict[str, Any] | None = None,
                completeness: Completeness = Completeness.UNKNOWN,
                timestamp: datetime | None = None,
                database_error: dict[str, Any] | None = None) -> TopSQLResult:
        metric_name, unit = POSTGRESQL_METRICS[metric]
        return TopSQLResult(
            instance.id, DatabaseType.POSTGRESQL,
            self.profile.detected_raw_version or "UNKNOWN", POSTGRESQL_SQL_PROFILE_ID,
            timestamp or datetime.now(timezone.utc), metric, RankingDirection.DESCENDING,
            metric_name, unit, MetricSemantics.CUMULATIVE, MetricScope.SQL_STATEMENT,
            "pg_stat_statements", visibility, completeness, status,
            limitations=limitations, collection_scope=CollectionScope.INSTANCE,
            source_metadata=metadata or {}, database_error=database_error,
        )

    @staticmethod
    def _query_failure(result: Any) -> tuple[CollectionStatus, DataVisibility, Limitation]:
        error = (result.error_type or "").casefold()
        if "denied" in error or "permission" in error:
            return CollectionStatus.PERMISSION_DENIED, DataVisibility.UNAVAILABLE, Limitation.PERMISSION_DENIED
        limitation = (Limitation.COLLECTOR_TIMEOUT if "timeout" in error
                      else Limitation.COLLECTION_FAILED)
        return CollectionStatus.FAILED, DataVisibility.UNAVAILABLE, limitation

    def collect(self, instance: DatabaseInstance, metric: str = "total_exec_time",
                top_n: int = 10) -> TopSQLResult:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        if metric not in POSTGRESQL_METRICS or metric not in POSTGRESQL_TOP_SQL_METRICS:
            raise ToolkitError("InvalidMetric", "Unsupported PostgreSQL Top SQL ranking metric.")
        if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
            raise ToolkitError("InvalidTopN", f"Top N must be between 1 and {TOP_LIMIT}.")

        registry = self.adapter.registry
        scripts = {name: registry.get(name, DatabaseType.POSTGRESQL)
                   for name in ("top_sql_source", "top_sql_info", "top_sql")}
        for name, script in scripts.items():
            script_metadata = script.metadata
            if (script_metadata.feature != "top-sql"
                    or self.profile.detected_version_family not in script_metadata.version_families
                    or self.profile.runtime_variant not in script_metadata.runtime_variants):
                raise ToolkitError("ProfileRegistryMismatch",
                                   f"PostgreSQL Top SQL Registry route does not match {name}.")

        source = self.adapter.execute_readonly(scripts["top_sql_source"])
        if not source.success:
            status, visibility, limitation = self._query_failure(source)
            return self._result(instance, metric, status, visibility, (limitation,),
                                timestamp=source.finished_at, database_error=source.database_error)
        if source.truncated or len(source.rows) != 1:
            return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                (Limitation.SOURCE_UNAVAILABLE,), timestamp=source.finished_at)
        source_row = source.rows[0]
        extension_installed = source_row.get("extension_installed")
        module_preloaded = source_row.get("module_preloaded")
        schema_name = source_row.get("schema_name")
        if (extension_installed is not True or module_preloaded is not True
                or source_row.get("tracking_mode") == "none"):
            return self._result(instance, metric, CollectionStatus.SOURCE_NOT_CONFIGURED,
                                DataVisibility.NOT_CONFIGURED,
                                (Limitation.SOURCE_NOT_CONFIGURED,), timestamp=source.finished_at)
        if schema_name != "public":
            return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                (Limitation.SOURCE_UNAVAILABLE,), timestamp=source.finished_at)
        if source_row.get("statements_view_available") is not True:
            return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                (Limitation.SOURCE_UNAVAILABLE,), timestamp=source.finished_at)

        limitations: list[Limitation] = []
        source_error: dict[str, Any] | None = None

        metadata: dict[str, Any] = {
            "extension_version": source_row.get("extension_version"),
            "compute_query_id": source_row.get("compute_query_id"),
            "tracking_mode": source_row.get("tracking_mode"),
            "queryid_stability": "NOT_GUARANTEED_ACROSS_MAJOR_VERSIONS",
            "planning_time_included": False,
            "stats_reset": None,
            "dealloc": None,
            "identity_unavailable_rows": 0,
        }
        partial = source_row.get("info_view_available") is not True
        if partial:
            limitations.extend((Limitation.COUNTER_EPOCH_UNKNOWN, Limitation.SOURCE_UNAVAILABLE))
        else:
            info = self.adapter.execute_readonly(scripts["top_sql_info"])
            if info.success and not info.truncated and len(info.rows) == 1:
                info_row = info.rows[0]
                metadata["stats_reset"] = info_row.get("stats_reset")
                dealloc = info_row.get("dealloc")
                if type(dealloc) is int and dealloc >= 0:
                    metadata["dealloc"] = dealloc
                    if dealloc > 0:
                        limitations.append(Limitation.CACHE_EVICTED)
                else:
                    partial = True
                    limitations.extend((Limitation.COUNTER_EPOCH_UNKNOWN, Limitation.SOURCE_UNAVAILABLE))
                if metadata["stats_reset"] is None:
                    partial = True
                    limitations.append(Limitation.COUNTER_EPOCH_UNKNOWN)
            else:
                partial = True
                limitations.extend((Limitation.COUNTER_EPOCH_UNKNOWN, Limitation.SOURCE_UNAVAILABLE))
                if "denied" in (info.error_type or "").casefold() or "permission" in (
                        info.error_type or "").casefold():
                        limitations.append(Limitation.PERMISSION_DENIED)
                source_error = info.database_error

        data = self.adapter.execute_top_sql_readonly(scripts["top_sql"], top_n, metric)
        timestamp = data.finished_at or datetime.now(timezone.utc)
        if not data.success:
            if data.error_type == "TopSQLSourceNotConfigured":
                return self._result(instance, metric, CollectionStatus.SOURCE_NOT_CONFIGURED,
                                DataVisibility.NOT_CONFIGURED,
                                    (Limitation.SOURCE_NOT_CONFIGURED,), metadata=metadata,
                                    timestamp=timestamp)
            status, visibility, limitation = self._query_failure(data)
            return self._result(instance, metric, status, visibility,
                                tuple(dict.fromkeys((*limitations, limitation))), metadata=metadata,
                                timestamp=timestamp, database_error=data.database_error)
        if len(data.rows) > top_n:
            raise ToolkitError("InvalidTopSQLResult", "PostgreSQL Top SQL Registry exceeded the requested Top N.")

        if str(metadata.get("compute_query_id") or "").casefold() == "off":
            partial = True
            limitations.append(Limitation.IDENTITY_AMBIGUOUS)
        if data.truncated:
            partial = True
            limitations.append(Limitation.TRUNCATED)

        items: list[TopSQLItem] = []
        previous_value: int | float | None = None
        previous_identity: tuple[int, int, int, int] | None = None
        for row in data.rows:
            value = _postgresql_numeric(row.get("ranking_value"), metric)
            if previous_value is not None and value > previous_value:
                raise ToolkitError("InvalidTopSQLOrder",
                                   "PostgreSQL Top SQL rows are not in declared rank order.")
            dbid, userid, queryid, toplevel = (row.get("dbid"), row.get("userid"),
                                                row.get("queryid"), row.get("toplevel"))
            if (type(dbid) is not int or type(userid) is not int or type(queryid) is not int
                    or type(toplevel) is not bool):
                partial = True
                metadata["identity_unavailable_rows"] += 1
                if Limitation.IDENTITY_AMBIGUOUS not in limitations:
                    limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                previous_value = value
                previous_identity = None
                continue
            identity_key = (dbid, userid, queryid, int(toplevel))
            if (previous_value is not None and value == previous_value and previous_identity is not None
                    and identity_key < previous_identity):
                raise ToolkitError("InvalidTopSQLOrder",
                                   "PostgreSQL Top SQL tie order is not deterministic by native identity.")
            identity = NativeSQLIdentity(DatabaseType.POSTGRESQL, IdentityKind.STATEMENT, {
                "dbid": dbid, "userid": userid, "queryid": queryid,
                "toplevel": int(toplevel),
            })
            items.append(TopSQLItem(identity, value, rank=len(items) + 1))
            previous_value = value
            previous_identity = identity_key
        if metric == "shared_blks_read":
            limitations.append(Limitation.NOT_DEVICE_IO)
        return TopSQLResult(
            instance.id, DatabaseType.POSTGRESQL,
            self.profile.detected_raw_version or "UNKNOWN", POSTGRESQL_SQL_PROFILE_ID,
            timestamp, metric, RankingDirection.DESCENDING, POSTGRESQL_METRICS[metric][0],
            POSTGRESQL_METRICS[metric][1], MetricSemantics.CUMULATIVE,
            MetricScope.SQL_STATEMENT, "pg_stat_statements",
            DataVisibility.PARTIAL if partial else DataVisibility.COMPLETE,
            Completeness.PARTIAL if partial else Completeness.COMPLETE, CollectionStatus.COLLECTED,
            tuple(items), tuple(dict.fromkeys(limitations)), CollectionScope.INSTANCE,
            metadata, source_error,
        )


class MySQLTopSQLCollector:
    """Read bounded cumulative digest statistics from Performance Schema."""

    def __init__(self, adapter: MySQLAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.MYSQL or profile.feature != "top-sql"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or profile.sql_profile_id != MYSQL_SQL_PROFILE_ID
                or profile.parser_contract_id != MYSQL_PARSER_CONTRACT_ID):
            raise ToolkitError("VersionVerificationRequired",
                               "MySQL Top SQL requires a verified MySQL 5.7, 8.0, or 8.4 profile.")
        self.profile = profile

    def _result(self, instance: DatabaseInstance, metric: str, status: CollectionStatus,
                visibility: DataVisibility,
                limitations: tuple[Limitation, ...], *, metadata: dict[str, Any] | None = None,
                completeness: Completeness = Completeness.UNKNOWN,
                timestamp: datetime | None = None,
                items: tuple[TopSQLItem, ...] = (),
                database_error: dict[str, Any] | None = None) -> TopSQLResult:
        metric_name, unit = MYSQL_METRICS[metric]
        return TopSQLResult(
            instance.id, DatabaseType.MYSQL,
            self.profile.detected_raw_version or "UNKNOWN", MYSQL_SQL_PROFILE_ID,
            timestamp or datetime.now(timezone.utc), metric, RankingDirection.DESCENDING,
            metric_name, unit, MetricSemantics.CUMULATIVE, MetricScope.DIGEST,
            "performance_schema.events_statements_summary_by_digest", visibility,
            completeness, status, items, limitations, CollectionScope.INSTANCE, metadata or {}, database_error,
        )

    @staticmethod
    def _query_failure(result: Any) -> tuple[CollectionStatus, DataVisibility, Limitation]:
        error = (result.error_type or "").casefold()
        if "denied" in error or "permission" in error:
            return (CollectionStatus.PERMISSION_DENIED, DataVisibility.UNAVAILABLE, Limitation.PERMISSION_DENIED)
        if "capability" in error or "unavailable" in error or "notconfigured" in error:
            return (CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE, Limitation.SOURCE_UNAVAILABLE)
        limitation = (Limitation.COLLECTOR_TIMEOUT if "timeout" in error
                      else Limitation.COLLECTION_FAILED)
        return CollectionStatus.FAILED, DataVisibility.UNAVAILABLE, limitation

    def collect(self, instance: DatabaseInstance, metric: str = "sum_timer_wait",
                top_n: int = 10) -> TopSQLResult:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        if not isinstance(metric, str) or metric not in MYSQL_METRICS or metric not in MYSQL_TOP_SQL_METRICS:
            raise ToolkitError("InvalidMetric", "Unsupported MySQL Top SQL ranking metric.")
        if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
            raise ToolkitError("InvalidTopN", f"Top N must be between 1 and {TOP_LIMIT}.")

        registry = self.adapter.registry
        scripts = {name: registry.get(name, DatabaseType.MYSQL)
                   for name in ("top_sql_source", "top_sql_info", "top_sql")}
        expected_rows = {"top_sql_source": 2, "top_sql_info": 2, "top_sql": TOP_LIMIT + 1}
        for name, script in scripts.items():
            script_metadata = script.metadata
            if (script_metadata.feature != "top-sql"
                    or self.profile.detected_version_family not in script_metadata.version_families
                    or self.profile.runtime_variant not in script_metadata.runtime_variants
                    or script_metadata.risk != "READ_ONLY" or not script_metadata.reviewed
                    or script_metadata.max_rows != expected_rows[name]):
                raise ToolkitError("ProfileRegistryMismatch",
                                   f"MySQL Top SQL Registry route does not match {name}.")

        source = self.adapter.execute_readonly(scripts["top_sql_source"])
        if not source.success:
            status, visibility, limitation = self._query_failure(source)
            return self._result(instance, metric, status, visibility, (limitation,),
                                timestamp=source.finished_at, database_error=source.database_error)
        if source.truncated or len(source.rows) != 1:
            return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                (Limitation.SOURCE_UNAVAILABLE,), timestamp=source.finished_at)

        source_row = source.rows[0]
        performance_schema = _mysql_flag(source_row.get("performance_schema_enabled"))
        digest_consumer = _mysql_flag(source_row.get("statements_digest_enabled"))
        digest_table = _mysql_flag(source_row.get("digest_table_available"))
        instrument_count = _mysql_counter(source_row.get("statement_instrument_count"))
        disabled_count = _mysql_counter(source_row.get("statement_instrument_disabled_count"))
        untimed_count = _mysql_counter(source_row.get("statement_instrument_untimed_count"))
        metadata: dict[str, Any] = {
            "digest_lost": None,
            "digest_null_bucket_present": None,
            "statement_instrument_count": instrument_count,
            "statement_instrument_disabled_count": disabled_count,
            "statement_instrument_untimed_count": untimed_count,
        }
        if performance_schema is False or digest_consumer is False:
            return self._result(instance, metric, CollectionStatus.SOURCE_NOT_CONFIGURED,
                                DataVisibility.NOT_CONFIGURED,
                                (Limitation.SOURCE_NOT_CONFIGURED,), metadata=metadata,
                                timestamp=source.finished_at)
        if (performance_schema is not True or digest_consumer is not True or digest_table is not True
                or instrument_count is None or instrument_count < 1
                or disabled_count is None or disabled_count > instrument_count
                or untimed_count is None or untimed_count > instrument_count):
            return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                (Limitation.SOURCE_UNAVAILABLE,), metadata=metadata,
                                timestamp=source.finished_at)
        if disabled_count >= instrument_count:
            return self._result(instance, metric, CollectionStatus.SOURCE_NOT_CONFIGURED,
                                DataVisibility.NOT_CONFIGURED,
                                (Limitation.SOURCE_NOT_CONFIGURED,), metadata=metadata,
                                timestamp=source.finished_at)

        limitations: list[Limitation] = [Limitation.COUNTER_EPOCH_UNKNOWN]
        source_error: dict[str, Any] | None = None
        partial = disabled_count > 0 or (metric in MYSQL_TIMER_METRICS and untimed_count > 0)
        if partial:
            limitations.append(Limitation.SOURCE_COVERAGE_INCOMPLETE)

        info = self.adapter.execute_readonly(scripts["top_sql_info"])
        if info.success and not info.truncated and len(info.rows) == 1:
            info_row = info.rows[0]
            digest_lost = _mysql_counter(info_row.get("digest_lost"))
            null_bucket = _mysql_flag(info_row.get("digest_null_bucket_present"))
            metadata["digest_lost"] = digest_lost
            metadata["digest_null_bucket_present"] = null_bucket
            if digest_lost is None or null_bucket is None:
                partial = True
                limitations.append(Limitation.SOURCE_UNAVAILABLE)
            elif digest_lost > 0:
                partial = True
                limitations.append(Limitation.SOURCE_COVERAGE_INCOMPLETE)
            if null_bucket is True:
                partial = True
                limitations.extend((Limitation.IDENTITY_AMBIGUOUS,
                                    Limitation.SOURCE_COVERAGE_INCOMPLETE))
        else:
            partial = True
            limitations.append(Limitation.SOURCE_UNAVAILABLE)
            source_error = info.database_error
            error = (info.error_type or "").casefold()
            if "denied" in error or "permission" in error:
                limitations.append(Limitation.PERMISSION_DENIED)

        data = self.adapter.execute_top_sql_readonly(scripts["top_sql"], top_n, metric)
        timestamp = data.finished_at or datetime.now(timezone.utc)
        if not data.success:
            status, visibility, limitation = self._query_failure(data)
            return self._result(instance, metric, status, visibility,
                                tuple(dict.fromkeys((*limitations, limitation))), metadata=metadata,
                                timestamp=timestamp, database_error=data.database_error)
        if len(data.rows) > top_n:
            raise ToolkitError("InvalidTopSQLResult",
                               "MySQL Top SQL Registry exceeded the requested Top N.")
        if data.truncated:
            partial = True
            limitations.append(Limitation.TRUNCATED)

        items: list[TopSQLItem] = []
        seen: set[tuple[str | None, str]] = set()
        previous_raw: Decimal | None = None
        previous_identity: tuple[bool, str, str] | None = None
        for row in data.rows:
            try:
                raw_value, value = _mysql_numeric(row.get("ranking_value"), metric)
            except ToolkitError:
                partial = True
                limitations.append(Limitation.SOURCE_UNAVAILABLE)
                continue
            digest = row.get("digest")
            schema_name = row.get("schema_name")
            if not isinstance(digest, str) or not digest:
                partial = True
                limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                continue
            if schema_name is not None and (not isinstance(schema_name, str) or not schema_name):
                partial = True
                limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                continue
            key = (schema_name, digest)
            if key in seen:
                raise ToolkitError("InvalidTopSQLIdentity", "MySQL Top SQL returned a duplicate digest identity.")
            identity_key = (schema_name is not None, schema_name or "", digest)
            if previous_raw is not None and raw_value > previous_raw:
                raise ToolkitError("InvalidTopSQLOrder", "MySQL Top SQL rows are not in declared rank order.")
            if (previous_raw == raw_value and previous_identity is not None
                    and identity_key < previous_identity):
                raise ToolkitError("InvalidTopSQLOrder",
                                   "MySQL Top SQL tie order is not deterministic by native identity.")
            fields: dict[str, str | int | None] = {"digest": digest, "schema_name": schema_name}
            identity = NativeSQLIdentity(DatabaseType.MYSQL, IdentityKind.STATEMENT, fields)
            items.append(TopSQLItem(identity, value, rank=len(items) + 1))
            seen.add(key)
            previous_raw = raw_value
            previous_identity = identity_key

        if data.truncated:
            limitations.append(Limitation.TRUNCATED)
        return self._result(
            instance, metric, CollectionStatus.COLLECTED,
            DataVisibility.PARTIAL if partial else DataVisibility.COMPLETE,
            tuple(dict.fromkeys(limitations)), metadata=metadata,
            completeness=Completeness.PARTIAL if partial else Completeness.COMPLETE,
            timestamp=timestamp, items=tuple(items), database_error=source_error,
        )


def _oracle_counter(value: Any) -> int:
    if value is None or isinstance(value, bool):
        raise ToolkitError("InvalidTopSQLResult", "Oracle returned an unavailable cumulative metric.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolkitError("InvalidTopSQLResult", "Oracle returned an invalid cumulative metric.") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ToolkitError("InvalidTopSQLResult", "Oracle returned an invalid cumulative metric.")
    return int(number)


class OracleTopSQLCollector:
    """Read bounded V$SQL cumulative counters at native child-cursor grain."""

    def __init__(self, adapter: OracleAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.ORACLE or profile.feature != "top-sql"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or profile.sql_profile_id != ORACLE_SQL_PROFILE_ID
                or profile.parser_contract_id != ORACLE_PARSER_CONTRACT_ID):
            raise ToolkitError("VersionVerificationRequired",
                               "Oracle Top SQL requires a verified Oracle 11gR2 or newer profile.")
        self.profile = profile

    def _result(self, instance: DatabaseInstance, metric: str, status: CollectionStatus,
                visibility: DataVisibility,
                completeness: Completeness, limitations: tuple[Limitation, ...], *,
                timestamp: datetime | None = None, items: tuple[TopSQLItem, ...] = (),
                metadata: dict[str, Any] | None = None,
                database_error: dict[str, Any] | None = None) -> TopSQLResult:
        modern = self.profile.detected_version_family != "11gr2"
        result_limitations = list(limitations)
        if metric == "elapsed_time" and Limitation.CUMULATIVE_ELAPSED_NOT_SINGLE_EXECUTION_WALL_TIME not in result_limitations:
            result_limitations.append(Limitation.CUMULATIVE_ELAPSED_NOT_SINGLE_EXECUTION_WALL_TIME)
        return TopSQLResult(
            instance.id, DatabaseType.ORACLE,
            self.profile.detected_raw_version or "UNKNOWN", ORACLE_SQL_PROFILE_ID,
            timestamp or datetime.now(timezone.utc), metric, RankingDirection.DESCENDING,
            ORACLE_METRICS[metric][0], ORACLE_METRICS[metric][1], MetricSemantics.CUMULATIVE,
            MetricScope.CHILD_CURSOR, "V$SQL child cursor", visibility,
            completeness, status, items, tuple(result_limitations),
            CollectionScope.DATABASE if modern else CollectionScope.INSTANCE,
            metadata or {}, database_error,
        )

    @staticmethod
    def _query_failure(result: Any) -> tuple[CollectionStatus, DataVisibility, Completeness, Limitation]:
        error = (result.error_type or "").casefold()
        if "denied" in error or "permission" in error:
            return (CollectionStatus.PERMISSION_DENIED, DataVisibility.UNAVAILABLE,
                    Completeness.UNKNOWN, Limitation.PERMISSION_DENIED)
        if "capability" in error or "unavailable" in error:
            return (CollectionStatus.SOURCE_UNAVAILABLE, DataVisibility.UNAVAILABLE,
                    Completeness.UNKNOWN, Limitation.SOURCE_UNAVAILABLE)
        limitation = (Limitation.COLLECTOR_TIMEOUT if "timeout" in error
                      else Limitation.COLLECTION_FAILED)
        return (CollectionStatus.FAILED, DataVisibility.UNAVAILABLE,
                Completeness.UNKNOWN, limitation)

    def collect(self, instance: DatabaseInstance, metric: str = "cpu_time",
                top_n: int = 10) -> TopSQLResult:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        if not isinstance(metric, str) or metric not in ORACLE_METRICS or metric not in ORACLE_TOP_SQL_METRICS:
            raise ToolkitError("InvalidMetric", "Unsupported Oracle Top SQL ranking metric.")
        if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
            raise ToolkitError("InvalidTopN", f"Top N must be between 1 and {TOP_LIMIT}.")

        instance_id = self.adapter.instance_id
        if type(instance_id) is not int or instance_id < 1:
            return self._result(
                instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNKNOWN, Completeness.UNKNOWN, (Limitation.IDENTITY_AMBIGUOUS,),
            )

        family = self.profile.detected_version_family
        script_name = "top_sql_11g" if family == "11gr2" else "top_sql_modern"
        script = self.adapter.registry.get(script_name, DatabaseType.ORACLE)
        metadata = script.metadata
        if (metadata.feature != "top-sql" or family not in metadata.version_families
                or self.profile.runtime_variant not in metadata.runtime_variants
                or metadata.risk != "READ_ONLY" or not metadata.reviewed
                or metadata.max_rows != TOP_LIMIT + 1):
            raise ToolkitError("ProfileRegistryMismatch", "Oracle Top SQL Registry route does not match the profile.")

        source_metadata: dict[str, Any] = {"instance_id": instance_id}
        con_id: int | None = None
        if family != "11gr2":
            context_script = self.adapter.registry.get("top_sql_container", DatabaseType.ORACLE)
            context_metadata = context_script.metadata
            if (context_metadata.feature != "top-sql" or family not in context_metadata.version_families
                    or self.profile.runtime_variant not in context_metadata.runtime_variants
                    or context_metadata.risk != "READ_ONLY" or not context_metadata.reviewed
                    or context_metadata.max_rows != 2):
                raise ToolkitError("ProfileRegistryMismatch", "Oracle Top SQL container route does not match the profile.")
            context = self.adapter.execute_readonly(context_script)
            if not context.success:
                status, visibility, completeness, limitation = self._query_failure(context)
                return self._result(instance, metric, status, visibility, completeness,
                                    (limitation,), timestamp=context.finished_at, metadata=source_metadata,
                                    database_error=context.database_error)
            if context.truncated or len(context.rows) != 1:
                return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                    Completeness.UNKNOWN, (Limitation.SOURCE_UNAVAILABLE,),
                                    timestamp=context.finished_at, metadata=source_metadata)
            try:
                con_id = _oracle_counter(context.rows[0].get("con_id"))
            except ToolkitError:
                return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                    Completeness.UNKNOWN, (Limitation.IDENTITY_AMBIGUOUS,),
                                    timestamp=context.finished_at, metadata=source_metadata)
            if con_id < 0:
                return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                    Completeness.UNKNOWN, (Limitation.IDENTITY_AMBIGUOUS,),
                                    timestamp=context.finished_at, metadata=source_metadata)
            con_name = context.rows[0].get("con_name")
            if ((con_name is not None and (not isinstance(con_name, str) or not con_name))
                    or (con_id > 0 and con_name is None)):
                return self._result(instance, metric, CollectionStatus.SOURCE_UNAVAILABLE,
                                DataVisibility.UNAVAILABLE,
                                    Completeness.UNKNOWN, (Limitation.IDENTITY_AMBIGUOUS,),
                                    timestamp=context.finished_at, metadata=source_metadata)
            source_metadata.update({"container_id": con_id, "container_name": con_name})

        result = self.adapter.execute_top_sql_readonly(script, top_n, metric, con_id=con_id)
        timestamp = result.finished_at or datetime.now(timezone.utc)
        if not result.success:
            status, visibility, completeness, limitation = self._query_failure(result)
            return self._result(instance, metric, status, visibility, completeness,
                                (limitation,), timestamp=timestamp, metadata=source_metadata,
                                database_error=result.database_error)
        if len(result.rows) > top_n:
            raise ToolkitError("InvalidTopSQLResult", "Oracle Top SQL Registry exceeded the requested Top N.")

        limitations: list[Limitation] = [Limitation.COUNTER_EPOCH_UNKNOWN]
        partial = result.truncated
        if result.truncated:
            limitations.append(Limitation.TRUNCATED)
        items: list[TopSQLItem] = []
        seen: set[tuple[int, int | None, str, int]] = set()
        previous_value: int | None = None
        previous_identity: tuple[str, int] | None = None
        null_metric_seen = False
        for row in result.rows:
            sql_id, child_number = row.get("sql_id"), row.get("child_number")
            try:
                child = _oracle_counter(child_number)
            except ToolkitError:
                partial = True
                limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                continue
            if not isinstance(sql_id, str) or not sql_id:
                partial = True
                limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                continue
            row_con_id: int | None = None
            if family != "11gr2":
                try:
                    row_con_id = _oracle_counter(row.get("con_id"))
                except ToolkitError:
                    partial = True
                    limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                    continue
                if row_con_id != con_id:
                    partial = True
                    limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                    continue
            identity_key = (instance_id, row_con_id, sql_id, child)
            if identity_key in seen:
                raise ToolkitError("InvalidTopSQLIdentity", "Oracle Top SQL returned a duplicate child-cursor identity.")
            seen.add(identity_key)

            raw_value = row.get("ranking_value")
            if raw_value is None:
                partial = True
                null_metric_seen = True
                limitations.append(Limitation.SOURCE_UNAVAILABLE)
                continue
            try:
                value = _oracle_counter(raw_value)
            except ToolkitError:
                partial = True
                limitations.append(Limitation.SOURCE_UNAVAILABLE)
                continue
            sort_identity = (sql_id, child)
            if null_metric_seen:
                raise ToolkitError("InvalidTopSQLOrder", "Oracle Top SQL placed a ranked value after an unavailable metric.")
            if previous_value is not None and value > previous_value:
                raise ToolkitError("InvalidTopSQLOrder", "Oracle Top SQL rows are not in declared rank order.")
            if value == previous_value and previous_identity is not None and sort_identity < previous_identity:
                raise ToolkitError("InvalidTopSQLOrder", "Oracle Top SQL tie order is not deterministic.")
            previous_value, previous_identity = value, sort_identity

            statement_fields: dict[str, str | int | None] = {
                "sql_id": sql_id, "child_number": child, "inst_id": instance_id,
            }
            if row_con_id is not None:
                statement_fields["con_id"] = row_con_id
            sql_identity = NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.STATEMENT, statement_fields)

            plan_identity = None
            raw_plan_hash = row.get("plan_hash_value")
            if raw_plan_hash is not None:
                try:
                    plan_hash = _oracle_counter(raw_plan_hash)
                except ToolkitError:
                    partial = True
                    limitations.append(Limitation.IDENTITY_AMBIGUOUS)
                else:
                    if plan_hash > 0:
                        plan_fields = dict(statement_fields)
                        plan_fields["plan_hash_value"] = plan_hash
                        plan_identity = NativeSQLIdentity(DatabaseType.ORACLE, IdentityKind.PLAN, plan_fields)
            items.append(TopSQLItem(sql_identity, value, rank=len(items) + 1, plan_identity=plan_identity))

        return self._result(
            instance, metric, CollectionStatus.COLLECTED,
                                DataVisibility.PARTIAL if partial else DataVisibility.COMPLETE,
            Completeness.PARTIAL if partial else Completeness.COMPLETE,
            tuple(dict.fromkeys(limitations)), timestamp=timestamp, items=tuple(items), metadata=source_metadata,
        )
