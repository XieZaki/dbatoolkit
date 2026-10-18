import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider

from dba.analyzers import SlowAnalyzer
from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, ReferenceCommand
from dba.errors import ToolkitError
from dba.registry import SqlScriptRegistry
from dba.settings import SQLServerSettings
from dba.slow import blocking_graph, build_slow_report, descendants, diagnose_slow
from dba.slow_reporter import render_console, render_markdown
from dba.sqlserver import SQLServerAdapter

INSTANCE = DatabaseInstance("test", "Test", DatabaseType.SQLSERVER, "test.invalid", 1433, "monitor")


def sample(name: str, rows: list[dict[str, Any]], **kwargs: Any) -> CollectorResult:
    now = datetime.now(timezone.utc)
    return CollectorResult(
        "test",
        name,
        now,
        now,
        1,
        tuple(rows),
        len(rows),
        kwargs.get("truncated", False),
        kwargs.get("success", True),
        kwargs.get("error_type"),
    )


def report(
    requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]] | None = None,
    transactions: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
    **settings: Any,
) -> Any:
    return build_slow_report(
        INSTANCE,
        (
            sample("active_requests", requests),
            sample("active_sessions", sessions or []),
            sample("transactions", transactions or []),
            sample("connections", connections or []),
        ),
        SQLServerSettings(**settings),
    )


def edge(child: int, parent: int, **kwargs: Any) -> dict[str, Any]:
    return {"session_id": child, "blocking_session_id": parent, **kwargs}


@pytest.mark.parametrize(
    "edges,roots,count",
    [
        ([(2, 1)], [1], 1),
        ([(2, 1), (3, 2), (4, 2)], [1], 3),
        ([(2, 1), (4, 3)], [1, 3], 1),
        ([], [], 0),
    ],
)
def test_blocking_graph(edges: list[tuple[int, int]], roots: list[int], count: int) -> None:
    graph = blocking_graph([edge(c, p) for c, p in edges])
    assert graph["roots"] == roots
    if roots:
        assert len(descendants(roots[0], graph["children"])) == count


def test_cycles_deep_chain_multiple_parents() -> None:
    graph = blocking_graph([edge(2, 1), edge(1, 2), edge(3, 2)])
    assert graph["roots"] == [] and graph["unresolved"] == [1, 2, 3]
    graph = blocking_graph([edge(n + 1, n) for n in range(1, 2500)])
    assert len(descendants(1, graph["children"])) == 2499
    graph = blocking_graph([edge(3, 1), edge(3, 2)])
    assert graph["roots"] == [1, 2] and graph["anomalies"]


@pytest.mark.parametrize("parent", [-2, -3, -4, -5, -99, 50000, 52])
def test_special_or_invalid_blocker(parent: int) -> None:
    graph = blocking_graph([edge(52, parent)])
    assert not graph["roots"] and graph["anomalies"]


def test_concurrency_and_ranking() -> None:
    value = report(
        [
            edge(52, 51, status="suspended", elapsed_ms=20000, wait_type="LCK_M_X"),
            edge(53, 0, status="running", elapsed_ms=60000),
            edge(53, 0, status="runnable", elapsed_ms=90000),
        ],
        [{"session_id": n} for n in range(51, 60)],
        slow_top_n=2,
        long_running_seconds=60,
        high_concurrency_threshold=3,
    )
    assert value.details["concurrency"] == {
        "connected_sessions": 9,
        "active_sessions": 2,
        "active_requests": 3,
        "running_requests": 1,
        "waiting_requests": 1,
        "blocked_requests": 1,
    }
    assert [r["elapsed_ms"] for r in value.details["top_requests"]] == [90000, 60000]
    assert len(value.details["long_running"]) == 2
    assert {f.title for f in value.findings} >= {
        "HIGH_CONCURRENCY",
        "LONG_RUNNING_SQL",
        "WAITING_SQL",
        "BLOCKING",
    }


