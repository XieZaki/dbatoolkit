"""Opt-in SQLite encrypted credentials; never auto-detect or migrate a Store."""
from __future__ import annotations

import base64
import ctypes
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

from dba.credentials import Credential, CredentialStore, validate_alias
from dba.encrypted_credentials import (
    MIN_MASTER_PASSWORD_LENGTH,
    CredentialKdfPolicy,
    EncryptedFileCredentialStore,
    default_store_path,
)
from dba.errors import ToolkitError

FORMAT = "dba-sqlite-credentials"
VERSION = 1
MAX_PAYLOAD = 4 * 1024 * 1024


def _failure(kind: str) -> ToolkitError:
    messages = {
        "CredentialStoreChanged": "Credential session does not match the target Store. Unlock the target again.",
        "CredentialStoreCorrupted": "Encrypted SQLite Store is damaged or payload authentication failed.",
        "CredentialStoreUnlockFailed": "Master password or key confirmation authentication failed.",
        "CredentialStoreBusy": "Credential Store is busy; no update was committed.",
        "CredentialStoreUnsupported": "Credential Store format or version is unsupported.",
        "CredentialStoreUnsafe": "Credential Store path or permissions are unsafe.",
    }
    return ToolkitError(kind, messages[kind])


def _windows_acl(
    path: Path, *, initialize: bool = False, allow_default_admin_owner: bool = False,
    repair_created_owner: bool = False,
) -> None:
    """Private DACL: only current user and SYSTEM; directory ACEs inherit to journals."""
    from ctypes import wintypes

    from dba.credential_session import _windows_identity

    sid, _ = _windows_identity()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if initialize:
        descriptor = ctypes.c_void_p()
        flags = "OICI" if path.is_dir() else ""
        # Some Windows service/admin tokens default newly created objects to the
        # Administrators owner.  Initialization must establish the current user
        # as owner as well as applying the private DACL; validation below then
        # verifies both properties without widening the accepted owner set.
        sddl = f"O:{sid}D:P(A;{flags};FA;;;{sid})(A;{flags};FA;;;SY)"
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise _failure("CredentialStoreUnsafe")
        try:
            if not advapi.SetFileSecurityW(str(path), 0x80000005, descriptor):
                raise _failure("CredentialStoreUnsafe")
        finally:
            kernel.LocalFree(descriptor)
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    owner_value = ""
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                            ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
    if advapi.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None,
                                   ctypes.byref(descriptor)) != 0:
        raise _failure("CredentialStoreUnsafe")
    try:
        if not dacl.value:
            raise _failure("CredentialStoreUnsafe")
        # ACL header: revision, reserved, size, ACE count, reserved.
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        if not count:
            raise _failure("CredentialStoreUnsafe")
        advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        owner_text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(owner, ctypes.byref(owner_text)):
            raise _failure("CredentialStoreUnsafe")
        try:
            owner_value = owner_text.value or ""
        finally:
            kernel.LocalFree(owner_text)
        for index in range(count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                raise _failure("CredentialStoreUnsafe")
            if ctypes.c_ubyte.from_address(ace.value).value != 0:
                raise _failure("CredentialStoreUnsafe")
            trustee = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(ace.value + 8, ctypes.byref(trustee)):
                raise _failure("CredentialStoreUnsafe")
            try:
                if trustee.value not in {sid, "S-1-5-18"}:
                    raise _failure("CredentialStoreUnsafe")
            finally:
                kernel.LocalFree(trustee)
    finally:
        kernel.LocalFree(descriptor)
    if owner_value not in {sid, "S-1-5-18"}:
        # SQLite creates rollback journals with the token's default Owner.
        # Hosted Windows runners use the Administrators group as that Owner.
        # While an existing journal is awaiting SQLite recovery/locking, allow
        # only this known transient Owner after the DACL has been validated.
        # The transaction owner repairs it before commit; persisted Store files
        # continue to require current-user/SYSTEM ownership.
        if owner_value != "S-1-5-32-544":
            raise _failure("CredentialStoreUnsafe")
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.IsUserAnAdmin.restype = ctypes.c_int
        if not shell.IsUserAnAdmin():
            raise _failure("CredentialStoreUnsafe")
        if repair_created_owner:
            _windows_acl(path, initialize=True)
        elif not allow_default_admin_owner:
            raise _failure("CredentialStoreUnsafe")


class SQLiteCredentialStore(EncryptedFileCredentialStore):
    """AES-GCM/Argon2id, DELETE rollback journal, synchronous EXTRA, explicit opt-in.

    Windows file sharing pins the main file during a transaction. It does not
    protect against arbitrary same-user writes, memory inspection or rollback.
    """

    def __init__(self, path: Path | None = None,
                 password_provider: Callable[[str], str] | None = None) -> None:
        super().__init__(path or default_store_path().parent / "sqlite" / "credentials.db", password_provider)
        self.path = self.path.absolute()
        self.binding: tuple[int, int, str] | None = None
        self._migration_allowed = False

    @property
    def backend_name(self) -> str:
        return "Encrypted SQLite Store"

    def _paths(self) -> None:
        if os.name != "nt":
            raise ToolkitError("CredentialBackendUnsupported", "SQLite credential file binding currently requires Windows.")
        super()._check_location()
        for part in (self.path, *self.path.parents):
            if part.exists() and (part.is_symlink() or part.stat().st_file_attributes & 0x400):
                raise _failure("CredentialStoreUnsafe")
        _windows_acl(self.path.parent)
        if self.path.exists():
            _windows_acl(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                if sidecar.is_symlink() or sidecar.stat().st_file_attributes & 0x400:
                    raise _failure("CredentialStoreUnsafe")
                try:
                    _windows_acl(sidecar, allow_default_admin_owner=suffix == "-journal")
                except ToolkitError:
                    if sidecar.exists():
                        raise

    @contextmanager
    def _connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        self._require_crypto()
        if not self.path.exists():
            raise ToolkitError("CredentialStoreNotInitialized", "Initialize the selected SQLite Store explicitly.")
        self._paths()
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 3, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise _failure("CredentialStoreBusy" if ctypes.get_last_error() in {32, 33} else "CredentialStoreChanged")
        try:
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        except OSError:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            raise _failure("CredentialStoreChanged") from None
        stream: BinaryIO = os.fdopen(fd, "rb")
        with stream:
            observed = os.fstat(stream.fileno())
            if self.binding is not None and (observed.st_dev, observed.st_ino) != self.binding[:2]:
                raise _failure("CredentialStoreChanged")
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=3)
                connection.execute("PRAGMA trusted_schema=OFF")
                if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise _failure("CredentialStoreUnsupported")
                connection.execute("PRAGMA synchronous=EXTRA")
                if connection.execute("PRAGMA synchronous").fetchone()[0] != 3:
                    raise _failure("CredentialStoreUnsupported")
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                journal = Path(str(self.path) + "-journal")
                if write and journal.exists():
                    _windows_acl(journal, repair_created_owner=True)
                connection.commit()
            except sqlite3.Error as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 255
                raise _failure("CredentialStoreBusy" if code in {5, 6} else "CredentialStoreCorrupted") from None
            finally:
                if connection is not None:
                    connection.close()

    def _row(self, connection: sqlite3.Connection) -> tuple[dict[str, Any], int, bytes, bytes]:
        row = connection.execute("SELECT substr(header,1,4097),generation,substr(nonce,1,13),substr(payload,1,?),length(payload) "
                                 "FROM credential_store WHERE id=1", (MAX_PAYLOAD + 1,)).fetchone()
        try:
            if row is None or len(row[0]) > 4096 or row[4] > MAX_PAYLOAD:
                raise ValueError
            header = json.loads(row[0])
            if header["format"] != FORMAT or type(header["version"]) is not int or header["version"] != VERSION:
                raise _failure("CredentialStoreUnsupported")
            if set(header) != {"format", "version", "uuid", "salt", "kdf", "verifier", "state"}:
                raise ValueError
            if (not isinstance(header["uuid"], str) or not isinstance(header["salt"], str)
                    or not isinstance(header["verifier"], str) or len(header["verifier"]) != 64):
                raise ValueError
            if header["state"] not in {"ready", "migration"}:
                raise ValueError
            if header["state"] == "migration" and not self._migration_allowed:
                raise ToolkitError("CredentialMigrationIncomplete", "Migration incomplete; target remains quarantined. Original Store is unchanged.")
            if uuid.UUID(header["uuid"]).hex != header["uuid"]:
                raise ValueError
            params = header["kdf"]
            if (not isinstance(params, dict) or set(params) != {"memory_cost_kib", "time_cost", "parallelism"}
                    or any(type(v) is not int for v in params.values())
                    or tuple(params[k] for k in ("memory_cost_kib", "time_cost", "parallelism"))
                    not in CredentialKdfPolicy.supported_profiles):
                raise _failure("CredentialStoreUnsupported")
            if len(base64.b64decode(header["salt"], validate=True)) != 16:
                raise ValueError
            if (type(row[1]) is not int or row[1] < 0 or not isinstance(row[2], bytes)
                    or not isinstance(row[3], bytes) or len(row[2]) != 12 or len(row[3]) < 16):
                raise ValueError
            if self.binding is not None and header["uuid"] != self.binding[2]:
                raise _failure("CredentialStoreChanged")
            return header, row[1], row[2], row[3]
        except (TypeError, ValueError, KeyError):
            raise _failure("CredentialStoreCorrupted") from None

    def _decrypt(self, row: tuple[dict[str, Any], int, bytes, bytes], key: bytes) -> dict[str, Credential]:
        header, generation, nonce, payload = row
        confirmation = hmac.digest(key, (FORMAT + header["uuid"]).encode(), "sha256").hex()
        if not secrets.compare_digest(confirmation, header["verifier"]):
            raise _failure("CredentialStoreUnlockFailed")
        AESGCM, _, _ = self._crypto()
        try:
            values = json.loads(AESGCM(key).decrypt(nonce, payload, self._canonical([header, generation])))
            if not isinstance(values, dict):
                raise ValueError
            result = {}
            for alias, value in values.items():
                validate_alias(alias)
                if (not isinstance(value, dict) or set(value) != {"username", "password"}
                        or any(not isinstance(v, str) for v in value.values())):
                    raise ValueError
                result[alias] = Credential(value["username"], value["password"])
            return result
        except Exception:
            raise _failure("CredentialStoreCorrupted") from None

    def _encode_row(self, values: dict[str, Credential], header: dict[str, Any],
                    generation: int, key: bytes) -> tuple[bytes, bytes]:
        for alias, credential in values.items():
            validate_alias(alias)
            if not isinstance(credential.username, str) or not isinstance(credential.password, str):
                raise ToolkitError("CredentialStoreFailed", "Credential schema is invalid.")
        plaintext = self._canonical({k: {"username": v.username, "password": v.password} for k, v in values.items()})
        if len(plaintext) > MAX_PAYLOAD - 16:
            raise ToolkitError("CredentialStoreLimit", "Credential Store payload exceeds its size limit.")
        nonce = secrets.token_bytes(12)
        AESGCM, _, _ = self._crypto()
        return nonce, cast(bytes, AESGCM(key).encrypt(nonce, plaintext, self._canonical([header, generation])))

    def _read_container(self) -> tuple[dict[str, Any], dict[str, Credential]]:
        with self._connection() as connection:
            row = self._row(connection)
            header = row[0]
            key = self._session_key
            if key is None:
                password = self._password_provider("Unlock encrypted SQLite Store: ")
                key = self._derive(password, base64.b64decode(header["salt"]), header["kdf"])
            values = self._decrypt(row, key)
            observed = self.path.stat()
            if self.binding is None:
                self.binding = observed.st_dev, observed.st_ino, header["uuid"]
            self._session_key = key
            return header, values

    def initialize(self, password: str, *, migration_pending: bool = False) -> None:
        self._require_crypto()
        if os.name != "nt":
            raise ToolkitError("CredentialBackendUnsupported", "SQLite credentials currently require Windows.")
        if len(password) < MIN_MASTER_PASSWORD_LENGTH:
            raise ToolkitError("WeakMasterPassword", "Master password must be at least 12 characters.")
        if self.path.exists():
            raise ToolkitError("CredentialStoreExists", "Destination already exists; it was not changed.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if any(self.path.parent.iterdir()):
            raise _failure("CredentialStoreUnsafe")
        _windows_acl(self.path.parent, initialize=True)
        self._paths()
        try:
            with self.path.open("xb"):
                pass
        except FileExistsError:
            raise ToolkitError("CredentialStoreExists", "Destination already exists; it was not changed.") from None
        _windows_acl(self.path, initialize=True)
        header: dict[str, Any] = {"format": FORMAT, "version": VERSION, "uuid": uuid.uuid4().hex,
                                 "state": "migration" if migration_pending else "ready",
                                 "salt": base64.b64encode(secrets.token_bytes(16)).decode(),
                                 "kdf": {"memory_cost_kib": 65536, "time_cost": 3, "parallelism": 1}}
        key = self._derive(password, base64.b64decode(header["salt"]), header["kdf"])
        header["verifier"] = hmac.digest(key, (FORMAT + header["uuid"]).encode(), "sha256").hex()
        nonce, payload = self._encode_row({}, header, 0, key)
        with self._connection(True) as connection:
            connection.execute("CREATE TABLE credential_store (id INTEGER PRIMARY KEY CHECK(id=1), "
                               "header TEXT NOT NULL,generation INTEGER NOT NULL,nonce BLOB NOT NULL,payload BLOB NOT NULL)")
            connection.execute("INSERT INTO credential_store VALUES(1,?,0,?,?)",
                               (self._canonical(header).decode(), nonce, payload))
        self._session_key = key
        self._migration_allowed = migration_pending
        self._read_container()

    def session_operation(self, key: bytes, binding: tuple[int, int, str],
                          change: Callable[[dict[str, Credential]], dict[str, Credential]] | None = None
                          ) -> dict[str, Credential]:
        self.binding = binding
        with self._connection(change is not None) as connection:
            row = self._row(connection)
            values = self._decrypt(row, key)
            if change is not None:
                values = change(values)
                nonce, payload = self._encode_row(values, row[0], row[1] + 1, key)
                connection.execute("UPDATE credential_store SET generation=?,nonce=?,payload=? WHERE id=1",
                                   (row[1] + 1, nonce, payload))
            return values

    def _change(self, change: Callable[[dict[str, Credential]], dict[str, Credential]]) -> None:
        self._read_container()
        assert self._session_key is not None and self.binding is not None
        self.session_operation(self._session_key, self.binding, change)

    def get(self, alias: str) -> Credential | None:
        return self._read_container()[1].get(validate_alias(alias))

    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted(self._read_container()[1]))

    def set(self, alias: str, credential: Credential) -> None:
        checked = validate_alias(alias)
        self._change(lambda values: {**values, checked: credential})

    def set_if_absent(self, alias: str, credential: Credential) -> None:
        checked = validate_alias(alias)
        def add(values: dict[str, Credential]) -> dict[str, Credential]:
            if checked in values:
                raise ToolkitError("CredentialAliasExists", "Credential alias already exists.")
            return {**values, checked: credential}
        self._change(add)

    def delete(self, alias: str) -> bool:
        checked = validate_alias(alias)
        existed = False
        def remove(values: dict[str, Credential]) -> dict[str, Credential]:
            nonlocal existed
            existed = checked in values
            return {k: v for k, v in values.items() if k != checked}
        self._change(remove)
        return existed

    def rekey(self, old_password: str, new_password: str) -> None:
        if len(new_password) < MIN_MASTER_PASSWORD_LENGTH:
            raise ToolkitError("WeakMasterPassword", "Master password must be at least 12 characters.")
        self.lock()
        with self._connection(True) as connection:
            row = self._row(connection)
            header = dict(row[0])
            old_key = self._derive(old_password, base64.b64decode(header["salt"]), header["kdf"])
            values = self._decrypt(row, old_key)
            header["salt"] = base64.b64encode(secrets.token_bytes(16)).decode()
            key = self._derive(new_password, base64.b64decode(header["salt"]), header["kdf"])
            header["verifier"] = hmac.digest(key, (FORMAT + header["uuid"]).encode(), "sha256").hex()
            nonce, payload = self._encode_row(values, header, row[1] + 1, key)
            connection.execute("UPDATE credential_store SET header=?,generation=?,nonce=?,payload=? WHERE id=1",
                               (self._canonical(header).decode(), row[1] + 1, nonce, payload))
        self.lock()


def migrate_store(source: EncryptedFileCredentialStore, target: SQLiteCredentialStore,
                  new_password: str) -> int:
    """Explicit copy, authenticate and compare. Old file and provider selection stay intact."""
    if os.name != "nt":
        raise ToolkitError("CredentialBackendUnsupported", "SQLite credential migration currently requires Windows.")
    if target.path.exists():
        raise ToolkitError("CredentialStoreExists", "Migration destination already exists.")
    from dba.credential_session import _windows_store_snapshot

    source_stream, _, _ = _windows_store_snapshot(str(source.path))
    try:
        source_raw = source_stream.read()
        _, values = source._read_container()
        target.initialize(new_password, migration_pending=True)
        target._change(lambda _current: dict(values))
        target.lock()
        # Readback with the supplied new password, not a retained in-memory data key.
        verifier = SQLiteCredentialStore(target.path, lambda _label: new_password)
        verifier._migration_allowed = True
        try:
            if verifier._read_container()[1] != values or source.path.read_bytes() != source_raw:
                raise ToolkitError("CredentialMigrationFailed", "Migration validation failed; preserve both files for review.")
            # Publish readiness by a transaction on the pinned, verified target.
            # There is no path rename or replace after verification.
            with verifier._connection(True) as connection:
                row = verifier._row(connection)
                key = verifier.session_key()
                if verifier._decrypt(row, key) != values:
                    raise ToolkitError("CredentialMigrationFailed", "Migration validation failed; target stays quarantined.")
                header = {**row[0], "state": "ready"}
                nonce, payload = verifier._encode_row(values, header, row[1] + 1, key)
                connection.execute("UPDATE credential_store SET header=?,generation=?,nonce=?,payload=? WHERE id=1",
                                   (verifier._canonical(header).decode(), row[1] + 1, nonce, payload))
        finally:
            verifier.lock()
        return len(values)
    finally:
        source.lock()
        target.lock()
        target._migration_allowed = False
        source_stream.close()
    # On failure the opt-in destination remains quarantined by private ACLs;
    # never delete a path that an external process might have replaced.


class SQLiteSessionStore(CredentialStore):
    """Existing CLI operations delegated to the bound Session; no local key cache."""
    def __init__(self, path: Path, client: Any) -> None:
        self.path, self.client = path, client

    @property
    def backend_name(self) -> str:
        return "Encrypted SQLite Store"

    def available(self) -> bool:
        return True

    def get(self, alias: str) -> Credential | None:
        return cast(Credential | None, self.client.get(alias, self.path))

    def aliases(self) -> tuple[str, ...]:
        rows = self.client.list_metadata(self.path)
        if rows is None:
            raise ToolkitError("CredentialSessionUnavailable", "Credential metadata is unavailable.")
        return tuple(item["alias"] for item in rows)

    def set(self, alias: str, credential: Credential) -> None:
        from dba.credential_session import _remote_error

        response = self.client.request("SET", alias=validate_alias(alias), store_path=str(self.path),
                                       credential={"username": credential.username, "password": credential.password})
        _remote_error(response)
        if response is None or response.get("status") != "SAVED":
            raise ToolkitError("CredentialStoreFailed", "Credential update rejected; unlock the target again.")

    def delete(self, alias: str) -> bool:
        self.client.delete(alias, self.path)
        return True
