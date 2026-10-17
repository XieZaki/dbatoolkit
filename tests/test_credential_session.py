from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dba.credential_session import (
    DEFAULT_TTL_SECONDS,
    MAX_MESSAGE_BYTES,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    CredentialSessionClient,
    _dispatch,
    _is_current,
    _new_manager,
    _read_frame,
    _status,
    _validate_request,
    parse_ttl,
)
from dba.credentials import Credential, CredentialStore
from dba.encrypted_credentials import (
    CredentialResolver,
    EncryptedFileCredentialStore,
    crypto_available,
)
from dba.errors import ToolkitError


def _load(manager: dict[str, object], path: Path, ttl_seconds: int = MIN_TTL_SECONDS) -> None:
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    response, stop = _dispatch(manager, {
        "protocol_version": 1,
        "operation": "LOAD",
        "credentials": {"monitor": {"username": "db_monitor", "password": 'DBA-Test-Secret-987654'}},
        "ttl_seconds": ttl_seconds,
        "store_path": str(path),
        "store_fingerprint": fingerprint,
        "store_key": base64.b64encode(b"k" * 32).decode("ascii"),
    })
    assert response["status"] == "UNLOCKED"
    assert not stop


@pytest.mark.parametrize(("value", "expected"), [
    (None, DEFAULT_TTL_SECONDS), ("5m", MIN_TTL_SECONDS), ("30m", 1800),
    ("8h", MAX_TTL_SECONDS),
])
def test_ttl_parser_accepts_bounded_durations(value: str | None, expected: int) -> None:
    assert parse_ttl(value) == expected


@pytest.mark.parametrize("value", ["0m", "4m", "9h", "0", "infinite", "30m ", "999999999m"])
def test_ttl_parser_rejects_unbounded_or_malformed_values(value: str) -> None:
    with pytest.raises(ToolkitError, match="TTL"):
        parse_ttl(value)


def test_agent_dispatch_supports_single_alias_metadata_lock_and_absolute_expiry(
    tmp_path: Path
) -> None:
    path = tmp_path / "credentials.enc"
    path.write_bytes(b"ciphertext-container")
    manager = _new_manager()
    _load(manager, path)

    response, stop = _dispatch(manager, {"protocol_version": 1, "operation": "GET", "alias": "monitor"})
    assert response["credential"] == {"username": "db_monitor", "password": 'DBA-Test-Secret-987654'}
    assert not stop

    metadata, _ = _dispatch(manager, {"protocol_version": 1, "operation": "LIST_METADATA"})
    assert metadata["credentials"] == [{"alias": "monitor", "username": "db_monitor"}]
    assert "password" not in json.dumps(metadata)

    assert not _is_current(manager, float(manager["expires_monotonic"]) + 0.1)
    assert manager["credentials"] is None


