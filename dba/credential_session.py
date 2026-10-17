"""Per-user, short-lived encrypted-credential sessions over local IPC.

Session contents live only in the agent process. The only filesystem object used
on Linux is a mode-0600 Unix socket inside a private per-user runtime directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from dba.credentials import Credential, validate_alias
from dba.errors import ToolkitError

PROTOCOL_VERSION = 1
DEFAULT_TTL_SECONDS = 30 * 60
MIN_TTL_SECONDS = 5 * 60
MAX_TTL_SECONDS = 8 * 60 * 60
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_IO_TIMEOUT_SECONDS = 5.0


def parse_ttl(value: str | None) -> int:
    if value is None:
        return DEFAULT_TTL_SECONDS
    if not value or value.strip() != value:
        raise ToolkitError("InvalidCredentialSessionTTL", "TTL must be a duration such as 30m or 1h.")
    unit = value[-1].lower()
    amount = value[:-1]
    if len(amount) > 5 or not amount.isdecimal():
        raise ToolkitError("InvalidCredentialSessionTTL", "TTL must be a duration such as 30m or 1h.")
    multiplier = {"m": 60, "h": 3600}.get(unit)
    if multiplier is None:
        raise ToolkitError("InvalidCredentialSessionTTL", "TTL must use minutes (m) or hours (h).")
    seconds = int(amount) * multiplier
    if not MIN_TTL_SECONDS <= seconds <= MAX_TTL_SECONDS:
        raise ToolkitError("InvalidCredentialSessionTTL", "TTL must be between 5 minutes and 8 hours.")
    return seconds


def _windows_identity() -> tuple[str, str]:
    """Return (SID, named-pipe path) for the current Windows token."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ToolkitError("CredentialSessionUnavailable", "Could not inspect the current Windows user token.")
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise ToolkitError("CredentialSessionUnavailable", "Could not inspect the current Windows user SID.")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_string = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_string)):
            raise ToolkitError("CredentialSessionUnavailable", "Could not resolve the current Windows user SID.")
        try:
            resolved_sid = sid_string.value
            if resolved_sid is None:
                raise ToolkitError("CredentialSessionUnavailable", "Could not resolve the current Windows user SID.")
            sid = resolved_sid
        finally:
            kernel.LocalFree(sid_string)
    finally:
        kernel.CloseHandle(token)
    suffix = hashlib.sha256(sid.encode("ascii")).hexdigest()[:32]
    return sid, rf"\\.\pipe\dba-toolkit-credential-{suffix}"


