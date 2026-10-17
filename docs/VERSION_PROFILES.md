# Version Profiles (V0.9.1 Phase B)

Phase A added version declaration and detection. Phase B migrated replication;
Phase C migrates `slow`. Slow SQL, permission requirements, parser aliases,
required/optional collectors and support state are selected together by the
immutable `DiagnosticProfileResolver`. Disk-growth and plan remain on their
existing paths.

## Inventory declaration

Set `version_family` to a quoted canonical string on each asset. Quoting avoids
YAML parsing a value such as `8.0` as a number.

```yaml
databases:
  - id: sql-prod-example
    db_type: sqlserver
    version_family: "2022"
    name: Example SQL Server
    host: sqlserver.example.invalid
    port: 1433
    credential_alias: sqlserver-monitor-example
```

Canonical families are SQL Server `2008r2`, `2012`, `2014`, `2016`, `2017`,
`2019`, `2022`, `2025`; MySQL `5.7`, `8.0`, `8.4`; PostgreSQL `13` through
`18`; and Oracle `11gr2`, `12c`, `18c`, `19c`, `21c`, `23ai`, `26ai`.

The field is optional during migration. `dba validate` stays offline, accepts
older Inventory entries and warns when a family is absent. It rejects numeric
YAML values, non-canonical strings, and the literal `unknown`. Missing family
does not get written back to Inventory. `dba check` may show a known detected
family as `AUTO_DETECTED` and recommends declaring it. A declared/detected
mismatch or failed detection makes permission readiness `UNKNOWN` for check.

## Detection and low-privilege requirements

Version queries are fixed, reviewed, bounded `READ_ONLY` Registry scripts and
run through the connected Adapter. SQL Server uses `SERVERPROPERTY`;
PostgreSQL uses `current_setting`; MySQL uses `VERSION()`; Oracle reads
`V$VERSION.BANNER`. Oracle version verification requires a direct object grant
on `SYS.V_$VERSION` (`GRANT SELECT ON SYS.V_$VERSION TO <monitor_account>`).
Toolkit reports the sanitized vendor error and the required object when this
view cannot be read; it never grants the permission. The complete Oracle banner
is retained in the structured check result, not emitted into structured logs.

Unknown future versions have no latest-version fallback. SQL Server 10.50 maps
to 2008 R2, while 10.0 remains unknown. MySQL 8.0 detection also preserves the
`pre_8022` / `8022_plus` runtime variant boundary. Oracle normalization uses
banner and release text together so a `23.26` release can map to 26ai without
being mistaken for 23ai.

## Commands

```powershell
dba show sql-prod-example
dba validate
dba check sql-prod-example
dba check sql-prod-example --json
```

`show` displays only the declared Inventory family and does not connect.
`validate` is static and offline. `check` displays declared family, detected
raw version, normalized family and verification status alongside the existing
account and permission summary. It does not persist detected versions.

The central `DiagnosticProfileResolver` returns a frozen profile containing the
SQL bundle, permission profile, parser contract and support state from one
binding. Replication and slow require declared and detected families to match
before any version-specific feature SQL runs. `AUTO_DETECTED`, `UNVERIFIED`,
`MISMATCH`, and unknown variants fail closed for diagnosis. `check` and
`permissions` report permission readiness as UNKNOWN without verified version
evidence. An unverified declared family may be used only for reference grant
guidance, never to claim readiness.

Slow SQL selection is profile-owned; collectors do not branch on database
version. Required collector failures stop diagnosis. Optional failures retain
available evidence and set `data_visibility` to `PARTIAL`; truncation on
required evidence marks the report incomplete. Long-running thresholds remain
in Analyzer/runtime configuration, not version profiles.

## Extending profiles

When migrating a feature, add one complete static binding for each supported
family/variant. Review SQL compatibility, minimum permissions, parser shape and
support together; update the corresponding Preflight and tests. `dba validate`
checks canonical family references, duplicate routes, component references,
variant constraints and forbidden administrator privileges in binding
metadata. It does not infer privileges from arbitrary SQL or documentation.

## Replication support matrix

