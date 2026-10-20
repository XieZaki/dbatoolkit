# DBA Toolkit V0.9.1 Long-Term Roadmap

## 1. Document role

This document defines product direction, phase boundaries, dependencies, entry criteria, exit criteria, and work that is intentionally deferred. It does not maintain task status. Task state exists only in `DBA_TOOLKIT_V0.9.1_BACKLOG.md`; the current pointer is in `DBA_TOOLKIT_V0.9.1_PROGRESS.md`; evidence is in `DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md`.

The package version remains `0.9.0` until the user explicitly approves a version change. “V0.9.1” in these planning filenames identifies the target planning stream, not a completed release.

## 2. Product goal

Build a lightweight, fully offline, production-safe diagnostic toolkit that helps a DBA quickly establish what is happening, how reliable the evidence is, which database-native object should be checked next, and which actions remain manual. The Toolkit collects, correlates, analyzes, and reports; the DBA decides and acts.

Primary databases are SQL Server, PostgreSQL, MySQL, and Oracle. Their diagnostic models may share secure infrastructure while preserving native semantics such as Oracle child cursors, MySQL digests, PostgreSQL backend/transaction semantics, and SQL Server DMV, Query Store, and Showplan evidence.

### Official product version targets

The user confirmed these product targets on 2026-10-19. They supersede older PG12+, PG13+, MongoDB 6.0+, Redis 6.0+, or narrower wording in historical plans.

| Database | Product target | Development priority | Current interpretation |
|---|---|---|---|
| MySQL | 5.7 and 8.0+ | Current mainline | Existing 5.7/8.0/8.4 Profiles are implementation evidence; each later family still requires Profile review and acceptance. |
| Oracle | 11gR2+ | Current mainline | Existing 11gR2 through listed modern families are not a blanket real-E2E claim. |
| PostgreSQL | 14+ | Current mainline | PG12/13 are outside the current product target. Existing PG13 compatibility code is retained but is not a formal support claim. |
| SQL Server | 2008 R2+ | Current mainline | Existing 2008 R2–2022 routes do not automatically support an unprofiled later release such as SQL Server 2025. |
| MongoDB | 4.0+ | Long-term Backlog | Product target only; Toolkit diagnostic features are not implemented. |
| Redis | 4.0+ | Long-term Backlog | Product target only; Toolkit diagnostic features are not implemented. |

Version language is evidence-scoped:

- `TARGET`: approved product direction; it does not imply code or acceptance.
- `IMPLEMENTED`: reviewed code/Profile exists for the named feature and version family.
- `E2E_VERIFIED`: a real database end-to-end run passed for the exact named feature/version/environment; record whether it is historical or current-HEAD. This is not implied by `IMPLEMENTED`, tests, or Profile presence.
- `UNVERIFIED`: target or implementation exists but required acceptance is absent or incomplete.
- `UNSUPPORTED`: the named feature/version combination has an explicit no-route/no-capability decision. It must not be used merely because E2E is missing.

An open-ended `+` target never auto-promotes a future release. A new family becomes formally supported only after version detection/Profile routing, reviewed SQL/permission/parser contracts where applicable, automated validation, and the necessary scoped acceptance.

## 3. Permanent engineering principles

- Only the Inventory-selected instance is contacted; no Inventory-wide diagnosis and no discovered-peer connection.
- Production operation is read-only, bounded, version-aware, least-privilege, offline, and fail-closed.
- SQL comes only from reviewed Registry entries with explicit timeout and row bounds.
- Version-Aware Profile, Permission Registry, Preflight, parser contract, and Registry SQL evolve atomically.
- Credentials never enter Inventory, CLI arguments, logs, reports, environment variables, or repository artifacts.
- Reference termination or permission commands are text for human review; Toolkit never executes them.
- No automatic KILL, cancel/terminate, failover, restart, repair, DDL/DML, GRANT/REVOKE, index, hint, parameter, or forced-plan action.
- Mock/Fake, static validation, historical E2E, current-HEAD E2E, and user-provided partial evidence are different evidence levels.
- A missing observation is not proof of health. `CONFIRMED`, `SUSPECTED`, `NOT_OBSERVED`, `INSUFFICIENT_EVIDENCE`, and `UNSUPPORTED` remain distinct.
- Existing foundations are reused. A new phase name does not authorize a second Adapter, Registry, Profile, Permission, Preflight, Analyzer, or report framework.
- Phase completion and transition require user review. Bug work returns to the paused main task.

## 4. Preserved project foundations and goals

The following remain part of the product and may not be silently removed:

- Credential Provider, encrypted credential storage, JIT resolution, and current-user Credential Session security.
- Inventory directory model and non-sensitive per-instance Connection Profile.
- Version-Aware Diagnostic Profiles and fail-closed version routing.
- Permission Registry, Account Security, Feature Readiness, and database-specific Preflight.
- Offline Runtime Manifest, lock inputs, wheelhouse, bootstrap, upgrade, and rollback flows.
- SQL Registry review, read-only enforcement, bounded collection, cleanup, and redaction.
- Four-database Slow/Blocking, Disk-Growth, and Replication capabilities.
- Existing SQL Server cached execution-plan capability.
- Existing ReportModel, presenters, bounded self-contained HTML, JSON, JSONL, and sensitive `.sqlplan` handling.
- Representative old-version compatibility E2E when safe environments become available.
- Linux deployment/IPC validation and old driver/TLS interoperability work.
- MongoDB and Redis as long-term extensions, not current diagnosis claims.
- `pt-query-digest` and `pgBadger` as optional, separate offline-log-analysis PoCs, not runtime dependencies.

## 5. Phase dependency chain

```text
Phase 0 Evidence Baseline
  -> Phase 1 Lightweight Performance Triage
  -> Phase 2 Top SQL and Resource Attribution
  -> Phase 3 Deep SQL and Plan Diagnosis
  -> Phase 4 Database-Specific Incident Scenarios
  -> Phase 5 Unified HTML Incident Workbench
```

Later phases may refine their own database order when entered. Phase 2 does not yet select its first database.

## 6. Phase 0 — Evidence baseline and current-HEAD closure

### Objective

Create durable planning, status, recovery, and acceptance records; separate historical evidence from current-HEAD evidence; close the most important existing E2E gaps before adding diagnostic features.

### Planned task sequence

1. `V091-P0-BASELINE-001` — establish Roadmap, Backlog, Progress, and Acceptance baseline.
2. `V091-P0-ORACLE-002` — formal current-HEAD Oracle P0.2 E2E closure.
3. `V091-P0-PLAN-003` — current-route SQL Server representative Plan E2E.
4. `V091-P0-FOURDB-004` — representative current-HEAD four-database regression summary.
5. `V091-P0-DOCS-005` — correct documentation drift without changing product scope or package version.

### Entry criteria

- Planning reconciliation exists and the user has approved the four-document structure.
- Actual HEAD and working tree are recorded without discarding user files.
- No new feature work is mixed into the evidence-closure phase.

### Exit criteria

- Every current capability distinguishes source implementation, automated evidence, historical E2E, current-HEAD E2E, and limitations.
- Oracle P0.2 and SQL Server current-route Plan have explicit pass, partial, failed, blocked, or pending records; no evidence is silently promoted.
- The representative four-database summary records only targets actually run.
- README and supporting documents no longer contradict the accepted Profile/evidence scope.
- User approves entry into Phase 1.

### Non-goals

No Top SQL, new Plan source, new database scenario, production connection, version bump, or Phase 1 implementation.

## 7. Phase 1 — Lightweight performance triage

### Objective

Use existing four-database Slow/Blocking and P0 staged evidence to distinguish Root Blocker, Blocking Victim, Independent Slow SQL, Long-running Transaction, and Evidence Insufficient/UNKNOWN, then tell the DBA which low-risk diagnostic should run next. These are evidence-backed roles/signals, not mutually exclusive buckets; one database-native session may carry multiple labels. Elapsed time alone never establishes CPU, I/O, or lock-wait causality.

### Scope

- Single instance, one best-effort snapshot, serial bounded SQL, and existing Slow collectors.
- Reuse shared Incident, ReportModel, Presenter, Profile, Permission, and Registry paths.
- Preserve database-native identity, transaction, wait, and blocking semantics.
- Every triage conclusion names its observed evidence source and completeness; missing, failed, truncated, unsupported, or identity-conflicting evidence degrades to UNKNOWN/PARTIAL rather than a healthy/no-issue claim.
- Leave an explicit route to Phase 2 Top SQL without running expensive ranking queries by default.

### Entry criteria

Phase 0 is reviewed and current evidence gaps are recorded or deliberately accepted.

### Exit criteria

Fixtures show Root Blocker, Blocking Victim, Independent Slow SQL, Long-running Transaction, and UNKNOWN distinctly; overlap remains representable; incomplete/truncated/ambiguous evidence cannot produce unsupported conclusions or unsafe action text; JSON, CLI, and HTML agree on labels, identity, source, and limitations; existing Slow/Blocking behavior and strict read-only safety remain intact; user approves Phase 2.

### Non-goals

No default dual sampling, historical monitoring, universal health score, async collector service, or full Top SQL implementation.

## 8. Phase 2 — Top SQL and resource attribution

### Objective

Identify long-running, high-frequency short, high cumulative CPU, high logical-read, genuinely supported physical-read, high-wait, and blocking-victim SQL without reducing all cases to elapsed time.

