# V0.9.1 Architecture: Version-Aware Diagnostic Profiles

**Status: APPROVED FOR IMPLEMENTATION**

**Baseline:** DBA Toolkit v0.9.0

**Scope:** SQL Server, PostgreSQL, MySQL and Oracle; slow, disk-growth, replication and SQL Server plan diagnosis.
**This document is architecture, not a statement that every listed version is implemented or verified.**

**Implementation status:** Phase A foundation is in progress. Inventory family
validation, reviewed version detectors, normalization, verification status and
the immutable single resolver are being implemented. Feature SQL routing,
permission profiles and parser migration remain later phases.

## 1. Goals and non-goals

Select the fixed, reviewed SQL, full module permission declaration, parser contract and support state appropriate to the connected database version. Keep Inventory simple: operators declare a major `version_family`; Toolkit detects the full server version and uses that runtime evidence for any minor-version boundary.

This is not an IAM product, capability graph, dynamic rule engine, feature-flag platform, plugin system, SQL generator, or remediation system. It does not automatically update Inventory or execute GRANT/REVOKE, account changes, DDL/DML, KILL, failover, or repair. SQL remains fixed and reviewed in the existing SQL Registry. Permissions remain statically declared per module, not inferred from SQL.

## 2. Core route and atomic profile

The route key is `(canonical_db_type, declared_version_family, detected_full_version, feature)`. It is resolved exactly once through the single public `DiagnosticProfileResolver.resolve(...)` entry point. Callers must not select SQL, permissions, parser behavior, or support independently.

The resolver returns an immutable `ResolvedDiagnosticProfile` containing at least:

```text
db_type
declared_version_family
detected_version
detected_version_family
version_status
feature
support_state
runtime_state
profile_family
sql_variant_id
permission_profile_id
parser_contract
runtime_variant_id (optional)
```

All four behavior bindings—support, SQL variant, permission profile and parser contract—are selected in one resolution. An invalid/incomplete profile fails closed before an Adapter is called. For example, this combination is forbidden even if every referenced object exists:

```text
SQL              mysql84
Permissions      mysql80
Parser           mysql84
```

It could execute a query while reporting the wrong grants or parsing the wrong field names. There are no public `resolve_sql_profile()`, `resolve_permission_profile()` or `resolve_parser_profile()` APIs. Private helpers may perform lookups, but only the central resolver returns an executable resolved profile.

The route registry is a thin binding registry only: it contains route, support/runtime rules, static variant selectors and IDs. SQL text stays in SQL Registry, privilege declarations stay in Permission Registry, and parsing stays in existing normalization code. No second full Feature Manifest is introduced.

## 3. Version families and Inventory schema

Inventory gains optional `version_family`. Schema: **`version_family` type = string only** and it must use a canonical value. YAML integers/floats are validation errors; the loader must never silently call `str()` to repair them.

```yaml
version_family: "2022"
version_family: "8.0"
version_family: "16"
version_family: "23ai"
```

Canonical values initially allowed by database type:

```text
sqlserver:   2008r2, 2012, 2014, 2016, 2017, 2019, 2022, 2025
mysql:       5.7, 8.0, 8.4
postgresql:  13, 14, 15, 16, 17, 18
oracle:      12c, 18c, 19c, 21c, 23ai, 26ai
```

These are declaration candidates, not blanket support promises. The static support matrix determines whether a family/feature is supported. Literal `version_family: "unknown"` is rejected; omitted family is the legacy-migration state. Database type aliases are normalized by Inventory parsing to one canonical `db_type` before profile resolution; the resolver accepts only canonical types.

Existing Inventory without a family remains loadable. `dba validate` initially emits a migration warning, not a failure. New examples and newly authored assets must quote and include the family. No detected version is written to Inventory. A later release may make the field required after migration evidence; V0.9.1 does not.

## 4. Version detection and normalization

