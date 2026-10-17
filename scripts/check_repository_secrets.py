"""Scan Git-index paths and literal credential patterns without printing values."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ScanFinding:
    path: str
    reason: str
    line: int | None = None


_SECRET_KEYS = r"password|passwd|pwd|secret|access_token|token"
_QUOTED_VALUE = r'''(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')'''
_ASSIGNMENT_LITERAL = re.compile(
    rf"(?i)(?<![\w])(?:{_SECRET_KEYS})(?![\w])\s*=\s*{_QUOTED_VALUE}"
)
_COLON_LITERAL = re.compile(
    rf"(?i)(?<![\w])(?:{_SECRET_KEYS})(?![\w])\s*:\s*{_QUOTED_VALUE}"
)
_BARE_VALUE = r"([A-Za-z0-9_./+!@#$%^&*~-]+)"
_CONNECTION_ASSIGNMENT = re.compile(
    rf"(?i)(?:server|data\s+source|host)\s*=\s*[^;,\s]+.{{0,200}}?"
    rf"(?:pwd|password)\s*=\s*(?:{_QUOTED_VALUE}|{_BARE_VALUE})"
)
_ENV_BARE_ASSIGNMENT = re.compile(
    rf"(?i)^\s*(?:export\s+)?(?:{_SECRET_KEYS})\s*=\s*{_BARE_VALUE}\s*$"
)
_CONFIG_BARE_ASSIGNMENT = re.compile(
    rf"(?i)^\s*[\"']?(?:{_SECRET_KEYS})[\"']?\s*:\s*{_BARE_VALUE}\s*(?:#.*)?$"
)
_URL_PASSWORD = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|oracle|mongodb(?:\+srv)?|redis)://"
    r"[^:/\s@]+:([^@/\s]+)@"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{3,}(?:\.\.\.)?)\b"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{20,})")
_PLACEHOLDER = re.compile(
    r"(?i)^(?:<(?:redacted|test[_-]?only|example[_-]?(?:password|secret|token)|"
    r"set[_-]password[_-]manually)>|\$\{[^}]+\}|your[_-].*|example[_-].*|"
    r"placeholder[_-].*|change[_-]?me|redacted|"
    r"test[_-]?(?:only|secret|password|token)|dba-test-secret-\d{6,}|"
    r"dba-jit-password-\d{6,}|examplepassword-notreal)$"
)
_FORBIDDEN_ARTIFACT = re.compile(
    r"(?i)(?:^|/)(?:reports|logs|\.venv|\.tmp|temp|tmp)(?:/|$)|"
    r"^config/inventory\.ya?ml$|^config/inventory/.+\.ya?ml$|"
    r"\.sqlplan$|(?:^|/)\.env(?:\.|$)|\.credential-(?:store|export)(?:\.|$)|\.keyring$|"
    r"(?:^|/)(?:credentials\.enc(?:\.lock)?|[^/]+\.credentials\.enc(?:\.lock)?|"
    r"master\.key|unlock\.key|recovery\.key|credential-password\.txt)$|"
    r"\.(?:key|pem|pfx|p12|secret|secrets)$"
)
_BARE_CONFIG_SUFFIXES = {".env", ".ini", ".cfg", ".conf", ".properties", ".sh"}
_SENSITIVE_NAMES = {"password", "passwd", "pwd", "secret", "access_token", "token"}


def _indexed_paths(root: Path, run: Callable[..., subprocess.CompletedProcess[bytes]]) -> list[str]:
    result = run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "Cannot list Git-index files.")
    return [
        value.decode("utf-8", errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    ]


def _indexed_content(
    root: Path,
    relative: str,
    run: Callable[..., subprocess.CompletedProcess[bytes]],
) -> bytes | None:
    result = run(
        ["git", "-C", str(root), "show", f":{relative}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout if result.returncode == 0 else None


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").strip()
    return not normalized or _PLACEHOLDER.fullmatch(normalized) is not None


def _literal_value(match: re.Match[str]) -> str:
    return match.group(1) if match.group(1) is not None else match.group(2)


def _line_findings(relative: str, line: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    if _PRIVATE_KEY.search(line):
        findings.append(ScanFinding(relative, "private key material"))
    for pattern, reason in (
        (_AWS_ACCESS_KEY, "AWS access key pattern"),
        (_GITHUB_TOKEN, "GitHub token pattern"),
    ):
        if pattern.search(line):
            findings.append(ScanFinding(relative, reason))
    bearer = _BEARER_TOKEN.search(line)
    if bearer and not _is_placeholder(bearer.group(1)):
        findings.append(ScanFinding(relative, "bearer token literal"))

    for pattern in (_ASSIGNMENT_LITERAL, _COLON_LITERAL):
        for match in pattern.finditer(line):
            if not _is_placeholder(_literal_value(match)):
                findings.append(ScanFinding(relative, "credential literal assignment"))

    connection = _CONNECTION_ASSIGNMENT.search(line)
    if connection:
        value = next((part for part in connection.groups() if part is not None), "")
        if not _is_placeholder(value) and not value.startswith(("$", "{", "%")):
            findings.append(ScanFinding(relative, "connection string contains a password"))

    suffix = Path(relative).suffix.lower()
    if suffix in _BARE_CONFIG_SUFFIXES:
        bare = _ENV_BARE_ASSIGNMENT.search(line)
        if bare and not _is_placeholder(bare.group(1)):
            findings.append(ScanFinding(relative, "credential literal assignment"))
    if suffix in {".md", ".yaml", ".yml", ".toml", ".json", ".properties", ".ini", ".cfg", ".conf"}:
        bare = _CONFIG_BARE_ASSIGNMENT.search(line)
        if bare and not _is_placeholder(bare.group(1)):
            findings.append(ScanFinding(relative, "credential literal assignment"))

    url = _URL_PASSWORD.search(line)
    if url and not _is_placeholder(url.group(1)):
        findings.append(ScanFinding(relative, "URL contains a password"))
    return list(dict.fromkeys(findings))


def _python_findings(relative: str, content: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        return [
            ScanFinding(
                relative,
                "Python syntax error prevents reliable secret classification",
                error.lineno,
            )
        ]

    def add_secret_literal(node: ast.AST, value: ast.AST) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if not _is_placeholder(value.value):
                findings.append(
                    ScanFinding(relative, "credential literal assignment", getattr(node, "lineno", None))
                )

    def target_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id.lower()
        if isinstance(node, ast.Attribute):
            return node.attr.lower()
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                return node.slice.value.lower()
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if any(target_name(target) in _SENSITIVE_NAMES for target in targets):
                add_secret_literal(node, value)
        elif isinstance(node, ast.keyword) and node.arg and node.arg.lower() in _SENSITIVE_NAMES:
            add_secret_literal(node, node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value.lower() in _SENSITIVE_NAMES:
                        add_secret_literal(node, value)

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for item in _line_findings(relative, node.value):
                if item.reason != "credential literal assignment":
                    findings.append(ScanFinding(item.path, item.reason, node.lineno))

    for line_number, line in enumerate(content.splitlines(), 1):
        for pattern, reason in (
            (_PRIVATE_KEY, "private key material"),
            (_AWS_ACCESS_KEY, "AWS access key pattern"),
            (_GITHUB_TOKEN, "GitHub token pattern"),
            (_BEARER_TOKEN, "bearer token literal"),
        ):
            if pattern.search(line):
                findings.append(ScanFinding(relative, reason, line_number))
    return list(dict.fromkeys(findings))


def scan_repository(
    root: Path,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for relative in _indexed_paths(root, run):
        normalized = relative.replace("\\", "/")
        if _FORBIDDEN_ARTIFACT.search(normalized):
            findings.append(ScanFinding(normalized, "tracked local configuration or runtime artifact"))
            continue
        data = _indexed_content(root, relative, run)
        if data is None:
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if Path(relative).suffix.lower() == ".py":
            findings.extend(_python_findings(normalized, content))
        else:
            for line_number, line in enumerate(content.splitlines(), 1):
                findings.extend(
                    ScanFinding(item.path, item.reason, line_number)
                    for item in _line_findings(normalized, line)
                )
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        findings = scan_repository(root)
    except RuntimeError:
        print("Cannot audit staged/tracked files: this directory is not a Git worktree.", file=sys.stderr)
        return 2
    if findings:
        for item in findings:
            location = f":{item.line}" if item.line is not None else ""
            print(f"FINDING {item.path}{location}: {item.reason}")
        print(f"Repository secret scan failed: {len(findings)} finding(s).")
        return 1
    print("Repository secret scan passed: no tracked artifacts or likely literal secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
