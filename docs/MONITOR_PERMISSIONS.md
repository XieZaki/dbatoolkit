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
| SQL Server | `VIEW SERVER STATE` through 2019; `VIEW SERVER PERFORMANCE STATE` on 2022+, plus `VIEW ANY DATABASE` | SQL Server only; current and cached plan DMV access follows the server version visibility capability | 2008 R2/2012: `VIEW SERVER STATE` + `VIEW ANY DATABASE`; 2014–2019: same; 2022: `VIEW SERVER PERFORMANCE STATE` + `VIEW ANY DATABASE`; optional `SELECT ON msdb.dbo.backupset` | 2008 R2/2012 use fixed `DBCC SQLPERF(LOGSPACE)`; 2014+ uses `sys.dm_db_log_space_usage`. No `sysadmin`, `CONTROL SERVER`, or `ALTER SERVER STATE`. |
| PostgreSQL | `pg_monitor`, `CONNECT` on the target database | Unsupported | `pg_monitor`, `CONNECT` | Disk-growth uses database size plus monitor-only WAL/archive/slot evidence; no slots and archive-off are normal. No `SUPERUSER` or `pg_signal_backend`. |
| MySQL | `PROCESS`, `SELECT performance_schema` (a global read-only SELECT also covers these objects) | Unsupported | No extra privilege for core schema totals; optional `PROCESS`, `REPLICATION CLIENT`, `SELECT performance_schema` for FILES/table, transaction, binlog, replica and InnoDB evidence | `PROCESS` is not inherited from slow. `SHOW BINARY LOGS` is conditional on logging being enabled and needs `REPLICATION CLIENT`; 5.7 uses `expire_logs_days`, 8.x uses `binlog_expire_logs_seconds`. No `SUPER`, `SYSTEM_USER`, root, or grant option. |
| Oracle | `CREATE SESSION`; object `SELECT` on required `SYS.V_$` views | Unsupported | Common bootstrap grants plus `SELECT` on `SYS.DBA_TABLESPACE_USAGE_METRICS`, `SYS.DBA_TABLESPACES`, `SYS.DBA_DATA_FILES`, `SYS.DBA_TEMP_FREE_SPACE`; FRA/archive/redo `SYS.V_$` grants optional | FRA disabled and `NOARCHIVELOG` are normal. No `DBA`, `SELECT_CATALOG_ROLE`, or `SYSDBA`. |

The generated statements contain the selected version profile's complete required privilege set. Optional grants are intentionally omitted so a DBA can decide whether additional evidence is needed. Disk-growth permissions are checked from Preflight grant evidence after version detection; a successful login alone is not proof of Feature readiness. MySQL optional `PROCESS`, `REPLICATION CLIENT`, and Performance Schema visibility affect optional evidence, not the minimum schema-size route. `pg_monitor` remains the supported PostgreSQL monitor role.

## Least privilege and maintenance

The tool never executes GRANT, REVOKE, CREATE USER, ALTER USER, KILL, or database configuration changes. The credentials remain governed by the existing Credential Provider and are not written to Inventory, logs, or reports. Grant and revoke principal/database identifiers are dialect-escaped; Oracle names are restricted to a simple identifier. SQL Server grants are version-aware; when version is not known, the generated script presents mutually exclusive version alternatives for DBA selection.

When adding a Collector, Analyzer, or Registry SQL, scan the referenced system objects and update the Feature's required/optional definitions, impact text, matching Preflight probes, and permission tests in the same change. The lightweight Registry SQL scanner test checks that catalog and monitor objects referenced by current scripts remain represented in Permission Registry metadata. This is a drift guard, not a complete SQL parser or a proof of effective privileges.
# Replication feature permissions

Use `dba permissions <database_id> --feature replication` to review replication readiness. SQL Server 2012+ uses its version-appropriate server-state monitoring permission; SQL Server 2008 R2 reports Availability Groups as unsupported. PostgreSQL uses `pg_monitor` and `CONNECT`. MySQL uses `REPLICATION CLIENT` for the fixed read-only status command. Oracle requires `CREATE SESSION` and `SELECT ON SYS.V_$DATABASE`; Data Guard metric, process, and destination views are optional and unavailable evidence is reported as partial. Grant and revoke statements are generated only as text for DBA review.

# Account security and feature readiness

Account Security classifies the account's risk; Permission Readiness evaluates the required and optional capabilities for one Feature from the shared Registry. These are independent results. For example, a MySQL account with only `GRANT SELECT ON *.*` is `NON_PRIVILEGED`, but `slow` is `MISSING` if `PROCESS` is absent. Permission evaluation applies scope implication: global SELECT covers database and table reads, database SELECT covers tables within that database, and table grants do not widen to a database or global grant. Implication requires the same privilege and does not cross databases. Global read-only SELECT can therefore satisfy the narrower `SELECT performance_schema` capability. `PROCESS` is required by `slow`; disk-growth uses it only for optional transaction/tablespace evidence, and `REPLICATION CLIENT` is optional for binlog inventory.

