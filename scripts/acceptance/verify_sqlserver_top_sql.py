"""User-run, read-only SQL Server Top SQL live acceptance helper.

Run from an interactive PowerShell in the repository with the supported venv:
    .\\.venv\\Scripts\\python.exe scripts\\acceptance\\verify_sqlserver_top_sql.py

The helper reads only the sqlserver2022 Inventory row and its Windows
Credential Manager alias. It never prints credentials or SQL text, creates no
database objects, and removes its temporary HTML reports on exit.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dba.connection import resolve_connection_settings
from dba.core import DatabaseType
from dba.credentials import KeyringCredentialStore
from dba.inventory import Inventory
from dba.logging import redact
from dba.presentation import render_human
from dba.reporting.presenters import _top_sql_section
from dba.runtime import select_sqlserver_driver
from dba.settings import SQLServerSettings

ROOT = Path(__file__).resolve().parents[2]
DATABASE_ID = "sqlserver2022"
EXPECTED_HOST = "127.0.0.1"
EXPECTED_PORT = 1433
EXPECTED_DATABASE = "master"
METRICS = (
    "cpu_time",
    "logical_reads",
    "physical_reads",
    "execution_count",
    "elapsed_time",
    "average_cpu_time",
    "average_elapsed_time",
)
EXPECTED_UNITS = {
    "cpu_time": ("total_cpu_time", "microseconds"),
    "logical_reads": ("total_logical_reads", "pages"),
    "physical_reads": ("total_physical_reads", "pages"),
    "execution_count": ("execution_count", "executions"),
    "elapsed_time": ("total_elapsed_time", "microseconds"),
    "average_cpu_time": ("average_cpu_time", "microseconds/execution"),
    "average_elapsed_time": ("average_elapsed_time", "microseconds/execution"),
}
_ALLOWED_STAGES = frozenset({
    "startup", "inventory", "runtime_settings", "credential_lookup", "driver_discovery",
    "database_connect", "database_identity", "toolkit_json", "report_parse",
    "html_report", "human_cli", "comparison", "cleanup",
})
_ACTIVE_STAGE = "startup"
_SAFE_ERROR_SUMMARIES = {
    "AttributeError": "INTERNAL_OBJECT_SHAPE_MISMATCH",
    "KeyError": "INTERNAL_REQUIRED_FIELD_MISSING",
    "TypeError": "INTERNAL_VALUE_SHAPE_MISMATCH",
    "ImportError": "LOCAL_RUNTIME_MODULE_UNAVAILABLE",
    "ModuleNotFoundError": "LOCAL_RUNTIME_MODULE_UNAVAILABLE",
    "RuntimeError": "ACCEPTANCE_PREREQUISITE_OR_CHECK_FAILED",
    "TimeoutError": "LOCAL_OPERATION_TIMED_OUT",
}
_FAILURE_REASONS: dict[str, frozenset[str]] = {
    "TOOLKIT_SUBPROCESS": frozenset({"CLI_EXIT_NONZERO", "CLI_TIMEOUT"}),
    "TOOLKIT_JSON_PARSE": frozenset({"STDOUT_EMPTY", "JSON_SYNTAX_INVALID", "OUTPUT_ENCODING_INVALID"}),
    "TOOLKIT_JSON_CONTRACT": frozenset({
        "JSON_ROOT_NOT_OBJECT", "SUCCESS_FIELD_INVALID", "CLI_REPORTED_FAILURE",
        "TOP_SQL_RESULT_INVALID", "VERSION_PROFILE_INVALID",
    }),
}


class _TopTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


class AcceptanceCheckFailure(RuntimeError):
    """Safe, enumerated helper failure; never carries driver or CLI output."""

    def __init__(self, check_id: str, failure_reason: str, **safe_details: int | bool) -> None:
        if failure_reason not in _FAILURE_REASONS.get(check_id, frozenset()):
            check_id, failure_reason, safe_details = (
                "TOOLKIT_JSON_CONTRACT", "TOP_SQL_RESULT_INVALID", {}
            )
        super().__init__("Acceptance check failed.")
        self.check_id = check_id
        self.failure_reason = failure_reason
        self.safe_details = {
            key: value for key, value in safe_details.items()
            if key in {"return_code", "json_line", "json_column", "stdout_empty"}
            and type(value) in {int, bool}
        }


def _set_stage(stage: str) -> None:
    """Track only a fixed, non-sensitive phase label for sanitized failures."""
    global _ACTIVE_STAGE
    _ACTIVE_STAGE = stage if stage in _ALLOWED_STAGES else "startup"


def _safe_exception_payload(exc: Exception) -> dict[str, Any]:
    """Return location and allow-listed classification without exception text."""
    frames = []
    traceback = exc.__traceback__
    script_path = Path(__file__).resolve()
    while traceback is not None:
        try:
            if Path(traceback.tb_frame.f_code.co_filename).resolve() == script_path:
                frames.append(traceback.tb_lineno)
        except (OSError, RuntimeError, ValueError):
            pass
        traceback = traceback.tb_next
    result: dict[str, Any] = {
        "result": "BLOCKED",
        "error_type": type(exc).__name__ if type(exc).__name__ in _SAFE_ERROR_SUMMARIES else "UnexpectedError",
        "stage": _ACTIVE_STAGE,
        "script": script_path.name,
        "summary": _SAFE_ERROR_SUMMARIES.get(type(exc).__name__, "UNEXPECTED_ACCEPTANCE_SCRIPT_ERROR"),
    }
    if isinstance(exc, AcceptanceCheckFailure):
        result.update({
            "error_type": "RuntimeError",
            "summary": "ACCEPTANCE_CHECK_FAILED",
            "check_id": exc.check_id,
            "failure_reason": exc.failure_reason,
            **exc.safe_details,
        })
    if frames:
        result["line"] = frames[-1]
    return result


def _credential_for_alias(store: KeyringCredentialStore, alias: str) -> Any:
    """Read the explicitly selected OS-store alias; never invoke encrypted-store fallback."""
    credential = store.get(alias)
    if credential is None:
        raise RuntimeError("Windows Credential Manager alias unavailable")
    return credential


def _parse_toolkit_output(output: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the CLI JSON envelope before accessing nested Top SQL fields."""
    if not isinstance(output, dict):
        raise AcceptanceCheckFailure("TOOLKIT_JSON_CONTRACT", "JSON_ROOT_NOT_OBJECT")
    success = output.get("success")
    if type(success) is not bool:
        raise AcceptanceCheckFailure("TOOLKIT_JSON_CONTRACT", "SUCCESS_FIELD_INVALID")
    if not success:
        raise AcceptanceCheckFailure("TOOLKIT_JSON_CONTRACT", "CLI_REPORTED_FAILURE")
    top = output.get("top_sql")
    if not isinstance(top, dict) or not isinstance(top.get("items"), list):
        raise AcceptanceCheckFailure("TOOLKIT_JSON_CONTRACT", "TOP_SQL_RESULT_INVALID")
    version_profile = output.get("version_profile", {})
    if not isinstance(version_profile, dict):
        raise AcceptanceCheckFailure("TOOLKIT_JSON_CONTRACT", "VERSION_PROFILE_INVALID")
    return output, top


