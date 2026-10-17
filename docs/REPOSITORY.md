# Repository, local configuration, and releases

## Configuration boundary

The source repository contains code, SQL Registry files, tests, documentation, deployment scripts, and sanitized examples. Each operations machine keeps its own site Inventory under `config/inventory/`; those YAML files are ignored by Git. The tracked `.gitkeep` preserves the empty directory after clone. Copy one disabled file from `examples/inventory/`, edit the local copy, and validate before use. Never commit a production Inventory, credential material, reports, logs, `.sqlplan`, or a virtual environment.

`credential_alias` may appear in a local Inventory, but username/password values remain in an OS Credential Store, explicitly initialized encrypted local store, or current-process interactive input. A Git update does not migrate credentials or rewrite local Inventory. Encrypted stores remain sensitive and must stay outside Git. `.gitignore` protects untracked paths only; it does not untrack a file or remove historical secrets.

## First install

Windows PowerShell:

```powershell
git clone <approved-repository-url>
cd DBAToolKit
.\deployment\windows\bootstrap.ps1 -Offline
# Copy a disabled example from examples\inventory\ into config\inventory\ and edit locally.
.\.venv\Scripts\dba.exe credentials set <credential_alias>
.\.venv\Scripts\dba.exe doctor
.\.venv\Scripts\dba.exe validate
.\.venv\Scripts\dba.exe check <database_id>
```

Linux:

```bash
git clone <approved-repository-url>
cd DBAToolKit
./deployment/linux/bootstrap.sh --offline
# Copy a disabled example into config/inventory/ and edit locally.
./.venv/bin/dba credentials set <credential_alias>
./.venv/bin/dba doctor
./.venv/bin/dba validate
./.venv/bin/dba check <database_id>
```

Prepare `deployment/wheelhouse/` for the target OS, Python ABI, and architecture before running offline bootstrap. Native ODBC installation remains an approved system-administration task.

## Upgrade

Review release notes and runtime manifest, then update the source checkout using the organization's approved Git workflow. The upgrade scripts do not run `git pull`; they only bootstrap the current checkout from its local wheelhouse, run `doctor`, and run `validate`.

```powershell
git pull --ff-only
.\deployment\windows\upgrade.ps1 -Database all-db
```

```bash
git pull --ff-only
./deployment/linux/upgrade.sh --database all-db
```

Use `-Database` / `--database` to install only the needed driver extra. Scripts do not delete or rewrite Inventory, reports, Credential Store entries, or database state. They never run GRANT, REVOKE, KILL, or SQL changes. A missing wheel fails locally without contacting an index.

## Rollback

Keep the previous release and its compatible wheelhouse until the new runtime passes checks. Check out the previous release tag or approved commit, bootstrap its dependencies into a matching venv, then run `doctor` and `validate`. Do not copy a `.venv` between releases or machines. Rollback does not remove local Inventory or credential entries.

## Tracking and sensitive-data checks

Before a release, inspect tracked paths and run:

```powershell
git status --short
git ls-files
git ls-files config reports logs
python scripts/check_repository_secrets.py
```

The scanner examines Git-tracked files only. It reports paths and finding types without printing candidate secret values. If a real Inventory, report, log, plan artifact, or credential file is already tracked, stop and review it; `.gitignore` does not remove it. Do not rewrite history automatically. If a secret reached a commit, rotate it and follow the organization's history-cleanup process.

If this directory is not a Git worktree, the current tracked-file state cannot be audited until it is placed in the intended repository. The scanner exits with a distinct unavailable status rather than treating that case as a clean scan.