MySQL classification keeps each explicit grant's privilege set, scope, grant option, role source and `CURRENT_USER()` principal. `ALL PRIVILEGES`, the reviewed high-risk administrative privileges, and any `WITH GRANT OPTION` are classified `PRIVILEGED`. `PROCESS` and `REPLICATION CLIENT` are permitted monitor grants. Write-capable privileges are separately rejected as unsafe for the read-only account policy. Active or granted roles and newly introduced, unclassified dynamic privileges produce `UNKNOWN`; they are not mislabeled as a specific high privilege, and the adapter fails closed until the uncertainty is reviewed. `SHOW GRANTS` is a fixed reviewed Registry script. If it fails, sanitized vendor code/message is returned as evidence.

`dba check` and `dba permissions` use the same Permission Registry assessment fields: status, missing required/optional capabilities, available capabilities, and impact only when readiness is not `READY`. A slow diagnosis with missing required permissions stops before running its analyzer and returns the missing capability reason plus a reference grant generated for the exact MySQL `CURRENT_USER()` user/host. The renderer escapes SQL identifiers/literals; a reference script is text only and states Toolkit did not execute it and DBA approval is required. The framework never runs `GRANT`, `REVOKE`, or account-management statements.

## Version-aware replication grants

Replication permissions are bound atomically with SQL and parser contracts.
Use `dba permissions <id> --feature replication`; when readiness is not READY,
the result includes the complete selected profile grant rather than an
incremental grant. PostgreSQL 13–18 uses `pg_monitor` and target-database
`CONNECT`. SQL Server 2012–2019 uses `VIEW SERVER STATE`; 2022 uses
`VIEW SERVER PERFORMANCE STATE`; 2008 R2 AG is unsupported and 2025 remains
UNKNOWN. MySQL uses only `REPLICATION CLIENT`. Oracle's 11gR2 requirements are
`CREATE SESSION`, `V_$VERSION`, `V_$DATABASE`, `V_$DATAGUARD_STATS`,
`V_$MANAGED_STANDBY`, and `V_$ARCHIVE_DEST_STATUS`; 12.2+ binds the process
grant to `V_$DATAGUARD_PROCESS`. All generated statements remain manual-review
text and are never executed.

## Version-aware slow permissions

`dba permissions <id> --feature slow` resolves the declared and detected
version before assessing the slow profile. Slow required grants are bound to
the same route as its SQL and parser; optional grants are reported separately
and affect visibility only. A declared but unverified family can produce
reference SQL with readiness `UNKNOWN`; a mismatch or auto-detected-only
version does not receive a grant script.

| Database family | Required | Optional / impact |
|---|---|---|
| SQL Server 2008 R2–2019 | `VIEW SERVER STATE`, `VIEW ANY DATABASE` | `VIEW SERVER PERFORMANCE STATE`; newer performance evidence may be unavailable |
| SQL Server 2022 | `VIEW SERVER PERFORMANCE STATE`, `VIEW ANY DATABASE` | `VIEW SERVER STATE` |
| MySQL 5.7 / 8.0 / 8.4 | `PROCESS`, `SELECT performance_schema` | Cross-account sessions require PROCESS; 5.7 uses legacy InnoDB lock views |
| PostgreSQL 13–18 | `pg_monitor`, `CONNECT` | None declared; visibility follows monitoring-role behavior |
| Oracle 11gR2 / 12c / 18c+ | `CREATE SESSION` and direct SELECT on `SYS.V_$INSTANCE`, `SYS.V_$VERSION`, `SYS.V_$DATABASE`, `SYS.V_$SESSION`, `SYS.V_$SQL`, `SYS.V_$TRANSACTION` | `SYS.V_$LOCK`, `SYS.V_$LOCKED_OBJECT`; these enable the advanced lock evidence query. Current wait fields come from `V_$SESSION`; process/session-wait probes are not slow grants |

Oracle's common bootstrap objects are part of every slow profile, including
version detection and common preflight. Optional views add lock, process, or
extended wait evidence; missing optional grants lower visibility while the
session-based diagnosis can continue. No slow profile requires sysadmin,
CONTROL SERVER, SUPERUSER, root, DBA, SELECT_CATALOG_ROLE, or SYSDBA.

## Version-aware disk-growth permissions

`dba permissions <id> --feature disk-growth` resolves the exact database family
before assessing permissions or generating a complete manual grant. SQL Server
2008 R2/2012 use the compatible `DBCC SQLPERF(LOGSPACE)` query; SQL Server
2014–2019 use `VIEW SERVER STATE`, while 2022 uses `VIEW SERVER PERFORMANCE
STATE`. MySQL schema totals do not inherit slow's `PROCESS`; `PROCESS`,
`REPLICATION CLIENT`, and Performance Schema access are optional evidence paths.
PostgreSQL uses `pg_monitor` and `CONNECT`. Oracle required grants include the
common login/version/preflight bootstrap and direct SELECT on the core
tablespace, datafile and temporary-space objects. FRA, archive and redo grants
remain optional. Oracle reference SQL uses the verified runtime principal and
direct `SYS` object names. Toolkit never executes the generated statements.

