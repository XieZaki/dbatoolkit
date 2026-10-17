"""Local runtime inspection. Never installs packages or opens database connections."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import platform
import re
import struct
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from dba import __version__
from dba.core import DatabaseType
from dba.inventory import ValidationError, load_yaml

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "deployment" / "runtime-manifest.yaml"
ODBC_18 = "ODBC Driver 18 for SQL Server"
ODBC_17 = "ODBC Driver 17 for SQL Server"


class RuntimeStatus(StrEnum):
    READY = "READY"
    READY_WITH_WARNING = "READY_WITH_WARNING"
    MISSING = "MISSING"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DriverRequirement:
    module: str
    distribution: str
    version: str
    native_drivers: tuple[str, ...] = ()
    mode: str | None = None


@dataclass(frozen=True)
class RuntimeManifest:
    python_supported: str
    python_primary: str
    drivers: dict[DatabaseType, DriverRequirement]
    credential_store: DriverRequirement
    credential_crypto: dict[str, DriverRequirement]

    @classmethod
    def load(cls, path: Path = DEFAULT_MANIFEST) -> "RuntimeManifest":
        data = load_yaml(path)
        if not isinstance(data, dict) or set(data) != {"runtime", "drivers"}:
            raise ValidationError(f"{path}: Invalid runtime manifest")
        runtime, raw_drivers = data["runtime"], data["drivers"]
        if not isinstance(runtime, dict) or not isinstance(runtime.get("python"), dict):
            raise ValidationError(f"{path}: Invalid Python runtime definition")
        python = runtime["python"]
        if set(runtime) != {"python", "credential_store", "credential_encryption"}:
            raise ValidationError(f"{path}: Invalid runtime sections")
        if set(python) != {"supported", "primary"} or not all(
            isinstance(python.get(key), str) and python[key] for key in ("supported", "primary")
        ):
            raise ValidationError(f"{path}: Invalid Python version definition")
        if not isinstance(raw_drivers, dict):
            raise ValidationError(f"{path}: Invalid driver definitions")
        drivers: dict[DatabaseType, DriverRequirement] = {}
        expected = {item.value for item in (
            DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL, DatabaseType.MYSQL,
            DatabaseType.ORACLE, DatabaseType.MONGODB, DatabaseType.REDIS,
        )}
        if set(raw_drivers) != expected:
            raise ValidationError(f"{path}: Runtime manifest must define all supported drivers")
        for name, value in raw_drivers.items():
            if not isinstance(value, dict) or not set(value) <= {
                "python_package", "distribution", "version", "preferred_native_drivers", "mode"
            } or not {"python_package", "distribution", "version"} <= set(value):
                raise ValidationError(f"{path}: Invalid driver definition: {name}")
            native = value.get("preferred_native_drivers", [])
            if not isinstance(native, list) or any(not isinstance(item, str) for item in native):
                raise ValidationError(f"{path}: Invalid native driver list: {name}")
            if any(not isinstance(value[key], str) or not value[key] for key in
                   ("python_package", "distribution", "version")):
                raise ValidationError(f"{path}: Invalid package definition: {name}")
            drivers[DatabaseType(name)] = DriverRequirement(
                value["python_package"], value["distribution"], value["version"],
                tuple(native), value.get("mode"),
            )
        store = runtime["credential_store"]
        if not isinstance(store, dict) or set(store) != {
            "python_package", "distribution", "version", "windows_backend", "linux_backends"
        }:
            raise ValidationError(f"{path}: Invalid credential store runtime definition")
        if (not all(isinstance(store.get(key), str) and store[key] for key in
                    ("python_package", "distribution", "version", "windows_backend"))
                or not isinstance(store.get("linux_backends"), list)
                or any(not isinstance(item, str) for item in store["linux_backends"])):
            raise ValidationError(f"{path}: Invalid credential store runtime definition")
        credential_store = DriverRequirement(
            store["python_package"], store["distribution"], store["version"]
        )
        encryption = runtime["credential_encryption"]
        if not isinstance(encryption, dict) or set(encryption) != {"cryptography", "argon2"}:
            raise ValidationError(f"{path}: Invalid credential encryption runtime definition")
        crypto_requirements: dict[str, DriverRequirement] = {}
        for name, value in encryption.items():
            if (not isinstance(value, dict) or set(value) != {
                    "python_package", "distribution", "version"
            } or not all(isinstance(value[key], str) and value[key]
                         for key in ("python_package", "distribution", "version"))):
                raise ValidationError(f"{path}: Invalid credential encryption package: {name}")
            crypto_requirements[name] = DriverRequirement(
                value["python_package"], value["distribution"], value["version"]
            )
        return cls(python["supported"], python["primary"], drivers, credential_store,
                   crypto_requirements)


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value)[:3]]
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def version_supported(version: str, constraint: str) -> bool:
    current = _version_tuple(version)
    for expression in constraint.split(","):
        match = re.fullmatch(r"\s*(>=|>|<=|<|==)\s*([0-9.]+)\s*", expression)
        if match is None:
            return False
        target = _version_tuple(match.group(2))
        operator = match.group(1)
        if not {">=": current >= target, ">": current > target, "<=": current <= target,
                "<": current < target, "==": current == target}[operator]:
            return False
    return True


def _package(requirement: DriverRequirement) -> dict[str, Any]:
    try:
        present = importlib.util.find_spec(requirement.module) is not None
    except (ImportError, ValueError):
        present = False
    version: str | None = None
    if present:
        try:
            version = importlib.metadata.version(requirement.distribution)
        except importlib.metadata.PackageNotFoundError:
            present = False
    status = (RuntimeStatus.READY if present and version is not None
              and version_supported(version, requirement.version)
              else RuntimeStatus.UNSUPPORTED if present else RuntimeStatus.MISSING)
    return {
        "package": requirement.distribution,
        "module": requirement.module,
        "required_version": requirement.version,
        "version": version,
        "status": status.value,
    }


def installed_odbc_drivers() -> tuple[str, ...]:
    try:
        module = importlib.import_module("pyodbc")
        return tuple(sorted(str(item) for item in module.drivers()))
    except Exception:
        return ()


def select_sqlserver_driver(drivers: tuple[str, ...] | list[str]) -> str | None:
    available = set(drivers)
    return ODBC_18 if ODBC_18 in available else ODBC_17 if ODBC_17 in available else None


def wheelhouse_status(path: Path) -> dict[str, Any]:
    wheels = tuple(sorted(item.name for item in path.glob("*.whl"))) if path.is_dir() else ()
    return {
        "path": str(path.resolve()),
        "exists": path.is_dir(),
        "wheel_count": len(wheels),
        "wheels": wheels,
        "status": RuntimeStatus.READY.value if wheels else RuntimeStatus.MISSING.value,
        "network_fallback": False,
    }


def runtime_info(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest = RuntimeManifest.load(manifest_path)
    packages = {kind.value: _package(requirement) for kind, requirement in manifest.drivers.items()}
    credential_package = _package(manifest.credential_store)
    credential_crypto = {name: _package(requirement)
                         for name, requirement in manifest.credential_crypto.items()}
    odbc = installed_odbc_drivers() if packages["sqlserver"]["status"] == "READY" else ()
    selected = select_sqlserver_driver(odbc)
    return {
        "toolkit_version": __version__,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "architecture_bits": struct.calcsize("P") * 8,
            "implementation": platform.python_implementation(),
            "supported": manifest.python_supported,
            "primary": manifest.python_primary,
        },
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "platform": platform.platform()},
        "drivers": packages,
        "sqlserver_odbc": {
            "installed": odbc,
            "driver18_status": RuntimeStatus.READY.value if ODBC_18 in odbc else RuntimeStatus.MISSING.value,
            "driver17_status": RuntimeStatus.READY.value if ODBC_17 in odbc else RuntimeStatus.MISSING.value,
            "selected": selected,
        },
        "oracle_mode": "thin",
        "credential_session": {
            "supported": True,
            "transport": "Windows Named Pipe" if os.name == "nt" else "Unix Domain Socket with peer UID checks",
        },
        "credential_store_package": credential_package,
        "credential_crypto_packages": credential_crypto,
    }


def doctor(
    manifest_path: Path = DEFAULT_MANIFEST,
    configured_databases: set[DatabaseType] | None = None,
) -> dict[str, Any]:
    from dba.credentials import credential_store_status

    info = runtime_info(manifest_path)
    components: dict[str, Any] = {}
    python_status = (RuntimeStatus.READY if version_supported(
        str(info["python"]["version"]), str(info["python"]["supported"])
    ) else RuntimeStatus.UNSUPPORTED)
    overall = python_status
    configured = configured_databases or set()
    for name, package in info["drivers"].items():
        status = RuntimeStatus(package["status"])
        detail = dict(package)
        if name == "sqlserver":
            detail["driver18_status"] = info["sqlserver_odbc"]["driver18_status"]
            detail["driver17_status"] = info["sqlserver_odbc"]["driver17_status"]
            selected = info["sqlserver_odbc"]["selected"]
            if status == RuntimeStatus.READY and selected is None:
                status = RuntimeStatus.MISSING
                detail["action"] = "Install approved Microsoft ODBC Driver 18 or compatible Driver 17."
            elif status == RuntimeStatus.READY and selected == ODBC_17:
                status = RuntimeStatus.READY_WITH_WARNING
                detail["warning"] = "Driver 17 compatibility mode selected; TLS/default behavior differs from Driver 18."
            detail["selected_native_driver"] = selected
        if name == "oracle":
            detail["mode"] = "thin"
            detail["instant_client_required"] = False
        required = DatabaseType(name) in configured
        if status == RuntimeStatus.MISSING and not required:
            detail["status"] = "NOT_INSTALLED"
            detail["optional"] = True
            components[name] = detail
            continue
        detail["status"] = status.value
        components[name] = detail
        if status == RuntimeStatus.MISSING and required:
            overall = RuntimeStatus.MISSING
        elif status == RuntimeStatus.UNSUPPORTED and required and overall != RuntimeStatus.MISSING:
            overall = RuntimeStatus.UNSUPPORTED
        elif status == RuntimeStatus.READY_WITH_WARNING and required and overall == RuntimeStatus.READY:
            overall = status
    python = {**info["python"], "status": python_status.value}
    credential = credential_store_status()
    credential["session"] = credential.get("credential_session", {"status": "UNKNOWN"})
    credential["package"] = info["credential_store_package"]
    credential["crypto_packages"] = info["credential_crypto_packages"]
    if any(item["status"] != RuntimeStatus.READY.value
           for item in info["credential_crypto_packages"].values()):
        providers = credential.get("providers", {})
        encrypted = providers.get("Encrypted Local Store", {}) if isinstance(providers, dict) else {}
        if encrypted.get("status") == "READY":
            encrypted["status"] = "MISSING"
        if credential["status"] == "READY":
            credential["status"] = RuntimeStatus.READY_WITH_WARNING.value
    if info["credential_store_package"]["status"] != RuntimeStatus.READY.value:
        providers = credential.get("providers", {})
        if isinstance(providers, dict):
            for name in tuple(providers):
                if name not in {"Encrypted Local Store", "Interactive Provider"}:
                    providers[name]["status"] = "UNAVAILABLE"
        if credential["status"] == "READY":
            credential["status"] = RuntimeStatus.READY_WITH_WARNING.value
    if credential["status"] == RuntimeStatus.READY_WITH_WARNING.value and overall == RuntimeStatus.READY:
        overall = RuntimeStatus.READY_WITH_WARNING
    return {"title": "DBA Toolkit Doctor", "toolkit": RuntimeStatus.READY.value,
            "python": python, "components": components, "overall": overall.value,
            "credential_store": credential, "network_accessed": False}


def database_runtime_status(database_type: DatabaseType,
                            manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    report = doctor(manifest_path, {database_type})
    component = report["components"].get(database_type.value)
    return component if isinstance(component, dict) else {
        "status": RuntimeStatus.UNSUPPORTED.value
    }
