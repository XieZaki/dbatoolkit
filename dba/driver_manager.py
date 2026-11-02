"""Manage explicitly installed, versioned database-driver packages.

Driver packages are local ZIP files. This module never downloads packages or
invokes pip. A package is executable code, so callers should install only ZIPs
from a trusted source.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, cast

from dba import __version__
from dba.core import DatabaseType
from dba.driver_versions import DriverModuleVersionError, validate_driver_module_version
from dba.errors import ToolkitError

SUPPORTED_DATABASES = frozenset({
    DatabaseType.MYSQL, DatabaseType.POSTGRESQL, DatabaseType.ORACLE, DatabaseType.SQLSERVER,
})
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_FILES = 20_000
MAX_MANIFEST_BYTES = 64 * 1024
_SAFE_VERSION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+/-]{0,119}$")
_VERSION_PART = re.compile(r"\d+")
_MANIFEST_FIELDS = frozenset({
    "schema_version", "database", "driver_name", "driver_version", "module",
    "distribution", "python_version", "python_abi", "windows_arch",
    "supported_database_versions", "minimum_toolkit_version",
    "required_dependencies", "native_components",
})


@dataclass(frozen=True)
class DriverPackage:
    schema_version: int
    database: DatabaseType
    driver_name: str
    driver_version: str
    module: str
    distribution: str
    python_version: str
    python_abi: str
    windows_arch: str
    supported_database_versions: tuple[str, ...]
    minimum_toolkit_version: str
    required_dependencies: tuple[tuple[str, str], ...]
    native_components: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: object) -> DriverPackage:
        if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
            raise _package_error("Driver package manifest has an invalid field set.")
        if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
            raise _package_error("Driver package manifest version is unsupported.")
        try:
            database = DatabaseType(raw["database"])
        except (ValueError, TypeError):
            raise _package_error("Driver package database type is unsupported.") from None
        if database not in SUPPORTED_DATABASES:
            raise _package_error("Only the four relational database drivers can be managed here.")
        strings = (
            "driver_name", "driver_version", "module", "distribution", "python_version",
            "python_abi", "windows_arch", "minimum_toolkit_version",
        )
        if any(not isinstance(raw.get(key), str) or not raw[key].strip()
               or not _SAFE_LABEL.fullmatch(raw[key]) for key in strings):
            raise _package_error("Driver package manifest contains an invalid value.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", raw["module"]):
            raise _package_error("Driver package module name is invalid.")
        versions = raw["supported_database_versions"]
        dependencies = raw["required_dependencies"]
        native = raw["native_components"]
        if (not isinstance(versions, list) or not versions
                or any(not isinstance(item, str) or not item.strip()
                       or not _SAFE_LABEL.fullmatch(item) for item in versions)):
            raise _package_error("Driver package must declare supported database versions or an explicit vendor range.")
        if (not isinstance(dependencies, list) or any(
                not isinstance(item, dict) or set(item) != {"distribution", "version"}
                or not isinstance(item.get("distribution"), str)
                or not _SAFE_LABEL.fullmatch(item["distribution"])
                or not isinstance(item.get("version"), str)
                or not re.fullmatch(r"(?:>=|>|<=|<|==|~=)?[0-9][A-Za-z0-9.+,!<>=~ -]{0,79}", item["version"])
                for item in dependencies)):
            raise _package_error("Driver package dependencies are invalid.")
        if not isinstance(native, list) or any(not isinstance(item, str) or not _SAFE_LABEL.fullmatch(item)
                                               for item in native):
            raise _package_error("Driver package native component list is invalid.")
        return cls(
            raw["schema_version"], database, raw["driver_name"], raw["driver_version"],
            raw["module"], raw["distribution"], raw["python_version"], raw["python_abi"],
            raw["windows_arch"], tuple(versions), raw["minimum_toolkit_version"],
            tuple((item["distribution"], item["version"]) for item in dependencies),
            tuple(native),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "database": self.database.value,
            "driver_name": self.driver_name,
            "driver_version": self.driver_version,
            "module": self.module,
            "distribution": self.distribution,
            "python_version": self.python_version,
            "python_abi": self.python_abi,
            "windows_arch": self.windows_arch,
            "supported_database_versions": list(self.supported_database_versions),
            "minimum_toolkit_version": self.minimum_toolkit_version,
            "required_dependencies": [
                {"distribution": name, "version": constraint}
                for name, constraint in self.required_dependencies
            ],
            "native_components": list(self.native_components),
        }


def _package_error(message: str, kind: str = "DriverPackageInvalid") -> ToolkitError:
    return ToolkitError(kind, message)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_json(path: Path, max_bytes: int) -> object:
    try:
        if path.is_symlink() or path.stat().st_size > max_bytes:
            raise ValueError("unsafe JSON file")
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise _package_error("Driver package state or metadata is missing, damaged, or unsafe.") from None


def _normalized_arch(value: str | None = None) -> str:
    machine = (value or platform.machine()).casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64" if struct.calcsize("P") == 8 else "x86"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def _python_abi() -> str:
    match = re.match(r"cp(\d+)", str(sysconfig.get_config_var("SOABI") or ""))
    if match:
        return "cp" + match.group(1)
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _version_parts(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in _VERSION_PART.findall(value))


def _version_at_least(current: str, minimum: str) -> bool:
    left = _version_parts(current)
    right = _version_parts(minimum)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def _version_matches(version: str, constraint: str) -> bool:
    current = _version_parts(version)
    if not current:
        return False
    for segment in constraint.split(","):
        match = re.fullmatch(r"\s*(>=|>|<=|<|==|~=)\s*([0-9]+(?:\.[0-9]+)*)\s*", segment)
        if match is None:
            return False
        expected = _version_parts(match.group(2))
        size = max(len(current), len(expected))
        lhs = current + (0,) * (size - len(current))
        rhs = expected + (0,) * (size - len(expected))
        op = match.group(1)
        if op == ">=" and not lhs >= rhs:
            return False
        if op == ">" and not lhs > rhs:
            return False
        if op == "<=" and not lhs <= rhs:
            return False
        if op == "<" and not lhs < rhs:
            return False
        if op == "==" and lhs != rhs:
            return False
        if op == "~=" and not (lhs >= rhs and lhs[:max(1, len(rhs) - 1)] == rhs[:max(1, len(rhs) - 1)]):
            return False
    return True


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _validate_driver_contract(package: DriverPackage) -> None:
    from dba.runtime import DEFAULT_MANIFEST, RuntimeManifest

    manifest = RuntimeManifest.load(DEFAULT_MANIFEST)
    requirement = manifest.drivers.get(package.database)
    if (requirement is None or package.module != requirement.module
            or _canonical_name(package.distribution) != _canonical_name(requirement.distribution)):
        raise _package_error("Driver package identity does not match the selected database contract.",
                             "DriverPackageIncompatible")
    if not _version_matches(package.driver_version, requirement.version):
        raise _package_error("Driver package version is outside the supported Toolkit driver range.",
                             "DriverPackageIncompatible")


def _manifest_at(package_root: Path) -> DriverPackage:
    raw = _read_json(package_root / "driver.json", MAX_MANIFEST_BYTES)
    return DriverPackage.from_mapping(raw)


def _validate_runtime_compatibility(package: DriverPackage) -> None:
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise _package_error("Driver package requires a 64-bit Windows runtime.",
                             "DriverPackageIncompatible")
    runtime_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if package.python_version != runtime_version or package.python_abi != _python_abi():
        raise _package_error("Driver package Python version or ABI does not match this Toolkit runtime.",
                             "DriverPackageIncompatible")
    if package.windows_arch != _normalized_arch():
        raise _package_error("Driver package architecture does not match this Windows runtime.",
                             "DriverPackageIncompatible")
    if not _version_at_least(__version__, package.minimum_toolkit_version):
        raise _package_error("Driver package requires a newer DBA Toolkit version.",
                             "DriverPackageIncompatible")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl_api = cast(Any, fcntl)
                fcntl_api.flock(stream.fileno(), fcntl_api.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl_api.flock(stream.fileno(), fcntl_api.LOCK_UN)
    except OSError:
        raise _package_error("Driver package state is locked or the local driver directory is unavailable.",
                             "DriverStoreUnavailable") from None


class DriverManager:
    """Install and select validated driver packages beneath one portable root."""

    def __init__(self, portable_home: Path | None = None) -> None:
        self.portable_home = portable_home
        self.root = (portable_home / "drivers") if portable_home is not None else None

    @classmethod
    def from_environment(cls) -> DriverManager:
        value = os.environ.get("DBA_TOOLKIT_HOME")
        return cls(Path(value).expanduser().resolve() if value else None)

    def _database_root(self, database: DatabaseType) -> Path | None:
        if self.root is None or database not in SUPPORTED_DATABASES:
            return None
        return self.root / database.value

    def _state(self, database: DatabaseType) -> dict[str, str | None]:
        folder = self._database_root(database)
        if folder is None:
            return {"active": None, "backup": None}
        path = folder / "state.json"
        if not path.exists():
            return {"active": None, "backup": None}
        raw = _read_json(path, 16 * 1024)
        if (not isinstance(raw, dict) or set(raw) != {"schema_version", "active", "backup"}
                or type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1):
            raise _package_error("Driver package state is invalid.")
        output: dict[str, str | None] = {"active": None, "backup": None}
        for key in ("active", "backup"):
            value = raw.get(key)
            if value is not None and (not isinstance(value, str) or not _SAFE_VERSION_ID.fullmatch(value)):
                raise _package_error("Driver package state contains an unsafe version reference.")
            output[key] = value
        return output

    def _write_state(self, database: DatabaseType, state: dict[str, str | None]) -> None:
        folder = self._database_root(database)
        if folder is None:
            raise _package_error("Driver management is available only from the portable distribution.",
                                 "DriverStoreUnavailable")
        folder.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema_version": 1, **state}, sort_keys=True, separators=(",", ":"))
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder,
                                             prefix=".state-", suffix=".tmp", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, folder / "state.json")
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise _package_error("Could not commit the driver version selection.",
                                 "DriverStoreUnavailable") from None

    def active_package(self, database: DatabaseType) -> tuple[Path, DriverPackage] | None:
        folder = self._database_root(database)
        if folder is None:
            return None
        state = self._state(database)
        version_id = state["active"]
        if version_id is None:
            return None
        package_root = folder / "versions" / version_id
        if package_root.is_symlink() or not package_root.is_dir():
            raise _package_error("The selected driver package is missing or unsafe.")
        resolved_folder = folder.resolve()
        try:
            package_root.resolve().relative_to(resolved_folder)
        except (ValueError, OSError):
            raise _package_error("The selected driver package is outside the driver store.") from None
        package = _manifest_at(package_root)
        if package.database != database:
            raise _package_error("The selected driver package does not match its database slot.")
        _validate_driver_contract(package)
        return package_root / "site-packages", package

    def _load_state_package(self, database: DatabaseType, key: str) -> tuple[Path, DriverPackage] | None:
        folder = self._database_root(database)
        if folder is None:
            return None
        version_id = self._state(database)[key]
        if version_id is None:
            return None
        root = folder / "versions" / version_id
        if root.is_symlink() or not root.is_dir():
            raise _package_error("A referenced driver version is missing or unsafe.")
        try:
            root.resolve().relative_to(folder.resolve())
        except (ValueError, OSError):
            raise _package_error("A referenced driver version is outside the driver store.") from None
        package = _manifest_at(root)
        if package.database != database:
            raise _package_error("A referenced driver version has the wrong database identity.")
        _validate_driver_contract(package)
        return root / "site-packages", package

    @staticmethod
    def _zip_digest(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError:
            raise _package_error("Driver ZIP cannot be read.") from None
        return digest.hexdigest()

    @staticmethod
    def _extract(archive: zipfile.ZipFile, destination: Path) -> None:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise _package_error("Driver ZIP contains too many files.")
        total_size = sum(item.file_size for item in infos)
        if total_size > MAX_UNPACKED_BYTES:
            raise _package_error("Driver ZIP exceeds the unpacked size limit.")
        seen: set[str] = set()
        for info in infos:
            raw_name = info.filename
            pure = PurePosixPath(raw_name)
            raw_parts = raw_name.split("/")
            if info.is_dir() and raw_parts and raw_parts[-1] == "":
                raw_parts.pop()
            windows_devices = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
                               *(f"lpt{n}" for n in range(1, 10))}
            unsafe_component = any(
                not part or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"\\|?*' for char in part)
                or part.split(".", 1)[0].casefold() in windows_devices
                for part in raw_parts
            )
            if (not raw_name or "\\" in raw_name or pure.is_absolute() or unsafe_component
                    or raw_name.startswith("//")):
                raise _package_error("Driver ZIP contains an unsafe path.")
            normalized_name = raw_name.rstrip("/").casefold()
            if normalized_name in seen:
                raise _package_error("Driver ZIP contains duplicate paths.")
            seen.add(normalized_name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise _package_error("Driver ZIP symbolic links are not allowed.")
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise _package_error("Driver ZIP contains a special file type.")
            if raw_name != "driver.json" and not raw_name.startswith("site-packages/"):
                raise _package_error("Driver ZIP contains files outside its approved package layout.")
            if info.flag_bits & 0x1:
                raise _package_error("Encrypted driver ZIP files are not supported.")
            target = destination.joinpath(*pure.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                raise _package_error("Driver ZIP extraction failed.") from None

    @staticmethod
    def _validate_dependencies(package: DriverPackage, package_path: Path) -> None:
        search = [str(package_path), *sys.path]
        distributions: dict[str, importlib.metadata.Distribution] = {}
        for candidate in importlib.metadata.distributions(path=search):
            name = candidate.metadata["Name"] if "Name" in candidate.metadata else None
            if name:
                distributions.setdefault(_canonical_name(name), candidate)
        for name, constraint in package.required_dependencies:
            dependency_distribution = distributions.get(_canonical_name(name))
            if (dependency_distribution is None
                    or not _version_matches(dependency_distribution.version, constraint)):
                raise _package_error("A driver package dependency is missing or incompatible.",
                                     "DriverPackageIncompatible")

    @staticmethod
    def _validate_import(package: DriverPackage, package_path: Path) -> None:
        source = (
            "import importlib, importlib.metadata, pathlib, sys\n"
            "app_root = pathlib.Path(sys.argv[5]).resolve()\n"
            "sys.path.insert(0, str(app_root))\n"
            "from dba.driver_versions import validate_driver_module_version\n"
            "root = pathlib.Path(sys.argv[1]).resolve()\n"
            "name, distribution, expected = sys.argv[2:5]\n"
            "sys.path.insert(0, str(root))\n"
            "module = importlib.import_module(name)\n"
            "loaded = pathlib.Path(module.__file__).resolve()\n"
            "loaded.relative_to(root)\n"
            "actual = importlib.metadata.version(distribution)\n"
            "assert actual == expected\n"
            "validate_driver_module_version(module, module_name=name, "
            "distribution_name=distribution, distribution_version=actual)\n"
        )
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
            env.pop(key, None)
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", source, str(package_path), package.module,
                 package.distribution, package.driver_version,
                 str(Path(__file__).resolve().parents[1])],
                capture_output=True, text=True, timeout=20, check=False, env=env,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise _package_error("Driver module isolated import validation failed.",
                                 "DriverPackageIncompatible") from None
        if result.returncode != 0:
            raise _package_error("Driver module isolated import validation failed.",
                                 "DriverPackageIncompatible")

    def install(self, archive_path: Path) -> dict[str, Any]:
        if self.root is None:
            raise _package_error("Driver install requires the portable runtime home.",
                                 "DriverStoreUnavailable")
        archive_path = archive_path.expanduser().resolve()
        try:
            size = archive_path.stat().st_size
        except OSError:
            raise _package_error("Driver ZIP cannot be read.") from None
        if size <= 0 or size > MAX_PACKAGE_BYTES:
            raise _package_error("Driver ZIP size is outside the allowed limit.")
        digest = self._zip_digest(archive_path)
        if os.name != "nt":
            raise _package_error("Windows driver packages can only be installed on Windows.",
                                 "DriverPackageIncompatible")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                manifest_info = archive.getinfo("driver.json")
                if manifest_info.file_size > MAX_MANIFEST_BYTES:
                    raise _package_error("Driver package manifest is too large.")
                package = DriverPackage.from_mapping(json.loads(
                    archive.read(manifest_info).decode("utf-8"), object_pairs_hook=_json_object))
                _validate_driver_contract(package)
                _validate_runtime_compatibility(package)
                database = package.database
                folder = self._database_root(database)
                assert folder is not None
                version_id = f"{package.driver_version}-{package.python_abi}-{digest[:16]}"
                if not _SAFE_VERSION_ID.fullmatch(version_id):
                    raise _package_error("Driver package version identifier is invalid.")
                versions = folder / "versions"
                with _exclusive_lock(folder / ".driver.lock"):
                    versions.mkdir(parents=True, exist_ok=True)
                    final_root = versions / version_id
                    if final_root.is_symlink():
                        raise _package_error("An existing driver version path is unsafe.")
                    if final_root.exists():
                        existing = _manifest_at(final_root)
                        if existing != package:
                            raise _package_error("An existing driver version has conflicting metadata.")
                        package_path = final_root / "site-packages"
                    else:
                        stage_root = Path(tempfile.mkdtemp(prefix=".install-", dir=versions))
                        try:
                            self._extract(archive, stage_root)
                            staged = _manifest_at(stage_root)
                            if staged != package or not (stage_root / "site-packages").is_dir():
                                raise _package_error("Driver package layout does not match its manifest.")
                            package_path = stage_root / "site-packages"
                            self._validate_dependencies(package, package_path)
                            self._validate_import(package, package_path)
                            os.replace(stage_root, final_root)
                        finally:
                            if stage_root.exists():
                                shutil.rmtree(stage_root, ignore_errors=True)
                        package_path = final_root / "site-packages"
                    self._validate_dependencies(package, package_path)
                    self._validate_import(package, package_path)
                    old_state = self._state(database)
                    self._write_state(database, {"active": version_id,
                                                 "backup": old_state.get("active")})
                return {"database": database.value, "driver_name": package.driver_name,
                        "driver_version": package.driver_version, "status": "INSTALLED",
                        "active_version": version_id, "rollback_available": old_state.get("active") is not None}
        except ToolkitError:
            raise
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, KeyError, TypeError):
            raise _package_error("Driver ZIP is invalid or could not be installed.") from None

    def rollback(self, database: DatabaseType) -> dict[str, Any]:
        if self.root is None:
            raise _package_error("Driver rollback requires the portable runtime home.",
                                 "DriverStoreUnavailable")
        if database not in SUPPORTED_DATABASES:
            raise _package_error("Only the four relational database drivers can be managed here.")
        folder = self._database_root(database)
        assert folder is not None
        with _exclusive_lock(folder / ".driver.lock"):
            state = self._state(database)
            backup = self._load_state_package(database, "backup")
            if backup is None:
                raise _package_error("No previous driver version is available for rollback.",
                                     "DriverRollbackUnavailable")
            backup_path, package = backup
            _validate_runtime_compatibility(package)
            self._validate_dependencies(package, backup_path)
            self._validate_import(package, backup_path)
            self._write_state(database, {"active": state["backup"], "backup": state["active"]})
        return {"database": database.value, "driver_name": package.driver_name,
                "driver_version": package.driver_version, "status": "ROLLED_BACK"}

    def list(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for database in sorted(SUPPORTED_DATABASES, key=lambda item: item.value):
            active = self._load_state_package(database, "active")
            backup = self._load_state_package(database, "backup")
            entries.append({
                "database": database.value,
                "active": ({"driver_name": active[1].driver_name,
                            "driver_version": active[1].driver_version,
                            "python_abi": active[1].python_abi} if active else None),
                "backup": ({"driver_name": backup[1].driver_name,
                            "driver_version": backup[1].driver_version} if backup else None),
                "managed": active is not None,
                "portable_management_enabled": self.root is not None,
            })
        return {"drivers": entries, "network_accessed": False}

    def check(self, database: DatabaseType | None = None) -> dict[str, Any]:
        from dba.runtime import DEFAULT_MANIFEST, RuntimeManifest

        manifest = RuntimeManifest.load(DEFAULT_MANIFEST)
        from dba.runtime import select_sqlserver_driver, version_supported

        candidates = [database] if database is not None else sorted(SUPPORTED_DATABASES,
                                                                    key=lambda item: item.value)
        checks: list[dict[str, Any]] = []
        for item in candidates:
            active = self.active_package(item)
            requirement = manifest.drivers[item]
            if active is None:
                try:
                    spec = importlib.util.find_spec(requirement.module)
                except (ImportError, ValueError):
                    spec = None
                version: str | None = None
                if spec is not None:
                    try:
                        version = importlib.metadata.version(requirement.distribution)
                    except importlib.metadata.PackageNotFoundError:
                        pass
                odbc_drivers: list[str] = []
                selected_native: str | None = None
                native_status: str | None = None
                if item == DatabaseType.SQLSERVER and version is not None:
                    try:
                        module = importlib.import_module(requirement.module)
                        odbc_drivers = sorted(str(value) for value in module.drivers())
                    except (ImportError, OSError, AttributeError):
                        pass
                    selected_native = select_sqlserver_driver(odbc_drivers)
                    native_status = "READY" if selected_native else "MISSING"
                python_status = ("MISSING" if version is None else
                                 "READY" if version_supported(version, requirement.version) else "UNSUPPORTED")
                status = ("SYSTEM_DRIVER_AVAILABLE" if python_status == "READY" else python_status)
                if item == DatabaseType.SQLSERVER and python_status == "READY" and not selected_native:
                    status = "MISSING"
                checks.append({"database": item.value,
                               "status": status,
                               "python_driver_status": python_status,
                               "driver_name": requirement.distribution,
                               "driver_version": version,
                               "managed": False,
                               "driver_database_support": "NOT_CHECKED",
                               "odbc_drivers": odbc_drivers if item == DatabaseType.SQLSERVER else None,
                               "selected_native_driver": selected_native,
                               "native_component_status": native_status})
                continue
            package_path, package = active
            _validate_runtime_compatibility(package)
            self._validate_dependencies(package, package_path)
            self._validate_import(package, package_path)
            managed_selected_native: str | None = None
            managed_odbc_drivers: list[str] = []
            if item == DatabaseType.SQLSERVER:
                module = load_database_driver(item, package.module)
                managed_odbc_drivers = sorted(str(value) for value in module.drivers())
                managed_selected_native = select_sqlserver_driver(managed_odbc_drivers)
            checks.append({"database": item.value,
                           "status": "READY" if item != DatabaseType.SQLSERVER or managed_selected_native else "MISSING",
                           "python_driver_status": "READY",
                           "driver_name": package.driver_name,
                           "driver_version": package.driver_version,
                           "managed": True,
                           "python_abi": package.python_abi,
                           "supported_database_versions": list(package.supported_database_versions),
                           "toolkit_database_profile": "NOT_CHECKED_WITHOUT_SERVER_VERSION",
                           "native_components": list(package.native_components),
                           "odbc_drivers": managed_odbc_drivers if item == DatabaseType.SQLSERVER else None,
                           "selected_native_driver": managed_selected_native,
                           "native_component_status": ("READY" if managed_selected_native else "MISSING")
                           if item == DatabaseType.SQLSERVER else None})
        return {"checks": checks, "network_accessed": False}


def managed_driver_path(database: DatabaseType) -> tuple[Path, DriverPackage] | None:
    return DriverManager.from_environment().active_package(database)


def load_database_driver(database: DatabaseType, module_name: str) -> Any:
    managed = managed_driver_path(database)
    if managed is None:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            raise ToolkitError("DriverUnavailable",
                               f"Required {database.value} Python driver is unavailable. See docs/DRIVER_SETUP.md.") from None
    package_path, package = managed
    if package.module != module_name:
        raise _package_error("Selected driver package module does not match the adapter contract.",
                             "DriverPackageIncompatible")
    _validate_runtime_compatibility(package)
    if str(package_path) not in sys.path:
        sys.path.insert(0, str(package_path))
        importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
        origin = getattr(module, "__file__", None)
        if not origin or not Path(origin).resolve().is_relative_to(package_path.resolve()):
            raise _package_error("Selected driver module was loaded outside its active package.",
                                 "DriverPackageIncompatible")
        actual = importlib.metadata.version(package.distribution)
        if actual != package.driver_version:
            raise _package_error("Selected driver metadata does not match its installed distribution.",
                                 "DriverPackageIncompatible")
        try:
            validate_driver_module_version(
                module,
                module_name=module_name,
                distribution_name=package.distribution,
                distribution_version=actual,
            )
        except DriverModuleVersionError:
            raise _package_error("Selected driver module release and distribution versions do not match.",
                                 "DriverPackageIncompatible") from None
        return module
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise ToolkitError("DriverUnavailable",
                           f"The selected {database.value} driver cannot be loaded. See docs/DRIVER_SETUP.md.") from None
