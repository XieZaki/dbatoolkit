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