Detectors live behind one shared contract and use a fixed, low-privilege read-only query/API independent of feature grants. No CLI-specific mapping and no generic capability-probe framework are added.

| Database | Detection approach | Family normalization |
|---|---|---|
| SQL Server | Fixed Registry query using `SERVERPROPERTY('ProductVersion')`; parse its numeric components, avoiding newer-only properties and DMV permission dependency | `10.50` → `2008r2`; known major versions map explicitly, e.g. 15 → `2019`, 16 → `2022`, 17 → `2025` |
| MySQL | `VERSION()` / `@@version` | Preserve parsed full version; `5.7.x`, `8.0.x`, `8.4.x` map to their families and select registered minor variants |
| PostgreSQL | `current_setting('server_version')` and/or `server_version_num` | Explicit major map, e.g. 16.x → `16` |
| Oracle | Fixed query of `V$VERSION.BANNER` through the public synonym, backed by a direct least-privilege grant on `SYS.V_$VERSION` | Parse product label and release number together; never infer 26ai from the first numeric component alone |

Oracle detection is deliberately concrete: use the backwards-compatible `BANNER` column for the fixed version query and include `SELECT ON SYS.V_$VERSION` in every Oracle module's full bootstrap/reference permission list. `CREATE SESSION` alone is not assumed sufficient. Do not use `DBA`, `SELECT_CATALOG_ROLE`, or `SYSDBA`. `DBMS_DB_VERSION` is not the selected runtime detector: its constants target PL/SQL conditional compilation and would require a separately reviewed execution/access contract; it is not needed when the narrow direct view grant is available. If the approved query is denied, report UNVERIFIED and do not pretend the declared family was detected.

Detector output retains `raw_version`, `detected_family`, and an optional `runtime_variant_id`. Parsing failures and unrecognized future versions produce UNKNOWN; they never fall back to the latest profile.

### Oracle labels and 12c boundary

Keep raw release text and normalized product family separate. 12c detection must preserve enough information to distinguish `12.1` from `12.2`; use an explicit internal variant for Data Guard differences. `V$DATAGUARD_PROCESS` is available from 12.2, while `V$MANAGED_STANDBY` is deprecated from 12.2, so a single undifferentiated `12c` SQL route is not valid for that feature.

For newer Oracle releases, map the full release/banner rather than its first number. Oracle 26ai can report a full release such as `23.26.0.0.0`; its marketing/product family is 26ai, while the raw release is retained. Both `23ai` and `26ai` require explicit registered mappings and tests. Unknown future product labels remain UNKNOWN.

## 5. Version status and fail-closed rules

`version_status` uses `VERIFIED`, `MISMATCH`, `UNVERIFIED`, and `AUTO_DETECTED`:

- **VERIFIED:** a declared family exists, detection succeeded, and the detected family matches it.
- **MISMATCH:** detection succeeded but detected and declared families differ.
- **UNVERIFIED:** no reliable runtime version could be detected, including failed connection/bootstrap or failed version query. A declaration may still support offline reference output only.
- **AUTO_DETECTED:** no family was declared, but a known family was detected on this connection. This is visibly distinct from operator-declared VERIFIED and is never persisted.

No declared family plus failed detection is `NO_DECLARED_FAMILY` with detected family UNKNOWN, represented as UNVERIFIED in the result plus an explicit reason. It cannot select a version-specific permission profile. A literal `unknown` is not a substitute for omission.

Version-sensitive diagnosis runs only when status is VERIFIED. AUTO_DETECTED is useful for `check` and `permissions` current-run assessment, but diagnosis remains blocked until the DBA records the detected family in Inventory and reruns. This conservative rule makes migration impact explicit and avoids silently treating runtime state as reviewed Inventory configuration. UNVERIFIED, MISMATCH and UNKNOWN never execute version-specific SQL. No `--force` override exists.

## 6. Minor-version variants without Inventory complexity

