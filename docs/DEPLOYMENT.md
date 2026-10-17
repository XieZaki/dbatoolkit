# Runtime and deployment

DBA Toolkit uses a native Python virtual environment as its default runtime. Docker remains an optional future packaging method. Never copy an existing `.venv` between machines: create a new environment from the Runtime Manifest and wheels built for the target OS, Python ABI, CPU architecture, and system libraries.

## Runtime definition

[`deployment/runtime-manifest.yaml`](../deployment/runtime-manifest.yaml) is the machine-readable inventory of supported Python versions, import modules, distributions, version ranges, native dependencies, Credential Store crypto libraries, and Oracle mode. `pyproject.toml` owns install extras, while `requirements/*.lock` provides matching deployment input files. Version ranges must be updated together.

The current main verification runtime is Python 3.12; the declared supported range is Python 3.11 through 3.13. Production test dependencies are kept in the `dev` extra and are not installed by a database runtime extra.

## Prepare an offline wheelhouse

On an approved connected preparation machine that matches the target OS, architecture, and Python version, build/download wheels into `deployment/wheelhouse`. For example:

```powershell
py -3.12 -m pip wheel ".[all-db]" --wheel-dir deployment\wheelhouse
```

```bash
python3.12 -m pip wheel '.[all-db]' --wheel-dir deployment/wheelhouse
```

Include build requirements such as setuptools when required by the target pip installation. A Windows wheel does not satisfy Linux; an x64 wheel does not necessarily satisfy ARM64; CPython ABI-specific wheels must match Python. Transfer the project and wheelhouse through the approved internal process. Third-party native installers do not belong in source control. Approved ODBC installers may be staged in `deployment/packages/` by the organization.

Extras are `sqlserver`, `postgresql`, `mysql`, `oracle`, `mongodb`, `redis`, and `all-db`. Prepare wheels on a connected staging machine for the target platform, then install on the isolated target only with `--no-index`:

```powershell
py -3.12 -m pip download --only-binary=:all: --dest deployment\wheelhouse ".[mysql]"
python -m pip install --no-index --find-links deployment\wheelhouse -e ".[mysql]"
```

```bash
python3.12 -m pip download --only-binary=:all: --dest deployment/wheelhouse '.[postgresql]'
python -m pip install --no-index --find-links deployment/wheelhouse -e '.[postgresql]'
```

Substitute the required extra for each database. PostgreSQL uses psycopg binary wheels; MySQL uses PyMySQL; Oracle uses python-oracledb Thin and needs no Instant Client; MongoDB uses pymongo; Redis uses redis-py. SQL Server's pyodbc wheel does not include Microsoft's ODBC driver: a system administrator installs approved Driver 18 or 17 separately. `bootstrap.ps1 -Offline` and `bootstrap.sh --offline` install from the local wheelhouse and never fall back to an index. Run `dba doctor` after installation to verify packages and native ODBC selection.

## Windows new-machine deployment

From the project root in PowerShell:

```powershell
.\deployment\windows\bootstrap.ps1 -Offline
.\.venv\Scripts\dba.exe doctor
.\.venv\Scripts\dba.exe validate
.\.venv\Scripts\dba.exe test-connection <database_id>
```

Select one database extra when a six-driver runtime is not needed:

```powershell
.\deployment\windows\bootstrap.ps1 -Offline -Database sqlserver
```

The script verifies Python/version/bitness, creates `.venv` if absent, or conservatively reuses a compatible existing Windows venv. It never deletes or silently rebuilds an existing environment. Installation always uses `--no-index --find-links deployment\wheelhouse`; a missing wheel reports the wheelhouse, requested extra, and target environment through pip's local resolution error. The script does not download packages, execute MSI files, modify PATH, or edit the registry.

## Linux new-machine deployment

The scripts are POSIX shell compatible. Mark them executable after transfer if file mode was not preserved:

```bash
chmod +x deployment/linux/bootstrap.sh deployment/linux/check-runtime.sh
./deployment/linux/bootstrap.sh --offline
./.venv/bin/dba doctor
./.venv/bin/dba validate
./.venv/bin/dba test-connection <database_id>
```