def test_idle_transaction_evidence_source_age_and_last_sql() -> None:
    value = report(
        [edge(52, 51, wait_type="LCK_M_X")],
        [
            {
                "session_id": 51,
                "status": "sleeping",
                "open_transaction_count": 1,
                "login_time": "2026-10-13T09:00:00",
                "host_name": "app",
                "login_name": "business",
                "program_name": "client",
            }
        ],
        [
            {
                "session_id": 51,
                "transaction_id": 7,
                "login_time": "2026-10-13T09:00:00",
                "transaction_state": 2,
                "transaction_type": 1,
                "transaction_age_seconds": 5862,
                "transaction_begin_time": "2026-10-13T10:00:00",
            }
        ],
        [{"session_id": 51, "client_net_address": "10.0.0.1", "last_sql": "UPDATE fixture"}],
    )
    root = value.details["roots"][0]
    assert root["transaction_age_seconds"] == 5862 and root["client_net_address"] == "10.0.0.1"
    assert root["transaction_id"] == 7 and root["transaction_identity_quality"] == "CONFIRMED"
    assert root["last_sql"] == ["UPDATE fixture"] and root["host_name"] == "app"
    finding = next(f for f in value.findings if f.title == "IDLE_TRANSACTION_BLOCKER")
    assert finding.severity.value == "CRITICAL" and finding.evidence["transactions"]
    commands = [c for f in value.findings for c in f.reference_commands]
    assert commands[0].text == "KILL 51;" and commands[0].manual_only
    assert not hasattr(commands[0], "execute") and not callable(commands[0])


@pytest.mark.parametrize("state", [None, 0, 1, 3, 6, 7, 8])
def test_idle_requires_unfinished_dmv_transaction(state: int | None) -> None:
    value = report(
        [edge(52, 51)],
        [{"session_id": 51, "status": "sleeping", "open_transaction_count": 1}],
        [{"session_id": 51, "transaction_state": state, "transaction_type": 1}],
    )
    assert "IDLE_TRANSACTION_BLOCKER" not in [f.title for f in value.findings]


def test_root_transaction_id_requires_unique_identity_and_same_login_time() -> None:
    session = {"session_id": 51, "status": "sleeping", "login_time": "2026-10-13T09:00:00",
               "open_transaction_count": 1, "login_name": "monitor", "host_name": "app",
               "program_name": "client"}
    tx = {"session_id": 51, "login_time": "2026-10-13T09:00:00", "transaction_id": 376008,
          "transaction_state": 2, "transaction_type": 1, "transaction_age_seconds": 174}
    root_report = report([edge(56, 51)], [session], [tx])
    root = root_report.details["roots"][0]
    assert root["transaction_id"] == 376008
    assert root["status"] == "sleeping" and root["transaction_identity_quality"] == "CONFIRMED"

    ambiguous = report([edge(56, 51)], [session], [tx, {**tx, "transaction_id": 376009}])
    assert ambiguous.details["roots"][0]["transaction_id"] is None
    assert ambiguous.details["roots"][0]["transaction_identity_quality"] == "AMBIGUOUS"
    assert not any(f.reference_commands for f in ambiguous.findings)

    reused = report([edge(56, 51)], [session], [{**tx, "login_time": "2026-10-13T10:00:00"}])
    assert reused.details["roots"][0]["transaction_id"] is None
    assert reused.details["roots"][0]["transaction_identity_quality"] == "RECHECK_REQUIRED"
    assert not any(f.reference_commands for f in reused.findings)


def test_root_reference_does_not_depend_on_optional_connection_address_collector() -> None:
    login_time = "2026-10-18T10:00:00"
    value = build_slow_report(INSTANCE, (
        sample("active_requests", [edge(56, 58, status="suspended")]),
        sample("active_sessions", [
            {"session_id": 58, "status": "sleeping", "login_time": login_time,
             "login_name": "app", "host_name": "worker", "program_name": "orders",
             "open_transaction_count": 1},
            {"session_id": 56, "status": "suspended", "login_time": login_time,
             "login_name": "app", "host_name": "worker", "program_name": "orders"},
        ]),
        sample("transactions", [{"session_id": 58, "login_time": login_time,
                                  "transaction_id": 376008, "transaction_state": 2,
                                  "transaction_type": 1, "transaction_age_seconds": 174}]),
    ), SQLServerSettings())
    root = next(finding for finding in value.findings if finding.title == "ROOT_BLOCKER")
    assert [command.text for command in root.reference_commands] == ["KILL 58;"]
    assert root.evidence["manual_action"]["status"] == "AVAILABLE"


