from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from dba.cli import _emit
from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.credentials import Credential
from dba.incident import build_incident_summary
from dba.logging import redact
from dba.presentation import render_human
from dba.reporting import HtmlReportRenderer, build_report_model
from dba.top_sql_collectors import SQLServerTopSQLCollector
from dba.versioning import PROFILE_RESOLVER, DetectedDatabaseVersion
from scripts.acceptance import verify_sqlserver_top_sql as acceptance
from tests.test_html_reports import _report

KNOWN_VALUES: dict[str, int | Decimal] = {
    "cpu_time": 1494425,
    "logical_reads": 120,
    "physical_reads": 8,
    "execution_count": 41,
    "elapsed_time": 1528406,
    "average_cpu_time": Decimal("36449.3902"),
    "average_elapsed_time": Decimal("36390.6190"),
}
IDENTITY_FIELDS = {
    "sql_handle": "0x01",
    "query_hash": "0x52B3B6CEDE6FC57A",
    "statement_start_offset": 18,
    "statement_end_offset": 2256,
    "database_id": 5,
}
PLAN_FIELDS = {
    "plan_handle": "0x02",
    "query_plan_hash": "0xFEDCBA9876543210",
    "plan_generation_num": 1,
}


def _item(rank: int, value: int | Decimal) -> dict[str, Any]:
    json_value: int | float = float(value) if isinstance(value, Decimal) else value
    return {
        "rank": rank,
        "metric_value": json_value,
        "sql_identity": {
            "database_engine": "sqlserver",
            "kind": "STATEMENT",
            "fields": {
                **IDENTITY_FIELDS,
                "sql_handle": IDENTITY_FIELDS["sql_handle"] if rank == 1 else f"0x{rank:02X}",
            },
        },
        "plan_identity": {
            "database_engine": "sqlserver",
            "kind": "PLAN",
            "fields": {
                **PLAN_FIELDS,
                "plan_handle": PLAN_FIELDS["plan_handle"] if rank == 1 else f"0x{rank + 16:02X}",
            },
        },
    }


def _top(metric: str, values: tuple[int | Decimal, ...] | None = None) -> dict[str, Any]:
    metric_name, unit = acceptance.EXPECTED_UNITS[metric]
    selected_values = values or (KNOWN_VALUES[metric],)
    return {
        "database_engine": "sqlserver",
        "database_version": "16.0.4305.5",
        "profile_id": "sqlserver_top_sql_shared",
        "collection_status": "COLLECTED",
        "data_visibility": "COMPLETE",
        "completeness": "COMPLETE",
        "collection_scope": "INSTANCE",
        "metric_scope": "SQL_STATEMENT",
        "metric_semantics": "CUMULATIVE",
        "ranking_direction": "DESCENDING",
        "metric_name": metric_name,
        "metric_unit": unit,
        "limitations": ["CACHE_EVICTED", "RECOMPILE_RESETS_COUNTERS"],
        "items": [_item(rank, value) for rank, value in enumerate(selected_values, 1)],
    }


def _envelope(top: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "top_sql": top,
        "version_profile": {
            "version_status": "VERIFIED",
        },
        "report": {"details": {"top_sql": top}},
    }


def _html_for(top: dict[str, Any]) -> str:
    rows = acceptance._presenter_html_rows(acceptance._json_rows(top))
    table = [
        "<table><tr><th>Rank</th><th>SQL Identity</th><th>Metric Value</th></tr>"
    ]
    table.extend("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    table.append("</table>")
    meta = " ".join((
        top["collection_scope"], top["metric_scope"], top["metric_semantics"],
        top["metric_name"], top["metric_unit"],
    ))
    return f"<p>{meta}</p>" + "".join(table)


def _profile() -> Any:
    return PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER,
        "2022",
        DetectedDatabaseVersion("16.0.4305.5", "2022"),
        "top-sql",
    )


def _matches_fixed_expected_record(top: dict[str, Any], item: dict[str, Any], metric: str) -> bool:
    expected_name, expected_unit = acceptance.EXPECTED_UNITS[metric]
    expected_value = Decimal(str(KNOWN_VALUES[metric]))
    return (
        top.get("metric_name") == expected_name
        and top.get("metric_unit") == expected_unit
        and item.get("rank") == 1
        and Decimal(str(item.get("metric_value"))) == expected_value
        and item.get("fields") == IDENTITY_FIELDS
        and item.get("plan_fields") == PLAN_FIELDS
    )


