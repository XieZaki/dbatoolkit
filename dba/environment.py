"""Backward-compatible SQL Server driver inspection; never installs or connects."""

import importlib
import struct
import sys
from typing import Any

from dba.runtime import ODBC_17, ODBC_18, select_sqlserver_driver


def _windows_drivers() -> list[str]:
    if sys.platform != "win32":
        return []
    registry = importlib.import_module("winreg")
    view = registry.KEY_WOW64_64KEY if struct.calcsize("P") == 8 else registry.KEY_WOW64_32KEY
    try:
        with registry.OpenKey(
            registry.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers",
            0,
            registry.KEY_READ | view,
        ) as key:
            names = []
            for index in range(registry.QueryInfoKey(key)[1]):
                name, value, _ = registry.EnumValue(key, index)
                if str(value).casefold() == "installed":
                    names.append(name)
            return sorted(names)
    except OSError:
        return []


def driver_info() -> dict[str, Any]:
    version: str | None = None
    drivers: list[str] = []
    source = "unavailable"
    error: str | None = None
    try:
        module = importlib.import_module("pyodbc")
        version = str(module.version)
        drivers = sorted(str(item) for item in module.drivers())
        source = "pyodbc"
    except ImportError:
        error = "PyodbcNotInstalled"
    except Exception:
        error = "DriverInspectionFailed"
    if source == "unavailable" and sys.platform == "win32":
        try:
            drivers = _windows_drivers()
            source = "windows_registry"
        except Exception:
            error = "DriverInspectionFailed"
    selected = select_sqlserver_driver(drivers)
    ready = version is not None and error is None and selected is not None
    return {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "python_bits": struct.calcsize("P") * 8,
        "pyodbc_version": version,
        "odbc_drivers": drivers,
        "driver_source": source,
        "driver18_present": ODBC_18 in drivers,
        "driver17_present": ODBC_17 in drivers,
        "selected_driver": selected,
        "warning": ("Driver 17 compatibility mode selected; review TLS and certificate behavior."
                    if selected == ODBC_17 else None),
        "ready": ready,
        "error_type": error if error else None if ready else "NativeDriverUnavailable",
        "installation_requirement": None if ready else
        "Prepare matching pyodbc wheels and Microsoft ODBC Driver 18 or compatible Driver 17 via the approved offline process; no automatic downloads.",
    }
