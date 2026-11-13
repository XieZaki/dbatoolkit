"""SQL Server lock-resource evidence from sys.dm_tran_locks and resource-database metadata."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

LOCK_COLLECTOR = "lock_resources"

HOBT_RESOURCE_TYPES = frozenset({"KEY", "PAGE", "RID", "HOBT", "OIB"})
NON_OBJECT_RESOURCE_TYPES = frozenset({"DATABASE", "FILE", "EXTENT", "APPLICATION", "METADATA"})
_ROLES = {
    "GRANT": "HOLDER",
    "WAIT": "WAITER", "LOW_PRIORITY_WAIT": "WAITER", "ABORT_BLOCKERS": "WAITER",
    "CONVERT": "CONVERTING", "LOW_PRIORITY_CONVERT": "CONVERTING",
}
_ROLE_LABELS = {"HOLDER": "持有锁", "WAITER": "等待锁", "CONVERTING": "转换中锁", "UNKNOWN": "状态未知的锁"}
_ENTITY_LABELS = {"OBJECT": "对象 ID", "ALLOCATION_UNIT": "分配单元 ID"}
_TABLE_TYPES = frozenset({"USER_TABLE", "SYSTEM_TABLE", "INTERNAL_TABLE"})


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def lock_role(status: Any) -> str:
    """Map only the native request_status; anything unrecognised stays UNKNOWN."""
    text = _text(status)
    return _ROLES.get(text.upper(), "UNKNOWN") if text else "UNKNOWN"


def _resolve(row: Mapping[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    resource_type = (_text(row.get("resource_type")) or "").upper()
    database_id = _int(row.get("resource_database_id"))
    entity = _int(row.get("resource_associated_entity_id"))
    in_scope = database_id is not None and database_id == _int(row.get("connection_database_id"))
    database = _text(row.get("resource_database_name"))
    obj: dict[str, Any] = {
        "database_name": database,
        "schema_name": _text(row.get("schema_name")),
        "object_name": _text(row.get("object_name")),
        "object_id": _int(row.get("resolved_object_id")),
        "object_type": _text(row.get("object_type_desc")),
        "system_object": row.get("is_ms_shipped") if type(row.get("is_ms_shipped")) is bool else None,
        "index_id": _int(row.get("index_id")),
        "index_name": _text(row.get("index_name")),
        "index_type": _text(row.get("index_type_desc")),
        "partition_number": _int(row.get("partition_number")),
        "metadata_scope": "CONNECTION_DATABASE" if in_scope else "OTHER_DATABASE",
    }
    if resource_type == "DATABASE":
        return "NOT_APPLICABLE", "数据库级锁，不对应表或索引。", obj
    if resource_type in NON_OBJECT_RESOURCE_TYPES:
        return "NOT_APPLICABLE", f"{resource_type} 资源不对应表或索引，保留原始资源。", obj
    if resource_type not in HOBT_RESOURCE_TYPES | {"OBJECT", "ALLOCATION_UNIT"}:
        return "UNRESOLVED", f"资源类型 {resource_type or '缺失'} 没有已知的对象映射，保留原始资源。", obj
    if resource_type == "OBJECT":
        if obj["object_name"] is None:
            return "UNRESOLVED", "对象元数据不可见或对象已不存在（OBJECT_NAME 未返回名称）。", obj
        return "RESOLVED", None, obj
    if not entity:
        return "UNRESOLVED", ("分配单元 ID 不可用。" if resource_type == "ALLOCATION_UNIT"
                              else "HoBT 信息不可用。"), obj
    if not in_scope:
        where = database or (f"ID {database_id}" if database_id is not None else "未知数据库")
        return "UNRESOLVED", (f"资源位于数据库 {where}，与连接数据库不同；HoBT 只能在资源所属数据库内解析，"
                              "Toolkit 不切换数据库。"), obj
    if resource_type == "ALLOCATION_UNIT":
        unit_type = _int(row.get("allocation_unit_type"))
        if unit_type is None:
            return "UNRESOLVED", "分配单元元数据不可见或分配单元已不存在。", obj
        if unit_type not in (1, 2, 3):
            return "UNRESOLVED", f"分配单元类型 {unit_type} 没有明确的分区映射。", obj
    if obj["index_id"] is None:
        return "UNRESOLVED", "资源所属数据库中未找到该 HoBT 的分区（元数据不可见或对象已变化）。", obj
    if obj["object_name"] is None:
        return "PARTIAL", "已定位分区，但对象名称不可见。", obj
    if obj["index_id"] > 0 and obj["index_name"] is None:
        return "PARTIAL", "已定位表，但索引名称不可见。", obj
    return "RESOLVED", None, obj


def _identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in (
        "resource_type", "resource_database_id", "resource_associated_entity_id",
        "resource_description", "resource_lock_partition"))


def build_lock_evidence(
    rows: Iterable[Mapping[str, Any]] | None, *, collected: bool, success: bool, truncated: bool,
    requests: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Group lock requests by native resource identity; never infer objects from SQL or sessions."""
    status = ("NOT_COLLECTED" if not collected else "FAILED" if not success
              else "TRUNCATED" if truncated else "COMPLETE")
    blockers: dict[int, set[int]] = {}
    lock_waiting: set[int] = set()
    for request in requests:
        sid, blocker = _int(request.get("session_id")), _int(request.get("blocking_session_id"))
        if sid is not None and blocker:
            blockers.setdefault(sid, set()).add(blocker)
            if str(request.get("wait_type") or "").upper().startswith("LCK_M_"):
                lock_waiting.add(sid)
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for row in rows or ():
        groups.setdefault(_identity(row), []).append(row)
    resources: list[dict[str, Any]] = []
    waiting_sessions: set[int] = set()
    for number, members in enumerate(groups.values(), 1):
        first = members[0]
        resolution, reason, obj = _resolve(first)
        entries: list[dict[str, Any]] = []
        for row in members:
            sid = _int(row.get("session_id"))
            role = lock_role(row.get("request_status"))
            entries.append({
                "session_id": sid, "request_id": _int(row.get("request_id")),
                "exec_context_id": _int(row.get("exec_context_id")),
                "owner_type": _text(row.get("request_owner_type")),
                "mode": _text(row.get("request_mode")), "status": _text(row.get("request_status")),
                "role": role,
            })
        holders = {entry["session_id"] for entry in entries if entry["role"] in {"HOLDER", "CONVERTING"}}
        for entry in entries:
            if entry["role"] in {"WAITER", "CONVERTING"} and entry["session_id"] is not None:
                waiting_sessions.add(entry["session_id"])
                direct = sorted(blockers.get(entry["session_id"], ()))
                entry["direct_blockers"] = direct
                entry["blocker_holds_resource"] = (any(item in holders for item in direct) if direct else None)
        resources.append({
            "resource_id": f"LR-{number:03d}",
            "resource": {
                "type": _text(first.get("resource_type")), "subtype": _text(first.get("resource_subtype")),
                "database_id": _int(first.get("resource_database_id")),
                "database_name": _text(first.get("resource_database_name")),
                "associated_entity_id": _int(first.get("resource_associated_entity_id")),
                "description": _text(first.get("resource_description")),
                "lock_partition": _int(first.get("resource_lock_partition")),
            },
            "object": obj,
            "resolution": resolution,
            "resolution_reason": reason,
            "requests": entries,
        })
    unmatched = sorted(lock_waiting - waiting_sessions) if status == "COMPLETE" else []
    return {
        "status": status,
        "complete": status == "COMPLETE" and not unmatched,
        "connection_database_id": next((_int(row.get("connection_database_id")) for group in groups.values()
                                        for row in group), None),
        "resources": resources,
        "unmatched_lock_waits": unmatched,
        "source": "sys.dm_tran_locks",
        "note": "锁 DMV 与请求 DMV 顺序采样，非原子快照；对象名称仅来自资源所属数据库的元数据。",
    }


