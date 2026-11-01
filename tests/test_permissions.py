from __future__ import annotations

from pathlib import Path

import pytest

from dba.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_single_deployment_permissions_guide_covers_four_engines_and_versions() -> None:
    guide = (ROOT / "docs" / "DATABASE_PERMISSIONS.md").read_text(encoding="utf-8")
    for heading in ("## SQL Server", "## Oracle", "## MySQL", "## PostgreSQL"):
        assert heading in guide
    assert "dba_monitor" in guide
    assert "2008 R2 through 2019" in guide
    assert "SQL Server 2022 and later" in guide
    assert "11gR2 through 12.1" in guide
    assert "12.2 and later" in guide
    assert "MySQL 5.7 and 8.x" in guide
    assert "PostgreSQL 13" in guide and "PostgreSQL 16" in guide
    assert "sys.dm_exec_query_memory_grants" in guide
    assert "SYS.V_$SQL_PLAN" in guide
    assert "performance_schema.*" in guide
    assert "pg_monitor" in guide
    assert "ALL PRIVILEGES" in guide and "Do not grant" in guide
    grant_lines = [line.strip() for line in guide.splitlines()
                   if line.strip().upper().startswith("GRANT ")]
    assert len(grant_lines) == len(set(grant_lines))


def test_readme_links_the_only_permissions_guide() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[Database Permissions](docs/DATABASE_PERMISSIONS.md)" in readme
    legacy = (ROOT / "docs" / "MONITOR_PERMISSIONS.md").read_text(encoding="utf-8")
    assert "Database Permissions" in legacy and "DATABASE_PERMISSIONS.md" in legacy
    assert "separate matrix" in legacy


def test_cli_does_not_expose_a_runtime_permissions_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert not (ROOT / "dba" / "permissions.py").exists()
    with pytest.raises(SystemExit) as error:
        main(["permissions", "test-db", "--feature", "slow"])
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "invalid choice" in output
    assert "GRANT" not in output
