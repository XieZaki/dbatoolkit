from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_system_cli import (
    is_local_test_instance,
    is_oracle_create_session_block,
    is_structured_incomplete_diagnosis,
    supported_collectors,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("group", "tags", "expected"),
    [
        ("local-test", [], True),
        ("LOCAL-TEST", [], True),
        (None, ["test"], True),
        (None, ["docker"], True),
        (None, ["local-test"], True),
        ("production", ["24x7"], False),
        (None, [], False),
    ],
)
def test_system_inventory_safety_gate(group: str | None, tags: list[str], expected: bool) -> None:
    assert is_local_test_instance(group, tags) is expected


@pytest.mark.unit
def test_system_collectors_only_include_implemented_postgresql_collectors() -> None:
    assert supported_collectors("postgresql") == (
        "server-info", "database-status", "active-sessions", "long-running"
    )


@pytest.mark.unit
def test_system_runner_accepts_only_reported_incomplete_diagnosis(
    tmp_path: Path,
) -> None:
    report = tmp_path / "diagnosis.html"
    report.write_text("<html></html>", encoding="utf-8")
    output = json.dumps({
        "incident_summary": {"data_completeness": "PARTIAL"},
        "report_path": str(report),
    })
    assert is_structured_incomplete_diagnosis(("diagnose", "mysql84", "slow"), 1, output)
    assert not is_structured_incomplete_diagnosis(("collect", "mysql84", "active-requests"), 1, output)
    assert not is_structured_incomplete_diagnosis(("diagnose", "mysql84", "slow"), 1, "not json")


@pytest.mark.unit
def test_system_runner_records_oracle_create_session_permission_block() -> None:
    output = json.dumps({
        "error": {
            "vendor_code": "ORA-01045",
            "message": "User DBA_MONITOR does not have CREATE SESSION privilege.",
        },
    })
    assert is_oracle_create_session_block(("check", "oraclefree"), 1, output)
    assert not is_oracle_create_session_block(("check", "oraclefree"), 0, output)
    assert not is_oracle_create_session_block(("check", "oraclefree"), 1, "not json")
