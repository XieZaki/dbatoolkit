# Version Profiles (V0.9.1)

Phase A added version declaration and detection. Phase B migrated replication;
Phase C migrated `slow`; Phase D migrated `disk-growth`; Phase E migrates the
existing SQL Server `plan` feature. Each migrated feature
selects reviewed SQL, permission requirements, parser contract,
required/optional collectors and support state together through the immutable
`DiagnosticProfileResolver`.

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
binding. Replication, slow and disk-growth require declared and detected families to match
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

Disk-growth SQL selection is profile-owned as well. Required totals determine
whether a report is incomplete; failures in optional FRA, archive, slot, binlog,
backup or transaction evidence lower visibility without discarding complete
core totals. A configured-off feature such as binary logging or archiving is
reported as `NOT_CONFIGURED`/`NOT_APPLICABLE`. Thresholds remain in the Analyzer
and are not version-profile settings.

Permission readiness, evidence completeness, and database health are separate
outputs. `PARTIAL` permission readiness means the required contract is satisfied
and optional context is unavailable. Optional-only gaps make data completeness
`PARTIAL`, but do not by themselves make storage health `DEGRADED` or `UNKNOWN`.
Health is `UNKNOWN` only when required core evidence is incomplete; otherwise it
is based on observed storage findings. Successful queries returning zero rows,
including Oracle `V$TRANSACTION`, are valid evidence and are not missing.

### Disk-growth support matrix

`SUPPORTED`, test coverage, and live verification are separate claims. In this
phase, every supported route has static SQL/profile validation and Fake Adapter
unit/integration coverage. Only the explicitly selected local Docker assets
may be described as E2E verified; older-version families remain unverified on
real servers unless separately recorded.

| Database family | Support | Routed SQL profile | Evidence level |
|---|---|---|---|
| SQL Server 2008 R2 | SUPPORTED | Legacy `DBCC SQLPERF(LOGSPACE)` plus file totals | Unit/Fake integration; no live legacy instance |
| SQL Server 2012 | SUPPORTED | Legacy log-space query plus compatible file SQL | Unit/Fake integration; no live legacy instance |
| SQL Server 2014–2019 | SUPPORTED | DMV log usage and bounded file/temp evidence | Unit/Fake integration; no live legacy instance |
| SQL Server 2022 | SUPPORTED | Current DMV profile; performance-state permission contract | Unit/Fake integration; E2E verified 2026-10-18 against SQL Server 16.0.4305.5 |
| SQL Server 2025 | UNKNOWN | No executable profile | Fail closed |
| MySQL 5.7 | SUPPORTED | `expire_logs_days` variant and bounded size queries | Unit/Fake integration; no live legacy instance |
| MySQL 8.0 / 8.4 | SUPPORTED | Current binlog configuration and size queries | Unit/Fake integration; MySQL 8.4 E2E verified 2026-10-18 against 8.4.11 |
| PostgreSQL 13–16 | SUPPORTED | Shared database/relation/slot/archive profile | Unit/Fake integration; PG16 E2E verified 2026-10-18 against 16.15 |
| PostgreSQL 17–18 | SUPPORTED | Slot query includes `wal_status` and `safe_wal_size` | Unit/Fake integration; no live major-version matrix |
| Oracle 11gR2 | SUPPORTED | Tablespace/data/temp core with legacy-compatible views | Unit/Fake integration; no live legacy instance |
| Oracle 12c–23ai | SUPPORTED | Tablespace/data/temp core; optional FRA/archive/redo | Unit/Fake integration; no live instance in this run |
| Oracle 26ai | SUPPORTED | Tablespace/data/temp core; optional FRA/archive/redo | E2E verified connection, principal, version and required permission gate; optional FRA/archive/redo grants may remain absent |

Optional collectors can report partial visibility without invalidating complete
core totals. Disabled binary logging, no PostgreSQL slots, disabled archiving,
and unconfigured Oracle FRA are valid not-configured states. Thresholds live in
Analyzer/runtime configuration, not in version profiles.

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

## Disk-growth support matrix