`version_family` selects the declared major family; the detected full version selects a statically registered variant inside that family. Variant rules are simple, explicit inclusive/exclusive version boundaries, not arbitrary expressions. Each variant is pre-registered, reviewed and tested. It binds its SQL ID and parser field mapping through the parent Diagnostic Profile; permissions are rebound and revalidated when needed. Runtime does not generate or concatenate SQL.

Required example:

```text
Declared family 8.0 + detected 8.0.21
  -> mysql80_pre_8022
  -> fixed SHOW SLAVE STATUS Registry script
  -> legacy Slave_* field aliases

Declared family 8.0 + detected 8.0.36
  -> mysql80_8022_plus
  -> fixed SHOW REPLICA STATUS Registry script
  -> modern Replica_* field aliases
```

The threshold follows the MySQL 8.0.22 terminology change. It is a runtime variant, not a new Inventory family. If a full version cannot be parsed to the precision needed for a boundary, the feature route is UNKNOWN and execution is blocked.

**Existing safety-policy reconciliation required before Phase B:** current Registry files and the SQL safety allowlist contain exact `SHOW REPLICA STATUS` / `SHOW SLAVE STATUS` scripts, while `docs/PROJECT_RULES.md` currently names only `SHOW BINARY LOGS` as the MySQL non-SELECT Registry exception. Do not broaden the allowlist or add another statement form as part of version routing. Before changing the replication route, reconcile that policy text with the already reviewed fixed statements and ensure the static checker, Registry metadata, permission scan and tests agree. Phase A does not execute or alter these scripts.

## 7. SQL Server family and feature routing

Declare every supported server family explicitly. Internal profile reuse is per feature and only after equivalence review; `2012-2019` and `2022+` are not catch-all executable ranges. A later family, including 2025, must have a normalized version mapping and explicit route entries before it can run diagnostics.

Known design boundaries:

- Availability Groups begin with SQL Server 2012, but actual support also depends on edition, feature enablement and instance configuration.
- Query Store begins with SQL Server 2016; it is not enabled by default for new SQL Server 2016/2017/2019 databases. Report feature support separately from per-database `NOT_CONFIGURED`.
- Server DMV access uses version-appropriate permissions: pre-2022 profiles may require `VIEW SERVER STATE`; SQL Server 2022+ server DMV profiles may require `VIEW SERVER PERFORMANCE STATE`. Bind permission needs to the exact DMV and SQL, not merely the database family.
- SQL Server 2008 R2 session collection retains its legacy SQL/source semantics; do not normalize missing modern fields to fabricated values.

Any profile shared by 2012, 2014, 2016, 2017 and 2019 must enumerate those families explicitly and prove its SQL columns, required grants and output contract are equivalent. Query Store and AG configuration differences remain feature/runtime state, not reasons to pretend the entire family range is identical.

## 8. Support state versus runtime state

These are independent axes.

**Support State** describes Toolkit knowledge for a family/feature:

```text
SUPPORTED     reviewed implementation covers the required evidence
PARTIAL       known implementation returns only specified partial evidence
UNSUPPORTED   known family/feature combination is not supported
UNKNOWN       evidence or route is insufficient to decide
```

PARTIAL requires explicit missing evidence and impact. It does not mean unknown, not configured, or permission missing.

**Runtime State** describes instance configuration or observation:

```text
CONFIGURED
NOT_CONFIGURED
UNKNOWN
```

Example: SQL Server 2022 AG can be `Support=SUPPORTED`, `Runtime=NOT_CONFIGURED`. SQL Server 2008 R2 AG can be `Support=UNSUPPORTED`. A failed probe is UNKNOWN, not UNSUPPORTED. Permission readiness remains a separate existing axis (`READY`, `PARTIAL`, `MISSING`, `UNSUPPORTED`, `UNKNOWN`); do not add a family of bootstrap/capability states.

## 9. Permission model and safety

