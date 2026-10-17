"""Versioned, authenticated local credential storage.

The master password is requested at runtime and is never persisted. This module
uses maintained crypto libraries; it does not implement cryptographic primitives.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import importlib.util
import json
import os
import secrets
import stat
import sys
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from dba.credential_session import CredentialSessionClient
from dba.credentials import (
    Credential,
    CredentialProvider,
    CredentialStore,
    confirm_ephemeral_credential,
    confirm_save_credential,
    prompt_jit_credential,
    validate_alias,
)
from dba.errors import ToolkitError

FORMAT = "dba-toolkit-credential-store"
FORMAT_VERSION = 1
CIPHER_NAME = "aes-256-gcm"
KDF_NAME = "argon2id"
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
MIN_MASTER_PASSWORD_LENGTH = 12


class CredentialKdfPolicy:
    """Central Argon2id policy. memory_cost is expressed in KiB by argon2-cffi."""

    memory_cost_kib = 64 * 1024
    time_cost = 3
    parallelism = 1
    supported_profiles = ((64 * 1024, 3, 1),)


def posix_mode_is_private(mode: int) -> bool:
    return stat.S_IMODE(mode) & 0o077 == 0


def default_store_path(environ: dict[str, str] | None = None,
                       platform_name: str | None = None,
                       home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    platform = os.name if platform_name is None else platform_name
    user_home = Path.home() if home is None else home
    if platform == "nt":
        root = env.get("APPDATA") or str(user_home / "AppData" / "Roaming")
        return Path(root) / "DBAToolkit" / "credentials.enc"
    root = env.get("XDG_CONFIG_HOME") or str(user_home / ".config")
    return Path(root) / "dba-toolkit" / "credentials.enc"


def crypto_available() -> bool:
    try:
        return (importlib.util.find_spec("argon2.low_level") is not None
                and importlib.util.find_spec("cryptography.hazmat.primitives.ciphers.aead") is not None)
    except (ImportError, ValueError):
        return False


def prompt_master_password(label: str = "Unlock encrypted credential store: ") -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ToolkitError("CredentialUnavailable", "Interactive terminal required to unlock credential store.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(label)
    except (EOFError, OSError, getpass.GetPassWarning):
        raise ToolkitError("CredentialUnavailable", "Secure master password input unavailable.") from None


def prompt_new_master_password() -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ToolkitError("CredentialUnavailable", "Interactive terminal required to initialize encrypted store.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass("Create master password (12+ characters; prefer a long passphrase): ")
            second = getpass.getpass("Confirm master password: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise ToolkitError("CredentialUnavailable", "Secure master password input unavailable.") from None
    if first != second:
        raise ToolkitError("CredentialMismatch", "Master password confirmation does not match.")
    if len(first) < MIN_MASTER_PASSWORD_LENGTH:
        raise ToolkitError("WeakMasterPassword", "Master password must be at least 12 characters.")
    return first


class _StoreLock:
    """Small cross-platform advisory lock. All Toolkit writers honor this lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> _StoreLock:
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            if not parent_existed:
                os.chmod(self.path.parent, 0o700)
            elif not posix_mode_is_private(self.path.parent.stat().st_mode):
                raise ToolkitError("CredentialStoreUnsafe", "Credential Store Permission Unsafe. Set directory mode 0700.")
        lock_existed = self.path.exists()
        self.handle = self.path.open("a+b")
        if os.name == "nt":
            import msvcrt

            if self.path.stat().st_size == 0:
                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.handle.close()
                self.handle = None
                raise ToolkitError("CredentialStoreBusy", "Credential store is being modified by another process.") from None
        else:
            import fcntl

            if lock_existed and not posix_mode_is_private(self.path.stat().st_mode):
                self.handle.close()
                self.handle = None
                raise ToolkitError("CredentialStoreUnsafe", "Credential Store Permission Unsafe. Set lock file mode 0600.")
            os.chmod(self.path, 0o600)
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            except OSError:
                self.handle.close()
                self.handle = None
                raise ToolkitError("CredentialStoreBusy", "Credential store is being modified by another process.") from None
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            self.handle.close()
            self.handle = None


