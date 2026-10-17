from __future__ import annotations

import io
from pathlib import Path

import pytest

from dba.credentials import (
    Credential,
    CredentialProvider,
    CredentialStore,
    confirm_save_credential,
    prompt_jit_credential,
)
from dba.encrypted_credentials import CredentialResolver, EncryptedFileCredentialStore
from dba.errors import ToolkitError

SECRET = "DBA-Test-Secret-987654"


class MemoryStore(CredentialStore):
    def __init__(self, values: dict[str, Credential] | None = None, *, ready: bool = True) -> None:
        self.values = values or {}
        self.ready = ready
        self.writes = 0

    @property
    def backend_name(self) -> str:
        return "Fake Secure Store"

    def available(self) -> bool:
        return self.ready

    def get(self, alias: str) -> Credential | None:
        return self.values.get(alias)

    def set(self, alias: str, credential: Credential) -> None:
        self.writes += 1
        self.values[alias] = credential

    def delete(self, alias: str) -> bool:
        return self.values.pop(alias, None) is not None

    def aliases(self) -> tuple[str, ...]:
        return tuple(self.values)


class Fallback(CredentialProvider):
    def get_credential(self, alias: str) -> Credential:
        raise AssertionError("JIT should use the dedicated credential prompt")


def _resolver(
    tmp_path: Path,
    store: MemoryStore,
    credential: Credential,
    *,
    save: bool,
    ephemeral: bool = True,
) -> CredentialResolver:
    return CredentialResolver(
        store, EncryptedFileCredentialStore(tmp_path / "not-initialized.enc"), Fallback(),
        database_id="db-01", credential_prompt=lambda database, alias: credential,
        save_prompt=lambda: save, ephemeral_prompt=lambda **kwargs: ephemeral,
    )


def test_missing_alias_jit_save_y_persists_and_original_command_gets_credential(tmp_path: Path) -> None:
    store = MemoryStore()
    credential = Credential("monitor", SECRET)
    resolver = _resolver(tmp_path, store, credential, save=True)

    assert resolver.get_credential("db-monitor") == credential
    assert store.get("db-monitor") == credential
    assert resolver.last_backend == store.backend_name
    assert store.writes == 1


def test_save_n_is_ephemeral_and_the_next_command_prompts_again(tmp_path: Path) -> None:
    store = MemoryStore()
    credential = Credential("monitor", SECRET)
    prompts = 0

    def prompt(_database: str, _alias: str) -> Credential:
        nonlocal prompts
        prompts += 1
        return credential

    for _ in range(2):
        resolver = CredentialResolver(
            store, EncryptedFileCredentialStore(tmp_path / "not-initialized.enc"), Fallback(),
            database_id="db-01", credential_prompt=prompt, save_prompt=lambda: False,
        )
        assert resolver.get_credential("db-monitor") == credential
        assert resolver.last_backend == "Ephemeral / This Run Only"
    assert prompts == 2
    assert store.aliases() == () and store.writes == 0


def test_secure_store_unavailable_uses_ephemeral_when_confirmed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = MemoryStore(ready=False)
    credential = Credential("monitor", SECRET)
    resolver = _resolver(tmp_path, store, credential, save=True)

    assert resolver.get_credential("db-monitor") == credential
    assert resolver.last_backend == "Ephemeral / This Run Only"
    assert "will be used for this command only" in capsys.readouterr().err
    assert store.writes == 0


def test_save_failure_requires_ephemeral_confirmation_and_secure_failure_defaults_no(
    tmp_path: Path,
) -> None:
    class BrokenStore(MemoryStore):
        def set(self, alias: str, credential: Credential) -> None:
            raise ToolkitError("CredentialStoreUnsafe", "unsafe store")

    credential = Credential("monitor", SECRET)
    resolver = _resolver(tmp_path, BrokenStore(), credential, save=True, ephemeral=False)
    with pytest.raises(ToolkitError, match="command cancelled"):
        resolver.get_credential("db-monitor")


def test_existing_os_credential_bypasses_jit(tmp_path: Path) -> None:
    stored = Credential("existing", "stored-secret")
    store = MemoryStore({"db-monitor": stored})
    resolver = CredentialResolver(
        store, EncryptedFileCredentialStore(tmp_path / "unused.enc"), Fallback(),
        credential_prompt=lambda *_: pytest.fail("existing alias must bypass JIT"),
    )
    assert resolver.get_credential("db-monitor") == stored


def test_noninteractive_jit_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import credentials

    monkeypatch.setattr(credentials.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ToolkitError, match="interactive input is unavailable"):
        prompt_jit_credential("db-01", "db-monitor")


def test_jit_prompt_hides_password_and_does_not_echo_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import credentials

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(credentials.sys, "stdin", TtyInput("monitor-user\n"))
    stderr = TtyInput()
    monkeypatch.setattr(credentials.sys, "stderr", stderr)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: SECRET)
    credential = prompt_jit_credential("db-01", "db-monitor")
    assert credential == Credential("monitor-user", SECRET)
    output = stderr.getvalue()
    assert "db-01" in output and "db-monitor" in output and SECRET not in output


def test_save_prompt_defaults_to_yes_and_accepts_no(monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import credentials

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(credentials.sys, "stdin", TtyInput("\n"))
    assert confirm_save_credential()
    monkeypatch.setattr(credentials.sys, "stdin", TtyInput("No\n"))
    assert not confirm_save_credential()