def test_sqlserver_blocking_chain_keeps_root_transaction_separate_from_victims() -> None:
    sessions = [{"session_id": sid, "status": "sleeping" if sid == 58 else "suspended",
                 "login_time": f"2026-10-18T13:{sid}:00", "login_name": f"app{sid}",
                 "host_name": "simulator", "program_name": "incident-test",
                 "open_transaction_count": 2 if sid != 58 else 1}
                for sid in (58, 56, 59)]
    transactions = [{"session_id": sid, "login_time": f"2026-10-18T13:{sid}:00",
                     "transaction_id": txid, "transaction_state": 2, "transaction_type": 1,
                     "transaction_age_seconds": 174}
                    for sid, txid in ((58, 376008), (56, 376018), (59, 376027))]
    value = report([edge(56, 58, wait_type="LCK_M_X", wait_time_ms=173000),
                    edge(59, 56, wait_type="LCK_M_X", wait_time_ms=173000)],
                   sessions, transactions)
    root = value.details["roots"][0]
    assert root["session_id"] == 58
    assert root["transaction_id"] == 376008
    assert root["transaction_identity_quality"] == "CONFIRMED"
    children = value.details["blocking_graph"]["children"]
    assert root["blocked_session_count"] == 2 and len(children.get("58", children.get(58, []))) == 1
    assert {row["transaction_id"] for row in value.details["transactions"]} == {
        376008, 376018, 376027}


def test_missing_root_ip_sql_and_legacy_fields() -> None:
    value = report([edge(52, 51)])
    assert value.details["roots"][0]["status"] is None
    assert not any(f.reference_commands for f in value.findings)
    value = report([edge(52, 51)], [{"session_id": 51, "status": "sleeping"}])
    assert value.details["roots"][0]["last_sql"] == []
    assert value.details["roots"][0]["client_net_address"] is None
    assert "unavailable" in render_console(value)
    assert not any(f.title == "IDLE_TRANSACTION_BLOCKER" for f in value.findings)


def test_partial_snapshot_and_sensitive_report() -> None:
    results = (
        sample("active_requests", [edge(52, 51)], truncated=True),
        sample("active_sessions", [{"session_id": 51, "host_name": "password=private"}]),
        sample("transactions", [], success=False, error_type="PermissionDenied"),
    )
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    assert value.details["incomplete"]
    assert not any(f.reference_commands for f in value.findings)
    markdown = render_markdown(value)
    assert "private" not in markdown and "truncated" in markdown
    for title in (
        "Summary",
        "Concurrency",
        "Long Running SQL",
        "Blocking Tree",
        "Root Blocker Details",
        "Transaction Details",
        "Session Source",
        "Findings",
        "Recommendations",
        "Reference Commands",
    ):
        assert f"## {title}" in markdown
    assert "Toolkit 未执行任何 KILL 操作" in markdown


def test_analyzer_dispatch_and_unsupported() -> None:
    assert SlowAnalyzer().analyze(INSTANCE, ())
    with pytest.raises(ToolkitError, match="Not implemented"):
        SlowAnalyzer().analyze(replace(INSTANCE, db_type=DatabaseType.MYSQL), ())
    assert not hasattr(ReferenceCommand("KILL 1;", "manual"), "execute")


def test_optional_failure_keeps_snapshot_and_closes() -> None:
    driver = FakeDriver()
    driver.connection.failures["transactions"] = RuntimeError("42000", "password=private")
    with SQLServerAdapter(
        INSTANCE, MemoryCredentialProvider(), SqlScriptRegistry(ROOT / "sql"), driver=driver
    ) as adapter:
        value = diagnose_slow(adapter)
    assert value.details["incomplete"] and driver.connection.closed
    assert "connections" not in driver.connection.executed
    assert "private" not in render_console(value)


