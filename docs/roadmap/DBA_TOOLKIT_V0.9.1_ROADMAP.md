# DBA Toolkit V0.9.1 Long-Term Roadmap

## 1. Document role

This document defines product direction, phase boundaries, dependencies, entry criteria, exit criteria, and work that is intentionally deferred. It does not maintain task status. Task state exists only in `DBA_TOOLKIT_V0.9.1_BACKLOG.md`; the current pointer is in `DBA_TOOLKIT_V0.9.1_PROGRESS.md`; evidence is in `DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md`.

The user explicitly approved the product version change and release of `0.9.1` on 2026-11-07. “V0.9.1” in these planning filenames identifies the target planning stream; a completed release requires the tagged GitHub Actions build and all verified Release assets.

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
- Version-Aware Profile, parser contract, and reviewed Registry SQL evolve together. Required database permissions are documented as deployment preparation in `docs/DATABASE_PERMISSIONS.md`; the approved `PERMISSION-PREFLIGHT-001` simplification supersedes historical references to runtime Permission Registry/readiness.
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
- Inventory directory model and non-sensitive per-instance Connection Profile. Directory scans skip symbolic-link YAML files and do not traverse symbolic-link directories; explicitly selected YAML files retain direct-load behavior. Disabled assets remain visible to list/show and are rejected before Credential Resolver use by connection and diagnosis operations.
- Version-Aware Diagnostic Profiles and fail-closed version routing.
- Database permission preparation guide; runtime keeps version detection, read-only SQL enforcement, and narrowly justified dangerous-account safeguards, not feature permission readiness.
- Offline Runtime Manifest, lock inputs, wheelhouse, bootstrap, upgrade, and rollback flows.
- SQL Registry review, read-only enforcement, bounded collection, cleanup, and redaction.
- Four-database Slow/Blocking, Disk-Growth, and Replication capabilities.
- Existing SQL Server cached execution-plan capability.
- Existing ReportModel, presenters, bounded self-contained HTML, JSON, JSONL, and sensitive `.sqlplan` handling.
- Representative old-version compatibility E2E when safe environments become available.
- Linux deployment/IPC validation and old driver/TLS interoperability work.
- MongoDB and Redis as long-term extensions, not current diagnosis claims.
- `pt-query-digest` and `pgBadger` as optional, separate offline-log-analysis PoCs, not runtime dependencies.

Credential maintenance remains a preserved project foundation. The user approved a conditional close of `CRED-MAINT-001` limited to the Windows-local, explicitly selected SQLite Provider and authenticated read-only legacy JSON compatibility; see the Backlog for canonical state and Acceptance §31 for evidence. This does not approve real-Store migration or production release, and it does not eliminate the documented same-user tampering, parent-directory replacement, file rollback, power-loss/storage, or older-Toolkit write risks.

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
- Reuse shared Incident, ReportModel, Presenter, Version Profile, and Registry paths, plus narrowly scoped account-safety checks. Feature grants are prepared by the DBA from the deployment guide.
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

Identify abnormal SQL by current elapsed time, frequency, cumulative/interval CPU and I/O evidence where genuinely supported, waits, and blocking role; correlate those statement-level observations with separately scoped database/instance resource context so the Toolkit can explain likely performance mechanisms without reducing all cases to elapsed time.

### Scope

