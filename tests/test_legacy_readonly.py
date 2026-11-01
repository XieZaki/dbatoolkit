from __future__ import annotations

from pathlib import Path

import pytest
from legacy_credential_fixture import write_legacy_store

from dba.credentials import Credential, PromptCredentialProvider
from dba.encrypted_credentials import CredentialResolver, EncryptedFileCredentialStore
from dba.errors import ToolkitError
from dba.sqlite_credentials import SQLiteCredentialStore, migrate_store

MASTER = "DBA-Test-Master-Only-123456789"
VALUE = Credential("fixture-monitor", "DBA-Test-Secret-123456789")


@pytest.mark.parametrize("operation", ["initialize", "set", "set_if_absent", "delete", "rekey",
                                       "persist_session_credentials", "_atomic_write", "_write_changed"])
def test_all_legacy_mutation_entries_deny_before_io(tmp_path: Path, operation: str) -> None:
    path = tmp_path / "legacy" / "credentials.enc"
    write_legacy_store(path, MASTER, {"fixture": VALUE})
    store = EncryptedFileCredentialStore(path, lambda _: MASTER)
    assert store.get("fixture") == VALUE
    original = path.read_bytes()
    entries = {
        "initialize": lambda: store.initialize(MASTER),
        "set": lambda: store.set("fixture", VALUE),
        "set_if_absent": lambda: store.set_if_absent("other", VALUE),
        "delete": lambda: store.delete("fixture"),
        "rekey": lambda: store.rekey(MASTER, MASTER + "-new"),
        "persist_session_credentials": lambda: store.persist_session_credentials({}, b"T" * 32, "0" * 64),
        "_atomic_write": lambda: store._atomic_write(b"must-not-write"),
        "_write_changed": lambda: store._write_changed(lambda _: pytest.fail("write callback ran")),
    }
    with pytest.raises(ToolkitError) as error:
        entries[operation]()
    assert error.value.error_type == "CredentialStoreReadOnly"
    assert "migrate" in str(error.value)
    assert path.read_bytes() == original
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
    assert store.get("fixture") == VALUE


def test_legacy_init_missing_path_cannot_create_anything(tmp_path: Path) -> None:
    path = tmp_path / "never-create" / "credentials.enc"
    with pytest.raises(ToolkitError) as error:
        EncryptedFileCredentialStore(path).initialize(MASTER)
    assert error.value.error_type == "CredentialStoreReadOnly"
    assert not path.parent.exists()


@pytest.mark.parametrize("verb", ["init", "set", "delete", "rekey"])
def test_cli_legacy_writes_explain_migration_before_prompts(tmp_path: Path, verb: str,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import dba.cli as cli
    path = tmp_path / "credentials.enc"
    write_legacy_store(path, MASTER, {"fixture": VALUE})
    original = path.read_bytes()
    monkeypatch.setattr(cli, "prompt_new_master_password", lambda: pytest.fail("legacy write prompted for password"))
    monkeypatch.setattr(cli, "prompt_credential_for_storage", lambda: pytest.fail("legacy write prompted for credentials"))
    assert cli.main(["credentials", verb, *(["fixture"] if verb in {"set", "delete"} else []),
                     "--backend", "encrypted", "--store-path", str(path), "--json"]) == 1
    output = capsys.readouterr().out
    assert "CredentialStoreReadOnly" in output and "migrate" in output
    assert VALUE.password not in output
    assert path.read_bytes() == original


def test_jit_legacy_cannot_write_or_implicitly_switch_store(tmp_path: Path) -> None:
    path = tmp_path / "old" / "credentials.enc"
    write_legacy_store(path, MASTER)
    original = path.read_bytes()
    source = EncryptedFileCredentialStore(path, lambda _: MASTER)
    resolver = CredentialResolver(None, source, PromptCredentialProvider(), mode="encrypted",
                                  credential_prompt=lambda *_: VALUE, save_prompt=lambda: True,
                                  ephemeral_prompt=lambda **_: pytest.fail("read-only Store must not fall back silently"))
    with pytest.raises(ToolkitError) as error:
        resolver.get_credential("fixture")
    assert error.value.error_type == "CredentialStoreReadOnly"
    assert path.read_bytes() == original
    assert not list(tmp_path.rglob("*.db"))
    with pytest.raises(ToolkitError):
        CredentialResolver(None, source, PromptCredentialProvider(), mode="sqlite")


def test_migration_and_new_writes_never_modify_legacy_or_merge_aliases(tmp_path: Path) -> None:
    path = tmp_path / "old" / "credentials.enc"
    write_legacy_store(path, MASTER, {"fixture": VALUE})
    source = EncryptedFileCredentialStore(path, lambda _: MASTER)
    original = path.read_bytes()
    target = SQLiteCredentialStore(tmp_path / "sqlite" / "credentials.db", lambda _: MASTER)
    assert migrate_store(source, target, MASTER) == 1
    assert target.get("fixture") == VALUE
    target.set("after-migration", VALUE)
    target.set("fixture", Credential("changed-in-sqlite", VALUE.password))
    assert source.get("fixture") == VALUE and source.get("after-migration") is None
    assert target.get("after-migration") == VALUE
    assert target.get("fixture").username == "changed-in-sqlite"
    assert path.read_bytes() == original


def test_legacy_cached_unlock_does_not_bypass_damage_or_replacement(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    write_legacy_store(path, MASTER, {"fixture": VALUE})
    store = EncryptedFileCredentialStore(path, lambda _: MASTER)
    assert store.get("fixture") == VALUE
    path.write_bytes(b"damaged fixture")
    for read in (lambda: store.get("fixture"), store.aliases):
        with pytest.raises(ToolkitError):
            read()


def test_sqlite_extra_is_enforced(tmp_path: Path) -> None:
    store = SQLiteCredentialStore(tmp_path / "sqlite" / "credentials.db", lambda _: MASTER)
    store.initialize(MASTER)
    with store._connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3
