# CLI Usage

Run `dba -h` for advanced commands. Daily use keeps to these scenario commands; defaults are human-readable and `--json` enables automation output.

```powershell
dba list
dba check <database_id>
dba status <database_id>
dba diagnose <database_id> slow
dba diagnose <database_id> disk-growth
dba diagnose <database_id> replication
dba diagnose <sqlserver_database_id> memory-grant
dba diagnose <database_id> slow --verbose
```

`dba check` reports the selected runtime, credential lookup, connection and detected database version; it does not assess feature permissions. `dba status` combines the existing lightweight collectors into a current snapshot. It is not a deep analyzer and does not replace scenario diagnosis.

`dba diagnose <database_id> replication` reports the selected instance's role, observed topology members, transport and apply/replay state, supported lag metrics, backlog, vendor errors, data completeness and reference DBA guidance. Use `--verbose` for additional bounded member and lag rows or `--json` for structured evidence. It never follows peer addresses or executes administrative actions. Prepare monitor grants before first use using [Database Permissions](DATABASE_PERMISSIONS.md); access denials retain the database error and point to that guide.

`dba diagnose <sqlserver_database_id> memory-grant` takes one bounded SQL Server snapshot for waiting grants and a separate bounded snapshot for granted requests. Each reviewed read-only query associates `sys.dm_exec_query_memory_grants` to `sys.dm_exec_requests` by `(session_id, request_id)` within that query. Missing request context does not discard the grant row; handle conflicts and incomplete identity lower completeness. The two samples are sequential, not atomic. KB fields retain their source units. No SQL text, Showplan, cumulative Top SQL, polling, or remediation is used. A successful empty sample means only that no waiter was observed in that sample; Memory Grant-only output does not assess overall database health. Prepare the version-appropriate access before use as described in [Database Permissions](DATABASE_PERMISSIONS.md).

Database authorization is deployment preparation, not a per-feature runtime check. The current grants and version notes are maintained in [Database Permissions](DATABASE_PERMISSIONS.md). Toolkit runs the selected reviewed read-only diagnostic; when the database rejects access, it preserves the vendor error and directs the operator to review the guide. It never runs GRANT or REVOKE.

For MySQL, the account safety check examines structured `SHOW GRANTS` evidence for known administrative, write-capable, grant-option and unresolved role risks. It does not determine whether an individual diagnostic has its required grants. Collector queries report their own database access errors, with the deployment guide as the operator reference.

```powershell
dba check <database_id> --json
dba status <database_id> --json
dba diagnose <database_id> slow --json
```

`doctor` reads Inventory to identify required database runtimes. An uninstalled driver for a type not in Inventory is shown as `NOT_INSTALLED` and does not make Overall `MISSING`. A missing driver for a configured database type does. Use repeatable `--database` to inspect explicit types, for example `dba doctor --database sqlserver --database oracle`. Driver 17 fallback reports `READY_WITH_WARNING`.

`diagnose` defaults to a compact incident-response summary: current impact, duration, wait, root-cause candidate, source and safe next action. It writes a bounded, self-contained offline HTML DBA report; `--json` returns the machine-readable report data and `incident_summary`. Runtime logs remain JSONL. HTML prioritizes Immediate Recovery, root blockers and related evidence; tables use reviewed fixed fields and report any omitted rows. `--verbose` adds secondary signal counts and collector quality without dumping SQL or every session. Reference Kill/Cancel/Terminate text is shown only when a complete, untruncated snapshot confirms a root blocker affecting other sessions. Every such command is text only, includes rollback risk, and requires DBA approval; Toolkit never executes it.

Slow and Disk-Growth now print a Simplified Chinese emergency summary after their core evidence stage, then continue with supplemental evidence and write the Chinese HTML report. Already collected results are reused. Missing supplemental evidence is shown as deferred/partial, not failed. Use `--json` to suppress human output and receive one JSON object after the full run. Every capture is serial `BEST_EFFORT`, not an atomic snapshot; Disk-Growth does not infer growth rates or session causality from one sample. See [P0 emergency diagnosis](EMERGENCY_DIAGNOSIS.md) for the identity recheck and manual-action warnings.

The default report name is `<database_id>_<scenario>_<YYYYMMDD_HHMMSS>.html` in `reports/`; a same-second collision adds `_02`, `_03`, and so on. Human report times use the machine's local timezone. Pass `--report path/to/report.html` to choose an output file; a non-HTML suffix is replaced with `.html`. Reports contain no external assets and do not open a browser automatically. SQL Server plan diagnosis writes a paired `.sqlplan` only when ShowPlan XML is available; treat both files as sensitive report artifacts. Human report budgets default to 2 MiB soft / 8 MiB hard. At limits the report reduces low-priority evidence, names displayed/omitted counts, and preserves Summary, Recovery, Root Cause, critical blocking evidence and Collection Quality. `--json` is the structured output; it is not described as a complete raw snapshot beyond the collectors' own row limits.

