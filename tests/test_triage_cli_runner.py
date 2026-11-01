from __future__ import annotations

import json
import locale
import subprocess

from tests.triage_cli_runner import run_cli


def test_cli_runner_decodes_utf8_under_chinese_windows_locale(monkeypatch) -> None:
    """A GBK system default must not corrupt UTF-8 JSON emitted by the CLI."""
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale=True: "cp936")

    def fake_run(*args, **kwargs):
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "strict"
        return subprocess.CompletedProcess(
            args[0], 0, stdout='{"message":"中文状态：部分证据"}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_cli(["dba", "diagnose", "pg16", "slow", "--json"])

    assert result.returncode == 0
    assert json.loads(result.stdout)["message"] == "中文状态：部分证据"
