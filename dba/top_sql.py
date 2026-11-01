"""Shared, collector-neutral Top SQL result contract.

This module describes observations; it does not collect or infer database data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from dba.core import DatabaseType

TOP_LIMIT = 100


class MetricSemantics(StrEnum):
    CURRENT = "CURRENT"
    CUMULATIVE = "CUMULATIVE"
    INTERVAL_DELTA = "INTERVAL_DELTA"
    HISTORICAL = "HISTORICAL"


class RankingDirection(StrEnum):
    DESCENDING = "DESCENDING"
    ASCENDING = "ASCENDING"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class CollectionStatus(StrEnum):
    COLLECTED = "COLLECTED"
    SOURCE_NOT_CONFIGURED = "SOURCE_NOT_CONFIGURED"
    UNSUPPORTED = "UNSUPPORTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FAILED = "FAILED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class MetricValueStatus(StrEnum):
    OBSERVED = "OBSERVED"
    NOT_COLLECTED = "NOT_COLLECTED"
    UNSUPPORTED = "UNSUPPORTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SOURCE_NOT_CONFIGURED = "SOURCE_NOT_CONFIGURED"
    COLLECTION_FAILED = "COLLECTION_FAILED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class DataVisibility(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"


class MetricScope(StrEnum):
    REQUEST = "REQUEST"
    SQL_STATEMENT = "SQL_STATEMENT"
    DIGEST = "DIGEST"
    CHILD_CURSOR = "CHILD_CURSOR"
    DATABASE = "DATABASE"
    INSTANCE = "INSTANCE"
    HISTORICAL_STORE = "HISTORICAL_STORE"


class CollectionScope(StrEnum):
    """Population covered by a result set, distinct from each ranked item's granularity."""

    UNKNOWN = "UNKNOWN"
    DATABASE = "DATABASE"
    INSTANCE = "INSTANCE"


class IdentityKind(StrEnum):
    STATEMENT = "STATEMENT"
    PLAN = "PLAN"
    REQUEST = "REQUEST"


class Limitation(StrEnum):
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SOURCE_NOT_CONFIGURED = "SOURCE_NOT_CONFIGURED"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    COLLECTOR_TIMEOUT = "COLLECTOR_TIMEOUT"
    TRUNCATED = "TRUNCATED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    RESET_SUSPECTED = "RESET_SUSPECTED"
    CACHE_EVICTED = "CACHE_EVICTED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    COLLECTION_FAILED = "COLLECTION_FAILED"
    COUNTER_EPOCH_UNKNOWN = "COUNTER_EPOCH_UNKNOWN"
    RECOMPILE_RESETS_COUNTERS = "RECOMPILE_RESETS_COUNTERS"
    METRIC_RESOLUTION_LIMITED = "METRIC_RESOLUTION_LIMITED"
    SOURCE_COVERAGE_INCOMPLETE = "SOURCE_COVERAGE_INCOMPLETE"
    NOT_DEVICE_IO = "NOT_DEVICE_IO"
    CUMULATIVE_ELAPSED_NOT_SINGLE_EXECUTION_WALL_TIME = "CUMULATIVE_ELAPSED_NOT_SINGLE_EXECUTION_WALL_TIME"