Use `--database postgresql`, `mysql`, `oracle`, `mongodb`, `redis`, or `sqlserver` to install one extra. The script uses `.venv/bin/python`, does not invoke a distribution package manager, and works without assuming either Debian/Ubuntu or RHEL/Rocky package tooling. System administrators remain responsible for native ODBC installation through the approved distribution-specific method.

## SQL Server ODBC behavior

`pyodbc` is a Python wheel but requires a system ODBC Driver. Runtime selection is deterministic:

1. `ODBC Driver 18 for SQL Server` when installed;
2. `ODBC Driver 17 for SQL Server` as compatibility fallback;
3. `DriverUnavailable` when neither exists.

Driver 17 is not treated as behaviorally identical to Driver 18. `doctor`, `runtime-info`, and legacy `driver-info` report the selected name and emit `READY_WITH_WARNING` for Driver 17. The connection string always supplies `Encrypt` and `TrustServerCertificate` from the resolved instance profile/runtime/code settings instead of relying on driver defaults. Driver 18 remains preferred because its TLS defaults and supported platform matrix differ from Driver 17. Toolkit never installs or removes either driver.

## Diagnostic reports

`dba diagnose` writes one self-contained, bounded human HTML report under `reports/` by default. It uses inline CSS and no network resources. `--json` is machine-readable and runtime logs remain JSONL. HTML is a filtered analysis report, not a raw evidence store. A plan diagnosis writes a paired `.sqlplan` next to the HTML only when ShowPlan XML is available; the XML is not embedded in HTML. Reports and SQLPLAN files can contain business SQL and object names and must remain protected local artifacts.

## Other database drivers

- PostgreSQL uses psycopg 3 binary wheels and needs no separate PostgreSQL client.
- MySQL uses PyMySQL and needs no native MySQL client.
- Oracle uses python-oracledb Thin Mode. Oracle Instant Client is not required or loaded as a deployment prerequisite.
- MongoDB uses pymongo; database features remain future work, but runtime readiness is managed now.
- Redis uses redis-py; database features remain future work, but runtime readiness is managed now.

## Inspection commands

`dba doctor` checks Python, configured database drivers, SQL Server ODBC 18/17, Oracle Thin Mode, OS Credential Store, encrypted-store libraries and interactive fallback without requesting a password or opening a database connection. Offline wheelhouses must contain `cryptography` and `argon2-cffi` for encrypted-store support. Inventory determines which database runtimes are required. Missing optional drivers are shown as `NOT_INSTALLED`; only a missing runtime used by an Inventory database makes Overall `MISSING`. `dba doctor --database sqlserver --database oracle` checks explicit types. Driver 17 fallback is `READY_WITH_WARNING`.

`dba runtime-info` shows Toolkit/Python/OS/architecture, package versions, installed/selected ODBC drivers, and Oracle mode; add `--json` for machine-readable output. Save this before and after an upgrade for audit comparison. `dba driver-info` remains as a SQL Server-focused compatibility command.

Daily operation uses `dba check <database_id>` and `dba status <database_id>`. Both default to human-readable output; append `--json` for automation. Detailed SQL execution events are kept in `logs/dba.jsonl` and are not printed during normal use.

## Connection Profiles and Credentials

Inventory entries may define type-checked, non-secret `connection` options. The resolver applies `instance.connection` first, then the corresponding database section of `--settings`, then safe code defaults. Existing Inventory without `connection` remains valid. Supported fields are SQL Server `encrypt`, `trust_server_certificate`, `connect_timeout`, `query_timeout`; PostgreSQL `sslmode`, `connect_timeout`, `query_timeout`; MySQL `ssl_disabled`, `ssl_verify_cert`, `ssl_verify_identity`, `ssl_ca`, `connect_timeout`, `query_timeout`; Oracle `connect_timeout`, `query_timeout` (mapped to driver call timeout). MongoDB and Redis have no implemented adapters and currently accept no options. Validation rejects unknown cross-database settings and sensitive keys, including nested keys.

