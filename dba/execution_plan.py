"""Bounded SQL Server Showplan XML parsing and evidence-only analysis."""

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DiagnosticReport, Finding, Severity
from dba.errors import ToolkitError
from dba.execution_plan_profiles import execution_plan_profile, parser_contract
from dba.logging import redact
from dba.settings import SQLServerSettings
from dba.sqlserver import SQLServerAdapter
from dba.versioning import ResolvedDiagnosticProfile, SupportState, VersionStatus


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _own(element: ET.Element) -> list[ET.Element]:
    found: list[ET.Element] = []
    pending = list(element)
    while pending:
        child = pending.pop()
        if _tag(child) == "RelOp":
            continue
        found.append(child)
        pending.extend(child)
    return found


@dataclass(frozen=True)
class PlanWarning:
    name: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class PlanNode:
    node_id: int | None
    physical_op: str | None
    logical_op: str | None
    estimated_rows: float | None
    actual_rows: float | None
    estimated_total_subtree_cost: float | None
    estimate_io: float | None
    estimate_cpu: float | None
    parallel: bool | None
    object_name: str | None
    index_name: str | None
    predicate: str | None
    actual_executions: float | None
    warnings: tuple[PlanWarning, ...] = ()
    implicit_conversions: tuple[str, ...] = ()
    child_node_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class MissingIndexEvidence:
    database: str | None
    schema: str | None
    table: str | None
    equality_columns: tuple[str, ...]
    inequality_columns: tuple[str, ...]
    include_columns: tuple[str, ...]
    impact: float | None


@dataclass(frozen=True)
class ParsedPlan:
    nodes: tuple[PlanNode, ...]
    missing_indexes: tuple[MissingIndexEvidence, ...]
    sanitized_xml: str


@dataclass(frozen=True)
class PlanSample:
    session_id: int
    source: str | None
    request_found: bool
    row: dict[str, Any] | None
    note: str | None
    samples: tuple[CollectorResult, ...]
    status: str = "PLAN_NOT_AVAILABLE"


@dataclass(frozen=True)
class PlanReport(DiagnosticReport):
    details: dict[str, Any] = field(default_factory=dict)