def test_active_root_does_not_claim_idle_transaction() -> None:
    value = report(
        [edge(52, 51), edge(51, 0, sql_text="SELECT 1")],
        [{"session_id": 51, "status": "running", "open_transaction_count": 1}],
        [{"session_id": 51, "transaction_state": 2, "transaction_type": 1}],
    )
    assert any(f.title == "ROOT_BLOCKER" for f in value.findings)
    assert not any(f.title == "IDLE_TRANSACTION_BLOCKER" for f in value.findings)


def test_truncated_requests_do_not_claim_idle_or_offer_kill() -> None:
    results = (
        sample("active_requests", [edge(52, 51)], truncated=True),
        sample(
            "active_sessions",
            [{"session_id": 51, "status": "sleeping", "open_transaction_count": 1}],
        ),
        sample("transactions", [{"session_id": 51, "transaction_state": 2, "transaction_type": 1}]),
        sample("connections", []),
    )
    value = build_slow_report(INSTANCE, results, SQLServerSettings())
    assert not any(
        f.title == "IDLE_TRANSACTION_BLOCKER" or f.reference_commands for f in value.findings
    )


def test_cli_diagnose(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    driver.connection.results.update(transactions=[], connections=[])
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {
                        "id": "test",
                        "name": "Test",
                        "db_type": "sqlserver",
                        "host": "test.invalid",
                        "port": 1433,
                        "credential_alias": "monitor",
                        "version_family": "2022",
                    }
                ]
            }
        )
    )
    output = tmp_path / "slow.html"
    args = [
        "--inventory",
        str(inventory),
        "--sql-root",
        str(ROOT / "sql"),
        "diagnose",
        "test",
        "slow",
        "--report",
        str(output),
        "--format",
        "json",
    ]
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] and output.exists() and driver.connection.closed
    assert payload["incident_summary"]["data_completeness"] == "COMPLETE"
    assert payload["incident_summary"]["report_path"] == str(output)
    html = output.read_text(encoding="utf-8")
    assert "故障概况" in html and "锁与阻塞链" in html
    assert set(driver.connection.executed) <= {
        "monitor_capabilities",
        "monitor_permissions",
        "version_detect",
        "server_info",
        "database_status",
        "active_sessions",
        "active_requests",
        "transactions",
        "connections",
    }
    assert main(args, provider=MemoryCredentialProvider(), driver=driver) == 1
    assert output.exists()
    assert "File could not be read" in capsys.readouterr().out
    console_path = tmp_path / "slow-console.html"
    human_args = args[:-2]
    human_args[human_args.index(str(output))] = str(console_path)
    console_driver = FakeDriver()
    console_driver.connection.results.update(transactions=[], connections=[])
    assert main(human_args, provider=MemoryCredentialProvider(), driver=console_driver) == 0
    human_output = capsys.readouterr().out
    assert "数据库性能应急诊断" in human_output and "详细中文报告已写入" in human_output
    assert "Current SQL" not in human_output and "Blocking Tree" not in human_output
    for option in ("--execute", "--force"):
        with pytest.raises(SystemExit) as exc:
            main([*args, option])
        assert exc.value.code == 2


def test_cli_unsupported_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {
                        "id": "other",
                        "name": "Other",
                        "db_type": "redis",
                        "host": "test.invalid",
                        "port": 3306,
                        "credential_alias": "monitor",
                    }
                ]
            }
        )
    )
    result = main(["--inventory", str(inventory), "diagnose", "other", "slow", "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["error_type"] == "NotImplemented" and "Not implemented" in payload["error_message"]
    )


@pytest.mark.parametrize("key", ["slow_top_n", "high_concurrency_threshold"])
@pytest.mark.parametrize("value", [0, -1, True, "10"])
def test_threshold_validation(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        SQLServerSettings(**{key: value})