Permissions are complete static module requirements keyed by database type, version profile and feature. Each permission profile declares required/optional privileges, scope/object, reason, impact and reference grant. Include bootstrap/connect prerequisites in every module's complete list so first-time setup does not reveal a second layer of grants. Examples: Oracle `CREATE SESSION` plus exact object SELECTs; PostgreSQL `CONNECT` plus preferred `pg_monitor`; SQL Server required connection and exact server permissions; MySQL authentication is a connection prerequisite rather than a GRANT, while its actual required grants remain feature-specific.

Account privilege discovery uses a small number of database-appropriate batched metadata queries, then evaluates all module profiles locally. It must not issue one query per privilege or demand DBA/catalog-wide privileges just to inspect permissions. If the connected account cannot reliably expose its own effective grants, the readiness is UNKNOWN; the command can still show full reference permissions when a declared family is available. Do not infer permission needs from SQL text. `check` does no broad diagnostic collection and prints only a concise module summary.

Observed missing items may be listed separately from the full module reference grant. If status is not READY, show that module's complete reviewed reference grant, not a synthesized incremental script. The DBA must review it; equivalent repeat grants are generally expected to be safe for the approved monitor permissions, but vendor behavior and shared-account usage still require review. READY omits the grant by default; `--verbose` may show it.

Default profiles have a simple static forbidden-privilege assertion:

```text
Oracle:      SYSDBA, DBA, SELECT_CATALOG_ROLE
SQL Server:  sysadmin, securityadmin, serveradmin, CONTROL SERVER,
             ALTER ANY LOGIN, IMPERSONATE ANY LOGIN, TAKE OWNERSHIP
PostgreSQL:  SUPERUSER, pg_signal_backend (not required for observational diagnosis)
MySQL:       ALL PRIVILEGES, SUPER, SYSTEM_USER
```

The assertion applies to both profile metadata and generated grant text. V0.9.1 has no waiver path. A future exception requires a separate security design and review; it is outside this version.

V0.9.1 adds no profile-based or module-wide REVOKE recommendation. Existing `generate-revoke`, if retained for compatibility, remains an advanced explicitly requested output, carries a prominent shared-account warning, is not shown by default, and is not derived as “remove this module's grants.” Toolkit never executes it.

Database-specific permission direction:

- **PostgreSQL:** prefer `pg_monitor` for monitoring where it is the reviewed least-practical set; use `CONNECT` where needed. Never require `SUPERUSER` or `pg_signal_backend`. Static role grants do not by themselves prove complete query-text visibility.
- **MySQL:** grants are feature-specific, e.g. `PROCESS` for slow-query visibility and `REPLICATION CLIENT` for replication status. No global `SELECT`, `SUPER`, `SYSTEM_USER`, or `ALL PRIVILEGES` defaults.
- **SQL Server:** permissions vary by DMV/version; use the exact server/database scope required by the script. Never default to `sysadmin` or `CONTROL SERVER`.
- **Oracle:** direct `SELECT` on exact `SYS.V_$...` objects plus `CREATE SESSION`; never default to `DBA`, `SELECT_CATALOG_ROLE`, or `SYSDBA`.

## 10. Parser contract

Prefer a shared parser plus a statically selected alias mapping. Example: MySQL `Slave_IO_Running` and `Replica_IO_Running` both normalize to `io_running`. A parser contract declares required normalized fields, optional fields and field aliases; it is bound atomically into the resolved profile. Create a separate semantic parser only when meaning, row shape or parsing algorithm differs—not for simple column renames.

Missing required evidence cannot be converted into an empty healthy value. The collector/analyzer reports a controlled partial result and the field-level reason. `Data Visibility` remains a lightweight output property (`FULL`, `PARTIAL`, `UNKNOWN`) for evidence completeness only; it is not used to resolve profiles or permission profiles.