Windows uses the Python keyring Windows Credential Manager backend. Linux uses a recognized secure Secret Service/KWallet/keychain backend; plaintext keyring fallbacks are rejected. For no-desktop hosts, `dba credentials init --backend encrypted` explicitly creates a local AES-256-GCM + Argon2id store. Offline wheelhouses must include `cryptography` and `argon2-cffi` compatible with the target OS/Python/architecture. OS Store remains preferred; existing encrypted store follows; hidden interactive input is the final fallback only when there is no existing encrypted store that failed to unlock. The encrypted store lives outside the project. Do not copy it through Git/email; it permits offline password guessing. `dba credentials set/list/show/test/delete/backend/rekey` manage entries. See [CREDENTIAL_SECURITY.md](CREDENTIAL_SECURITY.md) for threat model and recovery.

To avoid re-entering the encrypted-store master passphrase for each database command, run `dba credentials unlock --ttl 30m`, inspect with `dba credentials status`, and explicitly clear with `dba credentials lock`. TTL is absolute and bounded to 5 minutes through 8 hours. The process-memory Agent is started only on user request, uses a current-user Named Pipe ACL on Windows or private Unix socket with peer UID verification on Linux, and does not start as a service or at boot. A session ends on expiry, lock, Agent exit, reboot, or encrypted-store file change. No session secret is persisted. OS Credential Manager/Keyring remains preferred over this optional encrypted-store session.

Connection commands check the selected database's local runtime before invoking the Credential Provider. Missing Python/native drivers therefore fail quickly with `DriverUnavailable` and an instruction to run `dba doctor`.

## Runtime upgrades and rollback

1. Obtain the approved Toolkit release and review release notes, `runtime-manifest.yaml`, and lock inputs.
2. Prepare a new target-specific wheelhouse on a matching connected build machine.
3. Save `dba runtime-info` from the old runtime.
4. Create a new venv beside the old one, or move the old `.venv` aside and let bootstrap create a fresh one. Do not copy or mutate a venv from another machine.
5. For SQL Server 17→18, have a system administrator install approved Driver 18. Both can coexist; Toolkit selects 18 automatically. Do not uninstall 17 merely to run Toolkit.
6. Upgrade python-oracledb as a wheel; Thin Mode remains the default and needs no Instant Client.
7. Run `dba doctor`, `dba runtime-info`, `dba validate`, then one explicitly selected `test-connection` per database type.

Rollback by retaining the old project release, its matching wheelhouse/manifest, and its untouched venv until verification completes. Switch back to that directory/runtime; never try to repair rollback by copying files between virtual environments.

## Repository upgrade scripts

After the approved repository update, `deployment/windows/upgrade.ps1` or `deployment/linux/upgrade.sh` reuses or creates the local venv, installs only from the local wheelhouse, then runs `dba doctor` and `dba validate`. These scripts do not pull source, overwrite Inventory, access Credential Store contents, delete reports, or alter databases. See [REPOSITORY.md](REPOSITORY.md) for first install, upgrade, and rollback steps.

## Common errors

- `Wheelhouse contains no wheels`: prepare compatible wheels before transfer.
- `No matching distribution found`: the wheel for the package/version/OS/Python/architecture is absent.
- SQL Server `DriverUnavailable`: install approved ODBC Driver 18 or compatible Driver 17 at the OS level, then rerun `dba doctor`.
- `READY_WITH_WARNING`: Driver 17 was selected; validate TLS/certificate compatibility with the target environment.
- SQL Server `ConnectionTLSFailed`: compare the reported effective TLS settings with a DBA-approved connection policy. Toolkit defaults to `Encrypt=yes` and `TrustServerCertificate=no` and never retries with weaker settings. For an isolated test instance that is explicitly approved to use an unencrypted connection, set `encrypt: false` and `trust_server_certificate: true` in that instance's Inventory `connection` mapping. Do not weaken shared production defaults to accommodate a test server.
- Oracle package missing: add the python-oracledb wheel. Do not install Instant Client for the default Thin runtime.
- Existing venv incompatible: move it aside deliberately and rerun bootstrap. Bootstrap will not delete it.
