from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_repository_secrets import _line_findings, _python_findings, scan_repository

ROOT = Path(__file__).resolve().parents[1]


def _git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_ignore_rules_cover_local_artifacts_and_keep_examples(tmp_path: Path) -> None:
    _git(["init", "-q"], tmp_path)
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    candidates = {
        "config/inventory.yaml": True,
        "config/inventory/prod.yaml": True,
        "config/inventory/.gitkeep": False,
        "examples/inventory/sqlserver.example.yaml": False,
        "reports/report.html": True,
        "logs/dba.jsonl": True,
        "reports/plan.sqlplan": True,
        ".venv/Scripts/python.exe": True,
        ".tmp/pytest/credentials.enc": True,
        "mysql_login_test.py": True,
        "credentials.enc": True,
        "var/credentials.credentials.enc": True,
        "master.key": True,
        "unlock.key": True,
    }
    for relative, expected in candidates.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
        result = _git(["check-ignore", "--no-index", "-q", relative], tmp_path, check=False)
        assert (result.returncode == 0) is expected, relative


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_local_inventory_survives_repository_update(tmp_path: Path) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    _git(["init", "--initial-branch=main"], source)
    shutil.copyfile(ROOT / ".gitignore", source / ".gitignore")
    (source / "README.md").write_text("release one", encoding="utf-8")
    _git(["add", ".gitignore", "README.md"], source)
    _git(["-c", "user.name=DBA test", "-c", "user.email=test@example.invalid",
          "commit", "-m", "initial"], source)
    first = _git(["rev-parse", "HEAD"], source).stdout.decode().strip()
    (source / "README.md").write_text("release two", encoding="utf-8")
    _git(["add", "README.md"], source)
    _git(["-c", "user.name=DBA test", "-c", "user.email=test@example.invalid",
          "commit", "-m", "update"], source)
    local_inventory = source / "config" / "inventory" / "site.yaml"
    local_inventory.parent.mkdir(parents=True)
    local_inventory.write_text("databases: []\n", encoding="utf-8")
    _git(["checkout", "--detach", first], source)
    assert local_inventory.read_text(encoding="utf-8") == "databases: []\n"
    _git(["checkout", "main"], source)
    assert local_inventory.read_text(encoding="utf-8") == "databases: []\n"
    assert (source / "README.md").read_text(encoding="utf-8") == "release two"