def _odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def _same_number(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False


def _json_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("items")
    if not isinstance(rows, list):
        raise TypeError("Unexpected Top SQL item list")
    parsed: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise TypeError("Unexpected Top SQL item")
        sql_identity = item.get("sql_identity")
        plan_identity = item.get("plan_identity")
        sql_fields = sql_identity.get("fields", {}) if isinstance(sql_identity, dict) else {}
        plan_fields = plan_identity.get("fields", {}) if isinstance(plan_identity, dict) else {}
        if not isinstance(sql_fields, dict) or not isinstance(plan_fields, dict):
            raise TypeError("Unexpected native identity fields")
        parsed.append({
            "rank": item.get("rank"),
            "fields": sql_fields,
            "plan_fields": plan_fields,
            "sql_identity": sql_identity,
            "plan_identity": plan_identity,
            "metric_value": item.get("metric_value"),
        })
    return parsed


def _item_native_identity_complete(item: dict[str, Any]) -> bool:
    sql_identity = item.get("sql_identity")
    plan_identity = item.get("plan_identity")
    sql_fields = sql_identity.get("fields") if isinstance(sql_identity, dict) else None
    plan_fields = plan_identity.get("fields") if isinstance(plan_identity, dict) else None
    return (
        isinstance(sql_fields, dict)
        and isinstance(plan_fields, dict)
        and isinstance(sql_fields.get("sql_handle"), str)
        and bool(sql_fields["sql_handle"])
        and type(sql_fields.get("statement_start_offset")) is int
        and type(sql_fields.get("statement_end_offset")) is int
        and isinstance(plan_fields.get("plan_handle"), str)
        and bool(plan_fields["plan_handle"])
        and type(plan_fields.get("plan_generation_num")) is int
    )


def _metric_check_failures(metric: str, result: dict[str, Any]) -> list[dict[str, str]]:
    checks = (
        ("COLLECTION_STATUS", result.get("collection") == "COLLECTED", "COLLECTION_NOT_COLLECTED"),
        ("DATA_VISIBILITY", result.get("visibility") == "COMPLETE", "VISIBILITY_INCOMPLETE"),
        ("COMPLETENESS", result.get("completeness") == "COMPLETE", "COLLECTION_INCOMPLETE"),
        ("RESULT_ROWS", result.get("row_count", 0) > 0, "NO_RANKED_ROWS_TO_VERIFY"),
        ("TOP_N_LIMIT", result.get("top_n_respected") is True, "TOP_N_LIMIT_EXCEEDED"),
        ("RANK_ORDER", result.get("rank_order_valid") is True, "RANK_ORDER_INVALID"),
        ("NATIVE_IDENTITY", result.get("native_identity_complete") is True,
         "NATIVE_IDENTITY_INCOMPLETE"),
        ("OUTPUT_PARITY", result.get("output_parity") is True, "OUTPUT_PARITY_MISMATCH"),
    )
    return [
        {"metric": metric, "check_id": check_id, "failure_reason": reason}
        for check_id, passed, reason in checks if not passed
    ]


def _html_table(path: Path) -> tuple[list[list[str]], str]:
    parser = _TopTableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    for table in parser.tables:
        headers = [cell.casefold() for cell in table[0]] if table else []
        if headers in (
            ["rank", "sql identity", "metric value"],
            ["排序", "sql 原生身份", "指标值"],
        ):
            return table[1:], "TABLE_FOUND"
    return [], "PARSER_MISMATCH"


def _expected_native_identity(item: dict[str, Any], *, html: bool) -> str:
    identity = item.get("sql_identity")
    if not isinstance(identity, dict):
        rendered = "identity unavailable"
    else:
        fields = identity.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        allowed = {
            "sql_handle", "plan_handle", "query_hash", "query_plan_hash",
            "statement_start_offset", "statement_end_offset", "database_id",
            "plan_generation_num", "queryid", "dbid", "userid", "pid", "backend_start",
            "digest", "schema_name", "processlist_id", "thread_id", "sql_id",
            "child_number", "inst_id", "plan_hash_value",
        }
        if html:
            # Match the human HTML display projection: each native value is
            # redacted/bounded, and the whole identity cell has a final cap.
            native = ", ".join(
                f"{key}={str(redact(value))[:100]}" for key, value in fields.items()
                if key in allowed
            )
        else:
            native = ", ".join(f"{key}={value}" for key, value in fields.items() if key in allowed)
        rendered = f"{identity.get('database_engine', 'UNKNOWN')}:{native or 'identity unavailable'}" if html else (
            native or "identity unavailable"
        )
    return rendered[:256 if html else 240]


def _presenter_html_rows(items: list[dict[str, Any]]) -> list[list[str]]:
    section = _top_sql_section({"collection_status": "COLLECTED", "items": items})
    if section is None or not section.tables:
        return []
    return [list(row) for row in section.tables[0].rows]


def _identity_display_matches(expected: str, actual: str) -> bool:
    """Accept only exact or structurally parsed whitespace-only display variance."""
    return (expected == actual
            or _identity_diff_summary(expected, actual)["classification"]
            == "WHITESPACE_NORMALIZATION")


def _identity_diff_summary(expected: str, actual: str) -> dict[str, Any]:
    """Classify a display mismatch without emitting identity values."""
    engine_prefixes = {"sqlserver", "postgresql", "mysql", "oracle"}

    def split_engine_prefix(value: str) -> tuple[str | None, str]:
        prefix, separator, remainder = value.partition(":")
        if separator and prefix.casefold() in engine_prefixes:
            return prefix.casefold(), remainder
        return None, value

    expected_prefix, expected_native = split_engine_prefix(expected)
    actual_prefix, actual_native = split_engine_prefix(actual)
    expected_parts = [part.strip() for part in expected_native.split(",")
                      if part.strip() and "=" in part]
    actual_parts = [part.strip() for part in actual_native.split(",")
                    if part.strip() and "=" in part]
    expected_fields = [part.partition("=")[0].strip() for part in expected_parts]
    actual_fields = [part.partition("=")[0].strip() for part in actual_parts]
    expected_values = {part.partition("=")[0].strip(): part.partition("=")[2].strip()
                       for part in expected_parts if "=" in part}
    actual_values = {part.partition("=")[0].strip(): part.partition("=")[2].strip()
                     for part in actual_parts if "=" in part}

    if expected == actual:
        category, first_field = "EXACT_MATCH", "identity_cell"
    elif (expected_prefix != actual_prefix
          and (expected_prefix is not None or (expected_fields and actual_fields))):
        category, first_field = "ENGINE_PREFIX_MISMATCH", "database_engine"
    else:
        missing = [field for field in expected_fields if field not in actual_values]
        extra = [field for field in actual_fields if field not in expected_values]
        if missing:
            category, first_field = "FIELD_MISSING", missing[0]
        elif extra:
            category, first_field = "UNEXPECTED_FIELD", extra[0]
        elif expected_fields != actual_fields:
            category, first_field = "FIELD_ORDER_OR_SEPARATOR", next(
                (expected_fields[index] for index, field in enumerate(actual_fields)
                 if index >= len(expected_fields) or field != expected_fields[index]),
                "field_order",
            )
        elif not expected_fields and not actual_fields:
            # Empty parsed maps compare equal vacuously. Do not call arbitrary
            # unparseable text a whitespace-only difference.
            category, first_field = "IDENTITY_CELL_UNPARSEABLE", "identity_cell"
        else:
            first_field = next(
                (field for field in expected_fields
                 if actual_values.get(field) != expected_values.get(field)),
                "identity_cell",
            )
            expected_value = expected_values.get(first_field, "")
            actual_value = actual_values.get(first_field, "")
            if expected_value.startswith(actual_value) and len(actual_value) < len(expected_value):
                category = "FIELD_TRUNCATED"
            elif (expected_fields and actual_fields
                  and " ".join(expected.split()) == " ".join(actual.split())):
                category = "WHITESPACE_NORMALIZATION"
            elif (expected_fields and actual_fields
                  and expected_fields == actual_fields
                  and " ".join(expected_value.split()) == " ".join(actual_value.split())):
                category = "WHITESPACE_NORMALIZATION"
            else:
                category = "FIELD_VALUE_MISMATCH"

    first_diff_index = next(
        (index for index, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]),
        min(len(expected), len(actual)) if expected != actual else None,
    )

    def safe_codepoint(value: str | None) -> str:
        if value is None:
            return "END_OF_CELL"
        if value.isspace() or value in ",:=;|":
            return f"U+{ord(value):04X}:{unicodedata.name(value, 'UNKNOWN')}"
        return "IDENTITY_VALUE_CHAR_REDACTED"

    def safe_repr(value: str) -> str:
        key_pattern = (
            r"(?P<key>sql_handle|plan_handle|query_hash|query_plan_hash|"
            r"statement_start_offset|statement_end_offset|database_id|"
            r"plan_generation_num)"
        )
        pattern = re.compile(key_pattern + r"(?P<separator>\s*=\s*)(?P<value>[^,]*)")

        masked = list(value)
        preserve = set(" ,:=;|\t\r\n")
        safe_indexes: set[int] = set()
        matches = list(pattern.finditer(value))
        for match in matches:
            safe_indexes.update(range(*match.span("key")))
            safe_indexes.update(range(*match.span("separator")))
            for index in range(*match.span("value")):
                if value[index].isspace():
                    safe_indexes.add(index)
        if not matches:
            # Keep only structural characters/whitespace when no known native
            # identity field can be safely parsed from the cell.
            safe_indexes.update(
                index for index, character in enumerate(value)
                if character.isspace() or character in ",:=;|"
            )
        for index, character in enumerate(value):
            if index not in safe_indexes and character not in preserve:
                masked[index] = "X"
        return repr("".join(masked))

    return {
        "classification": category,
        "first_mismatch_field": first_field,
        "expected_field_names": expected_fields,
        "actual_field_names": actual_fields,
        "expected_cell_length": len(expected),
        "actual_cell_length": len(actual),
        "first_diff_index": first_diff_index,
        "expected_char_codepoint": (
            safe_codepoint(expected[first_diff_index])
            if first_diff_index is not None and first_diff_index < len(expected) else "END_OF_CELL"
        ),
        "actual_char_codepoint": (
            safe_codepoint(actual[first_diff_index])
            if first_diff_index is not None and first_diff_index < len(actual) else "END_OF_CELL"
        ),
        "expected_cell_repr_redacted": safe_repr(expected),
        "actual_cell_repr_redacted": safe_repr(actual),
    }


