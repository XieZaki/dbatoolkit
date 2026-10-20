# DBA Toolkit V0.9.1 Progress Pointer

## Current position

```text
CURRENT_PHASE: Phase 1
CURRENT_TASK: V091-P1-TRIAGE-001
CURRENT_TASK_STATUS_SOURCE: docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md
CURRENT_VERSION: 0.9.0
CURRENT_HEAD: f840cdc fix(oracle): align emergency SQL and identity presentation
CURRENT_TASK_DELIVERY: CODE_COMPLETE; AUTOMATED_TESTS_PASS (prior evidence); REAL_E2E_PARTIAL

LAST_COMPLETED_TASK: Phase 0 closeout approved by user; V091-P0-DOCS-005; V091-P0-FOURDB-004; V091-P0-ORACLE-002; V091-P0-PLAN-003; V091-P0-BASELINE-001; V091-SCOPE-VERSIONS-006
BLOCKED_TASKS: V091-P1-TRIAGE-001 acceptance gaps — Oracle test driver returns ORA-28009; PostgreSQL/MySQL independent slow SQL not demonstrated; BUG-P1-TRIAGE-CLI-STATUS-001 remains TODO.
NEXT_READY_TASK: V091-P1-TRIAGE-001 (close the recorded E2E gaps; do not start Phase 2)
WAITING_FOR_APPROVAL: YES — user direction is needed before changing the Oracle driver connection mode/schema scope or implementing the newly recorded CLI parity Bug.
WAITING_FOR_USER_ACTION: Provide a valid non-SYS Oracle lab driver login/mode or approve a reviewed schema-scoped Oracle test-driver adaptation; review whether to fix the minimal CLI status-parity Bug now.
FOLLOWING_ORDER: Finish V091-P1-TRIAGE-001 E2E -> user review -> Phase 2 planning only after acceptance
```

This file is a recovery pointer, not a second task status ledger. Consult the Backlog for canonical task state and Acceptance for evidence.

## Completed milestones and current task results

The dated milestone entries below preserve prior-session facts. Any earlier statement that Oracle-002 or FOURDB-004 was blocked/pending is superseded by the latest result in Acceptance §10 and the canonical states in Backlog.

