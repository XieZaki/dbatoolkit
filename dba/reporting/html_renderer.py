"""Escaped, offline HTML rendering from the shared report model."""

from __future__ import annotations

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

    def _table(self, table: ReportTable) -> str:
        headers = "".join(f"<th scope=\"col\">{_escape(_label(header))}</th>" for header in table.headers)
        rows = "".join("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in row) + "</tr>"
                       for row in table.rows)
        return self._fragment("table.html", HEADERS=headers, ROWS=rows)

    def _section(self, section: ReportSection) -> str:
        fields = "".join(self._fragment("field.html", LABEL=_escape(_label(label)), VALUE=_escape(value))
                         for label, value in section.fields)
        tables = "".join(self._table(table) for table in section.tables)
        blocks: list[str] = []
        for block in section.code_blocks:
            code = self._fragment("code_block.html", TITLE=_escape(_label(block.title)),
                                  LANGUAGE=_escape(block.language), CONTENT=_escape(block.content))
            blocks.append(self._fragment("details.html", TITLE=_escape(_label(block.title)), BODY=code)
                          if block.collapsed else code)
        summary = f"<p>{_escape(section.summary)}</p>" if section.summary else ""
        body = summary + (f"<dl class=\"fields\">{fields}</dl>" if fields else "") + tables + "".join(blocks)
        severity = _escape(section.severity.upper())
        section_html = self._fragment("section.html", ID=_escape(section.section_id),
                                      TITLE=_escape(_label(section.title)), SEVERITY=severity, BODY=body)
        if section.collapsed:
            section_html = section_html.replace(f'id="{_escape(section.section_id)}"', "")
            return self._fragment("collapsible_section.html", ID=_escape(section.section_id),
                                  TITLE=_escape(_label(section.title)), BODY=section_html)
        return section_html

    def render(self, model: ReportModel) -> str:
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


def _label(value: str) -> str:
    translations = {
        "UNKNOWN": "未知", "HEALTHY": "健康", "DEGRADED": "需关注", "NOT_DETECTED": "未观测到",
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
