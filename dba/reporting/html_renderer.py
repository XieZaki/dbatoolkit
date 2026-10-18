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
        headers = "".join(f"<th scope=\"col\">{_escape(header)}</th>" for header in table.headers)
        rows = "".join("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in row) + "</tr>"
                       for row in table.rows)
        return self._fragment("table.html", HEADERS=headers, ROWS=rows)

    def _section(self, section: ReportSection) -> str:
        fields = "".join(self._fragment("field.html", LABEL=_escape(label), VALUE=_escape(value))
                         for label, value in section.fields)
        tables = "".join(self._table(table) for table in section.tables)
        blocks: list[str] = []
        for block in section.code_blocks:
            code = self._fragment("code_block.html", TITLE=_escape(block.title),
                                  LANGUAGE=_escape(block.language), CONTENT=_escape(block.content))
            blocks.append(self._fragment("details.html", TITLE=_escape(block.title), BODY=code)
                          if block.collapsed else code)
        summary = f"<p>{_escape(section.summary)}</p>" if section.summary else ""
        body = summary + (f"<dl class=\"fields\">{fields}</dl>" if fields else "") + tables + "".join(blocks)
        severity = _escape(section.severity.upper())
        section_html = self._fragment("section.html", ID=_escape(section.section_id),
                                      TITLE=_escape(section.title), SEVERITY=severity, BODY=body)
        if section.collapsed:
            section_html = section_html.replace(f'id="{_escape(section.section_id)}"', "")
            return self._fragment("collapsible_section.html", ID=_escape(section.section_id),
                                  TITLE=_escape(section.title), BODY=section_html)
        return section_html

    def render(self, model: ReportModel) -> str:
        incident = model.incident_summary
        cards = (
            ("Status", incident.status), ("Primary Problem", incident.primary_problem),
            ("Observed Now", incident.observed_now), ("Severity", incident.severity),
        )
        summary_metrics = "".join(
            f"<div class=\"summary-metric\"><strong>{_escape(label)}</strong> "
            f"<span>{_escape(value)}</span></div>" for label, value in cards)
        summary_fields: list[tuple[str, str]] = []
        summary_fields.append(("Data Completeness", incident.data_completeness))
        if incident.required_evidence_status != "NOT_APPLICABLE":
            summary_fields.extend((
                ("Required Evidence", incident.required_evidence_status),
                ("Optional Evidence", incident.optional_evidence_status),
            ))
        if incident.unavailable_optional_evidence:
            summary_fields.append(("Unavailable Optional Evidence",
                                   ", ".join(incident.unavailable_optional_evidence)))
        for impact_key, impact_value in incident.impact.items():
            summary_fields.append((_label(impact_key), str(impact_value)))
        for timing_key, timing_value in incident.timing.items():
            summary_fields.append((_label(timing_key), timing_value))
        if incident.wait:
            summary_fields.extend((("Primary Wait Category", incident.wait.get("category", "UNKNOWN")),
                                   ("Primary Wait Event", incident.wait.get("event", "UNKNOWN"))))
        summary_fields.append(("Snapshot Time", _local_snapshot(incident.snapshot_time)))
        summary_body = "".join(self._fragment("field.html", LABEL=_escape(label), VALUE=_escape(value))
                               for label, value in summary_fields)
        summary_section = self._fragment(
            "section.html", ID="incident-summary", TITLE="Incident Summary",
            SEVERITY=_escape(incident.severity),
            BODY=f"<div class=\"summary-metrics\">{summary_metrics}</div>"
                 f"<dl class=\"fields\">{summary_body}</dl>")

        metadata = "".join(self._fragment("field.html", LABEL=_escape(label), VALUE=_escape(value))
                            for label, value in model.metadata)
        sections = [self._section(section) for section in model.sections]
        toc = "".join(f"<a href=\"#{_escape(section.section_id)}\">{_escape(section.title)}</a>"
                      for section in (ReportSection("incident-summary", "Incident Summary"), *model.sections))
        body = self._fragment("report_body.html", TITLE=_escape(model.title), SUMMARY=summary_section,
                              METADATA=metadata, TOC=toc, SECTIONS="".join(sections))
        page = self._template("report.html").replace("{{PAGE_TITLE}}", _escape(model.title))
        page = page.replace("{{BODY}}", body)
        return page + "\n"


def _label(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def _local_snapshot(value: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).astimezone().isoformat(sep=" ", timespec="seconds")
    except ValueError:
        return value