def held_resource_ids(evidence: Mapping[str, Any], session_id: Any) -> list[str]:
    """Contended resources on which this session holds or is converting a lock."""
    return [item["resource_id"] for item in evidence.get("resources", ())
            if any(entry.get("session_id") == session_id and entry.get("role") in {"HOLDER", "CONVERTING"}
                   for entry in item.get("requests", ()))
            and any(entry.get("role") in {"WAITER", "CONVERTING"} and entry.get("session_id") != session_id
                    for entry in item.get("requests", ()))]


def describe_object(resource: Mapping[str, Any]) -> str:
    obj = resource.get("object") or {}
    native = resource.get("resource") or {}
    status = resource.get("resolution")
    if status == "NOT_APPLICABLE":
        return f"不对应表或索引（{native.get('type') or '未知类型'}）"
    if status not in {"RESOLVED", "PARTIAL"}:
        return f"对象信息无法解析（{str(resource.get('resolution_reason') or '原因未知').rstrip('。')}）"
    database = obj.get("database_name") or f"数据库 ID {native.get('database_id')}"
    name = ".".join(str(part) for part in (database, obj.get("schema_name") or "?",
                                           obj.get("object_name") or "?"))
    parts = [name]
    if obj.get("object_type") and obj["object_type"] not in _TABLE_TYPES:
        parts.append(f"对象类型 {obj['object_type']}")
    if obj.get("system_object"):
        parts.append("系统对象")
    index_id = obj.get("index_id")
    if index_id == 0:
        parts.append("堆（HEAP，index_id 0）")
    elif index_id is not None:
        parts.append(f"索引 {obj['index_name']}" if obj.get("index_name") else f"索引 ID {index_id}（名称不可见）")
    if obj.get("partition_number") is not None:
        parts.append(f"分区 {obj['partition_number']}")
    if status == "PARTIAL":
        parts.append(str(resource.get("resolution_reason") or "部分解析"))
    return " · ".join(parts)