- Recorded the formal targets: MySQL 5.7/8.0+, Oracle 11gR2+, PostgreSQL 14+, SQL Server 2008 R2+, MongoDB 4.0+, and Redis 4.0+.
- Distinguished product/capability states `TARGET`, `IMPLEMENTED`, `E2E_VERIFIED`, `UNVERIFIED`, and `UNSUPPORTED`; the Backlog's task lifecycle statuses remain a separate vocabulary.
- Resolved `PG12-SCOPE-001`: PG12/13 are outside the current target; existing PG13 compatibility code remains untouched and is not promoted to formal support.
- Kept the four relational databases as the current mainline and MongoDB/Redis 4.0+ as long-term Backlog targets with no diagnostic implementation claim.
- Recorded older README/design/Profile wording as historical scope rather than deleting code or rewriting historical evidence.
- Added explicit scope notices to the version-aware Profile, emergency UX, execution-plan, and performance-triage design snapshots; appended a decision addendum to the reconciliation audit so its original C-02 finding remains historically intact but no longer appears undecided.
- Clarified the README's Oracle 12c+ sentence as a V0.7 historical summary, not the current minimum-version target.
- Preserved version 0.9.0 and did not modify business code, SQL Registry, Adapter, Profiles, Inventory, or database state.
- Closed the Phase 0 planning baseline after checking the four document roles, all 14 imported historical TODO groups, duplicate mappings, conflict register, and complete Phase 0–5 sequence (including Phase 2 Top SQL and Phase 3 deep SQL/Plan).
- Prepared the gated Oracle P0.2 isolated E2E runbook in Acceptance §9. It remains a plan only; `V091-P0-ORACLE-002` is now `BLOCKED` in Backlog pending a live, confirmed isolated endpoint and exact scope.
- No pytest or Oracle/database E2E was run during the earlier documentation-only baseline closeout. In the current task, only the offline Oracle test module was run; no database was accessed.
- `V091-P1-TRIAGE-001` implementation and offline regression were completed at working tree based on Git HEAD `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`; full pytest: 763 passed, 2 skipped; `ruff check .` and `mypy dba` passed. The earlier Docker access failure was sandbox-scoped; the later same-user read-only Docker check succeeded. Current-HEAD database evidence and remaining gaps are recorded in Acceptance §13.
- Latest restore run (2026-10-21, Asia/Shanghai): the authorized local Docker lab was reachable in a read-only context, and the four relational targets matched their approved test identities. Monitoring aliases were available from the OS credential provider; the encrypted store remained locked and untouched. SQL Server 2022 triage was verified for root+long transaction, intermediate dual role, victim, and independent slow SQL with CLI/JSON/offline HTML agreement. PostgreSQL 16 and MySQL 8.4 verified active blocking-role/long-transaction labels; synthetic sleep was not counted as independent slow SQL. MySQL triage/data visibility was PARTIAL. Oracle Free identity and a read-only baseline were verified, but its independent test driver failed with ORA-28009; no alternate authentication mode or account was tried. Oracle JSON/HTML reported UNKNOWN/PARTIAL while human CLI omitted these states; Bug `BUG-P1-TRIAGE-CLI-STATUS-001` is registered TODO. Toolkit database writes: none. The SQL Server test fixture was removed and verified absent; pre-existing PostgreSQL/MySQL fixtures were retained; tested simulator sessions exited. No source code, SQL Registry, Inventory, package version, or Docker settings changed. See Acceptance §13.
- On 2026-10-20, `tests/test_oracle.py` passed offline on the project venv (Python 3.12.14): 47 passed. The default `python` WindowsApps launcher could not start, so the project venv interpreter was used.
- Historical state before the later Docker-lab authorization: Oracle real E2E was blocked because Docker Desktop's Linux engine pipe was unavailable and target/workload authorization was not yet confirmed. This was superseded by the current-HEAD evidence in Acceptance §10.
- User cancelled Python 3.8 compatibility work: `RUNTIME-WIN-OFFLINE-PY38-001` is `CANCELLED` in Backlog, with its history retained. Project support remains Python `>=3.11,<3.14`.
- Windows fully offline deployment acceptance remains open under `OFFLINE-RUNTIME-001`; validate after the test environment is upgraded to supported Python. No install, dependency change, or network test was made.
- Historical scheduling note: Oracle was independently blocked and did not globally block Phase 0. The later authorized sequence and completed states are recorded in Backlog and Acceptance §10.
- Historical Stage A record (credential-unlock interpretation superseded by the current locked-session OS credential-provider verification below): the approved local SQL Server 2022 lab target passed a read-only `check`, `server-info`, Plan permission preflight, session enumeration, and current Plan Registry query. Live identity was SQL Server Developer 2022, product version `16.0.4305.5`; client was Python 3.12.14 / pyodbc 5.3.0 / ODBC Driver 17. Plan profile and permission route were verified (`sqlserver_plan_shared`, `sqlserver_plan_2022`, `VIEW SERVER PERFORMANCE STATE`, READY).
- Only one non-Toolkit service session was visible, sleeping with no open transaction; no active user request was observed. The current-request Plan Registry returned no row and the cached-plan Registry returned one candidate, but no reusable Showplan XML; the result was `PLAN_NOT_AVAILABLE`, `plan_available=false`, `metrics_scope=cached_last_execution`, zero operators. Do not call this a representative cache hit or a Plan bug.
- Human CLI summary, JSON, and the generated offline HTML all indicated unavailable Plan evidence; HTML had no external resource references. No `.sqlplan` was emitted. Generated reports remain ignored local artifacts and are treated as sensitive. No captured SQL was executed and no DB object/state was changed. No tests were run in this E2E turn.
- Limitations recorded: permission preflight for Plan was READY but account-security classification was UNKNOWN; the then-effective lab connection used `Encrypt=no` / `TrustServerCertificate=yes` with ODBC Driver 17. Lab network exposure and mount details remain in the local operational record, not this recovery pointer. No further live DB calls until SQL Server TLS meets PROJECT_RULES (`Encrypt=yes`, certificate validation); no unencrypted exception is accepted by the current task.
- Historical Stage A outcome before Stage B (superseded for Plan-003 overall by the current-HEAD evidence below): `E2E_PARTIAL`; representative plan retrieval, permission-denied degradation, and current-request plan were then unverified. Stage A cache-miss behavior remains a valid scoped result; the task is now `VERIFIED` in Backlog for the specifically recorded SQL Server 2022 lab scope.
- Static TLS audit located a per-instance override in ignored local Inventory, which superseded secure runtime defaults. `dba/connection.py` resolves per-asset options over runtime settings; `dba/sqlserver.py` serializes them as the ODBC `Encrypt` and `TrustServerCertificate` keywords. No config was changed and no database was contacted during this audit. Future DB access requires a trusted server certificate, client trust chain/name match, and effective `Encrypt=yes;TrustServerCertificate=no`.
- README still describes an isolated `Encrypt=no` exception and says the Docker test entry can connect directly; this is not authorization under the current TLS decision. Flagged in Acceptance for later documentation reconciliation; not edited in this static-only task.
- Static Stage B proposal uses a dedicated, pre-provisioned `DBAToolkitPlanTest` database, never `master`, with schema `[dba_toolkit_plan_test]` and table `[dba_toolkit_plan_test].[PlanFixture]`. Proposed operator SQL covers clustered scan, hash join and sort; a bounded concurrent read attempts the current-request path, then the persistent workload session tests cached-last-execution. Cleanup drops only the exact table after worker sessions close. No SQL was executed and no fixture was created.
- Existing V0.4 Plan script/fixture are reference-only, not reusable unchanged: fixed lab target and `master`, privileged setup account, server login/user/table/index creation, broad `VIEW ANY DEFINITION`, `TrustServerCertificate=yes`, and plaintext secret fields accepted over JSON stdin. It also validates post-completion cached plans, not a live current-request sample. No test script was run.
- 2026-10-20 user superseded the earlier TLS/workload gate: confirmed local Docker databases and the reviewed simulator directory are authorized bounded test resources; the confirmed SQL Server lab may retain its instance-level `Encrypt=no` / `TrustServerCertificate=yes` exception temporarily. This does not change production defaults or the Toolkit read-only/least-privilege monitor boundary. Added the concise lab rule to AGENTS/PROJECT_RULES and registered the separate lab TLS hardening task.
- Latest Stage B attempt (superseded by successful recovery below): read-only lab metadata was checked; the existing simulator was not a Plan E2E driver and the V0.4 Plan fixture was not safe to run unchanged. Toolkit `check` stopped before connection because the configured credential route required interactive encrypted-store unlock in that execution context. The prompt was cancelled without entering a secret. No administrator credential was used by Toolkit; no database connection, query, write, report, or SQLPLAN was produced.
- Historical blocker note (superseded): Plan-003 was once recorded `BLOCKED` pending an assumed encrypted-store unlock. Locked-session tests later proved the aliases are available from Windows Credential Manager in the authorized same-user context; no unlock was required, and Plan-003 is now `VERIFIED` for the documented scope.
- After the gate check, `.venv\Scripts\python.exe -m pytest tests/test_execution_plan.py -q` passed: 26 passed. This is offline automated evidence only and does not verify real Plan retrieval or change the task blocker.
- Latest credential routing check while `credentials status` reported `LOCKED`: the configured OS credential provider returned `READY` for the approved monitoring aliases. `dba check` succeeded against the authorized SQL Server 2022 lab and detected version `16.0.4305.5`, `sqlserver_plan_shared`, `sqlserver_plan_2022`, and Plan Preflight `READY`. The earlier restricted-process access failure did not reproduce in the authorized local process context. Code review confirms `CredentialResolver(auto)` returns an OS-store hit before querying the encrypted store. No code fix was needed; Encrypted Local Store remained locked and untouched.
- Stage B current-HEAD real E2E completed on the approved SQL Server 2022 lab using ODBC Driver 17 and existing `AdventureWorks2022` sample data. An independent driver issued only a bounded SELECT join/sort over SalesOrderHeader, SalesOrderDetail and Product; no DDL/DML or objects. Cached-plan and active-request paths returned `PLAN_AVAILABLE`; repeated bounded sampling caught three active requests. The active/cached records for the same driver session matched `session_id`, `sql_handle`, `plan_handle`, `query_hash`, `query_plan_hash`, and statement offsets (raw values were compared in process and not recorded). Eight plan nodes parsed; CLI/JSON/offline HTML and `.sqlplan` artifacts passed checks. No raw Showplan XML in HTML, tested driver password absent from artifacts, and Toolkit session count was zero after completion. The independent test driver used an elevated lab-only principal; that identity was never used by Toolkit. Account-security classification for the monitor remains `UNKNOWN`, with no high-risk roles/permissions reported; this does not establish a complete permission audit.
- Current targeted offline evidence: `.venv\Scripts\python.exe -m pytest tests/test_encrypted_credentials.py tests/test_connection_credentials.py tests/test_credential_session.py -q` — 73 passed, 1 skipped. The Plan regression module at this same HEAD passed 26 tests in the immediately preceding run. No credentials, source code, Registry, Adapter, Inventory, or package version changed.
- `V091-P0-PLAN-003` is `VERIFIED` in Backlog for the stated SQL Server 2022 lab scope. The earlier Stage B credential blocker is superseded by successful default-provider retrieval with the credential session locked. A lab network exposure risk remains tracked separately and was not changed.