No generic runtime-probe framework is introduced. Existing preflight and feature collection may perform a minimal, fixed validation needed to describe their own evidence. `check` uses one batched grant/visibility assessment where possible; if reliable assessment is unavailable, it reports UNKNOWN rather than running all collectors or probing each privilege separately.

## 11. Initial support and evidence matrix

Support state and evidence level are separate. Documentation tracks `PLANNED`, `UNIT_TESTED`, `INTEGRATION_TESTED`, and `E2E_VERIFIED`; these are not runtime enums. A family becomes runtime SUPPORTED only after reviewed scripts, privileges, parser contract and tests exist. Matrix entries below describe initial routing strategy, not blanket proof.

| Database family | Sharing / required split | Initial evidence boundary |
|---|---|---|
| SQL Server 2008 R2 | Distinct legacy session SQL; per-feature reuse only after review | Compatibility target; fake regression initially, no broad E2E claim |
| SQL Server 2012/2014/2016/2017/2019 | Reuse selected slow/disk profiles only after field/grant comparison; AG begins 2012; Query Store begins 2016 | Unit per family; system E2E representative versions only |
| SQL Server 2022 | Explicit route; performance-state permissions and feature state | Local SQL Server 2022 E2E is the current target |
| SQL Server 2025 | Explicit family only; never inherited implicitly from 2022 | Planned until reviewed/tested |
| MySQL 5.7 | Separate replication SQL and legacy aliases | Regression target; no E2E claim unless run |
| MySQL 8.0 | Static pre/post 8.0.22 replication variants | Unit boundary tests at 8.0.21/8.0.22; representative E2E |
| MySQL 8.4 | Separate explicit family; share only audited definitions | Local MySQL 8.4 E2E target |
| PostgreSQL 13–17 | Shared profile candidates with explicit family entries; explicit SQL for later-only fields | Unit per family; representative integration/system tests |
| PostgreSQL 18 | Explicit family; share only after catalog/permission/parser review | Local PG16 target does not verify PG18 |
| Oracle 12c | Internal 12.1 vs 12.2 variant where needed, especially Data Guard | Version-specific regression required |
| Oracle 18c/19c/21c | Explicit family mappings; share only after object/field review | Per-family evidence documented |
| Oracle 23ai/26ai | Separate explicit product-family mapping, retain raw full release | Oracle Free 23 E2E covers only tested scripts/privileges; 26ai remains unverified until tested |

PostgreSQL correction: `pg_replication_slots.wal_status` and `safe_wal_size` exist in PostgreSQL 13 as well as later releases, so those columns alone do not justify a PG13+ split. Newer fields still require explicit family variants or aliases. Never rely on an unknown column failing at execution time.

## 12. Command behavior matrix

| Command | VERIFIED | AUTO_DETECTED / no declaration | UNVERIFIED | MISMATCH | Unknown detected family |
|---|---|---|---|---|---|
| `show <id>` | Static Inventory only; show declaration | Static Inventory; family absent | Same | Same | Same; no connection |
| `validate` | Offline static validation only | Warn on absent family | No DB attempt | Cannot detect runtime mismatch | Family syntax/reference check only |
| `check <id>` | Connect, detect, compare, lightweight permission summary | Show detected family and recommend Inventory declaration; mark AUTO_DETECTED | Show reason and permission UNKNOWN; no grants dump | Show both families/status; no final permission readiness conclusion | Show UNKNOWN; no latest fallback |
| `permissions <id>` | Online readiness by detected profile; non-READY modules show complete grant | May assess the detected profile for this run, visibly AUTO_DETECTED; diagnosis still requires declaration | If declared family exists, show complete reference grants and UNVERIFIED/UNKNOWN readiness; if absent, guidance unavailable | No READY/MISSING conclusion and no grant output; ask to correct Inventory and rerun | No selected grants; ask for supported version evidence |
| `diagnose <id> <feature>` | Run only if support is SUPPORTED/PARTIAL and permission readiness permits | Block until family is declared and rerun | Block version-dependent execution | Stop before feature SQL | Stop before SQL |

