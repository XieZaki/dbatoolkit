from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from legacy_credential_fixture import write_legacy_store

from dba.credential_session import (
    MIN_TTL_SECONDS,
    PROTOCOL_VERSION,
    CredentialSessionClient,
    _validate_request,
)
from dba.credentials import Credential
from dba.encrypted_credentials import EncryptedFileCredentialStore
from dba.errors import ToolkitError
from dba.sqlite_credentials import SQLiteCredentialStore, SQLiteSessionStore, migrate_store

MASTER = "DBA-Test-Master-Only-123456789"
VALUE = Credential("fixture-monitor", "DBA-Test-Secret-Only-123456789")


def new_store(tmp_path: Path, name: str = "vault") -> SQLiteCredentialStore:
    store = SQLiteCredentialStore(tmp_path / name / "credentials.db", lambda _label: MASTER)
    store.initialize(MASTER)
    return store


def test_provider_roundtrip_rekey_and_authenticated_errors(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.set("fixture", VALUE)
    assert store.get("fixture") == VALUE
    assert store.aliases() == ("fixture",)
    assert VALUE.password.encode() not in store.path.read_bytes()
    assert VALUE.username.encode() not in store.path.read_bytes()
    store.lock()
    wrong = SQLiteCredentialStore(store.path, lambda _label: "incorrect fixture password")
    with pytest.raises(ToolkitError) as error:
        wrong.get("fixture")
    assert error.value.error_type == "CredentialStoreUnlockFailed"
    store.rekey(MASTER, MASTER + "-changed")
    verify = SQLiteCredentialStore(store.path, lambda _label: MASTER + "-changed")
    assert verify.get("fixture") == VALUE
    assert verify.delete("fixture")
    assert verify.aliases() == ()


def test_payload_corruption_rejects_writes_and_reads(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.set("fixture", VALUE)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE credential_store SET payload=zeroblob(length(payload))")
    connection.close()
    raw = store.path.read_bytes()
    for action in (lambda: store.get("fixture"), lambda: store.set("other", VALUE)):
        with pytest.raises(ToolkitError) as error:
            action()
        assert error.value.error_type == "CredentialStoreCorrupted"
    assert store.path.read_bytes() == raw


def test_cross_store_session_and_legal_commit(tmp_path: Path, isolated_credential_ipc: str) -> None:
    first, second = new_store(tmp_path, "first"), new_store(tmp_path, "second")
    first.set("fixture", VALUE)
    _, values = first._read_container()
    client = CredentialSessionClient()
    client.load(values, MIN_TTL_SECONDS, first.path, first.session_key(), "sqlite", first.binding)
    assert client.get("fixture", first.path) == VALUE
    before = second.path.read_bytes()
    for operation in (lambda: client.get("fixture", second.path),
                      lambda: client.save("other", VALUE, second.path),
                      lambda: client.list_metadata(second.path),
                      lambda: client.delete("fixture", second.path),
                      lambda: SQLiteSessionStore(second.path, client).set("other", VALUE)):
        with pytest.raises(ToolkitError) as mismatch:
            operation()
        assert mismatch.value.error_type == "CredentialStoreChanged"
    assert second.path.read_bytes() == before
    from dba.credentials import PromptCredentialProvider
    from dba.encrypted_credentials import CredentialResolver
    for database_id in ("fixture-sqlserver", "fixture-postgresql", "fixture-mysql", "fixture-oracle"):
        resolver = CredentialResolver(None, first, PromptCredentialProvider(), mode="sqlite", database_id=database_id)
        assert resolver.get_credential("fixture") == VALUE
    assert client.save("second-fixture", VALUE, first.path)["status"] == "SAVED"
    assert client.get("second-fixture", first.path) == VALUE
    assert client.status()["status"] == "UNLOCKED"
    client.delete("second-fixture", first.path)
    assert client.get("second-fixture", first.path) is None
    with pytest.raises(ToolkitError):
        _validate_request({"protocol_version": PROTOCOL_VERSION - 1, "operation": "STATUS"})


def test_same_alias_in_distinct_stores_never_cross_reads(tmp_path: Path,
                                                        isolated_credential_ipc: str) -> None:
    first = new_store(tmp_path, "same-alias-first")
    second = new_store(tmp_path, "same-alias-second")
    first_value = Credential("first-user", "DBA-Test-First-Only-123456")
    second_value = Credential("second-user", "DBA-Test-Second-Only-654321")
    first.set("shared-alias", first_value)
    second.set("shared-alias", second_value)

    _, first_values = first._read_container()
    client = CredentialSessionClient()
    client.load(first_values, MIN_TTL_SECONDS, first.path, first.session_key(),
                store_backend="sqlite", store_binding=first.binding)
    assert client.get("shared-alias", first.path) == first_value
    with pytest.raises(ToolkitError) as mismatch:
        client.get("shared-alias", second.path)
    assert mismatch.value.error_type == "CredentialStoreChanged"
    assert SQLiteCredentialStore(second.path, lambda _label: MASTER).get("shared-alias") == second_value


@pytest.mark.parametrize(("source_kind", "expected_error"), [
    ("wrong-password", "CredentialStoreUnlockFailed"),
    ("damaged", "CredentialStoreCorrupted"),
])
def test_migration_rejects_wrong_password_and_damaged_source(
    tmp_path: Path, source_kind: str, expected_error: str,
) -> None:
    path = tmp_path / source_kind / "credentials.enc"
    write_legacy_store(path, MASTER, {"fixture": VALUE})
    if source_kind == "damaged":
        path.write_bytes(b"damaged temporary legacy fixture")
    source_bytes = path.read_bytes()
    provider = EncryptedFileCredentialStore(
        path, lambda _label: "DBA-Test-Wrong-Master-Only-987654321"
        if source_kind == "wrong-password" else MASTER,
    )
    target_path = tmp_path / f"{source_kind}-target" / "credentials.db"

    with pytest.raises(ToolkitError) as error:
        migrate_store(provider, SQLiteCredentialStore(target_path), MASTER)

    assert error.value.error_type == expected_error
    assert path.read_bytes() == source_bytes
    assert not target_path.exists()


def test_cli_migration_requires_explicit_provider_selection(
    tmp_path: Path, isolated_credential_ipc: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import dba.cli as cli
    import dba.encrypted_credentials as encrypted

    source_path = tmp_path / "legacy-source" / "credentials.enc"
    write_legacy_store(source_path, MASTER, {"fixture": VALUE})
    source_bytes = source_path.read_bytes()
    destination = tmp_path / "migrated" / "credentials.db"
    monkeypatch.setattr(encrypted, "prompt_master_password", lambda _label="": MASTER)
    monkeypatch.setattr(cli, "prompt_new_master_password", lambda: MASTER)

    assert cli.main(["credentials", "migrate", "--source", str(source_path),
                     "--destination", str(destination), "--json"]) == 0
    migration_result = json.loads(capsys.readouterr().out)
    assert migration_result["status"] == "VERIFIED_COPY"
    assert migration_result["provider_switched"] is False
    assert CredentialSessionClient().status()["status"] == "LOCKED"
    assert source_path.read_bytes() == source_bytes

    monkeypatch.setattr(cli, "prompt_master_password", lambda _label="": MASTER)
    assert cli.main(["credentials", "unlock", "--backend", "sqlite", "--store-path",
                     str(destination), "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["credentials", "list", "--backend", "sqlite", "--store-path",
                     str(destination), "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["alias"] for row in listed["credentials"]] == ["fixture"]
    assert source_path.read_bytes() == source_bytes

    monkeypatch.setattr(cli, "prompt_credential_for_storage",
                        lambda: Credential("new-fixture-user", "DBA-Test-Post-Migration-Only-98765"))
    assert cli.main(["credentials", "set", "post-migration", "--backend", "sqlite",
                     "--store-path", str(destination), "--json"]) == 0
    capsys.readouterr()
    destination_bytes = destination.read_bytes()
    with pytest.raises(ToolkitError) as refused_overwrite:
        migrate_store(EncryptedFileCredentialStore(source_path, lambda _label: MASTER),
                     SQLiteCredentialStore(destination), MASTER)
    assert refused_overwrite.value.error_type == "CredentialStoreExists"
    assert destination.read_bytes() == destination_bytes
    assert source_path.read_bytes() == source_bytes


def test_writable_encrypted_init_requires_explicit_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import dba.cli as cli

    path = tmp_path / "auto-selected" / "credentials.enc"
    monkeypatch.setattr(cli, "prompt_new_master_password", lambda: MASTER)
    with pytest.raises(SystemExit) as exit_result:
        cli.main(["credentials", "init", "--store-path", str(path), "--json"])
    assert exit_result.value.code == 2
    assert "--backend" in capsys.readouterr().err
    assert not path.exists()
    assert not list(tmp_path.rglob("*.db"))


def test_sqlite_private_acl_covers_database_directory_and_journal(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("SQLite credential ACL validation is Windows-specific")
    from dba.credential_session import _windows_identity
    from dba.sqlite_credentials import _windows_acl

    def acl_evidence(path: Path) -> dict[str, object]:
        escaped_path = str(path).replace("'", "''")
        script = f"""
Import-Module Microsoft.PowerShell.Security -ErrorAction Stop
$acl = Get-Acl -LiteralPath '{escaped_path}'
$rules = @($acl.Access | ForEach-Object {{
    [pscustomobject]@{{
        Sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        Type = $_.AccessControlType.ToString()
        Rights = [int]$_.FileSystemRights
    }}
}})
[pscustomobject]@{{
    Owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    DaclProtected = $acl.AreAccessRulesProtected
    Rules = $rules
}} | ConvertTo-Json -Compress -Depth 4
"""
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        environment = os.environ.copy()
        environment["PSModulePath"] = os.pathsep.join((
            str(system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
            str(program_files / "WindowsPowerShell" / "Modules"),
        ))
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False, env=environment,
        )
        assert result.returncode == 0, "Windows ACL inspection failed: " + result.stderr
        return json.loads(result.stdout)

    store = new_store(tmp_path, "acl-vault")
    _windows_acl(store.path.parent)
    _windows_acl(store.path)
    sid, _ = _windows_identity()
    system_sid = "S-1-5-18"
    full_control = 0x1F01FF

    for path in (store.path.parent, store.path):
        acl = acl_evidence(path)
        # Verify the Owner independently from the DACL trustee checks.
        assert acl["Owner"] in {sid, system_sid}
        # Verify the DACL independently: only the current user and SYSTEM get full control.
        rules = acl["Rules"]
        assert isinstance(rules, list) and rules
        assert {rule["Sid"] for rule in rules} == {sid, system_sid}
        assert all(rule["Type"] == "Allow" for rule in rules)
        assert all(rule["Rights"] & full_control == full_control for rule in rules)
        assert acl["DaclProtected"] is True

    class RollbackProbe(Exception):
        pass

    journal = Path(str(store.path) + "-journal")
    with pytest.raises(RollbackProbe):
        with store._connection(True) as connection:
            connection.execute("UPDATE credential_store SET generation=generation+1 WHERE id=1")
            assert journal.exists()
            _windows_acl(journal, repair_created_owner=True)
            journal_acl = acl_evidence(journal)
            # Journal Owner and DACL are checked as separate properties too.
            assert journal_acl["Owner"] in {sid, system_sid}
            journal_rules = journal_acl["Rules"]
            assert isinstance(journal_rules, list) and journal_rules
            assert {rule["Sid"] for rule in journal_rules} == {sid, system_sid}
            assert all(rule["Type"] == "Allow" for rule in journal_rules)
            assert all(rule["Rights"] & full_control == full_control for rule in journal_rules)
            raise RollbackProbe

    assert not journal.exists()
    assert store.get("fixture") is None


def test_migration_readback_and_old_file_preserved(tmp_path: Path) -> None:
    source = EncryptedFileCredentialStore(tmp_path / "old" / "credentials.enc", lambda _label: MASTER)
    write_legacy_store(source.path, MASTER, {"fixture": VALUE})
    raw = source.path.read_bytes()
    target = SQLiteCredentialStore(tmp_path / "target" / "credentials.db", lambda _label: MASTER)
    assert migrate_store(source, target, MASTER) == 1
    assert target.get("fixture") == VALUE
    assert source.path.read_bytes() == raw
    assert SQLiteCredentialStore(target.path, lambda _: MASTER).aliases() == ("fixture",)


def test_migration_interrupt_quarantines_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = EncryptedFileCredentialStore(tmp_path / "old" / "credentials.enc", lambda _label: MASTER)
    write_legacy_store(source.path, MASTER, {"fixture": VALUE})
    raw = source.path.read_bytes()
    target = SQLiteCredentialStore(tmp_path / "target" / "credentials.db")
    def interrupt(*_args: object) -> None:
        raise KeyboardInterrupt
    monkeypatch.setattr(SQLiteCredentialStore, "_change", interrupt)
    with pytest.raises(KeyboardInterrupt):
        migrate_store(source, target, MASTER)
    assert source.path.read_bytes() == raw
    assert target.path.exists()
    with pytest.raises(ToolkitError) as error:
        SQLiteCredentialStore(target.path, lambda _: MASTER).aliases()
    assert error.value.error_type == "CredentialMigrationIncomplete"


def test_product_concurrent_saves(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    script = (
        "import sys,json; from pathlib import Path; from dba.sqlite_credentials import SQLiteCredentialStore; "
        "from dba.credentials import Credential; data=json.load(sys.stdin); "
        "s=SQLiteCredentialStore(Path(sys.argv[1]), lambda _: data['master']); "
        "s.set_if_absent(sys.argv[2], Credential('fixture', data['value']))"
    )
    children = [subprocess.Popen([sys.executable, "-c", script, str(store.path), f"fixture-{i}"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for i in range(3)]
    try:
        for child in children:
            assert child.stdin is not None
            child.stdin.write(json.dumps({"master": MASTER, "value": VALUE.password}))
            child.stdin.close()
        for child in children:
            assert child.wait(timeout=15) == 0
        assert store.aliases() == ("fixture-0", "fixture-1", "fixture-2")
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdout, child.stderr):
                if stream:
                    stream.close()


def test_product_replacement_identity_refusal(tmp_path: Path) -> None:
    first, replacement = new_store(tmp_path, "first"), new_store(tmp_path, "replacement")
    first.set("fixture", VALUE)
    with first._connection(True):
        child = subprocess.run([sys.executable, "-c", "import os,sys; os.replace(sys.argv[1],sys.argv[2])",
                                str(replacement.path), str(first.path)], capture_output=True, timeout=5, check=False)
        assert child.returncode != 0
    raw = replacement.path.read_bytes()
    os.replace(replacement.path, first.path)
    with pytest.raises(ToolkitError) as error:
        first.set("other", VALUE)
    assert error.value.error_type == "CredentialStoreChanged"
    assert first.path.read_bytes() == raw


def test_cli_sqlite_lifecycle(tmp_path: Path, isolated_credential_ipc: str,
                              monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import dba.cli as cli
    import dba.encrypted_credentials as encrypted

    monkeypatch.setattr(encrypted, "prompt_master_password", lambda _label="": MASTER)
    monkeypatch.setattr(cli, "prompt_master_password", lambda _label="": MASTER)
    monkeypatch.setattr(cli, "prompt_new_master_password", lambda: MASTER)
    monkeypatch.setattr(cli, "prompt_credential_for_storage", lambda: VALUE)
    monkeypatch.setattr(cli, "confirm_action", lambda _label: True)
    path = tmp_path / "cli-vault" / "credentials.db"
    def command(*args: str) -> dict[str, object]:
        assert cli.main(["credentials", *args, "--json"]) == 0
        output = capsys.readouterr().out
        assert VALUE.password not in output and MASTER not in output
        return json.loads(output)
    options = ["--backend", "sqlite", "--store-path", str(path)]
    command("init", *options)
    command("set", "fixture", *options)
    command("unlock", *options)
    for verb in ("list", "show", "test"):
        command(verb, *([] if verb == "list" else ["fixture"]), *options)
    command("set", "second-fixture", *options)
    assert CredentialSessionClient().get("second-fixture", path) == VALUE
    command("delete", "fixture", *options)
    assert CredentialSessionClient().get("fixture", path) is None
    command("status")
    command("rekey", *options)
    command("lock")
    assert SQLiteCredentialStore(path, lambda _: MASTER).get("second-fixture") == VALUE


def test_sqlite_session_jit_save_and_next_resolver_reuse(
    tmp_path: Path, isolated_credential_ipc: str,
) -> None:
    from dba.credentials import PromptCredentialProvider
    from dba.encrypted_credentials import CredentialResolver

    path = tmp_path / "sqlite-jit" / "credentials.db"
    store = SQLiteCredentialStore(path, lambda _: MASTER)
    store.initialize(MASTER)
    _, credentials = store._read_container()
    client = CredentialSessionClient()
    opened = client.load(credentials, MIN_TTL_SECONDS, path, store.session_key(),
                         store_backend="sqlite", store_binding=store.binding)
    assert opened["status"] == "UNLOCKED"
    store.lock()
    credentials.clear()

    resolver = CredentialResolver(
        None, store, PromptCredentialProvider(), mode="sqlite", database_id="temporary-test",
        credential_prompt=lambda *_: VALUE, save_prompt=lambda: True,
        ephemeral_prompt=lambda **_: pytest.fail("SQLite JIT save must persist through the selected Store"),
    )
    assert resolver.get_credential("jit-fixture") == VALUE
    assert resolver.last_backend == store.backend_name
    assert client.status()["status"] == "UNLOCKED"
    assert client.get("jit-fixture", path) == VALUE

    second = CredentialResolver(
        None, SQLiteCredentialStore(path, lambda _: MASTER), PromptCredentialProvider(), mode="sqlite",
        credential_prompt=lambda *_: pytest.fail("JIT alias must be read from the same SQLite Session"),
    )
    assert second.get_credential("jit-fixture") == VALUE
    assert second.last_backend == "Encrypted Credential Session"
    assert VALUE.password.encode() not in path.read_bytes()


@pytest.mark.parametrize(("when", "exit_code", "committed"), [("before", 73, False), ("after", 74, True)])
def test_product_crash_recovery(tmp_path: Path, when: str, exit_code: int, committed: bool) -> None:
    store = new_store(tmp_path)
    original = Credential("fixture", VALUE.password + "x" * 200000)
    store.set("original", original)
    script = '''
import json, os, sqlite3, sys
from pathlib import Path
from dba.sqlite_credentials import SQLiteCredentialStore
from dba.credentials import Credential
data=json.load(sys.stdin)
store=SQLiteCredentialStore(Path(sys.argv[1]), lambda _:data['master'])
store.session_key()
original_connect=sqlite3.connect
class CrashConnection(sqlite3.Connection):
    def commit(self):
        if self.total_changes and sys.argv[2]=='before': os._exit(73)
        super().commit()
        if self.total_changes: os._exit(74)
def connect(*args, **kwargs):
    connection=original_connect(*args, **kwargs, factory=CrashConnection)
    connection.execute('PRAGMA cache_size=8')
    connection.execute('PRAGMA cache_spill=ON')
    return connection
sqlite3.connect=connect
store.set('added', Credential('fixture',data['value']))
'''
    child = subprocess.run([sys.executable, "-c", script, str(store.path), when],
                           input=json.dumps({"master": MASTER, "value": VALUE.password}),
                           capture_output=True, text=True, timeout=15, check=False)
    assert child.returncode == exit_code
    if not committed:
        assert Path(str(store.path) + "-journal").exists()
    assert store.get("original") == original
    assert (store.get("added") is not None) is committed
    store.set("after-recovery", VALUE)
    assert store.get("after-recovery") == VALUE


def test_migration_readback_failure_never_activates_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = EncryptedFileCredentialStore(tmp_path / "old" / "credentials.enc", lambda _label: MASTER)
    write_legacy_store(source.path, MASTER, {"fixture": VALUE})
    raw = source.path.read_bytes()
    target = SQLiteCredentialStore(tmp_path / "target" / "credentials.db")
    read = SQLiteCredentialStore._read_container
    def incorrect_readback(self: SQLiteCredentialStore) -> tuple[dict[str, object], dict[str, Credential]]:
        freshly_opened = self._session_key is None
        header, values = read(self)
        return header, {} if freshly_opened else values
    monkeypatch.setattr(SQLiteCredentialStore, "_read_container", incorrect_readback)
    with pytest.raises(ToolkitError) as error:
        migrate_store(source, target, MASTER)
    assert error.value.error_type == "CredentialMigrationFailed"
    with pytest.raises(ToolkitError) as quarantined:
        SQLiteCredentialStore(target.path, lambda _: MASTER).aliases()
    assert quarantined.value.error_type == "CredentialMigrationIncomplete"
    assert source.path.read_bytes() == raw


def test_busy_transaction_has_distinct_error(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    other = SQLiteCredentialStore(store.path, lambda _: MASTER)
    other.session_key()
    with store._connection(True):
        with pytest.raises(ToolkitError) as error:
            other.set("must-not-write", VALUE)
        assert error.value.error_type == "CredentialStoreBusy"
    assert store.aliases() == ()


def test_sqlite_load_rejects_identical_copy_with_different_file_identity(tmp_path: Path,
                                                                       isolated_credential_ipc: str) -> None:
    first, second = new_store(tmp_path, "first"), new_store(tmp_path, "second")
    first.set("fixture", VALUE)
    second.path.write_bytes(first.path.read_bytes())
    _, values = first._read_container()
    client = CredentialSessionClient()
    with pytest.raises(ToolkitError) as mismatch:
        client.load(values, MIN_TTL_SECONDS, second.path, first.session_key(), "sqlite", first.binding)
    assert mismatch.value.error_type == "CredentialStoreChanged"


def test_unsupported_sqlite_version_is_distinct(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        header = json.loads(connection.execute("SELECT header FROM credential_store").fetchone()[0])
        header["version"] = 99
        connection.execute("UPDATE credential_store SET header=?", (json.dumps(header),))
    connection.close()
    with pytest.raises(ToolkitError) as error:
        store.get("fixture")
    assert error.value.error_type == "CredentialStoreUnsupported"