def _html_parity_result(
    rows: list[list[str]], items: list[dict[str, Any]], *,
    parser_status: str = "TABLE_FOUND", metadata_matches: bool = True,
) -> dict[str, Any]:
    if parser_status != "TABLE_FOUND" or not metadata_matches:
        return {"status": "MISMATCH", "failure_reason": "PARSER_MISMATCH"}
    if len(rows) != len(items):
        return {"status": "MISMATCH", "failure_reason": "ROW_NOT_FOUND"}
    for row, item in zip(rows, items, strict=True):
        if len(row) < 3:
            return {"status": "MISMATCH", "failure_reason": "PARSER_MISMATCH"}
        if row[0] != str(item.get("rank")):
            return {"status": "MISMATCH", "failure_reason": "ROW_NOT_FOUND"}
        expected_identity = _presenter_html_rows([item])
        expected_cell = expected_identity[0][1] if expected_identity else "identity unavailable"
        if row[1] != expected_cell:
            return {
                "status": "MISMATCH", "failure_reason": "IDENTITY_MISMATCH",
                "identity_diff": _identity_diff_summary(expected_cell, row[1]),
            }
        if not _same_number(row[2], item.get("metric_value")):
            return {"status": "MISMATCH", "failure_reason": "VALUE_MISMATCH"}
    return {"status": "MATCH", "failure_reason": None}


