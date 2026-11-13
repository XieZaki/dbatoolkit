"""SQL Server lock-object resolution from synthetic sys.dm_tran_locks rows (ISOLATED_OFFLINE_TEST)."""

import json
import re
from dataclasses import asdict
from html import unescape
from pathlib import Path
from typing import Any

import pytest
from test_slow import INSTANCE, edge, sample

from dba.cli import _redact_diagnostic_json
from dba.core import DatabaseType
from dba.incident import build_incident_summary, build_performance_triage, build_priority_candidates
from dba.presentation import render_emergency_triage, render_json
from dba.registry import SqlScriptRegistry
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.settings import SQLServerSettings
from dba.slow import build_slow_report
from dba.slow_profiles import SLOW_OPTIONAL_COLLECTORS, SLOW_SQL_PROFILES, validate_slow_profiles
from dba.sqlserver_locks import (
    build_lock_evidence,
    describe_modes,
    describe_object,
    describe_raw,
    lock_role,
    root_lock_fields,
)
from dba.versioning import PROFILE_BINDINGS

ROOT = Path(__file__).resolve().parents[1]
HOBT = 72057594050052096
TABLE = {
    "resource_database_name": "AdventureWorks2022", "resolved_object_id": 1077578877,
    "schema_name": "Production", "object_name": "ProductCategory", "object_type_desc": "USER_TABLE",
    "is_ms_shipped": False, "index_id": 1, "index_name": "PK_ProductCategory_ProductCategoryID",
    "index_type_desc": "CLUSTERED", "partition_number": 1,
}
UNRESOLVED_META = dict.fromkeys(
    ("resolved_object_id", "schema_name", "object_name", "object_type_desc", "is_ms_shipped", "index_id",
     "index_name", "index_type_desc", "partition_number", "allocation_unit_type"))


def lock(sid: int, mode: str, status: str, resource_type: str = "KEY", *, database_id: int = 5,
         entity: int | None = HOBT, description: str | None = "(8194443284a0)", connection_db: int = 5,
         **meta: Any) -> dict[str, Any]:
    return {
        "session_id": sid, "request_id": 0, "exec_context_id": 0, "request_owner_type": "TRANSACTION",
        "request_mode": mode, "request_status": status, "request_type": "LOCK",
        "resource_type": resource_type, "resource_subtype": "", "resource_database_id": database_id,
        "resource_database_name": "AdventureWorks2022" if database_id == 5 else "OtherDb",
        "connection_database_id": connection_db, "resource_associated_entity_id": entity,
        "resource_description": description, "resource_lock_partition": 0,
        **UNRESOLVED_META, **meta,
    }


def evidence(rows: list[dict[str, Any]], requests: list[dict[str, Any]] = (), **state: bool) -> dict[str, Any]:
    return build_lock_evidence(rows, collected=state.get("collected", True), success=state.get("success", True),
                               truncated=state.get("truncated", False), requests=requests)


