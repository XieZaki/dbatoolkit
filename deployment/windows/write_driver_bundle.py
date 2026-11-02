"""Create a deterministic, independently installable local driver ZIP."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import struct
import sys
import zipfile
from pathlib import Path

DATABASES = {
    "mysql": ("pymysql", "PyMySQL", ["MySQL 5.7", "MySQL 8.0", "MySQL 8.4"], []),
    "postgresql": ("psycopg", "psycopg", ["PostgreSQL 10-18 (driver library range)"], []),
    "oracle": ("oracledb", "oracledb", ["Oracle 12.1+ Thin; Oracle 11g requires Thick mode"], []),
    "sqlserver": ("pyodbc", "pyodbc", ["Depends on installed Microsoft ODBC Driver"], [
        "Microsoft ODBC Driver 18 or 17 must be installed separately",
    ]),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--database", choices=tuple(DATABASES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-toolkit-version", required=True)
    args = parser.parse_args()
    if platform.system() != "Windows" or struct.calcsize("P") != 8:
        raise SystemExit("Driver bundles must be built with 64-bit Windows Python.")
    module_name, distribution_name, supported_versions, native = DATABASES[args.database]
    package_root = args.site_packages.resolve(strict=True)
    distributions = list(importlib.metadata.distributions(path=[str(package_root)]))
    by_name = {}
    for item in distributions:
        name = item.metadata["Name"] if "Name" in item.metadata else ""
        by_name[name.casefold().replace("_", "-")] = item
    main_dist = by_name.get(distribution_name.casefold().replace("_", "-"))
    if main_dist is None:
        raise SystemExit("Driver distribution metadata is missing from the build stage.")
    sys.path.insert(0, str(package_root))
    module = importlib.import_module(module_name)
    if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(package_root):
        raise SystemExit("Driver module did not load from the isolated build stage.")
    module_version = getattr(module, "__version__", getattr(module, "version", None))
    if module_version is not None and str(module_version) != main_dist.version:
        raise SystemExit("Driver module version and distribution metadata differ.")

    dependencies = [
        {"distribution": item.metadata["Name"], "version": f"=={item.version}"}
        for item in sorted(distributions, key=lambda dist: (
            (dist.metadata["Name"] if "Name" in dist.metadata else "").casefold()
        ))
        if (item.metadata["Name"] if "Name" in item.metadata else "").casefold()
        != distribution_name.casefold()
    ]
    manifest = {
        "schema_version": 1,
        "database": args.database,
        "driver_name": distribution_name,
        "driver_version": main_dist.version,
        "module": module_name,
        "distribution": distribution_name,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "windows_arch": "x86_64",
        "supported_database_versions": supported_versions,
        "minimum_toolkit_version": args.minimum_toolkit_version,
        "required_dependencies": dependencies,
        "native_components": native,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        info = zipfile.ZipInfo("driver.json", (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100600 << 16
        archive.writestr(info, json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(package_root).as_posix()
            info = zipfile.ZipInfo(f"site-packages/{relative}", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, path.read_bytes())
    print(f"Created {args.database} driver package {main_dist.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