| Database family | Support | Routed profile / compatibility | Evidence |
|---|---|---|---|
| SQL Server 2008 R2 | SUPPORTED | DBCC SQLPERF(LOGSPACE) and compatible file metadata | Unit/Registry tested; no live 2008 R2 E2E |
| SQL Server 2012 | SUPPORTED | DBCC SQLPERF(LOGSPACE); AG evidence is optional | Unit/Registry tested; no live 2012 E2E |
| SQL Server 2014–2019 | SUPPORTED | `sys.dm_db_log_space_usage`, `VIEW SERVER STATE` | Unit/Registry tested; no live old-version E2E |
| SQL Server 2022 | SUPPORTED | `sys.dm_db_log_space_usage`, `VIEW SERVER PERFORMANCE STATE` | Local system E2E result recorded separately |
| SQL Server 2025 | UNKNOWN | No inherited route | Fail closed |
| MySQL 5.7 | SUPPORTED | `expire_logs_days`; schema totals; optional Top-N, FILES, InnoDB, binlog | Unit/Registry tested; no live 5.7 E2E |
| MySQL 8.0 / 8.4 | SUPPORTED | `binlog_expire_logs_seconds`; optional Top-N, FILES, InnoDB, binlog | Unit/Registry tested; live 8.4 evidence recorded separately |
| PostgreSQL 13–16 | SUPPORTED | Shared database-size, slot, archive bundle | Unit/Registry tested; live PG16 evidence recorded separately |
| PostgreSQL 17–18 | SUPPORTED | Slot query includes `wal_status` and `safe_wal_size` | Unit/Registry tested; no live PG17/18 E2E |
| Oracle 11gR2, 12c, 18c–26ai | SUPPORTED | Tablespace/datafile/temp core; FRA/archive/redo optional | Unit/Registry tested; Oracle 26ai requires the profile's manual core grants |

`SUPPORTED`, `UNIT_TESTED`, `INTEGRATION_TESTED` and `E2E_VERIFIED` describe
different evidence levels. No older release is described as live verified
without an actual target run.

## SQL Server Plan support matrix

| Version family | Support | Routed SQL / permission / parser | Evidence |
|---|---|---|---|
| SQL Server 2008 R2 | SUPPORTED | Shared reviewed `plan_request` + `plan_cached`; `VIEW SERVER STATE`; `sqlserver_showplan_v1` | Unit/Profile/Registry validation; no real 2008 R2 E2E |
| SQL Server 2012, 2014, 2016, 2017, 2019 | SUPPORTED | Same shared SQL and parser; `VIEW SERVER STATE` | Unit/Profile/Registry validation; no real old-version Plan E2E |
| SQL Server 2022 | SUPPORTED | Same shared SQL and parser; `VIEW SERVER PERFORMANCE STATE` | SQL Server 2022 plan E2E exists in `docs/VERIFICATION_V0_4.md`; V0.9.1 route now uses verified profile |
| SQL Server 2025 | UNKNOWN | No SQL, permission or parser fallback | Fail closed until official compatibility and integration evidence exist |

Plan uses only the existing active-request and connected-session cached-plan
DMV queries. A cache miss is reported as unavailable; Toolkit never submits the
captured statement to SQL Server. The shared SQL returns session/request,
SQL/plan handles, query hashes and statement offsets. More than one matching
row, truncated result or insufficient identity evidence is reported as
ambiguous rather than selecting an arbitrary plan. The parser contract bounds
XML bytes, element count and operator count; local `.sqlplan` output remains
sensitive evidence.

## P0 emergency diagnosis evidence split

V0.9.1 P0 runs the existing version-resolved collectors synchronously in two
stages. Slow emergency evidence includes sessions, requests/running SQL,
blocking, and transaction evidence; disk-growth emergency evidence uses the
profile's required storage sources plus available log/retention context.
Results already collected are reused by supplemental collection. This does
not add SQL, permission profiles, or version routes. Missing supplemental
evidence is shown as not collected or partial, not as a query failure. Each
stage remains subject to the same resolved profile and SQL Registry.
