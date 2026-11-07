from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from test_html_reports import _report

from dba.cli import main
from dba.incident import build_incident_summary
from dba.presentation import render_human, render_json
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.reporting.normalizers import (
    display_database_type,
    display_error_message,
    display_label,
    display_status_value,
)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "pre", "code"}:
            self._ignored.append(tag)
        if self._ignored:
            return
        for key, value in attrs:
            if key in {"aria-label", "placeholder", "title"} and value:
                self.parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored and self._ignored[-1] == tag:
            self._ignored.pop()

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip())


def test_display_dictionary_keeps_native_names_and_translates_known_ui_terms() -> None:
    assert display_label("Collection Status") == "采集状态"
    assert display_label("Root Cause Candidate") == display_label("Root cause candidate") == "根因候选"
    assert display_status_value("Collection Status", "PARTIAL") == "部分可用"
    assert display_database_type("sqlserver") == "SQL Server"
    assert display_label("SQL_ID") == "SQL_ID"
    assert display_label("SQL Handle") == "SQL Handle"
    assert display_error_message("NotImplemented", "not translated") == "当前数据库类型尚未实现此功能。"


def test_slow_html_visible_ui_has_no_known_english_labels_or_status_tokens() -> None:
    report = _report()
    model = build_report_model(report, build_incident_summary(report))
    page = HtmlReportRenderer().render(model)
    visible = _VisibleText()
    visible.feed(page)
    text = " ".join(visible.parts)

    assert "根因候选" in text
    assert "采集完整性" in text
    assert "会话身份与来源" in text
    assert display_database_type("sqlserver") in text
    for phrase in (
        "Incident Summary", "Root Cause Candidate", "Collection Quality",
        "Collection Status", "Data Completeness", "Findings and Recommendations",
        "Expand All", "Print / PDF", "COMPLETE", "PARTIAL", "COLLECTED",
        "FAILED", "UNMATCHED", "NOT_COLLECTED",
    ):
        assert phrase not in text


def test_four_database_slow_html_has_no_english_ui_labels_or_internal_statuses() -> None:
    from test_slow_report_json_html_parity import (
        _ENGINES,
        _attach_top_sql,
        _slow_snapshot,
        _top_sql_result,
    )

    forbidden = (
        "Incident Summary", "Root Cause Candidate", "Collection Quality",
        "Collection Status", "Data Completeness", "Findings and Recommendations",
        "Expand All", "Print / PDF", "No records matched", "No blocker",
        "Review the owning transaction.", "CUMULATIVE", "INTERVAL_DELTA", "HISTORICAL",
        "Complete", "Partial", "Collected", "Failed", "Unmatched", "Not Collected",
        "COMPLETE", "PARTIAL", "COLLECTED", "FAILED", "UNMATCHED", "NOT_COLLECTED",
    )
    for engine in _ENGINES:
        for state in ("complete_matched", "partial_unmatched", "complete_empty", "failed"):
            report = _slow_snapshot(engine)
            result, source = _top_sql_result(engine, state)
            _attach_top_sql(report, result, source)
            model = build_report_model(report, build_incident_summary(report))
            visible = _VisibleText()
            visible.feed(HtmlReportRenderer().render(model))
            text = " ".join(visible.parts)
            for phrase in forbidden:
                if phrase.isupper() and " " not in phrase:
                    found = re.search(rf"(?<![A-Z_]){re.escape(phrase)}(?![A-Z_])", text)
                    assert found is None, f"{engine.value}/{state} left UI status {phrase!r} untranslated"
                else:
                    assert phrase not in text, f"{engine.value}/{state} left UI text {phrase!r} untranslated"


def test_cli_help_and_human_errors_use_chinese_while_json_keeps_domain_enums(
        capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "用法：" in help_text and "选项：" in help_text
    assert "显示程序版本并退出" in help_text
    assert "show program's version number and exit" not in help_text

    error_text = render_human("diagnose", {
        "error_type": "NotImplemented",
        "error_message": "Not implemented for this database type.",
    })
    assert "当前功能尚未实现" in error_text
    assert "Not implemented for this database type." not in error_text

    document = render_json({
        "collection_status": "PARTIAL",
        "error_message": "Database connection failed.",
        "database_error": {"message": "Login failed for synthetic monitor."},
    })
    assert '"collection_status": "PARTIAL"' in document
    assert '"error_message": "数据库连接失败。"' in document
    assert '"message": "Login failed for synthetic monitor."' in document
