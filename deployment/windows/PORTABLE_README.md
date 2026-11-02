# DBA Toolkit 0.9.0 — Windows x64 Portable

This folder contains the offline Windows x64 runtime. No system Python, pip, virtual environment, or Internet connection is required after extraction.

## Start

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

`config/`, `data/`, `reports/`, `logs/`, and `drivers/` are kept separate from the application and runtime. Upgrades preserve these directories and the user Credential Store. Review `docs/DRIVER_SETUP.md` for driver package installation, rollback, and upgrade steps.

The Toolkit performs read-only diagnostics. It never executes displayed KILL/cancel/terminate commands, database changes, or grants.
