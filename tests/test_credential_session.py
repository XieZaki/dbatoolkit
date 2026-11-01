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
from typing import Any

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
        "protocol_version": 2,
        "operation": "LOAD",
        "credentials": {"monitor": {"username": "db_monitor", "password": 'DBA-Test-Secret-987654'}},
        "ttl_seconds": ttl_seconds,
        "store_path": str(path),
        "store_fingerprint": fingerprint,
        "store_backend": "encrypted",
        "store_binding": None,
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

    response, stop = _dispatch(manager, {"protocol_version": 2, "operation": "GET", "alias": "monitor", "store_path": str(path)})
    assert response["credential"] == {"username": "db_monitor", "password": 'DBA-Test-Secret-987654'}
    assert not stop

    metadata, _ = _dispatch(manager, {"protocol_version": 2, "operation": "LIST_METADATA", "store_path": str(path)})
    assert metadata["credentials"] == [{"alias": "monitor", "username": "db_monitor"}]
    assert "password" not in json.dumps(metadata)

    assert not _is_current(manager, float(manager["expires_monotonic"]) + 0.1)
    assert manager["credentials"] is None


def test_session_rejects_cross_store_and_legacy_requests(tmp_path: Path) -> None:
    first = tmp_path / "first.enc"
    second = tmp_path / "second.enc"
    first.write_bytes(b"first fixture")
    second.write_bytes(b"second fixture")
    manager = _new_manager()
    _load(manager, first)
    before = second.read_bytes()
    for request in (
        {"protocol_version": 2, "operation": "GET", "alias": "monitor"},
        {"protocol_version": 2, "operation": "GET", "alias": "monitor", "store_path": str(second)},
        {"protocol_version": 2, "operation": "LIST_METADATA", "store_path": str(second)},
        {"protocol_version": 2, "operation": "SAVE", "alias": "monitor", "store_path": str(second),
         "credential": {"username": "test", "password": "<TEST_ONLY>"}},
    ):
        with pytest.raises(ToolkitError):
            _dispatch(manager, request)
    assert second.read_bytes() == before


def test_session_get_rejects_change_before_final_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dba.credential_session as session

    path = tmp_path / "fixture.enc"
    path.write_bytes(b"initial fixture")
    manager = _new_manager()
    _load(manager, path)
    original = session._is_current
    calls = 0

    def replace_on_second_check(current: dict[str, Any], now: float | None = None) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"replacement fixture")
        return original(current, now)

    monkeypatch.setattr(session, "_is_current", replace_on_second_check)
    with pytest.raises((ToolkitError, PermissionError)):
        _dispatch(manager, {"protocol_version": 2, "operation": "GET", "alias": "monitor",
                            "store_path": str(path)})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_windows_get_blocks_external_process_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dba.credential_session as session

    path = tmp_path / "fixture.enc"
    replacement = tmp_path / "replacement.enc"
    path.write_bytes(b"initial fixture")
    replacement.write_bytes(b"replacement fixture")
    manager = _new_manager()
    _load(manager, path)
    original = session._is_current
    checks = 0

    def attempt_external_replace(current: dict[str, Any], now: float | None = None) -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:
            child = subprocess.run([sys.executable, "-c", "import os,sys; os.replace(sys.argv[1],sys.argv[2])",
                                    str(replacement), str(path)], capture_output=True, check=False)
            assert child.returncode != 0
        return original(current, now)

    monkeypatch.setattr(session, "_is_current", attempt_external_replace)
    response, _ = _dispatch(manager, {"protocol_version": 2, "operation": "GET", "alias": "monitor",
                                     "store_path": str(path)})
    assert response["status"] == "OK"
    assert path.read_bytes() == b"initial fixture"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file identity semantics")
def test_windows_get_rejects_replacement_with_identical_contents(tmp_path: Path) -> None:
    path = tmp_path / "fixture.enc"
    replacement = tmp_path / "replacement.enc"
    path.write_bytes(b"identical fixture")
    replacement.write_bytes(b"identical fixture")
    manager = _new_manager()
    _load(manager, path)
    path.unlink()
    replacement.replace(path)

    with pytest.raises(ToolkitError, match="target store"):
        _dispatch(manager, {"protocol_version": 2, "operation": "GET", "alias": "monitor",
                            "store_path": str(path)})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_windows_protected_handle_also_blocks_own_atomic_replace(tmp_path: Path) -> None:
    import os

    from dba.credential_session import _windows_store_snapshot

    path = tmp_path / "store.enc"
    staged = tmp_path / "staged.enc"
    path.write_bytes(b"old fixture")
    staged.write_bytes(b"new fixture")
    stream, _, _ = _windows_store_snapshot(str(path))
    try:
        with pytest.raises(PermissionError):
            os.replace(staged, path)
        assert path.read_bytes() == b"old fixture"
    finally:
        stream.close()
    os.replace(staged, path)
    assert path.read_bytes() == b"new fixture"


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
    response, stop = _dispatch(manager, {"protocol_version": 2, "operation": "LOCK"})
    assert response["status"] == "LOCKED"
    assert stop
    assert manager["credentials"] is None


