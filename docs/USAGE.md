# CLI Usage

Run `dba -h` for advanced commands. Daily use keeps to these scenario commands; defaults are human-readable and `--json` enables automation output.

```powershell
dba list
dba check <database_id>
dba status <database_id>
dba diagnose <database_id> slow
dba diagnose <database_id> disk-growth
dba diagnose <database_id> replication
dba diagnose <database_id> slow --verbose
```

`dba check` combines the selected runtime, credential lookup, connection/preflight and Permission Registry Feature readiness. `dba status` combines the existing lightweight collectors into a current snapshot. It is not a deep analyzer and does not replace scenario diagnosis.

`dba diagnose <database_id> replication` reports the selected instance's role, observed topology members, transport and apply/replay state, supported lag metrics, backlog, vendor errors, data completeness and reference DBA guidance. Use `--verbose` for additional bounded member and lag rows or `--json` for structured evidence. It never follows peer addresses or executes administrative actions. Check permissions with `dba permissions <database_id> --feature replication`.

`dba check` reports **Account Security** separately from **Permission Readiness**. A `NON_PRIVILEGED` account can still be `MISSING` required permissions for a Feature; that is not a connection failure or evidence that the account is over-privileged. Optional gaps appear as `PARTIAL`, while `READY` has no generic impact warning. A slow diagnosis is stopped when required permissions are missing and returns the specific missing capability, its Registry explanation, an identity-scoped reference GRANT, and `Toolkit Executed: NO` / `DBA Approval Required: YES`. Toolkit never runs GRANT or REVOKE.

For MySQL, the account security classifier examines structured `SHOW GRANTS` evidence, including privilege, scope, grant option, role grants and principal. Global read-only `SELECT ON *.*` is not classified as an administrator grant; it can satisfy the Performance Schema read capability, while missing `PROCESS` remains a separate readiness gap. `PROCESS` and `REPLICATION CLIENT` are monitoring capabilities. Known administrative/dangerous grants and `WITH GRANT OPTION` are rejected; active or granted roles that cannot be resolved are `UNKNOWN` and fail closed. Database permission inspection errors retain sanitized vendor evidence.

```powershell
dba check <database_id> --json
dba status <database_id> --json
dba diagnose <database_id> slow --json
```

`doctor` reads Inventory to identify required database runtimes. An uninstalled driver for a type not in Inventory is shown as `NOT_INSTALLED` and does not make Overall `MISSING`. A missing driver for a configured database type does. Use repeatable `--database` to inspect explicit types, for example `dba doctor --database sqlserver --database oracle`. Driver 17 fallback reports `READY_WITH_WARNING`.

`diagnose` defaults to a compact incident-response summary: current impact, duration, wait, root-cause candidate, source and safe next action. It writes a bounded, self-contained offline HTML DBA report; `--json` returns the machine-readable report data and `incident_summary`. Runtime logs remain JSONL. HTML prioritizes Immediate Recovery, root blockers and related evidence; tables use reviewed fixed fields and report any omitted rows. `--verbose` adds secondary signal counts and collector quality without dumping SQL or every session. Reference Kill/Cancel/Terminate text is shown only when a complete, untruncated snapshot confirms a root blocker affecting other sessions. Every such command is text only, includes rollback risk, and requires DBA approval; Toolkit never executes it.

The default report name is `<database_id>_<scenario>_<YYYYMMDD_HHMMSS>.html` in `reports/`; a same-second collision adds `_02`, `_03`, and so on. Human report times use the machine's local timezone. Pass `--report path/to/report.html` to choose an output file; a non-HTML suffix is replaced with `.html`. Reports contain no external assets and do not open a browser automatically. SQL Server plan diagnosis writes a paired `.sqlplan` only when ShowPlan XML is available; treat both files as sensitive report artifacts. Human report budgets default to 2 MiB soft / 8 MiB hard. At limits the report reduces low-priority evidence, names displayed/omitted counts, and preserves Summary, Recovery, Root Cause, critical blocking evidence and Collection Quality. `--json` is the structured output; it is not described as a complete raw snapshot beyond the collectors' own row limits.

Advanced maintenance commands remain available: `show`, `validate`, `driver-info`, `runtime-info`, `credentials`, `test-connection`, `permissions`, and `collect`. Running `dba` or `dba help` shows common tasks.

Inspect the current monitor account's implemented Feature status:

```powershell
dba permissions <database_id>
dba permissions <database_id> --feature slow
```

Prepare permission advice for manual review:

```powershell
dba permissions <database_id> --feature slow --generate-grant
dba permissions <database_id> --all-features --generate-grant
dba permissions <database_id> --feature slow --generate-revoke
dba permissions <database_id> --all-features --generate-revoke
```

Generated-only commands do not connect to the target; their permission status is `UNKNOWN`. Statements are text and are never executed. Confirm version, account host/scope, and organizational policy before manual use. A missing runtime capability is returned as structured evidence with its Feature, impact, and a suggested generated script. See [MONITOR_PERMISSIONS.md](MONITOR_PERMISSIONS.md) for permission details and supported database matrix.

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

For headless or offline machines, explicitly initialize the encrypted local store:

```powershell
dba credentials set sqlserver-monitor-example
dba credentials list
dba credentials show sqlserver-monitor-example
dba credentials test sqlserver-monitor-example
dba credentials delete sqlserver-monitor-example
dba credentials backend
dba credentials init --backend encrypted
dba credentials set sqlserver-monitor-example --backend encrypted
dba credentials rekey --backend encrypted
dba credentials unlock --ttl 30m
dba credentials status
dba credentials lock
```

`set` remains available for advanced bulk provisioning and rotation; updates and deletes require confirmation. `init` requires an explicit choice and hidden 12+ character master passphrase. The store uses AES-256-GCM and Argon2id in the user's AppData (Windows) or XDG config directory (Linux). Database commands resolve aliases in order: OS store, active encrypted session, existing encrypted store, then JIT enrollment. Add `--credential-backend auto|os|encrypted|interactive` to a database command to select the source explicitly. An existing encrypted store that cannot be unlocked fails closed. `credential_alias` remains the only credential reference in Inventory. See [CREDENTIAL_SECURITY.md](CREDENTIAL_SECURITY.md) for limitations and file handling.

For repeated encrypted-store use, `dba credentials unlock` prompts once and opens a current-user session with a 30-minute absolute TTL. Use `--ttl 5m` through `--ttl 8h` to choose a bounded lifetime; `--renew` requires the master passphrase again and replaces the session. `dba credentials status` reports `LOCKED` or the expiry; `dba credentials lock` clears the in-memory payload. Database commands still prefer a working OS credential store, then use the active encrypted session, and only prompt to unlock the encrypted file when no session is active. A JIT save through an active encrypted session writes the encrypted store and invalidates that session; the current command continues with its in-memory credential. Windows uses a current-SID Named Pipe, Linux a private Unix Socket with peer UID checks. See [CREDENTIAL_SECURITY.md](CREDENTIAL_SECURITY.md) for the threat model.
