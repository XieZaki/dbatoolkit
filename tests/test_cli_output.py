from __future__ import annotations

from typing import Any

import dba.cli as cli


class _Stream:
    def __init__(self, *, tty: bool) -> None:
        self.tty = tty
        self.options: dict[str, Any] | None = None

    def isatty(self) -> bool:
        return self.tty

    def reconfigure(self, **kwargs: Any) -> None:
        self.options = kwargs


def test_windows_redirected_cli_streams_use_utf8(monkeypatch: Any) -> None:
    stdout = _Stream(tty=False)
    stderr = _Stream(tty=False)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._configure_windows_pipe_encoding()

    assert stdout.options == {"encoding": "utf-8", "errors": "replace"}
    assert stderr.options == {"encoding": "utf-8", "errors": "replace"}


def test_windows_interactive_console_encoding_is_preserved(monkeypatch: Any) -> None:
    stdout = _Stream(tty=True)
    stderr = _Stream(tty=True)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._configure_windows_pipe_encoding()

    assert stdout.options is None
    assert stderr.options is None


def test_cli_final_triage_fingerprint_detects_completed_label_update() -> None:
    early = ({"session_id": 58, "classification_labels": ["LONG_RUNNING_TRANSACTION"],
              "classification_status": "PARTIAL"},)
    final = ({"session_id": 58, "classification_labels": ["ROOT_BLOCKER", "LONG_RUNNING_TRANSACTION"],
              "classification_status": "CONFIRMED"},)

    early_state = cli._triage_summary_fingerprint(early, {"status": "PARTIAL", "unknown_reasons": []})
    final_state = cli._triage_summary_fingerprint(final, {"status": "COMPLETE", "unknown_reasons": []})

    assert early_state != final_state
    assert cli._triage_summary_fingerprint(final, {"status": "COMPLETE", "unknown_reasons": []}) == final_state
