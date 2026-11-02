"""Create and verify self-contained Windows x64 update archives."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

UPDATE_SCHEMA = 1
PROGRAM_PATHS = (
    "runtime",
    "app",
    "docs",
    "deployment/windows/startup_check.py",
    "deployment/windows/upgrade-portable.ps1",
    "deployment/windows/rollback-portable.ps1",
    "升级.bat",
    "upgrade.bat",
    "启动.bat",
    "dba.bat",
    "README.md",
    "使用说明.md",
    "portable-manifest.json",
)
DRIVER_NAMES = ("mysql", "postgresql", "oracle", "sqlserver")
UPDATE_TOOLS = ("apply-update.ps1", "restore-update.ps1")
CHINESE_MANUALS = (
    "使用说明.md",
    "docs/用户操作手册.md",
    "docs/DRIVER_SETUP.md",
    "docs/UPDATE_GUIDE.md",
    "docs/故障排查.md",
)


class ReleasePackageError(ValueError):
    """A package is invalid or incomplete."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _has_chinese_text(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReleasePackageError(f"{label} is not valid UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise ReleasePackageError(f"{label} must be a JSON object.")
    return value


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ReleasePackageError("Package contains an unsafe file path.")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ReleasePackageError("Package contains an unsafe file path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleasePackageError("Package contains an unsafe file path.")
    return path.as_posix()


def _collect_files(root: Path, include: tuple[str, ...]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for relative in include:
        source = root / Path(relative)
        if source.is_symlink():
            raise ReleasePackageError("Full package contains a symbolic link.")
        if not source.exists():
            raise ReleasePackageError(f"Full package is missing required program content: {relative}")
        paths = [source] if source.is_file() else sorted(source.rglob("*"), key=lambda p: p.as_posix().casefold())
        for path in paths:
            if path.is_symlink():
                raise ReleasePackageError("Full package contains a symbolic link.")
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if "__pycache__" in relative_parts or path.suffix.casefold() in {".pyc", ".pyo"}:
                continue
            rel = path.relative_to(root).as_posix()
            files.append((_safe_relative_path(rel), path))
    unique: dict[str, Path] = {}
    for name, path in files:
        key = name.casefold()
        if key in unique:
            raise ReleasePackageError("Full package contains duplicate case-insensitive paths.")
        unique[key] = path
    return sorted(files, key=lambda item: item[0].casefold())


def _driver_packages(root: Path) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for database in DRIVER_NAMES:
        relative = f"app/default-driver-packages/{database}.zip"
        path = root / Path(relative)
        if path.is_symlink() or not path.is_file():
            raise ReleasePackageError(f"Full package is missing default driver package: {database}")
        data = path.read_bytes()
        digest = _sha256(data)
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo("driver.json")
                if info.file_size > 64 * 1024:
                    raise ReleasePackageError("Default driver manifest is too large.")
                driver = _read_json_bytes(archive.read(info), "Default driver manifest")
        except (KeyError, OSError, zipfile.BadZipFile):
            raise ReleasePackageError(f"Default driver package is invalid: {database}") from None
        if driver.get("database") != database:
            raise ReleasePackageError(f"Default driver package identity is invalid: {database}")
        version = driver.get("driver_version")
        abi = driver.get("python_abi")
        if not isinstance(version, str) or not isinstance(abi, str):
            raise ReleasePackageError("Default driver package metadata is incomplete.")
        output.append({"database": database, "path": relative, "sha256": digest,
                       "driver_version": version, "python_abi": abi})
    return output


def create_update(full_root: Path, output_zip: Path, build_id: str | None = None) -> dict[str, Any]:
    root = full_root.resolve(strict=True)
    portable = _read_json_bytes((root / "portable-manifest.json").read_bytes(), "Portable manifest")
    version = portable.get("application_version") or portable.get("product_version")
    if (portable.get("package_type") != "Full" or not isinstance(version, str)
            or not re.fullmatch(r"\d+\.\d+\.\d+", version)):
        raise ReleasePackageError("Full package manifest is missing a supported application version.")
    package_build_id = build_id or portable.get("build_id")
    if not isinstance(package_build_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", package_build_id):
        raise ReleasePackageError("Update build ID is invalid.")
    minimum = portable.get("minimum_supported_version", "0.9.0")
    maximum = portable.get("maximum_supported_version")
    runtime = portable.get("runtime_version")
    data_schema = portable.get("data_schema_version", 1)
    driver_schema = portable.get("driver_manifest_version", 1)
    if (not isinstance(minimum, str) or not re.fullmatch(r"\d+\.\d+\.\d+", minimum)
            or (maximum is not None and (not isinstance(maximum, str)
                                         or not re.fullmatch(r"\d+\.\d+\.\d+", maximum)))
            or not isinstance(runtime, str) or type(data_schema) is not int
            or type(driver_schema) is not int):
        raise ReleasePackageError("Full package update compatibility metadata is invalid.")

    payload_files = _collect_files(root, PROGRAM_PATHS)
    # Driver archives ship as updateable program defaults inside app, never as user data.
    required_drivers = _driver_packages(root)
    tool_root = Path(__file__).resolve().parent / "update-tools"
    tool_files: list[tuple[str, Path]] = []
    for name in UPDATE_TOOLS:
        path = tool_root / name
        if path.is_symlink() or not path.is_file():
            raise ReleasePackageError(f"Update executor file is missing: {name}")
        tool_files.append((f"update-tools/{name}", path))

    file_manifest: list[dict[str, Any]] = []
    entries: list[tuple[str, bytes]] = []
    total = 0
    for prefix, files in (("payload", payload_files), ("", tool_files)):
        for relative, path in files:
            archive_name = f"{prefix}/{relative}" if prefix else relative
            data = path.read_bytes()
            total += len(data)
            file_manifest.append({"path": archive_name, "size": len(data), "sha256": _sha256(data)})
            entries.append((archive_name, data))
    file_manifest.sort(key=lambda item: str(item["path"]).casefold())
    manifest = {
        "manifest_version": UPDATE_SCHEMA,
        "package_type": "Update",
        "application_version": version,
        "minimum_supported_version": minimum,
        "maximum_supported_version": maximum,
        "runtime_version": runtime,
        "architecture": "x64",
        "data_schema_version": data_schema,
        "driver_manifest_version": driver_schema,
        "build_id": package_build_id,
        "payload_total_bytes": total,
        "file_manifest": file_manifest,
        "default_driver_packages": required_drivers,
    }
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        raise ReleasePackageError("Output Update ZIP already exists; choose a new output directory.")
    try:
        with zipfile.ZipFile(output_zip, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in sorted(entries, key=lambda item: item[0].casefold()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, data)
            info = zipfile.ZipInfo("update-manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    except OSError:
        output_zip.unlink(missing_ok=True)
        raise ReleasePackageError("Could not create the Update ZIP.") from None
    return manifest


def verify_update_archive(archive: zipfile.ZipFile, expected_version: str | None = None) -> dict[str, Any]:
    infos = archive.infolist()
    names: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = _safe_relative_path(info.filename.rstrip("/"))
        key = name.casefold()
        if key in names:
            raise ReleasePackageError("Update ZIP contains duplicate paths.")
        names[key] = info
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ReleasePackageError("Update ZIP contains a symbolic link.")
    manifest_info = names.get("update-manifest.json")
    if manifest_info is None or manifest_info.file_size > 4 * 1024 * 1024:
        raise ReleasePackageError("Update ZIP is missing a valid update manifest.")
    manifest = _read_json_bytes(archive.read(manifest_info), "Update manifest")
    if manifest.get("manifest_version") != UPDATE_SCHEMA or manifest.get("package_type") != "Update":
        raise ReleasePackageError("Update manifest schema or package type is unsupported.")
    version = manifest.get("application_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleasePackageError("Update application version is invalid.")
    if expected_version is not None and version != expected_version:
        raise ReleasePackageError("Update version does not match the requested release.")
    file_manifest = manifest.get("file_manifest")
    if not isinstance(file_manifest, list) or not file_manifest:
        raise ReleasePackageError("Update file manifest is missing or empty.")
    expected: dict[str, tuple[int, str]] = {}
    total = 0
    for item in file_manifest:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ReleasePackageError("Update file manifest entry is invalid.")
        path = _safe_relative_path(item["path"])
        if path == "update-manifest.json" or path.casefold() in expected:
            raise ReleasePackageError("Update file manifest contains a duplicate or reserved path.")
        if not isinstance(item["size"], int) or item["size"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"])):
            raise ReleasePackageError("Update file manifest entry is invalid.")
        expected[path.casefold()] = (item["size"], item["sha256"])
        total += item["size"]
    actual_names = {key for key in names if key != "update-manifest.json"}
    if actual_names != set(expected):
        raise ReleasePackageError("Update ZIP contents do not match the file manifest.")
    for key, (size, digest) in expected.items():
        info = names[key]
        if info.file_size != size or _sha256(archive.read(info)) != digest:
            raise ReleasePackageError("Update payload hash or size verification failed.")
    if manifest.get("payload_total_bytes") != total:
        raise ReleasePackageError("Update manifest payload size is inconsistent.")
    driver_items = manifest.get("default_driver_packages")
    if (not isinstance(driver_items, list)
            or {item.get("database") for item in driver_items if isinstance(item, dict)} != set(DRIVER_NAMES)):
        raise ReleasePackageError("Update package must declare all four default driver bundles.")
    for item in driver_items:
        if not isinstance(item, dict) or set(item) != {"database", "path", "sha256", "driver_version", "python_abi"}:
            raise ReleasePackageError("Update default driver manifest entry is invalid.")
        path = _safe_relative_path(item["path"])
        if path != f"app/default-driver-packages/{item['database']}.zip":
            raise ReleasePackageError("Update default driver path is invalid.")
        archive_path = f"payload/{path}".casefold()
        expected_entry = expected.get(archive_path)
        if expected_entry is None or expected_entry[1] != item["sha256"]:
            raise ReleasePackageError("Update default driver digest does not match its payload entry.")
        try:
            with zipfile.ZipFile(io.BytesIO(archive.read(names[archive_path]))) as driver_archive:
                driver_info = driver_archive.getinfo("driver.json")
                driver_manifest = _read_json_bytes(driver_archive.read(driver_info), "Driver manifest")
        except (OSError, KeyError, zipfile.BadZipFile):
            raise ReleasePackageError("Update default driver bundle is invalid.") from None
        if (driver_manifest.get("database") != item["database"]
                or driver_manifest.get("driver_version") != item["driver_version"]
                or driver_manifest.get("python_abi") != item["python_abi"]):
            raise ReleasePackageError("Update default driver bundle identity is inconsistent.")
    required = (
        "payload/runtime/python.exe",
        "payload/app/portable_entry.py",
        "payload/portable-manifest.json",
        *(f"payload/{path}" for path in CHINESE_MANUALS),
        "update-tools/apply-update.ps1",
        "update-tools/restore-update.ps1",
    )
    if any(name.casefold() not in names for name in required) or not any(
        name.startswith("payload/app/sql/") for name in actual_names
    ):
        raise ReleasePackageError("Update ZIP is missing required runtime, app, SQL, or recovery content.")
    for manual_path in CHINESE_MANUALS:
        archive_path = f"payload/{manual_path}".casefold()
        try:
            text = archive.read(names[archive_path]).decode("utf-8-sig")
        except (KeyError, UnicodeDecodeError):
            raise ReleasePackageError("Update ZIP contains an invalid Chinese manual.") from None
        if not text.strip() or not _has_chinese_text(text):
            raise ReleasePackageError("Update ZIP contains incomplete Chinese manual content.")
    for name in actual_names:
        lowered = name.casefold()
        if "__pycache__/" in lowered or lowered.endswith((".pyc", ".pyo")):
            raise ReleasePackageError("Update ZIP contains runtime-specific bytecode cache files.")
        if lowered.startswith(("payload/config/", "payload/data/", "payload/reports/", "payload/logs/", "payload/drivers/")):
            raise ReleasePackageError("Update ZIP contains user data or active driver state.")
    return manifest


def _verify_full_archive(path: Path, version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ReleasePackageError("Full ZIP CRC check failed.")
            names = {info.filename.casefold(): info for info in archive.infolist()}
            if any("__pycache__/" in name or name.endswith((".pyc", ".pyo")) for name in names):
                raise ReleasePackageError("Full ZIP contains runtime-specific bytecode cache files.")
            manifest_info = names.get("portable-manifest.json")
            if manifest_info is None:
                raise ReleasePackageError("Full ZIP has no portable manifest.")
            manifest = _read_json_bytes(archive.read(manifest_info), "Full package manifest")
            if manifest.get("package_type") != "Full" or manifest.get("application_version") != version:
                raise ReleasePackageError("Full package type or version is inconsistent.")
            for required in ("runtime/python.exe", "app/portable_entry.py", "app/sql",
                             "启动.bat", "升级.bat", "deployment/windows/update-bootstrap.ps1",
                             *CHINESE_MANUALS):
                if not any(name == required.casefold() or name.startswith(required.casefold() + "/") for name in names):
                    raise ReleasePackageError("Full ZIP is missing required runtime, app, SQL, or launcher content.")
            for manual_path in CHINESE_MANUALS:
                text = archive.read(names[manual_path.casefold()]).decode("utf-8-sig")
                if not text.strip() or not _has_chinese_text(text):
                    raise ReleasePackageError("Full ZIP contains an empty Chinese manual.")
            for prefix, allowed in (("data/", {"data/readme.txt"}),
                                    ("reports/", {"reports/readme.txt"}),
                                    ("logs/", {"logs/readme.txt"})):
                if any(name.startswith(prefix) and name not in allowed for name in names):
                    raise ReleasePackageError("Full ZIP contains user data or diagnostic history.")
            allowed_config = {"config/inventory.yaml", "config/runtime.yaml.example"}
            if any(name.startswith("config/") and name not in allowed_config for name in names):
                raise ReleasePackageError("Full ZIP contains unexpected local configuration.")
            forbidden_suffixes = (".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm")
            if any(name.endswith(forbidden_suffixes) for name in names):
                raise ReleasePackageError("Full ZIP contains credential or local database data.")
            inventory_info = names.get("config/inventory.yaml")
            if inventory_info is not None and archive.read(inventory_info).strip() not in {b"databases: []", b"databases: []\r\n"}:
                raise ReleasePackageError("Full ZIP contains non-empty local Inventory data.")
    except (OSError, zipfile.BadZipFile):
        raise ReleasePackageError("Full ZIP is invalid.") from None


def verify_release(directory: Path, version: str) -> dict[str, Any]:
    full = directory / f"DBAToolkit-{version}-Windows-x64-Full.zip"
    update = directory / f"DBAToolkit-{version}-Windows-x64-Update.zip"
    manual = directory / "使用说明.md"
    sums = directory / f"DBAToolkit-{version}-SHA256SUMS.txt"
    for path in (full, update, manual, sums):
        if not path.is_file():
            raise ReleasePackageError(f"Release artifact is missing: {path.name}")
    _verify_full_archive(full, version)
    try:
        with zipfile.ZipFile(update) as archive:
            verify_update_archive(archive, version)
    except (OSError, zipfile.BadZipFile):
        raise ReleasePackageError("Update ZIP is invalid.") from None
    try:
        manual_text = manual.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise ReleasePackageError("Standalone Chinese user manual is invalid.") from None
    if "DBA Toolkit" not in manual_text or "快速入门" not in manual_text or not _has_chinese_text(manual_text):
        raise ReleasePackageError("Standalone Chinese user manual content is incomplete.")
    lines = sums.read_text(encoding="utf-8-sig").splitlines()
    expected_names = {full.name, update.name, manual.name}
    found: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
        if not match:
            raise ReleasePackageError("SHA256SUMS contains an invalid line.")
        found[match.group(2)] = match.group(1).lower()
    if set(found) != expected_names:
        raise ReleasePackageError("SHA256SUMS does not describe exactly the release ZIPs.")
    for path in (full, update, manual):
        if _sha256_file(path) != found[path.name]:
            raise ReleasePackageError("Release ZIP checksum does not match SHA256SUMS.")
    return {"version": version, "full_bytes": full.stat().st_size,
            "update_bytes": update.stat().st_size, "full_sha256": found[full.name],
            "update_sha256": found[update.name], "manual_bytes": manual.stat().st_size,
            "manual_sha256": found[manual.name]}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-update")
    create.add_argument("--full-root", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--build-id", required=True)
    verify = subparsers.add_parser("verify-release")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create-update":
            manifest = create_update(args.full_root, args.output, args.build_id)
            print(f"Update package created for {manifest['application_version']} ({manifest['build_id']}).")
        else:
            result = verify_release(args.directory, args.version)
            print(json.dumps(result, sort_keys=True))
    except (OSError, ReleasePackageError):
        print("Release package operation failed validation.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