- Keep Top SQL metric semantics explicit as `CURRENT`, `CUMULATIVE`, `INTERVAL_DELTA`, or `HISTORICAL`; report missing/unknown capability through quality state rather than a fabricated metric value.
- Use separate Top-N lists per metric; never create a cross-database composite health score.
- Evaluate MySQL digest summaries, SQL Server query stats, Oracle V$SQL/V$SQLSTATS child semantics, and optional preconfigured PostgreSQL pg_stat_statements as separate database tasks.
- Do not auto-install PostgreSQL extensions, enable Performance Schema consumers, change server parameters, or infer physical reads from unrelated counters.
- Short-window delta sampling remains opt-in until reset, identity, and cost behavior are tested.
- Implement a database-neutral result/quality contract first, without selecting the first database; then implement each engine source independently through its existing Profile, reviewed Registry, Collector, and shared reports. Update the deployment permission guide when a new source needs a grant.
- Distinguish source capabilities: current request, cache-resident cumulative statistics, optional database statement statistics, and explicitly sampled interval deltas. A missing metric remains unavailable, never zero.
- SQL Server current-request Active SQL ranking from `sys.dm_exec_requests` is a separate `CURRENT` snapshot capability from cache-resident cumulative `sys.dm_exec_query_stats` Top SQL. It does not change or satisfy the four-engine cumulative Top SQL collector sequence, and it is not comprehensive-diagnosis integration.
- Keep MySQL recent statement history separate from digest rankings; keep database/instance I/O statistics separate from SQL attribution; do not require Query Store, extensions, or premium diagnostics packs.
- Treat SQL/request evidence, database/instance resource observations, and host/storage-device evidence as different scopes. A broader-scope observation may provide context, but it must not be relabeled as a per-SQL metric.
- Correlate a current request with other evidence only through exact engine-native identity and compatible observation windows. SQL text similarity, a query hash alone, or a database-wide counter is not sufficient to prove causality.
- Distinguish SQL resource consumption from resource saturation: high SQL CPU is not proof of instance CPU saturation; logical reads are not physical device reads; a database file wait/latency metric is not by itself proof of a saturated storage device; memory allocation or a memory grant is not proof of host memory exhaustion.
- Classify CPU scheduling pressure, memory/workarea pressure and spill, I/O waits/latency, lock/latch contention, and worker/connection queues as separate candidate causes. Each finding must cite supporting evidence, counter-evidence, scope, time, collection quality, and missing evidence.
- Root-cause findings are multi-label and evidence graded (CONFIRMED, HIGH_CONFIDENCE, SUSPECTED, INSUFFICIENT); no universal score, single forced cause, or automatic remediation is produced. A wait event is a diagnostic signal, not a root cause by name alone.
- Instance resource sources are opt-in/low-cost and database-native where possible. Cumulative wait/file/resource counters require an explicit bounded delta before being described as a rate or current pressure. One snapshot remains best-effort, not a trend.
- Host CPU, OS memory, and storage-device saturation are not uniformly observable through database SQL. No SSH/remote shell, host agent, background telemetry, or server configuration change is introduced by this phase. Without an accepted host evidence source, those conclusions remain INSUFFICIENT and the report names the external evidence needed.

### Entry criteria

Phase 1 conclusions and evidence semantics are accepted. First build the database-neutral result contract; before any engine source task, the user selects that engine based on safe test availability and operational value. This design deliberately does not select the first database.

### Exit criteria

At least one explicitly supported database distinguishes a long current request, a high-frequency short statement, and a lock-wait victim using correct metric semantics, and exposes at least one correctly scoped database/instance resource context source. Findings distinguish observed workload consumption from demonstrated resource pressure; missing host/device evidence cannot produce a confirmed host-level bottleneck. Each database is accepted independently.

### Implementation order

1. `V091-P2-TOPSQL-CONTRACT-001`: shared metric/evidence contract, quality states, and serialization fixtures; no database SQL.
2. Implement and verify the four independent engine collectors (`V091-P2-TOPSQL-SQLSERVER-001`, `...-POSTGRESQL-001`, `...-MYSQL-001`, `...-ORACLE-001`) one at a time; the sequence remains SQL Server, PostgreSQL, MySQL, then Oracle. Backlog is the sole source of task state; sequence position does not imply live verification outside the exact version/scope recorded in Acceptance.
3. Only after all four engine collectors are verified, define and approve the separate `V091-P2-TOPSQL-INTEGRATION-001` task to integrate them into the comprehensive diagnosis flow. Per-engine collector tasks do not modify that flow.
4. `V091-P2-TOPSQL-DELTA-001`: opt-in bounded two-sample deltas only after at least one cumulative engine source is verified; this does not authorize a daemon or default double-sampling.

The user's sequencing decision is explicit: complete all four Top SQL collectors before comprehensive-diagnosis integration. Do not create a duplicate integration Task ID before that gate is met.

