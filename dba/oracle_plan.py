"""Identity-bound, read-only Oracle cached-plan collection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, DiagnosticReport
from dba.errors import ToolkitError
from dba.oracle import OracleAdapter, OraclePlanCursor, OraclePlanTarget
from dba.oracle_plan_profiles import (
    MAX_PLAN_OPERATORS,
    OraclePlanProfile,
    oracle_plan_contract,
    oracle_plan_profile,
)
from dba.registry import SqlScript
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus


@dataclass(frozen=True)
class OraclePlanReport(DiagnosticReport):
    details: dict[str, Any]


def _result_failure(result: CollectorResult) -> tuple[str, str]:
    error_type = result.error_type or "QueryFailed"
    if error_type in {"QueryDenied", "PermissionDenied"}:
        detail = result.error_message or "Oracle rejected the reviewed plan read."
        if "docs/DATABASE_PERMISSIONS.md" not in detail:
            detail = f"{detail} Review docs/DATABASE_PERMISSIONS.md."
        return "QUERY_DENIED", detail
    if error_type in {"QueryTimeout", "PlanTimeout", "CollectorTimeout"}:
        return "TIMEOUT", result.error_message or "Oracle cached-plan query timed out."
    return "COLLECTION_FAILED", result.error_message or "Oracle cached-plan query failed."


def _script(adapter: OracleAdapter, profile: ResolvedDiagnosticProfile,
            script_name: str, expected_rows: int) -> SqlScript:
    script = adapter.registry.get(script_name, DatabaseType.ORACLE)
    metadata = script.metadata
    if (metadata.feature != "plan" or profile.detected_version_family not in metadata.version_families
            or profile.runtime_variant not in metadata.runtime_variants
            or metadata.risk != "READ_ONLY" or not metadata.reviewed
            or metadata.max_rows != expected_rows):
        raise ToolkitError("ProfileRegistryMismatch", f"Oracle Plan Registry route does not match {script_name}.")
    return script


class OraclePlanCollector:
    def __init__(self, adapter: OracleAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type != DatabaseType.ORACLE or profile.feature != "plan"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or not profile.sql_profile_id
                or not profile.parser_contract_id):
            raise ToolkitError("VersionVerificationRequired",
                               "Oracle Plan requires a verified supported version profile.")
        try:
            if profile.detected_version_family is None:
                raise ValueError("Detected Oracle version family is missing")
            self.profile: OraclePlanProfile = oracle_plan_profile(
                profile.sql_profile_id, profile.detected_version_family,
            )
            self.contract = oracle_plan_contract(profile.parser_contract_id)
        except ValueError as exc:
            raise ToolkitError("ProfileResolutionError", str(exc)) from None
        self.resolved_profile = profile

    def collect(self, instance: DatabaseInstance, sql_id: str, child_number: int,
                *, inst_id: int | None = None, con_id: int | None = None,
                plan_hash_value: int | None = None) -> OraclePlanReport:
        if instance != self.adapter.instance:
            raise ToolkitError("InstanceMismatch", "Oracle Plan collector and adapter target different instances.")
        try:
            target = OraclePlanTarget(sql_id, child_number,
                                      inst_id if inst_id is not None else self.adapter.instance_id or 0,
                                      con_id, plan_hash_value)
        except ValueError as exc:
            raise ToolkitError("InvalidPlanIdentity", str(exc)) from None

        context_name = self.profile.context_script
        context_script = _script(self.adapter, self.resolved_profile, context_name, 2) if context_name else None
        identity_name = self.profile.identity_script
        identity_script = _script(self.adapter, self.resolved_profile, identity_name, 2)
        rows_script = _script(self.adapter, self.resolved_profile, self.profile.rows_script, 501)
        results: list[CollectorResult] = []

        if context_script is not None:
            context_result = self.adapter.execute_readonly(context_script)
            results.append(context_result)
            if not context_result.success:
                status, note = _result_failure(context_result)
                return self._report(instance, target, status, note, results)
            if context_result.truncated or len(context_result.rows) != 1:
                return self._report(instance, target, "IDENTITY_AMBIGUOUS",
                                    "Current Oracle connection container identity was not unique.", results)
            context = context_result.rows[0]
            actual_inst = context.get("inst_id")
            actual_con = context.get("con_id")
            if type(actual_inst) is not int or type(actual_con) is not int:
                return self._report(instance, target, "IDENTITY_AMBIGUOUS",
                                    "Current Oracle connection instance/container identity is incomplete.", results)
            if self.adapter.instance_id != actual_inst:
                return self._report(instance, target, "IDENTITY_CONFLICT",
                                    "V$INSTANCE and current connection instance identities disagree.", results,
                                    context=context)
            if target.inst_id != actual_inst:
                return self._report(instance, target, "IDENTITY_MISMATCH",
                                    "Requested INST_ID does not match the current Oracle connection instance.", results,
                                    context=context)
            if target.con_id is not None and target.con_id != actual_con:
                return self._report(instance, target, "IDENTITY_MISMATCH",
                                    "Requested CON_ID does not match the current Oracle connection container.", results,
                                    context=context)
            target = OraclePlanTarget(target.sql_id, target.child_number, actual_inst,
                                      actual_con, target.requested_plan_hash_value)
        else:
            if target.con_id is not None:
                return self._report(instance, target, "IDENTITY_MISMATCH",
                                    "Oracle 11gR2 does not expose CON_ID; omit --con-id.", results)
            if target.inst_id != self.adapter.instance_id:
                return self._report(instance, target, "IDENTITY_MISMATCH",
                                    "Requested INST_ID does not match the current Oracle connection instance.", results)

        identity_result = self.adapter.execute_plan_identity_readonly(identity_script, target)
        results.append(identity_result)
        if not identity_result.success:
            status, note = _result_failure(identity_result)
            return self._report(instance, target, status, note, results)
        if identity_result.truncated or len(identity_result.rows) > 1:
            return self._report(instance, target, "IDENTITY_AMBIGUOUS",
                                "More than one current V$SQL child cursor matches the requested identity.", results)
        if not identity_result.rows:
            return self._report(instance, target, "CURSOR_NOT_CACHED",
                                "The requested SQL_ID and CHILD_NUMBER are not present in the current shared pool.", results)

        cursor_row = identity_result.rows[0]
        if (cursor_row.get("sql_id") != target.sql_id
                or cursor_row.get("child_number") != target.child_number
                or cursor_row.get("inst_id") != target.inst_id
                or (target.con_id is not None and cursor_row.get("con_id") != target.con_id)
                or cursor_row.get("cursor_match_count") != 1):
            return self._report(instance, target, "IDENTITY_CONFLICT",
                                "V$SQL returned conflicting cursor identity evidence.", results,
                                cursor=cursor_row)
        current_hash = cursor_row.get("plan_hash_value")
        if type(current_hash) is not int or current_hash <= 0:
            return self._report(instance, target, "PLAN_HASH_UNAVAILABLE",
                                "The current child cursor has no valid PLAN_HASH_VALUE; no plan identity was fabricated.",
                                results, cursor=cursor_row, context=({"inst_id": target.inst_id,
                                "con_id": target.con_id} if target.con_id is not None else {"inst_id": target.inst_id}))
        if (target.requested_plan_hash_value is not None
                and target.requested_plan_hash_value != current_hash):
            return self._report(instance, target, "PLAN_HASH_MISMATCH",
                                "Current V$SQL PLAN_HASH_VALUE differs from the requested plan hash.",
                                results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id})

        address_hex = cursor_row.get("address_hex")
        child_address_hex = cursor_row.get("child_address_hex")
        hash_value = cursor_row.get("hash_value")
        if (not isinstance(address_hex, str) or type(hash_value) is not int
                or not isinstance(child_address_hex, str)):
            return self._report(instance, target, "IDENTITY_INCOMPLETE",
                                "Current V$SQL address identity is incomplete; the plan was not associated.",
                                results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id},
                                current_hash=current_hash)
        try:
            cursor_identity = OraclePlanCursor(
                target, address_hex, hash_value, child_address_hex, current_hash,
            )
        except ValueError:
            return self._report(instance, target, "IDENTITY_INCOMPLETE",
                                "Current V$SQL address identity is incomplete; the plan was not associated.",
                                results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id})

        plan_result = self.adapter.execute_plan_rows_readonly(rows_script, cursor_identity)
        results.append(plan_result)
        if not plan_result.success:
            status, note = _result_failure(plan_result)
            return self._report(instance, target, status, note, results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id},
                                current_hash=current_hash)
        if plan_result.truncated or len(plan_result.rows) > self.contract.max_operators:
            return self._report(instance, target, "PLAN_TOO_LARGE",
                                f"Cached plan exceeds the {self.contract.max_operators}-operator limit; no partial tree is presented.",
                                results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id},
                                current_hash=current_hash)
        if not plan_result.rows:
            return self._report(instance, target, "PLAN_NOT_CACHED",
                                "The cursor identity changed or its V$SQL_PLAN rows are no longer available.",
                                results, cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id},
                                current_hash=current_hash)

        nodes, error = self._validate_plan_rows(plan_result.rows, target, current_hash)
        if error:
            return self._report(instance, target, "PLAN_AMBIGUOUS", error, results,
                                cursor=cursor_row,
                                context={"inst_id": target.inst_id, "con_id": target.con_id},
                                current_hash=current_hash)
        return self._report(instance, target, "AVAILABLE", None, results,
                            cursor=cursor_row, context={"inst_id": target.inst_id, "con_id": target.con_id},
                            current_hash=current_hash, nodes=nodes)

    @staticmethod
    def _validate_plan_rows(rows: tuple[dict[str, Any], ...], target: OraclePlanTarget,
                            current_hash: int) -> tuple[list[dict[str, Any]], str | None]:
        nodes: list[dict[str, Any]] = []
        ids: set[int] = set()
        for row in rows:
            if (row.get("sql_id") != target.sql_id or row.get("child_number") != target.child_number
                    or row.get("inst_id") != target.inst_id or row.get("plan_hash_value") != current_hash
                    or (target.con_id is not None and row.get("con_id") != target.con_id)
                    or row.get("cursor_match_count") != 1):
                return [], "V$SQL_PLAN returned rows with conflicting SQL, child, instance, container, or plan identity."
            node_id = row.get("id")
            parent_id = row.get("parent_id")
            depth = row.get("depth")
            position = row.get("position")
            operation = row.get("operation")
            if (type(node_id) is not int or node_id < 0 or node_id in ids
                    or type(depth) is not int or depth < 0
                    or (position is not None and (type(position) is not int or position < 0))
                    or (parent_id is not None and position is None)
                    or (parent_id is None and depth != 0)
                    or not isinstance(operation, str) or not operation):
                return [], "Cached plan node identity or ordering fields are missing or duplicated."
            if parent_id is not None and (type(parent_id) is not int or parent_id < 0):
                return [], "Cached plan contains an invalid PARENT_ID."
            ids.add(node_id)
            nodes.append({
                "id": node_id,
                "parent_id": parent_id,
                "depth": depth,
                "position": position,
                "operation": operation,
                "options": row.get("options"),
                "object_owner": row.get("object_owner"),
                "object_name": row.get("object_name"),
                "estimated_rows": row.get("cardinality"),
                "estimated_cost": row.get("cost"),
                "estimated_bytes": row.get("bytes"),
                "actual_rows": None,
            })
        if any(node["parent_id"] is not None and node["parent_id"] not in ids for node in nodes):
            return [], "Cached plan contains a PARENT_ID without a corresponding node."
        by_id = {node["id"]: node for node in nodes}
        for node in nodes:
            parent_id = node["parent_id"]
            if parent_id is not None:
                parent = by_id[parent_id]
                if parent_id == node["id"] or parent["depth"] >= node["depth"]:
                    return [], "Cached plan contains an invalid parent-child depth relationship."
        return nodes, None

    def _report(self, instance: DatabaseInstance, target: OraclePlanTarget, status: str,
                note: str | None, results: list[CollectorResult], *,
                cursor: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
                current_hash: int | None = None,
                nodes: list[dict[str, Any]] | None = None) -> OraclePlanReport:
        now = datetime.now(timezone.utc)
        context = context or {}
        available = status == "AVAILABLE"
        requested_hash = target.requested_plan_hash_value
        identity_status = (
            "MATCHED_CURRENT_CURSOR_AND_PLAN_HASH" if requested_hash is not None and current_hash is not None
            else "CURRENT_CURSOR_MATCHED_HISTORICAL_PLAN_HASH_UNKNOWN"
            if current_hash is not None and cursor is not None
            else "NOT_VERIFIED"
        )
        limitations = [
            "CACHE_ONLY_CURRENT_SHARED_POOL",
            "CURSOR_LOAD_GENERATION_NOT_CAPTURED",
            "RUNTIME_ROW_SOURCE_STATISTICS_NOT_COLLECTED",
            "ADAPTIVE_PLAN_BRANCH_NOT_CLASSIFIED",
        ]
        if requested_hash is None:
            limitations.append("REQUEST_PLAN_HASH_NOT_SUPPLIED")
        if not available:
            limitations.append(status)
        details: dict[str, Any] = {
            "version": self.resolved_profile.detected_raw_version,
            "plan_available": available,
            "plan_status": status,
            "source": "V$SQL_PLAN",
            "identity_validation": identity_status,
            "historical_cursor_generation_verified": False,
            "request": {
                "sql_id": target.sql_id,
                "child_number": target.child_number,
                "inst_id": target.inst_id,
                "requested_plan_hash_value": requested_hash,
                "current_plan_hash_value": current_hash,
                **({"con_id": target.con_id} if target.con_id is not None else {}),
            },
            "plan_identity": ({
                "database_engine": "oracle",
                "kind": "PLAN",
                "fields": {
                    "sql_id": target.sql_id,
                    "child_number": target.child_number,
                    "inst_id": target.inst_id,
                    **({"con_id": target.con_id} if target.con_id is not None else {}),
                    "plan_hash_value": current_hash,
                },
            } if current_hash is not None else None),
            "top_operators": nodes or [],
            "operator_count": len(nodes or []),
            "max_operators": MAX_PLAN_OPERATORS,
            "completeness": "COMPLETE" if available else "UNAVAILABLE",
            "data_visibility": "COMPLETE" if available else "UNAVAILABLE",
            "limitations": limitations,
            "note": note,
            "collection_started_at": min((item.started_at for item in results), default=now).isoformat(),
            "collection_finished_at": max((item.finished_at for item in results), default=now).isoformat(),
            "collection_elapsed_ms": sum(item.elapsed_ms for item in results),
            "collection_row_count": sum(item.row_count for item in results),
            "collection_truncated": any(item.truncated for item in results),
            "collector_results": [asdict(item) for item in results],
            "context": context,
            "adaptive_plan_note": "Oracle adaptive plan candidate/final branches are not classified by this MVP.",
            "statistics_note": "CARDINALITY, COST and BYTES are optimizer estimates; actual row-source statistics are not collected.",
        }
        return OraclePlanReport(instance, now, "plan", (), details)


def diagnose_oracle_plan(adapter: OracleAdapter, profile: ResolvedDiagnosticProfile,
                         sql_id: str, child_number: int, *, inst_id: int | None = None,
                         con_id: int | None = None,
                         plan_hash_value: int | None = None) -> OraclePlanReport:
    return OraclePlanCollector(adapter, profile).collect(
        adapter.instance, sql_id, child_number, inst_id=inst_id, con_id=con_id,
        plan_hash_value=plan_hash_value,
    )