class ExecutionPlanParser:
    def __init__(self, max_xml_size: int, *, max_elements: int = 50000,
                 max_operators: int = 10000) -> None:
        self.max_xml_size = max_xml_size
        self.max_elements = max_elements
        self.max_operators = max_operators

    def parse(self, xml: str) -> ParsedPlan:
        if not isinstance(xml, str) or len(xml.encode("utf-8")) > self.max_xml_size:
            raise ToolkitError("PlanTooLarge", "Execution plan XML exceeds the configured limit.")
        upper = xml.upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            raise ToolkitError("InvalidPlanXML", "Execution plan XML contains unsupported declarations.")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            raise ToolkitError("InvalidPlanXML", "Execution plan XML could not be parsed.") from None
        if _tag(root) != "ShowPlanXML":
            raise ToolkitError("InvalidPlanXML", "Execution plan XML has an unexpected root.")
        # SQL text and plan attributes can contain marked credentials. Sanitize
        # decoded XML attributes before writing any XML artifact.
        all_elements = list(root.iter())
        if len(all_elements) > self.max_elements:
            raise ToolkitError("PlanTooLarge", "Execution plan contains too many XML elements.")
        for element in all_elements:
            element.attrib.update({key: str(redact(value)) for key, value in element.attrib.items()})
            if element.text:
                element.text = str(redact(element.text))
        nodes: list[PlanNode] = []
        known_warnings = {"SpillToTempDb", "NoJoinPredicate", "PlanAffectingConvert",
                          "MemoryGrantWarning", "MemoryGrant", "ColumnsWithNoStatistics",
                          "HashSpillDetails", "SortSpillDetails"}
        for relop in all_elements:
            if _tag(relop) != "RelOp":
                continue
            if len(nodes) >= self.max_operators:
                raise ToolkitError("PlanTooLarge", "Execution plan contains too many operators.")
            own = _own(relop)
            objects = [item for item in own if _tag(item) == "Object"]
            object_info = objects[0].attrib if objects else {}
            counters = [item for item in own if _tag(item) == "RunTimeCountersPerThread"]
            actual_rows = [v for item in counters if (v := _float(item.get("ActualRows"))) is not None]
            executions = [v for item in counters if (v := _float(item.get("ActualExecutions"))) is not None]
            predicates = [item.get("ScalarString") for item in own
                          if _tag(item) == "ScalarOperator" and item.get("ScalarString")]
            warnings: list[PlanWarning] = []
            for item in own:
                if _tag(item) == "Warnings":
                    for key, value in item.attrib.items():
                        if key in known_warnings and value.lower() not in {"0", "false"}:
                            warnings.append(PlanWarning(key, {key: value}))
                elif _tag(item) in known_warnings:
                    warnings.append(PlanWarning(_tag(item), dict(item.attrib)))
            conversions = [item.get("ScalarString") or "" for item in own
                           if _tag(item) == "ScalarOperator" and "CONVERT_IMPLICIT" in
                           (item.get("ScalarString") or "").upper()]
            conversions.extend(item.get("ScalarString") or "Implicit conversion in XML"
                               for item in own if _tag(item) == "Convert"
                               and (item.get("Implicit") or "").lower() in {"1", "true"})
            direct_children: list[int] = []
            pending = list(reversed(list(relop)))
            while pending:
                child = pending.pop()
                if _tag(child) == "RelOp":
                    try:
                        direct_children.append(int(child.get("NodeId", "")))
                    except ValueError:
                        pass
                else:
                    pending.extend(reversed(list(child)))
            try:
                node_id = int(relop.get("NodeId", ""))
            except ValueError:
                node_id = None
            nodes.append(PlanNode(
                node_id, relop.get("PhysicalOp"), relop.get("LogicalOp"),
                _float(relop.get("EstimateRows")), sum(actual_rows) if actual_rows else None,
                _float(relop.get("EstimatedTotalSubtreeCost")), _float(relop.get("EstimateIO")),
                _float(relop.get("EstimateCPU")),
                None if relop.get("Parallel") is None else relop.get("Parallel") in {"1", "true"},
                ".".join(str(object_info[key]) for key in ("Database", "Schema", "Table")
                         if object_info.get(key)) or None,
                object_info.get("Index"), predicates[0] if predicates else None,
                sum(executions) if executions else None,
                tuple(warnings), tuple(dict.fromkeys(conversions)), tuple(direct_children)))
        missing: list[MissingIndexEvidence] = []
        for group in all_elements:
            if _tag(group) != "MissingIndexGroup":
                continue
            for index in group:
                if _tag(index) != "MissingIndex":
                    continue
                columns: dict[str, list[str]] = {"EQUALITY": [], "INEQUALITY": [], "INCLUDE": []}
                for colgroup in index:
                    usage = colgroup.get("Usage", "")
                    if usage in columns:
                        columns[usage].extend(c.get("Name", "") for c in colgroup
                                              if _tag(c) == "Column" and c.get("Name"))
                missing.append(MissingIndexEvidence(index.get("Database"), index.get("Schema"),
                    index.get("Table"), tuple(columns["EQUALITY"]), tuple(columns["INEQUALITY"]),
                    tuple(columns["INCLUDE"]), _float(group.get("Impact"))))
        sanitized_xml = ET.tostring(root, encoding="unicode")
        if len(sanitized_xml.encode("utf-8")) > self.max_xml_size:
            raise ToolkitError("PlanTooLarge", "Sanitized execution plan exceeds the configured limit.")
        return ParsedPlan(tuple(nodes), tuple(missing), sanitized_xml)


