# Windows Full and Update packages

Chinese operator documentation ships in both Full and Update packages. Start with the root `使用说明.md`; the complete manual, driver guide, update guide, and troubleshooting guide are in `docs/`. GitHub Releases also attach the standalone Chinese quick-start manual and include its SHA256 in the release checksum list.

The current source/package version remains `0.9.0`. The `0.9.0` Full ZIP is the baseline installer. Formal releases from `0.10.0` onward publish a Full ZIP, a self-contained Update ZIP, and one SHA256SUMS file. An Update ZIP carries the complete target program/runtime/SQL/templates/default driver payload; it does not depend on an intermediate Update. The updater accepts supported installations directly from the manifest's minimum version, currently `0.9.0`.

## Install and update

1. Download the Full ZIP for a first installation or computer replacement. Verify its SHA256 from the release's `SHA256SUMS.txt`, extract to a short local path, and double-click `启动.bat`.
2. For an existing installation, close all Toolkit windows. Download the latest Update ZIP, verify it against `SHA256SUMS.txt`, and place exactly one `DBAToolkit-*-Windows-x64-Update.zip` in the installation's `updates/` folder.
3. Double-click `升级.bat`. The PowerShell updater runs from `deployment/windows`, outside the replaced `runtime/` and `app/`; it never launches the old Python runtime during replacement. It refuses to proceed if it cannot confirm that Toolkit processes are stopped.

The updater extracts to a unique staging directory under `updates/`, validates the manifest, complete file list, sizes and SHA256 values, supported source/target versions, architecture, runtime and data schema, and checks available disk space. It validates the staged runtime and SQL Registry before switching program paths. Replacements are same-volume moves with a retained `backup/program-backup-*` directory. A post-update startup failure restores the old program and any default-driver selection changed by this update. The staging directory and backup are retained for review; the updater does not delete user files.

The program replacement list is explicit: runtime, app, docs, launchers, portable manifest and startup/legacy recovery scripts. `config/`, `data/`, `reports/`, `logs/`, `updates/`, `backup/`, Windows Credential Manager and the active `drivers/` tree are outside that replacement list. The root `driver-packages/` directory is also retained. The new default bundles live under `app/default-driver-packages/`; a bundle is installed only if the active managed package is proven to match the previous shipped default bundle. A user-selected/custom active driver is left unchanged. If that custom driver is incompatible with the new runtime, it remains preserved and may need a separate manual driver update.

Successful upgrades retain a program backup. To manually roll back, close the Toolkit and run `deployment/windows/rollback-portable.ps1` with the chosen backup directory. It restores only the recorded program paths and the default-driver state captured for that upgrade; it does not restore or overwrite Inventory, Credential Store, reports, logs or other user data. Keep backups until the updated installation has been accepted.

Installations created from an earlier 0.9.0 Portable ZIP predate `升级.bat`. For this one-time transition, download and extract the new 0.9.0 Full package to a separate short path, then run that extracted package's `deployment/windows/upgrade-portable.ps1` with the Full ZIP and the existing installation directory. This compatibility bridge adds the persistent Update launcher/bootstrap while preserving the existing user directories. Future Update ZIPs use the one-click flow above.

## Compatibility and data schema

Update manifests include package type, application and supported source versions, runtime, x64 architecture, data schema, driver-manifest schema, build ID, complete payload file hashes/sizes, and default-driver package hashes. A target older than the installed version is rejected. The same application version is accepted only when its build ID differs, which supports development candidate artifacts without treating an identical build as an update. Current user-data schema is `1`; this release has no data migration. If a future release changes it, the updater will refuse that package until an explicit, ordered, idempotent migration and rollback path is implemented.

The embedded manifest detects missing/corrupt files and the release checksum detects accidental ZIP corruption. These checks are not a publisher signature. Download release artifacts only from the approved GitHub release channel. This workflow does not create a tag automatically; the source package version and pushed `vX.Y.Z` tag must already match.

## Build and verify

On an authorized Windows x64 build machine, prepare the pinned CPython 3.13.17 embeddable ZIP and matching local wheelhouse. The target version must match both `pyproject.toml` and `dba.__version__`; this task does not change either version.

```powershell
.\deployment\build_full.ps1 -Version 0.9.0
.\deployment\build_update.ps1 -Version 0.9.0
.\deployment\verify_release.ps1 -Version 0.9.0
```

To build both artifacts and checksums in one pass, use `deployment/build_release.ps1`. It takes `-PythonEmbedZip`, `-Wheelhouse`, `-BuildPython`, `-BuildId`, and `-OutputDirectory`; defaults point at `dist/portable-build-inputs/`. Release builds are repeatable only when the input wheels, runtime and source revision are the same; each package records its build ID.

`build_update.ps1` can build an Update for a package version only after the project source version has been intentionally changed to that release version. It does not relabel 0.9.0 code as a future application version. The 0.9.0 baseline normally publishes Full only; a same-version Update produced from a main-branch build is a development candidate distinguished by its build ID, not a formal release.

## GitHub Actions

Pushes to `main` run pytest, Ruff and MyPy, build/verify Full and Update candidates, and upload short-retention artifacts. A `vX.Y.Z` tag additionally must match both project version declarations and runs the same tests/package checks before creating a GitHub Release with Full, Update and SHA256SUMS assets. Only the publish job receives `contents: write`; normal build jobs are read-only. Manual workflow dispatch can build an artifact and can publish only when run against a matching tag with the publish option enabled. No job creates or pushes tags.

## Limits

- The current source version is still 0.9.0; future formal releases and release tags have not been created or tested here.
- The update and rollback checks do not emulate machine power loss or failing storage hardware.
- Same-user hostile file replacement, parent-directory replacement and compromised publisher credentials are outside this mechanism's guarantee.
- Network shares and non-Windows platforms are unsupported for the Windows updater.
- No database is contacted during package build, update validation or rollback.
# Windows Full / Update package and rollback
