import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_sqlserver import ROOT, FakeDriver, MemoryCredentialProvider

from dba.cli import main
from dba.inventory import Inventory, ValidationError


def write_inventory(path: Path, databases: list[dict[str, Any]],
                    defaults: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"databases": databases}
    if defaults is not None:
        data["defaults"] = defaults
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def item(database_id: str, **overrides: Any) -> dict[str, Any]:
    return {"id": database_id, "name": database_id, "host": "test.invalid", **overrides}


DEFAULTS = {"db_type": "sqlserver", "port": 1433, "credential_alias": "monitor",
            "environment": "production", "enabled": True}


def test_recursive_load_defaults_override_and_source(tmp_path: Path) -> None:
    directory = tmp_path / "inventory"
    sql = write_inventory(directory / "sqlserver/production.yaml", [
        item("mes", group="manufacturing", tags=[" core ", "mes", "core"]),
        item("test", environment="development", port=11433, enabled=False),
    ], DEFAULTS)
    pg = write_inventory(directory / "postgresql/region/east.yml", [
        item("erp", tags=["finance"]),
    ], {"db_type": "postgresql", "port": 5432, "credential_alias": "pg-monitor"})
    inventory = Inventory.load(directory)
    assert [instance.id for instance in inventory.instances] == ["erp", "mes", "test"]
    assert inventory.get("mes").tags == ["core", "mes"]
    assert inventory.get("mes").group == "manufacturing"
    assert inventory.get("mes").environment == "production"
    assert inventory.get("test").port == 11433 and inventory.get("test").enabled is False
    assert inventory.get("erp").credential_alias == "pg-monitor"
    assert inventory.get("erp").environment is None
    assert inventory.source_file("mes") == sql and inventory.source_file("erp") == pg
    assert [i.id for i in inventory.filter("sqlserver", "production", "manufacturing", "core")] == ["mes"]
    assert not inventory.filter("sqlserver", "development", "manufacturing", "core")


def test_defaults_do_not_cross_files(tmp_path: Path) -> None:
    directory = tmp_path / "inventory"
    write_inventory(directory / "a.yaml", [item("one")], DEFAULTS)
    second = write_inventory(directory / "nested/b.yaml", [item("two")])
    with pytest.raises(ValidationError) as exc:
        Inventory.load(directory)
    assert str(second) in str(exc.value) and "Missing required" in str(exc.value)


def test_default_tags_are_normalized_per_entry(tmp_path: Path) -> None:
    path = write_inventory(tmp_path / "inventory.yaml", [item("one"), item("two")],
                           {**DEFAULTS, "tags": [" core ", "core"]})
    inventory = Inventory.load(path)
    assert inventory.get("one").tags == ["core"]
    assert inventory.get("one").tags is not inventory.get("two").tags


@pytest.mark.regression
def test_duplicate_id_includes_both_sources_and_cli_validate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "inventory"
    first = write_inventory(directory / "a.yaml", [item("same")], DEFAULTS)
    second = write_inventory(directory / "nested/b.yaml", [item("same")], DEFAULTS)
    with pytest.raises(ValidationError) as exc:
        Inventory.load(directory)
    message = str(exc.value)
    assert "Duplicate database ID same" in message and str(first) in message and str(second) in message
    assert main(["--inventory", str(directory), "validate", "--json"]) == 1
    output = json.loads(capsys.readouterr().out)["error_message"]
    assert str(first) in output and str(second) in output


@pytest.mark.parametrize("location,invalid", [
    ("defaults", {"unknown": "x"}),
    ("defaults", {"credential_alias": ""}),
    ("defaults", {"enabled": "true"}),
    ("defaults", {"db_type": "unknown"}),
    ("defaults", {"password": 'DBA-Test-Secret-987654'}),
    ("entry", {"unknown": "x"}),
    ("entry", {"group": 12}),
    ("entry", {"tags": "core"}),
    ("entry", {"tags": ["core", 7]}),
    ("entry", {"tags": ["password=hidden"]}),
])
def test_invalid_field_includes_source(tmp_path: Path, location: str,
                                       invalid: dict[str, Any]) -> None:
    path = tmp_path / "inventory.yaml"
    defaults = {**DEFAULTS, **invalid} if location == "defaults" else DEFAULTS
    entry = item("one", **invalid) if location == "entry" else item("one")
    write_inventory(path, [entry], defaults)
    with pytest.raises(ValidationError) as exc:
        Inventory.load(path)
    assert str(path) in str(exc.value)
    assert "hidden" not in str(exc.value)


def test_malformed_yaml_includes_source(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("databases: [password: hidden", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        Inventory.load(path)
    assert str(path) in str(exc.value) and "hidden" not in str(exc.value)


def test_legacy_file_and_populated_directory_priority(tmp_path: Path) -> None:
    legacy = write_inventory(tmp_path / "inventory.yaml", [item("legacy")], DEFAULTS)
    assert Inventory.load(legacy).get("legacy").id == "legacy"
    directory = tmp_path / "inventory"
    directory.mkdir()
    assert Inventory.load(legacy).get("legacy").id == "legacy"
    current = write_inventory(directory / "sqlserver/production.yaml", [item("current")], DEFAULTS)
    inventory = Inventory.load(legacy)
    assert [i.id for i in inventory.instances] == ["current"]
    assert inventory.source_file("current") == current
    with pytest.raises(ValidationError):
        inventory.get("legacy")


def test_cli_group_tag_and_combined_filters(tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    directory = tmp_path / "inventory"
    write_inventory(directory / "sqlserver.yaml", [
        item("mes", group="manufacturing", tags=["core", "mes"]),
        item("reporting", group="analytics", tags=["core"]),
        item("dev", environment="development", group="manufacturing", tags=["core"]),
    ], DEFAULTS)
    write_inventory(directory / "postgresql.yaml", [item("pg", group="manufacturing", tags=["core"])],
                    {"db_type": "postgresql", "port": 5432, "credential_alias": "pg-monitor"})

    def listed(*options: str) -> list[dict[str, Any]]:
        assert main(["--inventory", str(directory), "list", *options, "--json"]) == 0
        return json.loads(capsys.readouterr().out)["databases"]

    assert {row["id"] for row in listed("--group", "manufacturing")} == {"mes", "dev", "pg"}
    assert {row["id"] for row in listed("--tag", "mes")} == {"mes"}
    rows = listed("--type", "sqlserver", "--env", "production", "--group", "manufacturing",
                  "--tag", "core")
    assert [row["id"] for row in rows] == ["mes"]
    assert rows[0]["tags"] == ["core", "mes"] and "source_file" not in rows[0]
    assert main(["--inventory", str(directory), "show", "mes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "mes"


def test_multifile_inventory_preserves_slow_diagnose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "inventory"
    write_inventory(directory / "sqlserver/production.yaml", [item("test")], DEFAULTS)
    driver = FakeDriver()
    driver.connection.results.update(transactions=[], connections=[])
    report = tmp_path / "slow.html"
    assert main([
        "--inventory", str(directory), "--sql-root", str(ROOT / "sql"),
        "diagnose", "test", "slow", "--format", "json", "--report", str(report),
    ], provider=MemoryCredentialProvider(), driver=driver) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    assert report.exists() and driver.connection.closed
