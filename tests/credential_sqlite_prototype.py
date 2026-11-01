"""Disposable transaction experiment. Not a production provider or a migration tool.

SQLite handles cooperating writers and crash recovery. A Windows handle denies
rename/delete while an operation is open. Same-user arbitrary writes, process
inspection and rollback attacks are outside this experiment's protection.
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import sqlite3
import sys
import uuid
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class Binding:
    identity: str
    device: int
    inode: int


class Rejected(Exception):
    pass


@contextmanager
def guard(path: Path, binding: Binding) -> Iterator[None]:
    """Pin the file name on Windows; SQLite separately locks transaction pages."""
    if os.name != "nt":
        raise Rejected("This prototype only evaluates Windows sharing semantics")
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise Rejected("Store unavailable")
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
    except OSError:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise Rejected("Store unavailable") from None
    with os.fdopen(fd, "rb") as stream:
        observed = os.fstat(stream.fileno())
        if (observed.st_dev, observed.st_ino) != (binding.device, binding.inode):
            raise Rejected("Store identity mismatch")
        yield


def aad(identity: str, generation: int) -> bytes:
    return f"dba-sqlite-experiment-v1:{identity}:{generation}".encode("ascii")


def encode(key: bytes, identity: str, generation: int, values: dict[str, str]) -> tuple[bytes, bytes]:
    nonce = secrets.token_bytes(12)
    return nonce, AESGCM(key).encrypt(nonce, json.dumps(values).encode(), aad(identity, generation))


def create(path: Path, key: bytes) -> Binding:
    # Caller supplies a new disposable path. Never overwrite an existing file.
    with path.open("xb"):
        pass
    identity = uuid.uuid4().hex
    nonce, payload = encode(key, identity, 0, {})
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE store (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT, "
                           "generation INTEGER, nonce BLOB, payload BLOB)")
        connection.execute("INSERT INTO store VALUES (1, ?, 0, ?, ?)", (identity, nonce, payload))
    observed = path.stat()
    return Binding(identity, observed.st_dev, observed.st_ino)


class TransactionStore:
    def __init__(self, path: Path, binding: Binding, key: bytes) -> None:
        self.path, self.binding, self.key = path, binding, key

    def operation(self, alias: str | None = None, value: str | None = None,
                  crash: str | None = None, probe: Callable[[], None] | None = None) -> dict[str, str]:
        try:
            with guard(self.path, self.binding):
                connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=3)
                try:
                    connection.execute("PRAGMA synchronous=FULL")
                    if crash is not None:
                        # Force dirty-page spill so abrupt-exit tests exercise a hot journal.
                        connection.execute("PRAGMA cache_size=8")
                        connection.execute("PRAGMA cache_spill=ON")
                    connection.execute("BEGIN IMMEDIATE" if alias is not None else "BEGIN")
                    row = connection.execute("SELECT identity, generation, nonce, payload FROM store WHERE id=1").fetchone()
                    if row is None or row[0] != self.binding.identity:
                        raise Rejected("Store identity mismatch")
                    identity, generation, nonce, encrypted = row
                    values = json.loads(AESGCM(self.key).decrypt(nonce, encrypted, aad(identity, generation)))
                    if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                           for k, v in values.items()):
                        raise Rejected("Store authentication failed")
                    if alias is not None:
                        if value is None:
                            raise Rejected("Value required")
                        if alias in values:
                            raise Rejected("Alias exists")
                        values[alias] = value
                        nonce, encrypted = encode(self.key, identity, generation + 1, values)
                        connection.execute("UPDATE store SET generation=?, nonce=?, payload=? WHERE id=1",
                                           (generation + 1, nonce, encrypted))
                        if probe is not None:
                            probe()
                        if crash == "before_commit":
                            os._exit(73)
                    connection.commit()
                    if crash == "after_commit":
                        os._exit(74)
                    return values
                finally:
                    connection.close()
        except Rejected:
            raise
        except Exception:
            raise Rejected("Store authentication or transaction failed") from None


if __name__ == "__main__":
    # Only test values pass over stdin; no keys in argv or environment.
    request: dict[str, Any] = json.load(sys.stdin)
    store = TransactionStore(Path(sys.argv[1]), Binding(**request["binding"]), bytes.fromhex(request["key"]))
    store.operation(request["alias"], request["value"], request.get("crash"))