def _acceptance_report() -> dict[str, Any]:
    metric_results: dict[str, Any] = {}
    for metric in acceptance.METRICS:
        top = _top(metric, (KNOWN_VALUES[metric], Decimal("1.0000")))
        rows = acceptance._json_rows(top)
        metric_name, unit = acceptance.EXPECTED_UNITS[metric]
        metric_results[metric] = {
            "database_engine": "sqlserver",
            "database_version": "16.0.4305.5",
            "version_profile": {
                "version_status": "VERIFIED",
            },
            "profile_id": "sqlserver_top_sql_shared",
            "collection": "COLLECTED",
            "visibility": "COMPLETE",
            "completeness": "COMPLETE",
            "collection_scope": "INSTANCE",
            "metric_scope": "SQL_STATEMENT",
            "semantics": "CUMULATIVE",
            "metric_name": metric_name,
            "unit": unit,
            "ranking_direction": "DESCENDING",
            "row_count": len(rows),
            "top_n_respected": True,
            "rank_order_valid": True,
            "native_identity_complete": all(
                acceptance._item_native_identity_complete(row) for row in rows
            ),
            "html_parity": "MATCH",
            "human_cli_parity": "MATCH",
            "human_cli_metadata_match": True,
            "output_parity": True,
            "rows": rows,
            "limitations": [],
        }
    return {
        "selected_metrics": list(acceptance.METRICS),
        "database_version": "16.0.4305.5",
        "database": "master",
        "sysadmin": False,
        "toolkit_database_write": False,
        "toolkit_captured_business_sql_executed": False,
        "test_driver_database_write": False,
        "temporary_objects_created": False,
        "metric_results": metric_results,
    }


def test_credential_lookup_uses_only_selected_os_store_alias() -> None:
    store = Mock()
    credential = Credential("monitor", "never-print-this")
    store.get.return_value = credential

    assert acceptance._credential_for_alias(store, "sqlserver-test-monitor") is credential
    store.get.assert_called_once_with("sqlserver-test-monitor")


def test_credential_lookup_missing_alias_fails_without_fallback() -> None:
    store = Mock()
    store.get.return_value = None

    with pytest.raises(RuntimeError, match="Windows Credential Manager alias unavailable"):
        acceptance._credential_for_alias(store, "sqlserver-test-monitor")
    store.get.assert_called_once_with("sqlserver-test-monitor")


def test_top_sql_result_parser_accepts_current_cli_shape() -> None:
    payload = {
        "success": True,
        "top_sql": {"items": [_item(1, 12)]},
        "version_profile": {"version_status": "VERIFIED"},
    }

    output, result = acceptance._parse_toolkit_output(payload)

    assert output is payload
    assert result is payload["top_sql"]
    parsed = acceptance._json_rows(result)[0]
    assert parsed["fields"]["sql_handle"] == "0x01"
    assert parsed["plan_fields"]["plan_handle"] == "0x02"


@pytest.mark.parametrize("payload", [None, [], {}, {"success": False}])
def test_top_sql_result_parser_rejects_invalid_json_envelope(payload: object) -> None:
    with pytest.raises((TypeError, acceptance.AcceptanceCheckFailure)):
        acceptance._parse_toolkit_output(payload)