def _windows_appcontainer_sid() -> str | None:
    """Return the current AppContainer package SID when running in a sandbox."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ToolkitError("CredentialSessionUnavailable", "Could not inspect the current Windows token.")
    try:
        is_appcontainer = wintypes.DWORD()
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 29, ctypes.byref(is_appcontainer),
                                   ctypes.sizeof(is_appcontainer), ctypes.byref(needed))
        if not is_appcontainer.value:
            return None
        advapi.GetTokenInformation(token, 31, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 31, buffer, needed, ctypes.byref(needed)):
            raise ToolkitError("CredentialSessionUnavailable", "Could not inspect the current AppContainer SID.")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_string = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_string)):
            raise ToolkitError("CredentialSessionUnavailable", "Could not resolve the current AppContainer SID.")
        try:
            return sid_string.value
        finally:
            kernel.LocalFree(sid_string)
    finally:
        kernel.CloseHandle(token)


def _unix_socket_path() -> Path:
    peer_cred = getattr(socket, "SO_PEERCRED", None)
    getuid = getattr(os, "getuid", None)
    if peer_cred is None or getuid is None:
        raise ToolkitError("CredentialSessionUnavailable", "This Unix platform cannot verify IPC peer identity.")
    uid = getuid()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path(tempfile.gettempdir())
    directory = base / f"dba-toolkit-{uid}"
    try:
        if runtime:
            base_info = base.lstat()
            if (base.is_symlink() or base_info.st_uid != uid or base_info.st_mode & 0o077
                    or not stat.S_ISDIR(base_info.st_mode)):
                raise ToolkitError("CredentialSessionUnavailable", "XDG_RUNTIME_DIR is not private to the current user.")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if directory.is_symlink() or info.st_uid != uid or info.st_mode & 0o077:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session runtime directory is unsafe.")
        os.chmod(directory, 0o700)
    except OSError:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session runtime directory is unavailable.") from None
    return directory / "session.sock"


def _endpoint() -> str | Path:
    if os.name == "nt":
        return _windows_identity()[1]
    return _unix_socket_path()


def _read_frame(stream: Any, deadline: float) -> dict[str, Any]:
    header = _read_exact(stream, 4, deadline)
    size = struct.unpack("!I", header)[0]
    if size <= 0 or size > MAX_MESSAGE_BYTES:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session message size is invalid.")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in pairs:
            if key in output:
                raise ValueError
            output[key] = item
        return output

    try:
        value = json.loads(_read_exact(stream, size, deadline), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise ToolkitError("CredentialSessionUnavailable", "Credential session message is malformed.") from None
    if not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL_VERSION:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session protocol version is unsupported.")
    return value


def _write_frame(stream: Any, message: dict[str, Any], deadline: float) -> None:
    try:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ToolkitError("CredentialSessionUnavailable", "Credential session message is invalid.") from None
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session message size is invalid.")
    _write_all(stream, struct.pack("!I", len(payload)) + payload, deadline)


def _read_exact(stream: Any, length: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < length:
        if time.monotonic() >= deadline:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session request timed out.")
        try:
            if isinstance(stream, socket.socket):
                part = stream.recv(length - len(result))
            elif isinstance(stream, int):
                part = _win_read(stream, length - len(result))
            else:
                part = stream.read(length - len(result))
        except BlockingIOError:
            time.sleep(0.01)
            continue
        except OSError:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session connection failed.") from None
        if not part:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session connection closed unexpectedly.")
        result.extend(part)
    return bytes(result)


def _write_all(stream: Any, payload: bytes, deadline: float) -> None:
    view = memoryview(payload)
    while view:
        if time.monotonic() >= deadline:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session request timed out.")
        try:
            if isinstance(stream, socket.socket):
                count = stream.send(view)
            elif isinstance(stream, int):
                count = _win_write(stream, view.tobytes())
            else:
                count = stream.write(view.tobytes())
        except BlockingIOError:
            time.sleep(0.01)
            continue
        except OSError:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session connection failed.") from None
        if count <= 0:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session connection closed unexpectedly.")
        view = view[count:]


def _win_read(handle: int, length: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(length)
    read = wintypes.DWORD()
    if not kernel.ReadFile(
        handle, buffer, length, ctypes.byref(read), None
    ):
        error = ctypes.get_last_error()
        if error in (232, 997):
            raise BlockingIOError
        raise OSError(error, "pipe read failed")
    return buffer.raw[:read.value]


def _win_write(handle: int, payload: bytes) -> int:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(payload)
    written = wintypes.DWORD()
    if not kernel.WriteFile(
        handle, buffer, len(payload), ctypes.byref(written), None
    ):
        error = ctypes.get_last_error()
        if error in (232, 997):
            raise BlockingIOError
        raise OSError(error, "pipe write failed")
    return written.value


def _open_windows_pipe(path: str, timeout: float = _IO_TIMEOUT_SECONDS) -> int:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                               ctypes.c_void_p, ctypes.c_void_p]
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handle = kernel.CreateFileW(path, 0xC0000000, 0, None, 3, 0, None)
        if handle != wintypes.HANDLE(-1).value:
            mode = wintypes.DWORD(1)  # PIPE_NOWAIT; bounded protocol reads/writes.
            kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None)
            return int(handle if isinstance(handle, int) else handle.value)
        error = ctypes.get_last_error()
        if error == 2:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session agent is not available.")
        if error == 5:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session pipe access was denied.")
        if error == 231:
            kernel.WaitNamedPipeW(path, 100)
        else:
            break
        time.sleep(0.05)
    raise ToolkitError("CredentialSessionUnavailable", "Credential session agent is not available.")


class CredentialSessionClient:
    """Bounded protocol client; unavailable means no active session, protocol errors fail closed."""

    def request(self, operation: str, **fields: Any) -> dict[str, Any] | None:
        endpoint = _endpoint()
        stream: Any
        if os.name == "nt":
            try:
                stream = _open_windows_pipe(str(endpoint))
            except ToolkitError as exc:
                if "not available" in str(exc):
                    return None
                raise
        else:
            stream = socket.socket(cast(Any, getattr(socket, "AF_UNIX")), socket.SOCK_STREAM)
            stream.settimeout(_IO_TIMEOUT_SECONDS)
            try:
                stream.connect(str(endpoint))
            except (FileNotFoundError, ConnectionRefusedError):
                stream.close()
                return None
            except OSError:
                stream.close()
                raise ToolkitError("CredentialSessionUnavailable", "Credential session connection failed.") from None
        deadline = time.monotonic() + _IO_TIMEOUT_SECONDS
        try:
            request = {"protocol_version": PROTOCOL_VERSION, "operation": operation, **fields}
            _write_frame(stream, request, deadline)
            response = _read_frame(stream, deadline)
            if set(response) - {"protocol_version", "status", "session", "credential", "credentials", "message"}:
                raise ToolkitError("CredentialSessionUnavailable", "Credential session response schema is invalid.")
            return response
        finally:
            if os.name == "nt":
                import ctypes

                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(stream)
            else:
                stream.close()

    def status(self) -> dict[str, Any]:
        response = self.request("STATUS")
        if response is None:
            return {"status": "LOCKED", "agent": "STOPPED"}
        if response.get("status") == "ERROR":
            raise ToolkitError("CredentialSessionUnavailable", "Credential session agent rejected its status request.")
        if response.get("status") not in {"LOCKED", "UNLOCKED"}:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session status response is invalid.")
        session = response.get("session")
        if isinstance(session, dict):
            session.pop("session_id", None)
        return response

    def get(self, alias: str) -> Credential | None:
        checked = validate_alias(alias)
        status = self.request("STATUS")
        if status is None or status.get("status") == "LOCKED":
            return None
        if status.get("status") != "UNLOCKED":
            raise ToolkitError("CredentialSessionUnavailable", "Credential session status response is invalid.")
        response = self.request("GET", alias=checked)
        if response is None:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session stopped during lookup.")
        if response.get("status") == "NOT_FOUND":
            return None
        item = response.get("credential")
        if response.get("status") != "OK" or not isinstance(item, dict) or set(item) != {"username", "password"}:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session returned an invalid credential response.")
        if not isinstance(item["username"], str) or not isinstance(item["password"], str):
            raise ToolkitError("CredentialSessionUnavailable", "Credential session returned an invalid credential response.")
        return Credential(item["username"], item["password"])

    def list_metadata(self) -> list[dict[str, str]] | None:
        response = self.request("LIST_METADATA")
        if response is None:
            return None
        rows = response.get("credentials")
        if response.get("status") != "OK" or not isinstance(rows, list):
            raise ToolkitError("CredentialSessionUnavailable", "Credential session metadata response is invalid.")
        result: list[dict[str, str]] = []
        for item in rows:
            if (not isinstance(item, dict) or set(item) != {"alias", "username"}
                    or not isinstance(item["alias"], str) or not isinstance(item["username"], str)):
                raise ToolkitError("CredentialSessionUnavailable", "Credential session metadata response is invalid.")
            result.append({"alias": item["alias"], "username": item["username"]})
        return result

    def ensure_agent(self) -> None:
        response = self.request("STATUS")
        if response is not None:
            return
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        try:
            subprocess.Popen(
                [sys.executable, "-m", "dba.credential_agent", "--serve"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=flags, start_new_session=os.name != "nt",
            )
        except OSError:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session agent could not be started.") from None
        deadline = time.monotonic() + _IO_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            response = self.request("STATUS")
            if response is not None:
                return
            time.sleep(0.05)
        raise ToolkitError("CredentialSessionUnavailable", "Credential session agent did not become ready.")

    def load(self, credentials: dict[str, Credential], ttl_seconds: int, store_path: Path,
             store_key: bytes) -> dict[str, Any]:
        self.ensure_agent()
        fingerprint = _file_fingerprint(store_path)
        payload = {alias: {"username": value.username, "password": value.password}
                   for alias, value in credentials.items()}
        response = self.request("LOAD", credentials=payload, ttl_seconds=ttl_seconds,
                                store_path=str(store_path), store_fingerprint=fingerprint,
                                store_key=base64.b64encode(store_key).decode("ascii"))
        payload.clear()
        if response is None or response.get("status") != "UNLOCKED":
            raise ToolkitError("CredentialSessionUnavailable", "Credential session could not be unlocked.")
        session = response.get("session")
        if isinstance(session, dict):
            session.pop("session_id", None)
        return response

    def lock(self) -> dict[str, Any]:
        response = self.request("LOCK")
        return response or {"status": "LOCKED", "agent": "STOPPED"}

    def save(self, alias: str, credential: Credential) -> dict[str, Any]:
        response = self.request("SAVE", alias=validate_alias(alias),
                                credential={"username": credential.username,
                                            "password": credential.password})
        if response is None:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session stopped during save.")
        if response.get("status") == "ERROR":
            error_type = response.get("message")
            if not isinstance(error_type, str) or not error_type.startswith("Credential"):
                error_type = "CredentialStoreFailed"
            raise ToolkitError(error_type, "Credential could not be stored securely.")
        return response


def _file_fingerprint(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        raise ToolkitError("CredentialStoreFailed", "Encrypted credential store could not be verified.") from None


def serve_agent() -> None:
    """Serve a single user session. Called only by the internal agent module."""
    if os.name == "nt":
        _serve_windows()
    else:
        _serve_unix()


def _validate_request(request: dict[str, Any]) -> str:
    if set(request) - {"protocol_version", "operation", "alias", "credentials", "credential", "ttl_seconds",
                       "store_path", "store_fingerprint", "store_key"}:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session request schema is invalid.")
    operation = request.get("operation")
    if operation not in {"STATUS", "GET", "LIST_METADATA", "LOAD", "SAVE", "LOCK", "SHUTDOWN"}:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session operation is unsupported.")
    expected = {
        "STATUS": {"protocol_version", "operation"},
        "GET": {"protocol_version", "operation", "alias"},
        "LIST_METADATA": {"protocol_version", "operation"},
        "LOAD": {"protocol_version", "operation", "credentials", "ttl_seconds",
                 "store_path", "store_fingerprint", "store_key"},
        "SAVE": {"protocol_version", "operation", "alias", "credential"},
        "LOCK": {"protocol_version", "operation"},
        "SHUTDOWN": {"protocol_version", "operation"},
    }[operation]
    if set(request) != expected:
        raise ToolkitError("CredentialSessionUnavailable", "Credential session request schema is invalid.")
    return str(operation)


def _new_manager() -> dict[str, Any]:
    return {"credentials": None, "expires_monotonic": 0.0, "created_at": None,
            "expires_at": None, "session_id": None, "store_path": None,
            "store_fingerprint": None, "store_key": None}


def _clear_session(manager: dict[str, Any]) -> None:
    credentials = manager.get("credentials")
    if isinstance(credentials, dict):
        credentials.clear()
    manager.update(_new_manager())


def _is_current(manager: dict[str, Any], now: float | None = None) -> bool:
    if not isinstance(manager.get("credentials"), dict):
        return False
    if (time.monotonic() if now is None else now) >= manager["expires_monotonic"]:
        _clear_session(manager)
        return False
    path = manager.get("store_path")
    if not isinstance(path, str):
        _clear_session(manager)
        return False
    try:
        current = _file_fingerprint(Path(path))
    except ToolkitError:
        _clear_session(manager)
        return False
    if not secrets.compare_digest(current, manager["store_fingerprint"]):
        _clear_session(manager)
        return False
    return True


def _status(manager: dict[str, Any]) -> dict[str, Any]:
    if not _is_current(manager):
        return {"protocol_version": PROTOCOL_VERSION, "status": "LOCKED"}
    remaining = max(0, int(manager["expires_monotonic"] - time.monotonic()))
    return {"protocol_version": PROTOCOL_VERSION, "status": "UNLOCKED", "session": {
        "session_id": manager["session_id"], "created_at": manager["created_at"],
        "expires_at": manager["expires_at"], "expires_in": remaining,
        "backend": "Encrypted Local Store", "agent_pid": os.getpid(),
    }}


def _dispatch(manager: dict[str, Any], request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    operation = _validate_request(request)
    if operation == "STATUS":
        return _status(manager), False
    if operation == "LOAD":
        credentials = request.get("credentials")
        ttl = request.get("ttl_seconds")
        path = request.get("store_path")
        fingerprint = request.get("store_fingerprint")
        if (not isinstance(credentials, dict) or not isinstance(ttl, int) or isinstance(ttl, bool)
                or not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS
                or not isinstance(path, str) or not isinstance(fingerprint, str)
                or len(fingerprint) != 64 or not isinstance(request.get("store_key"), str)):
            raise ToolkitError("CredentialSessionUnavailable", "Credential session load request is invalid.")
        try:
            store_key = base64.b64decode(request["store_key"], validate=True)
        except (ValueError, TypeError):
            raise ToolkitError("CredentialSessionUnavailable", "Credential session load request is invalid.") from None
        if len(store_key) != 32:
            raise ToolkitError("CredentialSessionUnavailable", "Credential session load request is invalid.")
        checked: dict[str, Credential] = {}
        for alias, item in credentials.items():
            alias = validate_alias(alias)
            if (not isinstance(item, dict) or set(item) != {"username", "password"}
                    or not isinstance(item["username"], str) or not isinstance(item["password"], str)):
                raise ToolkitError("CredentialSessionUnavailable", "Credential session load request is invalid.")
            checked[alias] = Credential(item["username"], item["password"])
        if not secrets.compare_digest(_file_fingerprint(Path(path)), fingerprint):
            raise ToolkitError("CredentialStoreChanged", "Encrypted credential store changed during unlock.")
        _clear_session(manager)
        now = datetime.now(timezone.utc)
        manager.update({"credentials": checked, "expires_monotonic": time.monotonic() + ttl,
                        "created_at": now.isoformat(), "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
                        "session_id": secrets.token_urlsafe(32), "store_path": path,
                        "store_fingerprint": fingerprint, "store_key": store_key})
        return _status(manager), False
    if operation == "LOCK":
        _clear_session(manager)
        return {"protocol_version": PROTOCOL_VERSION, "status": "LOCKED"}, True
    if operation == "SHUTDOWN":
        _clear_session(manager)
        return {"protocol_version": PROTOCOL_VERSION, "status": "LOCKED"}, True
    if not _is_current(manager):
        return {"protocol_version": PROTOCOL_VERSION, "status": "LOCKED"}, True
    if operation == "GET":
        alias = validate_alias(request.get("alias", ""))
        credential = manager["credentials"].get(alias)
        if credential is None:
            return {"protocol_version": PROTOCOL_VERSION, "status": "NOT_FOUND"}, False
        return {"protocol_version": PROTOCOL_VERSION, "status": "OK",
                "credential": {"username": credential.username, "password": credential.password}}, False
    if operation == "LIST_METADATA":
        rows = [{"alias": alias, "username": item.username}
                for alias, item in sorted(manager["credentials"].items())]
        return {"protocol_version": PROTOCOL_VERSION, "status": "OK", "credentials": rows}, False
    if operation == "SAVE":
        alias = validate_alias(request.get("alias", ""))
        item = request.get("credential")
        if (not isinstance(item, dict) or set(item) != {"username", "password"}
                or not isinstance(item["username"], str) or not isinstance(item["password"], str)):
            raise ToolkitError("CredentialSessionUnavailable", "Credential session save request is invalid.")
        credentials = manager["credentials"]
        if alias in credentials:
            return {"protocol_version": PROTOCOL_VERSION, "status": "ALREADY_EXISTS"}, False
        updated = dict(credentials)
        updated[alias] = Credential(item["username"], item["password"])
        from dba.encrypted_credentials import EncryptedFileCredentialStore

        store = EncryptedFileCredentialStore(Path(manager["store_path"]))
        try:
            store.persist_session_credentials(updated, manager["store_key"], manager["store_fingerprint"])
        except ToolkitError:
            _clear_session(manager)
            raise
        _clear_session(manager)
        return {"protocol_version": PROTOCOL_VERSION, "status": "SAVED_INVALIDATED"}, True
    raise ToolkitError("CredentialSessionUnavailable", "Credential session operation is unsupported.")


def _serve_unix() -> None:
    path = _unix_socket_path()
    manager = _new_manager()
    bound_info: os.stat_result | None = None
    server = socket.socket(cast(Any, getattr(socket, "AF_UNIX")), socket.SOCK_STREAM)
    try:
        try:
            server.bind(str(path))
        except OSError as exc:
            if exc.errno != getattr(__import__("errno"), "EADDRINUSE"):
                return
            try:
                probe = socket.socket(cast(Any, getattr(socket, "AF_UNIX")), socket.SOCK_STREAM)
                probe.settimeout(0.2)
                try:
                    probe.connect(str(path))
                    return
                except ConnectionRefusedError:
                    time.sleep(0.2)
                    try:
                        probe.connect(str(path))
                        return
                    except ConnectionRefusedError:
                        pass
                    except OSError:
                        return
                except OSError:
                    return
            finally:
                if "probe" in locals():
                    probe.close()
            try:
                info = path.lstat()
                getuid = getattr(os, "getuid", None)
                if getuid is None or info.st_uid != getuid() or not stat.S_ISSOCK(info.st_mode):
                    return
                path.unlink()
                server.bind(str(path))
            except OSError:
                return
        os.chmod(path, 0o600)
        bound_info = path.lstat()
        server.listen(8)
        while True:
            if manager["credentials"] is not None and not _is_current(manager):
                return
            server.settimeout(0.2)
            try:
                peer, _ = server.accept()
            except TimeoutError:
                continue
            try:
                if not _unix_peer_is_current(peer):
                    continue
                peer.settimeout(_IO_TIMEOUT_SECONDS)
                deadline = time.monotonic() + _IO_TIMEOUT_SECONDS
                request: dict[str, Any] = {}
                response: dict[str, Any] = {}
                try:
                    request = _read_frame(peer, deadline)
                    response, stop = _dispatch(manager, request)
                except ToolkitError as exc:
                    response, stop = ({"protocol_version": PROTOCOL_VERSION,
                                       "status": "ERROR", "message": exc.error_type}, False)
                _write_frame(peer, response, deadline)
                if stop:
                    return
            except ToolkitError:
                continue
            finally:
                supplied = request.get("credentials")
                if isinstance(supplied, dict):
                    supplied.clear()
                request.clear()
                response.clear()
                peer.close()
    finally:
        server.close()
        _clear_session(manager)
        try:
            current = path.lstat()
            if (bound_info is not None and current.st_dev == bound_info.st_dev
                    and current.st_ino == bound_info.st_ino):
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _unix_peer_is_current(peer: socket.socket) -> bool:
    try:
        peer_cred = getattr(socket, "SO_PEERCRED", None)
        getuid = getattr(os, "getuid", None)
        if peer_cred is None or getuid is None:
            return False
        raw = peer.getsockopt(socket.SOL_SOCKET, peer_cred, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", raw)
        return bool(uid == getuid())
    except (OSError, struct.error):
        return False


def _serve_windows() -> None:
    import ctypes
    from ctypes import wintypes

    sid, pipe_name = _windows_identity()
    appcontainer_sid = _windows_appcontainer_sid()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.DWORD, ctypes.POINTER(SecurityAttributes)]
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                               ctypes.c_void_p, ctypes.c_void_p]
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]

    descriptor = ctypes.c_void_p()
    appcontainer_ace = f"(A;;GA;;;{appcontainer_sid})" if appcontainer_sid else ""
    sddl = f"O:{sid}D:P(A;;GA;;;{sid}){appcontainer_ace}(A;;GA;;;SY)"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ToolkitError("CredentialSessionUnavailable", "Could not create a current-user pipe security descriptor.")
    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
    manager = _new_manager()
    handle = kernel.CreateNamedPipeW(
        pipe_name, 0x00000003 | 0x00080000, 0x00000000 | 0x00000001,
        1, MAX_MESSAGE_BYTES, MAX_MESSAGE_BYTES, 0, ctypes.byref(attributes),
    )
    if handle == wintypes.HANDLE(-1).value:
        kernel.LocalFree(descriptor)
        raise ToolkitError("CredentialSessionUnavailable",
                           f"Windows Named Pipe creation failed (code {ctypes.get_last_error()}).")
    handle = int(handle if isinstance(handle, int) else handle.value)
    mode = wintypes.DWORD(1)
    kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None)
    try:
        while True:
            if manager["credentials"] is not None and not _is_current(manager):
                return
            connected = False
            deadline = time.monotonic() + (30 if manager["credentials"] is None else
                                           max(1, manager["expires_monotonic"] - time.monotonic()))
            while time.monotonic() < deadline:
                if kernel.ConnectNamedPipe(handle, None):
                    connected = True
                    break
                if ctypes.get_last_error() == 535:
                    connected = True
                    break
                if ctypes.get_last_error() not in (536, 997, 232):
                    break
                time.sleep(0.05)
            stop = False
            peer_ok = connected and _windows_peer_is_current(kernel, handle, sid)
            request: dict[str, Any] = {}
            response: dict[str, Any] = {}
            if peer_ok:
                try:
                    limit = time.monotonic() + _IO_TIMEOUT_SECONDS
                    request = _read_frame(handle, limit)
                    response, stop = _dispatch(manager, request)
                except ToolkitError as exc:
                    response = {"protocol_version": PROTOCOL_VERSION, "status": "ERROR",
                                "message": exc.error_type}
                try:
                    _write_frame(handle, response, time.monotonic() + _IO_TIMEOUT_SECONDS)
                    kernel.FlushFileBuffers(handle)
                except ToolkitError:
                    pass
            supplied = request.get("credentials")
            if isinstance(supplied, dict):
                supplied.clear()
            request.clear()
            response.clear()
            kernel.DisconnectNamedPipe(handle)
            if stop:
                return
    finally:
        kernel.CloseHandle(handle)
        _clear_session(manager)
        kernel.LocalFree(descriptor)


def _windows_peer_is_current(kernel: Any, pipe: int, sid: str) -> bool:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.ULONG()
    if not kernel.GetNamedPipeClientProcessId(pipe, ctypes.byref(pid)):
        return False
    process = kernel.OpenProcess(0x1000, False, pid.value)
    if not process:
        return False
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return False
        try:
            needed = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
                return False
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_string = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_string)):
                return False
            try:
                return sid_string.value == sid
            finally:
                kernel.LocalFree(sid_string)
        finally:
            kernel.CloseHandle(token)
    finally:
        kernel.CloseHandle(process)