def describe_raw(resource: Mapping[str, Any]) -> str:
    native = resource.get("resource") or {}
    kind = native.get("type") or "未知类型"
    label = _ENTITY_LABELS.get(kind, "HoBT ID" if kind in HOBT_RESOURCE_TYPES else "关联实体 ID")
    parts = [kind, f"数据库 ID {native.get('database_id')}", f"{label} {native.get('associated_entity_id')}"]
    if native.get("description"):
        parts.append(str(native["description"]))
    if native.get("lock_partition"):
        parts.append(f"锁分区 {native['lock_partition']}")
    return " · ".join(parts)


def root_lock_fields(evidence: Any, root: Mapping[str, Any], session_id: Any,
                     limit: int = 3) -> tuple[tuple[str, str], ...]:
    """Human text shared by HTML and CLI for the contended locks a root session holds."""
    labels = ("锁对象（库 / 表 / 索引）", "锁模式", "锁资源（锁 DMV 原始值）", "锁对象解析")
    if not isinstance(evidence, Mapping) or evidence.get("status") in (None, "NOT_COLLECTED"):
        return tuple(zip(labels, ("未采集", "未采集", "未采集", "本次未采集锁 DMV。"), strict=True))
    if evidence.get("status") == "FAILED":
        return tuple(zip(labels, ("未采集", "未采集", "未采集",
                                  "锁 DMV 采集失败；原始等待资源与阻塞链不受影响。"), strict=True))
    wanted = set(root.get("held_lock_resource_ids") or ())
    held = [item for item in evidence.get("resources", ()) if item.get("resource_id") in wanted]
    truncated = "锁 DMV 结果已截断，仅含已读取的行。" if evidence.get("status") == "TRUNCATED" else ""
    if not held:
        return tuple(zip(labels, ("未采集", "未采集", "未采集",
                                  "锁 DMV 中未发现该会话持有被等待的锁；采样非原子，以原始等待资源为准。"
                                  + truncated), strict=True))
    shown = held[:limit]
    sid = int(session_id) if isinstance(session_id, str) and session_id.isdigit() else session_id

    def join(texts: list[str]) -> str:
        if len(texts) == 1:
            return texts[0]
        return "；".join(f"资源 {index}：{text}" for index, text in enumerate(texts, 1))

    more = f"（另有 {len(held) - len(shown)} 个资源未显示）" if len(held) > len(shown) else ""
    counts = {status: sum(item.get("resolution") == status for item in held)
              for status in ("RESOLVED", "PARTIAL", "UNRESOLVED", "NOT_APPLICABLE")}
    summary = (f"被等待资源 {len(held)} 个：已解析 {counts['RESOLVED']}，部分解析 {counts['PARTIAL']}，"
               f"无法解析 {counts['UNRESOLVED']}，不对应表或索引 {counts['NOT_APPLICABLE']}。"
               "对象名称仅来自资源所属数据库的元数据。" + truncated)
    return (
        (labels[0], join([describe_object(item) for item in shown]) + more),
        (labels[1], join([describe_modes(item, sid) for item in shown]) + more),
        (labels[2], join([describe_raw(item) for item in shown]) + more),
        (labels[3], summary),
    )


def describe_modes(resource: Mapping[str, Any], session_id: Any) -> str:
    """Separate the given session's own lock from the other requests on the same resource."""
    parts: list[str] = []
    others: dict[tuple[str, str, str], int] = {}
    for entry in resource.get("requests", ()):
        mode, status, role = entry.get("mode") or "未知", entry.get("status") or "未知", entry.get("role")
        label = _ROLE_LABELS.get(str(role), _ROLE_LABELS["UNKNOWN"])
        if entry.get("session_id") == session_id:
            parts.append(f"{label}：{mode}（会话 {session_id}，{status}）")
        else:
            others[(label, mode, status)] = others.get((label, mode, status), 0) + 1
    parts.extend(f"{label}：{mode}（其他 {count} 个请求，{status}）"
                 for (label, mode, status), count in others.items())
    return "；".join(parts)