@pytest.mark.parametrize("metric", acceptance.METRICS)
def test_one_collector_result_drives_json_html_and_human_cli(
    metric: str, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    value = KNOWN_VALUES[metric]
    raw = {
        "sql_handle": IDENTITY_FIELDS["sql_handle"],
        "plan_handle": PLAN_FIELDS["plan_handle"],
        "query_hash": IDENTITY_FIELDS["query_hash"],
        "query_plan_hash": PLAN_FIELDS["query_plan_hash"],
        "plan_generation_num": PLAN_FIELDS["plan_generation_num"],
        "statement_start_offset": IDENTITY_FIELDS["statement_start_offset"],
        "statement_end_offset": IDENTITY_FIELDS["statement_end_offset"],
        "database_id": IDENTITY_FIELDS["database_id"],
        "sql_text_available": 1,
        "total_cpu_time": 1494425,
        "total_logical_reads": 120,
        "total_physical_reads": 8,
        "execution_count": 41,
        "total_elapsed_time": 1528406,
        "average_cpu_time": "36449.390243",
        "average_elapsed_time": "36390.619047",
        "ranking_value": str(value),
    }
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    collector_rows = (
        {
            "sql_handle": raw["sql_handle"],
            "plan_handle": raw["plan_handle"],
            "query_hash": raw["query_hash"],
            "query_plan_hash": raw["query_plan_hash"],
            "plan_generation_num": raw["plan_generation_num"],
            "statement_start_offset": raw["statement_start_offset"],
            "statement_end_offset": raw["statement_end_offset"],
            "database_id": raw["database_id"],
            "sql_text_available": 1,
            "total_cpu_time": raw["total_cpu_time"],
            "total_logical_reads": raw["total_logical_reads"],
            "total_physical_reads": raw["total_physical_reads"],
            "execution_count": raw["execution_count"],
            "total_elapsed_time": raw["total_elapsed_time"],
            "average_cpu_time": raw["average_cpu_time"],
            "average_elapsed_time": raw["average_elapsed_time"],
            "ranking_value": raw["ranking_value"],
        },
    )
    collector_result = CollectorResult(
        database_id="sqlserver2022",
        collector_name="sqlserver_top_sql",
        started_at=now,
        finished_at=now,
        elapsed_ms=0,
        rows=collector_rows,
        row_count=1,
        truncated=False,
        success=True,
    )
    script = SimpleNamespace(metadata=SimpleNamespace(
        feature="top-sql",
        version_families=("2022",),
        runtime_variants=("default",),
    ))
    adapter = SimpleNamespace(
        instance=DatabaseInstance(
            "sqlserver2022", "SQL Server Lab", DatabaseType.SQLSERVER,
            "127.0.0.1", 1433, "monitor-alias", database="master", version_family="2022",
        ),
        registry=SimpleNamespace(get=Mock(return_value=script)),
        execute_readonly=Mock(return_value=collector_result),
    )

    result = SQLServerTopSQLCollector(cast(Any, adapter), _profile()).collect(
        adapter.instance, metric=metric, top_n=3,
    )
    assert adapter.execute_readonly.call_count == 1
    adapter.execute_readonly.assert_called_once_with(script, top_sql_parameters=(3, metric))
    payload = result.to_dict()

    _emit(
        SimpleNamespace(json=True, format="console", quiet=False),
        "diagnose",
        redact({"success": True, "top_sql": payload}),
    )
    json_payload = json.loads(capsys.readouterr().out)
    json_top = json_payload["top_sql"]
    human_text = render_human("top-sql", payload)

    base_report = _report()
    report = replace(
        base_report,
        diagnosed_at=result.collection_timestamp,
        scenario="top-sql",
        findings=(),
        details={**base_report.details, "version": result.database_version, "top_sql": payload},
    )
    model = build_report_model(
        report, build_incident_summary(report, "reports/sqlserver2022_top-sql.html"),
    )
    html_page = HtmlReportRenderer().render(model)
    html_path = tmp_path / f"{metric}.html"
    html_path.write_text(html_page, encoding="utf-8")
    html_rows, parser_status = acceptance._html_table(html_path)
    json_rows = acceptance._json_rows(json_top)
    html_parity = acceptance._html_parity_result(
        html_rows, json_rows, parser_status=parser_status,
    )
    human_parity = acceptance._human_cli_diff_summary(
        acceptance._human_cli_rows(human_text), json_rows,
    )

    assert json_top["ranking_metric"] == metric
    assert json_top["metric_unit"] == acceptance.EXPECTED_UNITS[metric][1]
    assert Decimal(str(json_rows[0]["metric_value"])) == Decimal(str(value))
    assert _matches_fixed_expected_record(json_top, json_rows[0], metric)
    assert json_rows[0]["fields"]["sql_handle"] == IDENTITY_FIELDS["sql_handle"]
    assert json_rows[0]["fields"]["query_hash"] == IDENTITY_FIELDS["query_hash"]
    assert json_rows[0]["fields"]["statement_start_offset"] == IDENTITY_FIELDS["statement_start_offset"]
    assert json_rows[0]["fields"]["statement_end_offset"] == IDENTITY_FIELDS["statement_end_offset"]
    assert json_rows[0]["fields"]["database_id"] == IDENTITY_FIELDS["database_id"]
    assert json_rows[0]["plan_fields"]["plan_handle"] == PLAN_FIELDS["plan_handle"]
    assert json_rows[0]["plan_fields"]["query_plan_hash"] == PLAN_FIELDS["query_plan_hash"]
    assert json_rows[0]["plan_fields"]["plan_generation_num"] == PLAN_FIELDS["plan_generation_num"]
    assert html_parity["status"] == "MATCH"
    assert human_parity["classification"] == "MATCH"
    assert "累计统计" in human_text and "实例级" in human_text
    assert "累计统计" in html_page and "实例级" in html_page


@pytest.mark.parametrize("corruption", [
    "sql_handle", "query_hash", "offset", "plan_handle", "query_plan_hash",
    "plan_generation", "metric_value", "unit", "metric_name", "rank",
])
def test_fixed_independent_expectation_rejects_wrong_identity_plan_metric_or_rank(
    corruption: str,
) -> None:
    top = _top("cpu_time")
    item = acceptance._json_rows(top)[0]
    if corruption == "sql_handle":
        item["fields"]["sql_handle"] = "0xFF"
    elif corruption == "query_hash":
        item["fields"]["query_hash"] = "0xBAD"
    elif corruption == "offset":
        item["fields"]["statement_start_offset"] += 2
    elif corruption == "plan_handle":
        item["plan_fields"]["plan_handle"] = "0xBAD"
    elif corruption == "query_plan_hash":
        item["plan_fields"]["query_plan_hash"] = "0xBAD"
    elif corruption == "plan_generation":
        item["plan_fields"]["plan_generation_num"] += 1
    elif corruption == "metric_value":
        item["metric_value"] += 1
    elif corruption == "unit":
        top["metric_unit"] = "milliseconds"
    elif corruption == "metric_name":
        top["metric_name"] = "total_elapsed_time"
    else:
        item["rank"] = 2

    assert not _matches_fixed_expected_record(top, item, "cpu_time")


def test_html_and_cli_parity_reject_wrong_identity_rank_value_and_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top = _top("cpu_time", (100, 80))
    rows = acceptance._json_rows(top)
    html = acceptance._presenter_html_rows(rows)
    assert acceptance._html_parity_result(html, rows)["status"] == "MATCH"
    assert acceptance._human_cli_diff_summary(
        acceptance._human_cli_rows(render_human("top-sql", top)), rows,
    )["classification"] == "MATCH"

    wrong_identity = [list(row) for row in html]
    wrong_identity[0][1] = wrong_identity[0][1].replace("0x01", "0xFF")
    assert acceptance._html_parity_result(wrong_identity, rows)["failure_reason"] == "IDENTITY_MISMATCH"

    wrong_value = [list(row) for row in html]
    wrong_value[0][2] = "101"
    assert acceptance._html_parity_result(wrong_value, rows)["failure_reason"] == "VALUE_MISMATCH"

    wrong_rank = [dict(row) for row in rows]
    wrong_rank[0]["rank"] = 2
    assert acceptance._human_cli_diff_summary(
        acceptance._human_cli_rows(render_human("top-sql", top)), wrong_rank,
    )["classification"] == "RANK_MISMATCH"

    wrong_cli_identity = render_human("top-sql", top).replace("0x01", "0xFF")
    assert acceptance._human_cli_diff_summary(
        acceptance._human_cli_rows(wrong_cli_identity), rows,
    )["classification"] == "IDENTITY_MISMATCH"

    original_renderer = render_human
    monkeypatch.setattr(
        acceptance, "render_human",
        lambda scenario, data: original_renderer(scenario, data).replace(
            "microseconds", "milliseconds",
        ),
    )
    html_parity, cli_parity, metadata_match = acceptance._same_source_output_parity(
        top, rows, html, html_parser_status="TABLE_FOUND", html_metadata_match=True,
    )
    assert html_parity["status"] == "MATCH"
    assert cli_parity["classification"] == "METADATA_MISMATCH"
    assert not metadata_match


@pytest.mark.parametrize(
    ("metric", "total", "count", "raw_expected", "rank_expected"),
    [
        ("average_cpu_time", 1494425, 41, "36449.390243", "36449.3902"),
        ("average_elapsed_time", 1528406, 42, "36390.619047", "36390.6190"),
    ],
)
def test_sqlserver_average_decimal_values_match_independent_operands(
    metric: str, total: int, count: int, raw_expected: str, rank_expected: str,
) -> None:
    with localcontext() as context:
        context.prec = 50
        quotient = Decimal(total) / Decimal(count)
        expected_raw = quotient.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        expected_rank = expected_raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        incorrectly_rounded_raw = quotient.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    assert expected_raw == Decimal(raw_expected)
    assert expected_rank == Decimal(rank_expected)
    assert incorrectly_rounded_raw != Decimal(raw_expected)
    assert expected_rank + Decimal("0.0001") != Decimal(rank_expected)
    assert acceptance._same_number(rank_expected, Decimal(rank_expected))
    assert not acceptance._same_number("36449.3903", rank_expected)


@pytest.mark.parametrize("corruption", [
    "unit", "name", "rank", "descending_order", "empty", "top_n", "native_sql_identity",
    "native_plan_identity", "sysadmin", "write", "output_parity",
])
def test_acceptance_layers_reject_contract_and_safety_corruption(corruption: str) -> None:
    report = _acceptance_report()
    metric_result = report["metric_results"]["cpu_time"]
    if corruption == "unit":
        metric_result["unit"] = "milliseconds"
    elif corruption == "name":
        metric_result["metric_name"] = "total_elapsed_time"
    elif corruption == "rank":
        metric_result["rows"][0]["rank"] = 2
    elif corruption == "descending_order":
        metric_result["rows"].reverse()
    elif corruption == "empty":
        metric_result["rows"] = []
        metric_result["row_count"] = 0
    elif corruption == "top_n":
        metric_result["rows"] = [
            {"rank": rank, "metric_value": 100 - rank, "fields": {}, "plan_fields": {}}
            for rank in range(1, 12)
        ]
    elif corruption == "native_sql_identity":
        metric_result["native_identity_complete"] = False
    elif corruption == "native_plan_identity":
        metric_result["native_identity_complete"] = False
    elif corruption == "sysadmin":
        report["sysadmin"] = True
    elif corruption == "write":
        report["toolkit_database_write"] = True
    elif corruption == "output_parity":
        metric_result["html_parity"] = "IDENTITY_MISMATCH"
    else:
        raise AssertionError(f"Unexpected corruption case: {corruption}")

    layers = acceptance._acceptance_layers(report)
    if corruption in {"sysadmin", "write"}:
        assert layers["SAFETY"]["status"] == "FAIL"
    elif corruption == "output_parity":
        assert layers["OUTPUT_PARITY"]["status"] == "FAIL"
    else:
        assert layers["COLLECTION_VALIDATION"]["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["sql_handle", "plan_handle", "offset", "generation"])
def test_native_identity_completeness_rejects_blank_or_wrongly_typed_fields(
    mutation: str,
) -> None:
    item = _item(1, 100)
    assert acceptance._item_native_identity_complete(item)
    if mutation == "sql_handle":
        item["sql_identity"]["fields"]["sql_handle"] = ""
    elif mutation == "plan_handle":
        item["plan_identity"]["fields"]["plan_handle"] = ""
    elif mutation == "offset":
        item["sql_identity"]["fields"]["statement_start_offset"] = True
    else:
        item["plan_identity"]["fields"]["plan_generation_num"] = "1"
    assert not acceptance._item_native_identity_complete(item)


def test_acceptance_runs_exactly_one_toolkit_collection_per_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = SimpleNamespace(
        db_type=DatabaseType.SQLSERVER,
        host="127.0.0.1",
        port=1433,
        database="master",
        credential_alias="sqlserver-test-monitor",
        connection=object(),
    )
    inventory = SimpleNamespace(get=Mock(return_value=instance))
    monkeypatch.setattr(acceptance, "Inventory", SimpleNamespace(load=Mock(return_value=inventory)))
    settings = SimpleNamespace(
        connect_timeout=5, query_timeout=10, encrypt=True, trust_server_certificate=False,
    )
    monkeypatch.setattr(
        acceptance, "SQLServerSettings", SimpleNamespace(load=Mock(return_value=settings)),
    )
    monkeypatch.setattr(acceptance, "resolve_connection_settings", lambda *_args: settings)
    credential = Credential("monitor", "not-printed")
    store = SimpleNamespace(get=Mock(return_value=credential))
    monkeypatch.setattr(acceptance, "KeyringCredentialStore", lambda: store)

    cursor = Mock()
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = ("16.0.4305.5", "Developer", "master", 1, 0)
    connection = Mock()
    connection.cursor.return_value = cursor
    pyodbc = SimpleNamespace(
        drivers=lambda: ["ODBC Driver 18 for SQL Server"],
        connect=Mock(return_value=connection),
    )
    monkeypatch.setattr(
        acceptance, "importlib", SimpleNamespace(import_module=lambda _name: pyodbc),
    )
    monkeypatch.setattr(
        acceptance, "select_sqlserver_driver",
        lambda _drivers: "ODBC Driver 18 for SQL Server",
    )

    calls: list[tuple[str, int]] = []
    def collect_once(metric: str, top_n: int, report_path: Path) -> dict[str, Any]:
        calls.append((metric, top_n))
        top = _top(metric, (KNOWN_VALUES[metric], Decimal("1.0000")))
        report_path.write_text(_html_for(top), encoding="utf-8")
        return _envelope(top)

    monkeypatch.setattr(acceptance, "_run_toolkit", collect_once)
    result = acceptance.run()

    assert result["result"] == "VERIFIED", result["acceptance_layers"]
    assert calls == [(metric, 10) for metric in acceptance.METRICS]
    assert [entry["row_count"] for entry in result["metric_results"].values()] == [2] * 7
    assert connection.close.call_count == 1


def test_run_toolkit_invokes_one_bounded_cli_process_and_inherits_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / ".venv" / "Scripts" / "dba.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    report_path = tmp_path / "report.html"
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(_envelope(_top("cpu_time"))),
        stderr="suppressed child output",
    )
    subprocess_run = Mock(return_value=completed)
    monkeypatch.setattr(acceptance, "subprocess", SimpleNamespace(run=subprocess_run))

    result = acceptance._run_toolkit("cpu_time", 10, report_path)

    assert result["top_sql"]["items"][0]["metric_value"] == KNOWN_VALUES["cpu_time"]
    subprocess_run.assert_called_once()
    args, kwargs = subprocess_run.call_args
    command = args[0]
    assert command[1:7] == [
        "diagnose", "sqlserver2022", "top-sql", "--credential-backend", "os", "--metric",
    ]
    assert command[7:12] == ["cpu_time", "--top", "10", "--json", "--report"]
    assert kwargs["timeout"] == 90
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False
    assert "env" not in kwargs


def test_unrecognized_cli_options_are_rejected_without_running_acceptance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    run = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(acceptance, "run", run)

    assert acceptance.main(["--unrecognized-option"]) == 2
    run.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "BLOCKED"
    assert output["stage"] == "startup"
    assert output["summary"] == "ACCEPTANCE_PREREQUISITE_OR_CHECK_FAILED"


def test_sanitized_exception_payload_omits_sensitive_message() -> None:
    secret = "test-secret"
    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        payload = acceptance._safe_exception_payload(exc)

    assert payload["result"] == "BLOCKED"
    assert payload["summary"] == "ACCEPTANCE_PREREQUISITE_OR_CHECK_FAILED"
    assert secret not in json.dumps(payload)


def test_same_source_failure_reasons_keep_identity_and_value_errors_distinct() -> None:
    top = _top("cpu_time", (100, 80))
    items = acceptance._json_rows(top)
    html = acceptance._presenter_html_rows(items)
    html[1][1] = html[1][1].replace("0x02", "0xEE")
    assert acceptance._html_parity_result(html, items)["failure_reason"] == "IDENTITY_MISMATCH"

    html = acceptance._presenter_html_rows(items)
    html[1][2] = "79"
    assert acceptance._html_parity_result(html, items)["failure_reason"] == "VALUE_MISMATCH"

    human = render_human("top-sql", top).replace("80", "79")
    assert acceptance._human_cli_diff_summary(
        acceptance._human_cli_rows(human), items,
    )["classification"] == "VALUE_MISMATCH"