def _human_cli_rows(output: str) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        if len(parts) < 3:
            return None
        try:
            value = Decimal(parts[-1])
        except InvalidOperation:
            return None
        identity = " ".join(parts[1:-1])
        native_fields: dict[str, str | int] = {}
        for segment in identity.split(", "):
            key, separator, raw_value = segment.partition("=")
            if not separator or not key or not raw_value:
                continue
            key = key.strip()
            raw_value = raw_value.strip()
            native_fields[key] = (int(raw_value) if key in {
                "statement_start_offset", "statement_end_offset", "database_id"
            } and raw_value.isdigit() else raw_value)
        rows.append({"rank": int(parts[0]), "identity": identity,
                     "fields": native_fields, "value": value})
    return rows


def _human_cli_diff_summary(cli_rows: list[dict[str, Any]] | None,
                            items: list[dict[str, Any]]) -> dict[str, Any]:
    if cli_rows is None:
        return {"classification": "OUTPUT_PARSE_MISMATCH", "first_mismatch_field": "row_format"}
    if len(cli_rows) != len(items):
        return {"classification": "ROW_COUNT_MISMATCH", "first_mismatch_field": "ranked_rows",
                "cli_row_count": len(cli_rows), "json_row_count": len(items)}
    first_value_mismatch_rank: int | None = None
    value_mismatch_count = 0
    for cli, item in zip(cli_rows, items, strict=True):
        if cli["rank"] != item.get("rank"):
            return {"classification": "RANK_MISMATCH", "first_mismatch_field": "rank"}
        expected = _expected_native_identity(item, html=False)
        if not _identity_display_matches(expected, cli["identity"]):
            return {"classification": "IDENTITY_MISMATCH",
                    "first_mismatch_rank": int(item["rank"]),
                    "identity_diff": _identity_diff_summary(expected, cli["identity"])}
        if not _same_number(cli["value"], item.get("metric_value")):
            value_mismatch_count += 1
            if first_value_mismatch_rank is None:
                first_value_mismatch_rank = int(item["rank"])
    if value_mismatch_count:
        return {
            "classification": "VALUE_MISMATCH",
            "first_mismatch_field": "metric_value",
            "first_mismatch_rank": first_value_mismatch_rank,
            "value_mismatch_count": value_mismatch_count,
            "all_rank_and_identity_rows_match": True,
        }
    return {"classification": "MATCH", "first_mismatch_field": None}


