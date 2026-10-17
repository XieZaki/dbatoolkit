from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_sqlserver import PASSWORD, ROOT, FakeDriver, MemoryCredentialProvider

from dba.cli import main
from dba.connection import resolve_connection_settings
from dba.core import DatabaseType
from dba.credentials import (
    Credential,
    CredentialStore,
    KeyringCredentialStore,
    StoredCredentialProvider,
    validate_alias,
)
from dba.errors import ToolkitError
from dba.inventory import Inventory, ValidationError
from dba.settings import MySQLSettings, OracleSettings, PostgreSQLSettings, SQLServerSettings


class FakeBackend:
    priority = 1


class FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get_keyring(self) -> FakeBackend:
        return FakeBackend()

    def get_password(self, service: str, alias: str) -> str | None:
        return self.data.get(f"{service}:{alias}")

    def set_password(self, service: str, alias: str, value: str) -> None:
        self.data[f"{service}:{alias}"] = value

    def delete_password(self, service: str, alias: str) -> None:
        del self.data[f"{service}:{alias}"]


class FakeStore(CredentialStore):
    def __init__(self, values: dict[str, Credential] | None = None) -> None:
        self.values = dict(values or {})

    @property
    def backend_name(self) -> str:
        return "Fake OS Store"

    def available(self) -> bool:
        return True

    def get(self, alias: str) -> Credential | None:
        return self.values.get(validate_alias(alias))

    def set(self, alias: str, credential: Credential) -> None:
        self.values[validate_alias(alias)] = credential

    def delete(self, alias: str) -> bool:
        return self.values.pop(validate_alias(alias), None) is not None

    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))


def test_instance_overrides_runtime_and_code_defaults() -> None:
    runtime = SQLServerSettings(encrypt=True, trust_server_certificate=False,
                                connect_timeout=9, query_timeout=20)
    result = resolve_connection_settings(
        DatabaseType.SQLSERVER,
        {"trust_server_certificate": True, "query_timeout": 12},
        runtime,
    )
    assert result == SQLServerSettings(encrypt=True, trust_server_certificate=True,
                                       connect_timeout=9, query_timeout=12)
    assert resolve_connection_settings(DatabaseType.SQLSERVER, {}, SQLServerSettings()) == SQLServerSettings()
    assert resolve_connection_settings(
        DatabaseType.SQLSERVER, {"encrypt": False}, SQLServerSettings()
    ).encrypt is False


@pytest.mark.parametrize(
    ("db_type", "connection", "defaults", "expected"),
    [
        (DatabaseType.POSTGRESQL, {"sslmode": "require"}, PostgreSQLSettings(), "require"),
        (DatabaseType.MYSQL, {"ssl_disabled": True, "ssl_verify_cert": False},
         MySQLSettings(), False),
        (DatabaseType.ORACLE, {"query_timeout": 15}, OracleSettings(), 15),
    ],
)
def test_database_specific_connection_resolution(
    db_type: DatabaseType, connection: dict[str, Any], defaults: Any, expected: Any
) -> None:
    resolved = resolve_connection_settings(db_type, connection, defaults)
    actual = (resolved.sslmode if db_type == DatabaseType.POSTGRESQL else
              resolved.ssl_enabled if db_type == DatabaseType.MYSQL else resolved.call_timeout)
    assert actual == expected


@pytest.mark.parametrize("connection", [
    {"xxx": "x"},
    {"password": 'DBA-Test-Secret-987654'},
    {"auth": {"password": 'DBA-Test-Secret-987654'}},
    {"username": "monitor"},
    {"user": "monitor"},
    {"token": 'DBA-Test-Secret-987654'},
    {"access_token": 'DBA-Test-Secret-987654'},
    {"private_key": "secret"},
    {"credential": "secret"},
])
def test_inventory_rejects_unknown_or_nested_secret_options(
    tmp_path: Path, connection: dict[str, Any]
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [{
        "id": "sql", "name": "SQL", "db_type": "sqlserver", "host": "localhost",
        "port": 1433, "credential_alias": "monitor", "connection": connection,
    }]}), encoding="utf-8")
    with pytest.raises(ValidationError):
        Inventory.load(path)


def test_inventory_connection_rejects_cross_database_fields(tmp_path: Path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [{
        "id": "pg", "name": "PG", "db_type": "postgresql", "host": "localhost",
        "port": 5432, "credential_alias": "monitor",
        "connection": {"trust_server_certificate": True},
    }]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="Unknown PostgreSQL connection option"):
        Inventory.load(path)


