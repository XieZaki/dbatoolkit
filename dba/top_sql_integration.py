"""Opt-in attachment of existing cumulative Top SQL evidence to Slow reports."""

from __future__ import annotations

from typing import Any, cast

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


_SQLSERVER_STATEMENT_IDENTITY_FIELDS = frozenset({
    "sql_handle", "statement_start_offset", "statement_end_offset", "database_id",
})
_SQLSERVER_PLAN_IDENTITY_FIELDS = frozenset({
    "plan_handle", "query_plan_hash", "plan_generation_num",
})
_SQLSERVER_STRING_IDENTITY_FIELDS = frozenset({"sql_handle", "query_hash", "plan_handle", "query_plan_hash"})


def _sqlserver_identity_fields(identity: Any) -> dict[str, Any]:
    if not isinstance(identity, dict):
        return {}
    fields = identity.get("fields")
    return cast(dict[str, Any], fields) if isinstance(fields, dict) else {}


def _sqlserver_identity_missing(identity: Any, kind: str) -> list[str]:
    required = (_SQLSERVER_STATEMENT_IDENTITY_FIELDS if kind == "STATEMENT"
                else _SQLSERVER_PLAN_IDENTITY_FIELDS)
    if (not isinstance(identity, dict) or identity.get("database_engine") != DatabaseType.SQLSERVER.value
            or identity.get("kind") != kind or not isinstance(identity.get("fields"), dict)):
        return sorted(required)
    fields = identity["fields"]
    missing = set(required.difference(fields))
    for key, value in fields.items():
        if (value is None or value == "" or isinstance(value, bool)
                or not isinstance(value, (str, int))):
            missing.add(str(key))
            continue
        if key in _SQLSERVER_STRING_IDENTITY_FIELDS and not isinstance(value, str):
            missing.add(str(key))
        elif key not in _SQLSERVER_STRING_IDENTITY_FIELDS and key in required and type(value) is not int:
            missing.add(str(key))
    return sorted(missing)


def _sqlserver_row_missing(fields: dict[str, Any], row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key, expected in fields.items():
        value = row.get(key)
        if (key not in row or value is None or value == "" or isinstance(value, bool)
                or type(value) is not type(expected)):
            missing.append(key)
    return missing


def _sqlserver_association(
    item: dict[str, Any], rows: list[dict[str, Any]], raw_rows: Any,
    request_data_present: bool, slow_details: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    statement_identity = item.get("sql_identity")
    plan_identity = item.get("plan_identity")
    statement_missing = _sqlserver_identity_missing(statement_identity, "STATEMENT")
    plan_missing = _sqlserver_identity_missing(plan_identity, "PLAN")
    top_missing = ([f"sql_identity.{key}" for key in statement_missing]
                   + [f"plan_identity.{key}" for key in plan_missing])
    identity_fields = _sqlserver_identity_fields(statement_identity)
    plan_fields = _sqlserver_identity_fields(plan_identity)

    if not request_data_present:
        return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                 "reason": "SLOW_REQUEST_SNAPSHOT_UNAVAILABLE"}, [])

    request_sample = next((sample for sample in slow_details.get("samples", [])
                           if isinstance(sample, dict) and sample.get("collector") == "active_requests"), None)
    if isinstance(request_sample, dict) and (
        request_sample.get("success") is False or request_sample.get("truncated") is True
    ):
        return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                 "reason": "SLOW_REQUEST_SNAPSHOT_UNAVAILABLE"}, [])

    if isinstance(raw_rows, list) and not raw_rows:
        if top_missing:
            return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                     "reason": "TOP_SQL_IDENTITY_INCOMPLETE", "missing_fields": top_missing,
                     "request_state": "NO_ACTIVE_REQUESTS"}, [])
        return ({"status": "NO_ACTIVE_REQUESTS", "source": None,
                 "reason": "NO_ACTIVE_REQUESTS"}, [])
    if not isinstance(raw_rows, list) or not rows:
        return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                 "reason": "SLOW_REQUEST_IDENTITY_INCOMPLETE"}, [])
    if top_missing:
        return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                 "reason": "TOP_SQL_IDENTITY_INCOMPLETE", "missing_fields": top_missing}, [])

    statement_matches: list[dict[str, Any]] = []
    incomplete_rows: list[dict[str, Any]] = []
    any_complete_statement = False
    for row in rows:
        row_missing = _sqlserver_row_missing(identity_fields, row)
        required_source_missing = sorted(_SQLSERVER_STATEMENT_IDENTITY_FIELDS.difference(row))
        row_missing = sorted(set(row_missing).union(required_source_missing))
        if row_missing:
            incomplete_rows.append(row)
            continue
        any_complete_statement = True
        if all(_same_native_value(row[key], value) for key, value in identity_fields.items()):
            statement_matches.append(row)

    if not statement_matches:
        if incomplete_rows:
            missing_fields = sorted({key for row in incomplete_rows
                                     for key in _sqlserver_row_missing(identity_fields, row)}
                                    .union(key for row in incomplete_rows
                                           for key in _SQLSERVER_STATEMENT_IDENTITY_FIELDS.difference(row)))
            return ({"status": "IDENTITY_INCOMPLETE", "source": None,
                     "reason": "SLOW_REQUEST_IDENTITY_INCOMPLETE", "missing_fields": missing_fields}, [])
        reason = "STATEMENT_IDENTITY_MISMATCH" if any_complete_statement else "NO_EXACT_NATIVE_IDENTITY_MATCH"
        return ({"status": "UNMATCHED", "source": None, "reason": reason}, [])

    plan_matches: list[dict[str, Any]] = []
    plan_incomplete_reasons: list[str] = []
    plan_mismatch = False
    for row in statement_matches:
        match_count = row.get("plan_generation_match_count")
        if type(match_count) is not int:
            plan_incomplete_reasons.append("SLOW_REQUEST_IDENTITY_INCOMPLETE")
            continue
        if match_count == 0:
            plan_incomplete_reasons.append("PLAN_GENERATION_UNAVAILABLE")
            continue
        if match_count > 1:
            plan_incomplete_reasons.append("PLAN_GENERATION_AMBIGUOUS")
            continue
        missing = _sqlserver_row_missing(plan_fields, row)
        if missing:
            plan_incomplete_reasons.append("SLOW_REQUEST_IDENTITY_INCOMPLETE")
        elif all(_same_native_value(row[key], value) for key, value in plan_fields.items()):
            plan_matches.append(row)
        else:
            plan_mismatch = True

    if not plan_matches:
        if plan_incomplete_reasons:
            reason = ("PLAN_GENERATION_AMBIGUOUS" if "PLAN_GENERATION_AMBIGUOUS" in plan_incomplete_reasons
                      else "PLAN_GENERATION_UNAVAILABLE" if "PLAN_GENERATION_UNAVAILABLE" in plan_incomplete_reasons
                      else "SLOW_REQUEST_IDENTITY_INCOMPLETE")
            return ({"status": "IDENTITY_INCOMPLETE", "source": None, "reason": reason}, [])
        if plan_mismatch:
            return ({"status": "UNMATCHED", "source": None,
                     "reason": "PLAN_IDENTITY_MISMATCH"}, [])
        return ({"status": "UNMATCHED", "source": None,
                 "reason": "NO_EXACT_NATIVE_IDENTITY_MATCH"}, [])
    if len(plan_matches) > 1:
        return ({"status": "UNMATCHED", "source": None,
                 "reason": "AMBIGUOUS_NATIVE_IDENTITY_MATCH"}, [])
    return ({"status": "MATCHED", "source": None}, plan_matches)


