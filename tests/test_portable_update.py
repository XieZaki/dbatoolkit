from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from deployment.windows.release_package import (
    ReleasePackageError,
    _collect_files,
    create_update,
    verify_release,
    verify_update_archive,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = PROJECT_ROOT / "deployment" / "windows" / "update-bootstrap.ps1"
ROLLBACK = PROJECT_ROOT / "deployment" / "windows" / "rollback-portable.ps1"
UPDATE_TOOLS = PROJECT_ROOT / "deployment" / "windows" / "update-tools"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
DATABASES = ("mysql", "postgresql", "oracle", "sqlserver")


def _driver_zip(path: Path, database: str, version: str) -> str:
    manifest = {
        "schema_version": 1,
        "database": database,
        "driver_name": database,
        "driver_version": version,
        "module": "fake_driver",
        "distribution": database,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "windows_arch": "x86_64",
        "supported_database_versions": ["synthetic"],
        "minimum_toolkit_version": "0.9.0",
        "required_dependencies": [],
        "native_components": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("driver.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("site-packages/fake_driver.py", "READY = True\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry_source() -> str:
    return '''from __future__ import annotations
import hashlib, json, os, sys, zipfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
args = sys.argv[1:]
os.chdir(root)
if args == ["--version"]:
    manifest = json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))
    print("DBAToolkit " + manifest["application_version"])
elif "validate" in args:
    if "--inventory" not in args:
        raise SystemExit("staged validation must pass an explicit disposable Inventory")
    inventory = Path(args[args.index("--inventory") + 1])
    if not inventory.is_file() or inventory.read_text(encoding="utf-8").strip() != "databases: []":
        raise SystemExit("staged validation Inventory is missing or invalid")
    print("{\\"success\\":true}")
elif args[:2] == ["driver", "install"]:
    bundle = Path(args[args.index("--file") + 1])
    with zipfile.ZipFile(bundle) as archive:
        driver = json.loads(archive.read("driver.json"))
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    version_id = f"{driver['driver_version']}-{driver['python_abi']}-{digest[:16]}"
    folder = root / "drivers" / driver["database"]
    versions = folder / "versions"
    target = versions / version_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "driver.json").write_text(json.dumps(driver), encoding="utf-8")
    state_path = folder / "state.json"
    old = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"active": None, "backup": None}
    state = {"schema_version": 1, "active": version_id, "backup": old.get("active")}
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status":"INSTALLED","active_version":version_id}))
else:
    print("synthetic portable CLI")
'''


def _full_tree(root: Path, version: str, build_id: str, *, minimum: str = "0.9.0",
               startup_fails: bool = False, driver_versions: dict[str, str] | None = None) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=False)
    runtime = root / "runtime"
    runtime.mkdir()
    base = Path(sys.base_prefix)
    shutil.copy2(base / "python.exe", runtime / "python.exe")
    major_minor = f"{sys.version_info.major}{sys.version_info.minor}"
    for filename in ("python3.dll", f"python{major_minor}.dll", "vcruntime140.dll"):
        candidate = base / filename
        if candidate.is_file():
            shutil.copy2(candidate, runtime / filename)
    pth_name = f"python{major_minor}._pth"
    pth_lines = [str(base / f"python{major_minor}.zip"), str(base / "Lib"), str(base / "DLLs"), "."]
    (runtime / pth_name).write_text("\n".join(pth_lines) + "\n", encoding="ascii")
    app = root / "app"
    (app / "sql").mkdir(parents=True)
    (app / "default-driver-packages").mkdir(parents=True)
    (app / "portable_entry.py").write_text(_entry_source(), encoding="utf-8")
    (app / "sql" / "synthetic.sql").write_text("SELECT 1\n", encoding="utf-8")
    (app / "dba").mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "USAGE.md").write_text("synthetic docs\n", encoding="utf-8")
    manual_titles = {
        "用户操作手册.md": "# DBA Toolkit 用户操作手册",
        "DRIVER_SETUP.md": "# Windows 绿色便携版与数据库驱动",
        "UPDATE_GUIDE.md": "# Windows 便携版升级与回退指南",
        "故障排查.md": "# 常见故障排查",
    }
    for filename, title in manual_titles.items():
        (root / "docs" / filename).write_text(title + "\n合成文档内容\n", encoding="utf-8")
    (root / "使用说明.md").write_text("# DBA Toolkit Windows 便携版 — 快速入门\n合成文档内容\n", encoding="utf-8")
    (root / "README.md").write_text("synthetic portable package\n", encoding="utf-8")
    for filename in ("启动.bat", "升级.bat", "upgrade.bat", "dba.bat"):
        (root / filename).write_text("@echo off\r\n", encoding="utf-8")
    deployment = root / "deployment" / "windows"
    deployment.mkdir(parents=True)
    (deployment / "startup_check.py").write_text(
        "raise SystemExit(1)\n" if startup_fails else "raise SystemExit(0)\n", encoding="utf-8"
    )
    for source, target in (
        (PROJECT_ROOT / "deployment" / "windows" / "upgrade-portable.ps1", deployment / "upgrade-portable.ps1"),
        (PROJECT_ROOT / "deployment" / "windows" / "rollback-portable.ps1", deployment / "rollback-portable.ps1"),
        (PROJECT_ROOT / "deployment" / "windows" / "update-bootstrap.ps1", deployment / "update-bootstrap.ps1"),
    ):
        shutil.copy2(source, target)
    hashes: dict[str, str] = {}
    versions = driver_versions or {}
    for database in DATABASES:
        version_string = versions.get(database, "1.0.0" if version in {"0.9.0", "0.10.0"} else "1.1.0")
        bundle = app / "default-driver-packages" / f"{database}.zip"
        hashes[database] = _driver_zip(bundle, database, version_string)
        (root / "driver-packages").mkdir(exist_ok=True)
        shutil.copy2(bundle, root / "driver-packages" / f"{database}.zip")
    manifest = {
        "package_type": "Full",
        "application_version": version,
        "product_version": version,
        "minimum_supported_version": minimum,
        "maximum_supported_version": None,
        "runtime_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "architecture": "x64",
        "data_schema_version": 1,
        "driver_manifest_version": 1,
        "build_id": build_id,
    }
    (root / "portable-manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return hashes


def _install_tree(root: Path, version: str, *, custom_driver: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    package = root / "seed"
    default_hashes = _full_tree(package, version, f"full-{version}")
    for relative in ("runtime", "app", "docs", "deployment", "driver-packages"):
        shutil.copytree(package / relative, root / relative)
    for filename in ("启动.bat", "升级.bat", "upgrade.bat", "dba.bat", "README.md", "使用说明.md", "portable-manifest.json"):
        shutil.copy2(package / filename, root / filename)
    (root / "deployment" / "windows" / "update-bootstrap.ps1").write_bytes(BOOTSTRAP.read_bytes())
    for path, content in (
        ("config/inventory.yaml", "databases: []\n"),
        ("config/runtime.yaml", "synthetic user config\n"),
        ("data/credential.sqlite", b"SYNTHETIC_CIPHERTEXT_ONLY"),
        ("reports/old.html", "synthetic report\n"),
        ("reports/snapshot.json", "{\"synthetic\":true}\n"),
        ("logs/user.log", "synthetic log\n"),
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    for database, active_version in (("mysql", "1.0.0"), ("postgresql", "custom-9.9")):
        if database == "postgresql" and not custom_driver:
            continue
        driver_root = root / "drivers" / database
        versions = driver_root / "versions"
        versions.mkdir(parents=True)
        if database == "mysql":
            active = f"{active_version}-cp{sys.version_info.major}{sys.version_info.minor}-{default_hashes[database][:16]}"
        else:
            active = active_version
        (versions / active).mkdir()
        (versions / active / "driver.json").write_text("synthetic preserved package", encoding="utf-8")
        (driver_root / "state.json").write_text(json.dumps({
            "schema_version": 1, "active": active, "backup": None,
        }, sort_keys=True), encoding="utf-8")
    return default_hashes, _hash_user_files(root)


def _hash_user_files(root: Path) -> dict[str, str]:
    selected = (
        "config/inventory.yaml", "config/runtime.yaml", "data/credential.sqlite",
        "reports/old.html", "reports/snapshot.json", "logs/user.log",
        "drivers/postgresql/state.json", "driver-packages/custom.zip",
    )
    custom = root / "driver-packages" / "custom.zip"
    if not custom.exists():
        custom.write_bytes(b"SYNTHETIC CUSTOM DRIVER ARCHIVE")
    result: dict[str, str] = {}
    for relative in selected:
        path = root / relative
        if path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _make_update(tmp_path: Path, version: str, build_id: str, *, minimum: str = "0.9.0",
                 startup_fails: bool = False, driver_versions: dict[str, str] | None = None) -> Path:
    full = tmp_path / f"full-{version}-{build_id}"
    _full_tree(full, version, build_id, minimum=minimum,
               startup_fails=startup_fails, driver_versions=driver_versions)
    update = tmp_path / f"DBAToolkit-{version}-Windows-x64-Update.zip"
    create_update(full, update, build_id)
    with zipfile.ZipFile(update) as archive:
        verify_update_archive(archive, version)
        names = {name.casefold() for name in archive.namelist()}
        for name in ("payload/使用说明.md", "payload/docs/用户操作手册.md",
                     "payload/docs/DRIVER_SETUP.md", "payload/docs/UPDATE_GUIDE.md",
                     "payload/docs/故障排查.md"):
            assert name.casefold() in names
    return update


def test_release_verification_requires_full_update_and_standalone_chinese_manual(tmp_path: Path) -> None:
    full_root = tmp_path / "full"
    _full_tree(full_root, "0.9.0", "synthetic-release")
    release = tmp_path / "release"
    release.mkdir()
    full_zip = release / "DBAToolkit-0.9.0-Windows-x64-Full.zip"
    with zipfile.ZipFile(full_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(full_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(full_root).as_posix())
    update_zip = release / "DBAToolkit-0.9.0-Windows-x64-Update.zip"
    create_update(full_root, update_zip, "synthetic-release")
    manual = release / "使用说明.md"
    shutil.copy2(PROJECT_ROOT / "使用说明.md", manual)
    sums = release / "DBAToolkit-0.9.0-SHA256SUMS.txt"
    artifacts = (full_zip, update_zip, manual)
    sums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                             for path in artifacts), encoding="utf-8")

    result = verify_release(release, "0.9.0")

    assert result["manual_bytes"] == manual.stat().st_size
    assert result["manual_sha256"] == hashlib.sha256(manual.read_bytes()).hexdigest()


def test_update_payload_skips_runtime_bytecode_caches(tmp_path: Path) -> None:
    root = tmp_path / "program"
    cache = root / "app" / "__pycache__"
    cache.mkdir(parents=True)
    (root / "app" / "entry.py").write_text("print('portable')\n", encoding="utf-8")
    (root / "app" / "entry.pyc").write_bytes(b"synthetic stale bytecode")
    (cache / "entry.cpython-312.pyc").write_bytes(b"synthetic old runtime cache")

    files = _collect_files(root, ("app",))

    assert [name for name, _ in files] == ["app/entry.py"]


def test_release_verifier_rejects_non_chinese_manual_content(tmp_path: Path) -> None:
    full_root = tmp_path / "full"
    _full_tree(full_root, "0.9.0", "english-manual")
    (full_root / "docs" / "故障排查.md").write_text("English only\n", encoding="utf-8")
    update = tmp_path / "english-manual.zip"
    create_update(full_root, update, "english-manual")

    with zipfile.ZipFile(update) as archive, pytest.raises(ReleasePackageError):
        verify_update_archive(archive, "0.9.0")


def _run_bootstrap(root: Path, package: Path | None = None) -> subprocess.CompletedProcess[str]:
    if not POWERSHELL:
        pytest.skip("Windows PowerShell is required for the portable updater integration tests.")
    command = [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(BOOTSTRAP), "-InstallRoot", str(root)]
    if package is not None:
        command.extend(["-PackageZip", str(package)])
    return subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell portable package checks")
@pytest.mark.parametrize(("installed", "target"), (
    ("0.9.0", "0.10.0"),
    ("0.9.0", "0.11.0"),
    ("0.10.0", "0.11.0"),
))
def test_direct_update_transition_preserves_user_files_and_custom_driver(
    tmp_path: Path, installed: str, target: str
) -> None:
    root = tmp_path / "portable"
    old_defaults, before = _install_tree(root, installed)
    new_driver_versions = {database: "1.2.0" for database in DATABASES}
    update = _make_update(tmp_path, target, f"build-{target}", driver_versions=new_driver_versions)
    updates = root / "updates"
    updates.mkdir()
    shutil.copy2(update, updates / update.name)

    result = _run_bootstrap(root)

    assert result.returncode == 0, (
        "Portable Update failed its synthetic direct transition: " + result.stdout + result.stderr
    )
    current = json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))
    assert current["application_version"] == target
    assert (root / "app" / "portable_entry.py").read_bytes() != b"old program"
    assert _hash_user_files(root) == before
    mysql_state = json.loads((root / "drivers" / "mysql" / "state.json").read_text(encoding="utf-8"))
    assert mysql_state["active"].startswith("1.2.0-")
    assert mysql_state["backup"].startswith("1.0.0-")
    custom_state = (root / "drivers" / "postgresql" / "state.json").read_text(encoding="utf-8")
    assert "custom-9.9" in custom_state
    assert list((root / "backup").glob("program-backup-*"))
    assert old_defaults["mysql"]


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell portable package checks")
def test_startup_failure_restores_program_and_keeps_user_files(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    _, before = _install_tree(root, "0.9.0")
    old_app = (root / "app" / "portable_entry.py").read_bytes()
    update = _make_update(tmp_path, "0.10.0", "bad-startup", startup_fails=True)

    result = _run_bootstrap(root, update)

    assert result.returncode != 0
    assert "automatically" in (result.stdout + result.stderr).lower() or "restored" in (result.stdout + result.stderr).lower()
    assert (root / "app" / "portable_entry.py").read_bytes() == old_app
    assert json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))["application_version"] == "0.9.0"
    assert _hash_user_files(root) == before
    assert list((root / "backup").glob("program-backup-*"))


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell portable package checks")
@pytest.mark.parametrize("failure", ("truncated", "payload_hash", "missing_file", "unsupported", "disk_space"))
def test_invalid_update_fails_before_program_or_user_files_change(tmp_path: Path, failure: str) -> None:
    root = tmp_path / "portable"
    _, before = _install_tree(root, "0.9.0")
    old_app = (root / "app" / "portable_entry.py").read_bytes()
    minimum = "0.10.0" if failure == "unsupported" else "0.9.0"
    update = _make_update(tmp_path, "0.11.0", f"invalid-{failure}", minimum=minimum)
    if failure == "truncated":
        update.write_bytes(update.read_bytes()[:100])
    else:
        replacement = tmp_path / ("edited-" + update.name)
        with zipfile.ZipFile(update) as source, zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if failure == "payload_hash" and info.filename == "payload/app/sql/synthetic.sql":
                    data = b"SELECT 999\\n"
                elif failure == "missing_file" and info.filename == "payload/app/sql/synthetic.sql":
                    continue
                elif failure == "unsupported" and info.filename == "update-manifest.json":
                    manifest = json.loads(data)
                    manifest["minimum_supported_version"] = "0.10.0"
                    data = json.dumps(manifest).encode("utf-8")
                elif failure == "disk_space" and info.filename == "update-manifest.json":
                    manifest = json.loads(data)
                    manifest["payload_total_bytes"] = 2**63 - 1
                    data = json.dumps(manifest).encode("utf-8")
                target.writestr(info.filename, data)
        update = replacement

    result = _run_bootstrap(root, update)

    assert result.returncode != 0
    assert (root / "app" / "portable_entry.py").read_bytes() == old_app
    assert _hash_user_files(root) == before


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell portable package checks")
def test_running_toolkit_process_blocks_upgrade(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    _install_tree(root, "0.9.0")
    update = _make_update(tmp_path, "0.10.0", "running-check")
    process = subprocess.Popen([str(root / "runtime" / "python.exe"), "-I", "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.5)
        result = _run_bootstrap(root, update)
        assert result.returncode != 0
        assert "still running" in (result.stdout + result.stderr).lower()
        assert json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))["application_version"] == "0.9.0"
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell portable package checks")
def test_manual_rollback_restores_program_only(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    _, before = _install_tree(root, "0.9.0")
    update = _make_update(tmp_path, "0.10.0", "manual-rollback")
    result = _run_bootstrap(root, update)
    assert result.returncode == 0, result.stderr
    backup = next((root / "backup").glob("program-backup-*"))
    rolled_back = subprocess.run(
        [POWERSHELL or "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(ROLLBACK),
         "-InstallRoot", str(root), "-BackupDirectory", str(backup)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert rolled_back.returncode == 0
    assert json.loads((root / "portable-manifest.json").read_text(encoding="utf-8"))["application_version"] == "0.9.0"
    assert _hash_user_files(root) == before