def test_secret_scanner_checks_tracked_files_without_echoing_values(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    _git(["init", "-q"], tmp_path)
    safe = tmp_path / "README.md"
    safe.write_text("Never commit a password. Use credential_alias.\n", encoding="utf-8")
    _git(["add", "README.md"], tmp_path)
    assert scan_repository(tmp_path) == []
    safe.write_text("pass" + "word: " + "actual-secret-value" + "\n", encoding="utf-8")
    _git(["add", "README.md"], tmp_path)
    findings = scan_repository(tmp_path)
    assert len(findings) == 1
    assert findings[0].path == "README.md" and findings[0].line == 1
    assert "actual-secret-value" not in repr(findings)


def test_secret_scanner_detects_literal_secret_forms_without_values() -> None:
    constructed_token = "github_pat_" + "abc123" + "..."
    key_header = "-----BEGIN " + "PRIVATE KEY-----"
    cases = [
        ("pass" + "word = " + '"' + "real-looking-secret-value" + '"',
         "credential literal assignment"),
        ("PWD=mysecret", "credential literal assignment"),
        ("postgresql://" + "user:" + "secret@host/db", "URL contains a password"),
        (key_header, "private key material"),
        (constructed_token, "GitHub token pattern"),
        ("Authorization: Bearer " + "A" * 24, "bearer token literal"),
        ("AWS_ACCESS_KEY_ID=AKIA" + "1234567890ABCDEF", "AWS access key pattern"),
    ]
    for line, reason in cases:
        findings = _line_findings("config.env", line)
        assert any(item.reason == reason for item in findings)
        assert all("mysecret" not in repr(item) for item in findings)


def test_secret_scanner_allows_dynamic_values_and_explicit_test_placeholders() -> None:
    lines = [
        "password = getpass.getpass()",
        "password = credential.password",
        "token = os.environ.get('API_TOKEN')",
        'password = "<REDACTED>"',
        'password = "<TEST_ONLY>"',
        'password = "DBA-Test-Secret-987654"',
        "Server=host;Password=$password",
        "Never store passwords in Git.",
    ]
    for line in lines:
        assert _line_findings("sample.py", line) == []


def test_secret_scanner_fails_closed_on_unparseable_python() -> None:
    malformed = "pass" + "word = " + '"' + "unfinished"
    findings = _python_findings("broken.py", malformed)
    assert len(findings) == 1
    assert findings[0].reason == "Python syntax error prevents reliable secret classification"


def test_secret_scanner_uses_staged_python_content_and_ignores_dynamic_inputs(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    _git(["init", "-q"], tmp_path)
    candidate = tmp_path / "sample.py"
    dynamic_code = (
        "import getpass, os\n"
        "password = getpass.getpass()\n"
        "token = os.environ.get('API_TOKEN')\n"
        "other = credential.password\n"
    )
    candidate.write_text(dynamic_code, encoding="utf-8")
    _git(["add", "sample.py"], tmp_path)
    assert scan_repository(tmp_path) == []

    literal_code = "pass" + "word = " + '"' + "real-looking-secret-value" + '"\n'
    candidate.write_text(dynamic_code + literal_code, encoding="utf-8")
    # The scanner audits the index, so an unstaged worktree edit is not mistaken
    # for content that would enter the commit.
    assert scan_repository(tmp_path) == []
    _git(["add", "sample.py"], tmp_path)
    findings = scan_repository(tmp_path)
    assert len(findings) == 1
    assert findings[0].reason == "credential literal assignment"
    assert "real-looking-secret-value" not in repr(findings)


def test_secret_scanner_flags_tracked_runtime_artifact(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    _git(["init", "-q"], tmp_path)
    artifact = tmp_path / "reports" / "incident.html"
    artifact.parent.mkdir()
    artifact.write_text("safe redacted fixture", encoding="utf-8")
    _git(["add", "-f", "reports/incident.html"], tmp_path)
    findings = scan_repository(tmp_path)
    assert findings and findings[0].reason == "tracked local configuration or runtime artifact"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_secret_scanner_flags_encrypted_store_without_echoing_contents(tmp_path: Path) -> None:
    _git(["init", "-q"], tmp_path)
    artifact = tmp_path / "credentials.enc"
    artifact.write_text("ciphertext-value-must-not-be-printed", encoding="utf-8")
    _git(["add", "-f", "credentials.enc"], tmp_path)
    findings = scan_repository(tmp_path)
    assert findings and "ciphertext-value-must-not-be-printed" not in repr(findings)


def test_upgrade_scripts_do_not_remove_user_state_or_manage_credentials() -> None:
    paths = ("deployment/windows/upgrade.ps1", "deployment/linux/upgrade.sh",
             "deployment/windows/bootstrap.ps1", "deployment/linux/bootstrap.sh")
    scripts = [(ROOT / path).read_text(encoding="utf-8").lower() for path in paths]
    for script in scripts:
        assert "git pull" not in script
        assert "remove-item" not in script and "rm -rf" not in script
        assert "credentials set" not in script and "credentials delete" not in script


def test_upgrade_scripts_resolve_paths_for_their_native_platform() -> None:
    windows = (ROOT / "deployment/windows/upgrade.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "deployment/linux/upgrade.sh").read_text(encoding="utf-8")
    assert "Join-Path $PSScriptRoot" in windows
    assert "script_dir=$(CDPATH= cd --" in linux
    assert "D:\\DBA" not in windows and "D:\\DBA" not in linux


def test_example_inventories_use_disabled_rfc_addresses() -> None:
    files = sorted((ROOT / "examples/inventory").glob("*.example.yaml"))
    assert {path.stem.split(".")[0] for path in files} == {
        "sqlserver", "postgresql", "mysql", "oracle", "mongodb", "redis"}
    contents = "\n".join(path.read_text(encoding="utf-8") for path in files).lower()
    assert "enabled: false" in contents
    assert "credential_alias: example-only-" in contents
    assert "10.10." not in contents and "192.168." not in contents
    assert "password:" not in contents and "secret:" not in contents
