import json
import tomllib
from pathlib import Path

import pytest
import yaml

from dba.cli import main
from dba.core import DatabaseType
from dba.credentials import Credential, CredentialProvider
from dba.errors import ToolkitError
from dba.inventory import ValidationError
from dba.runtime import (
    ODBC_17,
    ODBC_18,
    RuntimeManifest,
    RuntimeStatus,
    doctor,
    runtime_info,
    select_sqlserver_driver,
    version_supported,
    wheelhouse_status,
)

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_manifest_parses_all_six_databases() -> None:
    manifest = RuntimeManifest.load(ROOT / "deployment/runtime-manifest.yaml")
    assert set(manifest.drivers) == {
        DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL, DatabaseType.MYSQL,
        DatabaseType.ORACLE, DatabaseType.MONGODB, DatabaseType.REDIS,
    }
    assert manifest.drivers[DatabaseType.ORACLE].mode == "thin"
    assert manifest.drivers[DatabaseType.ORACLE].native_drivers == ()
    assert manifest.drivers[DatabaseType.SQLSERVER].native_drivers == (ODBC_18, ODBC_17)
    assert manifest.credential_store.distribution == "keyring"
    assert manifest.credential_crypto["cryptography"].distribution == "cryptography"
    assert manifest.credential_crypto["argon2"].distribution == "argon2-cffi"


def test_runtime_manifest_rejects_unknown_or_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text(yaml.safe_dump({"runtime": {}, "drivers": {}, "secret": 'DBA-Test-Secret-987654'}),
                    encoding="utf-8")
    with pytest.raises(ValidationError):
        RuntimeManifest.load(path)


def test_version_constraints() -> None:
    assert version_supported("3.12.7", ">=3.11,<3.14")
    assert not version_supported("3.14.0", ">=3.11,<3.14")
    assert version_supported("5.2.0", ">=5.2,<6")


def test_sqlserver_native_driver_preference_and_fallback() -> None:
    assert select_sqlserver_driver([ODBC_17, ODBC_18]) == ODBC_18
    assert select_sqlserver_driver([ODBC_17]) == ODBC_17
    assert select_sqlserver_driver([]) is None


def _fake_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dba.runtime.importlib.util.find_spec", lambda name: object())
    versions = {"pyodbc": "5.2.0", "psycopg": "3.2.1", "PyMySQL": "1.1.1",
                "oracledb": "3.0.0", "pymongo": "4.10.1", "redis": "5.2.1",
                "keyring": "25.2.1", "cryptography": "44.0.0", "argon2-cffi": "23.1.0"}
    monkeypatch.setattr("dba.runtime.importlib.metadata.version", lambda name: versions[name])