def test_agent_invalidates_session_when_encrypted_store_changes(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    path.write_bytes(b"initial")
    manager = _new_manager()
    _load(manager, path)
    path.write_bytes(b"replaced")
    assert _status(manager)["status"] == "LOCKED"
    assert manager["credentials"] is None


def test_lock_clears_credential_references(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    path.write_bytes(b"store")
    manager = _new_manager()
    _load(manager, path)
    response, stop = _dispatch(manager, {"protocol_version": 1, "operation": "LOCK"})
    assert response["status"] == "LOCKED"
    assert stop
    assert manager["credentials"] is None


def test_protocol_rejects_unknown_fields_and_version() -> None:
    with pytest.raises(ToolkitError, match="schema"):
        _validate_request({"protocol_version": 1, "operation": "STATUS", "dump": True})
    with pytest.raises(ToolkitError, match="schema"):
        _validate_request({"protocol_version": 1, "operation": "GET"})


def test_protocol_rejects_oversized_frame() -> None:
    reader = io.BytesIO(struct.pack("!I", MAX_MESSAGE_BYTES + 1))
    with pytest.raises(ToolkitError, match="size"):
        _read_frame(reader, time.monotonic() + 1)


def test_protocol_rejects_duplicate_json_fields() -> None:
    body = b'{"protocol_version":1,"protocol_version":1,"operation":"STATUS"}'
    reader = io.BytesIO(struct.pack("!I", len(body)) + body)
    with pytest.raises(ToolkitError, match="malformed"):
        _read_frame(reader, time.monotonic() + 1)


def test_unix_peer_uid_check_accepts_only_current_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    import dba.credential_session as session

    current_uid = 1001
    monkeypatch.setattr(session.os, "getuid", lambda: current_uid, raising=False)
    monkeypatch.setattr(session.socket, "SO_PEERCRED", 17, raising=False)

    class FakePeer:
        def __init__(self, peer_uid: int) -> None:
            self.peer_uid = peer_uid

        def getsockopt(self, _level: int, _option: int, _size: int) -> bytes:
            return struct.pack("3i", 123, self.peer_uid, 100)

    assert session._unix_peer_is_current(FakePeer(current_uid))  # type: ignore[arg-type]
    assert not session._unix_peer_is_current(FakePeer(current_uid + 1))  # type: ignore[arg-type]


def test_explicit_encrypted_resolver_uses_session_before_store_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = Credential("monitor", "session-password")

    class Store(CredentialStore):
        @property
        def backend_name(self) -> str:
            return "Fake OS Store"

        def available(self) -> bool:
            return False

        def get(self, alias: str) -> Credential | None:
            return None

        def set(self, alias: str, value: Credential) -> None:
            raise NotImplementedError

        def delete(self, alias: str) -> bool:
            return False

        def aliases(self) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(CredentialSessionClient, "get", lambda self, alias: credential)
    encrypted = EncryptedFileCredentialStore(tmp_path / "not-created.enc")
    monkeypatch.setattr(encrypted, "_require_crypto", lambda: pytest.fail("session should avoid decrypt"))
    resolver = CredentialResolver(Store(), encrypted, fallback=None, mode="encrypted")
    assert resolver.get_credential("monitor") == credential
    assert resolver.last_backend == "Encrypted Credential Session"


def test_session_hits_for_500_aliases_do_not_run_argon2id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = {
        f"monitor-{index}": Credential(f"user-{index}", f"session-password-{index}")
        for index in range(500)
    }

    def session_get(_self: CredentialSessionClient, alias: str) -> Credential | None:
        return credentials.get(alias)

    monkeypatch.setattr(CredentialSessionClient, "get", session_get)
    encrypted = EncryptedFileCredentialStore(tmp_path / "unused.enc")
    monkeypatch.setattr(
        encrypted, "_derive", lambda *_args, **_kwargs: pytest.fail("session hit must not run Argon2id")
    )
    resolver = CredentialResolver(None, encrypted, fallback=None, mode="encrypted")

    for index in range(500):
        assert resolver.get_credential(f"monitor-{index}") == credentials[f"monitor-{index}"]
        assert resolver.last_backend == "Encrypted Credential Session"


def test_current_platform_ipc_lifecycle(tmp_path: Path) -> None:
    client = CredentialSessionClient()
    if client.status().get("status") == "UNLOCKED":
        pytest.skip("An active per-user credential session must not be replaced by a test")
    store_path = tmp_path / "encrypted-store-fingerprint.bin"
    store_path.write_bytes(b"non-secret integration fingerprint")
    value = Credential("session-test-user", "in-memory-session-test-secret")
    try:
        opened = client.load({"session-test-alias": value}, MIN_TTL_SECONDS, store_path, b"k" * 32)
        assert opened.get("status") == "UNLOCKED"
        assert client.get("session-test-alias") == value
        child = subprocess.run(
            [sys.executable, "-c",
             "from dba.credential_session import CredentialSessionClient; "
             "value = CredentialSessionClient().get('session-test-alias'); "
             "assert value is not None and value.username == 'session-test-user'; "
             "print('SESSION_LOOKUP_OK')"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        assert child.stdout.strip() == "SESSION_LOOKUP_OK"
        assert value.password not in child.stdout + child.stderr
        status = client.status()
        assert status.get("status") == "UNLOCKED"
        assert "session_id" not in json.dumps(status)
    finally:
        client.lock()
    assert client.status().get("status") == "LOCKED"


@pytest.mark.skipif(not crypto_available(), reason="cryptography and argon2-cffi are not installed")
def test_active_session_can_securely_save_new_alias_and_invalidates_session(tmp_path: Path) -> None:
    from dba.encrypted_credentials import EncryptedFileCredentialStore

    client = CredentialSessionClient()
    if client.status().get("status") == "UNLOCKED":
        pytest.skip("An active per-user credential session must not be replaced by a test")
    store_path = tmp_path / "credentials.enc"
    master = "test-master-password-long-enough"
    store = EncryptedFileCredentialStore(store_path, password_provider=lambda _label: master)
    store.initialize(master)
    store.set("existing", Credential("monitor", "old-secret"))
    store.lock()
    _, decrypted = store._read_container()
    key = store.session_key()
    try:
        assert client.load(decrypted, MIN_TTL_SECONDS, store_path, key).get("status") == "UNLOCKED"
    finally:
        store.lock()
        decrypted.clear()
    assert client.get("new-alias") is None
    assert client.save("existing", Credential("replacement", "must-not-overwrite"))["status"] == "ALREADY_EXISTS"
    assert client.status().get("status") == "UNLOCKED"
    assert client.save("new-alias", Credential("monitor2", "DBA-JIT-Password-987654321"))["status"] == "SAVED_INVALIDATED"
    assert client.status().get("status") == "LOCKED"
    raw_store = store_path.read_bytes()
    assert b"DBA-JIT-Password-987654321" not in raw_store
    assert b"must-not-overwrite" not in raw_store
    verify = EncryptedFileCredentialStore(store_path, password_provider=lambda _label: master)
    assert verify.get("existing") == Credential("monitor", "old-secret")
    saved = verify.get("new-alias")
    assert saved == Credential("monitor2", "DBA-JIT-Password-987654321")
    verify.lock()
