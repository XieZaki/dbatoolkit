"""Exercise the actual embedded comparator without running a live snapshot."""

import json
import sys
from collections.abc import Callable
from html import escape
from pathlib import Path

import pytest

from dba.reporting.normalizers import display_label, display_slow_label, display_slow_value

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/acceptance/verify_sqlserver_slow_snapshot.ps1"
STATEMENT = "SELECT \"列\"\r\nWHERE x < 2 AND y = 'a&b'"
TRUNCATED = "<dd>当前语句文本已截断。</dd>"
CARD = (
    f'<article id="current-request-001"><code>{escape(STATEMENT.replace(chr(13), ""))}</code>'
    f'<button data-copy="{escape(STATEMENT.replace(chr(13), ""))}"></button>{TRUNCATED}</article>'
)
BLOCKING = '<section id="blocking-chain"></section>'


@pytest.fixture
def compare_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str, int], None]:
    source = SCRIPT.read_text(encoding="utf-8").split("$pairVerifier = @'\n", 1)[1].split("\n'@", 1)[0]
    verifier = compile(source, str(SCRIPT), "exec")

    def compare(markup: str, request_count: int) -> None:
        requests = [{"statement_sql_text": STATEMENT, "statement_sql_text_truncated": True}] * request_count
        summary = {
            "status": "OK", "severity": "INFO", "primary_problem": "正常",
            "observed_now": "空闲", "data_completeness": "COMPLETE",
            "impact": {"active_requests": len(requests)},
        }
        snapshot = {
            "incident_summary": summary, "snapshot_consistency": "BEST_EFFORT",
            "report": {"details": {"all_requests": requests}},
        }
        status = " · ".join(display_label(value) for value in ("OK", "INFO"))
        description = " · ".join(f"字段：{escape(value)}" for value in (
            display_label("正常"), display_label("空闲"),
            display_slow_value("采集完整性", "COMPLETE"), display_slow_label("BEST_EFFORT"),
        ))
        header = (
            f'<span class="status-pill good"><i></i>{escape(status)}</span>'
            f'<p class="hero-description">{description}</p>\n'
        )
        snapshot_path = tmp_path / "Baseline.json"
        page_path = tmp_path / "Baseline.html"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        page_path.write_text(header + markup, encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["-", str(snapshot_path), str(page_path)])
        exec(verifier, {})

    return compare


@pytest.mark.parametrize("attributes", [
    'class="panel-group" id="current-requests"',
    'class="panel-group" id="current-requests" aria-label="选中 SQL 详情"',
])
@pytest.mark.parametrize("cards", ["", CARD])
def test_request_card_parity(compare_pair: Callable[[str, int], None], attributes: str, cards: str) -> None:
    compare_pair(f"<section {attributes}>{cards}</section>" + BLOCKING, int(bool(cards)))


@pytest.mark.parametrize(("markup", "error"), [
    ('<!-- <section id="current-requests"> -->' + BLOCKING, "id current-requests"),
    ('<section id="current-requests"></section>', "id blocking-chain"),
    (BLOCKING + '<section id="current-requests"></section>', "must precede"),
])
def test_invalid_request_regions(compare_pair: Callable[[str, int], None], markup: str, error: str) -> None:
    with pytest.raises(AssertionError, match=error):
        compare_pair(markup, 0)


@pytest.mark.parametrize(("cards", "request_count", "error"), [
    ("", 1, "card count differs"),
    (CARD, 0, "card count differs"),
    (CARD.replace("current-request-001", "current-request-002"), 1, "order differs"),
    (CARD.replace("<code>", "<code>x"), 1, "HTML SQL differs"),
    (CARD.replace('data-copy="', 'data-copy="x'), 1, "SQL copy differs"),
    (CARD.replace(TRUNCATED, ""), 1, "SQL truncation flag differs"),
])
def test_request_mismatches_fail(
    compare_pair: Callable[[str, int], None], cards: str, request_count: int, error: str,
) -> None:
    with pytest.raises(AssertionError, match=error):
        compare_pair(f'<section id="current-requests">{cards}</section>' + BLOCKING, request_count)