`show` never connects and never shows a persisted last-detected value. `validate` remains entirely offline. `check` prints only connection/version and compact module states, never full grant scripts. `permissions` may use credentials for one online inspection; if connection or version verification fails, it retains its useful offline reference behavior as specified above. `diagnose` never grants, edits Inventory or auto-remediates.

When permissions are not ready, output both `Observed Missing` (when reliable) and the complete module reference set. If privilege metadata cannot be read, use UNKNOWN, not MISSING. With a declared family but failed connection, offline grant text is explicitly reference-only and version-unverified. Without declaration and without connectivity, output `Version Family: UNKNOWN` and `Permission Guidance: UNAVAILABLE`; do not guess.

On mismatch, `permissions` deliberately emits no grant script. It shows declared and detected families, states that no readiness conclusion was made, and tells the DBA to correct Inventory before retrying. This is simpler and safer than presenting two possibly confusing scripts.

## 13. `dba validate` and test responsibilities

`dba validate` is offline and bounded. It checks Inventory family type/canonical value, canonical database types, route existence, defined support/runtime states, all binding references, static version/variant overlap, SQL metadata database type/feature/version applicability/read-only/reviewed flags, permission profile applicability and forbidden grants, and parser contract completeness. It returns actionable domain errors with supported values; never raw `KeyError`/`ProfileNotFound` tracebacks.

Static validation checks that SQL, permission and parser compatibility scopes all cover the exact route and variant. A reference merely existing is insufficient. No connection or runtime database probe is part of `validate`.

The full all-route consistency matrix, no-cross-wiring tests, version-boundary tests and security assertion tests belong in pytest. Startup only loads/validates the static registry structure once; it does not repeat slow tests or connect to databases.

## 14. Output, logs and compatibility

Add an optional `version_profile` object to JSON without removing, renaming or changing existing fields. Keep its values normalized and bounded. If the project formalizes a JSON schema, this is an additive schema revision; consumers must tolerate the new optional object.

Human terminal and HTML output display declared family, detected full version, detected family, version status and a friendly profile label. Internal SQL/permission/parser IDs appear only in `--verbose`, technical JSON metadata or a report appendix. HTML remains self-contained and bounded. Do not store detected versions in Inventory or introduce persistent runtime state.

Structured logs may include canonical db type, declared/detected family, version status and selected friendly/profile identifier. They must not include credentials, connection strings, raw unbounded server output or SQL text outside the existing protected SQL audit rules.

## 15. Testing and Definition of Done

Ordinary pytest uses fake drivers and never connects to databases.

- **Unit:** all family normalization; strict YAML type; MySQL 8.0.21/8.0.22 routing; 12.1/12.2 Oracle routing; Oracle 23ai/26ai mapping; VERIFIED/AUTO_DETECTED/UNVERIFIED/MISMATCH; unknown future version; atomic binding; privilege forbidden list; support/runtime separation; parser aliases; no fallback.
- **Integration:** Inventory → detector result → sole resolver → SQL Registry → Permission Registry/preflight → parser contract → shared result/presenter. Assert mismatch and unknown never call the Adapter with feature SQL.
- **System:** opt-in local Docker coverage for SQL Server 2022, PostgreSQL 16, MySQL 8.4 and Oracle Free, marking only actually exercised features E2E_VERIFIED.
- **Regression:** SQL Server 2008 R2 legacy session behavior and 2022 permissions; AG/Query Store support versus configuration; MySQL 5.7 vs 8.0/8.4, 8.0.22 boundary and SSCursor lifecycle; Oracle service target, direct version-view grant, 12.1/12.2 Data Guard and 23ai/26ai normalization; PostgreSQL column differences; privilege scopes and truncation/visibility.