_IDENTITY_FIELDS: dict[tuple[DatabaseType, IdentityKind], frozenset[str]] = {
    (DatabaseType.SQLSERVER, IdentityKind.STATEMENT): frozenset(
        {"sql_handle", "query_hash", "statement_start_offset", "statement_end_offset", "database_id"}),
    (DatabaseType.SQLSERVER, IdentityKind.PLAN): frozenset(
        {"plan_handle", "query_plan_hash", "plan_generation_num", "database_id"}),
    (DatabaseType.SQLSERVER, IdentityKind.REQUEST): frozenset({"session_id", "request_id", "database_id"}),
    (DatabaseType.POSTGRESQL, IdentityKind.STATEMENT): frozenset({"queryid", "dbid", "userid", "toplevel"}),
    (DatabaseType.POSTGRESQL, IdentityKind.REQUEST): frozenset({"pid", "backend_start", "dbid", "userid"}),
    (DatabaseType.MYSQL, IdentityKind.STATEMENT): frozenset({"digest", "schema_name"}),
    (DatabaseType.MYSQL, IdentityKind.REQUEST): frozenset({"processlist_id", "thread_id"}),
    (DatabaseType.ORACLE, IdentityKind.STATEMENT): frozenset(
        {"sql_id", "child_number", "inst_id", "con_id"}),
    (DatabaseType.ORACLE, IdentityKind.PLAN): frozenset(
        {"sql_id", "child_number", "plan_hash_value", "inst_id", "con_id"}),
    (DatabaseType.ORACLE, IdentityKind.REQUEST): frozenset({"inst_id", "sid", "serial#"}),
}
_IDENTITY_KEY_ALTERNATIVES: dict[tuple[DatabaseType, IdentityKind], tuple[frozenset[str], ...]] = {
    (DatabaseType.SQLSERVER, IdentityKind.STATEMENT):
        (frozenset({"sql_handle"}), frozenset({"query_hash"})),
    (DatabaseType.SQLSERVER, IdentityKind.PLAN):
        (frozenset({"plan_handle"}), frozenset({"query_plan_hash"})),
    (DatabaseType.SQLSERVER, IdentityKind.REQUEST): (frozenset({"session_id"}),),
    (DatabaseType.POSTGRESQL, IdentityKind.STATEMENT):
        (frozenset({"queryid", "dbid", "userid", "toplevel"}),),
    (DatabaseType.POSTGRESQL, IdentityKind.REQUEST): (frozenset({"pid", "backend_start"}),),
    (DatabaseType.MYSQL, IdentityKind.STATEMENT): (frozenset({"digest"}),),
    (DatabaseType.MYSQL, IdentityKind.REQUEST):
        (frozenset({"processlist_id"}), frozenset({"thread_id"})),
    (DatabaseType.ORACLE, IdentityKind.STATEMENT):
        (frozenset({"sql_id", "child_number", "inst_id"}),),
    (DatabaseType.ORACLE, IdentityKind.PLAN):
        (frozenset({"sql_id", "child_number", "plan_hash_value", "inst_id"}),),
    (DatabaseType.ORACLE, IdentityKind.REQUEST): (frozenset({"inst_id", "sid", "serial#"}),),
}
_TOP_SQL_ENGINES = frozenset({DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                              DatabaseType.MYSQL, DatabaseType.ORACLE})


@dataclass(frozen=True)
class NativeSQLIdentity:
    """A native identity at an explicit scope; absent native fields stay absent."""

    database_engine: DatabaseType
    kind: IdentityKind
    fields: dict[str, str | int | None]

    def __post_init__(self) -> None:
        try:
            engine = DatabaseType(self.database_engine)
            kind = IdentityKind(self.kind)
        except ValueError as exc:
            raise ValueError("Unsupported SQL identity engine or kind.") from exc
        object.__setattr__(self, "database_engine", engine)
        object.__setattr__(self, "kind", kind)
        allowed = _IDENTITY_FIELDS.get((engine, kind))
        if allowed is None or not self.fields or set(self.fields) - allowed:
            raise ValueError("SQL identity fields do not match the native identity contract.")
        required_groups = _IDENTITY_KEY_ALTERNATIVES[(engine, kind)]
        if not any(required.issubset(self.fields) for required in required_groups):
            raise ValueError("Native SQL identity is incomplete; do not substitute another identity scope.")
        for field_name, value in self.fields.items():
            if value is None:
                if engine != DatabaseType.MYSQL or kind != IdentityKind.STATEMENT or field_name != "schema_name":
                    raise ValueError("Null native identity values are allowed only for MySQL SCHEMA_NAME.")
            elif not isinstance(value, (str, int)) or isinstance(value, bool) or value == "":
                raise ValueError("Native identity values must be non-empty strings or integers.")

    def to_dict(self) -> dict[str, Any]:
        return {"database_engine": self.database_engine.value, "kind": self.kind.value,
                "fields": dict(self.fields)}