def test_protocol_rejects_unknown_fields_and_version() -> None:
    with pytest.raises(ToolkitError, match="schema"):
        _validate_request({"protocol_version": 2, "operation": "STATUS", "dump": True})
    with pytest.raises(ToolkitError, match="schema"):
        _validate_request({"protocol_version": 2, "operation": "GET"})


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

    monkeypatch.setattr(CredentialSessionClient, "get", lambda self, alias, store_path=None: credential)
    monkeypatch.setattr(CredentialSessionClient, "status", lambda self: {"status": "UNLOCKED"})
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

    def session_get(_self: CredentialSessionClient, alias: str, store_path: Path | None = None) -> Credential | None:
        return credentials.get(alias)

    monkeypatch.setattr(CredentialSessionClient, "get", session_get)
    monkeypatch.setattr(CredentialSessionClient, "status", lambda self: {"status": "UNLOCKED"})
    encrypted = EncryptedFileCredentialStore(tmp_path / "unused.enc")
    monkeypatch.setattr(
        encrypted, "_derive", lambda *_args, **_kwargs: pytest.fail("session hit must not run Argon2id")
    )
    resolver = CredentialResolver(None, encrypted, fallback=None, mode="encrypted")

    for index in range(500):
        assert resolver.get_credential(f"monitor-{index}") == credentials[f"monitor-{index}"]
        assert resolver.last_backend == "Encrypted Credential Session"


def test_current_platform_ipc_lifecycle(tmp_path: Path, isolated_credential_ipc: str) -> None:
    client = CredentialSessionClient()
    assert client.status().get("status") == "LOCKED"
    store_path = tmp_path / "encrypted-store-fingerprint.bin"
    store_path.write_bytes(b"non-secret integration fingerprint")
    value = Credential("session-test-user", "in-memory-session-test-secret")
    try:
        opened = client.load({"session-test-alias": value}, MIN_TTL_SECONDS, store_path, b"k" * 32)
        assert opened.get("status") == "UNLOCKED"
        assert client.get("session-test-alias", store_path) == value
        child = subprocess.run(
            [sys.executable, "-c",
             "import sys; from pathlib import Path; import dba.credential_session as s; "
             "s._endpoint=lambda: sys.argv[2]; from dba.credential_session import CredentialSessionClient; "
             "value = CredentialSessionClient().get('session-test-alias', Path(sys.argv[1])); "
             "assert value is not None and value.username == 'session-test-user'; "
             "print('SESSION_LOOKUP_OK')", str(store_path), isolated_credential_ipc],
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
def test_legacy_active_session_rejects_writes_without_invalidating_reads(
    tmp_path: Path, isolated_credential_ipc: str,
) -> None:
    from legacy_credential_fixture import write_legacy_store
    client = CredentialSessionClient()
    assert client.status().get("status") == "LOCKED"
    store_path = tmp_path / "credentials.enc"
    master = "test-master-password-long-enough"
    write_legacy_store(store_path, master, {"existing": Credential("monitor", "<TEST_ONLY>")})
    original = store_path.read_bytes()
    store = EncryptedFileCredentialStore(store_path, password_provider=lambda _: master)
    _, values = store._read_container()
    client.load(values, MIN_TTL_SECONDS, store_path, store.session_key())
    store.lock()
    for operation in (
        lambda: client.save("fixture-only", Credential("monitor", "<TEST_ONLY>"), store_path),
        lambda: client.delete("existing", store_path),
    ):
        with pytest.raises(ToolkitError) as error:
            operation()
        assert error.value.error_type == "CredentialStoreReadOnly"
        assert "migrate" in str(error.value)
        assert store_path.read_bytes() == original
    assert client.get("existing", store_path) == Credential("monitor", "<TEST_ONLY>")
    assert client.list_metadata(store_path) == [{"alias": "existing", "username": "monitor"}]
    assert client.get("fixture-only", store_path) is None
