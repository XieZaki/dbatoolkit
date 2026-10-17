from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from dba.credentials import Credential, CredentialProvider, CredentialStore
from dba.encrypted_credentials import (
    EncryptedFileCredentialStore,
    _StoreLock,
    crypto_available,
    default_store_path,
    posix_mode_is_private,
)
from dba.errors import ToolkitError

SECRET = "DBA-Test-Secret-987654"
MASTER = "correct horse battery staple 2026"


def _store(path: Path, password: str = MASTER) -> EncryptedFileCredentialStore:
    return EncryptedFileCredentialStore(path, lambda _: password)


def test_default_store_paths_are_user_scoped_and_cross_platform(tmp_path: Path) -> None:
    windows = default_store_path({"APPDATA": r"C:\Users\operator\AppData\Roaming"}, "nt", tmp_path)
    assert str(windows).endswith(r"AppData\Roaming\DBAToolkit\credentials.enc")
    linux_xdg = default_store_path({"XDG_CONFIG_HOME": "/srv/user-config"}, "posix", tmp_path)
    assert linux_xdg == Path("/srv/user-config/dba-toolkit/credentials.enc")
    linux_default = default_store_path({}, "posix", tmp_path)
    assert linux_default == tmp_path / ".config" / "dba-toolkit" / "credentials.enc"


