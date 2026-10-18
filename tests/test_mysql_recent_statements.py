from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.mysql_collectors import collect_recent_statements, redact_statement
from dba.mysql_slow import build_slow_report
from dba.mysql_slow_reporter import render_recent_statements
from dba.registry import SqlScriptRegistry
from dba.settings import MySQLSettings
from dba.slow_profiles import SLOW_OPTIONAL_COLLECTORS, SLOW_SQL_PROFILES

ROOT = Path(__file__).resolve().parents[1]


class RecentFakeAdapter:
    def __init__(self, rows: tuple[dict[str, Any], ...], *, consumer: bool | None = True,
                 success: bool = True, error_type: str | None = None) -> None:
        self.instance = SimpleNamespace(id="mysql-test", db_type=DatabaseType.MYSQL)
        self.registry = SqlScriptRegistry(ROOT / "sql")
        self.slow_profile = None
        self.statement_history_consumer = consumer
        self.rows = rows
        self.success = success
        self.error_type = error_type
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def execute_readonly(self, script: Any, parameters: tuple[int, ...]) -> CollectorResult:
        self.calls.append((script.metadata.name, parameters))
        now = datetime.now(timezone.utc)
        return CollectorResult(
            "mysql-test", "recent_statements", now, now, 2.0, self.rows,
            len(self.rows), False, self.success, self.error_type,
            "permission denied" if not self.success else None,
        )


def report() -> SimpleNamespace:
    return SimpleNamespace(details={
        "sessions": [
            {"processlist_id": 36, "thread_id": 78, "user_name": "app"},
            {"processlist_id": 37, "thread_id": 79, "user_name": "victim"},
            {"processlist_id": 40, "thread_id": 80, "user_name": "idle-app"},
        ],
        "blocking_graph": {"roots": [36]},
        "roots": [{"processlist_id": 36, "blocked_count": 2}],
        "idle_transactions": [],
    })


def row(event_id: int, sql: str = "UPDATE incident_lab SET value = 123 WHERE id = 1") -> dict[str, Any]:
    return {"session_id": 36, "thread_id": 78, "event_id": event_id,
            "duration_ms": 3, "sql_text": sql, "sql_text_truncated": 0}


def test_recent_history_targets_confirmed_roots_and_returns_latest_five_only() -> None:
    target = RecentFakeAdapter(tuple(row(i) for i in (105, 104, 103, 102, 101, 100)))
    result = collect_recent_statements(target, report())  # type: ignore[arg-type]
    assert len(target.calls) == 1
    assert target.calls[0] == ("recent_statements", (78, 36, 78))
    script = target.registry.get("recent_statements", DatabaseType.MYSQL)
    assert "ORDER BY h.event_id DESC" in script.text
    assert "LIMIT 5" in script.text
    assert [item["event_id"] for item in result.rows[0]["statements"]] == [105, 104, 103, 102, 101]
    assert result.truncated and result.rows[0]["truncated"]
    assert result.rows[0]["limit_reached"] and result.rows[0]["more_rows_known"]
    assert all("123" not in item["sql_text_redacted"] for item in result.rows[0]["statements"])
    assert result.rows[0]["evidence_status"] == "AVAILABLE"