Advanced maintenance commands remain available: `show`, `validate`, `driver-info`, `runtime-info`, `credentials`, `test-connection`, and `collect`. Running `dba` or `dba help` shows common tasks.

`doctor` and `runtime-info` are local-only: they never request passwords or connect to a database. Connection, permission inspection, collection, and diagnosis commands verify their database driver before invoking the Credential Provider. Structured SQL audit events are written to `logs/dba.jsonl` with bounded rotation (5 MB, three backups), not mixed into interactive output; logs are excluded from version control. See [DEPLOYMENT.md](DEPLOYMENT.md) for Windows/Linux bootstrap, offline wheelhouse, ODBC fallback, and upgrades.

## Connection Profiles and Credentials

Per-instance `connection` contains only non-sensitive parameters supported by that database type. The effective value is merged as `instance.connection` > database runtime settings > secure code defaults. `dba show <database_id>` shows those resolved parameters; `test-connection` reports the selected driver, target database and effective TLS/SSL/timeouts. No credentials or connection strings are included.

```yaml
# SQL Server Docker test instance
connection:
  encrypt: false
  trust_server_certificate: true
  connect_timeout: 5
  query_timeout: 10

# SQL Server production (certificate validation remains enabled)
connection:
  encrypt: true
  trust_server_certificate: false
  connect_timeout: 5
  query_timeout: 15

# PostgreSQL: sslmode is disable, allow, prefer, require, verify-ca, or verify-full
connection:
  sslmode: verify-full
  connect_timeout: 5
  query_timeout: 15

# MySQL: only supported PyMySQL SSL and timeout options are accepted
connection:
  ssl_disabled: false
  ssl_verify_cert: true
  ssl_verify_identity: true
  connect_timeout: 5
  query_timeout: 15

# Oracle uses python-oracledb Thin Mode
connection:
  connect_timeout: 5
  query_timeout: 15
```

MongoDB and Redis currently have no database Adapter, so no instance connection options are accepted yet. Runtime defaults remain available for batch defaults and old Inventory entries without `connection`.

Run a database command directly after configuring its Inventory `credential_alias`. If the alias is missing from available secure stores, Toolkit prompts for the username and hidden database password, then asks whether to save it securely (default Yes). No uses it for the current command only. If no persistent backend is available, Toolkit continues ephemerally and never initializes a store automatically. An existing encrypted store that fails to unlock or validate stops with an error rather than prompting for replacement credentials.

For headless or offline machines, explicitly initialize the writable SQLite encrypted store. Legacy JSON stores remain readable but are read-only:

```powershell
dba credentials set sqlserver-monitor-example
dba credentials list
dba credentials show sqlserver-monitor-example
dba credentials test sqlserver-monitor-example
dba credentials delete sqlserver-monitor-example
dba credentials backend
dba credentials init --backend sqlite
dba credentials set sqlserver-monitor-example --backend sqlite
dba credentials rekey --backend sqlite
dba credentials unlock --backend sqlite --ttl 30m
dba credentials status
dba credentials lock
```

`set` remains available for advanced provisioning and rotation in the selected writable backend; updates and deletes require confirmation. `init` requires an explicit backend and hidden 12+ character master passphrase. The legacy JSON Store remains available only for authenticated reads and migration; writes are rejected with a migration hint. Database commands use the OS store under `auto`; SQLite must be explicitly selected with `--credential-backend sqlite` and is never automatically chosen. An existing encrypted Store that fails to unlock fails closed. `credential_alias` remains unchanged in Inventory. See [CREDENTIAL_SECURITY.md](CREDENTIAL_SECURITY.md) for provider selection, migration and limitations.

For repeated encrypted-store use, `dba credentials unlock` prompts once and opens a current-user session with a 30-minute absolute TTL. Use `--ttl 5m` through `--ttl 8h` to choose a bounded lifetime; `--renew` requires the master passphrase again and replaces the session. `dba credentials status` reports `LOCKED` or the expiry; `dba credentials lock` clears the in-memory payload. Database commands still prefer a working OS credential store, then use the active encrypted session, and only prompt to unlock the encrypted file when no session is active. A JIT save through an active encrypted session writes the encrypted store and invalidates that session; the current command continues with its in-memory credential. Windows uses a current-SID Named Pipe, Linux a private Unix Socket with peer UID checks. See [CREDENTIAL_SECURITY.md](CREDENTIAL_SECURITY.md) for the threat model.

## Opt-in encrypted SQLite credentials (Windows)

The existing credential commands accept `--backend sqlite` and optional `--store-path <private-directory>/credentials.db`. SQLite is never selected by `auto`. Database commands select it explicitly with `--credential-backend sqlite` and optional `--credential-store-path <path>`. Explicit migration uses `dba credentials migrate --source <old-file> --destination <new-private-directory>/credentials.db`; it verifies a copy and never switches Providers or changes the source. Do not run this against real credentials without separate operational approval. See [SQLite Provider and migration](design/credential-sqlite-provider.md) for commands, quarantine/recovery, rollback after new writes, Windows-only support and security limits. Legacy JSON writes are rejected in this release.
