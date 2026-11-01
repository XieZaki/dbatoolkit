"""Create disposable historical-format inputs; never a product write capability."""
from __future__ import annotations

import os
from pathlib import Path

from dba.credentials import Credential
from dba.encrypted_credentials import EncryptedFileCredentialStore


def write_legacy_store(path: Path, password: str, values: dict[str, Credential] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    # Pure encoding preserves historical cryptographic coverage. Product mutators
    # remain denied; fixture construction writes bytes only in pytest tmp_path.
    encoded = EncryptedFileCredentialStore(path)._encode(values or {}, password)
    with path.open("xb") as stream:
        stream.write(encoded)
    if os.name != "nt":
        os.chmod(path, 0o600)