@dataclass(frozen=True)
class TopSQLItem:
    sql_identity: NativeSQLIdentity | None
    metric_value: int | float | None
    value_status: MetricValueStatus = MetricValueStatus.OBSERVED
    rank: int | None = None
    plan_identity: NativeSQLIdentity | None = None
    request_identity: NativeSQLIdentity | None = None

    def __post_init__(self) -> None:
        status = MetricValueStatus(self.value_status)
        object.__setattr__(self, "value_status", status)
        if status is MetricValueStatus.OBSERVED:
            if (type(self.metric_value) not in (int, float) or
                    not math.isfinite(float(cast(int | float, self.metric_value)))):
                raise ValueError("An observed metric requires a finite numeric value; zero is valid.")
        elif self.metric_value is not None or self.rank is not None:
            raise ValueError("Unavailable metrics must use null value and must not receive a rank.")
        if self.rank is not None and (type(self.rank) is not int or self.rank < 1 or self.sql_identity is None):
            raise ValueError("Ranked rows require a positive rank and a native SQL identity.")
        engine = next((identity.database_engine for identity in
                       (self.sql_identity, self.plan_identity, self.request_identity) if identity is not None), None)
        for identity in (self.sql_identity, self.plan_identity, self.request_identity):
            if identity is not None and identity.database_engine != engine:
                raise ValueError("SQL, plan, and request identities must use the same database engine.")

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank,
                "sql_identity": self.sql_identity.to_dict() if self.sql_identity else None,
                "plan_identity": self.plan_identity.to_dict() if self.plan_identity else None,
                "request_identity": self.request_identity.to_dict() if self.request_identity else None,
                "metric_value": self.metric_value, "value_status": MetricValueStatus(self.value_status).value}