### Scope

- Keep metric semantics explicit: `CURRENT_REQUEST`, `CUMULATIVE`, `DATABASE_HISTORY`, `SAMPLE_DELTA`, `SINGLE_EXECUTION`, or `UNKNOWN`.
- Use separate Top-N lists per metric; never create a cross-database composite health score.
- Evaluate MySQL digest summaries, SQL Server query stats, Oracle V$SQL/V$SQLSTATS child semantics, and optional preconfigured PostgreSQL pg_stat_statements as separate database tasks.
- Do not auto-install PostgreSQL extensions, enable Performance Schema consumers, change server parameters, or infer physical reads from unrelated counters.
- Short-window delta sampling remains opt-in until reset, identity, and cost behavior are tested.

### Entry criteria

Phase 1 conclusions and evidence semantics are accepted. The user then selects the first database task based on safe test availability and operational value.

### Exit criteria

At least one explicitly supported database distinguishes a long current request, a high-frequency short statement, and a lock-wait victim using correct metric semantics. Each database is accepted independently.

## 9. Phase 3 — Deep SQL and execution-plan diagnosis

### Objective

Explain why a selected abnormal SQL may be slow using an identity-bound, pre-existing, read-only evidence source.

### Scope

- Reuse the current SQL Server Plan Registry/Profile/parser/report path; add only proven identity and evidence improvements.
- Oracle design retains SQL_ID, CHILD_NUMBER, PLAN_HASH_VALUE, V$SQL_PLAN/V$SQL_SHARED_CURSOR, cursor-sharing, and multiple-child semantics.
- MySQL may investigate only safe named-connection evidence for verified versions and supported active statements.
- PostgreSQL keeps Plan unsupported unless a separately approved pre-existing source is available; never run `EXPLAIN (ANALYZE)` on captured SQL.
- Query Store is a separate optional source only when already enabled and populated; Toolkit does not configure or force plans.

### Entry criteria

The selected SQL identity and metric semantics from Phase 2 are stable enough to prevent misattribution.

### Exit criteria

For each accepted database source, output states source, SQL identity, key nodes, actual versus estimated data only when truly available, candidate causes, missing evidence, and manual next checks. No unverified index, hint, parameter, or forcing command is generated.

## 10. Phase 4 — Database-specific incident scenarios

### Objective

Extend the existing Disk-Growth and Replication foundations one scenario at a time for high-frequency production incidents beyond SQL performance.

### Candidate directions

- Oracle: FRA, archive, undo, temp, redo, tablespace, and safe RAC evidence.
- MySQL: binlog/relay/redo/undo/temporary growth, high DML, replication lag, and connection exhaustion.
- PostgreSQL: WAL, slots, vacuum/autovacuum, xmin/long transaction, temp, bloat, and replication lag.
- SQL Server: log reuse, tempdb, memory grants, waits, file growth, HA/replication, and connection/request pressure.

### Entry and exit criteria

The user selects one bounded scenario. It extends existing profiles and permissions, has database-specific acceptance, and does not duplicate Disk-Growth or Replication. Completion of one database scenario does not imply parity across four databases.

## 11. Phase 5 — Unified HTML incident workbench

### Objective

Improve navigation and incident-response clarity after core evidence is stable, while retaining database-specific modules.

### Scope

- First layer: conclusion and priority investigation targets.
- Second layer: SQL, transaction, wait, plan, and impact evidence.
- Third layer: full bounded technical evidence, quality, version, and permission context.
- Reuse ReportModel, presenters, renderer, inline CSS, offline resources, and report budgets.
- No trend chart without historical data; no forced cross-database field parity.

### Entry and exit criteria

Core diagnosis contracts from earlier phases are stable. The final report remains offline, bounded, escaped, safe, and independently useful for each supported database.

## 12. Long-term backlog outside Phase 0–5

- MongoDB 4.0+ and Redis 4.0+ diagnostic design after the four primary databases are stable; both are product targets but current diagnostic implementation remains absent.
- Independent `pt-query-digest` and `pgBadger` offline-log PoCs after license, packaging, privacy, and bounded-output review.
- Representative old-version E2E matrix where safe images/licenses/environments exist.
- Native Linux deployment, Credential Session IPC, filesystem permission, and bootstrap verification.
- SQL Server old ODBC/TLS/patch interoperability and other native-driver compatibility checks.

Deferral is not cancellation. These items remain in the Backlog with explicit recovery conditions.

## 13. Explicit exclusions

No Web UI, microservice, telemetry, online dependency, remote profile/SQL download, cloud analysis API, automatic updater, database-side installation, arbitrary SQL shell, automatic remediation, or commercial-license-dependent default diagnostic source is part of this roadmap.