def test_legacy_inventory_defaults_connection_and_database_null(tmp_path: Path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(yaml.safe_dump({"databases": [{
        "id": "sql", "name": "SQL", "db_type": "sqlserver", "host": "localhost",
        "port": 1433, "credential_alias": "monitor", "database": None,
    }]}), encoding="utf-8")
    item = Inventory.load(path).get("sql")
    assert item.connection == {} and item.database is None
    assert resolve_connection_settings(DatabaseType.SQLSERVER, item.connection,
                                       SQLServerSettings()).encrypt is True


def test_keyring_store_lifecycle_is_opaque_and_alias_index_is_not_secret(
    tmp_path: Path,
) -> None:
    backend_type = type("WinVaultKeyring", (), {"priority": 1,
                                                  "__module__": "keyring.backends.Windows"})
    ring = FakeKeyring()
    ring.get_keyring = lambda: backend_type()  # type: ignore[method-assign]
    store = KeyringCredentialStore(tmp_path / "aliases.json", ring)
    credential = Credential("monitor", "  DBA test 密码!?  ")
    assert store.available()
    store.set("shared", credential)
    assert store.get("shared") == credential
    assert store.aliases() == ("shared",)
    assert "shared" in (tmp_path / "aliases.json").read_text(encoding="utf-8")
    assert credential.password not in (tmp_path / "aliases.json").read_text(encoding="utf-8")
    assert store.delete("shared") and store.get("shared") is None


def test_store_provider_prefers_store_and_falls_back_without_saving() -> None:
    stored = Credential("stored-user", PASSWORD)
    store = FakeStore({"shared": stored})

    class Fallback:
        def get_credential(self, alias: str) -> Credential:
            return Credential("temporary", "temporary-password")

    fallback = Fallback()
    provider = StoredCredentialProvider(store, fallback)
    assert provider.get_credential("shared") is stored
    assert provider.last_backend == "Fake OS Store"
    assert provider.get_credential("other") == fallback.get_credential("other")
    assert "other" not in store.values


def test_store_provider_falls_back_when_secure_backend_read_fails() -> None:
    class BrokenStore(FakeStore):
        def get(self, alias: str) -> Credential | None:
            raise ToolkitError("CredentialStoreFailed", "Credential store could not be read.")

    fallback_credential = Credential("temporary", "temporary-only")

    class Fallback:
        def get_credential(self, alias: str) -> Credential:
            return fallback_credential

    provider = StoredCredentialProvider(BrokenStore(), Fallback())
    assert provider.get_credential("shared") == fallback_credential
    assert provider.last_backend == "Interactive Provider"


def test_store_failure_reports_backend_block_not_missing_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStore(FakeStore):
        def get(self, alias: str) -> Credential | None:
            raise ToolkitError("CredentialStoreFailed", "Credential store could not be read.")

    class NoTerminal:
        def get_credential(self, alias: str) -> Credential:
            raise ToolkitError("CredentialUnavailable", "Interactive terminal required.")

    monkeypatch.setattr("dba.credentials.sys.stderr.isatty", lambda: False)
    with pytest.raises(ToolkitError, match="Secure credential store is unavailable"):
        StoredCredentialProvider(BrokenStore(), NoTerminal()).get_credential("shared")


@pytest.mark.parametrize("code", [5, 1168])
def test_windows_keyring_probe_detects_access_and_missing_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int,
) -> None:
    class WinError(Exception):
        pass

    winvault_type = type(
        "WinVaultKeyring", (), {"priority": 1, "__module__": "keyring.backends.Windows"}
    )

    class WindowsKeyring(FakeKeyring):
        def get_keyring(self) -> Any:
            return winvault_type()

        def get_password(self, service: str, alias: str) -> str | None:
            raise WinError(code, "credential read failed")

    monkeypatch.setattr("dba.credentials.os.name", "nt")
    store = KeyringCredentialStore(tmp_path / "aliases.json", WindowsKeyring())
    assert store.available() is (code == 1168)
    assert store.get("missing") is None


@pytest.mark.parametrize("accessible", [True, False])
def test_linux_keyring_probe_requires_live_backend_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accessible: bool,
) -> None:
    class LinuxKeyring(FakeKeyring):
        def get_keyring(self) -> Any:
            backend_type = type(
                "SecretServiceKeyring", (), {"priority": 1,
                                              "__module__": "keyring.backends.secretservice"}
            )
            return backend_type()

        def get_password(self, service: str, alias: str) -> str | None:
            if not accessible:
                raise RuntimeError("no session bus")
            return None

    monkeypatch.setattr("dba.credentials.os.name", "posix")
    store = KeyringCredentialStore(tmp_path / "aliases.json", LinuxKeyring())
    assert store.available() is accessible


