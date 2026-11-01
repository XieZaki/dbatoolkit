from __future__ import annotations

import pytest

from scripts.acceptance import inspect_sqlserver_memory_grant as helper


def test_metadata_checks_are_fixed_selects_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert helper.main([]) == 0
    output = capsys.readouterr().out
    statements = [part.strip() for part in output.split(";") if part.strip()]
    statements[0] = "\n".join(line for line in statements[0].splitlines()
                               if not line.lstrip().startswith("--")).strip()
    assert len(statements) == 5
    assert all(part.upper().startswith("SELECT ") for part in statements)
    assert "AdventureWorks2022" in output
    assert "SalesOrderDetail" in output
    assert "key_ordinal" in output and "is_included_column" in output
    assert "approximate_rows" in output
    assert "password" not in output.casefold()
    assert "credential" not in output.casefold()


def test_metadata_helper_rejects_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert helper.main(["unexpected"]) == 2
    assert capsys.readouterr().out == ""