def test_recent_sql_does_not_query_duplicate_thread_and_consumer_disabled() -> None:
    state = report()
    state.details["sessions"].append({"processlist_id": 88, "thread_id": 78})
    duplicate = RecentFakeAdapter(())
    result = collect_recent_statements(duplicate, state)  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "IDENTITY_AMBIGUOUS"
    assert duplicate.calls == []

    disabled = RecentFakeAdapter((), consumer=False)
    result = collect_recent_statements(disabled, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "CONSUMER_DISABLED"
    assert disabled.calls == []


def test_recent_sql_targets_idle_transaction_but_not_blocked_victim() -> None:
    state = report()
    state.details["idle_transactions"] = [{"processlist_id": 40}]
    target = RecentFakeAdapter(({"session_id": 36, "thread_id": 78, "event_id": None},))
    result = collect_recent_statements(target, state)  # type: ignore[arg-type]
    assert [parameters[1] for _, parameters in target.calls] == [36, 40]
    assert [row.get("session_id") for row in result.rows[:2]] == [36, 40]
    assert all(row.get("session_id") != 37 for row in result.rows)


def test_recent_sql_target_budget_and_unknown_consumer_are_explicit() -> None:
    state = report()
    state.details["sessions"] = [
        {"processlist_id": pid, "thread_id": pid + 1000} for pid in range(1, 8)
    ]
    state.details["blocking_graph"] = {"roots": [1, 2, 3, 4, 5, 6]}
    state.details["roots"] = [{"processlist_id": pid, "blocked_count": 1}
                               for pid in range(1, 7)]
    budget = RecentFakeAdapter(())
    result = collect_recent_statements(budget, state)  # type: ignore[arg-type]
    assert len(budget.calls) == 5
    assert result.truncated
    assert result.rows[-1]["evidence_status"] == "NOT_COLLECTED_BUDGET"

    unknown = RecentFakeAdapter((), consumer=None)
    result = collect_recent_statements(unknown, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "UNKNOWN"
    assert unknown.calls == []


def test_recent_sql_no_history_thread_gone_and_permission_statuses() -> None:
    no_history = RecentFakeAdapter(({"session_id": 36, "thread_id": 78, "event_id": None},))
    result = collect_recent_statements(no_history, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "NO_HISTORY_ROWS"

    disconnected = RecentFakeAdapter(())
    result = collect_recent_statements(disconnected, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "THREAD_NOT_CURRENT"

    denied = RecentFakeAdapter((), success=False, error_type="QueryDenied")
    result = collect_recent_statements(denied, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "PERMISSION_DENIED"
    assert "permission denied" not in repr(result.rows)

    timeout = RecentFakeAdapter((), success=False, error_type="QueryTimeout")
    result = collect_recent_statements(timeout, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "QUERY_TIMEOUT"

    driver_failure = RecentFakeAdapter((), success=False, error_type="QueryFailed")
    result = collect_recent_statements(driver_failure, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "QUERY_FAILED"

    malformed = RecentFakeAdapter(({"session_id": 36, "thread_id": 78,
                                    "event_id": "bad"},))
    result = collect_recent_statements(malformed, report())  # type: ignore[arg-type]
    assert result.rows[0]["evidence_status"] == "MALFORMED_EVENT"


def test_recent_sql_redaction_hides_literals_comments_controls_and_bounds_output() -> None:
    sql, truncated, status = redact_statement(
        "UPDATE users SET password='secret-value', token=\"token-value\" "
        "WHERE id=42 /* private */ -- note\x1b[31m"
    )
    assert status == "AVAILABLE" and not truncated
    assert "secret-value" not in (sql or "") and "token-value" not in (sql or "")
    assert "private" not in (sql or "") and "note" not in (sql or "")
    assert "secret-value" not in repr(sql)
    hex_sql, _, _ = redact_statement("SELECT 0xDEADBEEF")
    assert "DEADBEEF" not in (hex_sql or "")
    oversized, was_truncated, _ = redact_statement("SELECT " + "x" * 1100)
    assert was_truncated and len(oversized or "") <= 1000
    malformed, _, malformed_status = redact_statement("SELECT 'unterminated")
    assert malformed is None and malformed_status == "REDACTION_UNCERTAIN"


def test_exact_recent_sql_limit_is_not_reported_as_truncated() -> None:
    for count in (0, 4, 5):
        target = RecentFakeAdapter(tuple(row(i) for i in range(1, count + 1)))
        result = collect_recent_statements(target, report())  # type: ignore[arg-type]
        target_row = result.rows[0]
        assert result.truncated is False
        if count == 0:
            assert target_row["evidence_status"] == "THREAD_NOT_CURRENT"
            assert not target_row.get("limit_reached", False)
        else:
            assert target_row["evidence_status"] == "AVAILABLE"
            assert len(target_row["statements"]) == count
            assert target_row["limit_reached"] is (count == 5)
            assert target_row["more_rows_known"] is False
            assert target_row["truncated"] is False


def test_terminal_recent_sql_is_bounded_normalized_and_uses_redacted_text_only() -> None:
    for length in (50, 200, 201, 5000):
        source = "SELECT " + "x" * (length - len("SELECT "))
        redacted, was_truncated, status = redact_statement(source)
        assert status == "AVAILABLE"
        lines = render_recent_statements([{
            "session_id": 36, "evidence_status": "AVAILABLE", "statements": [{
                "sql_text_redacted": redacted, "truncated": was_truncated,
            }],
        }], 0.02)
        sql_line = next(line[3:] for line in lines.splitlines() if line.startswith("1. "))
        assert len(sql_line) <= 200
        assert ("前200字符" in lines) is (length > 200)
        assert "0.0 毫秒" not in lines
        assert "<0.1 毫秒" in lines

    safe_sql, _, _ = redact_statement("SELECT 'never-show-this'\r\nFROM test\x1b[31m")
    rendered = render_recent_statements([{
        "session_id": 36, "evidence_status": "AVAILABLE", "statements": [{
            "sql_text_redacted": safe_sql, "truncated": False,
        }],
    }])
    assert "never-show-this" not in rendered
    assert "\x1b" not in rendered and "\r" not in rendered
    assert "SELECT ? FROM test" in rendered
    assert "采集耗时：未记录" in rendered


def test_terminal_recent_sql_limits_statement_count_and_handles_control_characters() -> None:
    statements = [{"sql_text_redacted": f"SELECT {index}\n\x1b[2JFROM t", "truncated": False}
                  for index in range(7)]
    rendered = render_recent_statements([{
        "session_id": 36, "evidence_status": "AVAILABLE", "statements": statements,
    }])
    assert sum(line.startswith(tuple(f"{i}. " for i in range(1, 6)))
               for line in rendered.splitlines()) == 5
    assert "\x1b" not in rendered and "\nFROM" not in rendered
    assert "1. SELECT 0 FROM t" in rendered


def test_recent_collection_elapsed_time_covers_query_and_uses_high_resolution_clock() -> None:
    target = RecentFakeAdapter((row(105),))
    original = target.execute_readonly

    def delayed(script: Any, parameters: tuple[int, ...]) -> CollectorResult:
        time.sleep(0.005)
        return original(script, parameters)

    target.execute_readonly = delayed  # type: ignore[method-assign]
    result = collect_recent_statements(target, report())  # type: ignore[arg-type]
    assert result.elapsed_ms >= 4
    assert "采集耗时：未记录" in render_recent_statements([], None)
    assert "采集耗时：<0.1 毫秒" in render_recent_statements([], 0.01)


def test_recent_sql_is_optional_for_mysql_57_80_84_profiles() -> None:
    for profile_id in ("mysql57_slow", "mysql80_slow", "mysql84_slow"):
        assert SLOW_SQL_PROFILES[profile_id]["recent_statements"] == "recent_statements"
        assert "recent_statements" in SLOW_OPTIONAL_COLLECTORS[profile_id]


def test_recent_sql_failure_keeps_39_to_42_to_41_root_and_manual_action() -> None:
    instance = DatabaseInstance("mysql-test", "MySQL", DatabaseType.MYSQL,
                                "localhost", 3306, "monitor", environment="test")
    now = datetime.now(timezone.utc)
    samples = (
        CollectorResult("mysql-test", "active_sessions", now, now, 1, (
            {"processlist_id": 39, "thread_id": 81, "user_name": "app",
             "host_raw": "app-host", "command_name": "Sleep"},
            {"processlist_id": 42, "thread_id": 84, "user_name": "app",
             "host_raw": "app-host", "command_name": "Query"},
            {"processlist_id": 41, "thread_id": 83, "user_name": "app",
             "host_raw": "app-host", "command_name": "Query"},
        ), 3, False, True),
        CollectorResult("mysql-test", "active_requests", now, now, 1, (
            {"processlist_id": 42, "thread_id": 84, "state_name": "updating"},
            {"processlist_id": 41, "thread_id": 83, "state_name": "updating"},
        ), 2, False, True),
        CollectorResult("mysql-test", "transactions", now, now, 1, (
            {"trx_mysql_thread_id": 39, "thread_id": 81, "trx_id": "4302",
             "trx_started": "2026-10-18", "transaction_age_seconds": 30},
        ), 1, False, True),
        CollectorResult("mysql-test", "blocking", now, now, 1, (
            {"blocked_pid": 42, "blocker_pid": 39},
            {"blocked_pid": 41, "blocker_pid": 42},
        ), 1, False, True),
        CollectorResult("mysql-test", "recent_statements", now, now, 1, (), 0,
                        False, False, "QueryDenied"),
    )
    report = build_slow_report(instance, samples, MySQLSettings(), "8.4.11",
                               statement_script="active_requests_no_waits",
                               required_collectors=("active_sessions", "transactions", "blocking"),
                               optional_collectors=("recent_statements",))
    root = next(finding for finding in report.findings if finding.title == "ROOT_BLOCKER")
    assert report.details["data_visibility"] == "PARTIAL"
    assert report.details["blocking_graph"]["roots"] == [39]
    assert report.details["blocking_graph"]["children"] == {"39": [42], "42": [41]}
    assert report.details["transactions"][0]["trx_id"] == "4302"
    assert [command.text for command in root.reference_commands] == ["KILL CONNECTION 39;"]
    assert not hasattr(root.reference_commands[0], "execute")
