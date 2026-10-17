# Monitor Permission Framework

V0.8.1 centralizes monitor permission requirements in `dba/permissions.py`. A definition is keyed by database type and Feature and records required/optional privilege labels, operational impact, and the system objects referenced by reviewed Registry SQL. `requirements_for()` applies version constraints; the CLI derives effective state only from adapter Preflight evidence. `READY` means required and optional capabilities were verified, `PARTIAL` means required capabilities were verified but optional evidence is absent, `MISSING` means a required capability is known to be absent, `UNKNOWN` means current evidence is insufficient, and `UNSUPPORTED` means the Feature is not implemented for that database type.

## Use

Inspect implemented Feature status:

```powershell
dba permissions mes-sql01
dba permissions mes-sql01 --feature slow
```

Generate minimum required privileges for one Feature or the deduplicated union:

```powershell
dba permissions mes-sql01 --feature slow --generate-grant
dba permissions mes-sql01 --all-features --generate-grant
dba permissions mes-sql01 --feature slow --generate-revoke
dba permissions mes-sql01 --all-features --generate-revoke
```

Generation reads the Inventory and Credential Provider only; it does not connect to the target or need existing permissions. Feature status is consequently `UNKNOWN` in generated-only output. Generated SQL includes a review warning and is never submitted to a database. A DBA must verify target version, principal host/scope, organizational policy, and script before manual use. Runtime permission failures carry a structured feature/impact/evidence block and a text-only script suggestion where the principal is known.

## Current privilege matrix

| Database | slow | plan | disk-growth | Notes |
|---|---|---|---|---|
| SQL Server | `VIEW SERVER STATE` through 2019; `VIEW SERVER PERFORMANCE STATE` on 2022+, plus `VIEW ANY DATABASE` | SQL Server only; current and cached plan DMV access follows the server version visibility capability | server state/database visibility; optional `SELECT ON msdb.dbo.backupset` and performance-state evidence | No `sysadmin`, `CONTROL SERVER`, or `ALTER SERVER STATE`. Current version branches must be reviewed against target SQL Server and SQL scripts. |
| PostgreSQL | `pg_monitor`, `CONNECT` on the target database | Unsupported | `pg_monitor`, `CONNECT` | No `SUPERUSER` or `pg_signal_backend`; cancellation/termination ReferenceCommands remain text only. |
| MySQL | `PROCESS`, `SELECT performance_schema` (a global read-only SELECT also covers these objects) | Unsupported | `PROCESS`, `REPLICATION CLIENT`, `SELECT performance_schema` | No `SUPER`, `SYSTEM_USER`, root, or grant option. Global `SELECT ON *.*` is broad read visibility and is not recommended as the least-privilege grant, but does not by itself make the account privileged. Generated grants use a visibly marked `<approved_monitor_client_host>` placeholder unless the connected account's exact `CURRENT_USER()` is available. |
| Oracle | `CREATE SESSION`; object `SELECT` on required `SYS.V_$` views | Unsupported | `CREATE SESSION`; object `SELECT` on required `SYS.DBA_` views; optional FRA/archive/redo `SYS.V_$` views | No `DBA`, `SELECT_CATALOG_ROLE`, or `SYSDBA`. Synonyms are rendered as underlying `SYS.V_$...` objects. |

The generated statements contain only the registry's required privilege set. Optional grants are intentionally omitted so a DBA can decide whether the additional evidence is needed. Some SQL Server disk-growth and Oracle disk-growth object permissions are not probed by connection Preflight yet; their status stays `UNKNOWN` rather than being inferred from a successful login. MySQL's replication privilege is checked from effective-grant metadata. `pg_monitor` is the supported PostgreSQL monitor role; individual PostgreSQL probes remain the runtime evidence.

## Least privilege and maintenance

The tool never executes GRANT, REVOKE, CREATE USER, ALTER USER, KILL, or database configuration changes. The credentials remain governed by the existing Credential Provider and are not written to Inventory, logs, or reports. Grant and revoke principal/database identifiers are dialect-escaped; Oracle names are restricted to a simple identifier. SQL Server grants are version-aware; when version is not known, the generated script presents mutually exclusive version alternatives for DBA selection.

When adding a Collector, Analyzer, or Registry SQL, scan the referenced system objects and update the Feature's required/optional definitions, impact text, matching Preflight probes, and permission tests in the same change. The lightweight Registry SQL scanner test checks that catalog and monitor objects referenced by current scripts remain represented in Permission Registry metadata. This is a drift guard, not a complete SQL parser or a proof of effective privileges.
# Replication feature permissions

Use `dba permissions <database_id> --feature replication` to review replication readiness. SQL Server 2012+ uses its version-appropriate server-state monitoring permission; SQL Server 2008 R2 reports Availability Groups as unsupported. PostgreSQL uses `pg_monitor` and `CONNECT`. MySQL uses `REPLICATION CLIENT` for the fixed read-only status command. Oracle requires `CREATE SESSION` and `SELECT ON SYS.V_$DATABASE`; Data Guard metric, process, and destination views are optional and unavailable evidence is reported as partial. Grant and revoke statements are generated only as text for DBA review.

# Account security and feature readiness

Account Security classifies the account's risk; Permission Readiness evaluates the required and optional capabilities for one Feature from the shared Registry. These are independent results. For example, a MySQL account with only `GRANT SELECT ON *.*` is `NON_PRIVILEGED`, but `slow` is `MISSING` if `PROCESS` is absent. Permission evaluation applies scope implication: global SELECT covers database and table reads, database SELECT covers tables within that database, and table grants do not widen to a database or global grant. Implication requires the same privilege and does not cross databases. Global read-only SELECT can therefore satisfy the narrower `SELECT performance_schema` capability. `PROCESS` is required by `slow`; `REPLICATION CLIENT` is required by `disk-growth`, not by slow diagnosis.

MySQL classification keeps each explicit grant's privilege set, scope, grant option, role source and `CURRENT_USER()` principal. `ALL PRIVILEGES`, the reviewed high-risk administrative privileges, and any `WITH GRANT OPTION` are classified `PRIVILEGED`. `PROCESS` and `REPLICATION CLIENT` are permitted monitor grants. Write-capable privileges are separately rejected as unsafe for the read-only account policy. Active or granted roles and newly introduced, unclassified dynamic privileges produce `UNKNOWN`; they are not mislabeled as a specific high privilege, and the adapter fails closed until the uncertainty is reviewed. `SHOW GRANTS` is a fixed reviewed Registry script. If it fails, sanitized vendor code/message is returned as evidence.

`dba check` and `dba permissions` use the same Permission Registry assessment fields: status, missing required/optional capabilities, available capabilities, and impact only when readiness is not `READY`. A slow diagnosis with missing required permissions stops before running its analyzer and returns the missing capability reason plus a reference grant generated for the exact MySQL `CURRENT_USER()` user/host. The renderer escapes SQL identifiers/literals; a reference script is text only and states Toolkit did not execute it and DBA approval is required. The framework never runs `GRANT`, `REVOKE`, or account-management statements.

