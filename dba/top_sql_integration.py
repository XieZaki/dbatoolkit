"""Opt-in attachment of existing cumulative Top SQL evidence to Slow reports."""

from __future__ import annotations

from typing import Any

from dba.core import DatabaseType
from dba.logging import redact
from dba.mysql import MySQLAdapter
from dba.oracle import OracleAdapter
from dba.postgresql import PostgreSQLAdapter
from dba.reporting.model import DEFAULT_HUMAN_REPORT_POLICY, HumanReportPolicy
from dba.sqlserver import SQLServerAdapter
from dba.top_sql import TOP_LIMIT, CollectionStatus, MetricValueStatus
from dba.top_sql_collectors import (
    MySQLTopSQLCollector,
    OracleTopSQLCollector,
    PostgreSQLTopSQLCollector,
    SQLServerTopSQLCollector,
)
from dba.versioning import SupportState, VersionStatus, resolve_profile_for_adapter

_DEFAULT_METRICS = {
    DatabaseType.SQLSERVER: "cpu_time",
    DatabaseType.POSTGRESQL: "total_exec_time",
    DatabaseType.MYSQL: "sum_timer_wait",
    DatabaseType.ORACLE: "cpu_time",
}

_COMPLETE_IDENTITY_FIELDS: dict[DatabaseType, frozenset[str]] = {
    DatabaseType.SQLSERVER: frozenset({
        "sql_handle", "statement_start_offset", "statement_end_offset", "database_id",
    }),
    DatabaseType.POSTGRESQL: frozenset({"queryid", "dbid", "userid", "toplevel"}),
    DatabaseType.MYSQL: frozenset({"digest", "schema_name"}),
    DatabaseType.ORACLE: frozenset({"sql_id", "child_number", "inst_id"}),
}

_SQL_TEXT_FIELDS = {
    DatabaseType.SQLSERVER: ("sql_text", "sql_text_truncated"),
    DatabaseType.POSTGRESQL: ("query", "query_truncated"),
    DatabaseType.MYSQL: ("sql_text", "sql_text_truncated"),
    DatabaseType.ORACLE: ("sql_text", "sql_text_truncated"),
}

_SLOW_REQUEST_KEYS = {
    DatabaseType.SQLSERVER: "all_requests",
    DatabaseType.POSTGRESQL: "active_requests",
    DatabaseType.MYSQL: "active_requests",
    DatabaseType.ORACLE: "active_requests",
}


