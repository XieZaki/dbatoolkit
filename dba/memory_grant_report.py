"""Shared JSON-derived HTML and terminal presentation for Memory Grant evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dba.logging import redact

if TYPE_CHECKING:
    from dba.reporting.model import ReportSection

_HUMAN_ROW_LIMIT = 20


def memory_grant_sections(value: Any) -> list[ReportSection]:
    from dba.reporting.model import ReportSection

    data = value if isinstance(value, dict) else {}
    waiting = data.get("waiting_sample", {})
    granted = data.get("granted_sample", {})
    waiting = waiting if isinstance(waiting, dict) else {}
    granted = granted if isinstance(granted, dict) else {}
    return [
        _sample_section("memory-grant-waiting", "等待 Memory Grant", waiting, waiting=True),
        _sample_section("memory-grant-granted", "已授予的内存", granted, waiting=False),
        ReportSection(
            "memory-grant-evidence",
            "证据完整性",
            summary=("等待记录和已授予记录由两条顺序执行的只读查询分别采样，二者不是同一时刻的服务器快照。"
                     "请求关联缺失时仍保留内存授予记录，并标明请求上下文不可用。本场景不评估数据库整体健康度。"
                     "耗时为客户端端到端时间，包含驱动执行、网络读取和结果解析；不单独表示服务器端 SQL 执行耗时。"),
            fields=(
                ("证据完整性", _display(data.get("completeness"))),
                ("快照一致性", _display(data.get("snapshot_consistency"))),
                ("健康评估", _display(data.get("health_assessment"))),
                ("身份冲突数", _safe(data.get("identity_conflicts"))),
            ),
        ),
    ]


def _sample_section(section_id: str, title: str, sample: dict[str, Any], *, waiting: bool) -> ReportSection:
    from dba.reporting.model import ReportSection, ReportTable

    raw_rows = sample.get("rows", [])
    rows: list[tuple[str, ...]] = []
    if isinstance(raw_rows, list):
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            identity = raw.get("request_identity", {})
            identity = identity if isinstance(identity, dict) else {}
            if waiting:
                row = (
                    _safe(raw.get("session_id")), _safe(raw.get("request_id")),
                    _display(identity.get("request_status")), _safe(identity.get("wait_type")),
                    _safe(raw.get("wait_time_ms")), _safe(raw.get("requested_memory_kb")),
                    _safe(raw.get("required_memory_kb")), _safe(raw.get("queue_id")),
                    _safe(raw.get("wait_order")), _display(identity.get("request_context_status")),
                )
            else:
                row = (
                    _safe(raw.get("session_id")), _safe(raw.get("request_id")),
                    _display(identity.get("request_status")), _safe(raw.get("requested_memory_kb")),
                    _safe(raw.get("required_memory_kb")), _safe(raw.get("granted_memory_kb")),
                    _safe(raw.get("used_memory_kb")), _safe(raw.get("max_used_memory_kb")),
                    _safe(identity.get("wait_type")), _display(identity.get("request_context_status")),
                )
            rows.append(row)
    headers = (("会话 ID", "请求 ID", "请求状态", "等待类型", "等待时长（毫秒）",
                "请求内存（KB）", "所需内存（KB）", "队列 ID", "等待顺序", "请求身份")
               if waiting else
               ("会话 ID", "请求 ID", "请求状态", "请求内存（KB）", "所需内存（KB）",
                "已授予内存（KB）", "已用内存（KB）", "最大已用内存（KB）", "等待类型", "请求身份"))
    table = ReportTable(headers, tuple(rows)) if rows else None
    sample_label = "等待中" if waiting else "已授予"
    summary = (
        f"{sample_label}记录：{len(rows)}；查询返回：{sample.get('row_count', 0)}；"
        f"采样时间：{_safe(sample.get('started_at'))} 至 {_safe(sample.get('finished_at'))}；"
        f"客户端耗时：{_elapsed_label(sample)}；是否截断：{_display(sample.get('truncated'))}；"
        f"采集状态：{_display(sample.get('success'))}。"
    )
    fields = () if sample.get("success") else (
        ("错误类型", _safe(sample.get("error_type"))),
        ("错误详情", _safe(sample.get("error_message"))),
    )
    return ReportSection(section_id, title, summary=summary, fields=fields,
                         tables=(table,) if table else ())


def render_memory_grant_human(value: Any, report_path: str | None = None) -> str:
    data = value if isinstance(value, dict) else {}
    waiting = data.get("waiting_sample", {})
    granted = data.get("granted_sample", {})
    waiting = waiting if isinstance(waiting, dict) else {}
    granted = granted if isinstance(granted, dict) else {}
    lines = [
        "SQL Server Memory Grant 等待队列",
        "=" * 64,
        f"数据库：{_safe(data.get('database_id'))} | 版本：{_safe(data.get('database_version'))}",
        f"采集状态：{_display(data.get('collection_status'))} | "
        f"证据完整性：{_display(data.get('completeness'))}",
        "数据库整体健康度：未评估",
        "等待记录和已授予记录分别顺序采样，不构成原子快照。",
        "耗时为客户端端到端时间，包含驱动执行、网络读取和结果解析；不单独表示服务器端 SQL 执行耗时。",
    ]
    for sample_name, sample, is_waiting in (("等待中", waiting, True), ("已授予", granted, False)):
        raw_rows = sample.get("rows", [])
        raw_rows = raw_rows if isinstance(raw_rows, list) else []
        lines.extend(("", f"{sample_name}记录：{len(raw_rows)} | "
                      f"采样时间：{_safe(sample.get('started_at'))} 至 {_safe(sample.get('finished_at'))} | "
                      f"客户端耗时：{_elapsed_label(sample)} | "
                      f"是否截断：{_display(sample.get('truncated'))}"))
        if not sample.get("success"):
            lines.append(f"  采集失败：{_safe(sample.get('error_type'))} — "
                         f"{_safe(sample.get('error_message'))}")
        for raw in raw_rows[:_HUMAN_ROW_LIMIT]:
            if not isinstance(raw, dict):
                continue
            identity = raw.get("request_identity", {})
            identity = identity if isinstance(identity, dict) else {}
            if is_waiting:
                text = (f"会话={_safe(raw.get('session_id'))} 请求={_safe(raw.get('request_id'))} "
                        f"等待={_safe(identity.get('wait_type'))}/{_safe(raw.get('wait_time_ms'))} 毫秒 "
                        f"请求内存={_safe(raw.get('requested_memory_kb'))} KB "
                        f"所需内存={_safe(raw.get('required_memory_kb'))} KB")
            else:
                text = (f"会话={_safe(raw.get('session_id'))} 请求={_safe(raw.get('request_id'))} "
                        f"已授予={_safe(raw.get('granted_memory_kb'))} KB "
                        f"已用={_safe(raw.get('used_memory_kb'))} KB "
                        f"最大已用={_safe(raw.get('max_used_memory_kb'))} KB")
            lines.append(f"  {text} | 请求身份：{_display(identity.get('request_context_status'))}")
        if len(raw_rows) > _HUMAN_ROW_LIMIT:
            lines.append(f"  HTML/JSON 报告另含 {len(raw_rows) - _HUMAN_ROW_LIMIT} 条记录。")
    lines.extend(("", "说明：等待请求只能证明发生了 Memory Grant 等待；已授予内存与当前内存使用量不同。"
                  "单条已授予请求不能据此判定为唯一根因。"))
    if report_path:
        lines.extend(("", "详细 HTML 报告", f"  {report_path}"))
    return str(redact("\n".join(lines)))


def _safe(value: Any) -> str:
    if value is None:
        return "—"
    text = str(redact(value))
    return "".join(char if char in "\t " or ord(char) >= 32 and ord(char) != 127
                   and not 0x80 <= ord(char) <= 0x9F else " " for char in text)


def _display(value: Any) -> str:
    from dba.reporting.normalizers import display_value

    return _safe(display_value(value))


def _elapsed_label(sample: dict[str, Any]) -> str:
    elapsed = sample.get("elapsed_ms")
    if sample.get("elapsed_scope") != "CLIENT_END_TO_END" or not isinstance(elapsed, (int, float)):
        return "未记录"
    return f"{elapsed:.3f} 毫秒（客户端端到端）"