def test_credentials_cli_never_prints_password(capsys: pytest.CaptureFixture[str]) -> None:
    store = FakeStore({"shared": Credential("monitor", PASSWORD)})
    assert main(["credentials", "list"], credential_store=store) == 0
    listing = capsys.readouterr().out
    assert "shared" in listing and PASSWORD not in listing
    assert main(["credentials", "show", "shared", "--json"], credential_store=store) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "STORED" and "password" not in shown
    assert shown["username"] == "monitor" and PASSWORD not in json.dumps(shown)
    assert main(["credentials", "test", "shared", "--json"], credential_store=store) == 0
    tested = capsys.readouterr().out
    assert '"status": "READY"' in tested and PASSWORD not in tested


def test_credentials_cli_update_and_delete_require_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = FakeStore({"shared": Credential("monitor", "old-value")})
    monkeypatch.setattr("dba.cli.confirm_action", lambda prompt: True)
    monkeypatch.setattr(
        "dba.cli.prompt_credential_for_storage",
        lambda: Credential("monitor2", "new-value"),
    )
    assert main(["credentials", "set", "shared", "--json"], credential_store=store) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["updated"] and store.get("shared") == Credential("monitor2", "new-value")
    assert "new-value" not in json.dumps(updated)
    assert main(["credentials", "delete", "shared", "--json"], credential_store=store) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted"] and store.get("shared") is None


def test_cli_show_and_test_connection_expose_only_effective_safe_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump({"databases": [{
        "id": "sqlserver2022", "name": "SQL", "db_type": "sqlserver",
        "host": "127.0.0.1", "port": 1433, "database": None,
        "credential_alias": "monitor",
        "connection": {"encrypt": False, "trust_server_certificate": True},
    }]}), encoding="utf-8")
    assert main(["--inventory", str(inventory_path), "show", "sqlserver2022", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["effective_connection"]["encrypt"] is False
    assert shown["effective_connection"]["trust_server_certificate"] is True
    assert PASSWORD not in json.dumps(shown)

    driver = FakeDriver()
    assert main(["--inventory", str(inventory_path), "--sql-root", str(ROOT / "sql"),
                 "test-connection", "sqlserver2022", "--json"],
                provider=MemoryCredentialProvider(), driver=driver) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["connection"]["database"] == "master"
    assert result["connection"]["encrypt"] is False
    assert result["connection"]["trust_server_certificate"] is True
    assert "password" not in result and PASSWORD not in json.dumps(result)

    runtime_path = tmp_path / "runtime.yaml"
    runtime_path.write_text(yaml.safe_dump({"sqlserver": {
        "encrypt": True, "trust_server_certificate": False,
        "connect_timeout": 9, "query_timeout": 20,
    }}), encoding="utf-8")
    assert main(["--inventory", str(inventory_path), "--settings", str(runtime_path),
                 "show", "sqlserver2022", "--json"]) == 0
    merged = json.loads(capsys.readouterr().out)["effective_connection"]
    assert merged["encrypt"] is False  # instance override wins
    assert merged["trust_server_certificate"] is True  # instance override wins
    assert merged["connect_timeout"] == 9  # omitted instance field inherits runtime
    assert merged["query_timeout"] == 20


def test_cli_uses_stored_credential_alias_without_interactive_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(yaml.safe_dump({"databases": [{
        "id": "sql", "name": "SQL", "db_type": "sqlserver", "host": "127.0.0.1",
        "port": 1433, "credential_alias": "shared",
    }]}), encoding="utf-8")
    driver = FakeDriver()
    store = FakeStore({"shared": Credential("monitor", PASSWORD)})
    assert main(["--inventory", str(inventory_path), "--sql-root", str(ROOT / "sql"),
                 "test-connection", "sql", "--json"], driver=driver, credential_store=store) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["credential"] == {"alias": "shared", "backend": "Fake OS Store"}
    assert PASSWORD not in json.dumps(result)


def test_cli_has_no_password_argument(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["test-connection", "anything", "--password", "secret"])
    assert exc.value.code == 2
    assert "--password" in capsys.readouterr().err
