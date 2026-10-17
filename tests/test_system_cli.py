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
            ("test-connection", instance.id),
            ("permissions", instance.id, "--all-features"),
            ("collect", instance.id, "server-info"),
            ("collect", instance.id, "database-status"),
            ("collect", instance.id, "active-sessions"),
            ("collect", instance.id, "active-requests"),
            ("collect", instance.id, "long-running"),
            ("diagnose", instance.id, "slow"),
            ("diagnose", instance.id, "disk-growth"),
            ("diagnose", instance.id, "replication"),
        ])

    for command in commands:
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
        outcomes.append({
            "command": list(command), "exit_code": result.returncode, "elapsed_ms": elapsed_ms,
            "output_bytes": len(output.encode("utf-8")),
        })
        assert result.returncode == 0, f"CLI command failed: {' '.join(command)}"

    artifact = Path(".tmp/system-test/cli-results.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