def _same_source_output_parity(
    top: dict[str, Any], items: list[dict[str, Any]], html_rows: list[list[str]],
    *, html_parser_status: str, html_metadata_match: bool,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Render human CLI from the JSON invocation's result, never recollect DMV data."""
    html = _html_parity_result(
        html_rows, items, parser_status=html_parser_status,
        metadata_matches=html_metadata_match,
    )
    human_text = render_human("top-sql", top)
    cli = _human_cli_diff_summary(_human_cli_rows(human_text), items)
    metadata_match = all(token in human_text for token in (
        str(top.get("metric_name", "")), str(top.get("metric_unit", "")),
        "累计统计", "SQL 语句", "实例级",
    ))
    if not metadata_match:
        cli = {"classification": "METADATA_MISMATCH", "first_mismatch_field": "metric_metadata"}
    return html, cli, metadata_match


def _acceptance_layers(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate collection, same-result output parity, and monitoring safety."""
    failures: dict[str, list[str]] = {
        "COLLECTION_VALIDATION": [],
        "OUTPUT_PARITY": [],
        "SAFETY": [],
    }
    selected = report.get("selected_metrics")
    if not isinstance(selected, list) or selected != list(METRICS):
        failures["COLLECTION_VALIDATION"].append("ALL_SEVEN_METRICS_REQUIRED")
        selected = []

    results = report.get("metric_results")
    if not isinstance(results, dict):
        results = {}
        failures["COLLECTION_VALIDATION"].append("METRIC_RESULTS_INVALID")

    for metric in selected:
        data = results.get(metric)
        if not isinstance(data, dict):
            failures["COLLECTION_VALIDATION"].append(f"{metric}:RESULT_MISSING")
            continue
        expected_name, expected_unit = EXPECTED_UNITS[metric]
        rows = data.get("rows")
        profile = data.get("version_profile")
        if not isinstance(rows, list) or not isinstance(profile, dict):
            failures["COLLECTION_VALIDATION"].append(f"{metric}:RESULT_SHAPE_INVALID")
            continue
        ranks_valid = [row.get("rank") for row in rows] == list(range(1, len(rows) + 1))
        values: list[Decimal] = []
        for row in rows:
            try:
                value = Decimal(str(row.get("metric_value")))
            except (InvalidOperation, TypeError, ValueError):
                value = Decimal("NaN")
            values.append(value)
        precision_valid = all(
            value.is_finite()
            and (
                value == value.quantize(Decimal("0.0001"))
                if metric.startswith("average_")
                else value == value.to_integral_value()
            )
            for value in values
        )
        if not all((
            data.get("database_engine") == "sqlserver",
            data.get("database_version") == report.get("database_version"),
            str(report.get("database_version", "")).startswith("16."),
            profile.get("version_status") == "VERIFIED",
            data.get("profile_id") == "sqlserver_top_sql_shared",
            data.get("collection") == "COLLECTED",
            data.get("visibility") == "COMPLETE",
            data.get("completeness") == "COMPLETE",
            data.get("collection_scope") == "INSTANCE",
            data.get("metric_scope") == "SQL_STATEMENT",
            data.get("semantics") == "CUMULATIVE",
            data.get("metric_name") == expected_name,
            data.get("unit") == expected_unit,
            data.get("ranking_direction") == "DESCENDING",
            len(rows) > 0,
            len(rows) <= 10,
            ranks_valid,
            all(values[index] >= values[index + 1] for index in range(len(values) - 1)),
            precision_valid,
            data.get("native_identity_complete") is True,
        )):
            failures["COLLECTION_VALIDATION"].append(f"{metric}:CONTRACT_RANK_OR_VALUE_INVALID")
        if (data.get("html_parity") != "MATCH"
                or data.get("human_cli_parity") != "MATCH"
                or data.get("human_cli_metadata_match") is not True):
            failures["OUTPUT_PARITY"].append(f"{metric}:FORMAT_IDENTITY_OR_VALUE_MISMATCH")
    if (report.get("sysadmin") is not False
            or report.get("toolkit_database_write") is not False
            or report.get("toolkit_captured_business_sql_executed") is not False
            or report.get("test_driver_database_write") is not False
            or report.get("temporary_objects_created") is not False):
        failures["SAFETY"].append("READ_ONLY_BOUNDARY_UNVERIFIED")
    if report.get("database") != EXPECTED_DATABASE:
        failures["SAFETY"].append("DATABASE_SCOPE_MISMATCH")

    return {
        name: {"status": "FAIL" if reasons else "PASS", "reasons": reasons}
        for name, reasons in failures.items()
    }


def _run_toolkit(metric: str, top_n: int, report_path: Path) -> dict[str, Any]:
    executable = ROOT / ".venv" / "Scripts" / "dba.exe"
    if not executable.is_file():
        raise RuntimeError("Toolkit executable missing")
    try:
        completed = subprocess.run(
            [str(executable), "diagnose", DATABASE_ID, "top-sql", "--credential-backend", "os",
             "--metric", metric, "--top", str(top_n), "--json", "--report", str(report_path)],
            cwd=ROOT, text=True, capture_output=True, timeout=90, check=False,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceCheckFailure("TOOLKIT_SUBPROCESS", "CLI_TIMEOUT") from None
    if completed.returncode != 0:
        raise AcceptanceCheckFailure(
            "TOOLKIT_SUBPROCESS", "CLI_EXIT_NONZERO", return_code=completed.returncode
        )
    if not completed.stdout.strip():
        raise AcceptanceCheckFailure("TOOLKIT_JSON_PARSE", "STDOUT_EMPTY", stdout_empty=True)
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AcceptanceCheckFailure(
            "TOOLKIT_JSON_PARSE", "JSON_SYNTAX_INVALID",
            json_line=exc.lineno, json_column=exc.colno,
        ) from None
    except UnicodeError:
        raise AcceptanceCheckFailure(
            "TOOLKIT_JSON_PARSE", "OUTPUT_ENCODING_INVALID"
        ) from None
    parsed_output, _ = _parse_toolkit_output(output)
    return parsed_output


def run() -> dict[str, Any]:
    _set_stage("inventory")
    inventory = Inventory.load(ROOT / "config" / "inventory.yaml")
    instance = inventory.get(DATABASE_ID)
    if (instance.db_type is not DatabaseType.SQLSERVER or instance.host != EXPECTED_HOST
            or instance.port != EXPECTED_PORT
            or (instance.database or EXPECTED_DATABASE).casefold() != EXPECTED_DATABASE):
        raise RuntimeError("Inventory target is outside the approved local SQL Server scope")

    _set_stage("runtime_settings")
    settings = SQLServerSettings.load(ROOT / "config" / "runtime.yaml")
    settings = resolve_connection_settings(DatabaseType.SQLSERVER, instance.connection, settings)
    _set_stage("credential_lookup")
    store = KeyringCredentialStore()
    credential = _credential_for_alias(store, instance.credential_alias)

    _set_stage("driver_discovery")
    pyodbc = importlib.import_module("pyodbc")
    driver = select_sqlserver_driver(list(pyodbc.drivers()))
    if driver not in {"ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"}:
        raise RuntimeError("Supported Microsoft ODBC Driver unavailable")
    parts = {
        "DRIVER": driver,
        "SERVER": f"tcp:{EXPECTED_HOST},{EXPECTED_PORT}",
        "DATABASE": EXPECTED_DATABASE,
        "UID": credential.username,
        "PWD": credential.password,
        "Encrypt": "yes" if settings.encrypt else "no",
        "TrustServerCertificate": "yes" if settings.trust_server_certificate else "no",
        "APP": "dba-top-sql-acceptance-reference",
    }
    connection_string = ";".join(f"{key}={_odbc_value(value)}" for key, value in parts.items())
    _set_stage("database_connect")
    connection = pyodbc.connect(
        connection_string, timeout=settings.connect_timeout, autocommit=True, readonly=True,
    )
    connection.timeout = settings.query_timeout
    try:
        cursor = connection.cursor()
        try:
            _set_stage("database_identity")
            identity = cursor.execute("""SELECT
                CAST(SERVERPROPERTY('ProductVersion') AS varchar(32)),
                CAST(SERVERPROPERTY('Edition') AS varchar(128)),
                DB_NAME(),
                DB_ID(),
                IS_SRVROLEMEMBER('sysadmin');""").fetchone()
        finally:
            cursor.close()
        version, edition, database, database_id, is_sysadmin = identity
        if not str(version).startswith("16.") or database != EXPECTED_DATABASE:
            raise RuntimeError("Live identity did not match the approved SQL Server 2022 target")
        if is_sysadmin != 0:
            raise RuntimeError("Monitoring identity unexpectedly has sysadmin membership")

        report: dict[str, Any] = {
            "result": "PARTIAL",
            "validation_scope": "SQLSERVER_2022_TOP_SQL",
            "selected_metrics": list(METRICS),
            "test_time_utc": datetime.now(timezone.utc).isoformat(),
            "software_version": "0.9.0",
            "database_version": str(version),
            "edition": str(edition),
            "database": str(database),
            "database_id": database_id,
            "driver": driver,
            "sysadmin": False,
            "metric_results": {},
            "failure_checks": [],
            "toolkit_captured_business_sql_executed": False,
            "toolkit_database_write": False,
            "test_driver_database_write": False,
            "temporary_objects_created": False,
            "cleanup": (
                "No workload or database object was created; the read-only identity "
                "connection and each Toolkit process were closed."
            ),
        }

        with tempfile.TemporaryDirectory(prefix="dba-top-sql-acceptance-") as temporary:
            temp_root = Path(temporary)
            for metric in METRICS:
                report_path = temp_root / f"{metric}.html"
                _set_stage("toolkit_json")
                output = _run_toolkit(metric, 10, report_path)
                _set_stage("report_parse")
                _, top = _parse_toolkit_output(output)
                items = _json_rows(top)
                if output.get("report", {}).get("details", {}).get("top_sql") != top:
                    raise AcceptanceCheckFailure(
                        "TOOLKIT_JSON_CONTRACT", "TOP_SQL_RESULT_INVALID"
                    )
                _set_stage("html_report")
                html_rows, parser_status = _html_table(report_path)
                html_text = report_path.read_text(encoding="utf-8")
                html_metadata_match = all(token in html_text for token in (
                    "INSTANCE", "SQL_STATEMENT", "CUMULATIVE",
                    str(top.get("metric_name", "")), str(top.get("metric_unit", "")),
                ))
                _set_stage("human_cli")
                html_parity, human_parity, human_metadata_match = _same_source_output_parity(
                    top, items, html_rows, html_parser_status=parser_status,
                    html_metadata_match=html_metadata_match,
                )
                output_parity = (
                    html_parity["status"] == "MATCH"
                    and human_parity["classification"] == "MATCH"
                    and human_metadata_match
                )
                profile = output.get("version_profile")
                if not isinstance(profile, dict):
                    raise AcceptanceCheckFailure(
                        "TOOLKIT_JSON_CONTRACT", "VERSION_PROFILE_INVALID"
                    )
                rows_valid = (
                    len(items) <= 10
                    and [item.get("rank") for item in items] == list(range(1, len(items) + 1))
                    and all(
                        Decimal(str(items[index]["metric_value"]))
                        >= Decimal(str(items[index + 1]["metric_value"]))
                        for index in range(len(items) - 1)
                    )
                )
                native_identity_complete = all(
                    _item_native_identity_complete(item) for item in items
                )
                result = {
                    "database_engine": top.get("database_engine"),
                    "database_version": top.get("database_version"),
                    "version_profile": profile,
                    "profile_id": top.get("profile_id"),
                    "collection": top.get("collection_status"),
                    "visibility": top.get("data_visibility"),
                    "completeness": top.get("completeness"),
                    "collection_scope": top.get("collection_scope"),
                    "metric_scope": top.get("metric_scope"),
                    "semantics": top.get("metric_semantics"),
                    "metric_name": top.get("metric_name"),
                    "unit": top.get("metric_unit"),
                    "ranking_direction": top.get("ranking_direction"),
                    "row_count": len(items),
                    "top_n_respected": len(items) <= 10,
                    "rank_order_valid": rows_valid,
                    "native_identity_complete": native_identity_complete,
                    "html_parity": html_parity["status"],
                    "human_cli_parity": human_parity["classification"],
                    "human_cli_metadata_match": human_metadata_match,
                    "output_parity": output_parity,
                    "rows": items,
                    "limitations": top.get("limitations", []),
                }
                report["metric_results"][metric] = result
                report["failure_checks"].extend(_metric_check_failures(metric, result))
                if not output_parity:
                    report["failure_checks"].append({
                        "metric": metric, "check_id": "OUTPUT_PARITY",
                        "failure_reason": "FORMAT_IDENTITY_OR_VALUE_MISMATCH",
                    })

        report["acceptance_layers"] = _acceptance_layers(report)
        report["task_observations_pass"] = (
            all(layer["status"] == "PASS" for layer in report["acceptance_layers"].values())
            and not report["failure_checks"]
        )
        report["result"] = "VERIFIED" if report["task_observations_pass"] else "PARTIAL"
        physical = report["metric_results"].get("physical_reads", {})
        report["physical_reads_have_nonzero_sample"] = any(
            row.get("metric_value", 0) > 0 for row in physical.get("rows", [])
        )
        return report
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    try:
        _set_stage("startup")
        if argv:
            raise RuntimeError("This acceptance helper does not accept command-line options.")
        report = run()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("result") == "VERIFIED" else 2
    except Exception as exc:
        # Driver and child-process exceptions may embed credentials, endpoints,
        # or SQL. Report a fixed classification plus this script's line only.
        print(json.dumps(_safe_exception_payload(exc), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