Every engine task must include version-family field routing, least-privilege review, preflight/visibility states, bounds, automated tests, and scoped real E2E before that feature is called verified. Task lifecycle state is maintained only in Backlog.

## 9. Phase 3 — Deep SQL and execution-plan diagnosis

### Objective

Explain why a selected abnormal SQL may be slow using an identity-bound, pre-existing, read-only evidence source.

### Scope

- Reuse the current SQL Server Plan Registry/Profile/parser/report path; add only proven identity and evidence improvements.
- Oracle design retains SQL_ID, CHILD_NUMBER, PLAN_HASH_VALUE, V$SQL_PLAN/V$SQL_SHARED_CURSOR, cursor-sharing, and multiple-child semantics.
- MySQL may investigate only safe named-connection evidence for verified versions and supported active statements.
- PostgreSQL keeps Plan unsupported unless a separately approved pre-existing source is available; never run `EXPLAIN (ANALYZE)` on captured SQL.
- Query Store is a separate optional source only when already enabled and populated; Toolkit does not configure or force plans.
- Plan evidence is one dimension of root-cause analysis, not a substitute for wait/resource evidence. A scan, sort, estimate mismatch, or plan-shape pattern is a candidate explanation until the selected SQL identity and runtime evidence support it.
- Actual rows, runtime spills, and per-execution resource usage are displayed only when a pre-existing source actually supplies them and exact identity association is proven; an estimated/cached plan must not be presented as an actual execution plan.
- Do not execute captured business SQL or use EXPLAIN ANALYZE to manufacture runtime evidence. Do not generate unverified index, hint, parameter, or forcing commands.

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

On 2026-10-28 the user approved conditional closure of the SQL Server Memory Grant scenario on its implemented and baseline-tested scope, with the deferred production-release evidence gates retained in Acceptance §26. This is not full live verification. PostgreSQL WAL / replication slots may be planned next as one bounded scenario; implementation requires separate approval and must first assess existing Disk-Growth and Replication coverage. Work remains sequential.

On 2026-10-28 the user approved the PostgreSQL WAL retention and replication-slot enhancement within the existing disk-growth route. Its canonical status is in Backlog and its actual evidence is in Acceptance §27; it does not close the separate Memory Grant release gates.

On 2026-10-28 the user accepted conditional closure of the PostgreSQL WAL enhancement. Its remaining real-environment evidence gates are retained in Acceptance §27. The next authorized work is a four-engine integration inventory and regression, not another incident scenario; conditional scenario closure does not establish production release readiness.

## 11. Phase 5 — Unified HTML incident workbench

### Objective

Improve navigation and incident-response clarity after core evidence is stable, while retaining database-specific modules.

### Scope

- First layer: a concise Chinese conclusion, observed duration/scope, what is confirmed versus suspected, the highest-priority investigation target, and the next safe check. If the evidence cannot explain the duration, say so plainly instead of inventing a cause.
- Second layer: correlated SQL/request, transaction/blocking, wait, plan, and database/instance resource evidence; every claim links to its evidence and preserves native scope/time semantics.
- Third layer: full bounded technical evidence, source/identity details, metric units and windows, completeness/visibility, version/Profile, permission and reset/eviction context. Internal Collector names, Registry paths, and raw status keys belong in this technical layer, not as the primary DBA-facing explanation.
- Reuse ReportModel, presenters, renderer, inline CSS, offline resources, and report budgets; do not build a second report framework or put diagnosis logic in HTML templates.
- CLI, JSON, and HTML must agree on the same findings and uncertainty. Localize human-facing states and descriptions while retaining raw technical values in expandable details.
- Do not render a root-cause badge or recommendation unless the Analyzer supplies an evidence-backed finding. UNKNOWN/PARTIAL/unavailable evidence must explain what was not observed and why.
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

No Web UI, microservice, continuous telemetry, online dependency, remote profile/SQL download, cloud analysis API, automatic updater, database-side installation, arbitrary SQL shell, automatic remediation, remote OS shell/SSH probe, host agent, or commercial-license-dependent default diagnostic source is part of this roadmap. Any future host-evidence integration requires a separate explicit design and approval.
