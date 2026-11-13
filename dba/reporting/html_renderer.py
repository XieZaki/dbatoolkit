"""Escaped, offline HTML rendering from the shared report model."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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
        searchable = not slow and section.section_id in {"appendix", "session-sources"}
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

        def meta_value(*keys: str, default: str = "UNKNOWN") -> str:
            return next((str(metadata[key]) for key in keys if metadata.get(key) not in (None, "")), default)

        status = incident.status or "UNKNOWN"
        severity = incident.severity or "UNKNOWN"
        status_class = ("danger" if severity in {"CRITICAL", "HIGH"} or status == "CRITICAL" else
                        "clear" if status == "HEALTHY" and incident.data_completeness == "COMPLETE" else
                        "caution" if status in {"DEGRADED", "PARTIAL"} or
                        incident.data_completeness != "COMPLETE" else "unknown")
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
        root_sessions = [section.fields[0][1] for section in model.sections
                         if section.section_id.startswith("wb-root-blocker-") and section.fields]
        kpi_feet = ("单次有界采集", f"{metrics[4][0]} {'未知' if metrics[4][1] is None else metrics[4][1]}",
                    "会话 " + "、".join(root_sessions[:3]) if root_sessions else "本次采样未发现根阻塞",
                    "含已关联与未关联明细的事务")
        kpi_html = "".join(
            f'<article class="card kpi"><div class="kpi-top"><span class="metric-label">{_escape(label)}</span>'
            f'<span class="kpi-icon" aria-hidden="true">{icon}</span></div>'
            f'<strong class="kpi-value{" danger" if index in (1, 2) and value not in (None, 0, "0") else ""}">'
            f'{_escape("未知" if value is None else value)}</strong>'
            f'<div class="kpi-foot">{_escape(foot)}</div></article>'
            for index, ((label, value), icon, foot) in enumerate(zip(metrics[:4], ("⌁", "⊣", "◎", "⧗"), kpi_feet)))
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

        def render_section(section_id: str, *, collapsed: bool | None = None) -> str:
            section = sections.get(section_id)
            if section is None:
                return ""
            if collapsed is not None and section.collapsed != collapsed:
                section = replace(section, collapsed=collapsed)
            return self._section(section, slow=True)

        def table_of(section_id: str) -> ReportTable | None:
            section = sections.get(section_id)
            return section.tables[0] if section is not None and section.tables else None

        def links_of(table: ReportTable) -> dict[tuple[int, int], str]:
            return {(row, column): target for row, column, target in table.cell_links
                    if re.fullmatch(r"#[a-z][a-z0-9_-]{0,100}", target)}

        # Overview: primary conclusion, evidence and impact ranking.
        problems = [section for section in model.sections
                    if section.section_id.startswith("diagnosis-") and section.section_id != "diagnosis-sql-concurrency"]
        primary = problems[0] if problems else ReportSection("diagnosis-result", "本次诊断结论",
                                                             summary="现有证据不足，无法确认当前性能问题。")
        primary_fields = dict(primary.fields)
        cause = next((value for label, value in primary.fields if label in {"根阻塞关联证据", "发生原因"}), "")
        conclusion = primary_fields.get("诊断结论") or primary.summary
        severity_tag = {"CRITICAL": ("tag-red", "高优先级"), "HIGH": ("tag-red", "高优先级"),
                        "WARNING": ("tag-amber", "中优先级")}.get(primary.severity.upper(), ("tag-blue", "信息"))
        tone = "danger" if severity_tag[0] == "tag-red" else "caution" if severity_tag[0] == "tag-amber" else "info"
        evidence_section = sections.get("wb-primary-evidence")
        evidence_html = "".join(
            f'<div class="evidence"><strong>{_state_value(value)}</strong><span>{_escape(label)}</span></div>'
            for label, value in (evidence_section.fields if evidence_section else ()))
        other_problems = "".join(
            f'<li><span class="tag {"tag-red" if item.severity.upper() in {"CRITICAL", "HIGH"} else "tag-amber" if item.severity.upper() == "WARNING" else "tag-blue"}">'
            f'{_escape(dict(item.fields).get("严重程度", _label(item.severity)))}</span>'
            f'<strong>{_escape(_label(item.title))}</strong><span>{_escape(dict(item.fields).get("诊断结论", item.summary))}</span></li>'
            for item in problems[1:])
        recommendation = primary_fields.get("建议操作")
        diagnosis_html = (
            f'<div class="section-head"><div><div class="section-title">当前首要诊断结论</div>'
            f'<div class="section-sub">按业务影响排序 · 证据不足时不下结论</div></div>'
            f'<span class="tag {severity_tag[0]}">{severity_tag[1]}</span></div>'
            f'<div class="diagnosis-main"><div class="alert-icon" aria-hidden="true">!</div><div>'
            f'<h3>{_escape(_label(primary.title))}</h3><p>{_escape(" ".join(item for item in (conclusion, cause) if item))}</p>'
            f'</div></div>'
            + (f'<div class="evidence-row">{evidence_html}</div>' if evidence_html else "")
            + '<div class="action-row"><a class="button button-primary" href="#blocking-chain">查看阻塞源与锁</a>'
              '<button class="button button-danger" id="openRecovery" type="button">查看人工处置参考</button>'
              '<a class="button" href="#current-requests">查看当前执行 SQL</a></div>'
            + (f'<p class="note">建议：{_escape(recommendation)}</p>' if recommendation else "")
            + '<p class="note">Toolkit 不执行 KILL、TERMINATE 或 CANCEL；终止会话可能触发长时间回滚，须由 DBA 人工判断。</p>'
            + (f'<ul class="issue-list">{other_problems}</ul>' if other_problems else "")
            + (f'<details data-expandable><summary>诊断依据与可信程度（{len(problems)} 项）</summary>'
               + "".join(self._section(item, slow=True) for item in problems) + "</details>" if problems else ""))
        diagnosis_class = f"card card-pad diagnosis {tone}"

        top_table = table_of("wb-top-impact")
        top_html = "".join(
            f'<button class="priority-item" type="button" data-open-session="{_escape(row[1])}">'
            f'<span class="priority-rank">{_escape(row[0])}</span><span class="priority-info">'
            f'<span class="priority-name">会话 {_escape(row[1])}</span><span class="priority-meta">{_escape(row[2])}</span></span>'
            f'<span class="priority-impact"><strong>{_escape(row[3])}</strong></span></button>'
            for row in (top_table.rows if top_table else ()))
        top_html = top_html or '<p class="empty">本次采样没有需要排序的会话。</p>'

        resources = sections.get("wb-resources")
        resource_notes = dict(resources.detail_fields) if resources else {}
        resource_judgement = (f'<p class="note">{_escape(resources.summary)}</p>'
                              if resources and resources.summary else "")
        overview_resources = "".join(
            f'<div class="resource-line"><span>{_escape(label)}</span><strong>{_state_value(value)}</strong></div>'
            for label, value in (resources.fields if resources else ())) + resource_judgement
        report_info = "".join(
            f'<div class="detail-cell"><span class="detail-label">{_escape(_label(key))}</span>'
            f'<strong>{_escape(_report_value(key, value, slow=True))}</strong></div>'
            for key, value in model.metadata)

        # Current SQL: impact-ordered list plus one detail panel per current request.
        sql_table = table_of("wb-sql-list")
        sql_rows = []
        if sql_table is not None:
            sql_links = links_of(sql_table)
            for row_index, row in enumerate(sql_table.rows):
                target = sql_links.get((row_index, 1), "")
                index = target.rsplit("-", 1)[-1] if target else ""
                cells = [f'<td><span class="priority-rank">{_escape(row[0])}</span></td>',
                         f'<td class="sql-text"><a href="{_escape(target)}">{_escape(row[1])}</a></td>'
                         if target else f'<td class="sql-text">{_escape(row[1])}</td>',
                         f'<td class="mono">{_escape(row[2])}</td>']
                cells.extend(f"<td>{_state_value(value)}</td>" for value in row[3:])
                sql_rows.append(f'<tr data-sql-index="{_escape(index)}" tabindex="0">{"".join(cells)}</tr>')
        sql_headers = "".join(f'<th scope="col">{_escape(header)}</th>' for header in (sql_table.headers if sql_table else ()))
        sql_list = (f'<div class="table-wrap"><table data-sql-table><thead><tr>{sql_headers}</tr></thead>'
                    f'<tbody>{"".join(sql_rows)}</tbody></table></div>'
                    if sql_rows else '<p class="empty">本次采样没有当前活动 SQL。</p>')
        sql_summary = sections["wb-sql-list"].summary if "wb-sql-list" in sections else ""
        detail_panels = []
        for section in model.sections:
            if not section.section_id.startswith("current-request-"):
                continue
            index = section.section_id.rsplit("-", 1)[-1]
            resource = sections.get(f"sql-resource-{index}")
            detail_panels.append(
                f'<div class="sql-detail" data-sql-detail="{_escape(index)}"><div class="two-col">'
                f'{self._section(replace(section, collapsed=False), slow=True)}'
                f'{self._section(replace(resource, collapsed=False), slow=True) if resource else ""}</div></div>')
        current_requests = render_section("request-runtime") + "".join(detail_panels)

        # Blocking: banner, chain visualization, manual reference, root details and victims.
        roots = [section for section in model.sections if section.section_id.startswith("wb-root-blocker-")]
        banner = ""
        if roots:
            first = dict(roots[0].fields)
            banner = (f'<div class="blocker-banner"><div><strong>根阻塞会话 {_escape(first.get("会话", ""))} · '
                      f'{_escape(_report_value("状态", first.get("当前状态", ""), slow=True))} · '
                      f'影响 {_escape(first.get("影响会话数", "0"))} 个会话</strong><br>'
                      f'<span>{_escape(first.get("事务", ""))}；会话状态须与事务和锁一起判断。</span></div>'
                      '<a class="button button-danger" href="#manual-action">查看人工处置参考</a></div>')
        blocking_table = table_of("blocking")
        chain_html = ""
        if blocking_table is not None and blocking_table.rows:
            headers = blocking_table.headers
            blocking_links = links_of(blocking_table)
            levels: dict[str, list[str]] = {}
            for row_index, row in enumerate(blocking_table.rows):
                values = dict(zip(headers, row, strict=False))
                session = values.get("Session", "").replace("└─", "").strip()
                role = values.get("Role", "")
                link = blocking_links.get((row_index, len(headers) - 1))
                node = (f'<div class="chain-node{" root" if role == "Root Blocker" else ""}">'
                        f'<div class="sid">层级 {_escape(values.get("层级", ""))} · '
                        f'{_escape(_report_value("Role", role, slow=True))}</div>'
                        f'<div class="node-title">会话 {_escape(session)}</div>'
                        f'<div class="node-meta">状态 {_escape(_report_value("Status", values.get("Status", "—"), slow=True))}'
                        f'<br>直接阻塞者 {_escape(values.get("Direct Blocker", "—"))}</div>'
                        + (f'<a href="{_escape(link)}">当前 SQL</a>' if link else "")
                        + "</div>")
                levels.setdefault(values.get("层级", "0"), []).append(node)
            columns = [f'<div class="chain-level">{"".join(nodes)}</div>' for _, nodes in sorted(
                levels.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 0)]
            arrow = '<div class="chain-arrow" aria-hidden="true">→</div>'
            chain_html = f'<div class="chain">{arrow.join(columns)}</div>'
        blocking_section = sections.get("blocking")
        chain_summary = _escape(display_report_summary(blocking_section.summary)) if blocking_section else ""
        chain_body = (chain_html or f'<p class="empty">{chain_summary or "本次采样未观测到阻塞关系。"}</p>')
        if blocking_section is not None and blocking_table is not None and blocking_table.rows:
            chain_body += (f'<details data-expandable><summary>阻塞树明细</summary>'
                           f'{self._section(blocking_section, slow=True)}</details>')
        root_cards = []
        for root in roots:
            fields = dict(root.fields)
            grid = _detail_grid(
                (label, _report_value("状态", value, slow=True) if label == "当前状态" else value)
                for label, value in root.fields
                if label in {"会话", "当前状态", "影响会话数", "事务", "用户", "客户端主机", "客户端 IP", "程序"})
            sql_parts = []
            if "当前 SQL" in fields:
                link = root.summary_link or ""
                sql_parts.append('<div class="source-title">当前 SQL'
                                 + (f' · <a href="{_escape(link)}">查看详情</a>' if link else "")
                                 + f'</div><div class="sql-box">{_escape(fields["当前 SQL"])}</div>')
            for block in root.code_blocks:
                sql_parts.append(f'<div class="source-title">{_escape(block.title)} '
                                 f'<button class="button button-copy" type="button" data-copy="{_escape(block.content)}">复制 SQL</button>'
                                 f'</div><pre class="sql-box"><code>{_escape(block.content)}</code></pre>')
            for label, value in root.fields:
                if label.startswith("上一条 SQL"):
                    sql_parts.append(f'<div class="source-title">{_escape(label)} {_state_value(value)}</div>')
            lock_grid = _detail_grid((label, value) for label, value in root.fields
                                     if label in {"锁等待资源（原始值）", "锁对象（库 / 表 / 索引）", "锁模式",
                                                  "锁资源（锁 DMV 原始值）", "锁对象解析"})
            lock_sub = ("对象名称来自锁 DMV 与资源所属数据库元数据；原始资源保留" if "锁对象解析" in fields
                        else "锁资源为采集原始值；解析到表名待开发")
            root_cards.append(
                f'<div class="card card-pad"><div class="section-head"><div><div class="section-title">'
                f'{_escape(root.title)}</div><div class="section-sub">当前状态、事务与 SQL 上下文</div></div>'
                f'<span class="tag tag-red">根阻塞</span></div>{grid}'
                f'<div class="source-block">{"".join(sql_parts)}</div>'
                f'<p class="note">上一条 SQL 仅为会话上下文，不能证明其取得了当前锁。</p></div>'
                f'<div class="card card-pad"><div class="section-head"><div><div class="section-title">锁对象与影响范围'
                f'</div><div class="section-sub">{lock_sub}</div></div></div>{lock_grid}</div>')
        victims = render_section("wb-blocked-sessions")
        root_details = (f'<div class="two-col">{"".join(root_cards)}</div>' if root_cards else "") + victims

        # Plans: TOP 5 current SQL on the left, selected plan panel on the right.
        plan_section = sections.get("plan-status")
        plan_table = plan_section.tables[0] if plan_section and plan_section.tables else None
        plan_html = f'<p class="empty">{_escape(plan_section.summary if plan_section else "本次采样没有当前活动 SQL。")}</p>'
        if plan_table is not None and plan_table.rows:
            plan_links = links_of(plan_table)
            items = []
            panels = []
            for row_index, row in enumerate(plan_table.rows):
                values = dict(zip(plan_table.headers, row, strict=False))
                link = plan_links.get((row_index, 1), "")
                items.append(f'<button class="plan-list-item" type="button" data-plan-select="{row_index}">'
                             f'<span class="plan-query">{_escape(values.get("SQL 摘要", ""))}</span>'
                             f'<span class="plan-meta">#{_escape(values.get("排序", ""))} · Session '
                             f'{_escape(values.get("Session / Request", ""))} · 计划 {_escape(values.get("计划状态", ""))}</span></button>')
                access = values.get("独立获取方式", "")
                command = access.split("（", 1)[0] if access.startswith("dba ") else ""
                meta = _detail_grid(((label, values.get(label, "")) for label in
                                     ("Session / Request", "SQL 标识", "计划标识", "计划状态", "运行统计")),
                                    cell_class="plan-meta-cell", grid_class="plan-meta-grid")
                panels.append(
                    f'<div class="plan-panel" data-plan-panel="{row_index}"><div class="plan-summary"><div>'
                    f'<div class="section-title">排序 {_escape(values.get("排序", ""))} · Session '
                    f'{_escape(values.get("Session / Request", ""))}</div>'
                    f'<div class="section-sub">计划与该请求按数据库原生标识关联</div></div></div>'
                    f'<div class="plan-sql-panel"><div class="plan-sql-head"><strong>SQL 摘要</strong>'
                    + (f'<a href="{_escape(link)}">查看完整 SQL</a>' if link else "")
                    + f'</div><div class="sql-box">{_escape(values.get("SQL 摘要", ""))}</div></div>{meta}'
                    f'<div class="plan-flow"><div class="plan-node placeholder"><div class="plan-node-top">'
                    f'<span class="plan-op">执行计划树</span>{_state_value(values.get("计划状态", "待开发"))}</div>'
                    f'<div class="plan-node-meta"><span>Slow 报告不内嵌计划，不展示模拟计划。</span></div></div></div>'
                    + (f'<div class="plan-runtime-note"><strong>独立获取方式</strong>'
                       f'<pre class="sql-box"><code>{_escape(command)}</code></pre>'
                       f'<button class="button button-copy" type="button" data-copy="{_escape(command)}">复制命令</button>'
                       f'<span>{_escape(access[len(command):].strip("（）"))}</span></div>' if command else "")
                    + "</div>")
            plan_html = (f'<div class="plan-layout"><div class="card"><div class="card-pad plan-list-head">'
                         f'<div class="section-title">当前 SQL · TOP {len(items)}</div>'
                         f'<div class="section-sub">{_escape(plan_section.summary if plan_section else "")}</div></div>'
                         f'{"".join(items)}</div><div class="card plan-canvas">{"".join(panels)}</div></div>')

        # Resources and engine signals.
        resource_cards = "".join(
            f'<div class="card resource-card"><div class="small">{_escape(label)}</div>'
            f'<div class="big">{_state_value(value)}</div><div class="small">{_escape(resource_notes.get(label, ""))}</div></div>'
            for label, value in (resources.fields if resources else ()))
        signals = sections.get("wb-engine-signals")
        signal_notes = dict(signals.detail_fields) if signals else {}
        signal_html = "".join(
            f'<div class="signal"><div class="signal-top"><span class="signal-name">{_escape(name)}</span>'
            f'{_state_value(value)}</div><div class="signal-note">{_escape(signal_notes.get(name, ""))}</div></div>'
            for name, value in (signals.fields if signals else ())) or '<p class="empty">本报告未提供该数据库的专项信号。</p>'

        # Sessions: searchable list; the drawer reads the matching hidden detail block.
        session_table = table_of("wb-sessions")
        detail_table = table_of("wb-session-details")
        filters = {"根阻塞": ("blocking", "tag-red"), "被阻塞": ("blocked", "tag-amber"),
                   "执行中": ("active", "tag-blue"), "空闲": ("idle", "tag-gray")}
        state_labels = {"blocked": "已确认参与阻塞", "observed-blocking": "已观测阻塞，阻塞图不完整",
                        "clear": "完整采样确认未参与阻塞", "partial": "阻塞状态未能确认"}
        session_rows = []
        session_details = []
        if session_table is not None:
            for row_index, row in enumerate(session_table.rows):
                category = row[-1]
                filter_value, tag = filters.get(category, ("idle", "tag-gray"))
                state = session_table.row_states[row_index] if row_index < len(session_table.row_states) else ""
                cells = [f'<td><span class="strong">{_escape(row[0])}</span> <span class="tag {tag}">{_escape(category)}</span></td>']
                for header, value in zip(session_table.headers[1:-1], row[1:-1], strict=False):
                    shown = _report_value("状态", value, slow=True) if header == "状态" else value
                    cells.append(f"<td>{_state_value(shown)}</td>")
                state_attr = (f' class="row-{state}" data-diagnostic-state="{state}" title="{state_labels[state]}"'
                              if state in state_labels else "")
                session_rows.append(f'<tr{state_attr} data-session="{_escape(row[0])}" data-filter="{filter_value}" '
                                    f'data-detail-index="{row_index}">{"".join(cells)}</tr>')
        if detail_table is not None:
            detail_links = links_of(detail_table)
            for row_index, row in enumerate(detail_table.rows):
                values = dict(zip(detail_table.headers, row, strict=False))
                grid = _detail_grid(
                    (label, _report_value("状态", value, slow=True) if label == "状态" else value)
                    for label, value in values.items() if label not in {"SQL 来源", "SQL 文本"})
                source = values.get("SQL 来源", "")
                text = values.get("SQL 文本", "")
                link = detail_links.get((row_index, detail_table.headers.index("SQL 来源")), "")
                sql_markup = (f'<div class="source-title">{_escape(source)} {_state_value(text)}</div>'
                              if text in _STATES else
                              f'<div class="source-title">{_escape(source)}'
                              + (f' · <a href="{_escape(link)}">查看当前执行 SQL</a></div>'
                                 f'<div class="sql-box">{_escape(text)}</div>' if link else
                                 f' <button class="button button-copy" type="button" data-copy="{_escape(text)}">复制 SQL</button>'
                                 f'</div><pre class="sql-box"><code>{_escape(text)}</code></pre>'))
                session_details.append(f'<div data-session-detail="{row_index}">{grid}'
                                       f'<div class="source-block">{sql_markup}</div></div>')
        session_headers = "".join(f'<th scope="col">{_escape(header)}</th>'
                                  for header in (session_table.headers[:-1] if session_table else ()))
        session_list = (f'<div class="table-wrap"><table id="sessionTable" data-session-search="true"><thead><tr>'
                        f'{session_headers}</tr></thead><tbody>{"".join(session_rows)}</tbody></table></div>'
                        if session_rows else '<p class="empty">本次采样没有可展示的会话。</p>')
        session_summary = sections["wb-sessions"].summary if "wb-sessions" in sections else ""

        reserved = {"blocking", "immediate-recovery", "transaction-analysis", "collection-quality",
                    "request-runtime", "observed-wait", "workload-summary", "nonblocked-long-running",
                    "active-waits", "plan-status", "findings", "priority-candidates", "root-related-requests",
                    "blocked-long-running", "blocked-victims", "diagnosis-sql-concurrency"}
        reserved.update(section.section_id for section in model.sections
                        if section.section_id.startswith(("current-request-", "sql-resource-", "diagnosis-", "wb-")))
        appendix = "".join(render_section(section.section_id, collapsed=True)
                           for section in model.sections if section.section_id not in reserved)

        def nav_count(value: object) -> str:
            return (f'<span class="nav-count">{_escape(value)}</span>'
                    if type(value) is int and value > 0 else "")

        toc_items = [("page-overview", "◈", "诊断总览", ""),
                     ("page-sessions", "▤", "活跃会话", nav_count(len(session_rows))),
                     ("page-blocking", "⊣", "阻塞分析", nav_count(metrics[1][1])),
                     ("page-sql", "⌁", "当前执行 SQL", nav_count(metrics[0][1])),
                     ("page-plans", "⌘", "执行计划", ""),
                     ("page-resources", "▥", "资源与引擎诊断", "")]
        active_tab = ' class="active" aria-current="page"'
        toc = "".join(
            (('<div class="nav-heading">根因分析</div>' if anchor == "page-resources" else "")
             + f'<a href="#{anchor}" data-tab="{anchor}"{active_tab if index == 0 else ""}>'
             f'<span class="nav-icon" aria-hidden="true">{icon}</span><span>{_escape(label)}</span>{count}</a>')
            for index, (anchor, icon, label, count) in enumerate(toc_items))
        endpoint = meta_value("Endpoint", "连接地址", default="")
        engine_name = display_database_type(meta_value("Database Type", "数据库类型"))
        version = meta_value("Version", "版本", default="")
        known_version = version.upper() not in {"", "UNKNOWN", "UNAVAILABLE"} and version not in {"未知", "不可用"}
        chips = [("engine", f"{engine_name} {display_slow_label(version)}" if known_version
                  else f"{engine_name}（版本未知）"),
                 ("", f"{meta_value('Instance', '实例')} / {meta_value('Database ID', '数据库 ID')}")]
        if endpoint:
            chips.append(("endpoint", endpoint))
        instance_chips = "".join(f'<span class="instance-chip {kind}">{_escape(text)}</span>'
                                 for kind, text in chips)
        values = {
            "PAGE_TITLE": _escape(model.title), "TITLE": _escape(model.title),
            "STATUS": _escape(status_display), "SEVERITY": _escape(_label(severity)),
            "STATUS_CLASS": status_class,
            "PRIMARY_PROBLEM": _escape(primary_problem_display),
            "OBSERVED_NOW": _escape(observed_now_display),
            "COMPLETENESS": _escape(display_slow_value("采集完整性", incident.data_completeness)),
            "SNAPSHOT_CONSISTENCY": _escape(display_slow_label(meta_value(
                "Snapshot Consistency", "快照一致性", default="UNKNOWN"))),
            "CAPTURE_TIME": _escape(display_slow_label(meta_value("Capture Finished", "采集结束", default="未知"))),
            "KPIS": kpi_html, "DIAGNOSIS_CLASS": diagnosis_class, "DIAGNOSIS": diagnosis_html,
            "TOP_IMPACT": top_html, "OVERVIEW_RESOURCES": overview_resources, "REPORT_INFO": report_info,
            "SQL_CONCURRENCY": render_section("diagnosis-sql-concurrency"),
            "SQL_SUMMARY": _escape(sql_summary), "SQL_LIST": sql_list, "CURRENT_REQUESTS": current_requests,
            "BLOCKER_BANNER": banner, "BLOCKING_CHAIN": chain_body, "RECOVERY": render_section("immediate-recovery"),
            "ROOT_DETAILS": root_details, "TRANSACTIONS": render_section("transaction-analysis"),
            "PLANS": plan_html, "RESOURCE_JUDGEMENT": resource_judgement,
            "RESOURCE_CARDS": resource_cards, "ENGINE_SIGNALS": signal_html,
            "ENGINE_NAME": _escape(engine_name),
            "SESSION_SUMMARY": _escape(session_summary), "SESSION_LIST": session_list,
            "SESSION_DETAILS": "".join(session_details),
            "QUALITY": render_section("collection-quality"), "APPENDIX": appendix,
            "SUMMARY_COPY": _escape("\n".join(summary_lines)),
            "INSTANCE_CHIPS": instance_chips, "TOC": toc,
        }
        page = self._template("slow_report.html")
        page = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values.get(match.group(1), match.group(0)), page)
        return page + "\n"


_STATES = {"待开发", "未采集", "不支持"}


def _state_value(value: str) -> str:
    text = str(redact(value))
    if text in {"待开发", "不支持"}:
        return f'<span class="tag tag-gray">{escape(text)}</span>'
    if text == "未采集":
        return '<span class="tag tag-amber">未采集</span>'
    return _escape(value)


def _detail_grid(pairs: Iterable[tuple[str, str]], *, cell_class: str = "detail-cell",
                 grid_class: str = "detail-grid") -> str:
    cells = "".join(f'<div class="{cell_class}"><span class="detail-label">{_escape(label)}</span>'
                    f'<strong>{_state_value(value)}</strong></div>' for label, value in pairs)
    return f'<div class="{grid_class}">{cells}</div>' if cells else ""


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