@pytest.mark.parametrize("mode,expected", [
    (0o700, True), (0o600, True), (0o750, False), (0o640, False),
    (0o777, False), (0o644, False),
])
def test_posix_permission_policy_is_platform_independent(mode: int, expected: bool) -> None:
    assert posix_mode_is_private(mode) is expected


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_encrypted_store_format_randomness_roundtrip_and_secret_absence(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    store = _store(path)
    store.initialize(MASTER)
    store.set("monitor", Credential("dba_monitor", SECRET))
    raw = path.read_bytes()
    header = json.loads(raw)
    assert header["format"] == "dba-toolkit-credential-store"
    assert header["version"] == 1
    assert header["kdf"] == {"name": "argon2id", "memory_cost_kib": 65536,
                              "time_cost": 3, "parallelism": 1}
    assert header["cipher"] == {"name": "aes-256-gcm"}
    assert len(header["salt"]) >= 22 and len(header["nonce"]) == 16
    assert SECRET.encode() not in raw and b"dba_monitor" not in raw and b"monitor" not in raw
    reopened = _store(path)
    assert reopened.get("monitor") == Credential("dba_monitor", SECRET)
    assert reopened.aliases() == ("monitor",)
    path_two = tmp_path / "second.enc"
    _store(path_two).initialize(MASTER)
    assert json.loads(path_two.read_bytes())["ciphertext"] != header["ciphertext"]


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_wrong_password_ciphertext_and_header_tampering_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    _store(path).initialize(MASTER)
    with pytest.raises(ToolkitError, match="Unable to unlock encrypted credential store"):
        _store(path, "incorrect master password").aliases()

    original = json.loads(path.read_text(encoding="utf-8"))
    modified = dict(original)
    text = modified["ciphertext"]
    modified["ciphertext"] = ("A" if text[0] != "A" else "B") + text[1:]
    path.write_bytes(EncryptedFileCredentialStore._canonical(modified))
    with pytest.raises(ToolkitError):
        _store(path).aliases()

    original["salt"] = base64.b64encode(b"Z" * 16).decode("ascii")
    path.write_bytes(EncryptedFileCredentialStore._canonical(original))
    with pytest.raises(ToolkitError, match="Unable to unlock encrypted credential store"):
        _store(path).aliases()


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_encrypted_store_crud_rekey_and_corruption_preservation(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    store = _store(path)
    store.initialize(MASTER)
    store.set("monitor", Credential("dba_monitor", SECRET))
    store.set("monitor", Credential("dba_monitor", SECRET + "-updated"))
    assert store.get("monitor") == Credential("dba_monitor", SECRET + "-updated")
    before_rekey = path.read_bytes()
    store.rekey(MASTER, "new master passphrase for 2026")
    assert path.read_bytes() != before_rekey
    with pytest.raises(ToolkitError):
        _store(path, MASTER).get("monitor")
    assert _store(path, "new master passphrase for 2026").get("monitor") == Credential(
        "dba_monitor", SECRET + "-updated"
    )
    assert store.delete("monitor")
    assert store.aliases() == ()

    path.write_text("{corrupt", encoding="utf-8")
    corrupt = path.read_bytes()
    with pytest.raises(ToolkitError, match="Corrupted"):
        store.set("monitor", Credential("dba_monitor", SECRET))
    assert path.read_bytes() == corrupt


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_encrypted_set_if_absent_never_overwrites_existing_alias(tmp_path: Path) -> None:
    store = _store(tmp_path / "credentials.enc")
    store.initialize(MASTER)
    original = Credential("monitor", "first-secret")
    store.set("shared", original)
    with pytest.raises(ToolkitError, match="already exists"):
        store.set_if_absent("shared", Credential("replacement", "second-secret"))
    assert store.get("shared") == original


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_jit_save_uses_initialized_encrypted_store_and_next_resolver_reuses_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dba.encrypted_credentials import CredentialResolver

    class OSStore(CredentialStore):
        @property
        def backend_name(self) -> str:
            return "Unavailable OS Store"

        def available(self) -> bool:
            return False

        def get(self, alias: str) -> Credential | None:
            return None

        def set(self, alias: str, credential: Credential) -> None:
            raise AssertionError("unavailable OS store must not be written")

        def delete(self, alias: str) -> bool:
            return False

        def aliases(self) -> tuple[str, ...]:
            return ()

    path = tmp_path / "credentials.enc"
    initializer = _store(path)
    initializer.initialize(MASTER)
    initializer.lock()
    encrypted = _store(path)
    new_credential = Credential("monitor", "DBA-JIT-Password-987654321")
    first = CredentialResolver(
        OSStore(), encrypted, CredentialProviderFallback(), database_id="db-01",
        credential_prompt=lambda _db, _alias: new_credential, save_prompt=lambda: True,
    )
    assert first.get_credential("new-alias") == new_credential
    assert first.last_backend == "Encrypted Local Store"
    assert new_credential.password not in path.read_text(encoding="utf-8")
    capsys.readouterr()

    second = CredentialResolver(
        OSStore(), _store(path), CredentialProviderFallback(),
        credential_prompt=lambda *_: pytest.fail("persisted alias must bypass JIT"),
    )
    assert second.get_credential("new-alias") == new_credential


class CredentialProviderFallback(CredentialProvider):
    def get_credential(self, alias: str) -> Credential:
        raise AssertionError("resolver should not use the generic fallback")


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_failed_atomic_update_preserves_original_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "credentials.enc"
    store = _store(path)
    store.initialize(MASTER)
    old = path.read_bytes()

    def fail(_: bytes) -> None:
        raise ToolkitError("CredentialStoreWriteFailed", "failed")

    monkeypatch.setattr(store, "_atomic_write", fail)
    with pytest.raises(ToolkitError):
        store.set("monitor", Credential("dba_monitor", SECRET))
    assert path.read_bytes() == old
    assert _store(path).aliases() == ()


def test_linux_permissions_are_rejected_when_unsafe(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not available on Windows")
    folder = tmp_path / "dba-toolkit"
    folder.mkdir(mode=0o700)
    path = folder / "credentials.enc"
    path.write_text("not-used", encoding="utf-8")
    os.chmod(folder, 0o755)
    store = _store(path)
    assert store.permission_status() == "UNSAFE"
    with pytest.raises(ToolkitError, match="Permission Unsafe"):
        store.aliases()


def test_file_lock_rejects_concurrent_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "credentials.lock"
    if os.name == "nt":
        import msvcrt

        locked = False
        def lock_file(fd: int, mode: int, count: int) -> None:
            nonlocal locked
            if mode == msvcrt.LK_NBLCK and locked:
                raise OSError("locked")
            locked = mode == msvcrt.LK_NBLCK
        monkeypatch.setattr(msvcrt, "locking", lock_file)
    else:
        import fcntl

        locked = False
        def lock_file(fd: int, mode: int) -> None:
            nonlocal locked
            if mode == fcntl.LOCK_EX and locked:
                raise OSError("locked")
            locked = mode == fcntl.LOCK_EX
        monkeypatch.setattr(fcntl, "flock", lock_file)
    with _StoreLock(path):
        with pytest.raises(ToolkitError, match="being modified"):
            with _StoreLock(path):
                pass


def test_encrypted_provider_does_not_fallback_when_store_is_corrupt(tmp_path: Path) -> None:
    from dba.encrypted_credentials import CredentialResolver

    class OSStore(CredentialStore):
        @property
        def backend_name(self) -> str:
            return "Fake OS Store"

        def available(self) -> bool:
            return False

        def get(self, alias: str) -> Credential | None:
            return None

        def set(self, alias: str, credential: Credential) -> None:
            raise NotImplementedError

        def delete(self, alias: str) -> bool:
            return False

        def aliases(self) -> tuple[str, ...]:
            return ()

    class Fallback(CredentialProvider):
        called = False

        def get_credential(self, alias: str) -> Credential:
            self.called = True
            return Credential("user", SECRET)

    path = tmp_path / "credentials.enc"
    path.write_text("corrupt", encoding="utf-8")
    fallback = Fallback()
    resolver = CredentialResolver(
        OSStore(), EncryptedFileCredentialStore(path), fallback,
        credential_prompt=lambda *_: pytest.fail("corrupt store must never trigger JIT"),
    )
    with pytest.raises(ToolkitError):
        resolver.get_credential("monitor")
    assert not fallback.called


def test_crypto_unavailable_fails_before_master_password_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = EncryptedFileCredentialStore(tmp_path / "credentials.enc")
    monkeypatch.setattr("dba.encrypted_credentials.crypto_available", lambda: False)
    assert not store.available()
    with pytest.raises(ToolkitError, match="cryptography and argon2-cffi"):
        store._crypto()


def test_resolver_uses_os_first_and_interactive_when_no_persistent_provider(tmp_path: Path) -> None:
    from dba.encrypted_credentials import CredentialResolver

    class Store(CredentialStore):
        def __init__(self, credential: Credential | None) -> None:
            self.credential = credential

        @property
        def backend_name(self) -> str:
            return "Fake OS Store"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> Credential | None:
            return self.credential

        def set(self, alias: str, credential: Credential) -> None:
            raise NotImplementedError

        def delete(self, alias: str) -> bool:
            return False

        def aliases(self) -> tuple[str, ...]:
            return ()

    class Fallback(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            return Credential("interactive", SECRET)

    os_credential = Credential("from-os", SECRET)
    resolver = CredentialResolver(Store(os_credential), EncryptedFileCredentialStore(
        tmp_path / "no-store"), Fallback())
    assert resolver.get_credential("monitor") == os_credential
    assert resolver.last_backend == "Fake OS Store"

    fallback_resolver = CredentialResolver(Store(None), EncryptedFileCredentialStore(
        tmp_path / "no-store"), Fallback(), credential_prompt=lambda _db, _alias: Credential("interactive", SECRET),
        save_prompt=lambda: False)
    assert fallback_resolver.get_credential("monitor").username == "interactive"
    assert fallback_resolver.last_backend == "Ephemeral / This Run Only"


def test_jit_save_y_persists_in_existing_os_store_and_save_n_does_not(
    tmp_path: Path,
) -> None:
    from dba.encrypted_credentials import CredentialResolver

    class Fallback(CredentialProvider):
        def get_credential(self, alias: str) -> Credential:
            raise AssertionError("JIT prompt callback should be used")

    class Store(CredentialStore):
        def __init__(self) -> None:
            self.values: dict[str, Credential] = {}

        @property
        def backend_name(self) -> str:
            return "Fake Secure Store"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> Credential | None:
            return self.values.get(alias)

        def set(self, alias: str, credential: Credential) -> None:
            self.values[alias] = credential

        def delete(self, alias: str) -> bool:
            return self.values.pop(alias, None) is not None

        def aliases(self) -> tuple[str, ...]:
            return tuple(self.values)

    credential = Credential("monitor", "DBA-JIT-Password-987654321")
    store = Store()
    resolver = CredentialResolver(
        store, EncryptedFileCredentialStore(tmp_path / "missing.enc"),
        Fallback(), database_id="db-01",
        credential_prompt=lambda database, alias: credential,
        save_prompt=lambda: True,
    )
    assert resolver.get_credential("db-monitor") == credential
    assert store.get("db-monitor") == credential
    assert resolver.last_backend == "Fake Secure Store"

    second_store = Store()
    no_save = CredentialResolver(
        second_store, EncryptedFileCredentialStore(tmp_path / "other-missing.enc"),
        Fallback(), database_id="db-01",
        credential_prompt=lambda database, alias: credential,
        save_prompt=lambda: False,
    )
    assert no_save.get_credential("db-monitor") == credential
    assert second_store.aliases() == ()
    assert no_save.last_backend == "Ephemeral / This Run Only"


def test_no_master_password_cli_option_exists(capsys: pytest.CaptureFixture[str]) -> None:
    from dba.cli import main

    with pytest.raises(SystemExit):
        main(["credentials", "init", "--backend", "encrypted", "--master-password"])
    output = capsys.readouterr().err
    assert "--master-password" in output


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_one_kdf_unlock_is_reused_for_500_alias_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "credentials.enc"
    store = _store(path)
    store.initialize(MASTER)
    credentials = {f"monitor-{index}": Credential(f"user-{index}", SECRET)
                   for index in range(500)}
    store._atomic_write(store._encode(credentials, MASTER))
    store.lock()
    reopened = _store(path)
    calls = 0
    original = reopened._derive

    def counted(password: str, salt: bytes, params: dict[str, int]) -> bytes:
        nonlocal calls
        calls += 1
        return original(password, salt, params)

    monkeypatch.setattr(reopened, "_derive", counted)
    assert len(reopened.aliases()) == 500
    for index in range(500):
        assert reopened.get(f"monitor-{index}") is not None
    assert calls == 1


def test_unsupported_store_version_fails_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    raw = EncryptedFileCredentialStore._canonical(
        {"format": "dba-toolkit-credential-store", "version": 9}
    )
    path.write_bytes(raw)
    with pytest.raises(ToolkitError, match="format or version is unsupported"):
        _store(path).aliases()
    assert path.read_bytes() == raw


@pytest.mark.parametrize("field,value", [
    ("kdf", {"name": "unsupported-kdf", "memory_cost_kib": 65536,
              "time_cost": 3, "parallelism": 1}),
    ("cipher", {"name": "unsupported-cipher"}),
])
def test_unsupported_crypto_algorithm_fails_without_unlock(
    tmp_path: Path, field: str, value: dict[str, object]
) -> None:
    header: dict[str, object] = {
        "format": "dba-toolkit-credential-store", "version": 1,
        "kdf": {"name": "argon2id", "memory_cost_kib": 65536,
                "time_cost": 3, "parallelism": 1},
        "cipher": {"name": "aes-256-gcm"},
        "salt": base64.b64encode(b"S" * 16).decode("ascii"),
        "nonce": base64.b64encode(b"N" * 12).decode("ascii"),
        "ciphertext": base64.b64encode(b"C" * 16).decode("ascii"),
    }
    header[field] = value
    path = tmp_path / "credentials.enc"
    path.write_bytes(EncryptedFileCredentialStore._canonical(header))
    with pytest.raises(ToolkitError, match="unsupported"):
        _store(path).aliases()

