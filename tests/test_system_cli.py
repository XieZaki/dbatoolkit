from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dba.inventory import Inventory

pytestmark = pytest.mark.system


def is_local_test_instance(group: str | None, tags: list[str]) -> bool:
    return (group or "").casefold() == "local-test" or bool(
        {tag.casefold() for tag in tags}.intersection({"test", "docker", "local-test"})
    )


def supported_collectors(database_type: str) -> tuple[str, ...]:
    common = ("server-info", "database-status", "active-sessions")
    if database_type == "postgresql":
        return (*common, "long-running")
    return (*common, "active-requests", "long-running")


def is_structured_incomplete_diagnosis(command: tuple[str, ...], returncode: int,
                                       output: str) -> bool:
    if returncode == 0 or not command or command[0] != "diagnose":
        return False
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False
    summary = payload.get("incident_summary")
    report_path = payload.get("report_path")
    return bool(
        isinstance(summary, dict)
        and summary.get("data_completeness") in {"PARTIAL", "INCOMPLETE"}
        and isinstance(report_path, str)
        and Path(report_path).is_file()
    )


def is_oracle_create_session_block(command: tuple[str, ...], returncode: int,
                                   output: str) -> bool:
    if returncode == 0 or len(command) < 2 or command[0] not in {
        "check", "test-connection", "permissions", "collect", "diagnose"
    }:
        return False
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False
    error = payload.get("error")
    return bool(
        isinstance(error, dict)
        and error.get("vendor_code") == "ORA-01045"
        and "CREATE SESSION" in str(error.get("message", ""))
    )


def is_expected_oracle_disk_permission_block(command: tuple[str, ...], returncode: int,
                                             output: str) -> bool:
    """Recognize only the documented local Oracle test account's missing core grants."""
    if returncode == 0 or command != ("diagnose", "oraclefree", "disk-growth"):
        return False
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False
    readiness = payload.get("permission_readiness")
    expected_missing = {
        "SELECT SYS.DBA_TABLESPACE_USAGE_METRICS",
        "SELECT SYS.DBA_TABLESPACES",
        "SELECT SYS.DBA_DATA_FILES",
        "SELECT SYS.DBA_TEMP_FREE_SPACE",
    }
    return bool(
        payload.get("error_type") == "MissingPermission"
        and isinstance(readiness, dict)
        and readiness.get("feature") == "disk-growth"
        and readiness.get("status") == "MISSING"
        and set(readiness.get("missing_required", ())) == expected_missing
        and readiness.get("toolkit_executed") is False
    )


def test_real_cli_against_explicit_local_test_inventory() -> None:
    if os.environ.get("DBA_TOOLKIT_SYSTEM_TESTS") != "1":
        pytest.skip("set DBA_TOOLKIT_SYSTEM_TESTS=1 to opt into real local system tests")

    selected_ids = [
        value.strip() for value in os.environ.get("DBA_TOOLKIT_SYSTEM_DATABASES", "").split(",")
        if value.strip()
    ]
    if not selected_ids:
        pytest.fail("set DBA_TOOLKIT_SYSTEM_DATABASES to explicit database_id values")

    inventory = Inventory.load(Path("config/inventory.yaml"))
    selected = [inventory.get(database_id) for database_id in selected_ids]
    unsafe = [
        instance.id for instance in selected
        if not instance.enabled or not is_local_test_instance(instance.group, instance.tags)
    ]
    if unsafe:
        pytest.skip(
            "Not safe for automated system test; selected Inventory entries must be enabled and "
            "explicitly tagged local-test: " + ", ".join(unsafe)
        )

    executable = shutil.which("dba")
    if executable is None:
        executable_name = "dba.exe" if os.name == "nt" else "dba"
        candidate = Path(sys.executable).parent / executable_name
        if not candidate.is_file():
            pytest.fail("dba CLI executable is not installed in the active Python environment")
        executable = str(candidate)

    outcomes: list[dict[str, object]] = []
    commands: list[tuple[str, ...]] = [
        ("--version",), ("list",), ("doctor",), ("runtime-info",), ("validate",),
    ]
    for instance in selected:
        commands.extend([
            ("show", instance.id),
            ("check", instance.id),
            ("test-connection", instance.id),
            ("permissions", instance.id, "--all-features"),
            *(('collect', instance.id, collector)
              for collector in supported_collectors(instance.db_type.value)),
            ("diagnose", instance.id, "slow"),
            ("diagnose", instance.id, "disk-growth"),
            ("diagnose", instance.id, "replication"),
        ])

    blocked_instances: dict[str, str] = {}
    for command in commands:
        if len(command) > 1 and command[1] in blocked_instances:
            outcomes.append({
                "command": list(command), "exit_code": None, "elapsed_ms": 0,
                "output_bytes": 0, "skipped_reason": blocked_instances[command[1]],
            })
            continue
        started = time.monotonic()
        result = subprocess.run(
            [executable, *command, "--json"] if command[0] not in {"--version", "validate"}
            else [executable, *command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        output = result.stdout
        assert "Traceback" not in output and "Exception ignored" not in output, (
            f"CLI command crashed: {' '.join(command)}"
        )
        expected_create_session_block = is_oracle_create_session_block(
            command, result.returncode, output
        )
        expected_oracle_disk_permission_block = is_expected_oracle_disk_permission_block(
            command, result.returncode, output
        )
        outcomes.append({
            "command": list(command), "exit_code": result.returncode, "elapsed_ms": elapsed_ms,
            "output_bytes": len(output.encode("utf-8")),
            **({"expected_blocker": "ORA-01045 CREATE SESSION"}
               if expected_create_session_block else {}),
            **({"expected_blocker": "Oracle disk-growth core grants missing"}
               if expected_oracle_disk_permission_block else {}),
        })
        assert result.returncode == 0 or is_structured_incomplete_diagnosis(
            command, result.returncode, output
        ) or expected_create_session_block or expected_oracle_disk_permission_block, (
            f"CLI command failed: {' '.join(command)}"
        )
        if expected_create_session_block:
            blocked_instances[command[1]] = "Skipped: ORA-01045 CREATE SESSION is missing"

    artifact = Path(".tmp/system-test/cli-results.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