def _matched_sql_text(
    matches: list[dict[str, Any]], text_field: str, truncated_field: str,
    policy: HumanReportPolicy,
) -> dict[str, Any]:
    if len(matches) > 1:
        return {"status": "UNMATCHED", "source": None,
                "reason": "AMBIGUOUS_NATIVE_IDENTITY_MATCH"}
    row = matches[0]
    sql_text = row.get(text_field)
    if row.get(truncated_field) in (True, 1, "1"):
        return {"status": "UNMATCHED", "source": None, "reason": "SOURCE_SQL_TEXT_TRUNCATED"}
    if not isinstance(sql_text, str) or not sql_text:
        return {"status": "UNMATCHED", "source": None, "reason": "SOURCE_SQL_TEXT_UNAVAILABLE"}
    clipped = sql_text[:policy.max_sql_chars]
    was_truncated = len(sql_text) > policy.max_sql_chars
    if was_truncated:
        clipped += "\n[SQL 文本因人工报告长度上限而省略。]"
    return {"status": "MATCHED", "source": "slow.active_requests",
            "sql_text": str(redact(clipped)), "truncated_by_report_policy": was_truncated}


def associate_slow_sql_text(
    engine: DatabaseType,
    items: list[dict[str, Any]],
    slow_details: dict[str, Any],
    policy: HumanReportPolicy = DEFAULT_HUMAN_REPORT_POLICY,
) -> list[dict[str, Any]]:
    """Attach bounded SQL text only when complete native statement/plan identities match."""
    request_key = _SLOW_REQUEST_KEYS[engine]
    request_data_present = request_key in slow_details
    raw_rows = slow_details.get(request_key, [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    text_field, truncated_field = _SQL_TEXT_FIELDS[engine]
    associated: list[dict[str, Any]] = []
    for raw_item in items:
        item = dict(raw_item)
        if engine is DatabaseType.SQLSERVER:
            association, matches = _sqlserver_association(
                item, rows, raw_rows, request_data_present, slow_details)
            if matches:
                association = _matched_sql_text(matches, text_field, truncated_field, policy)
        else:
            matches = [row for row in rows if _identity_matches(engine, item, row)]
            association = {"status": "UNMATCHED", "source": None,
                           "reason": "NO_EXACT_NATIVE_IDENTITY_MATCH"}
            if len(matches) > 1:
                association["reason"] = "AMBIGUOUS_NATIVE_IDENTITY_MATCH"
            elif matches:
                association = _matched_sql_text(matches, text_field, truncated_field, policy)
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
