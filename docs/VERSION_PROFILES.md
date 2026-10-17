# Version Profiles (V0.9.1 Phase A)

Phase A adds the version declaration and verification foundation. Existing
Collector and Analyzer SQL routing remains on the current legacy paths until a
later migration phase binds SQL, permission requirements and parser contracts
atomically. `UNKNOWN` support in a legacy binding means “not migrated”; it does
not claim that a feature is ready for that server family.

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
`18`; and Oracle `12c`, `18c`, `19c`, `21c`, `23ai`, `26ai`.

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

The central `DiagnosticProfileResolver` returns a frozen profile containing
the SQL, permission and parser IDs from one binding. Phase A binds only explicit
legacy placeholders and a small set of foundation routes. Their support state
is `UNKNOWN`; these records validate the model and variant selection, but do
not change execution routing. SQL Server 2008 R2 replication is explicitly
`UNSUPPORTED` for the Availability Group feature.

## Extending profiles

When migrating a feature, add one complete static binding for each supported
family/variant. Review SQL compatibility, minimum permissions, parser shape and
support together; update the corresponding Preflight and tests. `dba validate`
checks canonical family references, duplicate routes, component references,
variant constraints and forbidden administrator privileges in binding
metadata. It does not infer privileges from arbitrary SQL or documentation.