## Active Bug stack

```text
PAUSED_MAIN_TASK: V091-P1-TRIAGE-001 (E2E acceptance gaps)
ACTIVE_BUG: BUG-P1-TRIAGE-CLI-STATUS-001 (TODO; identified, fix not started)
RESUME_TASK: V091-P1-TRIAGE-001
```

New Bugs must be registered in the Backlog with `bug_id`, `parent_task_id`, `phase`, `priority`, `description`, `impact`, `status`, `verification`, and `resume_task_id`. A fixed Bug resumes the paused task; it does not choose a new roadmap direction.

## Current blockers and pending decisions

- Historical blocker note for `V091-P0-ORACLE-002` is superseded by the 2026-10-20 current-HEAD Oracle Free 26ai P0.2 E2E in Acceptance §10; Backlog is the authority for current state. Optional Oracle Slow/Disk permissions remain a separate PARTIAL limitation.
- `V091-P0-PLAN-003` is `VERIFIED` in Backlog for SQL Server 2022 `16.0.4305.5` and the recorded Plan path. Credential session may remain `LOCKED`; the approved OS credential provider supplied the monitor identity without unlocking the encrypted store. A lab network exposure risk remains separate.
- 2026-10-20: `V091-P0-FOURDB-004` completed its approved serial E2E at HEAD `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`. SQL Server 2022, PostgreSQL 16, MySQL 8.4, and Oracle Free were identity-checked and exercised with separate read-only Toolkit monitor identities. Per-database results, outputs, test-driver writes, Toolkit write boundary, and cleanup are in Acceptance §10. SQL Server and PostgreSQL scenarios are verified; MySQL and Oracle breadth retain explicit partial qualifications. No cross-version claim follows.
- The same Oracle run satisfies `V091-P0-ORACLE-002` for Oracle Free 26ai P0.2; its previous BLOCKED state is superseded in Backlog. Optional Oracle Slow/Disk permissions remain absent and were not granted.
- Automated regression run for FOURDB-004: the targeted suite passed 349 tests; see Acceptance §10. This is recorded separately from live E2E.
- MySQL runtime provenance discrepancy (imported module reports 2.2.8; distribution metadata reports 1.1.3) is retained as `MYSQL-DRIVER-PROVENANCE-001`; no install or code modification was made.
- `V091-P0-DOCS-005` is complete in Backlog. README and Version Profiles now distinguish formal targets, implemented Version-Aware routes, and exact current-HEAD E2E scope; Backlog records MySQL driver provenance, lab credential-output safety automation, and delayed lab SQL Server TLS hardening as separate work. Acceptance §11 records this documentation-only reconciliation; no DB or pytest E2E was repeated.
- On 2026-10-20, read-only container metadata confirmed the previously authorized SQL Server 2022 test instance. The user authorized local-only diagnostic reads; persistent data and backup mounts were not accessed or changed. Local Inventory remains outside Git.
- The approved SQL Server monitoring identity is currently available through the OS credential provider. No credential value was displayed or recorded. Do not modify credential state or ask the user to share a secret.
- Local driver inspection: Python 3.12.14, pyodbc 5.3.0, ODBC Driver 17 selected (Driver 18 absent). This verifies the local client only, not a database connection.
- Historical records identify SQL Server `16.0.4305.5`, pyodbc 5.3.0, ODBC Driver 17 fallback and Plan permission readiness (`VIEW SERVER PERFORMANCE STATE`); these are historical, not refreshed current-instance facts. The V0.4 Plan fixture was a separate disposable SQL Server `16.0.4295.3` environment, not proof that the current local endpoint is the same disposable instance.
- Historical pointer before the latest user review: `V091-P0-FOURDB-004` and `V091-P0-DOCS-005` were complete for their recorded scopes and Phase 0 closeout was awaiting review. This scheduling note is superseded by the user's 2026-10-20 Phase 0 closeout and Phase 1 entry approval above.
- `RUNTIME-WIN-OFFLINE-PY38-001` is `CANCELLED` by explicit user decision. Windows fully offline deployment remains a separate acceptance objective under `OFFLINE-RUNTIME-001`, to be tested on supported Python after the environment upgrade.
- The existing V0.7.1 Oracle integration wrapper is not approved for direct reuse: it selects a fixed lab target, uses privileged setup, and installs a driver from the Internet. Acceptance §9 records the safe adaptation gates.
- Oracle Free 26ai P0.2 is verified for its recorded isolated-lab scope. Optional Slow/Disk view permissions remain a partial limitation; old Oracle target versions and RAC remain unverified. Toolkit monitor remains read-only and does not execute KILL.
- Phase 2 first database remains deliberately undecided until Phase 1 acceptance.
- MongoDB, Redis, `pt-query-digest`, and `pgBadger` remain long-term Backlog items.
- Representative old-version, Linux, and native-driver compatibility environments are not currently established by this baseline.
- Historical matrices still retain their contemporaneous version families as implementation/evidence records; scope notices direct readers to the Roadmap and Acceptance for current product targets.
- User formally approved Phase 0 closeout and Phase 1 entry. No Phase 0 VERIFIED task was reopened; scoped MySQL, Oracle optional-permission, lab TLS, credential-output, and old-version follow-ups remain in Backlog.
- Phase 1 baseline review (2026-10-20, HEAD `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`) found reusable per-database Slow/Blocking graphs, root/blocking-victim evidence, transaction/wait/session context, version-routed existing collectors, and shared CLI/JSON/HTML presentation. No DB or tests were run for this baseline.
- Historical baseline-only note (2026-10-20, superseded): before implementation, `V091-P1-TRIAGE-001` was `TODO` and the planned delta was an incremental evidence-backed multi-label triage contract. The current implementation and E2E evidence are recorded above and in Acceptance §13; Backlog remains the only task-status source.