V0.9.1 Definition of Done is: strict family schema, canonical mapping, low-privilege detectors, verification states, static runtime variants, one immutable atomic resolver, version-aware SQL/permission/parser binding, separate support/runtime states, documented CLI matrix, offline validation, all-route unit/integration tests, user docs and regression evidence. No full cross-product Docker matrix is required.

## 16. Implementation phases

### Phase A — Foundation

Implement strict optional Inventory family strings and canonical values; central detector contract and normalized result; VERIFIED/MISMATCH/UNVERIFIED/AUTO_DETECTED semantics; one resolver API; immutable resolved model; static registry structural validation. Do not migrate every feature in this phase. Unit tests must prove no independent component routing API and no invalid profile can reach an Adapter.

### Phase B — SQL routing

Extend existing SQL Registry metadata with ID, database type, feature, family/variant applicability, READ_ONLY and reviewed state. Add fixed static variants and support routing, beginning with replication, including MySQL 8.0.22. Preserve SQL as Registry files; no dynamic generation.

For MySQL replication, Phase B is gated on the explicit safety-policy reconciliation in §6; the router may select only exact reviewed Registry scripts already permitted by the reconciled project policy.

### Phase C — Permissions

Bind complete module permission profiles by family/variant, align preflight, add forbidden-permission validation, batched readiness summary and mismatch/offline behavior. Preserve text-only GRANT. Keep module-wide REVOKE out of new profile guidance.

### Phase D — Parser contracts

Add shared parser contracts and field alias maps. Split semantic parser implementations only when meaning or record shape truly changes. Add lightweight evidence visibility in common results.

### Phase E — Migration and tests

Migrate slow/disk-growth/replication and SQL Server plan incrementally, update examples and docs, run unit/integration/regression tests, then opt-in representative Docker E2E. Record actual evidence levels and provide an Inventory migration warning. Do not auto-edit local Inventory.

## 17. Maintenance workflow and complexity limits

Adding a database version or feature follows a short review checklist: vendor documentation review; version and capability boundary review; SQL compatibility and safety review; complete module permission and preflight review; parser contract review; route registration; unit/integration tests; representative system test where available. A missing binding is a validation/test failure.

Multiple declared families may reuse an internal profile only per feature and only when SQL, permissions, parser semantics and support state are equivalent. Use explicit routes and a single static variant selector. Do not add multiple inheritance, mixins, a profile graph, automatic profile generation, dynamic SQL, automatic permission inference or remote profile loading. This keeps work proportional: one route binding plus only the SQL/permission/parser differences the feature actually needs.

## 18. ADR

### Decision

Use version-aware atomic diagnostic profiles resolved through one central resolver. Inventory carries a strict optional canonical major-family string; the connection supplies full runtime version and minor variant. Only a complete immutable profile can proceed to existing read-only execution.

### Alternatives rejected

- Family-only routing, which cannot distinguish minor release boundaries.
- Independent SQL/permission/parser resolvers, which can cross-wire execution and grant advice.
- Dynamic capability/IAM frameworks and SQL-to-privilege inference.
- Latest-version fallback for unknown servers.
- Dynamic SQL generation, automatic Inventory rewrite and broad Feature Manifest duplication.

### Consequences

Version routing becomes auditable and mismatch behavior becomes predictable. Maintainers must keep route, SQL, permissions, parser and tests synchronized. Older Inventory remains valid with warnings, but version-specific diagnosis requires an explicit family declaration. System verification remains representative rather than exhaustive. This is a small static compatibility layer over existing project registries, not a new platform.

## Phase A gate

Architecture v2 closes the prior design blockers by making the following mandatory: one resolver, immutable atomic result, minor/static variant strategy, command behavior matrix, explicit Oracle detector and direct narrow permission, strict string schema, support/runtime separation, and compatibility assertions across SQL/permission/parser. **Phase A may begin with the exact Foundation scope above.** This approval is for implementation planning; it does not claim runtime features or version cells are already implemented or verified.