class ExecutionPlanCollector:
    def __init__(self, adapter: SQLServerAdapter, profile: ResolvedDiagnosticProfile) -> None:
        self.adapter = adapter
        if (profile.db_type.value != "sqlserver" or profile.feature != "plan"
                or profile.version_status != VersionStatus.VERIFIED
                or profile.support_state != SupportState.SUPPORTED
                or not profile.sql_profile_id or not profile.parser_contract_id):
            raise ToolkitError("VersionVerificationRequired",
                              "Plan collection requires a verified supported diagnostic profile.")
        try:
            self.sql_profile = execution_plan_profile(profile.sql_profile_id)
            self.parser = parser_contract(profile.parser_contract_id)
        except ValueError as exc:
            raise ToolkitError("ProfileResolutionError", str(exc)) from None
        self.profile = profile

    def collect(self, session_id: int) -> PlanSample:
        if type(session_id) is not int or not 1 <= session_id <= 32767:
            raise ToolkitError("InvalidSession", "Session ID must be a positive SQL Server session ID.")
        max_xml_size = min(self.adapter.settings.plan_max_xml_size, self.parser.max_xml_bytes)
        params = (max_xml_size, max_xml_size, session_id)
        active_script = self.adapter.registry.get(self.sql_profile.request_script)
        cached_script = self.adapter.registry.get(self.sql_profile.cached_script)
        for script in (active_script, cached_script):
            metadata = script.metadata
            if (metadata.feature != self.profile.feature
                    or self.profile.detected_version_family not in metadata.version_families
                    or self.profile.runtime_variant not in metadata.runtime_variants):
                raise ToolkitError("ProfileRegistryMismatch",
                                  "Plan SQL metadata does not match the resolved version profile.")
        active = self.adapter.execute_readonly(active_script,
                                               plan_parameters=params)
        if not active.success:
            raise ToolkitError(active.error_type or "QueryFailed", active.error_message or "Plan probe failed.")
        results = [active]
        if active.truncated or len(active.rows) > 1:
            return PlanSample(session_id, "active_request", True, None,
                              "Multiple active requests matched this session; plan identity is ambiguous.",
                              (active,), "PLAN_AMBIGUOUS")
        active_row = active.rows[0] if active.rows else None
        if active_row is not None and (active_row.get("plan_xml") or active_row.get("plan_oversize")):
            note = "Execution plan unavailable: XML exceeds configured size limit." if active_row.get("plan_oversize") else None
            status = "PLAN_NOT_AVAILABLE" if note else "PLAN_AVAILABLE"
            return PlanSample(session_id, "active_request", True, active_row, note, tuple(results), status)
        cached = self.adapter.execute_readonly(cached_script,
                                               plan_parameters=params)
        results.append(cached)
        if not cached.success:
            raise ToolkitError(cached.error_type or "QueryFailed", cached.error_message or "Cached plan probe failed.")
        if cached.truncated or len(cached.rows) > 1:
            return PlanSample(session_id, "cached_ambiguous", active_row is not None, None,
                              "Multiple cached plans matched this session; no plan was selected.",
                              tuple(results), "PLAN_AMBIGUOUS")
        cached_row = cached.rows[0] if cached.rows else None
        if active_row is not None:
            same_statement = bool(
                cached_row and cached_row.get("sql_handle") == active_row.get("sql_handle")
                and isinstance(active_row.get("statement_start_offset"), int)
                and isinstance(active_row.get("statement_end_offset"), int)
                and cached_row.get("statement_start_offset") == active_row.get("statement_start_offset")
                and cached_row.get("statement_end_offset") == active_row.get("statement_end_offset")
                and cached_row.get("query_hash") == active_row.get("query_hash")
            )
            if cached_row and cached_row.get("plan_xml") and same_statement:
                return PlanSample(session_id, "cached_same_sql", True, cached_row,
                                  "Current plan unavailable; cached SQL handle and statement offsets matched.",
                                  tuple(results), "PLAN_AVAILABLE")
            return PlanSample(session_id, "active_request", True, active_row,
                              "Execution plan unavailable.", tuple(results), "PLAN_NOT_AVAILABLE")
        if cached_row and (cached_row.get("plan_xml") or cached_row.get("plan_oversize")):
            note = ("Active request no longer exists. Execution plan unavailable: XML exceeds configured size limit."
                    if cached_row.get("plan_oversize") else
                    "Active request no longer exists; latest cached plan belongs to this connected session.")
            status = "PLAN_NOT_AVAILABLE" if cached_row.get("plan_oversize") else "PLAN_AVAILABLE"
            return PlanSample(session_id, "cached_last_execution", False, cached_row,
                              note, tuple(results), status)
        note = "Active request no longer exists. Execution plan unavailable."
        return PlanSample(session_id, None, False, cached_row, note, tuple(results), "PLAN_NOT_AVAILABLE")


