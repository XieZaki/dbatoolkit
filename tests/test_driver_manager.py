from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dba import __version__
from dba.cli import main as cli_main
from dba.core import DatabaseType
from dba.driver_manager import DriverManager, _normalized_arch, _python_abi, load_database_driver
from dba.driver_versions import DriverModuleVersionError, validate_driver_module_version
from dba.errors import ToolkitError


def _manifest(database: str, version: str, module: str) -> dict[str, object]:
    distribution = {
        "mysql": "PyMySQL",
        "postgresql": "psycopg",
        "oracle": "oracledb",
        "sqlserver": "pyodbc",
    }[database]
    return {
        "schema_version": 1,
        "database": database,
        "driver_name": distribution,
        "driver_version": version,
        "module": module,
        "distribution": distribution,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_abi": _python_abi(),
        "windows_arch": _normalized_arch(),
        "supported_database_versions": ["test-only"],
        "minimum_toolkit_version": __version__,
        "required_dependencies": [],
        "native_components": [],
    }


def _write_driver_zip(path: Path, *, database: str = "mysql", version: str = "1.0.0",
                      module: str = "pymysql", source: str = "VALUE = 'loaded'\n",
                      manifest_override: dict[str, object] | None = None,
                      extra: dict[str, bytes] | None = None) -> Path:
    manifest = manifest_override or _manifest(database, version, module)
    dist = str(manifest["distribution"]).replace("-", "_")
    driver_version = str(manifest["driver_version"])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("driver.json", json.dumps(manifest))
        if database == "mysql" and module == "pymysql":
            version_parts = tuple(int(part) for part in version.split(".")[:3])
            module_source = (
                f"__version__ = {version!r}\n"
                f"VERSION_STRING = {version!r}\n"
                f"VERSION = {(*version_parts, 'final')!r}\n"
                + source
            )
        else:
            module_source = f"__version__ = {version!r}\n" + source
        archive.writestr(f"site-packages/{module.replace('.', '/')}.py", module_source)
        archive.writestr(
            f"site-packages/{dist}-{driver_version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {manifest['distribution']}\nVersion: {driver_version}\n\n",
        )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def test_pymysql_dual_version_identity_uses_release_metadata() -> None:
    module = SimpleNamespace(
        __version__="2.2.8",
        VERSION_STRING="1.1.3",
        VERSION=(1, 1, 3, "final"),
        version_info=(2, 2, 8, "final", 1),
    )

    evidence = validate_driver_module_version(
        module,
        module_name="pymysql",
        distribution_name="PyMySQL",
        distribution_version="1.1.3",
    )

    assert evidence.release_version == "1.1.3"
    assert evidence.reported_module_version == "2.2.8"
    assert evidence.reported_version_role == "mysqlclient compatibility"


def test_pymysql_dual_version_identity_rejects_release_or_compatibility_conflict() -> None:
    module = SimpleNamespace(
        __version__="2.2.8",
        VERSION_STRING="1.1.3",
        VERSION=(1, 1, 4, "final"),
        version_info=(2, 2, 8, "final", 1),
    )
    with pytest.raises(DriverModuleVersionError):
        validate_driver_module_version(
            module,
            module_name="pymysql",
            distribution_name="PyMySQL",
            distribution_version="1.1.3",
        )

    module.VERSION = (1, 1, 3, "final")
    module.version_info = (2, 2, 9, "final", 1)
    with pytest.raises(DriverModuleVersionError):
        validate_driver_module_version(
            module,
            module_name="pymysql",
            distribution_name="PyMySQL",
            distribution_version="1.1.3",
        )


def test_regular_driver_module_version_must_match_distribution() -> None:
    module = SimpleNamespace(__version__="3.3.6")
    evidence = validate_driver_module_version(
        module,
        module_name="psycopg",
        distribution_name="psycopg",
        distribution_version="3.3.6",
    )
    assert evidence.release_version == "3.3.6"
    with pytest.raises(DriverModuleVersionError):
        validate_driver_module_version(
            module,
            module_name="psycopg",
            distribution_name="psycopg",
            distribution_version="3.3.7",
        )


@pytest.mark.skipif(os.name != "nt", reason="portable driver packages target Windows")
def test_install_activate_load_and_rollback_isolated_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBA_TOOLKIT_HOME", str(tmp_path / "portable"))
    manager = DriverManager.from_environment()
    first_zip = _write_driver_zip(tmp_path / "mysql-1.zip", version="1.1.3")
    result = manager.install(first_zip)
    assert result["status"] == "INSTALLED"
    assert next(item for item in manager.list()["drivers"] if item["database"] == "mysql")["managed"]

    active_before = manager.active_package(DatabaseType.MYSQL)
    assert active_before is not None
    previous_module = sys.modules.pop("pymysql", None)
    managed_path = str(active_before[0])
    try:
        module = load_database_driver(DatabaseType.MYSQL, "pymysql")
        assert module.VALUE == "loaded"
    finally:
        sys.modules.pop("pymysql", None)
        if previous_module is not None:
            sys.modules["pymysql"] = previous_module
        if managed_path in sys.path:
            sys.path.remove(managed_path)
    assert active_before[0].is_relative_to(tmp_path / "portable" / "drivers" / "mysql")

    second_zip = _write_driver_zip(tmp_path / "mysql-2.zip", version="1.1.4",
                                   source="VALUE = 'new'\n")
    upgraded = manager.install(second_zip)
    assert upgraded["rollback_available"] is True
    assert manager.rollback(DatabaseType.MYSQL)["status"] == "ROLLED_BACK"
    active_after = manager.active_package(DatabaseType.MYSQL)
    assert active_after is not None
    assert active_after[1].driver_version == "1.1.3"


