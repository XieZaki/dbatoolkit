"""Escaped, offline HTML rendering from the shared report model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from html import escape
from importlib.resources import files

from dba.logging import redact
from dba.reporting.model import ReportModel, ReportSection, ReportTable


def _escape(value: object) -> str:
    return escape(str(redact(value)), quote=True)


class HtmlReportRenderer:
    """Render a self-contained report using small packaged HTML templates."""

    def __init__(self) -> None:
        self._root = files("dba.reporting.templates")

    def _template(self, name: str) -> str:
        return self._root.joinpath(name).read_text(encoding="utf-8")

    def _fragment(self, name: str, **values: str) -> str:
        template = self._template(name)
        for key, value in values.items():
            template = template.replace("{{" + key + "}}", value)
        return template

    def _table(self, table: ReportTable, *, table_id: str | None = None,
               session_search: bool = False, tree: bool = False) -> str:
        headers = "".join(f"<th scope=\"col\">{_escape(_label(header))}</th>" for header in table.headers)
        links = {(row_index, column_index): target
                 for row_index, column_index, target in getattr(table, "cell_links", ())
                 if type(row_index) is int and type(column_index) is int and isinstance(target, str)
                 and re.fullmatch(r"#[a-z][a-z0-9_-]{0,100}", target)}
        state_labels = {
            "blocked": ("danger", "已确认参与阻塞"),
            "clear": ("clear", "完整采样确认未参与阻塞"),
            "partial": ("caution", "阻塞状态未能确认"),
        }
        rendered_rows = []
        for row_index, row in enumerate(table.rows):
            cells = []
            for column_index, value in enumerate(row):
                normalized = str(redact(value)).strip().upper()
                badge_class = {
                    "CRITICAL": "danger", "HIGH": "danger", "FAILED": "danger",
                    "MISSING_REQUIRED": "danger", "COMPLETE": "clear", "HEALTHY": "clear",
                    "READY": "clear", "WARNING": "caution", "PARTIAL": "caution",
                    "OPTIONAL_UNAVAILABLE": "caution", "TRUNCATED": "caution",
                    "UNKNOWN": "caution", "NOT_COLLECTED": "caution",
                    "IDENTITY_INCOMPLETE": "caution", "NOT_ASSESSED": "caution",
                }.get(normalized)
                escaped = (f'<span class="state-badge {badge_class}">{_escape(_label(str(value)))}</span>'
                           if badge_class else _escape(value))
                state = table.row_states[row_index] if row_index < len(table.row_states) else ""
                if column_index == 0 and state in state_labels:
                    state_class, state_text = state_labels[state]
                    escaped = f'<span class="state-badge {state_class}">{state_text}</span> {escaped}'
                target = links.get((row_index, column_index))
                if target is not None:
                    escaped = f'<a href="{_escape(target)}">{escaped}</a>'
                cell_class = ' class="tree-session"' if tree and column_index == 1 else ""
                cells.append(f"<td{cell_class}>{escaped}</td>")
            state = table.row_states[row_index] if row_index < len(table.row_states) else ""
            if state not in {"blocked", "clear", "partial"}:
                state = ""
            row_class = f' class="row-{state}" data-diagnostic-state="{state}"' if state else ""
            rendered_rows.append(f"<tr{row_class}>" + "".join(cells) + "</tr>")
        rows = "".join(rendered_rows)
        identifier = f' id="{_escape(table_id)}"' if table_id and re.fullmatch(
            r"[a-z][a-z0-9_-]{0,100}", table_id) else ""
        search_attr = ' data-session-search="true"' if session_search else ""
        table_markup = f"<table{identifier}{search_attr}><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"
        return f'<div class="table-wrap">{table_markup}</div>'

    def _section(self, section: ReportSection, *, slow: bool = False) -> str:
        fields = "".join(self._fragment("field.html", LABEL=_escape(_label(label)), VALUE=_escape(value))
                         for label, value in section.fields)
        searchable = section.section_id in {"appendix", "session-sources"}
        tables = "".join(self._table(
            table,
            table_id=("sessionTable" if section.section_id == "appendix" and index == 0 else
                      "sessionIdentityTable" if section.section_id == "session-sources" and index == 0 else
                      f"{section.section_id}-table-{index + 1}" if slow else None),
            session_search=searchable,
            tree=section.section_id == "blocking")
            for index, table in enumerate(section.tables))
        blocks: list[str] = []
        for block in section.code_blocks:
            code = self._fragment("code_block.html", TITLE=_escape(_label(block.title)),
                                  LANGUAGE=_escape(block.language), CONTENT=_escape(block.content))
            if slow:
                copy_button = (f'<button class="button button-copy" type="button" data-copy="{_escape(block.content)}">'
                               '复制文本</button>')
                code = code.replace("<div class=\"code-block\">", '<div class="code-block code-copyable">', 1)
                code = code.replace("<h3>", f"{copy_button}<h3>", 1)
            anchor_id = getattr(block, "anchor_id", None)
            if anchor_id and re.fullmatch(r"[a-z][a-z0-9_-]{0,100}", anchor_id):
                code = f'<div id="{_escape(anchor_id)}">{code}</div>'
            if block.collapsed:
                detail = self._fragment("details.html", TITLE=_escape(_label(block.title)), BODY=code)
                if slow:
                    detail = detail.replace("<details>", '<details data-expandable>', 1)
                blocks.append(detail)
            else:
                blocks.append(code)
        summary = f"<p>{_escape(section.summary)}</p>" if section.summary else ""
        body = summary + (f"<dl class=\"fields\">{fields}</dl>" if fields else "") + tables + "".join(blocks)
        severity = _escape(section.severity.upper())
        section_html = self._fragment("section.html", ID=_escape(section.section_id),
                                      TITLE=_escape(_label(section.title)), SEVERITY=severity, BODY=body)
        if slow:
            section_html = section_html.replace("<section id=", '<section class="report-section" id=', 1)
            if section.section_id == "immediate-recovery":
                section_html = section_html.replace(
                    'class="report-section"', 'class="report-section manual-action"', 1)
        if section.collapsed:
            section_html = section_html.replace(f'id="{_escape(section.section_id)}"', "")
            collapsible = self._fragment("collapsible_section.html", ID=_escape(section.section_id),
                                         TITLE=_escape(_label(section.title)), BODY=section_html)
            if slow:
                collapsible = collapsible.replace("<details", '<details data-expandable', 1)
            return collapsible
        return section_html

    def render(self, model: ReportModel) -> str:
        if model.scenario == "slow":
            return self._render_slow(model)
        incident = model.incident_summary
        cards = (
            ("状态", _label(incident.status)), ("主要问题", _label(incident.primary_problem)),
            ("当前观测", _label(incident.observed_now)), ("严重程度", _label(incident.severity)),
        )
        summary_metrics = "".join(
            f"<div class=\"summary-metric\"><strong>{_escape(label)}</strong> "
            f"<span>{_escape(value)}</span></div>" for label, value in cards)
        summary_fields: list[tuple[str, str]] = []
        summary_fields.append(("证据完整性", incident.data_completeness))
        if incident.required_evidence_status != "NOT_APPLICABLE":
            summary_fields.extend((
                ("必需证据", incident.required_evidence_status),
                ("可选证据", incident.optional_evidence_status),
            ))
        if incident.unavailable_optional_evidence:
            summary_fields.append(("不可用的可选证据",
                                   ", ".join(incident.unavailable_optional_evidence)))
        for impact_key, impact_value in incident.impact.items():
            summary_fields.append((_label(impact_key), str(impact_value)))
        for timing_key, timing_value in incident.timing.items():
            summary_fields.append((_label(timing_key), timing_value))
        if incident.wait:
            summary_fields.extend((("主要等待类别", incident.wait.get("category", "UNKNOWN")),
                                   ("主要等待事件", incident.wait.get("event", "UNKNOWN"))))
        summary_fields.append(("采样时间", _local_snapshot(incident.snapshot_time)))
        summary_body = "".join(self._fragment("field.html", LABEL=_escape(_label(label)), VALUE=_escape(value))
                               for label, value in summary_fields)
        summary_section = self._fragment(
            "section.html", ID="incident-summary", TITLE="故障概况",
            SEVERITY=_escape(incident.severity),
            BODY=f"<div class=\"summary-metrics\">{summary_metrics}</div>"
                 f"<dl class=\"fields\">{summary_body}</dl>")

        metadata = "".join(self._fragment("field.html", LABEL=_escape(_label(label)), VALUE=_escape(value))
                            for label, value in model.metadata)
        sections = [self._section(section) for section in model.sections]
        toc = "".join(f"<a href=\"#{_escape(section.section_id)}\">{_escape(_label(section.title))}</a>"
                      for section in (ReportSection("incident-summary", "故障概况"), *model.sections))
        body = self._fragment("report_body.html", TITLE=_escape(model.title), SUMMARY=summary_section,
                              METADATA=metadata, TOC=toc, SECTIONS="".join(sections))
        page = self._template("report.html").replace("{{PAGE_TITLE}}", _escape(model.title))
        page = page.replace("{{BODY}}", body)
        return page + "\n"

    def _render_slow(self, model: ReportModel) -> str:
        if any(section.section_id == "report-budget" for section in model.sections):
            # Evidence was already reduced by the shared policy. Keep the final shell
            # compact enough to honor a caller's hard byte ceiling.
            return self.render(replace(model, scenario="bounded-slow"))
        incident = model.incident_summary
        sections = {section.section_id: section for section in model.sections}
        metadata = dict(model.metadata)

        def pick(values: Mapping[str, object], *keys: str) -> object | None:
            return next((values[key] for key in keys if values.get(key) not in (None, "")), None)

        status = incident.status or "UNKNOWN"
        severity = incident.severity or "UNKNOWN"
        status_class = ("danger" if severity in {"CRITICAL", "HIGH"} or status == "CRITICAL" else
                        "clear" if status == "HEALTHY" and incident.data_completeness == "COMPLETE" else
                        "caution" if status in {"DEGRADED", "PARTIAL"} or
                        incident.data_completeness != "COMPLETE" else "unknown")
        metadata_html = "".join(
            f'<div class="hero-meta-item"><span>{_escape(_label(key))}</span><strong>{_escape(value)}</strong></div>'
            for key, value in model.metadata)

        def meta_value(*keys: str, default: str = "UNKNOWN") -> str:
            return next((str(metadata[key]) for key in keys if metadata.get(key) not in (None, "")), default)

        impact = incident.impact
        timing = incident.timing
        metrics = (
            ("阻塞请求", pick(impact, "blocked_requests", "blocked_sessions", "blocked_statements")),
            ("活动请求", pick(impact, "active_requests", "active_sessions")),
            ("未结束事务", pick(impact, "open_transactions", "idle_transactions", "long_transactions")),
            ("最长阻塞", pick(timing, "longest_block", "longest_blocking", "blocking_duration")),
        )
        metric_html = "".join(
            f'<article class="metric {"danger" if label == "阻塞请求" and value not in (None, 0, "0") else ""}">'
            f'<span class="metric-label">{_escape(label)}</span><strong>{_escape("—" if value is None else value)}</strong></article>'
            for label, value in metrics)
        summary_lines = [
            f"诊断：{model.title}",
            f"实例：{meta_value('Instance', '实例')}",
            f"数据库：{meta_value('Database Type', '数据库类型')} / {meta_value('Version', '版本')}",
            f"采集：{meta_value('Capture Started', '采集开始', default='N/A')} — "
            f"{meta_value('Capture Finished', '采集结束', default='N/A')}",
            f"一致性：{meta_value('Snapshot Consistency', '快照一致性', default='UNKNOWN')}",
            f"状态：{status} / {severity}；问题：{incident.primary_problem}；观测：{incident.observed_now}",
            *[f"影响 {key}：{value}" for key, value in incident.impact.items()],
            *[f"时间 {key}：{value}" for key, value in incident.timing.items()],
        ]
        summary_copy = "\n".join(summary_lines)

        def render_section(section_id: str, *, collapsed: bool = False) -> str:
            section = sections.get(section_id)
            if section is None:
                return ""
            if collapsed and not section.collapsed:
                section = replace(section, collapsed=True)
            return self._section(section, slow=True)

        root_blockers = render_section("root-blockers")
        root_cause = render_section("root-cause")
        chain = render_section("blocking")
        recovery = render_section("immediate-recovery")
        quality = render_section("collection-quality")
        appendix_sections = "".join(render_section(item, collapsed=True)
                                    for item in ("session-sources", "appendix") if item in sections)
        reserved = {"root-blockers", "root-cause", "blocking", "immediate-recovery",
                    "collection-quality", "session-sources", "appendix"}
        evidence_sections = "".join(
            render_section(section.section_id, collapsed=True)
            for section in model.sections if section.section_id not in reserved)
        toc_items = [("summary", "故障概况"), ("root-assessment", "根阻塞判断"),
                     ("blocking-chain", "阻塞传播链"), ("manual-action", "人工处置参考"),
                     ("evidence", "诊断证据"), ("collection-quality", "采集完整性"),
                     ("session-appendix", "会话附录")]
        toc = "".join(f'<a href="#{anchor}">{_escape(label)}</a>' for anchor, label in toc_items)
        page = self._template("slow_report.html")
        values = {
            "PAGE_TITLE": _escape(model.title), "TITLE": _escape(model.title),
            "STATUS": _escape(_label(status)), "SEVERITY": _escape(_label(severity)),
            "STATUS_CLASS": status_class,
            "PRIMARY_PROBLEM": _escape(_label(incident.primary_problem)),
            "OBSERVED_NOW": _escape(_label(incident.observed_now)),
            "COMPLETENESS": _escape(_label(incident.data_completeness)),
            "SNAPSHOT_CONSISTENCY": _escape(_label(meta_value(
                "Snapshot Consistency", "快照一致性", default="UNKNOWN"))),
            "METADATA": metadata_html, "METRICS": metric_html,
            "TOC": toc, "ROOT_ASSESSMENT": root_blockers + root_cause,
            "BLOCKING_CHAIN": chain, "RECOVERY": recovery,
            "EVIDENCE": evidence_sections, "QUALITY": quality,
            "APPENDIX": appendix_sections, "SUMMARY_COPY": _escape(summary_copy),
        }
        for key, value in values.items():
            page = page.replace("{{" + key + "}}", value)
        return page + "\n"


def _label(value: str) -> str:
    translations = {
        "UNKNOWN": "未知", "HEALTHY": "健康", "DEGRADED": "需关注", "NOT_DETECTED": "未观测到",
        "NOT_ASSESSED": "未评估",
        "BLOCKING": "锁与阻塞", "IDLE_TRANSACTION": "长期未提交事务", "LONG_RUNNING_SQL": "慢 SQL",
        "HIGH_CONCURRENCY": "高并发", "Database": "数据库", "Instance": "实例",
        "Database Type": "数据库类型", "Version": "版本", "Scenario": "诊断场景",
        "Generated At": "报告生成时间", "Environment": "环境", "Data Completeness": "证据完整性",
        "Root Blockers": "根阻塞", "Root Cause Candidate": "现场诊断结论",
        "Recent SQL Evidence": "最近执行 SQL",
        "Blocking Analysis": "锁与阻塞链", "Transaction Analysis": "长期未提交事务",
        "Session Identity": "会话身份与来源", "Request Runtime": "请求运行信息",
        "Workload Summary": "当前数据库状态", "Long Running SQL": "慢 SQL",
        "Wait Analysis": "等待分析", "Appendix — Other Sessions": "其他会话（附录）",
        "Immediate Recovery": "人工处置参考", "Collection Quality": "证据完整性",
        "Findings and Recommendations": "诊断发现与建议", "Database Space": "数据文件与表空间",
        "Transaction Log": "事务日志与保留证据", "Other Storage Evidence": "其他空间与日志证据",
        "Diagnostic Context": "未获取的证据与诊断上下文", "Execution Plan Follow-up": "执行计划后续检查",
        "Capture Window": "采集时间窗口", "Priority Review Candidates": "优先核查对象",
        "Database ID": "数据库 ID", "Session": "会话", "Role": "角色",
        "Status": "状态", "Login": "用户", "Host": "主机", "Program": "应用",
        "Client IP": "客户端 IP", "Open Txn": "未结束事务数", "Elapsed": "持续时间",
        "Wait": "等待事件", "Blocked By": "阻塞者", "CPU": "CPU", "Logical Reads": "逻辑读",
        "Reads": "物理读", "Writes": "写入", "Root Session": "根会话", "Affected Sessions": "影响会话数",
        "Direct Children": "直接子会话", "Maximum Depth": "最大深度", "Longest Block": "最长阻塞",
        "Collector": "采集器", "Impact": "影响", "Finding": "发现", "Recommendation": "建议",
        "Evidence Reference": "证据位置", "Severity": "严重程度", "Age": "事务时长",
        "Transaction ID": "事务 ID", "Begin Time": "开始时间", "Type": "类型", "State": "状态",
        "Report contents": "报告目录",
        "Rank": "排序", "User": "用户", "Reason": "原因", "Application": "应用",
        "Runtime seconds": "运行秒数", "Transaction age seconds": "事务时长秒",
        "SQL identity": "SQL 标识", "Transaction identity": "事务标识", "Login time": "登录时间",
        "Blocked session count": "影响会话数", "Evidence timestamp": "证据时间",
        "Identity recheck": "身份复核", "Priority": "优先级", "Action": "建议动作",
        "Reference Command": "人工参考命令", "Capture Started": "采集开始",
        "Triage Completed": "初报完成", "Capture Finished": "采集结束",
        "Snapshot Consistency": "快照一致性",
        "Indirectly Affected": "间接受影响会话",
        "Direct Blocked": "直接阻塞会话", "Direct Blocker": "直接阻塞者",
        "SQL identifier type": "SQL 标识类型",
        "Root Blocker": "根阻塞会话", "Wait Resource": "等待资源描述",
        "MySQL PROCESSLIST_ID": "MySQL PROCESSLIST_ID",
        "Performance Schema THREAD_ID": "Performance Schema THREAD_ID",
        "PostgreSQL PID": "PostgreSQL PID", "Oracle INST_ID": "Oracle INST_ID",
        "Oracle SID": "Oracle SID", "Oracle SERIAL#": "Oracle SERIAL#",
        "Observation": "当前观测", "Session Open Transaction Count": "会话级未结束事务计数",
        "Identity quality": "身份可信度", "Identity reuse note": "会话复用提示",
        "Target Session": "目标会话", "Open Transaction": "未结束事务",
        "Rollback Risk": "回滚风险", "Rollback Duration": "回滚耗时",
        "DBA Approval Required": "需要 DBA 审批", "Toolkit Executed": "Toolkit 是否执行",
        "Root-blocker Related Requests": "根阻塞相关请求",
        "Non-blocked Long-running Requests": "非阻塞的长运行请求",
        "Blocked Long-running Requests": "被阻塞的长运行请求",
        "MISSING_REQUIRED": "必需证据缺失",
        "OPTIONAL_UNAVAILABLE": "可选证据不可用", "NOT_COLLECTED": "未采集",
        "TRUNCATED": "结果已截断", "COMPLETE": "完整", "FAILED": "失败",
        "CRITICAL": "严重", "WARNING": "警告", "HIGH": "高", "INFO": "提示",
        "YES": "是", "NO": "否", "ACTIVE": "活动",
        "sleeping": "休眠", "suspended": "挂起", "running": "运行中",
        "Blocked": "被阻塞会话", "Long Running": "长运行请求",
        "Data completeness": "证据完整性", "Emergency evidence": "应急证据",
        "Full profile evidence": "完整配置证据", "required evidence": "必需证据",
        "Optional evidence": "可选证据", "Required evidence": "必需证据",
        "Optional unavailable": "可选证据不可用", "Collector row limit reached": "已达到采集行数上限",
        "NOT_APPLICABLE": "不适用", "PARTIAL": "部分可用", "INCOMPLETE": "不完整",
        "PENDING": "待采集", "IDENTITY_INCOMPLETE": "身份信息不完整",
        "REQUIRED_BEFORE_MANUAL_ACTION": "人工操作前必须重新核验",
    }
    if value in translations:
        return translations[value]
    parts = value.replace("_", " ").split()
    joined = " ".join(parts)
    return translations.get(joined, joined)


def _local_snapshot(value: str) -> str:
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.isoformat(sep=" ", timespec="seconds") + " (时区未知)"
        return parsed.astimezone().isoformat(sep=" ", timespec="seconds")
    except ValueError:
        return value