## Resume procedure for the next Codex session

1. Read root `AGENTS.md` and `docs/PROJECT_RULES.md` completely.
2. Read Roadmap, Backlog, this Progress file, and Acceptance.
3. Run `git status --short` and `git log -1 --oneline`; preserve user files and record any HEAD drift.
4. Resume only the task explicitly approved by the user. Oracle and SQL Server evidence is scoped to exact lab/version cells in Acceptance; open optional permissions and old-version gaps do not block Phase 1 triage.
5. `V091-P0-PLAN-003` is verified for its recorded SQL Server lab scope. The approved OS credential provider works without unlocking the encrypted store. Do not use the administrator test-driver identity in Toolkit. Phase 0 is closed; do not modify lab infrastructure for Phase 1 without need and scope.
6. Phase 1 entry is approved. `V091-P1-TRIAGE-001` implementation and offline regression are complete; current-HEAD E2E is partial. Resume only the documented gaps and Bug; do not start Phase 2 before acceptance and user approval.
7. Update task status only in Backlog, add actual evidence only to Acceptance, update this pointer, report the next task, and stop.

## Current task summary

```text
CURRENT_TASK: V091-P1-TRIAGE-001 — implementation done; current-HEAD E2E incomplete; Backlog is authoritative for task status.
BLOCKED_TASKS: Oracle test-driver ORA-28009; PostgreSQL/MySQL independent slow SQL not proven; BUG-P1-TRIAGE-CLI-STATUS-001 needs a scoped fix and verification.
NEXT_READY_TASK: V091-P1-TRIAGE-001 (same task; no next feature task).
NEXT_STATUS_SOURCE: docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md
FOLLOWING: Close this task's E2E gaps, then stop for user review before Phase 2 planning.
PROHIBITED: Production access, automatic grants, KILL execution, unapproved workload, scope expansion, broad Phase 1 implementation beyond V091-P1-TRIAGE-001.
```