def test_doctor_driver17_warning_and_oracle_thin(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_packages(monkeypatch)
    monkeypatch.setattr("dba.runtime.installed_odbc_drivers", lambda: (ODBC_17,))
    report = doctor(ROOT / "deployment/runtime-manifest.yaml", {DatabaseType.SQLSERVER})
    assert report["overall"] == RuntimeStatus.READY_WITH_WARNING.value
    assert report["components"]["sqlserver"]["selected_native_driver"] == ODBC_17
    assert report["components"]["oracle"]["mode"] == "thin"
    assert report["components"]["oracle"]["instant_client_required"] is False
    assert report["credential_store"]["package"]["version"] == "25.2.1"
    assert report["credential_store"]["crypto_packages"]["cryptography"]["version"] == "44.0.0"
    assert report["credential_store"]["crypto_packages"]["argon2"]["version"] == "23.1.0"
    assert report["network_accessed"] is False


def test_doctor_accepts_encrypted_provider_when_os_store_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_packages(monkeypatch)
    monkeypatch.setattr("dba.runtime.installed_odbc_drivers", lambda: (ODBC_18,))
    monkeypatch.setattr("dba.credentials.credential_store_status", lambda: {
        "backend": "Encrypted Local Store", "status": "READY", "stored_aliases": None,
        "providers": {
            "Windows Credential Manager": {"status": "UNAVAILABLE"},
            "Encrypted Local Store": {"status": "READY", "cipher": "AES-256-GCM",
                                      "kdf": "Argon2id", "format": 1},
            "Interactive Provider": {"status": "READY"},
        },
    })
    report = doctor(ROOT / "deployment/runtime-manifest.yaml", {DatabaseType.SQLSERVER})
    assert report["credential_store"]["status"] == "READY"
    assert report["credential_store"]["backend"] == "Encrypted Local Store"
    assert report["overall"] == "READY"


def test_doctor_ignores_uninstalled_optional_runtimes_but_requires_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_packages(monkeypatch)
    monkeypatch.setattr("dba.runtime.installed_odbc_drivers", lambda: (ODBC_18,))
    original = runtime_info

    def info_with_postgres_missing(path: Path = ROOT / "deployment/runtime-manifest.yaml") -> dict:
        result = original(path)
        result["drivers"]["postgresql"]["status"] = "MISSING"
        result["drivers"]["postgresql"]["version"] = None
        return result

    monkeypatch.setattr("dba.runtime.runtime_info", info_with_postgres_missing)
    monkeypatch.setattr("dba.credentials.credential_store_status", lambda: {
        "backend": "Fake", "status": "READY", "stored_aliases": 0,
    })
    optional = doctor(ROOT / "deployment/runtime-manifest.yaml", {DatabaseType.SQLSERVER})
    assert optional["components"]["postgresql"]["status"] == "NOT_INSTALLED"
    assert optional["overall"] == "READY"
    required = doctor(ROOT / "deployment/runtime-manifest.yaml", {DatabaseType.SQLSERVER,
                                                                     DatabaseType.POSTGRESQL})
    assert required["components"]["postgresql"]["status"] == "MISSING"
    assert required["overall"] == "MISSING"


def test_runtime_info_reports_machine_and_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_packages(monkeypatch)
    monkeypatch.setattr("dba.runtime.installed_odbc_drivers", lambda: (ODBC_18, ODBC_17))
    info = runtime_info(ROOT / "deployment/runtime-manifest.yaml")
    assert info["sqlserver_odbc"]["selected"] == ODBC_18
    assert info["drivers"]["postgresql"]["version"] == "3.2.1"
    assert info["oracle_mode"] == "thin"
    assert Path(info["python"]["executable"]).is_absolute()


class FailIfPrompted(CredentialProvider):
    called = False

    def get_credential(self, alias: str) -> Credential:
        self.called = True
        raise AssertionError("credential prompt must not run")


def test_missing_runtime_fails_before_credential_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "pg", "db_type": "postgresql", "host": "db.invalid",
        "port": 5432, "database": "postgres", "credential_alias": "monitor",
    }]}), encoding="utf-8")
    monkeypatch.setattr("dba.cli.database_runtime_status",
                        lambda kind: {"status": RuntimeStatus.MISSING.value})
    provider = FailIfPrompted()
    assert main(["--inventory", str(inventory), "test-connection", "pg", "--json"],
                provider=provider) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_type"] == "DriverUnavailable"
    assert not provider.called


def test_check_missing_runtime_fails_before_credential_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "pg", "db_type": "postgresql", "host": "db.invalid",
        "port": 5432, "database": "postgres", "credential_alias": "monitor",
    }]}), encoding="utf-8")
    monkeypatch.setattr("dba.cli.database_runtime_status",
                        lambda kind: {"status": RuntimeStatus.MISSING.value,
                                      "package": "psycopg"})
    provider = FailIfPrompted()
    assert main(["--inventory", str(inventory), "check", "pg"]) == 1
    output = capsys.readouterr().out
    assert "Database Runtime Missing" in output and "dba doctor" in output
    assert not provider.called


