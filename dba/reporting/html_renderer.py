"""Escaped, offline HTML rendering from the shared report model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from html import escape
from importlib.resources import files

from dba.logging import redact
from dba.reporting.model import ReportModel, ReportSection, ReportTable
from dba.reporting.normalizers import (
    display_database_type,
    display_label,
    display_quality_impact,
    display_report_summary,
    display_report_value,
    display_scenario,
    display_slow_label,
    display_slow_value,
)


def _escape(value: object) -> str:
    return escape(str(redact(value)), quote=True)


def _severity_badge(severity: str) -> str:
    severity_class = _escape(severity.upper())
    return f' <span class="badge {severity_class}">{_escape(_label(severity))}</span>'


def _report_value(label: str, value: str, *, slow: bool = False) -> str:
    normalized = " ".join(label.replace("_", " ").split()).casefold()
    if normalized in {"database type", "database engine", "数据库类型"} or normalized == "type" and not slow:
        return display_database_type(value)
    if normalized in {"scenario", "diagnostic scenario", "诊断场景"}:
        return display_scenario(value.casefold() if slow else value)
    return display_slow_value(label, value) if slow else display_report_value(label, value)


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
               session_search: bool = False, tree: bool = False,
               section_id: str | None = None, slow: bool = False) -> str:
        label = display_slow_label if slow else _label
        headers = "".join(f"<th scope=\"col\">{_escape(label(header))}</th>" for header in table.headers)
        links = {(row_index, column_index): target
                 for row_index, column_index, target in getattr(table, "cell_links", ())
                 if type(row_index) is int and type(column_index) is int and isinstance(target, str)
                 and re.fullmatch(r"#[a-z][a-z0-9_-]{0,100}", target)}
        state_labels = {
            "blocked": ("danger", "已确认参与阻塞"),
            "observed-blocking": ("caution", "已观测阻塞，阻塞图不完整"),
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
                    "UNSUPPORTED": "caution", "UNAVAILABLE": "caution",
                    "SOURCE_UNAVAILABLE": "caution", "SOURCE_NOT_CONFIGURED": "caution",
                    "PERMISSION_DENIED": "danger", "COLLECTION_FAILED": "danger",
                    "IDENTITY_INCOMPLETE": "caution", "NOT_ASSESSED": "caution",
                }.get(normalized)
                is_status = column_index < len(table.headers)
                header = table.headers[column_index] if is_status else ""
                display_cell = (display_quality_impact(str(value))
                                if section_id == "collection-quality" and header == "Impact" else
                                _report_value(header, str(value), slow=slow) if is_status else str(value))
                escaped = (f'<span class="state-badge {badge_class}">{_escape(_label(display_cell))}</span>'
                           if badge_class else _escape(display_cell))
                state = table.row_states[row_index] if row_index < len(table.row_states) else ""
                if column_index == 0 and state in state_labels:
                    state_class, state_text = state_labels[state]
                    escaped = f'<span class="state-badge {state_class}">{state_text}</span> {escaped}'
                target = links.get((row_index, column_index))
                if target is not None:
                    escaped = f'<a href="{_escape(target)}">{escaped}</a>'
                cell_class = ' class="tree-session"' if tree and header == "Session" else ""
                cells.append(f"<td{cell_class}>{escaped}</td>")
            state = table.row_states[row_index] if row_index < len(table.row_states) else ""
            if state not in {"blocked", "clear", "partial", "observed-blocking"}:
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
        label = display_slow_label if slow else _label

        def fields_markup(fields: tuple[tuple[str, str], ...]) -> str:
            if not fields:
                return ""
            items = "".join(self._fragment("field.html", LABEL=_escape(label(field)),
                                           VALUE=_escape(_report_value(field, value, slow=slow)))
                            for field, value in fields)
            return f'<dl class="fields">{items}</dl>'
        searchable = section.section_id in {"appendix", "session-sources"}
        tables = "".join(self._table(
            table,
            table_id=("sessionTable" if section.section_id == "appendix" and index == 0 else
                      "sessionIdentityTable" if section.section_id == "session-sources" and index == 0 else
                      f"{section.section_id}-table-{index + 1}" if slow else None),
            session_search=searchable,
            tree=section.section_id == "blocking", section_id=section.section_id, slow=slow)
            for index, table in enumerate(section.tables))
        blocks: list[str] = []
        for block in section.code_blocks:
            code = self._fragment("code_block.html", TITLE=_escape(_label(block.title)),
                                  LANGUAGE=_escape("SQL" if slow and block.language == "sql" else block.language),
                                  CONTENT=_escape(block.content))
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
        summary_text = _escape(section.summary if section.summary_link else display_report_summary(section.summary))
        if section.summary_link and re.fullmatch(r"#[a-z][a-z0-9_-]{0,100}", section.summary_link):
            summary_text = f'<a href="{_escape(section.summary_link)}">{summary_text}</a>'
        summary = f"<p>{summary_text}</p>" if section.summary else ""
        detail = (self._fragment("details.html", TITLE="连接与请求详情", BODY=fields_markup(section.detail_fields))
                  .replace("<details>", '<details data-expandable>', 1) if section.detail_fields else "")
        body = summary + fields_markup(section.fields) + tables + "".join(blocks) + detail
        ordinary_sql_card = (section.severity.upper() == "INFO" and
                             section.section_id.startswith(("current-request-", "sql-resource-")))
        section_html = self._fragment("section.html", ID=_escape(section.section_id),
                                      TITLE=_escape(_label(section.title)),
                                      BADGE="" if ordinary_sql_card else _severity_badge(section.severity),
                                      BODY=body)
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
        if model.scenario == "top-sql":
            return self._render_top_sql(model)
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
        summary_body = "".join(self._fragment("field.html", LABEL=_escape(_label(label)),
                                                VALUE=_escape(_report_value(label, value)))
                               for label, value in summary_fields)
        summary_section = self._fragment(
            "section.html", ID="incident-summary", TITLE="故障概况",
            BADGE=_severity_badge(incident.severity),
            BODY=f"<div class=\"summary-metrics\">{summary_metrics}</div>"
                 f"<dl class=\"fields\">{summary_body}</dl>")

        metadata = "".join(self._fragment("field.html", LABEL=_escape(_label(label)),
                                    VALUE=_escape(_report_value(label, value)))
                            for label, value in model.metadata)
        sections = [self._section(section) for section in model.sections]
        toc = "".join(f"<a href=\"#{_escape(section.section_id)}\">{_escape(_label(section.title))}</a>"
                      for section in (ReportSection("incident-summary", "故障概况"), *model.sections))
        body = self._fragment("report_body.html", TITLE=_escape(model.title), SUMMARY=summary_section,
                              METADATA=metadata, TOC=toc, SECTIONS="".join(sections))
        page = self._template("report.html").replace("{{PAGE_TITLE}}", _escape(model.title))
        page = page.replace("{{BODY}}", body)
        return page + "\n"

    def _render_top_sql(self, model: ReportModel) -> str:
        """Render Top SQL as a statistics snapshot, not an incident assessment."""
        metadata = dict(model.metadata)
        section = next((item for item in model.sections if item.section_id == "top-sql"), None)
        fields = dict(section.fields) if section is not None else {}

        def value(*keys: str, default: str = "UNKNOWN") -> str:
            return next((str(source[key]) for source in (fields, metadata)
                         for key in keys if source.get(key) not in (None, "", "—")), default)

        collection_status = value("Collection Status")
        completeness = value("Completeness", "Data Completeness")
        summary_items = (
            ("数据库类型", value("Database Engine", "Database Type")),
            ("数据库版本", value("Database Version", "Version")),
            ("采集状态", collection_status),
            ("证据完整性", completeness),
            ("返回记录数", value("Returned Records", default="0")),
            ("排序指标", value("Ranking Metric", "Metric Name")),
            ("指标单位", value("Metric Unit")),
            ("指标语义", value("Metric Semantics")),
            ("统计范围", value("Metric Scope", "Collection Scope")),
            ("采集范围", value("Collection Scope")),
            ("数据来源", value("Evidence Source")),
            ("采集时间", value("Collection Timestamp")),
        )
        status_class = ("collected" if collection_status == "COLLECTED" else
                        "failed" if collection_status in {"FAILED", "PERMISSION_DENIED"} else
                        "limited" if collection_status not in {"UNKNOWN", ""} else "unknown")
        completeness_class = ("complete" if completeness == "COMPLETE" else
                              "partial" if completeness == "PARTIAL" else "unknown")
        cards: list[str] = []
        for label, item in summary_items:
            if label == "采集状态":
                rendered = (f'<span class="top-sql-badge status-{status_class}">'
                            f'{_escape(_label(item))}</span>')
            elif label == "证据完整性":
                rendered = (f'<span class="top-sql-badge completeness-{completeness_class}">'
                            f'{_escape(_label(item))}</span>')
            else:
                display_value = (display_report_value("Metric Semantics", item)
                                 if label == "指标语义" else
                                 display_database_type(item)
                                 if label == "数据库类型" else _label(item))
                rendered = _escape(display_value)
            cards.append(f'<div class="summary-metric"><strong>{_escape(label)}</strong> '
                         f'<span>{rendered}</span></div>')
        summary = ("Top SQL 展示数据库统计视图中的排序结果；累计统计不代表当前故障根因。"
                   if collection_status == "COLLECTED" else
                   "Top SQL 采集状态与证据完整性分别展示；失败或不可见不会伪装成完整结果。")
        summary_section = (
            '<section id="summary" class="top-sql-summary"><div class="section-heading">'
            f'<h2>Top SQL 统计摘要</h2><p>{_escape(summary)}</p></div>'
            f'<div class="summary-metrics">{"".join(cards)}</div></section>')
        report_sections = [item for item in model.sections if item.section_id != "collection-quality"]
        rendered_sections = "".join(self._section(item) for item in report_sections)
        toc = ''.join(f'<a href="#{_escape(item.section_id)}">{_escape(_label(item.title))}</a>'
                      for item in (ReportSection("summary", "Top SQL Summary"), *report_sections))
        body = self._fragment("report_body.html", TITLE=_escape(model.title), SUMMARY=summary_section,
                              METADATA="", TOC=toc, SECTIONS=rendered_sections)
        page = self._template("report.html").replace("{{PAGE_TITLE}}", _escape(model.title))
        page = page.replace("{{BODY}}", body)
        return page + "\n"

    def _render_slow(self, model: ReportModel) -> str:
        if any(section.section_id == "report-budget" for section in model.sections):
            # Evidence was already reduced by the shared policy. Keep the final shell
            # compact enough to honor a caller's hard byte ceiling.
            targets = {f"#{section.section_id}" for section in model.sections}
            bounded_sections = tuple(replace(
                section,
                tables=tuple(replace(table, cell_links=tuple(
                    link for link in table.cell_links if link[2] in targets)) for table in section.tables),
            ) for section in model.sections)
            return self.render(replace(model, scenario="bounded-slow", sections=bounded_sections))
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
            f'<div class="hero-meta-item"><span>{_escape(_label(key))}</span>'
            f'<strong>{_escape(_report_value(key, value, slow=True))}</strong></div>'
            for key, value in model.metadata)

        def meta_value(*keys: str, default: str = "UNKNOWN") -> str:
            return next((str(metadata[key]) for key in keys if metadata.get(key) not in (None, "")), default)

        database_type = meta_value("Database Type", "数据库类型", default="UNKNOWN").lower()
        blocked_count = pick(incident.impact, "blocked_requests", "blocked_sessions", "blocked_statements")
        postgresql_clear_snapshot = (database_type == "postgresql" and status == "HEALTHY"
                                     and blocked_count in (0, "0"))
        status_display = "本次采样未发现异常" if postgresql_clear_snapshot else _label(status)
        primary_problem_display = ("本次采样未发现异常" if postgresql_clear_snapshot
                                   else _label(incident.primary_problem))
        observed_now_display = ("当前未观测到阻塞" if postgresql_clear_snapshot
                                else _label(incident.observed_now))

        impact = incident.impact
        timing = incident.timing
        open_transactions = pick(impact, "open_transactions", "idle_transactions", "long_transactions")
        sqlserver_slow = database_type == "sqlserver" and model.scenario == "slow"
        if open_transactions is None and sqlserver_slow:
            open_transactions = _label("UNKNOWN")

        def sampled_count(value: object | None) -> object | None:
            return None if value == 0 and incident.data_completeness != "COMPLETE" else value

        metrics = (
            ("活动 SQL 会话" if "active_sql_sessions" in impact else
             "活动语句" if "active_statements" in impact else
             "活动会话" if "active_sessions" in impact and "active_requests" not in impact else "活动请求",
             sampled_count(pick(impact, "active_requests", "active_sql_sessions", "active_statements", "active_sessions"))),
            ("阻塞请求" if "blocked_requests" in impact else
             "阻塞会话" if "blocked_sessions" in impact else
             "受阻语句" if "blocked_statements" in impact else "阻塞请求",
             sampled_count(pick(impact, "blocked_requests", "blocked_sessions", "blocked_statements"))),
            ("根阻塞会话", sampled_count(pick(impact, "root_blockers"))),
            ("未结束事务", open_transactions),
            ("最长受阻锁等待" if sqlserver_slow else "最长阻塞",
             pick(timing, "longest_block", "longest_blocking", "blocking_duration")),
        )
        metric_html = "".join(
            f'<article class="metric {"danger" if label in {"阻塞请求", "阻塞会话", "受阻语句"} and value not in (None, 0, "0") else ""}">'
            f'<span class="metric-label">{_escape(label)}</span><strong>{_escape("未知" if value is None else value)}</strong></article>'
            for label, value in metrics)
        summary_lines = [
            f"诊断：{model.title}",
            f"实例：{meta_value('Instance', '实例')}",
            f"数据库：{display_database_type(meta_value('Database Type', '数据库类型'))} / "
            f"{display_slow_label(meta_value('Version', '版本'))}",
            f"采集：{display_slow_label(meta_value('Capture Started', '采集开始', default='N/A'))} — "
            f"{display_slow_label(meta_value('Capture Finished', '采集结束', default='N/A'))}",
            f"一致性：{display_slow_label(meta_value('Snapshot Consistency', '快照一致性', default='UNKNOWN'))}",
            f"采集完整性：{display_slow_value('采集完整性', incident.data_completeness)}",
            f"状态：{status_display} / {_label(severity)}；问题：{primary_problem_display}；观测：{observed_now_display}",
            *[f"{label if label.startswith('最长') else label + '数'}：{'未知' if value is None else value}"
              for label, value in metrics],
        ]
        summary_copy = "\n".join(summary_lines)

        def render_section(section_id: str, *, collapsed: bool = False) -> str:
            section = sections.get(section_id)
            if section is None:
                return ""
            if collapsed and not section.collapsed:
                section = replace(section, collapsed=True)
            return self._section(section, slow=True)

        chain = render_section("blocking")
        problems = "".join(self._section(section, slow=True) for section in model.sections
                           if section.section_id.startswith("diagnosis-"))
        recovery = render_section("immediate-recovery")
        quality = render_section("collection-quality")
        transactions = render_section("transaction-analysis")
        current_requests_html = "".join(
            self._section(section, slow=True)
            for section in model.sections
            if section.section_id == "request-runtime"
            or section.section_id.startswith("current-request-")
        )
        performance_ids = ("observed-wait", "workload-summary", "nonblocked-long-running", "active-waits")
        resource_ids = tuple(section.section_id for section in model.sections if section.section_id.startswith("sql-resource-"))
        performance = "".join(render_section(item) for item in resource_ids)
        resource_scope = "没有可展示的资源卡，活动请求采集状态见第 03 节。"
        if resource_ids:
            resource_scope = ("资源计数从当前请求开始累计至采集时刻，展示范围见第 03 节。"
                              if sqlserver_slow else "按活动请求展示已采集的资源与等待，展示范围见第 03 节。")
        reserved = {"blocking", "immediate-recovery", "transaction-analysis",
                    "collection-quality", "request-runtime", *resource_ids, *performance_ids,
                    "findings", "priority-candidates", "root-related-requests", "blocked-long-running", "blocked-victims"}
        reserved.update(section.section_id for section in model.sections
                        if section.section_id.startswith(("current-request-", "diagnosis-")))
        evidence_sections = "".join(
            render_section(section.section_id, collapsed=True)
            for section in model.sections if section.section_id not in reserved)
        toc_items = [("summary", "诊断概况"), ("root-assessment", "性能问题诊断"),
                     ("current-requests", "当前活动 SQL"), ("blocking-chain", "阻塞关系"),
                     ("transactions", "事务分析"), ("evidence", "SQL 资源消耗与等待分析"),
                     ("session-appendix", "高级诊断附录")]
        toc = "".join(f'<a href="#{anchor}">{_escape(label)}</a>' for anchor, label in toc_items)
        page = self._template("slow_report.html")
        values = {
            "PAGE_TITLE": _escape(model.title), "TITLE": _escape(model.title),
            "STATUS": _escape(status_display), "SEVERITY": _escape(_label(severity)),
            "STATUS_CLASS": status_class,
            "PRIMARY_PROBLEM": _escape(primary_problem_display),
            "OBSERVED_NOW": _escape(observed_now_display),
            "COMPLETENESS": _escape(display_slow_value("采集完整性", incident.data_completeness)),
            "SNAPSHOT_CONSISTENCY": _escape(display_slow_label(meta_value(
                "Snapshot Consistency", "快照一致性", default="UNKNOWN"))),
            "METADATA": metadata_html, "METRICS": metric_html,
            "TOC": toc, "ROOT_ASSESSMENT": problems,
            "CURRENT_REQUESTS": current_requests_html,
            "BLOCKING_CHAIN": chain, "RECOVERY": recovery,
            "TRANSACTIONS": transactions, "RESOURCE_SCOPE": _escape(resource_scope),
            "EVIDENCE": performance, "QUALITY": quality,
            "APPENDIX": evidence_sections, "SUMMARY_COPY": _escape(summary_copy),
        }
        for key, value in values.items():
            page = page.replace("{{" + key + "}}", value)
        return page + "\n"


def _label(value: str) -> str:
    return display_label(value)


def _local_snapshot(value: str) -> str:
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.isoformat(sep=" ", timespec="seconds") + " (时区未知)"
        return parsed.astimezone().isoformat(sep=" ", timespec="seconds")
    except ValueError:
        return value