class ExecutionPlanAnalyzer:
    def __init__(self, settings: SQLServerSettings) -> None:
        self.settings = settings

    def analyze(self, sample: PlanSample, plan: ParsedPlan | None,
                instance: DatabaseInstance) -> PlanReport:
        findings: list[Finding] = []
        if plan is not None:
            lookup = {n.node_id: n for n in plan.nodes if n.node_id is not None}
            for node in plan.nodes:
                evidence = {"node_id": node.node_id, "physical_op": node.physical_op,
                            "logical_op": node.logical_op, "estimated_rows": node.estimated_rows,
                            "actual_rows": node.actual_rows, "cost": node.estimated_total_subtree_cost,
                            "estimate_io": node.estimate_io, "estimate_cpu": node.estimate_cpu,
                            "object": node.object_name, "index": node.index_name,
                            "predicate": node.predicate}
                op = node.physical_op or ""
                if op in {"Table Scan", "Clustered Index Scan", "Index Scan"}:
                    large = node.actual_rows is not None and node.actual_rows >= self.settings.plan_large_scan_rows
                    findings.append(Finding("LARGE_SCAN" if large else "SCAN_OPERATOR",
                        Severity.WARNING if large else Severity.INFO, evidence,
                        "检查过滤条件选择性、实际行数及现有索引设计；扫描本身不证明缺少索引。"))
                if (node.actual_rows is not None and node.estimated_rows is not None
                        and node.actual_rows != node.estimated_rows):
                    ratio: str | float | None
                    if node.estimated_rows == 0:
                        ratio = "unbounded" if node.actual_rows > 0 else None
                        severe = node.actual_rows > 0
                    else:
                        value = max(node.actual_rows / node.estimated_rows,
                                    node.estimated_rows / max(node.actual_rows, 1e-12))
                        ratio = round(value, 2)
                        severe = value >= self.settings.plan_cardinality_ratio_critical
                    if severe or (isinstance(ratio, float) and ratio >= self.settings.plan_cardinality_ratio_warning):
                        findings.append(Finding("CARDINALITY_MISMATCH",
                            Severity.CRITICAL if severe else Severity.WARNING,
                            {**evidence, "ratio": ratio},
                            "人工检查统计信息、参数值、数据倾斜及编译计划；不自动更新统计信息。"))
                if op == "Key Lookup" or node.logical_op == "Key Lookup":
                    findings.append(Finding("KEY_LOOKUP", Severity.INFO, evidence,
                        "结合实际执行次数和 Lookup 成本检查；不自动生成索引变更语句。"))
                if op == "Sort":
                    findings.append(Finding("SORT_OPERATOR", Severity.INFO, evidence,
                        "核对排序行数、估算成本及 XML 明确记录的溢出证据。"))
                if op == "Hash Match":
                    findings.append(Finding("HASH_MATCH", Severity.INFO, evidence,
                        "结合 Join/Aggregate 类型、行数和明确的 XML Warning 核对成本。"))
                if op == "Nested Loops":
                    children = [lookup[c] for c in node.child_node_ids if c in lookup]
                    if (len(children) >= 2 and children[0].actual_rows is not None
                            and children[1].actual_executions is not None
                            and children[0].actual_rows >= self.settings.plan_large_scan_rows
                            and children[1].actual_executions >= self.settings.plan_large_scan_rows):
                        findings.append(Finding("POTENTIAL_EXPENSIVE_NESTED_LOOP", Severity.WARNING,
                            {**evidence, "outer_actual_rows": children[0].actual_rows,
                             "inner_actual_executions": children[1].actual_executions},
                            "核对外侧行数、内侧重复执行次数及连接谓词。"))
                for expression in node.implicit_conversions:
                    findings.append(Finding("IMPLICIT_CONVERSION", Severity.WARNING,
                        {**evidence, "expression": expression},
                        "核对谓词两侧数据类型和转换位置，人工评估索引使用影响。"))
                for warning in node.warnings:
                    findings.append(Finding("PLAN_WARNING", Severity.WARNING,
                        {**evidence, "warning": warning.name, "warning_details": warning.evidence},
                        "核对执行计划中明确记录的 Warning 及相应运行时指标。"))
            for missing in plan.missing_indexes:
                findings.append(Finding("MISSING_INDEX_EVIDENCE", Severity.INFO,
                    {"database": missing.database, "schema": missing.schema, "table": missing.table,
                     "equality_columns": missing.equality_columns,
                     "inequality_columns": missing.inequality_columns,
                     "include_columns": missing.include_columns, "impact": missing.impact,
                     "note": "Missing Index information is advisory evidence only."},
                    "仅作证据；结合现有索引、写入负载和工作负载人工评估。"))
        row = sample.row or {}
        confidence = (
            "AMBIGUOUS" if sample.status == "PLAN_AMBIGUOUS"
            else "HIGH" if sample.source == "active_request" and row.get("plan_handle")
            and row.get("statement_start_offset") is not None
            else "MEDIUM" if sample.source == "cached_same_sql"
            else "AMBIGUOUS" if sample.source == "cached_last_execution"
            else "UNKNOWN"
        )
        sorted_nodes = sorted(plan.nodes, key=lambda n: n.estimated_total_subtree_cost or 0,
                              reverse=True)[:self.settings.plan_top_operators] if plan else []
        return PlanReport(instance, datetime.now(timezone.utc), "plan", tuple(findings), redact({
            "session_id": sample.session_id, "source": sample.source,
            "request_found": sample.request_found, "note": sample.note,
            "plan_status": sample.status, "plan_available": plan is not None,
            "confidence": confidence,
            "plan_oversize": bool(row.get("plan_oversize")),
            "metrics_scope": "current_request" if sample.source == "active_request" else "cached_last_execution",
            "request": {k: v for k, v in row.items() if k not in {"plan_xml", "plan_oversize"}},
            "operator_count": len(plan.nodes) if plan else 0,
            "top_operators": [asdict(n) for n in sorted_nodes],
            "missing_indexes": [asdict(m) for m in plan.missing_indexes] if plan else [],
            "collector_samples": [{"script": r.collector_name, "row_count": r.row_count,
                                   "elapsed_ms": r.elapsed_ms, "truncated": r.truncated}
                                  for r in sample.samples],
        }))


def diagnose_plan(adapter: SQLServerAdapter, session_id: int,
                  profile: ResolvedDiagnosticProfile) -> tuple[PlanReport, str | None]:
    collector = ExecutionPlanCollector(adapter, profile)
    sample = collector.collect(session_id)
    xml = sample.row.get("plan_xml") if sample.row else None
    parsed = (ExecutionPlanParser(
        min(adapter.settings.plan_max_xml_size, collector.parser.max_xml_bytes),
        max_elements=collector.parser.max_elements, max_operators=collector.parser.max_operators,
    ).parse(xml) if xml else None)
    report = ExecutionPlanAnalyzer(adapter.settings).analyze(sample, parsed, adapter.instance)
    report = replace(report, details={**report.details, "version_profile": asdict(profile)})
    return report, (
        parsed.sanitized_xml if parsed else None)