def only(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resources = evidence(rows)["resources"]
    assert len(resources) == 1
    return resources[0]


def test_t1_object_lock_uses_object_id_with_resource_database() -> None:
    item = only([lock(55, "IX", "GRANT", "OBJECT", entity=1077578877, description="",
                      **{**TABLE, "index_id": None, "index_name": None, "index_type_desc": None,
                         "partition_number": None})])
    assert item["resolution"] == "RESOLVED" and item["object"]["object_id"] == 1077578877
    assert describe_object(item) == "AdventureWorks2022.Production.ProductCategory"
    assert describe_raw(item) == "OBJECT · 数据库 ID 5 · 对象 ID 1077578877"


def test_t2_key_lock_resolves_table_and_exact_index() -> None:
    item = only([lock(55, "X", "GRANT", **TABLE)])
    assert item["resolution"] == "RESOLVED" and item["resolution_reason"] is None
    assert describe_object(item) == ("AdventureWorks2022.Production.ProductCategory · "
                                     "索引 PK_ProductCategory_ProductCategoryID · 分区 1")
    assert describe_raw(item) == f"KEY · 数据库 ID 5 · HoBT ID {HOBT} · (8194443284a0)"


@pytest.mark.parametrize("resource_type,description", [("PAGE", "1:312"), ("RID", "1:312:4")])
def test_t3_t4_page_and_rid_with_hobt(resource_type: str, description: str) -> None:
    item = only([lock(55, "X", "GRANT", resource_type, description=description, **TABLE)])
    assert item["resolution"] == "RESOLVED" and "ProductCategory" in describe_object(item)
    assert describe_raw(item).startswith(f"{resource_type} · 数据库 ID 5 · HoBT ID {HOBT} · {description}")


@pytest.mark.parametrize("resource_type", ["HOBT", "OIB"])
def test_t5_hobt_and_oib(resource_type: str) -> None:
    item = only([lock(55, "Sch-M", "GRANT", resource_type, description="", **TABLE)])
    assert item["resolution"] == "RESOLVED"
    assert "索引 PK_ProductCategory_ProductCategoryID" in describe_object(item)


@pytest.mark.parametrize("unit_type,resolution,reason", [
    (1, "RESOLVED", None), (2, "RESOLVED", None), (3, "RESOLVED", None),
    (0, "UNRESOLVED", "分配单元类型 0 没有明确的分区映射。"),
    (None, "UNRESOLVED", "分配单元元数据不可见或分配单元已不存在。"),
])
def test_t6_allocation_unit_only_with_explicit_mapping(unit_type: int | None, resolution: str,
                                                       reason: str | None) -> None:
    meta = TABLE if resolution == "RESOLVED" else {}
    item = only([lock(55, "X", "GRANT", "ALLOCATION_UNIT", entity=72057594058768384, description="",
                      **meta, allocation_unit_type=unit_type)])
    assert (item["resolution"], item["resolution_reason"]) == (resolution, reason)
    if resolution == "UNRESOLVED":
        assert describe_object(item).startswith("对象信息无法解析（") and "ProductCategory" not in describe_object(item)


def test_t7_heap_shows_heap_without_inventing_an_index_name() -> None:
    item = only([lock(55, "X", "GRANT", "RID", description="1:900:2",
                      **{**TABLE, "object_name": "StagingRows", "index_id": 0, "index_name": None,
                         "index_type_desc": "HEAP"})])
    assert item["resolution"] == "RESOLVED"
    text = describe_object(item)
    assert "堆（HEAP，index_id 0）" in text and "索引 " not in text and "UNKNOWN" not in text


def test_t8_partitioned_table_keeps_partition_and_index() -> None:
    rows = [lock(55, "X", "GRANT", entity=HOBT + part, **{**TABLE, "partition_number": part,
                                                           "index_name": "PK_Sales_Partitioned"})
            for part in (2, 7)]
    resources = evidence(rows)["resources"]
    assert [item["object"]["partition_number"] for item in resources] == [2, 7]
    assert all("索引 PK_Sales_Partitioned" in describe_object(item) for item in resources)


def test_t9_other_database_never_reuses_connection_metadata() -> None:
    key = only([lock(55, "X", "GRANT", database_id=9, connection_db=5)])
    assert key["resolution"] == "UNRESOLVED" and key["object"]["metadata_scope"] == "OTHER_DATABASE"
    assert key["resolution_reason"] == ("资源位于数据库 OtherDb，与连接数据库不同；HoBT 只能在资源所属数据库内解析，"
                                        "Toolkit 不切换数据库。")
    obj = only([lock(55, "IX", "GRANT", "OBJECT", database_id=9, connection_db=5, entity=245575913,
                     description="", resource_database_name="OtherDb", resolved_object_id=245575913,
                     schema_name="dbo", object_name="Orders")])
    assert obj["resolution"] == "RESOLVED" and describe_object(obj) == "OtherDb.dbo.Orders"
    assert obj["object"]["object_type"] is None


def test_t9_sql_guards_every_catalog_join_by_resource_database() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    script = registry.get("lock_resources")
    registry.authorize(script, DatabaseType.SQLSERVER)
    text = " ".join(script.text.split())
    for alias in ("hp", "au", "o", "i"):
        join = re.search(rf"LEFT JOIN sys\.\w+ AS {alias} ON (.+?)(?: LEFT JOIN| CROSS APPLY| WHERE)", text)
        assert join is not None and "l.resource_database_id = DB_ID()" in join[1], alias
    assert "OBJECT_NAME(target.object_id, l.resource_database_id)" in text
    assert "OBJECT_SCHEMA_NAME(target.object_id, l.resource_database_id)" in text
    assert not re.search(r"\b(USE|EXEC|EXECUTE|sp_executesql)\b", script.text, re.I)


def test_t10_metadata_not_visible_degrades_without_names() -> None:
    hidden = only([lock(55, "X", "GRANT")])
    assert hidden["resolution"] == "UNRESOLVED"
    assert describe_object(hidden) == "对象信息无法解析（资源所属数据库中未找到该 HoBT 的分区（元数据不可见或对象已变化））"
    partial = only([lock(55, "X", "GRANT", **{**TABLE, "index_name": None})])
    assert partial["resolution"] == "PARTIAL" and "索引 ID 1（名称不可见）" in describe_object(partial)
    obj = only([lock(55, "IX", "GRANT", "OBJECT", entity=99, description="")])
    assert obj["resolution"] == "UNRESOLVED" and "OBJECT_NAME 未返回名称" in describe_object(obj)


@pytest.mark.parametrize("entity", [None, 0])
def test_t11_missing_hobt_keeps_raw_resource(entity: int | None) -> None:
    item = only([lock(55, "X", "GRANT", "PAGE", entity=entity, description="1:312", **TABLE)])
    assert item["resolution"] == "UNRESOLVED" and describe_object(item) == "对象信息无法解析（HoBT 信息不可用）"
    assert "ProductCategory" not in describe_object(item)
    assert describe_raw(item) == f"PAGE · 数据库 ID 5 · HoBT ID {entity} · 1:312"


@pytest.mark.parametrize("status,role", [
    ("GRANT", "HOLDER"), ("WAIT", "WAITER"), ("CONVERT", "CONVERTING"), ("LOW_PRIORITY_WAIT", "WAITER"),
    ("LOW_PRIORITY_CONVERT", "CONVERTING"), ("ABORT_BLOCKERS", "WAITER"), (None, "UNKNOWN"), ("GRANTED?", "UNKNOWN"),
])
def test_t12_lock_status_roles(status: str | None, role: str) -> None:
    assert lock_role(status) == role


def test_t12_holder_waiter_and_converter_modes_are_separate() -> None:
    requests = [edge(56, 55, wait_type="LCK_M_U"), edge(57, 55, wait_type="LCK_M_X")]
    item = evidence([lock(55, "X", "GRANT", **TABLE), lock(56, "U", "WAIT", **TABLE),
                     lock(57, "X", "CONVERT", **TABLE)], requests)["resources"][0]
    assert describe_modes(item, 55) == ("持有锁：X（会话 55，GRANT）；等待锁：U（其他 1 个请求，WAIT）；"
                                        "转换中锁：X（其他 1 个请求，CONVERT）")
    waiter = next(entry for entry in item["requests"] if entry["session_id"] == 56)
    assert waiter["direct_blockers"] == [55] and waiter["blocker_holds_resource"] is True


def test_t13_multiple_resources_stay_separate() -> None:
    rows = [lock(55, "X", "GRANT", **TABLE), lock(56, "U", "WAIT", **TABLE),
            lock(55, "X", "GRANT", entity=HOBT + 9, description="(aaaa)", **{**TABLE, "index_id": 3,
                                                                              "index_name": "IX_Name"}),
            lock(57, "S", "WAIT", entity=HOBT + 9, description="(aaaa)", **{**TABLE, "index_id": 3,
                                                                              "index_name": "IX_Name"})]
    value = evidence(rows, [edge(56, 55, wait_type="LCK_M_U"), edge(57, 55, wait_type="LCK_M_S")])
    assert [item["resource_id"] for item in value["resources"]] == ["LR-001", "LR-002"]
    fields = dict(root_lock_fields(value, {"held_lock_resource_ids": ["LR-001", "LR-002"]}, "55"))
    assert fields["锁对象（库 / 表 / 索引）"].startswith("资源 1：") and "；资源 2：" in fields["锁对象（库 / 表 / 索引）"]
    assert "索引 PK_ProductCategory_ProductCategoryID" in fields["锁对象（库 / 表 / 索引）"].split("；资源 2：")[0]
    assert "索引 IX_Name" in fields["锁对象（库 / 表 / 索引）"].split("；资源 2：")[1]
    assert "等待锁：S" in fields["锁模式"].split("；资源 2：")[1] and "等待锁：U" not in fields["锁模式"].split("；资源 2：")[1]


@pytest.mark.parametrize("resource_type,expected", [
    ("DATABASE", "NOT_APPLICABLE"), ("METADATA", "NOT_APPLICABLE"), ("APPLICATION", "NOT_APPLICABLE"),
    ("XACT", "UNRESOLVED"), (None, "UNRESOLVED"),
])
def test_t14_unknown_or_non_object_resource_types_degrade_safely(resource_type: str | None, expected: str) -> None:
    item = only([lock(55, "X", "GRANT", resource_type, description="raw-value")])  # type: ignore[arg-type]
    assert item["resolution"] == expected and "raw-value" in describe_raw(item)


def _blocking_report(lock_sample: Any | None) -> Any:
    requests = [edge(55, 0, status="suspended", wait_type="WAITFOR", command="WAITFOR", elapsed_ms=40000,
                     sql_text="WAITFOR DELAY '00:01:00'"),
                edge(56, 55, status="suspended", wait_type="LCK_M_X", wait_ms=30000, elapsed_ms=30000,
                     wait_resource=f"KEY: 5:{HOBT} (8194443284a0)", sql_text="UPDATE Production.ProductCategory")]
    sessions = [{"session_id": sid, "login_time": "2026-11-12T10:00:00", "login_name": "app", "host_name": "h",
                 "program_name": "p", "open_transaction_count": 1, "status": "suspended"} for sid in (55, 56)]
    transactions = [{"session_id": 55, "login_time": "2026-11-12T10:00:00", "transaction_id": 581791,
                     "transaction_type": 1, "transaction_state": 2, "transaction_begin_time": "2026-11-12T10:00:01",
                     "transaction_age_seconds": 41}]
    results = [sample("active_requests", requests), sample("active_sessions", sessions),
               sample("transactions", transactions), sample("connections", [])]
    if lock_sample is not None:
        results.append(lock_sample)
    value = build_slow_report(INSTANCE, tuple(results), SQLServerSettings(),
                              optional_collectors=("transactions", "connections", "lock_resources"))
    value.details["performance_triage"] = build_performance_triage(value)
    value.details["priority_candidates"] = build_priority_candidates(value, include_blocking_victims=True)
    return value


LOCK_ROWS = [lock(55, "X", "GRANT", **TABLE), lock(56, "X", "WAIT", **TABLE)]


def _root_card(page: str) -> str:
    blocking = page.split('id="page-blocking"', 1)[1].split('id="page-plans"', 1)[0]
    return unescape(blocking.split("锁对象与影响范围", 1)[1].split('class="card card-pad"', 1)[0])


def test_html_json_and_cli_agree_on_lock_object() -> None:
    value = _blocking_report(sample("lock_resources", LOCK_ROWS))
    summary = build_incident_summary(value)
    payload = json.loads(render_json(_redact_diagnostic_json({"report": asdict(value),
                                                              "incident_summary": asdict(summary)})))
    lock_json = payload["report"]["details"]["lock_resources"]
    root_json = payload["report"]["details"]["roots"][0]
    assert lock_json["status"] == "COMPLETE" and lock_json["complete"] is True
    assert root_json["held_lock_resource_ids"] == ["LR-001"]
    resource = lock_json["resources"][0]
    assert resource["resolution"] == "RESOLVED" and resource["object"]["index_name"] == TABLE["index_name"]
    assert [(entry["session_id"], entry["mode"], entry["status"], entry["role"]) for entry in resource["requests"]] \
        == [(55, "X", "GRANT", "HOLDER"), (56, "X", "WAIT", "WAITER")]
    expected = dict(root_lock_fields(lock_json, root_json, "55"))
    assert expected["锁模式"] == "持有锁：X（会话 55，GRANT）；等待锁：X（其他 1 个请求，WAIT）"
    card = _root_card(HtmlReportRenderer().render(build_report_model(value, summary)))
    for label, text in expected.items():
        assert label in card and text in card, label
    assert f"KEY: 5:{HOBT} (8194443284a0)" in card and "解析到表名待开发" not in card
    cli = render_emergency_triage(value, tuple(value.details["priority_candidates"]),
                                  capture_started_at="2026-11-12T10:00:41")
    for label, text in expected.items():
        assert f"   {label}：{text}" in cli, label
    assert summary.primary_problem == "BLOCKING" and summary.data_completeness == "COMPLETE"


@pytest.mark.parametrize("lock_sample,state", [
    (None, "本次未采集锁 DMV。"),
    (sample("lock_resources", [], success=False, error_type="QueryFailed"),
     "锁 DMV 采集失败；原始等待资源与阻塞链不受影响。"),
    (sample("lock_resources", []), "锁 DMV 中未发现该会话持有被等待的锁；采样非原子，以原始等待资源为准。"),
])
def test_missing_or_failed_lock_evidence_keeps_blocking_diagnosis(lock_sample: Any, state: str) -> None:
    baseline = _blocking_report(None)
    value = _blocking_report(lock_sample)
    assert [finding.title for finding in value.findings] == [finding.title for finding in baseline.findings]
    root = value.details["roots"][0]
    for key in ("session_id", "blocked_sessions", "transaction_id", "transaction_identity_quality"):
        assert root[key] == baseline.details["roots"][0][key]
    assert root["manual_action"]["status"] == baseline.details["roots"][0]["manual_action"]["status"]
    assert value.details["blocking_graph"] == baseline.details["blocking_graph"]
    assert value.details["data_visibility"] == baseline.details["data_visibility"]
    summary = build_incident_summary(value)
    assert summary.data_completeness == build_incident_summary(baseline).data_completeness
    card = _root_card(HtmlReportRenderer().render(build_report_model(value, summary)))
    assert state in card and f"KEY: 5:{HOBT} (8194443284a0)" in card


def test_truncated_lock_sample_is_marked_and_does_not_claim_completeness() -> None:
    value = _blocking_report(sample("lock_resources", LOCK_ROWS, truncated=True))
    lock_json = value.details["lock_resources"]
    assert lock_json["status"] == "TRUNCATED" and lock_json["complete"] is False
    fields = dict(root_lock_fields(lock_json, value.details["roots"][0], "55"))
    assert fields["锁对象解析"].endswith("锁 DMV 结果已截断，仅含已读取的行。")


def test_lock_wait_without_pending_lock_row_marks_association_incomplete() -> None:
    value = _blocking_report(sample("lock_resources", [lock(55, "X", "GRANT", **TABLE)]))
    assert value.details["lock_resources"]["unmatched_lock_waits"] == [56]
    assert value.details["lock_resources"]["complete"] is False
    assert any("lock-object association is incomplete" in line for line in value.details["quality"])


def test_lock_collector_routed_for_every_sqlserver_slow_family_only() -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    assert validate_slow_profiles(registry, PROFILE_BINDINGS) == ()
    assert {name for name, scripts in SLOW_SQL_PROFILES.items() if "lock_resources" in scripts} == {
        "sqlserver_2008r2_slow", "sqlserver_shared_slow"}
    assert all(SLOW_OPTIONAL_COLLECTORS[name][-1] == "lock_resources"
               for name in ("sqlserver_2008r2_slow", "sqlserver_shared_slow"))