@pytest.mark.skipif(os.name != "nt", reason="portable driver packages target Windows")
def test_failed_zip_keeps_active_driver_and_rejects_traversal(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBA_TOOLKIT_HOME", str(tmp_path / "portable"))
    manager = DriverManager.from_environment()
    manager.install(_write_driver_zip(tmp_path / "valid.zip", version="1.1.3"))
    before = manager.active_package(DatabaseType.MYSQL)
    assert before is not None

    unsafe = _write_driver_zip(tmp_path / "unsafe.zip", version="1.1.4",
                               extra={"site-packages/../../outside.txt": b"no"})
    with pytest.raises(ToolkitError, match="unsafe path"):
        manager.install(unsafe)

    after = manager.active_package(DatabaseType.MYSQL)
    assert after is not None
    assert after[1].driver_version == before[1].driver_version
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="portable driver packages target Windows")
def test_rejects_runtime_abi_mismatch_without_changing_active_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DBA_TOOLKIT_HOME", str(tmp_path / "portable"))
    manager = DriverManager.from_environment()
    manager.install(_write_driver_zip(tmp_path / "valid.zip", version="1.1.3"))
    wrong = _manifest("mysql", "1.1.4", "pymysql")
    wrong["python_abi"] = "cp000"
    with pytest.raises(ToolkitError, match="ABI"):
        manager.install(_write_driver_zip(tmp_path / "wrong-abi.zip", version="1.1.4",
                                          manifest_override=wrong))
    active = manager.active_package(DatabaseType.MYSQL)
    assert active is not None and active[1].driver_version == "1.1.3"


@pytest.mark.skipif(os.name != "nt", reason="portable driver packages target Windows")
def test_rejects_driver_identity_outside_database_contract(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBA_TOOLKIT_HOME", str(tmp_path / "portable"))
    manager = DriverManager.from_environment()
    with pytest.raises(ToolkitError, match="identity"):
        manager.install(_write_driver_zip(tmp_path / "wrong-module.zip", version="1.1.3",
                                          module="unrelated_module"))
    assert not (tmp_path / "portable" / "drivers" / "mysql" / "state.json").exists()


def test_driver_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "driver.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    from dba.driver_manager import _read_json

    with pytest.raises(ToolkitError):
        _read_json(path, 1000)


def test_manager_does_not_enable_portable_writes_without_home(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
    monkeypatch.delenv("DBA_TOOLKIT_HOME", raising=False)
    manager = DriverManager.from_environment()
    assert manager.root is None
    assert manager.list()["network_accessed"] is False
    with pytest.raises(ToolkitError, match="portable runtime home"):
        manager.install(tmp_path / "missing.zip")


def test_normalized_architecture_accounts_for_python_process_bitness() -> None:
    import struct

    expected = "x86_64" if struct.calcsize("P") == 8 else "x86"
    assert _normalized_arch("AMD64") == expected


@pytest.mark.parametrize(
    ("arguments", "method"),
    [
        (["driver", "list", "--json"], "list"),
        (["driver", "check", "--database", "mysql", "--json"], "check"),
        (["driver", "install", "--file", "unused.zip", "--json"], "install"),
        (["driver", "rollback", "--database", "mysql", "--json"], "rollback"),
    ],
)
def test_driver_cli_does_not_access_credentials(
    arguments: list[str], method: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class FakeDriverManager:
        @classmethod
        def from_environment(cls) -> FakeDriverManager:
            return cls()

        def list(self) -> dict[str, object]:
            calls.append("list")
            return {"drivers": [], "network_accessed": False}

        def check(self, _database: DatabaseType | None = None) -> dict[str, object]:
            calls.append("check")
            return {"checks": [{"status": "READY"}], "network_accessed": False}

        def install(self, _path: Path) -> dict[str, object]:
            calls.append("install")
            return {"status": "INSTALLED"}

        def rollback(self, _database: DatabaseType) -> dict[str, object]:
            calls.append("rollback")
            return {"status": "ROLLED_BACK"}

    class ExplodingCredentialStore:
        def __getattribute__(self, _name: str) -> object:
            raise AssertionError("driver command accessed the Credential Store")

    monkeypatch.setattr("dba.driver_manager.DriverManager", FakeDriverManager)
    assert cli_main(arguments, credential_store=ExplodingCredentialStore()) == 0  # type: ignore[arg-type]
    assert calls == [method]
    output = json.loads(capsys.readouterr().out)
    assert isinstance(output, dict)
    if method == "list":
        assert output == {"drivers": [], "network_accessed": False}
    elif method == "check":
        assert output["checks"] == [{"status": "READY"}]
    else:
        assert output["status"] == {"install": "INSTALLED", "rollback": "ROLLED_BACK"}[method]
