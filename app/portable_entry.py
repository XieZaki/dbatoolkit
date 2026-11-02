"""Entry point used by the Windows portable launcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    package_root = Path(__file__).resolve().parents[1]
    os.environ["DBA_TOOLKIT_HOME"] = str(package_root)
    os.chdir(package_root)
    application = package_root / "app"
    if str(application) not in sys.path:
        sys.path.insert(0, str(application))
    arguments = list(sys.argv[1:])
    if not any(item == "--sql-root" or item.startswith("--sql-root=") for item in arguments):
        arguments = ["--sql-root", str(application / "sql"), *arguments]
    from dba.cli import main as toolkit_main

    return toolkit_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
