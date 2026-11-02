"""Fast, offline startup checks for the portable Windows package."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    os.environ["DBA_TOOLKIT_HOME"] = str(root)
    required = ("yaml", "keyring", "cryptography", "argon2.low_level")
    try:
        for module in required:
            importlib.import_module(module)
    except ImportError:
        print("Portable Python/core dependency is incomplete; reinstall the package.", file=sys.stderr)
        return 1

    try:
        from dba.core import DatabaseType
        from dba.driver_manager import DriverManager

        manager = DriverManager.from_environment()
    except Exception:
        print("Driver manager is unavailable; see docs/DRIVER_SETUP.md.", file=sys.stderr)
        return 1

    print(f"DBA Toolkit portable runtime: Python {sys.version_info.major}.{sys.version_info.minor} x64")
    for database in (DatabaseType.MYSQL, DatabaseType.POSTGRESQL,
                     DatabaseType.ORACLE, DatabaseType.SQLSERVER):
        try:
            item = manager.check(database)["checks"][0]
        except Exception:
            print(f"Driver {database.value}: UNAVAILABLE (see docs/DRIVER_SETUP.md)")
            continue
        status = str(item.get("status", "UNKNOWN"))
        name = str(item.get("database", "database"))
        suffix = ""
        if name == "sqlserver" and not item.get("selected_native_driver"):
            suffix = " (install ODBC Driver 18 or 17 separately)"
        if status not in {"READY", "SYSTEM_DRIVER_AVAILABLE"}:
            suffix = suffix or " (see docs/DRIVER_SETUP.md)"
        print(f"Driver {name}: {status}{suffix}")

    for directory in ("config", "data", "reports", "logs"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(root / "app" / "portable_entry.py"), "validate", "--json"],
            cwd=root, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("SQL Registry startup validation could not run.", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print("SQL Registry validation failed; the Toolkit did not start.", file=sys.stderr)
        return 1
    print("SQL Registry: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