class EncryptedFileCredentialStore(CredentialStore):
    """AES-256-GCM + Argon2id store in the current user's config directory."""

    def __init__(self, path: Path | None = None,
                 password_provider: Callable[[str], str] | None = None) -> None:
        self._default_location = path is None
        self.path = path or default_store_path()
        self._password_provider = password_provider or prompt_master_password
        self._unlocked: dict[str, Credential] | None = None
        self._cached_master: str | None = None
        self._session_key: bytes | None = None

    @property
    def backend_name(self) -> str:
        return "Encrypted Local Store"

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def available(self) -> bool:
        return crypto_available()

    def permission_status(self) -> str:
        if os.name == "nt":
            return "BEST_EFFORT"
        try:
            if self.path.parent.exists() and not posix_mode_is_private(self.path.parent.stat().st_mode):
                return "UNSAFE"
            if self.path.exists() and not posix_mode_is_private(self.path.stat().st_mode):
                return "UNSAFE"
        except OSError:
            return "UNSAFE"
        return "SECURE" if self.path.exists() else "READY"

    def _crypto(self) -> tuple[Any, Any, Any]:
        self._require_crypto()
        try:
            from argon2.low_level import Type, hash_secret_raw
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise ToolkitError(
                "CredentialCryptoUnavailable",
                "Encrypted credential storage requires cryptography and argon2-cffi from the local wheelhouse.",
            ) from None
        return AESGCM, hash_secret_raw, Type

    @staticmethod
    def _require_crypto() -> None:
        if not crypto_available():
            raise ToolkitError(
                "CredentialCryptoUnavailable",
                "Encrypted credential storage requires cryptography and argon2-cffi from the local wheelhouse.",
            )

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _aad(header: dict[str, Any]) -> bytes:
        authenticated = {key: header[key] for key in ("format", "version", "kdf", "cipher", "salt")}
        return EncryptedFileCredentialStore._canonical(authenticated)

    def _derive(self, password: str, salt: bytes, params: dict[str, int]) -> bytes:
        AESGCM, hash_secret_raw, Type = self._crypto()
        del AESGCM
        try:
            return cast(bytes, hash_secret_raw(
                secret=password.encode("utf-8"), salt=salt, time_cost=params["time_cost"],
                memory_cost=params["memory_cost_kib"], parallelism=params["parallelism"],
                hash_len=KEY_BYTES, type=Type.ID,
            ))
        except Exception:
            raise ToolkitError("CredentialStoreUnlockFailed", self._unlock_message()) from None

    @staticmethod
    def _unlock_message() -> str:
        return ("Unable to unlock encrypted credential store. The master password may be incorrect "
                "or the store may be corrupted.")

    def _check_location(self) -> None:
        if self.path.is_symlink():
            raise ToolkitError("CredentialStoreUnsafe", "Credential Store Permission Unsafe: symbolic links are not accepted.")
        if os.name != "nt":
            status = self.permission_status()
            if status == "UNSAFE":
                raise ToolkitError(
                    "CredentialStoreUnsafe",
                    f"Credential Store Permission Unsafe. Set directory mode 0700 and file mode 0600: {self.path}",
                )
        else:
            try:
                resolved = self.path.resolve()
                public = Path(os.environ.get("PUBLIC", "C:/Users/Public")).resolve()
                resolved.relative_to(public)
                raise ToolkitError("CredentialStoreUnsafe", "Credential store cannot be located in a public directory.")
            except ValueError:
                pass
            except OSError:
                raise ToolkitError("CredentialStoreUnsafe", "Credential store path cannot be verified.") from None
        if self._default_location:
            repository = Path(__file__).resolve().parents[1]
            try:
                self.path.resolve().relative_to(repository)
            except ValueError:
                return
            except OSError:
                raise ToolkitError("CredentialStoreUnsafe", "Credential store path cannot be verified.") from None
            raise ToolkitError("CredentialStoreUnsafe", "Encrypted credential store cannot be located inside the Toolkit repository.")

    def _read_container(self) -> tuple[dict[str, Any], dict[str, Credential]]:
        self._require_crypto()
        self._check_location()
        try:
            raw = self.path.read_bytes()
            header = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.") from None
        if not isinstance(header, dict):
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.")
        if self._canonical(header) != raw:
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.")
        if header.get("format") != FORMAT or header.get("version") != FORMAT_VERSION:
            raise ToolkitError("CredentialStoreUnsupported", "Credential store format or version is unsupported; the file was not changed.")
        if set(header) != {
            "format", "version", "kdf", "cipher", "salt", "nonce", "ciphertext"
        }:
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.")
        kdf, cipher = header.get("kdf"), header.get("cipher")
        if not isinstance(kdf, dict) or set(kdf) != {"name", "memory_cost_kib", "time_cost", "parallelism"}:
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.")
        if kdf.get("name") != KDF_NAME:
            raise ToolkitError("CredentialStoreUnsupported", "Credential store KDF is unsupported; the file was not changed.")
        if cipher != {"name": CIPHER_NAME}:
            raise ToolkitError("CredentialStoreUnsupported", "Credential store cipher is unsupported; the file was not changed.")
        params = {key: kdf[key] for key in ("memory_cost_kib", "time_cost", "parallelism")}
        profile = (params["memory_cost_kib"], params["time_cost"], params["parallelism"])
        if (any(type(value) is not int for value in params.values())
                or profile not in CredentialKdfPolicy.supported_profiles):
            raise ToolkitError("CredentialStoreUnsupported", "Credential store KDF parameters are outside supported safety limits.")
        try:
            salt = base64.b64decode(header["salt"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
            ciphertext = base64.b64decode(header["ciphertext"], validate=True)
        except (TypeError, ValueError):
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.") from None
        if len(salt) < SALT_BYTES or len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
            raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed. Preserve a backup before recovery.")
        password = self._cached_master or self._password_provider("Unlock encrypted credential store: ")
        key = self._derive(password, salt, params)
        AESGCM, _, _ = self._crypto()
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, self._aad(header))
            payload = json.loads(plaintext)
            credentials = payload["credentials"]
            if (set(payload) != {"store_version", "credentials"}
                    or payload.get("store_version") != 1 or not isinstance(credentials, dict)):
                raise ValueError
            decoded: dict[str, Credential] = {}
            for alias, value in credentials.items():
                checked_alias = validate_alias(alias)
                if not isinstance(value, dict) or set(value) != {"username", "password"}:
                    raise ValueError
                if not isinstance(value["username"], str) or not isinstance(value["password"], str):
                    raise ValueError
                decoded[checked_alias] = Credential(value["username"], value["password"])
        except Exception:
            raise ToolkitError("CredentialStoreUnlockFailed", self._unlock_message()) from None
        self._cached_master = password
        self._unlocked = decoded
        self._session_key = key
        return header, decoded

    def session_key(self) -> bytes:
        """Return the in-memory data key after a successful store unlock."""
        if self._unlocked is None or self._session_key is None:
            self._read_container()
        if self._session_key is None:
            raise ToolkitError("CredentialStoreUnlockFailed", self._unlock_message())
        return self._session_key

    def persist_session_credentials(
        self, credentials: dict[str, Credential], key: bytes, expected_fingerprint: str
    ) -> str:
        """Atomically rewrite an already-unlocked store without retaining its master password."""
        self._require_crypto()
        self._check_location()
        with _StoreLock(self.path.with_suffix(self.path.suffix + ".lock")):
            try:
                raw = self.path.read_bytes()
                current = hashlib.sha256(raw).hexdigest()
            except OSError:
                raise ToolkitError("CredentialStoreFailed", "Encrypted credential store could not be verified.") from None
            if not secrets.compare_digest(current, expected_fingerprint):
                raise ToolkitError("CredentialStoreChanged", "Encrypted credential store changed during session save.")
            try:
                header = json.loads(raw)
                if (not isinstance(header, dict) or self._canonical(header) != raw
                        or header.get("format") != FORMAT or header.get("version") != FORMAT_VERSION):
                    raise ValueError
                AESGCM, _, _ = self._crypto()
                nonce = secrets.token_bytes(NONCE_BYTES)
                updated_header = dict(header)
                updated_header["nonce"] = base64.b64encode(nonce).decode("ascii")
                payload = self._canonical({
                    "store_version": 1,
                    "credentials": {alias: {"username": item.username, "password": item.password}
                                    for alias, item in credentials.items()},
                })
                updated_header["ciphertext"] = base64.b64encode(
                    AESGCM(key).encrypt(nonce, payload, self._aad(updated_header))
                ).decode("ascii")
                encoded = self._canonical(updated_header)
            except (KeyError, TypeError, ValueError):
                raise ToolkitError("CredentialStoreCorrupted", "Credential Store Corrupted or Authentication Failed.") from None
            self._atomic_write(encoded)
        self._unlocked = None
        self._cached_master = None
        self._session_key = None
        return hashlib.sha256(encoded).hexdigest()

    def _encode(self, credentials: dict[str, Credential], password: str) -> bytes:
        AESGCM, _, _ = self._crypto()
        salt, nonce = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(NONCE_BYTES)
        params = {"memory_cost_kib": CredentialKdfPolicy.memory_cost_kib,
                  "time_cost": CredentialKdfPolicy.time_cost,
                  "parallelism": CredentialKdfPolicy.parallelism}
        header: dict[str, Any] = {
            "format": FORMAT, "version": FORMAT_VERSION,
            "kdf": {"name": KDF_NAME, **params}, "cipher": {"name": CIPHER_NAME},
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        payload = self._canonical({
            "store_version": 1,
            "credentials": {alias: {"username": item.username, "password": item.password}
                            for alias, item in credentials.items()},
        })
        key = self._derive(password, salt, params)
        header["ciphertext"] = base64.b64encode(
            AESGCM(key).encrypt(nonce, payload, self._aad(header))
        ).decode("ascii")
        return self._canonical(header)

    def _atomic_write(self, encoded: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".credentials-", suffix=".tmp", dir=self.path.parent)
        temp = Path(temp_name)
        replaced = False
        try:
            if os.name != "nt":
                os.chmod(temp, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            replaced = True
            if os.name != "nt":
                os.chmod(self.path, 0o600)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            message = ("Credential store replacement completed but durability could not be confirmed. "
                       "Preserve the current file and verify it before retrying." if replaced else
                       "Credential store update failed; the existing store was preserved.")
            raise ToolkitError("CredentialStoreWriteFailed", message) from None

    def initialize(self, password: str) -> None:
        self._require_crypto()
        if len(password) < MIN_MASTER_PASSWORD_LENGTH:
            raise ToolkitError("WeakMasterPassword", "Master password must be at least 12 characters.")
        self._check_location()
        with _StoreLock(self.path.with_suffix(self.path.suffix + ".lock")):
            if self.path.exists():
                raise ToolkitError("CredentialStoreExists", "Credential store already exists; it was not overwritten.")
            self._atomic_write(self._encode({}, password))
        self._cached_master = password
        self._unlocked = {}

    def rekey(self, old_password: str, new_password: str) -> None:
        self._require_crypto()
        if len(new_password) < MIN_MASTER_PASSWORD_LENGTH:
            raise ToolkitError("WeakMasterPassword", "Master password must be at least 12 characters.")
        self._cached_master = old_password
        with _StoreLock(self.path.with_suffix(self.path.suffix + ".lock")):
            _, credentials = self._read_container()
            self._atomic_write(self._encode(credentials, new_password))
        self._cached_master = new_password
        self._unlocked = credentials.copy()

    def _write_changed(self, change: Callable[[dict[str, Credential]], dict[str, Credential]]) -> None:
        with _StoreLock(self.path.with_suffix(self.path.suffix + ".lock")):
            _, current = self._read_container()
            password = self._cached_master
            if password is None:
                raise ToolkitError("CredentialStoreUnlockFailed", self._unlock_message())
            updated = change(current)
            self._atomic_write(self._encode(updated, password))
        self._unlocked = updated.copy()

    def get(self, alias: str) -> Credential | None:
        self._require_crypto()
        checked = validate_alias(alias)
        if not self.path.exists():
            return None
        if self._unlocked is None:
            _, credentials = self._read_container()
        else:
            credentials = self._unlocked
        return credentials.get(checked)

    def set(self, alias: str, credential: Credential) -> None:
        self._require_crypto()
        checked = validate_alias(alias)
        if not self.path.exists():
            raise ToolkitError("CredentialStoreNotInitialized", "Run `dba credentials init --backend encrypted` first.")
        if self._unlocked is None:
            self._read_container()
        def update(credentials: dict[str, Credential]) -> dict[str, Credential]:
            updated = credentials.copy()
            updated[checked] = credential
            return updated
        self._write_changed(update)

    def set_if_absent(self, alias: str, credential: Credential) -> None:
        self._require_crypto()
        checked = validate_alias(alias)
        if not self.path.exists():
            raise ToolkitError("CredentialStoreNotInitialized", "Run `dba credentials init --backend encrypted` first.")
        if self._unlocked is None:
            self._read_container()

        def add(credentials: dict[str, Credential]) -> dict[str, Credential]:
            if checked in credentials:
                raise ToolkitError("CredentialAliasExists", "Credential alias already exists in target backend.")
            updated = credentials.copy()
            updated[checked] = credential
            return updated

        self._write_changed(add)

    def delete(self, alias: str) -> bool:
        self._require_crypto()
        checked = validate_alias(alias)
        if not self.path.exists():
            return False
        if self._unlocked is None:
            self._read_container()
        existed = False
        def remove(credentials: dict[str, Credential]) -> dict[str, Credential]:
            nonlocal existed
            updated = credentials.copy()
            existed = checked in updated
            updated.pop(checked, None)
            return updated
        if checked not in (self._unlocked or {}):
            return False
        self._write_changed(remove)
        return existed

    def aliases(self) -> tuple[str, ...]:
        self._require_crypto()
        if not self.path.exists():
            return ()
        if self._unlocked is None:
            _, credentials = self._read_container()
        else:
            credentials = self._unlocked
        return tuple(sorted(credentials))

    def lock(self) -> None:
        self._unlocked = None
        self._cached_master = None
        self._session_key = None


class CredentialResolver(CredentialProvider):
    """Resolve persistent credentials and enroll missing aliases just in time."""

    def __init__(self, os_store: CredentialStore | None, encrypted_store: EncryptedFileCredentialStore,
                 fallback: CredentialProvider, mode: str = "auto", *, database_id: str = "",
                 credential_prompt: Callable[[str, str], Credential] = prompt_jit_credential,
                 save_prompt: Callable[[], bool] = confirm_save_credential,
                 ephemeral_prompt: Callable[..., bool] = confirm_ephemeral_credential) -> None:
        if mode not in {"auto", "os", "encrypted", "interactive"}:
            raise ValueError("Unsupported credential backend selection")
        self.os_store = os_store
        self.encrypted_store = encrypted_store
        self.fallback = fallback
        self.mode = mode
        self.database_id = database_id
        self.credential_prompt = credential_prompt
        self.save_prompt = save_prompt
        self.ephemeral_prompt = ephemeral_prompt
        self.last_backend: str | None = None

    def get_credential(self, alias: str) -> Credential:
        checked = validate_alias(alias)
        if self.mode == "interactive":
            credential = self.fallback.get_credential(checked)
            self.last_backend = "Interactive Provider"
            return credential
        session_client: CredentialSessionClient | None = None
        active_session = False
        if self.mode == "encrypted":
            session_client = CredentialSessionClient()
            active_session = session_client.status().get("status") == "UNLOCKED"
            session_credential = session_client.get(checked)
            if session_credential is not None:
                self.last_backend = "Encrypted Credential Session"
                return session_credential
            if self.encrypted_store.exists and not active_session:
                self.encrypted_store._require_crypto()
                stored = self.encrypted_store.get(checked)
                if stored is not None:
                    self.last_backend = self.encrypted_store.backend_name
                    return stored
            return self._enroll(checked, self.encrypted_store, session_client)
        if self.mode in {"auto", "os"} and self.os_store is not None:
            try:
                if self.os_store.available():
                    os_credential = self.os_store.get(checked)
                    if os_credential is not None:
                        self.last_backend = self.os_store.backend_name
                        return os_credential
            except ToolkitError as exc:
                if exc.error_type not in {"CredentialStoreUnavailable", "CredentialStoreFailed"}:
                    raise
        if self.mode == "auto":
            session_client = CredentialSessionClient()
            active_session = session_client.status().get("status") == "UNLOCKED"
            session_credential = session_client.get(checked)
            if session_credential is not None:
                self.last_backend = "Encrypted Credential Session"
                return session_credential
        if self.mode in {"auto", "encrypted"} and self.encrypted_store.exists and not active_session:
            encrypted_credential = self.encrypted_store.get(checked)
            if encrypted_credential is not None:
                self.last_backend = self.encrypted_store.backend_name
                return encrypted_credential
        if self.mode == "interactive":
            fallback_credential = self.fallback.get_credential(checked)
            self.last_backend = "Interactive Provider"
            return fallback_credential
        target: CredentialStore | None = None
        if self.mode == "os":
            target = self.os_store if self.os_store is not None and self.os_store.available() else None
        elif self.mode == "encrypted":
            target = self.encrypted_store if self.encrypted_store.exists else None
        elif self.os_store is not None and self.os_store.available():
            target = self.os_store
        elif self.encrypted_store.exists:
            target = self.encrypted_store
        return self._enroll(checked, target, session_client)

    def _enroll(self, alias: str, target: CredentialStore | None, session_client: Any = None) -> Credential:
        credential = self.credential_prompt(self.database_id, alias)
        if not self.save_prompt():
            self.last_backend = "Ephemeral / This Run Only"
            return credential
        if target is None:
            sys.stderr.write("No secure persistent credential backend is available.\n"
                             "This credential will be used for this command only.\n"
                             "To enable encrypted storage: dba credentials init --backend encrypted\n")
            self.last_backend = "Ephemeral / This Run Only"
            return credential
        try:
            if target is self.encrypted_store and session_client is not None:
                status = session_client.status()
                if status.get("status") == "UNLOCKED":
                    response = session_client.save(alias, credential)
                    if response.get("status") == "ALREADY_EXISTS":
                        raise ToolkitError("CredentialAliasExists", "Credential alias already exists in target backend.")
                    if response.get("status") != "SAVED_INVALIDATED":
                        raise ToolkitError("CredentialStoreFailed", "Credential could not be stored securely.")
                    self.last_backend = target.backend_name
                    return credential
            if target.get(alias) is not None:
                raise ToolkitError("CredentialAliasExists", "Credential alias already exists in target backend.")
            set_if_absent = getattr(target, "set_if_absent", None)
            if callable(set_if_absent):
                set_if_absent(alias, credential)
            else:
                target.set(alias, credential)
        except ToolkitError as exc:
            safe_ephemeral = exc.error_type in {
                "CredentialStoreUnavailable", "CredentialStoreNotInitialized",
            }
            sys.stderr.write(f"Credential was not persisted securely ({exc.error_type}).\n")
            if not self.ephemeral_prompt(safe_default=safe_ephemeral):
                raise ToolkitError("CredentialStoreFailed", "Credential was not persisted; command cancelled.") from None
            self.last_backend = "Ephemeral / This Run Only"
            return credential
        self.last_backend = target.backend_name
        sys.stderr.write(f"Credential saved securely. Backend: {self.last_backend}.\n")
        return credential

