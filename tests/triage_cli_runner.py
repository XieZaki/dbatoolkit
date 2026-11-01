"""Small UTF-8-safe subprocess wrapper for live triage verification."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def run_cli(arguments: Sequence[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a CLI command with deterministic text decoding, independent of host locale."""
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
        check=False,
    )
