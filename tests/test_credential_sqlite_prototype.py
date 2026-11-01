from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import credential_sqlite_prototype as prototype
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only transaction experiment")
KEY = b"T" * 32  # Deliberate disposable test key, never used outside tmp_path.


def worker(path: Path, binding: prototype.Binding, alias: str, crash: str | None = None) -> subprocess.Popen[str]:
    child = subprocess.Popen([sys.executable, str(Path(prototype.__file__)), str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert child.stdin is not None
    child.stdin.write(json.dumps({"binding": asdict(binding), "key": KEY.hex(),
                                 "alias": alias, "value": "fixture-" + alias, "crash": crash}))
    child.stdin.close()
    return child


def finish(child: subprocess.Popen[str], expected: int = 0) -> None:
    try:
        assert child.wait(timeout=15) == expected
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        for stream in (child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def test_sqlite_normal_save_get_and_binding_survives_commits(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    binding = prototype.create(path, KEY)
    store = prototype.TransactionStore(path, binding, KEY)
    assert store.operation("first", "fixture-first") == {"first": "fixture-first"}
    store.operation("second", "fixture-second")
    assert store.operation() == {"first": "fixture-first", "second": "fixture-second"}
    assert b"fixture-first" not in path.read_bytes()


def test_sqlite_cross_store_save_rejected_without_modification(tmp_path: Path) -> None:
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    binding = prototype.create(first, KEY)
    prototype.create(second, KEY)
    before = second.read_bytes()
    with pytest.raises(prototype.Rejected):
        prototype.TransactionStore(second, binding, KEY).operation("bad", "fixture")
    assert second.read_bytes() == before


def test_sqlite_multiple_process_saves_preserve_all_updates(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    binding = prototype.create(path, KEY)
    children = [worker(path, binding, f"alias-{index}") for index in range(4)]
    for child in children:
        finish(child)
    values = prototype.TransactionStore(path, binding, KEY).operation()
    assert values == {f"alias-{index}": f"fixture-alias-{index}" for index in range(4)}


def test_sqlite_external_replacement_blocked_during_operation_and_rejected_after(tmp_path: Path) -> None:
    path, replacement = tmp_path / "store.db", tmp_path / "replacement.db"
    binding = prototype.create(path, KEY)
    prototype.create(replacement, KEY)
    before = replacement.read_bytes()
    def attempt_replace() -> None:
        child = subprocess.run([sys.executable, "-c", "import os,sys; os.replace(sys.argv[1],sys.argv[2])",
                                str(replacement), str(path)], capture_output=True, timeout=5, check=False)
        assert child.returncode != 0
    store = prototype.TransactionStore(path, binding, KEY)
    store.operation("saved", "fixture-saved", probe=attempt_replace)
    assert store.operation() == {"saved": "fixture-saved"}
    os.replace(replacement, path)
    with pytest.raises(prototype.Rejected):
        prototype.TransactionStore(path, binding, KEY).operation("bad", "fixture")
    assert path.read_bytes() == before


@pytest.mark.parametrize(("crash", "code", "present"), [("before_commit", 73, False), ("after_commit", 74, True)])
def test_sqlite_abrupt_exit_recovery(tmp_path: Path, crash: str, code: int, present: bool) -> None:
    path = tmp_path / "store.db"
    binding = prototype.create(path, KEY)
    store = prototype.TransactionStore(path, binding, KEY)
    store.operation("existing", "fixture-original-" + "x" * 200000)
    finish(worker(path, binding, "new", crash), code)
    if crash == "before_commit":
        assert path.with_name(path.name + "-journal").exists()
    recovered = store.operation()
    assert recovered["existing"] == "fixture-original-" + "x" * 200000
    assert ("new" in recovered) is present
    store.operation("after-recovery", "fixture-recovered")
    assert store.operation()["after-recovery"] == "fixture-recovered"


@pytest.mark.parametrize("damage", ["wrong_key", "ciphertext", "truncated"])
def test_sqlite_authentication_and_incomplete_file_fail_closed(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "store.db"
    binding = prototype.create(path, KEY)
    store = prototype.TransactionStore(path, binding, KEY)
    store.operation("existing", "fixture-secret")
    if damage == "ciphertext":
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE store SET payload=zeroblob(length(payload))")
    elif damage == "truncated":
        with path.open("r+b") as stream:
            stream.truncate(64)
    else:
        store = prototype.TransactionStore(path, binding, b"W" * 32)
    damaged = path.read_bytes()
    with pytest.raises(prototype.Rejected):
        store.operation("must-not-save", "fixture")
    assert path.read_bytes() == damaged