| Database family | Support | Selected profile | Evidence in this phase |
|---|---|---|---|
| SQL Server 2008 R2 | UNSUPPORTED | No AG SQL | Unit/regression tested; no AG statement is executed |
| SQL Server 2012, 2014, 2016, 2017, 2019 | SUPPORTED | Shared pre-2022 AG SQL/parser; `VIEW SERVER STATE` | Static and Fake Adapter integration tested; not E2E verified in Phase B |
| SQL Server 2022 | SUPPORTED | Explicit 2022 AG profile; `VIEW SERVER PERFORMANCE STATE` | Static and Fake Adapter integration tested; not E2E verified in Phase B |
| SQL Server 2025 | UNKNOWN | No inherited SQL route | Fail-closed unit tested |
| MySQL 5.7 | SUPPORTED | `SHOW SLAVE STATUS` | Static and Fake Adapter integration tested; not E2E verified in Phase B |
| MySQL 8.0 < 8.0.22 | SUPPORTED | `SHOW SLAVE STATUS`, legacy aliases | Boundary and Fake Adapter tested; not E2E verified in Phase B |
| MySQL 8.0 >= 8.0.22 / 8.4 | SUPPORTED | `SHOW REPLICA STATUS`, modern aliases | Boundary and Fake Adapter tested; not E2E verified in Phase B |
| PostgreSQL 13–18 | SUPPORTED | Shared explicit `pg13_18_replication` profile | Resolver family and Fake Adapter tested; not E2E verified in Phase B |
| Oracle 11gR2 | SUPPORTED | `V_$MANAGED_STANDBY` legacy Data Guard profile | SQL/grant/parser Fake Adapter integration tested; NOT E2E VERIFIED |
| Oracle 12c 12.1 | SUPPORTED | Legacy `V_$MANAGED_STANDBY` profile | Static and Fake Adapter tested; NOT E2E VERIFIED |
| Oracle 12c 12.2+ and 18c/19c/21c/23ai/26ai | SUPPORTED | Modern `V_$DATAGUARD_PROCESS` profile | Static and Fake Adapter tested; not E2E verified in Phase B |

`SUPPORTED` describes a reviewed implementation and is separate from evidence
level. Phase B adds unit and Fake Adapter integration coverage; it does not
claim live compatibility for the full family matrix. Oracle 11gR2 specifically
has no live E2E verification. PostgreSQL `PRIMARY` with no peers or slots is
`NOT_CONFIGURED` and `HEALTHY` when all applicable queries complete; sender
capacity alone does not imply that a standby is expected. A MySQL status query
returning zero channels is likewise complete `NOT_CONFIGURED` evidence.

MySQL's non-SELECT allowlist is exact and closed: `SHOW BINARY LOGS`,
`SHOW GRANTS`, `SHOW SLAVE STATUS`, and `SHOW REPLICA STATUS`. It does not allow
arbitrary `SHOW` statements. Replication requires `REPLICATION CLIENT` only.

Oracle 11gR2 detection maps `11.2.x` to canonical `11gr2`; `11.1` and malformed
versions remain UNKNOWN. The legacy route uses `V_$DATABASE`,
`V_$DATAGUARD_STATS`, `V_$MANAGED_STANDBY`, and `V_$ARCHIVE_DEST_STATUS`, plus
the version detector's `V_$VERSION` grant. It does not reference
`V_$DATAGUARD_PROCESS`.

## Slow support matrix

| Database family | Support | Routed profile | Compatibility evidence |
|---|---|---|---|
| SQL Server 2008 R2 | SUPPORTED | Legacy session DMV + shared request/transaction SQL | Static reviewed SQL and Fake Adapter regression; not live-verified |
| SQL Server 2012–2019 | SUPPORTED | Shared SQL Server slow bundle | Static reviewed SQL and Fake Adapter regression; not live-verified for every family |
| SQL Server 2022 | SUPPORTED | Shared bundle, 2022 permission contract | Representative local runtime target; confirm current result with system suite |
| SQL Server 2025 | UNKNOWN | No executable profile | Fail closed pending review |
| MySQL 5.7 | SUPPORTED | PROCESSLIST + legacy InnoDB lock views | Static SQL and Fake Adapter regression; not live-verified |
| MySQL 8.0, 8.4 | SUPPORTED | Performance Schema and 8.x lock views | Static SQL and Fake Adapter regression; not live-verified for every minor family |
| PostgreSQL 13–18 | SUPPORTED | Shared `pg_stat_activity` / lock bundle | Static SQL and Fake Adapter regression; not live-verified across all majors |
| Oracle 11gR2 | SUPPORTED | Legacy-compatible `V$SESSION`/`V$SQL` bundle | Static SQL and Fake Adapter regression; NOT E2E VERIFIED |
| Oracle 12c 12.1, 12.2+ | SUPPORTED | Explicit 12c runtime variants | Static SQL and Fake Adapter regression; not live-verified |
| Oracle 18c, 19c, 21c, 23ai, 26ai | SUPPORTED | Shared modern-family bundle | Static SQL and Fake Adapter regression; verify current 26ai E2E separately |

Support denotes a reviewed implementation, not live verification for every
release. SQL Server 2008 R2 and MySQL 5.7 remain first-class routes despite
the absence of live old-version servers in the current test environment.