@dataclass(frozen=True)
class TopSQLResult:
    database_id: str
    database_engine: DatabaseType
    database_version: str
    profile_id: str
    collection_timestamp: datetime
    ranking_metric: str
    ranking_direction: RankingDirection
    metric_name: str
    metric_unit: str
    metric_semantics: MetricSemantics
    metric_scope: MetricScope
    evidence_source: str
    data_visibility: DataVisibility
    completeness: Completeness
    collection_status: CollectionStatus
    items: tuple[TopSQLItem, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    collection_scope: CollectionScope = CollectionScope.UNKNOWN
    source_metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)
    database_error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        engine = DatabaseType(self.database_engine)
        status = CollectionStatus(self.collection_status)
        completeness = Completeness(self.completeness)
        direction = RankingDirection(self.ranking_direction)
        semantics = MetricSemantics(self.metric_semantics)
        scope = MetricScope(self.metric_scope)
        collection_scope = CollectionScope(self.collection_scope)
        visibility = DataVisibility(self.data_visibility)
        limitations = tuple(Limitation(value) for value in self.limitations)
        source_metadata = dict(self.source_metadata)
        if not isinstance(self.collection_timestamp, datetime):
            raise ValueError("Collection timestamp must be a datetime.")
        if not self.database_id or not self.database_version or not self.profile_id or not self.evidence_source:
            raise ValueError("Database, version, profile, and evidence source are required.")
        if engine not in _TOP_SQL_ENGINES:
            raise ValueError("This Phase 2 contract currently covers the four relational database engines only.")
        if self.collection_timestamp.tzinfo is None:
            raise ValueError("Collection timestamp must include a timezone.")
        if not self.ranking_metric or not self.metric_name or not self.metric_unit:
            raise ValueError("Ranking metric, metric name, and native unit are required.")
        metadata_fields = {
            DatabaseType.POSTGRESQL: {
                "stats_reset", "dealloc", "extension_version", "compute_query_id",
                "planning_time_included", "identity_unavailable_rows", "tracking_mode",
                "queryid_stability",
            },
            DatabaseType.MYSQL: {
                "digest_lost", "digest_null_bucket_present", "statement_instrument_count",
                "statement_instrument_disabled_count", "statement_instrument_untimed_count",
            },
            DatabaseType.ORACLE: {"instance_id", "container_id", "container_name"},
        }
        allowed_metadata = metadata_fields.get(engine, set())
        if (engine not in metadata_fields and source_metadata) or set(source_metadata) - allowed_metadata:
            raise ValueError("Top SQL source metadata contains an unsupported field for this engine.")
        if any(not isinstance(key, str) or not key for key in source_metadata):
            raise ValueError("Top SQL source metadata keys must be non-empty strings.")
        if any(value is not None and type(value) not in (str, int, float, bool)
               for value in source_metadata.values()):
            raise ValueError("Top SQL source metadata values must be scalar and JSON-safe.")
        if any(type(value) is float and not math.isfinite(value) for value in source_metadata.values()):
            raise ValueError("Top SQL source metadata cannot contain non-finite numbers.")
        for key in ("dealloc", "identity_unavailable_rows", "digest_lost", "statement_instrument_count",
                    "statement_instrument_disabled_count", "statement_instrument_untimed_count"):
            value = source_metadata.get(key)
            if (key in source_metadata and value is not None
                    and (not isinstance(value, int) or isinstance(value, bool) or value < 0)):
                raise ValueError("Top SQL source counters must be non-negative integers.")
        if ("digest_null_bucket_present" in source_metadata
                and source_metadata["digest_null_bucket_present"] is not None
                and type(source_metadata["digest_null_bucket_present"]) is not bool):
            raise ValueError("MySQL digest-bucket metadata must be boolean.")
        if ("planning_time_included" in source_metadata
                and type(source_metadata["planning_time_included"]) is not bool):
            raise ValueError("Top SQL planning-time metadata must be boolean.")
        instance_id, container_id = source_metadata.get("instance_id"), source_metadata.get("container_id")
        if ("instance_id" in source_metadata and instance_id is not None
                and (type(instance_id) is not int or instance_id < 1)):
            raise ValueError("Oracle Top SQL instance metadata must be a positive integer.")
        if ("container_id" in source_metadata and container_id is not None
                and (type(container_id) is not int or container_id < 0)):
            raise ValueError("Oracle Top SQL container metadata must be a non-negative integer.")
        if ("container_name" in source_metadata and source_metadata["container_name"] is not None
                and (not isinstance(source_metadata["container_name"], str)
                     or not source_metadata["container_name"])):
            raise ValueError("Oracle Top SQL container name must be a non-empty string.")
        if status is not CollectionStatus.COLLECTED and self.items:
            raise ValueError("A non-collected result cannot contain ranked observations.")
        observed = [item for item in self.items if MetricValueStatus(item.value_status) is MetricValueStatus.OBSERVED]
        if len(observed) != len(self.items):
            raise ValueError("Unavailable metric states belong in result status, not ranking rows.")
        if any(identity is not None and identity.database_engine is not engine
               for item in self.items for identity in
               (item.sql_identity, item.plan_identity, item.request_identity)):
            raise ValueError("All native identities must match the result database engine.")
        ranks = [item.rank for item in self.items]
        if any(rank is not None for rank in ranks) and ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("Ranks must be contiguous and ordered from one.")
        values = [cast(int | float, item.metric_value) for item in self.items]
        expected = sorted(values, reverse=direction is RankingDirection.DESCENDING)
        if values != expected:
            raise ValueError("Rows must be sorted using the declared ranking direction.")
        if status is CollectionStatus.COLLECTED and not self.items and completeness is not Completeness.COMPLETE:
            # Empty, complete collection is distinct from no source/data visibility.
            if not self.limitations:
                raise ValueError("An empty non-complete result must explain its limitation.")
        object.__setattr__(self, "database_engine", engine)
        object.__setattr__(self, "collection_status", status)
        object.__setattr__(self, "completeness", completeness)
        object.__setattr__(self, "ranking_direction", direction)
        object.__setattr__(self, "metric_semantics", semantics)
        object.__setattr__(self, "metric_scope", scope)
        object.__setattr__(self, "collection_scope", collection_scope)
        object.__setattr__(self, "data_visibility", visibility)
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(self, "source_metadata", source_metadata)

    def is_comparable_to(self, other: TopSQLResult) -> bool:
        """Whether two result sets share the same ranking population and semantics."""
        keys = ("database_id", "database_engine", "database_version", "profile_id", "ranking_metric",
                "ranking_direction", "metric_name", "metric_unit", "metric_semantics", "metric_scope",
                "collection_scope",
                "evidence_source")
        return all(getattr(self, key) == getattr(other, key) for key in keys)

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_id": self.database_id, "database_engine": DatabaseType(self.database_engine).value,
            "database_version": self.database_version, "profile_id": self.profile_id,
            "collection_timestamp": self.collection_timestamp.isoformat(),
            "ranking_metric": self.ranking_metric, "ranking_direction": RankingDirection(self.ranking_direction).value,
            "metric_name": self.metric_name, "metric_unit": self.metric_unit,
            "metric_semantics": MetricSemantics(self.metric_semantics).value,
            "metric_scope": MetricScope(self.metric_scope).value, "evidence_source": self.evidence_source,
            "collection_scope": CollectionScope(self.collection_scope).value,
            "data_visibility": DataVisibility(self.data_visibility).value,
            "completeness": Completeness(self.completeness).value,
            "collection_status": CollectionStatus(self.collection_status).value,
            "limitations": [Limitation(value).value for value in self.limitations],
            "source_metadata": dict(self.source_metadata),
            "items": [item.to_dict() for item in self.items],
            "database_error": self.database_error,
        }