def test_check_missing_credential_is_actionable_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "pg", "db_type": "postgresql", "host": "db.invalid",
        "port": 5432, "database": "postgres", "credential_alias": "monitor",
    }]}), encoding="utf-8")
    monkeypatch.setattr("dba.cli.database_runtime_status",
                        lambda kind: {"status": RuntimeStatus.READY.value})

    class MissingProvider(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            raise ToolkitError("CredentialNotFound", "Credential alias was not found.")

    assert main(["--inventory", str(inventory), "check", "pg"],
                provider=MissingProvider(), driver=object()) == 1
    rendered = capsys.readouterr().out
    assert "Credential Not Found" in rendered
    assert "dba credentials set <credential_alias>" in rendered


def test_no_arguments_and_help_show_common_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "dba check <database_id>" in output and "dba diagnose <database_id> slow" in output
    assert main(["help"]) == 0
    assert "Common DBA Commands" in capsys.readouterr().out


def test_doctor_uses_inventory_and_runtime_info_does_not_load_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dba.runtime import RuntimeStatus

    monkeypatch.setattr("dba.cli.doctor", lambda configured_databases=None: {
        "overall": RuntimeStatus.READY.value,
        "python": {"status": "READY"}, "toolkit": "READY",
        "credential_store": {"status": "READY"},
        "components": {"sqlserver": {"status": "READY"}},
    })
    inventory = ROOT / "config" / "inventory.yaml"
    assert main(["--inventory", str(inventory), "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "READY"
    assert main(["--inventory", "missing.yaml", "doctor", "--database", "sqlserver",
                 "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "READY"
    monkeypatch.setattr("dba.cli.runtime_info", lambda: {"toolkit_version": "test"})
    assert main(["--inventory", "missing.yaml", "runtime-info", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["toolkit_version"] == "test"


def test_wheelhouse_resolver_is_local_only(tmp_path: Path) -> None:
    missing = wheelhouse_status(tmp_path / "missing")
    assert missing["status"] == "MISSING" and missing["network_fallback"] is False
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    (wheelhouse / "example-1.0-py3-none-any.whl").touch()
    ready = wheelhouse_status(wheelhouse)
    assert ready["status"] == "READY" and ready["wheel_count"] == 1


def test_bootstrap_scripts_are_cross_platform_and_non_installing() -> None:
    windows = (ROOT / "deployment/windows/bootstrap.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "deployment/linux/bootstrap.sh").read_text(encoding="utf-8")
    assert "Scripts\\python.exe" in windows
    assert "$venv/bin/python" in linux
    assert "--no-index" in windows and "--no-index" in linux
    combined = (windows + linux).lower()
    forbidden = ("invoke-webrequest", "curl ", "wget ", "& msiexec", "start-process", "apt install",
                 "dnf install", "yum install", "rpm -i", "dpkg -i")
    assert not any(command in combined for command in forbidden)
    assert "remove-item" not in windows.lower() and "rm -rf" not in linux.lower()


def test_dependency_extras_and_lock_files_are_present() -> None:
    project_path = ROOT / "pyproject.toml"
    project = project_path.read_text(encoding="utf-8")
    for extra in ("sqlserver", "postgresql", "mysql", "oracle", "mongodb", "redis", "all-db"):
        assert f"{extra} =" in project
    for name in ("base", "sqlserver", "postgresql", "mysql", "oracle", "mongodb", "redis"):
        assert (ROOT / "requirements" / f"{name}.lock").is_file()
    base_lock = (ROOT / "requirements/base.lock").read_text(encoding="utf-8")
    assert "cryptography>=" in base_lock and "argon2-cffi>=" in base_lock
    data = tomllib.loads(project)
    extras = data["project"]["optional-dependencies"]
    manifest = RuntimeManifest.load(ROOT / "deployment/runtime-manifest.yaml")
    expected = {
        "sqlserver": "pyodbc", "postgresql": "psycopg[binary]", "mysql": "PyMySQL[rsa]",
        "oracle": "oracledb", "mongodb": "pymongo", "redis": "redis",
    }
    for name, package in expected.items():
        requirement = manifest.drivers[DatabaseType(name)]
        assert extras[name] == [package + requirement.version]
