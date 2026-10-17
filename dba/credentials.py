import getpass
import json
import os
import re
import sys
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from dba.errors import ToolkitError


class Mode(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class Credential:
    username: str
    password: str = field(repr=False)


class CredentialProvider(ABC):
    @abstractmethod
    def get_credential(self, alias: str) -> Credential:
        """Future providers: Credential Manager, DPAPI, Secret Store, Vault/PAM."""


class CredentialStore(ABC):
    """OS-backed secret storage. Implementations must never persist plaintext."""

    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def get(self, alias: str) -> Credential | None: ...

    @abstractmethod
    def set(self, alias: str, credential: Credential) -> None: ...

    @abstractmethod
    def delete(self, alias: str) -> bool: ...

    @abstractmethod
    def aliases(self) -> tuple[str, ...]: ...


def validate_alias(alias: str) -> str:
    value = alias.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value):
        raise ToolkitError("InvalidCredentialAlias", "Credential alias is invalid.")
    return value


class KeyringCredentialStore(CredentialStore):
    """Windows Credential Manager or Linux system keyring through keyring."""

    SERVICE = "dba-toolkit"

    def __init__(self, index_path: Path | None = None, keyring_module: Any | None = None) -> None:
        self._keyring = keyring_module
        self._index_path = index_path or self._default_index_path()

    @staticmethod
    def _default_index_path() -> Path:
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        return root / "dba-toolkit" / "credential-aliases.json"

    def _load_keyring(self) -> Any | None:
        if self._keyring is not None:
            return self._keyring
        try:
            import keyring

            self._keyring = keyring
        except ImportError:
            return None
        return self._keyring

    @property
    def backend_name(self) -> str:
        module = self._load_keyring()
        if module is None:
            return "Unavailable"
        if os.name == "nt":
            return "Windows Credential Manager"
        try:
            backend = module.get_keyring()
            return f"System Keyring ({backend.__class__.__name__})"
        except Exception:
            return "System Keyring"

    def available(self) -> bool:
        module = self._load_keyring()
        if module is None:
            return False
        try:
            backend = module.get_keyring()
            if float(getattr(backend, "priority", 0)) <= 0:
                return False
            identity = f"{backend.__class__.__module__}.{backend.__class__.__name__}".casefold()
            if os.name == "nt":
                if "winvault" not in identity and "windows" not in identity:
                    return False
                # Backend selection alone does not prove that this Windows
                # account can access Credential Manager. A read-only sentinel
                # probe distinguishes ERROR_NOT_FOUND from access failures.
                try:
                    module.get_password(self.SERVICE, "__dba_toolkit_backend_probe__")
                    return True
                except Exception as exc:
                    return _credential_not_found(exc)
            recognized = any(
                marker in identity
                for marker in ("secretservice", "kwallet", "keychain", "libsecret")
            )
            if not recognized:
                return False
            try:
                # A non-writing sentinel read verifies a live session/service
                # connection; backend selection alone is insufficient on headless hosts.
                module.get_password(self.SERVICE, "__dba_toolkit_backend_probe__")
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _read_index(self) -> list[str]:
        try:
            value = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        return sorted({item for item in value if isinstance(item, str)
                       and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", item)}) \
            if isinstance(value, list) else []

    def _write_index(self, aliases: list[str]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(sorted(set(aliases))), encoding="utf-8")
        temporary.replace(self._index_path)

    def get(self, alias: str) -> Credential | None:
        alias = validate_alias(alias)
        module = self._load_keyring()
        if module is None or not self.available():
            return None
        try:
            payload = module.get_password(self.SERVICE, alias)
            if payload is None:
                return None
            value = json.loads(payload)
            if not isinstance(value, dict) or set(value) != {"username", "password"}:
                raise ToolkitError("CredentialStoreInvalid", "Stored credential has invalid format.")
            username, password = value["username"], value["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ToolkitError("CredentialStoreInvalid", "Stored credential has invalid format.")
            return Credential(username, password)
        except ToolkitError:
            raise
        except Exception as exc:
            if _credential_not_found(exc):
                return None
            raise ToolkitError("CredentialStoreFailed", "Credential store could not be read.") from None

    def set(self, alias: str, credential: Credential) -> None:
        alias = validate_alias(alias)
        if not self.available():
            raise ToolkitError("CredentialStoreUnavailable", "Secure OS credential store is unavailable.")
        module = self._load_keyring()
        assert module is not None
        payload = json.dumps(
            {"username": credential.username, "password": credential.password},
            ensure_ascii=False,
        )
        try:
            module.set_password(self.SERVICE, alias, payload)
            self._write_index([*self._read_index(), alias])
        except Exception:
            raise ToolkitError("CredentialStoreFailed", "Credential could not be stored.") from None

    def delete(self, alias: str) -> bool:
        alias = validate_alias(alias)
        if not self.available():
            raise ToolkitError("CredentialStoreUnavailable", "Secure OS credential store is unavailable.")
        module = self._load_keyring()
        assert module is not None
        if self.get(alias) is None:
            return False
        try:
            module.delete_password(self.SERVICE, alias)
            self._write_index([item for item in self._read_index() if item != alias])
            return True
        except Exception:
            raise ToolkitError("CredentialStoreFailed", "Credential could not be deleted.") from None

    def aliases(self) -> tuple[str, ...]:
        if not self.available():
            return ()
        return tuple(alias for alias in self._read_index() if self.get(alias) is not None)


class StoredCredentialProvider(CredentialProvider):
    """Use the OS store first, then an in-memory interactive credential."""

    def __init__(
        self, store: CredentialStore, fallback: CredentialProvider | None = None
    ) -> None:
        self.store = store
        self.fallback = fallback or PromptCredentialProvider()
        self.last_backend: str | None = None

    def get_credential(self, alias: str) -> Credential:
        alias = validate_alias(alias)
        store_unavailable = False
        try:
            store_available = self.store.available()
            store_unavailable = not store_available
            credential = self.store.get(alias) if store_available else None
        except ToolkitError as exc:
            if exc.error_type not in {"CredentialStoreUnavailable", "CredentialStoreFailed"}:
                raise
            store_unavailable = True
            credential = None
        if credential is not None:
            self.last_backend = self.store.backend_name
            return credential
        try:
            if sys.stderr.isatty():
                if store_unavailable:
                    sys.stderr.write(
                        "Secure credential store is unavailable in this Windows user context. "
                        "Enter credentials for this run only.\n"
                    )
                else:
                    sys.stderr.write(
                        f"Credential alias not found in secure store: {alias}. "
                        f"Run `dba credentials set {alias}` to save it, or enter credentials "
                        "for this run only.\n"
                    )
            credential = self.fallback.get_credential(alias)
            self.last_backend = "Interactive Provider"
            return credential
        except ToolkitError:
            message = (
                "Secure credential store is unavailable and interactive input was not completed. "
                "Run under a Windows user with Credential Manager access or use an interactive terminal."
                if store_unavailable else
                f"Credential alias not found: {validate_alias(alias)}. Run `dba credentials set {alias}` "
                "or use an interactive terminal for this run."
            )
            raise ToolkitError(
                "CredentialUnavailable",
                message,
            ) from None


def _credential_not_found(exc: Exception) -> bool:
    """Recognize only the Windows Credential Manager missing-item code."""
    code = getattr(exc, "winerror", None)
    if code is None and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
    return code == 1168  # ERROR_NOT_FOUND; other failures must not look like missing aliases.


def credential_store_status(store: CredentialStore | None = None) -> dict[str, Any]:
    selected = store or KeyringCredentialStore()
    os_available = selected.available()
    from dba.encrypted_credentials import EncryptedFileCredentialStore, crypto_available

    encrypted = EncryptedFileCredentialStore()
    encrypted_exists = encrypted.exists
    permission_check = encrypted.permission_status()
    encrypted_ready = crypto_available() and permission_check != "UNSAFE"
    if os_available:
        selected_name = selected.backend_name
        status = "READY"
    elif encrypted_exists and encrypted_ready:
        selected_name = encrypted.backend_name
        status = "READY"
    elif encrypted_exists and not encrypted_ready:
        selected_name = "Interactive Provider"
        status = "READY_WITH_WARNING"
    else:
        selected_name = "Interactive Provider"
        status = "READY_WITH_WARNING"
    from dba.credential_session import CredentialSessionClient

    return {
        "backend": selected_name,
        "status": status,
        "credential_session": CredentialSessionClient().status(),
        "stored_aliases": len(selected.aliases()) if os_available else None,
        "fallback": None if os_available or encrypted_exists else "Interactive Provider",
        "providers": {
            selected.backend_name: {"status": "READY" if os_available else "UNAVAILABLE"},
            "Encrypted Local Store": {
                "status": ("READY" if encrypted_ready and encrypted_exists else
                           "NOT_INITIALIZED" if encrypted_ready else
                           "UNSAFE" if permission_check == "UNSAFE" else "MISSING"),
                "path": str(encrypted.path),
                "format": 1,
                "cipher": "AES-256-GCM",
                "kdf": "Argon2id",
                "permission_check": permission_check,
                "dependency_status": "READY" if encrypted_ready else "MISSING",
                "required_packages": ("cryptography", "argon2-cffi"),
            },
            "Interactive Provider": {"status": "READY"},
        },
    }


def prompt_credential_for_storage() -> Credential:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ToolkitError("CredentialUnavailable", "Interactive terminal required for credential input.")
    try:
        username = input("Monitoring account username: ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Monitoring account password: ")
            confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise ToolkitError("CredentialMismatch", "Password confirmation does not match.")
        credential = Credential(username, password)
        validate_monitoring_credential(credential)
        return credential
    except ToolkitError:
        raise
    except (EOFError, OSError, ValueError, getpass.GetPassWarning):
        raise ToolkitError("CredentialUnavailable", "Secure credential input unavailable.") from None


def prompt_jit_credential(database_id: str, alias: str) -> Credential:
    """Collect a database credential for one command without persisting it."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ToolkitError(
            "CredentialUnavailable", "Credential alias not found and interactive input is unavailable."
        )
    try:
        sys.stderr.write(
            "Credential Not Found\n"
            "--------------------------------\n"
            f"Database\n  {database_id}\n\n"
            f"Credential Alias\n  {alias}\n\n"
            "Username: "
        )
        username = input().strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Database password: ")
        credential = Credential(username, password)
        validate_monitoring_credential(credential)
        return credential
    except ToolkitError:
        raise
    except (EOFError, OSError, ValueError, getpass.GetPassWarning):
        raise ToolkitError("CredentialUnavailable", "Secure credential input unavailable.") from None


def confirm_save_credential() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("Save this credential securely for future use? [Y/n]: ").strip().casefold()
        return answer not in {"n", "no"}
    except (EOFError, OSError):
        return False


def confirm_ephemeral_credential(*, safe_default: bool) -> bool:
    if not sys.stdin.isatty():
        return False
    suffix = "[Y/n]" if safe_default else "[y/N]"
    try:
        answer = input(f"Use this credential for this run only? {suffix}: ").strip().casefold()
    except (EOFError, OSError):
        return False
    if not answer:
        return safe_default
    return answer in {"y", "yes"}


def confirm_action(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip().casefold() in {"y", "yes"}
    except (EOFError, OSError):
        return False


class MockCredentialProvider(CredentialProvider):
    def __init__(self, credentials: dict[str, Credential], mode: Mode = Mode.DEVELOPMENT) -> None:
        if mode != Mode.DEVELOPMENT:
            raise ValueError("Mock credentials are restricted to development and tests")
        self._credentials = credentials.copy()

    def get_credential(self, alias: str) -> Credential:
        return self._credentials[alias]


def validate_provider(provider: CredentialProvider, mode: Mode) -> None:
    if mode == Mode.PRODUCTION and isinstance(provider, MockCredentialProvider):
        raise ValueError("MockCredentialProvider is prohibited in production")


def validate_monitoring_credential(credential: Credential) -> None:
    if credential.username.strip().casefold() in {"sa", "root", "sys", "system", "postgres"}:
        raise ValueError("Privileged accounts are prohibited")
    if (
        not credential.username.strip()
        or not credential.password
        or "\x00" in credential.username + credential.password
    ):
        raise ValueError("A monitoring username and password are required")


class PromptCredentialProvider(CredentialProvider):
    """Interactive real credentials, never stored on disk or accepted via argv."""

    def get_credential(self, alias: str) -> Credential:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ToolkitError(
                "CredentialUnavailable", "Interactive terminal required for credential input."
            )
        try:
            # Alias is an identifier, not a credential. Do not echo external values.
            username = input("Monitoring account username: ").strip()
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Monitoring account password: ")
            return Credential(username, password)
        except (EOFError, OSError, getpass.GetPassWarning):
            raise ToolkitError(
                "CredentialUnavailable", "Secure credential input unavailable."
            ) from None