def _status_envelope(status: str, engine: DatabaseType, version: Any, metric: str | None,
                     **details: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {"status": status, "database_engine": engine.value,
                                "database_version": version if isinstance(version, str) else "UNKNOWN"}
    if metric:
        envelope["ranking_metric"] = metric
    envelope.update(details)
    return envelope


def _complete_identity_fields(engine: DatabaseType, identity: Any) -> dict[str, Any] | None:
    if not isinstance(identity, dict) or identity.get("database_engine") != engine.value:
        return None
    fields = identity.get("fields")
    if not isinstance(fields, dict):
        return None
    kind = identity.get("kind")
    if kind == "PLAN":
        required_by_engine = {
            DatabaseType.SQLSERVER: {"plan_handle", "query_plan_hash", "plan_generation_num"},
            DatabaseType.ORACLE: {"sql_id", "child_number", "inst_id", "plan_hash_value"},
        }
        required = required_by_engine.get(engine)
        if required is None:
            return None
    elif kind == "STATEMENT":
        required = set(_COMPLETE_IDENTITY_FIELDS[engine])
    else:
        return None
    if engine is DatabaseType.ORACLE and "con_id" in fields:
        required.add("con_id")
    if not required.issubset(fields):
        return None
    return fields


def _same_native_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    return bool(left == right)


def _identity_matches(engine: DatabaseType, item: dict[str, Any], row: dict[str, Any]) -> bool:
    identity_fields = _complete_identity_fields(engine, item.get("sql_identity"))
    if identity_fields is None:
        return False
    if engine is DatabaseType.SQLSERVER and item.get("plan_identity") is None:
        return False
    if any(key not in row or not _same_native_value(row[key], value)
           for key, value in identity_fields.items()):
        return False
    plan_identity = item.get("plan_identity")
    if plan_identity is not None:
        plan_fields = _complete_identity_fields(engine, plan_identity)
        if plan_fields is None:
            return False
        if any(key not in row or not _same_native_value(row[key], value)
               for key, value in plan_fields.items()):
            return False
    return True


def associate_slow_sql_text(
    engine: DatabaseType,
    items: list[dict[str, Any]],
    slow_details: dict[str, Any],
    policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
) -> list[dict[str, Any]]:
    """Attach bounded SQL text only when complete native statement/plan identities match."""
    raw_rows = slow_details.get(_SLOW_REQUEST_KEYS[engine], [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    text_field, truncated_field = _SQL_TEXT_FIELDS[engine]
    associated: list[dict[str, Any]] = []
    for raw_item in items:
        item = dict(raw_item)
        matches = [row for row in rows if _identity_matches(engine, item, row)]
        association: dict[str, Any] = {"status": "UNMATCHED", "source": None,
                                       "reason": "NO_EXACT_NATIVE_IDENTITY_MATCH"}
        if len(matches) > 1:
            association["reason"] = "AMBIGUOUS_NATIVE_IDENTITY_MATCH"
        elif matches:
            row = matches[0]
            sql_text = row.get(text_field)
            if row.get(truncated_field) in (True, 1, "1"):
                association["reason"] = "SOURCE_SQL_TEXT_TRUNCATED"
            elif not isinstance(sql_text, str) or not sql_text:
                association["reason"] = "SOURCE_SQL_TEXT_UNAVAILABLE"
            else:
                clipped = sql_text[:policy.max_sql_chars]
                was_truncated = len(sql_text) > policy.max_sql_chars
                if was_truncated:
                    clipped += "\n[SQL text omitted by human report limit.]"
                association = {
                    "status": "MATCHED",
                    "source": "slow.active_requests",
                    "sql_text": str(redact(clipped)),
                    "truncated_by_report_policy": was_truncated,
                }
        item["sql_text_association"] = association
        associated.append(item)
    return associated


def collect_slow_top_sql(
    adapter: Any,
    slow_details: dict[str, Any],
    *,
    enabled: bool,
    core_complete: bool,
    metric: str | None = None,
    top_n: int = 10,
) -> dict[str, Any] | None:
    """Collect once through the existing engine Collector, isolated from Slow failures."""
    if not enabled:
        return None
    engine = adapter.instance.db_type
    chosen_metric = metric or _DEFAULT_METRICS.get(engine)
    version = slow_details.get("version")
    if not core_complete:
        return _status_envelope(MetricValueStatus.NOT_COLLECTED.value, engine, version,
                                chosen_metric, reason="CORE_EVIDENCE_INCOMPLETE")
    if type(top_n) is not int or not 1 <= top_n <= TOP_LIMIT:
        return _status_envelope(CollectionStatus.FAILED.value, engine, version, chosen_metric,
                                error_type="InvalidTopLimit")

    if engine not in _DEFAULT_METRICS:
        return _status_envelope(CollectionStatus.UNSUPPORTED.value, engine, version, chosen_metric,
                                reason="UNSUPPORTED_DATABASE")
    assert chosen_metric is not None
    try:
        profile, _ = resolve_profile_for_adapter(adapter, "top-sql")
        if profile.version_status is not VersionStatus.VERIFIED:
            return _status_envelope(CollectionStatus.UNSUPPORTED.value, engine, version, chosen_metric,
                                    reason="VERSION_NOT_VERIFIED")
        if profile.support_state is not SupportState.SUPPORTED:
            return _status_envelope(CollectionStatus.UNSUPPORTED.value, engine, version, chosen_metric,
                                    reason="PROFILE_UNSUPPORTED")

        if engine is DatabaseType.SQLSERVER and isinstance(adapter, SQLServerAdapter):
            result = SQLServerTopSQLCollector(adapter, profile).collect(adapter.instance, chosen_metric, top_n)
        elif engine is DatabaseType.POSTGRESQL and isinstance(adapter, PostgreSQLAdapter):
            result = PostgreSQLTopSQLCollector(adapter, profile).collect(adapter.instance, chosen_metric, top_n)
        elif engine is DatabaseType.MYSQL and isinstance(adapter, MySQLAdapter):
            result = MySQLTopSQLCollector(adapter, profile).collect(adapter.instance, chosen_metric, top_n)
        elif engine is DatabaseType.ORACLE and isinstance(adapter, OracleAdapter):
            result = OracleTopSQLCollector(adapter, profile).collect(adapter.instance, chosen_metric, top_n)
        else:
            return _status_envelope(CollectionStatus.UNSUPPORTED.value, engine, version, chosen_metric,
                                    reason="ADAPTER_DATABASE_MISMATCH")
        payload = result.to_dict()
        payload["items"] = associate_slow_sql_text(engine, payload["items"], slow_details)
        return _status_envelope(result.collection_status.value, engine, payload.get("database_version"),
                                payload.get("ranking_metric"), top_sql=payload)
    except Exception as exc:
        # The additive evidence must never turn a completed core diagnosis into a CLI failure.
        return _status_envelope(CollectionStatus.FAILED.value, engine, version, chosen_metric,
                                error_type=type(exc).__name__,
                                reason="TOP_SQL_ADDITIONAL_COLLECTION_FAILED")
