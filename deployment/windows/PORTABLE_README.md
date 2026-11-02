# DBA Toolkit 0.9.0 — Windows x64 Portable

This folder contains the offline Windows x64 runtime. No system Python, pip, virtual environment, or Internet connection is required after extraction.

Keep the extracted folder at a reasonably short local path. Deeply nested Windows paths can exceed legacy path limits while loading native driver components or staging an upgrade.

## Start

For a Chinese quick start, see `使用说明.md`. The complete Chinese operation, driver, update, and troubleshooting manuals are under `docs/`.

Double-click `启动.bat`, or run commands from PowerShell:

```powershell
.\dba.bat --version
.\dba.bat list
.\dba.bat validate
.\dba.bat doctor
.\dba.bat driver list
.\dba.bat driver check
```

The initial Inventory is intentionally empty. Add local assets under `config/`; do not put passwords in YAML. Read `docs/DRIVER_SETUP.md` and `docs/DATABASE_PERMISSIONS.md` before connecting to a database. SQL Server also requires an administrator-installed Microsoft ODBC Driver 18 or 17. The other databases use the bundled Python drivers; Oracle defaults to Thin Mode.

## Local files

`config/`, `data/`, `reports/`, `logs/`, `updates/`, `backup/`, and `drivers/` are kept separate from the application and runtime. Upgrades preserve these directories and Windows Credential Manager entries. Review `docs/DRIVER_SETUP.md` and `docs/PORTABLE_UPDATES.md` for driver package installation, update, and rollback steps.

## Upgrade the Toolkit

Copy exactly one current `DBAToolkit-*-Windows-x64-Update.zip` into `updates/`, close all Toolkit windows, and double-click `升级.bat`. Verify release checksums from `SHA256SUMS.txt` before copying. The update is a complete program/runtime payload and supports direct upgrades from the manifest's supported minimum version; no intermediate Update ZIP is required. The external PowerShell updater validates the payload, preserves user files and custom drivers, and retains a rollback backup. It never runs KILL/cancel/terminate commands or changes a database. See `docs/PORTABLE_UPDATES.md` for compatibility and manual rollback details.

The Toolkit performs read-only diagnostics. It never executes displayed KILL/cancel/terminate commands, database changes, or grants.
