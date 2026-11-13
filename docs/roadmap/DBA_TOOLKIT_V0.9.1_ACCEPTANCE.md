# DBA Toolkit V0.9.1 Acceptance Evidence Ledger

## 1. Document role

This document records evidence, not task status. Task state is maintained only in `DBA_TOOLKIT_V0.9.1_BACKLOG.md`.

Baseline code HEAD: `f840cdc fix(oracle): align emergency SQL and identity presentation`

Package version: `0.9.0`

Baseline date: 2026-10-19

Product target decision: 2026-10-19. A later higher version is not formally supported solely because the target uses `+`; it must have an explicit Profile and feature-specific acceptance evidence.

## 2. Evidence taxonomy

| Evidence level | Meaning |
|---|---|
| `SOURCE_IMPLEMENTED` | Current source contains the path and reviewed contracts. |
| `STATIC_VALIDATED` | Registry/profile/permission/schema/lint/type checks passed for the recorded HEAD. |
| `UNIT_PASSED` | Deterministic unit tests passed without a database. |
| `FAKE_MOCK_PASSED` | Composed fake/mock Adapter or CLI tests passed; this is not real database E2E. |
| `HISTORICAL_E2E_PASSED` | A repository record proves a real database path for its historical code/version/environment only. |
| `CURRENT_HEAD_E2E_PASSED` | The exact recorded HEAD completed the scoped real CLI/database path. |
| `E2E_VERIFIED` | Summary label for a specific database-version-feature/environment real E2E; the record must identify historical versus current HEAD. It never implies all features or versions. |
| `USER_PROVIDED_PARTIAL` | The user observed real behavior, but the repository lacks enough reproducible scope to call the full task current-HEAD E2E. |
| `NOT_VERIFIED` | Required evidence has not been run or recorded. |
| `BLOCKED` | A known prerequisite prevented the evidence path. |

No level implies another unless explicitly recorded. In particular, Unit/Fake does not imply E2E, and historical E2E does not imply current-HEAD E2E.

## 2.1 Official target versus capability state

| Database | Official product target | Current implementation/evidence summary |
|---|---|---|
| MySQL | 5.7 and 8.0+ | 5.7/8.0/8.4 feature Profiles exist for current relational capabilities; verification is feature/version-specific, not blanket. |
| Oracle | 11gR2+ | Listed 11gR2 and modern Profile routes exist for several features; 11gR2 remains largely real-E2E unverified. |
| PostgreSQL | 14+ | Current code retains 13–18 families. PG14–18 overlap the target; PG13 is retained compatibility code outside the target and is not formally promoted. |
| SQL Server | 2008 R2+ | Existing routes cover named 2008 R2–2022 feature families with different evidence; later releases require explicit Profile/acceptance. |
| MongoDB | 4.0+ | Long-term TARGET. Python runtime dependency readiness exists; current Toolkit diagnostic capability is `UNSUPPORTED`/not implemented because no feature Profile/path exists. |
| Redis | 4.0+ | Long-term TARGET. Python runtime dependency readiness exists; current Toolkit diagnostic capability is `UNSUPPORTED`/not implemented because no feature Profile/path exists. |

State interpretation for every row in this ledger:

- `TARGET` is approved scope only.
- `IMPLEMENTED` requires a reviewed feature path/Profile for the named family.
- `IMPLEMENTED` does not imply `E2E_VERIFIED`; the latter requires an actual real database run for the named feature/version/environment and an explicit historical/current-HEAD label.
- `E2E_VERIFIED` is never inferred from source, static checks, unit tests, fake/mock tests, Profile presence, or a product `TARGET` declaration.
- `UNVERIFIED` means target or implementation lacks that acceptance.
- `UNSUPPORTED` requires an explicit feature/version no-route decision and is not a synonym for unverified.

The `+` in a product target does not automatically implement or verify a future release. A newly released family remains target-but-unverified until detection/Profile, SQL/permission/parser contracts where needed, automated checks, and scoped acceptance are complete.

## 3. Baseline offline quality evidence

These commands were run earlier on 2026-10-19 at the same Git HEAD `f840cdc`, before this documentation-only task. They were not rerun for `V091-P0-BASELINE-001`.

| Gate | Result | Classification | Limitation |
|---|---|---|---|
| `python -m pytest -q` | 751 passed, 2 skipped | `UNIT_PASSED` / `FAKE_MOCK_PASSED` according to test markers | Ordinary suite did not enable opt-in database System/Docker tests. |
| `ruff check .` | Passed | `STATIC_VALIDATED` | Style/static lint only. |
| `mypy --strict dba` | Passed for 60 source files | `STATIC_VALIDATED` | Type evidence only. |
| `dba validate` | Passed; 4 local entries and 103 SQL scripts | `STATIC_VALIDATED` | Registry/profile/config validation; no database connection. Local Inventory values are not recorded here. |
| tracked-file Secret Scanner | Passed | `STATIC_VALIDATED` | Scans Git-tracked files only and does not prove untracked/local files are secret-free. |

## 4. SQL Server evidence baseline

| Capability | Current source and automated evidence | Historical real E2E | Current-HEAD real E2E | Known limits / required evidence |
|---|---|---|---|---|
| Slow / Blocking | `SOURCE_IMPLEMENTED`; Version-Aware Slow profile, requests/sessions/transactions/connections, blocking graph, bounded report; current offline suite passed. | `docs/VERIFICATION_V0_3.md` and `docs/audits/test-report-v0.9.0.md` record SQL Server 2022 real paths at earlier commits. | `NOT_VERIFIED` after `f840cdc` as a formal full run. | Old SQL Server families and recent shared P0/report changes need scoped current evidence; historical results remain valid only for their versions/commits. |
| Plan version route and permission | `SOURCE_IMPLEMENTED`; Resolver binds reviewed SQL, version-specific permission, parser, and support state; mismatch/ambiguity/cache-miss tests exist. | Post-status in `docs/design/execution-plan-capability-safety-audit-v0.9.1.md` records the earlier SQL Server 2022 route evidence. | `CURRENT_HEAD_E2E_PASSED` for SQL Server 2022 `16.0.4305.5`: profile, permission preflight, cache-miss degradation, cached/current plan retrieval and outputs are recorded below. | Permission-denied live degradation and complete account-security classification remain unverified; no generalization beyond the tested scope. |
| Representative cached Plan retrieval | `SOURCE_IMPLEMENTED`; plan request/cache Registry SQL and bounded Showplan parser exist. | `docs/VERIFICATION_V0_4.md` records SQL Server 2022 representative cached plans on the earlier path. | `CURRENT_HEAD_E2E_PASSED` for SQL Server 2022 `16.0.4305.5`, `sqlserver_plan_shared`, the recorded monitor identity, and AdventureWorks2022 only; detailed Stage A/B record below. | Does not generalize to other versions, accounts, databases, or features; monitor account security classification remains `UNKNOWN`. |
| Disk-Growth | `SOURCE_IMPLEMENTED`; current Version-Aware route and automated tests exist. | `docs/VERIFICATION_V0_8.md` and later profile records include SQL Server 2022 real evidence. | `NOT_VERIFIED` as part of a post-`f840cdc` four-database run. | One snapshot is current state, not growth rate. Older families remain unverified. |
| Replication | `SOURCE_IMPLEMENTED`; AG route, permission, parser, analyzer, and report tests exist. | `docs/audits/test-report-v0.9.0.md` records SQL Server 2022 `NOT_CONFIGURED` with complete evidence. | `NOT_VERIFIED` after `f840cdc`. | SQL Server 2008 R2 is unsupported for AG; older supported AG families lack broad real E2E. |

## 5. PostgreSQL evidence baseline

The official PostgreSQL product target is 14+. Current source still contains PostgreSQL 13–18 Profile families. PG14–18 overlap the target; PG13 is retained existing compatibility code outside the current target and is not a formal support or verification claim. PG12/13 are not current target versions, but this scope decision does not delete existing code or retroactively change historical evidence.

| Capability | Current source and automated evidence | Historical real E2E | Current-HEAD real E2E | Known limits / required evidence |
|---|---|---|---|---|
| Slow / Blocking | `SOURCE_IMPLEMENTED`; pg_stat_activity/locks/transactions, identity, blocking graph, required/optional evidence, and action-safety tests exist. | PostgreSQL Docker/system records include Slow paths before the latest P0.1 fixes. | `NOT_VERIFIED` after `f684271` and later shared changes. | PID reuse needs backend_start/transaction identity recheck; visibility depends on monitor permissions. |
| P0.1 transaction/action safety fixes | `SOURCE_IMPLEMENTED`; `dba/postgresql_slow.py`, Incident/Presenter, and targeted automated tests changed in `f684271`. | No repository record proves the complete post-fix real path. | `NOT_VERIFIED`. | `V091-P0-FOURDB-004` must record blocking, non-blocking, incomplete evidence, output, and cleanup if a safe target is available. |
| Disk-Growth | `SOURCE_IMPLEMENTED`; database/relation/slot/archive/transaction profiles and tests exist. | PG16.15 real evidence exists in V0.8 and later system records. | `NOT_VERIFIED` after `f840cdc`. | Database protocol does not provide reliable pg_wal filesystem size; cumulative temp counters are not current rates. |
| Replication | `SOURCE_IMPLEMENTED`; primary/standby/slot/receiver/archiver routes and tests exist. | V0.9.0 records PG16.15 real evidence. | `NOT_VERIFIED` after `f840cdc`. | A single snapshot is not a trend; replay timestamp and no-standby semantics require context. |
| Top SQL / pg_stat_statements | No Toolkit Top SQL Profile/Collector. | None. | `NOT_VERIFIED`. | Extension may be used only when already configured; Toolkit never installs/enables it. |

## 5.1 PostgreSQL target/evidence boundary

| Version family | Product state | Implementation state | Verification state |
|---|---|---|---|
| PostgreSQL 12 | Outside current target | No current declared family in `dba/versioning.py` | No current claim. |
| PostgreSQL 13 | Outside current target | Existing compatibility Profile code retained | Historical/Fake evidence does not make it formally supported. |
| PostgreSQL 14–15 | `TARGET` | Existing Profile routes for current features | `UNVERIFIED` on real current-HEAD instances unless a feature-specific record says otherwise. |
| PostgreSQL 16 | `TARGET` | Existing Profile routes | Historical PG16.15 E2E exists for named features; current-HEAD scope remains separately recorded. |
| PostgreSQL 17–18 | `TARGET` | Existing Profile routes with feature-specific variants | Mostly automated/static evidence; real matrix remains `UNVERIFIED`. |
| PostgreSQL 19+ | Covered only as future product direction by 14+ | No automatic route | Requires new Profile and acceptance before formal support. |

## 6. MySQL evidence baseline

| Capability | Current source and automated evidence | Historical real E2E | Current-HEAD real E2E | Known limits / required evidence |
|---|---|---|---|---|
| Slow / Blocking | `SOURCE_IMPLEMENTED`; 5.7/8.0/8.4 profile variants, sessions/current statements/transactions/locks, and action safety tests exist. | MySQL 8.0.46 historical Slow and MySQL 8.4.11 V0.9.0 system evidence exist. | `NOT_VERIFIED` after recent P0/MySQL changes. | Sleep is not an idle transaction without InnoDB transaction evidence. |
| Recent SQL | `SOURCE_IMPLEMENTED`; bounded blocker-directed `events_statements_history` path, redaction, fallback, and report tests exist. | No formal real record for this current feature. | `NOT_VERIFIED`. | Consumer state, ring-buffer overwrite, permissions, and connection/thread identity must be tested; recent history is not digest Top SQL. |
| Wait Detail | Current collector can preserve partial evidence and use reviewed fallback when the wait consumer is unavailable. | V0.9.0 MySQL 8.4.11 reported Slow `DEGRADED/PARTIAL` because current wait detail was unavailable. | `NOT_VERIFIED` after current fixes. | Missing wait detail must remain partial, not zero/healthy. |
| Digest Top SQL | Not implemented. | None. | `NOT_VERIFIED`. | `events_statements_summary_by_digest` belongs to Phase 2; do not confuse it with recent statements. |
| Disk-Growth | `SOURCE_IMPLEMENTED`; version-specific binlog configuration and bounded size evidence exist. | MySQL 8.4.11 real evidence exists in V0.8 and later records. | `NOT_VERIFIED` after `f840cdc`. | Binlog total is not growth rate; rows examined is not physical read. |
| Replication | `SOURCE_IMPLEMENTED`; exact legacy/modern SHOW routes and alias parsers exist. | V0.9.0 records MySQL 8.4.11 `NOT_CONFIGURED`/partial evidence. | `NOT_VERIFIED` after `f840cdc`. | Query failure cannot be interpreted as no channels; old versions remain Fake-only. |

## 7. Oracle evidence baseline

The old V0.9.0 ORA-01045 record and the user's later real blocking exercise are both retained. Neither overwrites the other because they cover different accounts, dates, code paths, and evidence scope.

| Capability | Current source and automated evidence | Historical/user real evidence | Current-HEAD real E2E | Known limits / required evidence |
|---|---|---|---|---|
| Slow / Blocking base | `SOURCE_IMPLEMENTED`; session/request/transaction/lock capability routes, RAC degradation, identity, and action-safety tests exist. | `docs/VERIFICATION_V0_7_1.md` records Oracle Free 23 historical full Slow/blocking E2E. | `NOT_VERIFIED` after the latest P0.1/P0.2 fixes. | Historical pass does not prove the latest presentation/identity path. |
| P0.1 / P0.2 fixes | `SOURCE_IMPLEMENTED`; commits `f7375b3`, `5266bfa`, and `f840cdc` improve current/previous SQL, XID, V$TRANSACTION time source, identity quality, action status, CLI/HTML/JSON. Automated tests passed at baseline. | `USER_PROVIDED_PARTIAL`: on 2026-10-19 the user observed a real root blocker, XID, blocking chain, and manual action reference. Exact reproducible command/environment/full acceptance record is not yet in the repository. | `NOT_VERIFIED` as formal complete current-HEAD E2E. | `V091-P0-ORACLE-002` must preserve the user evidence, record exact scope, and verify baseline/blocking/incomplete paths without executing KILL. |
| V0.9.0 Oracle system path | Source existed at that historical commit. | `docs/audits/test-report-v0.9.0.md` records endpoint/driver/alias availability but ORA-01045 due to missing CREATE SESSION; dependent queries were skipped. | Not applicable as a current pass. | This is a historical account-permission blocker, not evidence that Oracle functionality failed or that later evidence is invalid. |
| Disk-Growth | `SOURCE_IMPLEMENTED`; Version-Aware core/optional routes and automated tests exist. | V0.8 records Oracle 23 historical E2E; later Phase D system evidence reached version/permission gate and recorded four missing required core grants. | `NOT_VERIFIED` after `f840cdc`. | Current route requires explicit direct object grants; no automatic grant. |
| Replication | `SOURCE_IMPLEMENTED`; 11gR2/12.1 legacy and modern Data Guard routes have static/Fake coverage. | No complete real current route record; V0.9.0 account could not connect. | `NOT_VERIFIED`. | Oracle 11gR2 remains explicitly not E2E verified. |
| Plan Collector | Not implemented. Current SQL_ID/child/cursor statistics are not a plan collector. | None. | `NOT_VERIFIED`. | `PLAN-PC-001` is a future design/permission feasibility task; no default AWR/ASH or re-execution. |

## 8. Cross-cutting foundation evidence

| Foundation | Source/automated evidence | Real/platform evidence | Current limitation |
|---|---|---|---|
| Credentials and encrypted/session security | Providers, encrypted store, agent/session, fail-closed tests; baseline suite passed. | Historical Windows/local workflows exist. | Real Linux peer-UID/socket and broader platform verification remain incomplete. |
| Inventory / Connection Profile | Strict schema, directory precedence, sensitive-field rejection, connection resolver, tests. | Used by historical System tests. | Local Inventory is ignored and must never be treated as repository configuration. |
| Permission Registry / Preflight | Four-database definitions, feature readiness, account security, consistency tests. | Historical target-specific readiness records exist. | Preflight is conservative evidence, not proof of all database permissions. |
| SQL Registry | Reviewed/read-only metadata, digest recheck, timeout/row bounds, validation. | Historical real queries used Registry paths. | Static checks do not prove absence of function side effects; least privilege remains final boundary. |
| Offline Runtime / Wheelhouse | Manifest, extras, locks, bootstrap/upgrade/rollback tests/docs. | Windows is primary recorded host. | Linux and old native driver/TLS matrices remain incomplete. |
| Reporting | Shared bounded console/JSON/HTML and `.sqlplan` handling; automated escaping/redaction/omission tests. | Historical system reports exist. | Business literals cannot be claimed fully anonymized; reports remain sensitive. |

## 8.1 MongoDB and Redis long-term target boundary

| Database | Target | Current capability | Evidence state | Required before formal feature support |
|---|---|---|---|---|
| MongoDB | 4.0+ | Driver/runtime packaging readiness only; no Toolkit diagnostic Adapter/Profile/Collector/Analyzer contract is claimed. | Product target is `UNVERIFIED`; current diagnostic feature is `UNSUPPORTED`/not implemented. | User-approved feature scope, version detection/Profile, read-only/minimum-permission design, bounded collection, tests, and real acceptance. |
| Redis | 4.0+ | Driver/runtime packaging readiness only; no Toolkit diagnostic Adapter/Profile/Collector/Analyzer contract is claimed. | Product target is `UNVERIFIED`; current diagnostic feature is `UNSUPPORTED`/not implemented. | User-approved feature scope, version detection/Profile, read-only/minimum-permission design, bounded collection, tests, and real acceptance. |

## 9. Next evidence requirements

### `V091-P0-ORACLE-002`

#### 2026-10-20 current-HEAD preparation result

| Item | Result |
|---|---|
| Exact Git HEAD | `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (`fix(oracle): align emergency SQL and identity presentation`) |
| CODE COMPLETE / implementation | P0.2 source paths are present at this HEAD in `dba/oracle_slow.py`, `dba/incident.py`, `dba/oracle_slow_reporter.py`, Oracle Registry SQL, and CLI/reporting; no business code was changed in this task. This is not real E2E evidence. |
| AUTOMATED TESTS | `.venv\Scripts\python.exe -m pytest -q tests/test_oracle.py` — **47 passed** on Python 3.12.14. Offline Fake/unit evidence only; not database E2E. The default Python launcher could not start, so the project virtual-environment interpreter was used. |
| Existing isolation evidence | `docs/VERIFICATION_V0_7_1.md` documents a historical Oracle Free 23.0 single-instance `FREEPDB1` test and cleanup. `docs/audits/test-report-v0.9.0.md` later records a missing-`CREATE SESSION` blocker for a different account/path. Both remain historical and do not establish the current target state. |
| Current target readiness | Docker CLI was present, but `docker ps` could not connect to the Docker Desktop Linux engine pipe. An ignored local Oracle Inventory candidate has a version-family declaration inconsistent with the historical Free 23 evidence; neither establishes current database version or authorization. No credential alias or password was read or recorded. |
| REAL E2E VERIFIED | **No.** There is no current-HEAD Oracle real E2E pass. |
| REAL E2E PENDING | `BLOCKED`. No database connection, container start, test-object creation, SQL workload, grant, or cleanup operation was attempted. |
| Blocker to clear | Confirm an available isolated target and exact service/PDB/instance, current Oracle queried version, explicit authorization for the disposable fixture/actors and DML workload, owner-provisioned permissions, approved credential alias, offline client readiness, and cleanup owner/window. |

The task is not E2E-verified. The prior user-provided real blocking exercise remains `USER_PROVIDED_PARTIAL` and is not overwritten or promoted.

#### Historical Phase 0 scheduling note (2026-10-20; superseded by §10)

At this point in the chronology, the Oracle prerequisites above remained unresolved and `V091-P0-ORACLE-002` was blocked; it did not globally block Phase 0. This records the state before the later Docker-lab authorization and current-HEAD run. The subsequent §10 evidence supersedes this blocker state without rewriting the earlier runbook or the 47-test offline evidence.

#### Prepared isolated E2E runbook (not executed)

This is a gated procedure, not authorization to connect. Run only after the user approves the exact disposable Oracle service/PDB, Oracle version, test window, and cleanup owner. Production endpoints are categorically out of scope. Do not use production credentials or run workload SQL there. The Toolkit monitor account remains least-privilege/read-only; Toolkit must never execute `KILL`, `ALTER SYSTEM`, `COMMIT`, `ROLLBACK`, grants, DDL, or DML. Only the separate test driver may operate on the explicitly approved disposable fixture.

**Existing code/test evidence inspected:** `tests/test_oracle.py` includes blocking graph/RAC/cycle and missing-root cases, correlated transaction identity/XID and `V$TRANSACTION.START_DATE`, missing/mismatched/truncated evidence suppression, current-versus-previous SQL distinction, and shared CLI/HTML/JSON action-gate tests. Relevant test names include `test_oracle_chain_victims_sleep_and_long_sql_are_separate`, `test_oracle_root_gate_requires_correlated_transaction_identity`, `test_oracle_truncated_transaction_sample_is_observed_but_not_verified`, `test_oracle_previous_sql_context_is_explicit_and_duration_matches_html`, `test_oracle_suppression_is_shared_by_cli_html_and_json`, and `test_oracle_allowed_reference_is_identical_across_cli_html_and_json`. The existing `tests/integration/run_oracle_verification.ps1` / `verify_oracle.py` are historical V0.7.1 fixtures, not approved current-HEAD commands: the PowerShell wrapper targets a fixed lab instance, uses privileged setup, creates users/table, and runs `pip install` from the Internet. Do not run them unchanged; use a reviewed, pinned, offline client/harness and unique test identifiers after authorization.

**Gates and sequence:**

1. Before touching a database, record `git rev-parse HEAD`, worktree status, package version, and run the offline Oracle regression set (`python -m pytest -q tests/test_oracle.py`). This is not E2E evidence. Check that the selected Oracle server is disposable (dedicated disposable PDB/container, not merely a schema on a shared/production service), identify service/PDB and version/edition, confirm credentials are supplied through the configured credential provider, and confirm the test client has no network/package-install step. If any condition is unclear, stop.
2. Have the isolated-environment owner provision, outside Toolkit, a unique test owner/table and separate test actors plus a monitor account with only the reviewed direct read grants for the Oracle Slow Profile. Do not add grants automatically. Store secrets only in the approved credential provider; use a temporary Inventory with aliases, not passwords. Reserve output under a private temporary directory excluded from Git.
3. With actors disconnected, inspect the read-only permission assessment using `dba --inventory <temporary-inventory> permissions <database-id> --feature slow`; confirm required direct grants and account/role safety, and do not use grant-generation options. Then run `dba --inventory <temporary-inventory> test-connection <database-id>` and `dba --inventory <temporary-inventory> diagnose <database-id> slow --report <private-temp>/baseline.html`. Require successful readiness, zero observed blocker roots, no `ROOT_BLOCKER` action text, bounded offline HTML, and no credential markers. Save sanitized metadata only.
4. For the blocking case, use only the dedicated disposable fixture table with two pre-seeded rows. Actor A updates row 1 and holds its transaction; actor B updates row 2, then attempts row 1; actor C attempts row 2. This establishes A → B → C (two lock edges) without changing application data. Use bounded client call timeouts and explicit coordinator signals confirming each lock/wait before sampling. The monitor performs only `dba ... diagnose ... slow --format json --report <private-temp>/blocking.html`; also capture a normal console run. Never execute the displayed ReferenceCommand. Check exactly one Root Blocker, at least two blocked victims with direct/indirect counts, transaction XID (`XIDUSN:XIDSLOT:XIDSQN`), start time sourced from `V$TRANSACTION.START_DATE`, root `INST_ID/SID/SERIAL#`, and distinct SQL identity/context: root `PREV_SQL_ID`/previous SQL is not relabeled current SQL; waiting actors' current SQL is associated with their own session identities. Verify the text-only action is gated by complete correlated identity and says Toolkit did not execute it.
5. Compare normalized evidence across console, JSON, and HTML: same sampled root identity, XID/start-time source, blocker chain/counts, current/previous SQL availability labels, and action `AVAILABLE`/`SUPPRESSED` status. HTML must be self-contained and bounded; outputs must contain no passwords or unrelated SQL. Raw SQL and identifiers are sensitive, so retain only sanitized excerpts/hashes and controlled local artifacts.
6. Exercise fail-closed paths before live workload cleanup: offline Fake tests must cover absent/conflicting session identity, transaction address mismatch/missing transaction, and truncated core evidence, and prove action suppression in CLI/JSON/HTML. If real truncation is additionally authorized, make a temporary copy of the SQL Registry under the private temp directory, lower only the copied Oracle collector row limit, invoke via `--sql-root <temporary-copy>`, and verify `truncated`/incomplete evidence suppresses the reference. Never edit the repository Registry for this test. Missing identity should use the existing Fake fixtures unless a safe, reproducible isolated-server mechanism has been separately approved; do not revoke shared grants just to manufacture a failure.
7. Cleanup in `finally`: release root A with client-side `ROLLBACK`; this should unblock B, then C; ensure both worker calls return within timeout, issue client-side `ROLLBACK` on each actor connection, close all connections/cursors, verify no test sessions remain, and drop only the uniquely named disposable fixture objects/users if their owner approves. No DBA-side KILL. If rollback/cleanup fails, stop and escalate to the isolated-environment owner; do not broaden cleanup to other sessions or objects.
8. Record exact HEAD, target type/version/edition/service scope, driver version/Thin mode, Profile, monitor direct grants/roles, sanitized command shapes, scenario results, output consistency, cleanup result, and any skipped case. Mark only the tested feature/version/environment `CURRENT_HEAD_E2E_PASSED`/`E2E_VERIFIED`; leave all untested targets/features `UNVERIFIED`. User-provided partial evidence remains separately labeled and is not overwritten.

The single documented invocation shape after approval is `dba --inventory <temporary-inventory> [--sql-root <reviewed-temporary-registry-copy>] diagnose <database-id> slow [--format json] --report <private-temp>/<case>.html`; the console case omits `--format json`. Do not run an invocation until the approval gate above is met.

- Exact Git HEAD, Oracle product version/edition, single-instance/RAC status, driver, service, Profile, direct permissions, and test account roles.
- No-block baseline and an approved disposable A→B→C or equivalent blocking chain.
- Root identity: INST_ID/SID/SERIAL#, logon/session identity, XID, V$TRANSACTION start-date source, current/previous SQL availability, direct/indirect victim counts.
- Complete versus missing/truncated/optional evidence behavior and ReferenceCommand suppression/display; `Toolkit Executed: NO`.
- Chinese CLI, JSON, offline HTML, redaction, report bounds, exit behavior, rollback, actor/session/object cleanup.
- The monitor account remains read-only and never executes KILL, GRANT, DDL, or DML.

### `V091-P0-PLAN-003`

The environment-gate, pre-unlock, and Stage A sections below are contemporaneous historical records and remain unchanged. They are superseded as the current Plan-003 result by the credential-provider recovery and Stage B current-HEAD record dated 2026-10-20 at the end of this section. The current task state is maintained only in Backlog.

#### 2026-10-20 environment gate result (preparation only; no database access)

| Item | Result |
|---|---|
| Exact Git HEAD | `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (`fix(oracle): align emergency SQL and identity presentation`). |
| Local Inventory metadata | An ignored local Inventory entry identified the approved SQL Server 2022 test target and its monitor credential alias; no username/password was read or recorded. The Inventory is local-only and is not part of this repository. Metadata alone does not prove the current service is disposable. |
| Historical target/version/driver | `docs/audits/test-report-v0.9.0.md` records SQL Server Developer `16.0.4305.5`; `docs/design/execution-plan-capability-safety-audit-v0.9.1.md` records pyodbc 5.3.0 and ODBC Driver 17 fallback. The Plan permission assessment was `READY` with `VIEW SERVER PERFORMANCE STATE`. These facts are historical and were not refreshed against the current instance. |
| Historical Plan fixture | `docs/VERIFICATION_V0_4.md` records a separate one-time disposable Docker SQL Server `16.0.4295.3` run using Python 3.12.15, pyodbc 5.3.0 and ODBC Driver 18; it reports fixture cleanup. The existing `tests/integration/verify_plan_sqlserver.py` and `plan_fixture.sql` create a workload login/table/index and later drop them. They were inspected but not run; the old `plan --session` route is not by itself proof of the current Version-Aware path. |
| Current isolation availability (initial sandboxed check) | Docker metadata inspection failed with permission denied in the initial execution context. A later metadata-only check after user confirmation is recorded below. |
| Current version / ODBC / test principal / live permissions | `NOT_VERIFIED` for the current instance. Historical values above must not be promoted. |
| Automated tests | Not run in this preparation-only turn. Prior audit records 111 targeted Plan/version/permission tests and quality gates, but they were not rerun. |
| Database access / writes / captured SQL | No database connection; no database read or write; no captured SQL executed. Credentials were not accessed. |
| Real E2E | `REAL_E2E_PENDING` / `BLOCKED`; current-HEAD Version-Aware representative cached-plan verification remains incomplete. |
| Blocker / required next step | Confirm an available isolated SQL Server target and explicitly confirm the exact test object/workload, cleanup boundary, and authorization. Then re-check actual server version, driver, Profile, principal and direct permissions before any live validation. |

No software Bug was opened: the stopping condition is unavailable/unconfirmed test infrastructure and scope, not an identified code defect. This record does not alter Oracle's independent blocked status or authorize any database operation.

#### Follow-up container identity and isolation review after user confirmation (2026-10-20)

| Item | Result |
|---|---|
| Container identity/runtime | Read-only Docker metadata matched the user-confirmed SQL Server 2022 test instance. |
| Network | The port was published beyond loopback, so this was not network-isolated by binding alone. Reachability remained dependent on host/network controls. |
| Data mounts | Persistent database and backup mounts were present; their contents were not inspected. |
| Credentials / live account | A local monitor credential alias was configured; no secret was read. The actual current principal and permission grants had not yet been refreshed from the database. |
| Decision | Container identity was confirmed, but network exposure, persistent mounts, and unrefreshed monitor permissions left the data/exposure boundary unresolved. Stop before database access until the user confirms these are acceptable for the isolated test scope and confirms the monitor principal/permission boundary. |
| Operations performed | Docker metadata only. No SQL Server connection, database read/write, credential access, query execution, object change, or test run. |

#### Stage A resumed after user confirmation (2026-10-20; stopped before DB connection)

| Item | Result |
|---|---|
| Authorization scope | User approved read-only environment checks and existing Plan Registry reads for the confirmed local SQL Server 2022 test instance only. No other Inventory asset or database was selected. Network publication, mounts, and volumes were left unchanged. |
| Inventory path/identity | The ignored local Inventory loaded offline and resolved one SQL Server 2022 test target with a monitor alias; local show selected ODBC Driver 17. No database connection was made by `show`. |
| Local client driver | `.venv/Scripts/dba.exe ... driver-info --json` reports Python 3.12.14, pyodbc 5.3.0, ODBC Driver 17 selected and Driver 18 absent. This describes the local client, not a server handshake. |
| Credential retrieval | The configured credential route reported `CredentialUnavailable` in this execution context because interactive unlock was required. No password/master password was requested in chat, retrieved, displayed, or modified. |
| Current server identity/version and live permission preflight | Not run because the existing encrypted credential session is locked. No fallback credential source was used. Historical `16.0.4305.5` and `VIEW SERVER PERFORMANCE STATE` readiness remain historical only. |
| Plan Registry, CLI/JSON/HTML, `.sqlplan` | Not run; no Plan query/report/artifact generated. Current cache hit, cache miss, permission degradation, and identity association remain pending. |
| Database writes/captured SQL | None. No SQL Server connection or query was performed; no captured SQL executed; no objects changed. |
| Risk record | The test service remained published beyond loopback; this is retained as an independent lab risk. Persistent data and backup mounts were not accessed or changed. |
| Status | `REAL_E2E_PENDING` / `BLOCKED` until the user unlocks the existing encrypted credential session locally. After unlock, resume only read-only checks and existing Plan reads. |

This environment gate does not imply a Plan code defect. No Bug was opened, and no database operation was attempted.

#### Stage A resumed after user unlock (2026-10-20; read-only loopback E2E partial)

| Item | Result |
|---|---|
| Exact Git HEAD / package version | `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (`fix(oracle): align emergency SQL and identity presentation`); package version remains `0.9.0`. |
| Authorized target | Only the user-confirmed local SQL Server 2022 test target was used. The configured monitor alias returned `READY` via the OS credential provider; no credential value was retrieved into output or recorded. No other asset/host was contacted. |
| Live server identity | Existing `server-info` Registry Collector reported SQL Server 2022 Developer Edition on Linux, product version `16.0.4305.5`, product level `RTM`, and no named instance. This matched the expected SQL Server 2022 test target. Host/container identifiers are intentionally omitted. |
| Client / profile | Python `3.12.14`, pyodbc `5.3.0`, ODBC Driver 17 selected (Driver 18 absent). The live detected family and Version-Aware Plan profile were `2022` / `sqlserver_plan_shared`; Permission Profile `sqlserver_plan_2022`; parser contract `sqlserver_showplan_v1`; version status `VERIFIED`, support `SUPPORTED`. |
| Permission/security readiness | Plan preflight `READY`, with `VIEW SERVER PERFORMANCE STATE` available and no missing required privileges. Account-security classification remained `UNKNOWN`; no role or permission changes were attempted. The connection configuration reported `Encrypt=no` and `TrustServerCertificate=yes`; ODBC 17 compatibility warning remained. These are recorded limitations, not changed here. |
| Read-only sequence and time | 2026-10-20 18:59–19:01 Asia/Shanghai: credential readiness, `check`, `server-info`, `active-sessions`, and `diagnose plan` commands were run against the approved test target. The Plan queries were the current reviewed `plan_request` and `plan_cached` Registry scripts. |
| Existing-session candidate | Only one non-Toolkit session was visible: session 53, `SQLServerCEIP`, sleeping, zero open transactions. No active user request was observed. Plan request Registry returned zero rows; cached Plan Registry returned one candidate row, but no reusable Showplan XML. Result: `PLAN_NOT_AVAILABLE`, `plan_available=false`, `metrics_scope=cached_last_execution`, `operator_count=0`, confidence `UNKNOWN`. Do not promote this to a representative plan/cache-hit verification or infer a defect. |
| Output agreement / artifact | Human CLI incident summary reported missing Plan evidence and no session termination command; structured JSON reported `PLAN_NOT_AVAILABLE`; generated offline HTML reported unavailable Plan evidence and had no external resource references. No `.sqlplan` was generated. Generated reports remain local, ignored, and potentially sensitive; report paths are not recorded here. |
| Writes / captured SQL / cleanup | Toolkit executed only reviewed read-only metadata and Plan Registry queries. Captured SQL was **not** executed. No DDL/DML, object creation, KILL, DBCC, transaction control, container/network/mount change, or backup-mount access occurred. No test objects or transactions were created, so no database cleanup was required. No `.sqlplan` file was emitted. |
| Automated tests | No tests were run in this E2E turn. This record is live read-only E2E evidence, not a new unit-test run. |
| Result / remaining acceptance | `E2E_PARTIAL`; task remains `E2E_PENDING`. Current identity/version/Profile/permission routing and unavailable-plan degradation are observed. A representative cached-plan hit, active-request plan, permission-denied live degradation, and artifact behavior with an actual `.sqlplan` remain unverified. Stage B requires a separate exact workload/object/cleanup proposal and explicit user approval; do not run a workload to force a cache hit. |
| Environment risk | The test service remained published beyond loopback as previously inspected. This remains a separate security risk; local Toolkit access does not authorize remote access. Persistent data and backup mounts remained untouched. |

**Follow-up connection gate:** the live check exposed `Encrypt=no` with `TrustServerCertificate=yes`, which does not meet `docs/PROJECT_RULES.md`'s SQL Server requirement for explicit encryption and certificate validation. No settings were changed. Do not make further live DB connections until the local runtime is configured compliantly, or the user explicitly approves the exact loopback-only unencrypted exception. This gate is independent of the pending test-workload approval.

No Bug was opened: a plan-unavailable result for the only visible system session is not evidence of a broken Plan feature. Oracle `V091-P0-ORACLE-002` remains independently blocked and unchanged.

#### Stage B preparation (2026-10-20; static review only, not authorized to connect)

**TLS source audit**

| Layer | Static finding |
|---|---|
| Effective source | Ignored local Inventory for the SQL Server test asset has `connection.encrypt: false` and `connection.trust_server_certificate: true`. This is the direct source of the Stage A effective values. No password/credential values were inspected or recorded. |
| Precedence | `dba/cli.py::_effective_settings` applies `resolve_connection_settings`; `dba/connection.py::resolve_connection_settings` validates the instance options and `dataclasses.replace`s the runtime defaults, so per-instance Inventory overrides runtime. The local `config/runtime.yaml` has secure defaults `encrypt: true`, `trust_server_certificate: false`; `dba/settings.py::SQLServerSettings` has the same secure code defaults. |
| Adapter / driver | `dba/sqlserver.py` explicitly emits both ODBC keywords from effective settings. Driver selection prefers ODBC 18, then 17. Stage A observed ODBC 17 selected and ODBC 18 absent. Current connection option allowlist does not support `HostNameInCertificate`; avoid relying on that Driver-18-only keyword. |
| Documentation discrepancy | `README.md` still states an isolated target may opt into `Encrypt=no` and says the Docker test entry is ready to connect. That wording is not authorization under the user's current decision and must not be used to bypass TLS. It remains for the later docs-fact task; README was not changed in this static-only turn. |

**TLS remediation proposal (no change made)**

1. Keep both client and server authentication checks enabled. Provision a CA-issued, valid server-authentication certificate for the SQL Server 2022 Linux instance, with the canonical client DNS name in its certificate identity (CN/FQDN and SAN as required by the chosen CA/client), and a chain trusted by the Windows client. Keep the private key only in a protected server-side location accessible to `mssql`; never put it in Git, Inventory, runtime YAML, backup mounts, or reports.
2. Because the current Inventory host is an IP literal while the certificate should be validated by a DNS identity, choose a stable test-only DNS name whose client-side resolution is explicitly restricted to the local machine; issue/configure the certificate for that exact name and have the future temporary test Inventory use that name. This resolution/Inventory change is a proposal only and needs separate approval. Do not use an untrusted alias or a certificate-name override to conceal mismatch.
3. An authorized SQL Server environment owner would install the certificate and key into the Linux SQL Server instance, restrict ownership/read permissions to the `mssql` account, configure `network.tlscert` / `network.tlskey`, and schedule the required SQL Server restart. Do not set global `network.forceencryption` or change container mounts/ports in this task; certificate rollout/restart is a separate explicitly approved operation. The existing all-interface published port remains an independent network risk.
4. Set effective client values to `Encrypt=yes` and `TrustServerCertificate=no` (prefer removing the insecure per-instance overrides so secure runtime defaults apply, or explicitly set the secure pair after the cert/name is ready). ODBC Driver 17 accepts `yes`/`no` and performs validation when encryption is requested; Driver 18 may be used when installed. Do not set `TrustServerCertificate=yes`, `Encrypt=no`, ODBC `ServerCertificate` pinning, or a client-wide TLS policy exception. After separate approval and cert provisioning, verify a test connection succeeds with normal chain and hostname validation; a certificate error must fail closed, not trigger a fallback.

Microsoft documentation reviewed: [ODBC 17/18 encryption differences](https://learn.microsoft.com/en-us/sql/connect/odbc/major-version-differences?view=sql-server-ver15), [ODBC encryption and connection-string keywords](https://learn.microsoft.com/en-us/sql/connect/odbc/dsn-connection-string-attribute?view=sql-server-ver17), [SQL Server on Linux TLS certificate and configuration](https://learn.microsoft.com/en-us/sql/linux/security/encrypted-connections?view=sql-server-ver16), and [`mssql-conf` TLS paths and restart behavior](https://learn.microsoft.com/en-us/sql/linux/configure/mssql-conf?view=sql-server-ver17). Microsoft documents that `TrustServerCertificate=yes` skips certificate validation, Driver 17 defaults to no encryption while Driver 18 defaults to encryption, and SQL Server on Linux needs an accessible TLS cert/key. No live TLS check was performed in this turn.

**Decision precedence note:** this latest user instruction supersedes the earlier Stage A note that left room for a narrowly approved no-TLS exception. For the current task no unencrypted exception is accepted; only trusted TLS with hostname/chain validation clears the connection gate.

**Proposed Stage B isolation boundary**

| Field | Proposal / gate |
|---|---|
| Stage B database | Proposed dedicated database `DBAToolkitPlanTest` on the already identified SQL Server 2022 test instance. It was not found/confirmed/created in this static review. It must be owner-provisioned and explicitly approved before execution. `master` is prohibited as the test database; do not create any fixture object there. No database creation command is included in the Toolkit path. |
| Objects | Pre-provision a test-only schema `[dba_toolkit_plan_test]` (owned by `dbo` or an explicitly named nonprivileged owner) and use only `[dba_toolkit_plan_test].[PlanFixture]`, constraint `[PK_DbaToolkitPlanFixture]`. Abort if the exact table already exists; never drop an object not created by this run. No secondary index is needed. |
| Test identities | Keep the Toolkit monitor identity separate from a dedicated test-only fixture identity. Do not put secrets in JSON, CLI args, environment variables, source, or logs; use the approved OS credential provider and keep values in process memory. Existing account-security evidence is `UNKNOWN`; before any run the owner must confirm the monitor is not `sysadmin`/`db_owner` and its direct permissions are least-privilege. |
| Monitor permissions | On SQL Server 2022, only the Plan Profile's existing required server permission `VIEW SERVER PERFORMANCE STATE`, `CONNECT SQL`, and database `CONNECT` to `DBAToolkitPlanTest` are in scope. No `VIEW ANY DEFINITION`, `sysadmin`, `db_owner`, `CONTROL SERVER`, GRANT execution, or production permission changes. Re-run current Permission Registry/Preflight after TLS and database readiness; if it reports a gap, stop and request an owner-reviewed grant decision. |
| Fixture-driver permissions | Separate isolated test-only identity, limited to `CONNECT` on `DBAToolkitPlanTest`, database `CREATE TABLE`, `ALTER` on `[dba_toolkit_plan_test]`, and `SELECT, INSERT` on that schema. No server role or production access. A test-environment owner must provision the schema and identity outside Toolkit after approval. |
| Execution boundary | A separate test driver owns all fixture DDL/DML and SELECT workload. Toolkit receives only a session ID and executes the existing reviewed `plan_request` / `plan_cached` Registry queries. Toolkit must not receive fixture-owner credentials and must not execute the sample SQL. No `DBCC FREEPROCCACHE`, cache clearing, KILL, stored procedure installation, server configuration, or application-data access. |

**Exact proposed workload SQL (not executed)**

Precondition: `DBAToolkitPlanTest` exists and the exact fixture table does not. The dedicated schema may be pre-provisioned; if it is absent, only the environment owner may create it using a connection explicitly opened to `DBAToolkitPlanTest` (never `master`):

```sql
IF DB_NAME() <> N'DBAToolkitPlanTest'
BEGIN
    ;THROW 51001, 'Wrong database; refusing Plan fixture setup.', 1;
END;
CREATE SCHEMA [dba_toolkit_plan_test] AUTHORIZATION [dbo];
```

The independent driver first checks that the target table is absent and aborts on collision:

```sql
IF OBJECT_ID(N'dba_toolkit_plan_test.PlanFixture', N'U') IS NOT NULL
    THROW 51000, 'Plan fixture already exists; refusing to overwrite it.', 1;
```

Then, only in `DBAToolkitPlanTest`, the independent fixture driver runs:

```sql
CREATE TABLE [dba_toolkit_plan_test].[PlanFixture] (
    [id] int NOT NULL
        CONSTRAINT [PK_DbaToolkitPlanFixture] PRIMARY KEY CLUSTERED,
    [join_key] int NOT NULL,
    [sort_key] int NOT NULL,
    [payload] varchar(80) NOT NULL
);
```

```sql
;WITH n ([id]) AS (
    SELECT CONVERT(int, 1)
    UNION ALL
    SELECT [id] + 1 FROM n WHERE [id] < 5000
)
INSERT INTO [dba_toolkit_plan_test].[PlanFixture]
    ([id], [join_key], [sort_key], [payload])
SELECT [id], [id] % 100, 5001 - [id], CONCAT('fixture-', [id])
FROM n
OPTION (MAXRECURSION 5000);
```

On one persistent fixture-driver connection, capture its `@@SPID`, then run one query at a time; after each query finishes, invoke Toolkit for that same SPID to test the `cached_last_execution` path:

```sql
SELECT SUM(CONVERT(bigint, [join_key])) AS [checksum]
FROM [dba_toolkit_plan_test].[PlanFixture] WITH (INDEX(1));
```

```sql
SELECT COUNT_BIG(*) AS [matches]
FROM [dba_toolkit_plan_test].[PlanFixture] AS a
INNER HASH JOIN [dba_toolkit_plan_test].[PlanFixture] AS b
    ON b.[join_key] = a.[join_key]
WHERE a.[id] BETWEEN 1 AND 500
OPTION (MAXDOP 1);
```

```sql
SELECT TOP (100) [id], [payload]
FROM [dba_toolkit_plan_test].[PlanFixture]
ORDER BY [sort_key] DESC
OPTION (MAXDOP 1);
```

Expected evidence to inspect in the *actual* Showplan, not assume from SQL text: a clustered scan in the scan query, a Hash Match in the hinted join, and a Sort/Top-N Sort in the unindexed sort query. If SQL Server produces another operator, record the actual plan without forcing additional workload or changing indexes.

For current-request capture, the driver runs this bounded read on a worker thread and signals immediately before dispatching the Toolkit's read-only Plan command; the 2.5 million row-pair shape is limited to the 5,000-row fixture and `a.id <= 500`. If it completes before sampling, record current-request E2E as pending; do not enlarge the workload or add `WAITFOR`/blocking merely to force a hit.

```sql
SELECT SUM(CONVERT(bigint, a.[join_key]) * CONVERT(bigint, b.[join_key])) AS [checksum]
FROM [dba_toolkit_plan_test].[PlanFixture] AS a
CROSS JOIN [dba_toolkit_plan_test].[PlanFixture] AS b
WHERE a.[id] BETWEEN 1 AND 500
OPTION (MAXDOP 1);
```

For the live-request and subsequent cached result, compare identity internally: `session_id`, `sql_handle`, statement start/end offsets, `query_hash`, `query_plan_hash`, and `plan_handle` where present. The current sample should be `source=active_request`; after completion the same still-connected SPID should return `source=cached_last_execution`. Do not publish raw handles in shared evidence. Verify operator nodes from parsed Showplan and distinguish current-request metrics from last-execution cache metrics.

**CLI/report check and cleanup**

1. Use a temporary, non-versioned Inventory at a private local path that points only to `DBAToolkitPlanTest` and the verified DNS name, references the existing monitor alias, and sets `encrypt: true`, `trust_server_certificate: false`. Do not edit the existing local Inventory in this task. After TLS is ready and separately authorized, run `dba ... diagnose <asset> plan --session <spid>` for normal human CLI and `dba ... diagnose <asset> plan --session <spid> --json --report <private>/plan.html` for JSON plus offline HTML and paired `.sqlplan` when available. Current CLI accepts `--json`; the legacy `--format json` is a hidden compatibility option, not needed.
2. Compare normalized availability/source/session/statement identity/operator fields across CLI, JSON and HTML. Confirm HTML is self-contained and does not embed raw Showplan XML; inspect the `.sqlplan` as a sensitive artifact. Check output/report/artifact in memory for known credential markers, `UID=`, `PWD=`, connection strings, and unrelated SQL; never print secret values. Keep reports local/ignored and do not commit them.
3. After worker completion, close all fixture/workload sessions. The independent fixture driver drops only the table it created:

   ```sql
   IF DB_NAME() <> N'DBAToolkitPlanTest'
   BEGIN
       ;THROW 51001, 'Wrong database; refusing Plan fixture cleanup.', 1;
   END;
   DROP TABLE [dba_toolkit_plan_test].[PlanFixture];
   ```

   Verify only that exact table is absent. Leave the owner-provisioned schema and database intact. If a worker has an open transaction, the driver rolls back its own transaction and closes that connection before drop; Toolkit never commits/rolls back, kills sessions, or cleans objects. On cleanup failure, stop and escalate to the test-environment owner; do not broaden cleanup.

**Existing fixture reuse decision:** `tests/integration/plan_fixture.sql` and `verify_plan_sqlserver.py` were inspected only; neither was executed. Reusable ideas are a small deterministic row set, query shapes for scan/join/sort, persistent workload SPID, paired HTML/`.sqlplan`, and a `finally` cleanup block. The existing script must not be reused unchanged because it hardcodes a Docker host and `master`, connects setup as `sa`, creates a server login/user and a `dbo` table/index, grants broad `VIEW ANY DEFINITION`, uses `TrustServerCertificate=yes`, and accepts administrator/workload/monitor password values in a JSON stdin config. It marks setup complete only after the whole setup batch, so partial setup can bypass its cleanup; its assertions verify post-completion cached plans but don't deliberately sample a live current request, and it doesn't scan `.sqlplan` for credentials. Its current CLI `--format json` option is accepted but hidden; prefer `--json`.

**This turn result:** static source/document review only. No database connection, `pytest`, integration script, SQL workload, DDL/DML, TLS/certificate/DNS/Inventory change, package install, commit, or push occurred. Task stays `E2E_PENDING`; Stage A evidence remains partial and unchanged.

#### Stage B authorization recovery and environment check (2026-10-20; no DB connection)

The user's latest lab authorization supersedes the earlier Stage B workload/TLS approval gates. It permits bounded operations in the identity-confirmed local Docker lab by an independent test driver, while the Toolkit remains on its separate least-privilege monitoring identity and reviewed read-only Registry path. The lab-only `Encrypt=no` / `TrustServerCertificate=yes` exception was accepted for this confirmed SQL Server test target only; it is not production-compliant and does not change secure defaults. `LAB-SQLSERVER-TLS-001` tracks later lab hardening.

| Item | Result |
|---|---|
| Exact Git HEAD / package version | `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (`fix(oracle): align emergency SQL and identity presentation`); package version `0.9.0`. |
| Container identity | Read-only Docker metadata confirmed the user-authorized SQL Server 2022 test instance and its published service port. No network, container, mount, or volume changes. This reconfirms container metadata only, not the live SQL Server product version. |
| Test-driver configuration | A user-designated local-only configuration identified the SQL Server sample database and a test principal; no credential value was retained. The configuration was not changed or copied, and no other configured target was contacted. |
| Toolkit Inventory and TLS source | Ignored local Inventory selected the SQL Server test asset and monitor alias; its per-instance override set `encrypt: false` and `trust_server_certificate: true`. CLI effective-setting output confirmed `Encrypt=no; TrustServerCertificate=yes` before connection. This exception was explicitly limited to the authorized lab. |
| Toolkit credential gate | The Toolkit check and credential-readiness probe stopped with `CredentialUnavailable` because the configured encrypted store required interactive unlock in that execution context. A prompt was cancelled without entering a secret. The administrator test-driver credential was not used by Toolkit and no credential was emitted in the report. |
| Existing script audit | `sqlserver_lab.py` is for a lock chain and `WAITFOR` slow query over `dbo.DBA_INCIDENT_LAB`; it creates/inserts and later drops that fixed table, so it is not a Plan E2E driver and must not be run for this task. `verify_plan_sqlserver.py` / `plan_fixture.sql` are V0.4-only and unsafe unchanged: fixed target, `master`, login/user/table/index creation, grants, and JSON-stdin secrets. Neither script was executed. |
| Preferred Stage B workload | Once the monitor identity is available, prefer existing `AdventureWorks2022` sample data and no new objects. An independent test driver may run a bounded, read-only representative join/group/sort SELECT over the sample's Sales and Product tables; verify the objects and query shape before execution. Do not force plan operators. Keep the driver session open for cached-last-execution lookup; if an active request cannot be captured naturally, record it as not verified rather than adding an unbounded workload. No SQL was run in this attempt. |
| Cleanup boundary | The preferred sample-data path creates no database objects or persistent data changes. Close only the driver sessions started for the test; confirm they are gone. No plan-cache flush, DDL, DML, transaction cleanup, or global state change is permitted/needed for this query-only plan. Locally generated reports/`.sqlplan`, if later produced, remain sensitive ignored artifacts and are not copied into tracked docs. |
| Operations and result | Docker metadata reads and local file/source review only; no SQL Server connection, query, database read/write, captured SQL execution, report, or `.sqlplan`. No automated test was run in this environment-check portion. Stage B did not execute. |
| Status and resumption | Stage A remains `E2E_PARTIAL`. Current-HEAD cache hit, live request, identity association, actual node parsing, CLI/JSON/HTML/`.sqlplan`, and output redaction remain unverified. The task status was `BLOCKED` solely until the approved monitor credential route became available; the lab and ordinary bounded workload were already authorized. Do not ask for a lab password in chat or substitute the administrator account in Toolkit. |

The earlier TLS remediation proposal and dedicated `DBAToolkitPlanTest` fixture remain historical options only; they are not prerequisites under the latest user decision. The all-interface port publication remains a separate exposure risk and is not changed by this loopback test authorization.

#### Offline Plan regression after environment gate (2026-10-20)

`& .venv\Scripts\python.exe -m pytest tests/test_execution_plan.py -q` completed with **26 passed** in 11.32 seconds. This is automated/offline evidence only; it does not change the Stage A partial or Stage B blocked real-E2E result. No database was accessed by pytest.

#### Credential-provider recovery and SQL Server Stage B current-HEAD E2E (2026-10-20)

This record supersedes the earlier Stage B note that required unlocking the Encrypted Local Store. The approved OS credential provider was verified with Credential Session locked; no credential-store mutation or code change was required.

| Item | Result |
|---|---|
| Exact HEAD / package | `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`; software version remains `0.9.0`. |
| Credential session/provider | Credential status returned `LOCKED`; the configured OS credential provider returned `READY` for the approved SQL Server and MySQL monitor aliases. The Encrypted Local Store remained present but locked. No secret value was displayed/read into this evidence record, and the encrypted store was not unlocked/read/modified. |
| Routing review | `CredentialResolver(auto)` checks the OS store first and immediately returns a matching OS alias before querying `CredentialSessionClient` or the encrypted store. The CLI credential test path likewise resolves the OS alias before encrypted fallback. In a restricted sandbox, the Windows API returned access denied (`ERROR_ACCESS_DENIED`), making the provider unavailable in that process; the authorized same-user execution context successfully retrieved the alias. This is an execution-context boundary, not a reproduced application fallback defect. No source fix was warranted. |
| Live Toolkit identity/profile | `dba check` succeeded through the configured monitor alias on the single approved test target. SQL Server Developer Edition 64-bit, `16.0.4305.5`, family `2022`; Python `3.12.14`, pyodbc `5.3.0`, ODBC Driver 17. Profile `sqlserver_plan_shared`; Permission Profile `sqlserver_plan_2022`; parser `sqlserver_showplan_v1`; Plan Preflight `READY` (`VIEW SERVER PERFORMANCE STATE`). The approved lab-only `Encrypt=no` / `TrustServerCertificate=yes` setting was used; this is not production TLS validation. |
| Account-safety caveat | Toolkit account-security classification remained `UNKNOWN`; the check reported no high-risk server roles/permissions or database roles/ownership, but also reported unknown/unclassified effective permission evidence. This is not a complete privilege audit and is not represented as one. Toolkit ran only its existing reviewed read-only Registry path. The independent lab test driver authenticated as the configured lab administrator (`sysadmin`); that identity was never passed to Toolkit. |
| Independent workload | Reused an existing lab-driver connection helper and local-only configuration in-process, but not its lock/DDL/DML simulator path. After validating the approved target, sample database, actual version, and driver account, the driver executed only a bounded `TOP (5000)` join/sort SELECT across SalesOrderHeader, SalesOrderDetail, and Product tables, date-bounded to 2013 and `MAXDOP 1`. The captured run made 10 read-only repetitions and streamed 50,000 rows. No schema/object creation, data modification, transaction control, plan forcing, or cache clearing occurred. |
| Cache and current request | Cached Plan returned `PLAN_AVAILABLE`, source `cached_last_execution`. Concurrent bounded sampling captured three `active_request` plans in four probes; a subsequent cached read returned `cached_last_execution`. For the same driver session, the active and cached results matched `session_id`, `sql_handle`, `plan_handle`, `query_hash`, `query_plan_hash`, and statement start/end offsets. Values were compared in memory and are intentionally not recorded. |
| Parsed plan | Eight RelOp nodes, with observed physical operators `Clustered Index Scan`, `Index Scan`, `Hash Match`, `Merge Join`, `Sort`, and `Compute Scalar`. Operators are reported from the actual Showplan, not inferred from query text. |
| CLI / JSON / HTML / `.sqlplan` | Human CLI and JSON commands exited successfully and reported plan availability. JSON source distinguishes `active_request` from `cached_last_execution`; offline HTML contains the actual operator labels, has no external references, and does not embed raw `ShowPlanXML`. Paired `.sqlplan` files parsed with a `ShowPlanXML` root and eight RelOp nodes. The known test-driver password was absent from the inspected HTML and `.sqlplan`; connection-string credential fields were absent. Reports remain ignored local artifacts; report paths are intentionally omitted. |
| Writes and cleanup | Toolkit executed only reviewed read-only Plan/permission Registry SQL; `Toolkit write: NO`. Independent test driver executed only SELECT; `test-driver write: NO`. No test objects were created. Driver connection was closed; a final session check found zero remaining `dba-toolkit` sessions. No object/data cleanup was required. Docker port/network/mounts/data volume and backup mounts were not changed/accessed. |
| Automated tests | Credential-provider/session/connection regression set: `.venv\Scripts\python.exe -m pytest tests/test_encrypted_credentials.py tests/test_connection_credentials.py tests/test_credential_session.py -q` — **73 passed, 1 skipped**. Plan parser/route set: `.venv\Scripts\python.exe -m pytest tests/test_execution_plan.py -q` — **26 passed** on this same HEAD. No product code changed. |
| Result / limits | Stage A `PLAN_NOT_AVAILABLE` cache-miss degradation remains valid evidence. Stage B cached and active request retrieval, SQL/plan identity association, nodes, outputs and cleanup are verified for this SQL Server 2022 lab scope only. This is not a claim for other SQL Server versions, drivers, accounts, databases, or complete monitor-account privilege classification. No Bug was found. |

The local lab service remained published beyond loopback; no network change was made. The earlier “credential alias requires encrypted-store unlock” blocker is superseded by successful default-provider retrieval while the session was locked.

- Exact HEAD/version/driver/Profile/permission and safe target identity.
- One available cached plan retrieved through the current route and one ordinary cache-miss result.
- HTML/JSON/`.sqlplan` bounds, sensitive-artifact handling, ambiguity behavior, no captured-SQL execution, and cleanup.

### `V091-P0-FOURDB-004`

- Per-database target, version, driver, account safety, Profile, feature, result, completeness, and limitations.
- Explicit `NOT_RUN`, `BLOCKED`, or `PARTIAL` rather than inferred success for unavailable targets.
- No cross-version or cross-feature extrapolation.

#### 2026-10-20 current-HEAD four-lab regression

This addendum supersedes earlier scheduling notes that FOURDB-004 or Oracle-002 were awaiting authorization/target confirmation. Those notes remain historical. Only user-approved local Docker labs were used; no other Inventory assets were accessed. Exact source HEAD for every run: `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (`fix(oracle): align emergency SQL and identity presentation`); package version remained `0.9.0`. Times below are local `Asia/Shanghai` on 2026-10-20. A lab network-exposure risk remained separately recorded; no port, network, mount, volume, or backup-path change/access occurred.

| Database | Target/version and client | Toolkit credential/Profile/Preflight | Current-HEAD findings and output | Status and explicit limits |
|---|---|---|---|---|
| SQL Server | Approved local SQL Server 2022 test instance; Developer `16.0.4305.5`; ODBC Driver 17; `pyodbc 5.3.0`; diagnostic Inventory remained on `master` because the monitor login was denied opening `AdventureWorks2022` (4060). Tested 22:14–22:40. | Separate monitor identity; Slow `sqlserver_slow_shared` / `sqlserver_shared_slow` / `sqlserver_slow_2022` READY; Disk `sqlserver_disk_2022` READY; Replication permission READY; Plan profile `sqlserver_plan_shared` / permission `sqlserver_plan_2022` referenced from the separate verified Plan-003 record. Account security classification `UNKNOWN` (unclassified effective permission evidence). | Slow baseline and bounded A→B→C simulator: `BLOCKING`, `ROOT_BLOCKER`, `IDLE_TRANSACTION_BLOCKER`, `LONG_RUNNING_SQL`, `WAITING_SQL`; one root and two graph edges; root XID/transaction age/time and session identity; current/previous SQL fields were observed. CLI/JSON/offline HTML aligned; ReferenceCommand remained manual-only; Disk-Growth collection/output succeeded. Replication collector returned `REPLICATION_NOT_CONFIGURED`. | `VERIFIED` for the exercised SQL Server 2022 features. Plan E2E is referenced, not repeated. Monitor could not connect directly to AdventureWorks2022, and account-security classification is not a full privilege audit. No broad version claim. |
| PostgreSQL | PostgreSQL `16.15 (Debian)`; `psycopg 3.3.6`; fixture was isolated to a temporary unique schema in the existing `pagila` sample database. Tested 22:20–22:23. | Separate monitor identity; Slow `pg13_18_slow` READY; Disk `postgresql_disk_13_16` / permission READY; Replication permission READY; Plan is not applicable/unsupported on this path. Monitor classified `NON_PRIVILEGED`. | Baseline Slow/Disk-Growth and Replication collections succeeded; `REPLICATION_NOT_CONFIGURED`. Bounded A→B→C workload produced `BLOCKING`, `ROOT_BLOCKER`, `IDLE_IN_TRANSACTION`, `LONG_RUNNING_SQL`, `WAITING_SQL`; one root, two graph edges; root PID matched the actor and transaction start/current SQL context were present. CLI/JSON/offline HTML aligned; ReferenceCommand manual-only; truncation/sensitive-value checks passed. | `VERIFIED` for the exercised PostgreSQL 16 paths. No replica topology was provisioned, and this does not verify all supported PostgreSQL target versions. |
| MySQL | MySQL Community `8.4.11`; `PyMySQL 1.1.3` distribution metadata. Tested 22:24–22:27. | Separate monitor identity; Slow `mysql_slow_modern` / `mysql84_slow` READY; Disk `mysql_disk_84` READY; Replication permission READY. Monitor classified `NON_PRIVILEGED`. | Baseline Slow/Disk-Growth and Replication collections succeeded; `REPLICATION_NOT_CONFIGURED`. Bounded A→B→C workload produced `BLOCKING`, `ROOT_BLOCKER`, `IDLE_TRANSACTION`, `LONG_RUNNING_SQL`, `WAITING_SQL`; one root/two graph edges; root process ID and test-driver identity matched, transaction/XID and age evidence were present. Sleeping-root current SQL was absent as expected; last-SQL evidence was not inferred from the snapshot. CLI/JSON/offline HTML aligned; manual-only reference and truncation/sensitive-value checks passed. | `PARTIAL`: exercised MySQL 8.4 diagnostic scenarios passed, but the imported module reports version `2.2.8` while installed distribution metadata and Toolkit report `1.1.3`. This runtime provenance mismatch was not modified or explained. `MYSQL-DRIVER-PROVENANCE-001` is retained in Backlog; do not extrapolate driver compatibility until resolved. Digest Top SQL remains unimplemented/out of this task. |
| Oracle | Oracle AI Database 26ai Free `23.26.3.0.0` (`v$instance.version=23.0.0.0.0`); `python-oracledb 3.4.2`, Thin. Fixture in confirmed test schema `CHINOOK`. Tested 22:28–22:35. | Separate monitor identity; Slow `oracle_modern_slow`; required route worked, but optional `V$LOCK` / `V$LOCKED_OBJECT` checks made preflight `PARTIAL`; Disk-Growth route also `PARTIAL` for optional recovery/archive views; Replication permission READY. Monitor classified `NON_PRIVILEGED`. | Baseline and A→B→C tests; `BLOCKING`, `ROOT_BLOCKER`, `IDLE_TRANSACTION`, `LONG_RUNNING_SQL`, `WAITING_SQL`; root SID/SERIAL# matched the independent driver, XID and transaction start time were present, current/previous SQL were distinguished, and current SQL was observed on an active fixture request. CLI/JSON/offline HTML aligned; incomplete/truncated evidence suppressed ReferenceCommand; complete evidence rendered it as manual-only. SQL redaction and offline HTML checks passed. Replication returned `REPLICATION_NOT_CONFIGURED`. | `PARTIAL` for FOURDB feature breadth because optional Slow/Disk permissions are absent and no grants were made. The narrower P0.2 acceptance task `V091-P0-ORACLE-002` is `VERIFIED` for this Oracle Free 26ai lab scenario only. No 11gR2/older, RAC, or full Disk-Growth claim. |

**Driver workload and cleanup.** SQL Server reused the reviewed lab simulator against `AdventureWorks2022` and its dedicated fixture; the independent driver performed bounded test DDL/DML/lock simulation, each worker rolled back and exited, the exact table was dropped, and the final read-only check confirmed it absent with zero tagged sessions. PostgreSQL and MySQL used isolated fixtures in existing public sample databases; Oracle used only a confirmed test schema. Each fixture was prechecked, workers rolled back/closed, only the run-owned fixture was removed, and independent post-cleanup checks confirmed each object absent and zero tagged sessions. Previously existing fixtures were deliberately preserved. No production or non-lab DB was accessed.

For every database, `Toolkit writes: NO`; Toolkit used a separate monitor identity, reviewed read-only Registry/permission paths, and never executed captured business SQL, KILL, grants, or remediation. Driver-side DDL/DML was confined to the authorized disposable test fixtures and cleaned. Replication was not configured on the single-instance lab targets; no primary/replica topology was created. SQL Server Plan is covered only by the separate Plan-003 Acceptance section. Reports are local ignored artifacts; they were checked for offline references and test credential markers and were not added to Git. Raw Oracle identity values/SQL handles and credential values are not recorded here.

**Automated evidence.** `.venv\Scripts\python.exe -m pytest tests/test_sqlserver.py tests/test_postgresql.py tests/test_mysql.py tests/test_oracle.py tests/test_slow.py tests/test_slow_profiles.py tests/test_disk_growth.py tests/test_replication.py -q` — **349 passed in 46.85s**. This is automated regression evidence in addition to, not a substitute for, the per-database real E2E above. No product source, SQL Registry, Adapter, Inventory, or version was changed.

**Oracle-002 closure scope.** The current-HEAD run covered a non-blocking baseline; a multi-level blocker chain; correlated root identity, XID and transaction start; active current SQL and prior SQL context; CLI/JSON/HTML consistency; complete-evidence manual-only ReferenceCommand display; and safe suppression on truncated evidence. A test-only Registry copy limited rows and was kept outside the repository; it did not weaken or change the production Registry. No KILL was executed. This closes the specified P0.2 acceptance for the recorded Oracle Free 26ai lab only, while the optional permission gaps and cross-version/RAC limitations remain separate evidence gaps.

## 11. Documentation fact reconciliation (2026-10-20)

`V091-P0-DOCS-005` updated current product descriptions to reflect the user-confirmed six-database targets and distinguish target ranges, implemented Version-Aware routes, and exact current-HEAD E2E cells. README and Version Profiles now refer to the evidence above rather than promoting historical or single-version results. Existing historical Verification reports and the reconciliation audit were not rewritten.

No database was connected and no E2E or pytest suite was rerun for this documentation task. Documentation checks cover relative Markdown link targets, task-ID references, scope/status wording, and `git diff --check`; results are recorded in Progress. The separate `LAB-CREDENTIAL-OUTPUT-001` Backlog item tracks the prior sensitive-output incident and requires a safe allowlisted reader/redaction design with automated regression coverage. No credential value is included in this ledger. `MYSQL-DRIVER-PROVENANCE-001` and `LAB-SQLSERVER-TLS-001` remain independent Backlog items; neither blocks Phase 1 triage. The user approved Phase 0 closeout and Phase 1 entry on 2026-10-20.

## 12. Phase 1 baseline and planned acceptance — V091-P1-TRIAGE-001

### Baseline inspection (2026-10-20)

This is a static source/design review at HEAD `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e` (package version `0.9.0`). No database was connected, no pytest or integration test was run, and no code or SQL Registry changed. Phase 0 results above remain scoped to their recorded databases/versions; they are not new Phase 1 evidence.

| Capability | Reusable current implementation | Baseline gap / safe boundary |
|---|---|---|
| Root Blocker / chain | `dba/slow.py`, `dba/postgresql_slow.py`, `dba/mysql_slow.py`, and `dba/oracle_slow.py` each build vendor-specific blocking graphs, roots, anomalies, and root evidence. Existing code has identity gates for action references. | Reuse graph/root facts; do not duplicate root attribution in a universal analyzer. Incomplete/truncated/anomalous graphs cannot support a definitive “no blocker” conclusion. |
| Blocking Victim | Graph descendants and victim/request details already exist; MySQL and Oracle expose `blocked_victims` explicitly, and the shared Presenter renders blocking trees. | SQL Server/PostgreSQL do not expose the same normalized victim contract. Make victim a distinct evidence-backed label, not a cause/root; preserve native edge and wait fields. |
| Independent Slow SQL | Existing per-database `LONG_RUNNING_SQL` findings use existing request/session evidence and thresholds. MySQL and Oracle already exclude confirmed blocked victims from independent slow candidates; PostgreSQL filters blocked/intentional sleep sessions in its long list. | Preserve these semantics; ensure blocking requests are not recast as independent root causes. Runtime threshold is a duration signal only and cannot imply CPU, I/O, or lock causality. |
| Long-running Transaction | Existing transaction collectors and idle/open-transaction findings exist across the four relational adapters; root evidence includes transaction context where collected. | Transaction age is not SQL runtime. SQL Server has stronger association in the confirmed blocking-root path than a general standalone long-transaction classifier; the other engines have different idle/open transaction states. Missing transaction rows, timestamps, or identity must remain UNKNOWN. |
| Evidence Insufficient / UNKNOWN | Slow profiles declare required/optional collectors; reports retain samples, timestamps, success/truncation and completeness; permission/Profile checks fail closed. | Convert missing, failed, truncated, unsupported, permission-limited, or identity-conflicting evidence into explicit classification/source reasons, never healthy/no-issue by default. |
| Identity and SQL context | `dba/slow_profiles.py` has vendor parser mappings; CLI runs the current Profile-resolved Slow path and serializes report details. | Do not flatten identities: SQL Server session/request IDs; PostgreSQL PID plus backend start; MySQL PROCESSLIST_ID vs Performance Schema THREAD_ID vs InnoDB trx_id; Oracle INST_ID/SID/SERIAL# plus SQL_ID/XID. Preserve current/previous SQL source and availability reason. |
| Presentation | `dba/incident.py`, `dba/presentation.py`, `dba/reporting/presenters.py`, and the shared offline HTML renderer provide incident summaries, priority candidates, JSON, CLI, and HTML. | `build_priority_candidates` has a categories list but current related-session presentation stores a single role string and does not consistently expose each conclusion's source/completeness as a stable common contract. Add only minimal reusable triage result/source references and render them consistently; do not create another renderer. |

Existing capability is sufficient for a first triage result without adding database SQL, provided existing required evidence is available and internally consistent. Insufficient evidence must produce UNKNOWN/PARTIAL. The minimum implementation delta is a reusable multi-label domain result (classification(s), native identity, evidence references/source IDs, observed time/window, completeness/unknown reasons, and safe next check) plus shared CLI/JSON/HTML fields and focused tests. No new Collector, Registry SQL, Permission Registry entry, profile, service, Top SQL ranking, double-sampling, or historical-trend claim is part of this task unless implementation review proves an indispensable gap and that delta is separately approved.

### Planned acceptance — not yet executed

These are acceptance criteria for `V091-P1-TRIAGE-001`, not evidence that the task has passed:

1. Fake/unit cases independently produce Root Blocker, Blocking Victim, Independent Slow SQL, Long-running Transaction, and Evidence Insufficient/UNKNOWN. A session may retain multiple supported labels; a victim is not called a root cause, and a root/transaction label is not erased by another role.
2. Chain, duration, wait, transaction, SQL, database identity, and evidence source are traceable to actual Collector/Registry/Profile data. Missing permission, failed/unsupported/truncated sampling, ambiguous identity, inconsistent snapshots, or graph anomalies produce explicit UNKNOWN/PARTIAL and reasons.
3. A blocked long-duration request remains a Blocking Victim and is not used to claim an independent slow SQL cause. Non-blocked long SQL is only a duration candidate; no CPU/I/O/lock attribution without a matching native metric/wait fact.
4. Long-running transaction is computed from transaction evidence/state, not the SQL elapsed threshold. Distinguish idle-open, active, and unavailable/ambiguous transaction evidence according to each engine's native semantics.
5. SQL Server, PostgreSQL, MySQL, and Oracle retain distinct identity keys, wait semantics, SQL-context semantics, and supported/optional evidence behavior. Do not infer equivalence from normalized labels.
6. JSON, CLI, and offline HTML show consistent labels, identity, evidence source/time, completeness, and limitations; HTML remains escaped, bounded, and offline. Raw payloads and SQL text remain under existing redaction/report budgets.
7. Existing Slow/Blocking regression tests pass; security review confirms Toolkit remains strict read-only, with no captured SQL execution, KILL, DDL/DML, or privilege expansion. Test-driver workload, if needed for later E2E, remains separate.
8. Automated Fake/Mock evidence and real E2E evidence are recorded separately per exact current HEAD/database/version. Reuse Phase 0 E2E where applicable without presenting historical or scoped evidence as fresh Phase 1 verification.

Evidence state: this baseline review adds no Phase 1 test or E2E result. `V091-P1-TRIAGE-001` is the first implementation task and sole task owner in Backlog.

### Implementation and automated evidence (2026-10-20; final verification 2026-10-21 Asia/Shanghai)

Implemented at working tree based on Git HEAD `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`; package version remains `0.9.0`. No commit was created. `dba.incident.build_performance_triage` adds a bounded, multi-label contract over existing Slow snapshot evidence. Every recognized or withheld rule has a rule ID, classification-level status, source collector/analyzer, stable source path, sample timestamps, integrity, bounded facts, and uncertainty reasons. It carries database/profile/permission and snapshot metadata, uses evidence-derived review ordering, and always reports `root_cause_status=NOT_ESTABLISHED`.

Root Blocker requires a complete, anomaly-free observed graph and downstream impact; Blocking Victim requires an observed edge and adequate identity; an intermediate node keeps both labels. Independent Slow SQL requires existing long-request evidence and a complete graph proving the session is outside every known graph node; a blocked/root-chain request can retain a long-request observed signal without being labeled independent. Long-running Transaction requires the engine's existing transaction finding plus native transaction age/identity; SQL elapsed time is not substituted. Oracle `INST_ID/SID/SERIAL#` disagreements or incomplete session identity suppress affected classifications. MySQL PROCESSLIST_ID, THREAD_ID and trx_id remain distinct; PostgreSQL PID/backend_start and SQL Server session/request identifiers remain distinct. Missing/failed/truncated/unresolved evidence is UNKNOWN/PARTIAL, not a clean health assertion.

The contract is attached to the existing Slow report `details`, preserving older fields. JSON carries the same structured object; console and bounded offline HTML expose the labels, rule IDs, identity, source/time/integrity and limitations. No SQL text, credentials, new Registry SQL, database permission, Adapter/Profile, or product version change was added. The default legacy priority candidate list remains compatible; the CLI triage view explicitly includes blocker/victim nodes. No CPU/I/O cause, history, or confirmed root cause is inferred.

Automated evidence (Fake/fixtures; not database E2E):

- `.venv\\Scripts\\python.exe -m pytest -q` — **763 passed, 2 skipped**.
- `.venv\\Scripts\\python.exe -m pytest tests/test_performance_triage.py tests/test_emergency_diagnosis.py tests/test_mysql.py::test_mysql_realistic_blocking_chain_threshold_sleep_and_transaction_identity tests/test_oracle.py::test_oracle_priority_candidate_reason_join_has_clean_chinese_punctuation tests/test_html_reports.py tests/test_incident.py -q` — **56 passed**.
- `.venv\\Scripts\\python.exe -m ruff check .` — passed.
- `.venv\\Scripts\\python.exe -m mypy dba` — passed, 60 source files.

The tests cover a three-level A→B→C chain, B's simultaneous victim/intermediate roles, root plus long transaction, independent/blocked long requests, distinct transaction and SQL durations, graph truncation/anomalies, permission failure, Oracle serial conflict, four database-native identity shapes, per-label evidence references, JSON/CLI/HTML consistency, SQL/credential non-copying in the triage object, and existing Slow/Blocking compatibility.

### Current-HEAD real E2E — partial (2026-10-21 Asia/Shanghai)

Exact source HEAD: `f840cdc15f91648dc2bc683b5ea8eb5a8d08b74e`; package version remains `0.9.0`. The working tree was already dirty before this run; no product source, SQL Registry, Adapter, Profile, Inventory, Docker configuration, or package version was changed. Only the previously authorized local Docker lab was used. An initial sandboxed Docker API call returned `permission denied`; a read-only retry in the approved execution context succeeded. No context switch, restart, rebuild, volume deletion, or remote connection occurred. Four local lab targets matched the authorized database families: SQL Server 2022, PostgreSQL 16, MySQL 8.4, and Oracle Free. Their services were published beyond loopback; all Toolkit and driver connections in this run remained on the local machine.

The four configured monitoring aliases returned `OS credential provider / READY`. A sandboxed auto-provider check had selected the locked Encrypted Local Store because OS access was not exposed in that process; the approved local-provider check succeeded. No master password was requested, no encrypted-store value was read, and the encrypted store was left locked and unchanged. The independent driver validated its local-only target and credential availability in-process; no configuration value or password was emitted or copied.

| Lab target / actual identity | Permission/profile and workload | Current-HEAD triage result | E2E state |
|---|---|---|---|
| SQL Server 2022; Developer `16.0.4305.5`; Toolkit target database `master`; `AdventureWorks2022` was reported ONLINE by reviewed `sys.databases`. | Toolkit pyodbc `5.3.0` / Microsoft ODBC Driver 17; Slow permission readiness `READY`. Independent driver used an elevated lab-only principal, separate from Toolkit. The reviewed simulator created one previously absent dedicated fixture, ran bounded 100-second A→B→C plus independent slow-request workload, then dropped only that fixture. An earlier 75-second attempt was sampled after it ended and is not counted. | At `2026-10-21T00:21:40+08:00`: triage `COMPLETE`; root carried `ROOT_BLOCKER` + `LONG_RUNNING_TRANSACTION`; middle carried `BLOCKING_VICTIM` + `INTERMEDIATE_BLOCKER`; leaf was a `BLOCKING_VICTIM`; chain-external request was `INDEPENDENT_SLOW_SQL`. Rule IDs/source paths and snapshot time were present; all root-cause statuses stayed `NOT_ESTABLISHED`. Human CLI, JSON, and offline HTML showed the same five labels. | **VERIFIED for the exercised SQL Server 2022 triage scenarios only.** Not a version-matrix claim. |
| PostgreSQL 16; server `16.15`, `server_version_num=160015`; `psycopg 3.3.6`; an existing public sample database and fixture were used. | Separate monitor identity; tested Slow permission readiness `READY`. The fixture had the expected columns and keys; the test identity had SELECT/UPDATE and test-scope object-creation permission. Ran the reviewed simulator against the existing fixture only; no setup/drop. All workers rolled back and exited. | At `2026-10-21T00:28:43+08:00`: triage `COMPLETE`; root had `ROOT_BLOCKER` + `LONG_RUNNING_TRANSACTION`, middle had both victim/intermediate labels, leaf had victim; PID + `backend_start` and evidence references were present. Human CLI/JSON/offline HTML agreed on the observed labels. Synthetic sleep is explicitly excluded from independent-long-SQL attribution by the current analyzer, so `INDEPENDENT_SLOW_SQL` was not proven by this fixture. A late post-workload sample returned UNKNOWN/no candidates and was not treated as a pass or health assertion. | **PARTIAL.** Blocking roles and transaction were verified; independent slow SQL and a genuine incomplete-evidence scenario remain unverified. |
| MySQL 8.4; Community `8.4.11`; existing public sample database and fixture were used. | Imported PyMySQL module `2.2.8`, distribution metadata `1.1.3` (existing `MYSQL-DRIVER-PROVENANCE-001`; not changed). `permission_status=READY`, but reports showed `data_visibility=PARTIAL`. Independent driver used fixture-scoped SELECT/UPDATE; extra fixture-management privileges were observed but unused. Ran bounded simulator transactions on the existing fixture only; no setup/drop. | At `2026-10-21T00:31:57+08:00` and `00:32:57+08:00`: root had `ROOT_BLOCKER` + `LONG_RUNNING_TRANSACTION`, middle had victim/intermediate labels, leaf had victim. Identities kept `PROCESSLIST_ID`, `THREAD_ID`, and root `trx_id` separate; rule/source references were present; root cause stayed unestablished. Triage remained `PARTIAL` because `data_visibility=PARTIAL`. The independent synthetic sleep session was absent from the read-only active-request result, so no `INDEPENDENT_SLOW_SQL` label was claimed. In a repeated chain sample, CLI/JSON/offline HTML agreed on the observed role labels and offline status. | **PARTIAL.** Blocking roles, native IDs, and long transaction verified. Independent slow SQL and the source of partial visibility remain unverified. |
| Oracle Free; `V$INSTANCE.VERSION=23.0.0.0.0`; `python-oracledb 3.4.2`, Thin. A confirmed test schema was used. | Separate monitor identity; Toolkit read-only baseline used profile `oracle_modern_slow`, with permission readiness and data visibility `PARTIAL`. Independent driver connection to the approved test target failed with vendor code ORA-28009. No alternate authentication mode, grant, schema change, or workload was attempted. | At `2026-10-21T00:37:27+08:00`: JSON triage was `UNKNOWN`, permission status `PARTIAL`, and no candidate assessment was emitted; this is not a clean-health conclusion. JSON/HTML exposed UNKNOWN/PARTIAL, but the human CLI did not print the overall triage/permission status or corresponding evidence limitation. | **BLOCKED** for failure-scenario E2E; read-only identity/baseline only. See `BUG-P1-TRIAGE-CLI-STATUS-001` in Backlog. |

**Commands and evidence artifacts.** The E2E used read-only Docker metadata checks; credential readiness, `server-info`/`database-status`, and `slow` diagnosis commands; and the reviewed standalone lab simulator `simulate` actions. No Toolkit command ran custom SQL. Generated reports remain local/ignored and were not copied into versioned docs; their paths and contents are intentionally omitted because they may contain sensitive diagnostic evidence.

**Cleanup and write boundary.** Toolkit database writes/DDL/DML/KILL/privilege changes: **NO**. It used separate monitor identities, reviewed Registry/Profile/preflight paths, and only performed current diagnosis reads. Test-driver SQL Server writes: **YES**, limited to creating/populating and then dropping the run-specific fixture in `AdventureWorks2022`; post-cleanup read verified that fixture absent and zero tagged simulator sessions. PostgreSQL/MySQL test drivers issued updates only inside transactions and rolled back; pre-existing fixtures were preserved, and post-checks found zero simulator sessions. Oracle test-driver writes: **NO**. Docker configuration, persistent mounts, credentials, grants, and instance settings were not changed. No captured business SQL was run by Toolkit.

**Unclosed acceptance gaps.** `INDEPENDENT_SLOW_SQL` was only demonstrated on SQL Server. The PostgreSQL lab helper uses synthetic `pg_sleep`, which existing code deliberately excludes; MySQL's synthetic SLEEP session did not appear in the current active-request evidence and must remain unclassified. No controlled incomplete/truncated evidence case was created in the databases; Fake/Mock regression remains separate from real E2E. Oracle test workload remains blocked by ORA-28009 and requires a correctly scoped test-driver connection decision. Oracle's empty/partial baseline also exposed a CLI-vs-JSON/HTML status/limitation presentation gap; `BUG-P1-TRIAGE-CLI-STATUS-001` is recorded as TODO. The first SQL Server and PostgreSQL samples taken after their workload ended are recorded as mistimed/UNKNOWN evidence, not passes.

Automated evidence remains the prior implementation run at this same source HEAD: full pytest **763 passed, 2 skipped**; `ruff check .` and `mypy dba` passed. These suites were not rerun during this E2E/documentation turn. Real E2E status is per the matrix above and is not inferred from those offline tests. `V091-P1-TRIAGE-001` remains `E2E_PENDING` in Backlog; no Phase 2 task was started.

## 13.1 CLI status Bug fix and final E2E recovery attempt (2026-10-21 Asia/Shanghai)

**Source baseline.** Git HEAD was `469083a3cf0680ad24aaad7aa496740722e5b45a`; package version remains `0.9.0`. The worktree already contained the user's untracked operation manual and it was preserved. This entry records working-tree code/tests plus an E2E readiness attempt; no commit was created.

**BUG-P1-TRIAGE-CLI-STATUS-001 — fixed, automated scope verified.** The gap was in `render_emergency_triage` in the shared CLI presentation layer: it rendered a generic incomplete-evidence sentence but did not consume the existing `performance_triage` status or uncertainty fields. The fix adds a bounded human summary of overall triage status, permission readiness, data visibility, root-cause status, and up to five evidence-limit reasons. Collector behavior, triage classification rules, and JSON fields were not changed. New tests cover an empty Oracle UNKNOWN baseline and a truncated PARTIAL snapshot and compare CLI output with the existing JSON and offline HTML values. Real Oracle presentation remains unverified in this turn.

**Automated evidence (current working tree, not database E2E).** `tests/test_performance_triage.py` — **14 passed**; full `.venv\\Scripts\\python.exe -m pytest -q` — **765 passed, 2 skipped**; `.venv\\Scripts\\ruff.exe check .` — passed; `.venv\\Scripts\\python.exe -m mypy dba` — passed (60 source files). No database E2E evidence is inferred from these tests.

**Lab availability and write boundary.** Read-only checks found the selected local Docker context's engine unavailable, Docker Desktop not running, and all four approved loopback database ports (SQL Server 1433, PostgreSQL 5432, MySQL 3306, Oracle 1521) closed. No Toolkit or independent test-driver database connection was attempted; no SQL workload, DDL/DML, account, object, or database cleanup action occurred. Windows Credential Manager retrieval was READY for the existing SQL Server, PostgreSQL, and MySQL monitor aliases. No Oracle-named credential target was present in the OS credential listing. An attempt in the restricted process to test a monitor alias prompted for the encrypted-store master password; no password was entered, and the encrypted store was not unlocked or modified.

**Oracle driver static review.** The existing standalone Oracle simulator reads a single configured credential for its driver user, and the configured user is `SYS`; its connection function does not request SYSDBA mode and uses that same identity for setup and workload sessions, which explains the previously observed ORA-28009 and violates the ordinary-workload-identity boundary. It creates/updates `DBA_INCIDENT_LAB` in the login's current schema and removes that table during cleanup. No driver or schema change was applied because the live target is unavailable and a corrected, separate ordinary actor identity cannot be verified in this run. The required safe adaptation is: use SYSDBA only for bounded PDB identity verification and provisioning/cleanup; create a unique temporary low-privilege actor schema (or use an existing ordinary test-schema credential if available); run all simulated sessions as that ordinary actor; clean up only the run-owned table/account after confirming no worker sessions remain. The separate Toolkit monitor account remains read-only. No production path is involved.

| Database | Latest result for this task attempt | E2E status |
|---|---|---|
| SQL Server 2022 | Earlier triage E2E remains historical at its recorded HEAD; shared CLI Presenter changed in this working tree, so current-HEAD output regression was not live-tested. | `PENDING` |
| PostgreSQL 16 | No connection; bounded compute-only independent slow SQL and live complete-blocking-graph evidence were not sampled. Existing `pg_sleep` is not accepted as the required independent slow SQL sample. | `BLOCKED` |
| MySQL 8.4 | No connection; bounded compute-only independent slow SQL and live `data_visibility`/identity behavior were not sampled. Existing `SLEEP` is not accepted as active-request evidence. | `BLOCKED` |
| Oracle Free 26ai | No connection; current configured driver principal is SYS but is used as an ordinary login; no Oracle monitor alias was available from Windows Credential Manager in this process. P0.2/earlier Oracle evidence remains scoped to its historical HEAD and does not verify this Phase 1 working tree. | `BLOCKED` |

**UNKNOWN/PARTIAL evidence.** Automated tests pass for missing permissions, missing/failed/truncated blocking evidence, identity conflict, and UNKNOWN/PARTIAL presentation. No live database evidence-loss scenario was run. `Toolkit writes: NO`; `test-driver writes: NO`; cleanup is `NOT_APPLICABLE` because no workload/session/object was created. A representative current-HEAD SQL Server E2E regression is still required because the shared Presenter changed.

`BUG-P1-TRIAGE-CLI-STATUS-001` is `VERIFIED` for the automated rendering fix in Backlog. `V091-P1-TRIAGE-001` remains `E2E_PENDING`; the four-database evidence matrix is incomplete and no Phase 2 work started. Resume only after the existing Docker lab is available and the Oracle monitor alias/test-driver adaptation gates are resolved.

## 13.2 Oracle lab simulator SYSDBA/ordinary-actor repair (2026-10-21 Asia/Shanghai)

**Source baseline.** Git HEAD is `469083a3cf0680ad24aaad7aa496740722e5b45a`; software version remains `0.9.0`. The repository worktree was already dirty, including an untracked user manual; it was preserved. Only the specifically authorized external Oracle simulator files were edited for this repair. No commit or push was made.

**Oracle lab driver adaptation (code changed; live behavior unverified).** `oracle_lab.py` now rejects `SYS` through the ordinary `connect()` function. Its separate `connect_sysdba()` explicitly requests `oracledb.AUTH_MODE_SYSDBA` and gates the operation to a loopback host, port 1521, and service `FREEPDB1`. When the configured lab user is SYS, the driver permits only a single `simulate` action: it verifies the connected PDB is `FREEPDB1`, checks the Free 23.x instance version, and confirms the existing `CHINOOK.TRACK` sample rows before making changes. It then creates an unpredictable disposable actor, grants only CREATE SESSION and SELECT/UPDATE on `CHINOOK.TRACK`, and runs the bounded lock-chain driver workload through the ordinary actor. The run performs transactional row updates and the shared driver rolls them back. Finally, the simulator checks `V$SESSION`; it drops the temporary actor only if all actor sessions have exited, otherwise it leaves the account in place to avoid interrupting a live test session and reports its generated username for cleanup. The existing user config/password was neither displayed, modified, nor copied. Toolkit Collector, Registry, Adapter, Profile, Inventory, and production monitor identity were not changed.

This is a statically reviewed adaptation, not a successful Oracle execution. In particular, the presence/shape of `CHINOOK.TRACK`, the grants, SYSDBA connection to the configured PDB, and cleanup behavior still require live validation against the approved lab. The script fails closed before account creation if the target identity/version or expected sample rows do not match.

**Current availability and database boundary.** Read-only checks on this run found Docker context `desktop-linux`, Docker Desktop not running, Docker Engine API unavailable, and `127.0.0.1:1521` closed. No Oracle connection attempt was made; no database SQL, account, grant, workload, or cleanup action occurred. No other approved endpoint was contacted. Accordingly Oracle Root Blocker, victim/intermediate roles, transaction duration, native identity/evidence association, CLI/JSON/HTML, and live UNKNOWN/PARTIAL remain unverified for this working-tree state.

**Automated and static evidence (not E2E).** `test_oracle_lab_driver.py` — **4 passed** with fake modules and no credential/config import; cases cover ordinary SYS rejection, explicit SYSDBA mode, remote-target rejection, and use/cleanup of a disposable ordinary actor against the existing sample object. Ruff passed for the new test file. AST parsing passed for the two edited/added external Python files. Repository tests `tests/test_oracle.py tests/test_performance_triage.py` — **61 passed**. The full project suite was not rerun. Running Ruff against the entire legacy `oracle_lab.py` reports three existing E701 one-line-suite findings at the legacy `setup`, `cleanup`, and `timeout` helpers; those unrelated lines were not changed.

**Files and secrets.** Outside the repository: the existing `oracle_lab.py` received the narrow driver change and `test_oracle_lab_driver.py` was added for credential-free Mock coverage. Neither `local_config.py` nor other engine scripts were changed. No password, DSN, or config object was printed or added to Git. Toolkit database writes: **NO**. Test-driver database writes: **NO** in this attempt, because the lab was unavailable. Cleanup: **NOT APPLICABLE** (no account/session/object created).

`V091-P1-TRIAGE-001` stays `E2E_PENDING`. The Oracle simulator fix removes the observed ordinary-SYS authentication defect by separating administrative setup from ordinary workload identity in its intended execution path, but does not close Oracle live acceptance. Resume when the existing Docker lab is running and its Oracle target is accessible; resolve the distinct Toolkit Oracle monitoring credential through an approved provider without unlocking or modifying the encrypted store. Then run the simulator and Toolkit read-only diagnosis, verify the actual SQL/identity/evidence and report parity, and confirm zero test sessions and removal of the disposable account. Other triage gaps remain as recorded above.

## 13.3 Four-database triage recovery and PARTIAL-visibility correction (2026-10-21 Asia/Shanghai)

**Source baseline and scope.** Commit HEAD remained `469083a3cf0680ad24aaad7aa496740722e5b45a`; package version remains `0.9.0`. All four user-approved loopback targets were reached through their existing Inventory entries and Credential Manager monitor aliases; the encrypted local store was not unlocked. Database identities/versions checked for this run were SQL Server Developer `16.0.4305.5`, PostgreSQL `16.15`, MySQL `8.4.11`, and Oracle Free 26ai in `FREEPDB1` (`V$INSTANCE.VERSION=23.0.0.0.0`). Docker Engine availability was not used as a gate. No local credential file was printed, copied, or edited.

**SQL Server 2022 — Presenter regression VERIFIED for the empty/UNKNOWN snapshot.** The current read-only command was `dba diagnose sqlserver2022 slow --json`, paired with the human CLI invocation and the generated HTML. The JSON triage status was `UNKNOWN`, with zero assessments and no confirmed labels; CLI also contained `UNKNOWN` and no Independent label; paired HTML contained `UNKNOWN` and had no external-resource references. This is a snapshot result, not a database-health assertion. No test-driver workload or database writes were used in this check. This closes the requested real current-HEAD empty-baseline status-presentation regression; it does not extend SQL Server feature/version coverage.

**PostgreSQL 16 — blocking roles VERIFIED; Independent Slow SQL remains PENDING.** The existing `pagila` fixture and rows were reused. A finite computation-only `generate_series`/MD5 SELECT started at `2026-10-21T11:59:47Z` and ran for 77.12 seconds under a transaction-local 85-second `statement_timeout`; no `pg_sleep`, DDL, or DML was used. Several live Toolkit samples did not expose a qualifying long-running candidate, so no `INDEPENDENT_SLOW_SQL` conclusion is claimed. A final synchronized sampling attempt failed in its local Python wrapper while decoding subprocess JSON with the host GBK codec; it produced no usable Toolkit result. Afterward, a read-only check found zero `DBA-Standalone-PG-Lab` sessions and the existing fixture rows remained `(1,0)` and `(2,0)`. This sampling/visibility gap is not evidence that the Collector is defective or that no slow SQL existed; the independent-slow real E2E remains open.

**MySQL 8.4 — PARTIAL-safe Independent Slow SQL behavior observed; positive independence remains unverified.** The existing Sakila data and fixture were reused; no setup/cleanup DDL was run. A finite SHA2 aggregation over bounded `sakila.film` row subsets used the statement-scoped `MAX_EXECUTION_TIME` hint (85-second cap, then 80-second cap) and did not use `SLEEP`. At approximately `2026-10-21T11:55:49Z`, a live snapshot observed a running statement with distinct `PROCESSLIST_ID` and Performance Schema `THREAD_ID`; post-test read-only checks found no matching active computation and fixture rows remained unchanged. Performance Schema statement/thread views were readable, but preflight/data visibility was `PARTIAL` (`instrumented_waits` unavailable). The Toolkit did not execute captured SQL. The query was interrupted by its per-statement timeout; the driver connection closed.

The first live snapshot exposed a false-positive path: despite PARTIAL data visibility and UNKNOWN evidence integrity, a long request received `INDEPENDENT_SLOW_SQL / CONFIRMED`. A minimal fix now treats top-level or permission-profile PARTIAL/UNKNOWN visibility, non-ready permission states, and non-complete full-profile evidence as incomplete blocking evidence in both priority-candidate and triage assessment construction. Regression cases cover report-level and nested PARTIAL/UNKNOWN visibility, missing permission, and incomplete full-profile evidence. A post-fix live sample retained an UNKNOWN classification and did not assign the Independent label; its offline HTML assessment also showed UNKNOWN evidence status and the explicit uncertainty that blocking evidence does not prove independence. The human CLI sample contained no Independent label and retained the PARTIAL limitation. Positive real MySQL independence is not verified because the blocking graph cannot currently prove full visibility.

**Oracle Free 26ai — blocker-chain behavior PARTIAL, identity and cleanup VERIFIED for this workload.** The reviewed existing `oracle_lab.py` was run once for 75 seconds (local start `2026-10-21T19:57:02+08:00`; Toolkit sample `2026-10-21T11:57:45Z`). Its local `SYS AS SYSDBA` connection was limited to PDB identity/sample-row checks, disposable actor creation, minimal grants, and cleanup. Three ordinary actor sessions used existing `CHINOOK.TRACK` rows; transactional updates were rolled back. The real graph contained one root candidate and two blocking edges. Toolkit output associated `INST_ID/SID/SERIAL#` for all three sessions; the root's XID and transaction duration were observed, and SQL_ID/session associations were present for active victims. The middle session retained both `BLOCKING_VICTIM` and `INTERMEDIATE_BLOCKER`; the leaf was `BLOCKING_VICTIM`; the root retained `LONG_RUNNING_TRANSACTION`. Root attribution was explicitly `UNKNOWN`, not confirmed, because the read-only monitor lacks optional `SELECT SYS.V_$LOCK` and `SELECT SYS.V_$LOCKED_OBJECT`; data visibility and the result remained `PARTIAL`. No privilege change was made to the Toolkit monitor. The live workload snapshot's positive role labels and evidence limits were present in the paired CLI/JSON/HTML reports; subsequent empty baseline checks showed `UNKNOWN`/`PARTIAL` consistently across all three outputs and offline HTML had no external resources. A read-only SYSDBA cleanup verification found zero `DBATRIAGE%` users and zero corresponding sessions. No existing user password was changed.

**UNKNOWN/PARTIAL and read-only boundary.** Real UNKNOWN was observed on the SQL Server empty baseline. Real PARTIAL was observed in MySQL due incomplete wait/blocking visibility and in Oracle due optional lock-view permissions. PostgreSQL's missed long-query candidate is recorded as unverified, not a negative/healthy result. Toolkit database writes, DDL/DML, KILL, grants, and captured-business-SQL execution: **NO**. Independent Oracle test-driver writes: **YES**, limited to one disposable user/grant set and transactional updates on existing sample rows, rolled back; cleanup verified. PostgreSQL/MySQL drivers issued only bounded read-only SELECTs. Existing sample databases and fixtures were retained. No test session remained after checks.

**Automated/static evidence.** After the final classifier and regression-test edits: `.venv\Scripts\python.exe -m pytest -q` — **770 passed, 2 skipped**; `.venv\Scripts\ruff.exe check .` — passed; `.venv\Scripts\python.exe -m mypy dba` — passed (60 files); credential-free external `test_oracle_lab_driver.py` — **4 passed**; `git diff --check` — passed. The Oracle live workload is distinct from these automated tests.

**Current conclusion.** `BUG-P1-TRIAGE-PARTIAL-VISIBILITY-001` is `VERIFIED` for the fix and safety behavior: automated regression plus a live MySQL PARTIAL sample withheld the Independent label. `V091-P1-TRIAGE-001` remains `E2E_PENDING`: PostgreSQL independent-slow candidate was not observed; MySQL positive independent classification is not provable with current PARTIAL visibility; Oracle Root Blocker remains UNKNOWN under current least privilege; and no live malformed/truncated-evidence injection was made. Task status remains controlled by Backlog. No commit/push or Phase 2 work occurred.

## 13.4 Targeted triage gaps, Oracle grant/revoke, and final CLI parity (2026-10-21 Asia/Shanghai)

**Source and scope.** Commit HEAD was `469083a3cf0680ad24aaad7aa496740722e5b45a`; package version remained `0.9.0`. Tests ran against the current uncommitted working tree, including the shared CLI final-summary fix and the existing evidence-safety changes. No commit or push was created. Only the four previously authorized local loopback labs were used. Database CLI output was captured in memory with explicit UTF-8 decoding; no credential values, raw local configuration, or full SQL text were emitted. Docker CLI availability was not used as a gate.

**PostgreSQL 16 — Independent Slow SQL VERIFIED for this scoped case.** The earlier 77.12-second computation began at `2026-10-21T11:59:47Z` and ended at approximately `12:01:04Z`; the first saved report capture began at `12:01:13.626925Z`, around nine seconds after completion. A subsequent saved report began at `12:02:35.784172Z`. Those samples did not overlap the workload, so the prior absence of a candidate was a sampling-window miss; saved evidence did not indicate a Collector, profile, permission, identity, or analyzer-filter failure. A separate orchestration attempt was lost when its implicit Windows GBK decoder rejected CLI output. A test-only wrapper now pins `encoding="utf-8"` with strict decoding, covered by a regression simulating `cp936` locale and Chinese JSON text (`tests/test_triage_cli_runner.py`).

For the bounded retest, PostgreSQL `16.15`, profile `pg13_18_slow`, Permission Readiness `READY`, and the `60s` long-running threshold were confirmed. One compute-only SELECT over existing Pagila sample data and bounded generated values ran with transaction-local `statement_timeout=82s`; it started at `12:22:33.993543Z`, completed at `12:23:49.930850Z`, and was sampled at `12:23:36.589961Z` with native PID `157`, duration `62,615ms`, `state=active`, and no wait event. Active-session, transaction, blocking, and long-running collectors succeeded without truncation; visibility was `FULL`. The result was `INDEPENDENT_SLOW_SQL / CONFIRMED`, rule `TRIAGE-INDEPENDENT-SLOW-01`, backed by complete long-running duration evidence and a complete blocking sample. Paired offline HTML: `reports/pg16_slow_20261021_202336.html`. The test connection closed and a read-only post-check found zero `DBA-Standalone-PG-Triage` sessions. No DDL/DML or Toolkit business-SQL execution occurred.

**MySQL 8.4 — PARTIAL source verified; no forced independence.** Current read-only evidence identified MySQL Community `8.4.11`, profile `mysql84_slow`, Permission Readiness `READY` with no missing optional permissions, but `data_visibility=PARTIAL` and full-profile status `PARTIAL`. All five current collectors succeeded without truncation. Preflight could read Performance Schema setup state and found the `events_waits_current` consumer disabled. The adapter consequently selected its reviewed `active_requests_no_waits` fallback, which intentionally supplies no current wait detail; the report explicitly records this limitation. This is a Performance Schema configuration limitation, not a missing grant, empty-data inference, or demonstrated Collector defect. Existing live evidence retained distinct `PROCESSLIST_ID` and `THREAD_ID`; the post-fix real sample withheld `INDEPENDENT_SLOW_SQL` in CLI/JSON/HTML while visibility remained PARTIAL. No wait consumer, MySQL permission, or server setting was changed. Current offline reports: `reports/mysql84_slow_20261021_203644.html` and `reports/mysql84_slow_20261021_203645.html`. Positive MySQL independence remains unverified while blocking/wait visibility cannot support it.

**Oracle Free 26ai — Root Blocker VERIFIED after temporary exact grants; initial permissions restored.** Before any change, the monitoring identity connected through its existing Windows Credential Manager alias to PDB `FREEPDB1`; SYSDBA independently confirmed Oracle Free version `23.0.0.0.0`. Direct `SELECT` grants on `SYS.V_$LOCK` and `SYS.V_$LOCKED_OBJECT` were absent, and there were zero `DBATRIAGE%` disposable users/sessions. In this PDB only, SYS AS SYSDBA temporarily granted exactly those two object privileges. Toolkit preflight changed from `PARTIAL` to `READY`, visibility `FULL`; no DBA, catalog-wide, role, or unrelated privilege was granted.

The reviewed `oracle_lab.py` used SYSDBA only for temporary ordinary-actor setup/cleanup; three workload sessions used a disposable ordinary actor against existing `CHINOOK.TRACK` rows, with updates rolled back. At `12:35:23.729567Z`, the graph supported one confirmed root and two victim edges. The root had `(INST_ID=1, SID=177, SERIAL#=11078)`, XID `9:20:790`, labels `ROOT_BLOCKER` and `LONG_RUNNING_TRANSACTION`, and complete evidence for `TRIAGE-ROOT-01` from `active_sessions:details.blocking_graph.roots`, plus transaction-age evidence. The middle session retained `BLOCKING_VICTIM` and `INTERMEDIATE_BLOCKER`; the leaf was `BLOCKING_VICTIM`; native Oracle identities included INST_ID/SID/SERIAL# and active SQL_ID associations. JSON, human CLI final update, and offline HTML agreed on the four labels; root cause remained `NOT_ESTABLISHED`. Paired live reports: `reports/oraclefree_slow_20261021_203523.html` and `reports/oraclefree_slow_20261021_203524.html`.

After the simulator exited, checks found zero temporary users and sessions. Only the grants added by this run were revoked; the final direct-grant set was empty, matching the initial state. A read-only post-revoke diagnosis returned `PARTIAL` permission/data visibility and `UNKNOWN` triage in JSON, CLI, and offline HTML; no existing password changed. Post-revoke reports: `reports/oraclefree_slow_20261021_203645.html` and `reports/oraclefree_slow_20261021_203646.html`.

**SQL Server 2022 — final shared Presenter parity VERIFIED.** The lab identity was SQL Server `16.0.4305.5`, database `AdventureWorks2022`, ODBC Driver 17, profile `sqlserver_shared_slow`, permission profile `sqlserver_slow_2022` READY. The run-owned `dbo.DBA_INCIDENT_LAB` fixture was confirmed absent before setup; the bounded 75-second simulator created/populated it, ran A→B→C plus an independent request, rolled back worker transactions, and explicit cleanup dropped the fixture. No Plan E2E was repeated. The first run showed JSON/HTML confirmed Root/Victim/Intermediate/Independent/Long-transaction labels while the human summary reflected an earlier partial snapshot. `BUG-P1-TRIAGE-CLI-FINAL-PARITY-001` was registered and fixed: when supplemental evidence changes the triage fingerprint, CLI emits a final updated summary while preserving the early emergency summary. After the fix, a second bounded A→B→C sample showed the same five labels in final CLI, JSON, and offline HTML; overall status was `COMPLETE`, while root-cause status remained `NOT_ESTABLISHED`. Empty post-cleanup baseline was `UNKNOWN` with zero assessments in JSON, CLI, and offline HTML. Reports: `reports/sqlserver2022_slow_20261021_203317.html`, `reports/sqlserver2022_slow_20261021_203318.html`, and empty baseline `reports/sqlserver2022_slow_20261021_203416.html`. Cleanup confirmed the fixture absent and zero `DBA-Standalone-SQLServer-Lab` sessions.

**CLI bug and automated verification.** The CLI fix changes presentation timing only; it does not alter database collectors, SQL Registry, classification rules, or JSON schema. Regressions cover completed-summary fingerprint changes, two-stage collection order, and UTF-8 subprocess decoding under simulated Chinese Windows `cp936`. Full repository pytest: **772 passed, 2 skipped**; `ruff check .`: **passed**; `mypy dba`: **passed** (60 source files); external credential-free Oracle simulator tests: **4 passed** (pytest emitted only a cache-write permission warning for the parent `.pytest_cache`). `git diff --check`: **passed** (Git emitted only line-ending normalization warnings).

**UNKNOWN/PARTIAL, write boundary, and remaining gaps.** Real `UNKNOWN` was observed on the SQL Server empty baseline. Real `PARTIAL` was observed for MySQL's disabled waits consumer and Oracle after the exact grants were revoked; all channels preserved the limitations, and neither asserted Independent. Toolkit DDL/DML, grants, KILL, or captured-SQL execution: **NO**. Independent drivers performed only authorized Oracle temporary user/privilege setup and transactional fixture updates (rolled back), plus SQL Server's run-owned fixture setup/drop; PostgreSQL workload was read-only; no new MySQL workload ran in this targeted turn. Temporary identities, sessions, and the SQL Server fixture were cleaned; no data volume or sample database was removed. No real malformed/truncated-evidence injection was performed; those cases remain automated-only.

`BUG-P1-TRIAGE-CLI-FINAL-PARITY-001` is `VERIFIED` in Backlog. `V091-P1-TRIAGE-001` remains `E2E_PENDING`: MySQL positive Independent classification is not currently provable while its wait consumer is disabled, and live malformed/truncated/incomplete evidence injection remains unverified. Do not start Phase 2. No commit or push occurred.

## 13.5 MySQL positive Independent Slow SQL and layered evidence closeout (2026-10-21 Asia/Shanghai)

**Source and scope.** Commit HEAD remains `469083a3cf0680ad24aaad7aa496740722e5b45a`; the working tree was already dirty and remains uncommitted. Software/package version is `0.9.0`. This was a targeted MySQL 8.4 follow-up; previously recorded SQL Server 2022, PostgreSQL 16, and Oracle Free 26ai evidence in §13.3–§13.4 was not rerun or overwritten. Only the authorized MySQL Docker lab at `127.0.0.1:3306` was used. Existing Toolkit Inventory target `mysql84` resolved to the OS Credential Manager alias `test-only-mysql-monitor`; credential status was READY in the approved local execution context. The independent test driver used the existing external lab configuration in memory; no config contents or credential values were emitted or copied. The instance identity was rechecked as MySQL Community `8.4.11`, database `sakila`, with `@@performance_schema=1`.

**Consumer and Instrument baseline.** Before the test, the exact raw `performance_schema.setup_consumers.ENABLED` value for `events_waits_current` was `NO`. The read-only Toolkit preflight showed `instrumented_waits=false` and selected the reviewed `active_requests_no_waits` fallback. The independent test driver had an applicable Performance Schema setup-table UPDATE privilege. The relevant existing lock Instruments `wait/lock/metadata/sql/mdl` and `wait/lock/table/sql/handler` were already `ENABLED=YES, TIMED=YES`; no Instrument was changed. Other wait Instrument groups were observed as `wait/io`: 52 enabled/7 disabled, `wait/lock`: 2 enabled, `wait/synch`: 342 disabled. For this path the reviewed active-request Registry query left-joins `events_waits_current`, while the blocking graph comes from the separate `data_lock_waits` Collector; the required minimal setting change was therefore the single Consumer, not a broad Instrument enablement. MySQL 8.4 documentation confirms setup-consumer changes take effect immediately and `ENABLED` is mutable ([setup_consumers](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-setup-consumers-table.html)); current wait events are exposed per thread through `events_waits_current` ([wait event tables](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-wait-tables.html)).

The driver temporarily changed only `events_waits_current` from `NO` to `YES`, verified the live value, and Toolkit preflight then reported `instrumented_waits=true`. It did not alter `setup_instruments`, server startup settings, global Toolkit configuration, grants, schema objects, or sample data. After sampling, the driver restored `events_waits_current=NO`; read-only post-check confirmed the exact original value. Wait Instrument group counts and the selected lock Instrument states remained as before. No temporary account/object or persistent server configuration was created.

**MySQL 8.4 — positive Independent Slow SQL VERIFIED.** A single independent test-driver session ran one compute-only aggregate over existing Sakila rows with the SQL statement's `MAX_EXECUTION_TIME(85000)` cap; it performed no business-data write, DDL, or DML. While the same request was observed active at 60 seconds, its native identity was `PROCESSLIST_ID=40` and Performance Schema `THREAD_ID=82`. Toolkit used the separate monitor alias and the existing `mysql84_slow` SQL and Permission Profiles; permission readiness was `READY`. Active-request and blocking collectors succeeded without truncation, `data_visibility=FULL`, full-profile evidence was `COMPLETE`, and triage status was `COMPLETE`. The matching assessment was `INDEPENDENT_SLOW_SQL / CONFIRMED`, rule `TRIAGE-INDEPENDENT-SLOW-01`, with matching PROCESSLIST_ID/THREAD_ID evidence and a sampled time of `2026-10-21T13:48:30.132866Z`. `root_cause_status` remained `NOT_ESTABLISHED`.

The final human CLI and JSON assessment both contained the same confirmed label and rule; the paired offline HTML contained them as well and had no external `http(s)` resources. The report is `reports/mysql84_slow_independent_20261021_214829.html`. Toolkit executed only its reviewed read-only Registry path: no Toolkit database writes, grants, KILL, or captured business SQL. The separate test driver changed only the temporary Performance Schema Consumer and executed the bounded SELECT. It closed the workload connection; post-check found zero remaining sessions for the workload `PROCESSLIST_ID`. The Consumer was restored before task closeout.

**Four-database evidence review.** This closeout adds no new SQL Server, PostgreSQL, or Oracle run. The existing §13.4 current-working-tree evidence remains the source for SQL Server 2022 final CLI/JSON/HTML parity and multi-label A→B→C plus Independent sample; PostgreSQL 16 `INDEPENDENT_SLOW_SQL / CONFIRMED` with complete blocking evidence; and Oracle Free 26ai root/victim/intermediate evidence with exact temporary lock-view grants, followed by revocation and the original permission state restored. These are scoped lab/version cells only; no legacy-version matrix is implied. Real `UNKNOWN` remains evidenced by the SQL Server empty baseline; real `PARTIAL` remains evidenced by MySQL after Consumer restoration and Oracle after grant revocation.

**Layered exception for anomalous evidence.** Per the user's explicit approval in this closeout request, malformed Collector evidence, identity conflict, synthetic truncation, and forced Collector failure are accepted through the existing Collector-boundary automated tests rather than requiring additional live database damage/injection. This is an acceptance-method adjustment, not a claim that those malformed states were reproduced against live databases. Real DB evidence still covers current positive classification, empty-baseline UNKNOWN, configuration/permission-derived PARTIAL, and CLI/JSON/HTML parity. The automated tests and this real E2E record are kept as distinct evidence classes.

**Automation and static gates.** Current working tree full suite: `.venv\Scripts\python.exe -m pytest -q` — **772 passed, 2 skipped**. `.venv\Scripts\ruff.exe check .` — passed. `.venv\Scripts\python.exe -m mypy dba` — passed (60 source files). `git diff --check` — passed; Git emitted only its existing LF-to-CRLF normalization warnings. These automated results do not substitute for the live MySQL sample above. No business source or tests were changed during this final acceptance turn.

**Current conclusion.** MySQL positive independence now has real evidence without forcing `PARTIAL` to `COMPLETE`; the server's exact original Consumer value and wait Instrument state were restored/retained, and no test session remained. Together with the scoped four-database evidence and automated UNKNOWN/PARTIAL boundary coverage, the task's approved acceptance scope is met. `V091-P1-TRIAGE-001` is updated to `VERIFIED` in the Backlog. This does not claim all diagnostic capabilities are complete across all six target databases or all target versions. No Phase 2 work, commit, or push occurred.

## 14. Phase 2 Top SQL architecture audit — V091-P2-TOPSQL-DESIGN-001 (2026-10-21)

**Scope/evidence.** Current source HEAD `469083a3cf0680ad24aaad7a496740722e5b45a`; package version `0.9.0`; working tree was already dirty. Read-only inspection of existing SQL Registry files, profiles, permissions, collectors, presenters, and relevant tests; no database was accessed and no tests or source code were run/changed for this design-only task. The detailed design and primary vendor references are in [performance triage and SQL analysis design §15](../design/performance-triage-and-sql-analysis-v0.9.1.md#15-phase-2-top-sql-design-audit-and-implementation-split-2026-10-21).

| Engine | Current-HEAD audit result | Target/implementation/evidence boundary |
|---|---|---|
| SQL Server 2008 R2+ | Current `dm_exec_requests` exposes request-scoped values. `plan_cached.sql` contains cached-stat fields for a single session/handle in the Plan feature; no ranked `dm_exec_query_stats` Top SQL Collector/Profile exists. | Source-adjacent fields are `IMPLEMENTED` only for their present request/plan scope. Generic Top SQL is not implemented; no cross-version Top SQL E2E exists. Query Store is optional and not a requirement. |
| PostgreSQL 14+ | `pg_stat_activity` current request and `pg_stat_database` database-wide statistics are present; no `pg_stat_statements` Registry/Profile/Collector exists. | SQL-level Top SQL is not implemented. Extension is optional/preconfigured only; PG16+ `pg_stat_io` is not SQL-level attribution. No SQL-level E2E exists. |
| MySQL 5.7, 8.0+ | Current statement and blocker-directed recent history paths exist; no `events_statements_summary_by_digest` Top SQL path. | Recent SQL is not digest ranking. No SQL-level CPU or defensible SQL-level physical-read Top SQL is established by current code; no digest Top SQL E2E exists. |
| Oracle 11gR2+ | Current active request can carry associated V$SQL cursor cumulative fields; no SQL_ID/child Top SQL Collector/Profile exists. | Existing fields are not an independent ranked list; no all-version or RAC Top SQL E2E exists. No AWR/ASH/paid-pack dependency is allowed by this design. |

**Designed contract and implementation gates.** Future rows carry engine/version/Profile, database-native SQL and plan identity, metric name/value/unit, `CURRENT`/`CUMULATIVE`/`INTERVAL_DELTA`/`HISTORICAL` semantics, observation/window times, collection scope, source, permission and visibility status, plus reset/eviction limitations. Current snapshots, cache/digest/cursor counters, interval deltas, and genuinely retained history remain distinct. Counts without a measured interval do not establish current frequency. Interval deltas require two explicit bounded samples, exact identity/epoch matching, and safe handling of reset, restart, cache/child/digest churn; they remain a later opt-in task with no persistence or daemon. Missing metrics remain unavailable, not zero. Each engine task must update Registry, Profile, Permission Registry, Preflight, tests and shared output together.

**Acceptance plan (not test evidence).** `V091-P2-TOPSQL-CONTRACT-001` will first prove the shared typed contract and quality-state serialization with fixtures only. Each separately selected engine task then needs version-route and permission tests, unit/visibility tests, bounded current-HEAD lab E2E, exact metric units/semantics and CLI/JSON/offline-HTML parity. The interval-delta task additionally needs matching-counter, reset/eviction/identity-change, sampling-budget and low-impact lab cases. No criterion is satisfied by this design document itself.

**Status cross-check.** `V091-P1-TRIAGE-001` remains `VERIFIED` per the last Acceptance conclusion in §13.5 and canonical Backlog; this audit does not reopen it. The design task is recorded `VERIFIED` in Backlog for the documentation/static-audit scope only. No Phase 2 implementation started. Existing user changes and untracked files were preserved. No commit or push was created.

## 15. Shared Top SQL contract — V091-P2-TOPSQL-CONTRACT-001 (2026-10-21)

**Scope and baseline.** Implemented on working-tree base HEAD `469083a3cf0680ad24aaad7a496740722e5b45a`; package version remains `0.9.0`. The worktree already contained user changes before this task; those were preserved. This task adds only the collector-neutral domain contract, shared presentation hooks, tests and this evidence. No database was connected, no database SQL/Collector/Adapter/Profile/Inventory was changed, and no engine-specific Top SQL capability or live E2E is claimed.

**Contract.** `dba/top_sql.py` defines timezone-aware collection time, engine/version/Profile, metric and ranking metadata, unit, `CURRENT`/`CUMULATIVE`/`INTERVAL_DELTA`/`HISTORICAL` semantics, scope, source, permission, data visibility, completeness, collection status, explicit limitations, and ranked items. A measured zero is a valid finite observation; an unavailable metric uses `null` plus a reason/status and cannot be ranked. A successful complete empty collection is distinguishable from unconfigured, unsupported, denied, failed, or unavailable collection. Completeness and representativeness/visibility are independent fields. Rows must follow the declared direction; comparisons require matching database, engine/version/Profile, metric, direction, unit, semantics, scope and evidence source.

**Native identity and safety.** Identity kinds explicitly separate SQL statement, plan and active request. Allow-listed native fields retain SQL Server handles/hashes/offsets/database ID; PostgreSQL queryid/dbid/userid separately from PID/backend_start; MySQL DIGEST/SCHEMA_NAME separately from PROCESSLIST_ID/THREAD_ID; and Oracle SQL_ID/CHILD_NUMBER/INST_ID separately from plan hash and session identity. No missing native identifier is synthesized. The contract has no SQL-text, credential, host, or connection-string field. Human renderers only emit allow-listed identity values, a bounded number of rows, metric values and quality metadata; they never render SQL text. HTML remains the existing escaped/offline shared renderer. Reports without `details.top_sql` are unchanged; an empty or unavailable result never produces a fabricated ranking.

**Automated evidence.** `.venv\Scripts\python.exe -m pytest tests/test_top_sql_contract.py tests/test_html_reports.py tests/test_presentation.py -q` — **46 passed**. Full suite `.venv\Scripts\python.exe -m pytest -q` — **798 passed, 2 skipped**. `.venv\Scripts\ruff.exe check .` passed. `.venv\Scripts\mypy.exe dba` passed (61 source files). `git diff --check` passed (Git reported only existing LF-to-CRLF normalization warnings). Tests verify native identity separation, Oracle child/instance data, SQL Server offsets, zero versus null, all four semantics, metric name/unit preservation without conversion, ordering and comparability guards, explicit reset/eviction/truncation limitations, JSON and human CLI mapping, offline HTML parity/redaction and unchanged legacy Slow report without a Top SQL section.

**Conclusion and boundary.** `V091-P2-TOPSQL-CONTRACT-001` is `VERIFIED` in the Backlog for shared contract and fixture/presentation scope only. No real Top SQL source, four-database ranking, Collector acceptance, or cross-engine ranking exists yet. The first engine remains undecided; the user must select one existing engine task before any Collector implementation starts. No commit or push was created.

## 17. SQL Server cache-resident Top SQL — V091-P2-TOPSQL-SQLSERVER-001 (2026-10-21)

### Current single-collection acceptance standard (2026-10-24; offline implementation)

This approved method supersedes the historical before/Toolkit/after DMV comparison procedure for the next live run. The helper performs exactly one Top SQL CLI collection for each of the seven required metrics. The JSON result and its corresponding saved offline HTML report come from that invocation; the human CLI Formatter is run in-process against the exact same `top_sql` object. A separate local read-only identity/permission query confirms the approved SQL Server instance and required permission; it is not a Top SQL collection. DMV snapshots, dynamic interval/rank-swap classification, the extra CPU Top-3 collection, and the temporary targeted-sampling mode have been removed.

Live checks retain SQL Server 2022 identity/version and Profile, permission readiness, complete collection/visibility, metric name/unit/cumulative semantics, at-most-10 rows, contiguous ranks, descending values and precision, SQL statement plus plan identity completeness, same-result JSON/HTML/CLI parity, non-sysadmin status, and the no-write/no-captured-SQL safety declaration. HTML and human CLI are display projections; full native SQL/plan identity remains in JSON and is checked by fixed expected-field mapping tests. This offline change does not close the task.

Independent deterministic regressions use fixed, independently checked SQL Server values and expected native fields. They cover each metric's source field/unit, Registry-side `TOP (?)` and deterministic tie ordering, complete statement identity and plan generation (including two plans for one statement), invalid collector ordering, and known average operands: `1494425 / 41 -> 36449.390243 -> 36449.3902` and `1528406 / 42 -> 36390.619047 -> 36390.6190`. These tests check six-place SQL division and four-place ranking separately; they do not use live Collector output as their oracle. Negative checks retain wrong rank, identity, plan, value, unit, completeness, permission, sysadmin and write-path failures.

Historical E2E JSON files are unchanged. Earlier reports' complete after-snapshot matches for logical reads, physical reads, execution count and elapsed time are historical auxiliary observations only, with their original sampling-window limitations; they do not decide PASS under the current standard. No live E2E was run after this change. `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING`, limited to SQL Server 2022 lab evidence; compatibility E2E for the broader SQL Server target range is not implied.

**Offline verification (2026-10-24; HEAD `469083a`).** The focused acceptance and SQL Server collector suite passed **71 tests**; full pytest passed **870 tests, 2 skipped**. Ruff passed. MyPy passed for `dba` (64 files), the helper/acceptance tests (2 files), and the SQL Server collector tests with imported legacy test helpers excluded (1 file). `git diff --check` passed with only the existing LF-to-CRLF normalization notices. The full suite ran without the shared pytest cache and used a unique task-local basetemp after the existing `.tmp/pytest` ACL prevented cleanup of that shared path. No database, credential lookup, SQL load, or Docker operation was performed. Software version remains `0.9.0`; task remains `E2E_PENDING` for user review and the revised live run.

**Diagnosis-subcommand option placement correction (2026-10-22).** Static comparison with the user's successful `dba.exe diagnose ... top-sql ... --json` found `--credential-backend os` before `diagnose`, although `dba.cli` registers it on the `diagnose` subparser. A local parser-only `--help` check confirmed the misplaced form exits 2 and the corrected form exits 0. The helper now places the option after `diagnose <database_id> top-sql`; `--json` remains after metric/Top-N. It uses the same `.venv\\Scripts\\dba.exe`, `cwd=ROOT`, 90-second timeout, captured text output and default `env=None`, inheriting the invoking Windows user's environment/token. Explicit OS backend selection avoids encrypted-store fallback. Regression coverage asserts argument order and subprocess settings. No DB was accessed; user-reported valid JSON smoke is consistent with this correction but does not complete live raw-DMV/metric/output-parity checks. Task remains `E2E_PENDING`.

**Scope and baseline.** Implemented against current working-tree base HEAD `469083a3cf0680ad24aaad7a496740722e5b45a`; package version remains `0.9.0`. The repository had pre-existing dirty files and an untracked user manual; those were preserved. The Backlog task is `E2E_PENDING`. SQL Server 2022 lab E2E is not claimed.

**Implementation.** Added reviewed SQL Server Registry query `sql/sqlserver/top_sql.sql` / YAML metadata. It server-ranks bounded `TOP (?)` rows from `sys.dm_exec_query_stats`, correlating `sys.dm_exec_sql_text` for database context. A row remains a statement/compiled-plan observation, not a query-hash aggregate: native `sql_handle`, `plan_handle`, `query_hash`, `query_plan_hash`, statement start/end offsets, `database_id` when available, and `plan_generation_num` are retained. No plan XML or SQL statement text is returned. The query has a 15-second Registry timeout and 101-row fetch cap; CLI Top N is bound, deterministic and limited to 1–100. No captured SQL is executed and the plan cache is not modified or cleared.

The implemented rankings are all `CUMULATIVE`: worker CPU time (microseconds; native accuracy is millisecond precision), logical reads (pages), physical reads (pages, explicitly not OS/device I/O), execution count (executions, not QPS), elapsed time (microseconds; millisecond precision), average CPU and average elapsed (microseconds/execution, computed from cumulative totals divided by execution count). Query text is deliberately omitted from CLI/JSON/HTML rather than copied into a diagnostic artifact. Results carry counter-epoch-unknown, cache-eviction, recompile-reset and metric-specific precision/device-I/O limitations. Empty successful cache reads are complete empty results, not fabricated zero-ranked entries; text or native-identity loss and fetch truncation lower completeness.

**Version/Profile/Permission evidence.** Profile/Registry validation accepts a shared query route for SQL Server 2008 R2, 2012, 2014, 2016, 2017, 2019 and 2022 without later-only DMV columns. The legacy permission profile requires `VIEW SERVER STATE` through SQL Server 2019; SQL Server 2022 uses `VIEW SERVER PERFORMANCE STATE`. SQL Server 2025 is explicitly `UNKNOWN`, has no executable Registry route and is not represented as implemented or verified. Static route validation is not live old-version compatibility evidence. Diagnosis requires a verified declared/detected version and permission preflight; missing permissions produce a manual-only reference grant, never an executed GRANT. No Query Store or additional permission is required.

**Product/output path.** CLI: `dba diagnose <database_id> top-sql --metric cpu_time --top 10`; supported metric arguments are `cpu_time`, `logical_reads`, `physical_reads`, `execution_count`, `elapsed_time`, `average_cpu_time` and `average_elapsed_time`. Default human CLI and offline HTML show at most ten identities, with omitted-row count; JSON includes the requested Top N. All outputs identify metric, unit, cumulative semantics, source, profile, permission/data visibility, collection time and limitations. SQL text is absent from all outputs. The shared offline HTML renderer is reused.

**Automated evidence.** `.venv\Scripts\python.exe -m pytest -q` — **811 passed, 2 skipped** after the final timeout-degradation test and Adapter compatibility fix. `.venv\Scripts\ruff.exe check .` — passed. `.venv\Scripts\mypy.exe dba` — passed (64 source files). `.venv\Scripts\python.exe -m dba validate` — passed (4 Inventory entries, 104 Registry SQL scripts). `git diff --check` — passed; Git emitted only its existing LF-to-CRLF normalization warnings. New focused tests cover SQL Server 2008 R2 through 2022 static route declarations, 2025 UNKNOWN, version permission differences, all metric units/semantics and averages, statement offsets and plan generation identity, server-bound Top N/metric parameters, bounded fetch/timeout, empty cache, missing text/identity, truncation, and CLI/JSON/offline-HTML result parity. The Top SQL Incident Summary remains `UNKNOWN / NOT_ASSESSED`; a cumulative ranking does not claim current database health. These fixtures are automated evidence only.

**Real E2E gate.** Read-only environment checks found Docker CLI could not access `npipe:////./pipe/dockerDesktopLinuxEngine` (permission denied). `Test-NetConnection 127.0.0.1 -Port 1433` returned `False`; `Get-NetTCPConnection -LocalPort 1433 -State Listen` returned no listener. The existing Inventory target is the approved loopback SQL Server 2022 asset, but its live identity/version could not be reconfirmed. No Credential Manager query, database connection, test SQL/workload, or database write occurred. The real E2E matrix therefore remains `PENDING`, not failed and not verified. Resume after the approved SQL Server 2022 lab listener is available: re-check identity/version and monitor permission Profile, seed multiple bounded statements with distinct execution/resource totals using the separate test driver, compare cache DMV ranks against those observations, run Toolkit read-only collection, inspect CLI/JSON/HTML parity, then confirm driver sessions/resources are clear. No legacy-version E2E is inferred.

**Conclusion.** `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING` in the canonical Backlog. The next engine Collector is not started; `V091-P2-TOPSQL-POSTGRESQL-001` remains `BACKLOG`. No software version change, commit, or push occurred.

**Lab availability recheck (2026-10-21).** At HEAD `469083a3cf0680ad24aaad7a496740722e5b45a`, `Test-NetConnection 127.0.0.1 -Port 1433` returned `TcpTestSucceeded=False`, and `Get-NetTCPConnection -LocalPort 1433 -State Listen` returned no listener. Docker CLI availability was not treated as a database-availability test. Since the approved endpoint was not listening, no credential lookup, SQL Server identity/version query, Toolkit diagnostic, test-driver workload, database write, or output comparison was attempted; this is an environment block, not an E2E failure. The task remains `E2E_PENDING`.

**Static scope note (not live evidence).** The reviewed Registry SQL reads `sys.dm_exec_query_stats` without a database filter, so its ranking population is the instance plan cache. `st.dbid` is retained per returned statement as its database context when available. The current result contract's `metric_scope=SQL_STATEMENT` describes item granularity; it does not encode the collection population as `INSTANCE` or `DATABASE`. Static review did not find an explicit `DATABASE` scope claim, but live CLI/JSON/HTML scope presentation remains unverified and must be checked after the lab returns. No code was changed while the endpoint was unavailable.

**Scope contract correction and automated evidence (2026-10-21).** Added `CollectionScope` and the additive `TopSQLResult.collection_scope` field. Existing constructor calls remain compatible and default to `UNKNOWN`; SQL Server collector results—including unavailable/error results—declare `collection_scope=INSTANCE`, while retaining `metric_scope=SQL_STATEMENT`. Native per-row `database_id` remains only the SQL statement's database context and does not imply a database-filtered ranking. Collection scope participates in result comparability. Human CLI, JSON, and offline HTML expose the collection scope. Regression coverage verifies instance population versus statement granularity, retained row database context, collection-scope comparability, output parity, and legacy reports without the new field.

**Automated verification.** Focused command `.venv\Scripts\python.exe -m pytest tests/test_top_sql_contract.py tests/test_top_sql_sqlserver.py tests/test_presentation.py tests/test_html_reports.py -q` — **61 passed**. Full `.venv\Scripts\python.exe -m pytest -q` — **813 passed, 2 skipped**. `.venv\Scripts\ruff.exe check .` passed. `.venv\Scripts\python.exe -m mypy dba` passed (64 source files). `git diff --check` passed with only existing LF-to-CRLF normalization notices. These are automated/contract results, not database E2E.

**Remaining real E2E.** Per the user's instruction, this turn did not repeat Docker/port checks or connect to a database. No actual SQL Server version, monitor permission readiness, cache contents, metric ranks/units/averages, Top N behavior, native identity association, CLI/JSON/HTML live parity, or database write/cleanup status was observed. `V091-P2-TOPSQL-SQLSERVER-001` therefore remains `E2E_PENDING`; resume live validation only when the user reports the existing lab is reachable. No next-engine Collector or comprehensive-diagnosis integration was started. The user decided that all four engine collectors must be completed before a separate integration task is defined and approved.

**Live E2E resume attempt (2026-10-21).** The user reported the SQL Server Docker lab running. Existing Inventory identifies target `sqlserver2022` on the authorized loopback endpoint, database `master`, declared family `2022`, using alias `sqlserver-test-monitor`. Before connecting, Toolkit credential checks reported Windows Credential Manager `UNAVAILABLE`; direct OS-keyring retrieval for the alias failed, while the default command path selected the locked Encrypted Local Store and required interactive unlock. No unlock, password input, or credential change was attempted. This is a credential-access blocker in the current execution context, not evidence that the lab is down. No database connection was made; actual identity/version, profile/preflight, metrics, ranks, native handles, collection scope in live output, and CLI/JSON/HTML live parity remain unverified. No test workload, Toolkit query, database write, session, transaction, object, or configuration change occurred; cleanup is not applicable. The task stays `E2E_PENDING`.

**Automated gates on the current dirty working tree.** Focused tests `.venv\Scripts\python.exe -m pytest tests/test_top_sql_contract.py tests/test_top_sql_sqlserver.py tests/test_presentation.py tests/test_html_reports.py -q` — **61 passed**. Full suite `.venv\Scripts\python.exe -m pytest -q` — **813 passed, 2 skipped**. Ruff and MyPy passed. `git diff --check` passed with existing line-ending notices. One initial concurrent pair of pytest runs raced while cleaning the shared `.tmp/pytest` tree and produced setup/test errors; focused and full suites were then rerun serially and passed. No product code changed in this E2E resume attempt.

**Final acceptance resume (2026-10-22; HEAD `469083a3cf0680ad24aaad7a496740722e5b45a`).** The user reports a successful command from their normal Windows PowerShell: `dba.exe diagnose sqlserver2022 top-sql --metric cpu_time --top 10`. The user-observed output identifies SQL Server 2022 `16.0.4305.5`, Profile `sqlserver_top_sql_shared`, `COLLECTED`, Permission `READY`, Visibility/Completeness `COMPLETE`, collection population `INSTANCE`, item granularity `SQL_STATEMENT`, and descending cumulative CPU Top 10. This confirms one user-run CPU smoke path and live scope/status display, but it is not independently captured or compared against DMV rows in this session.

**Current execution-context result.** Retrying through the local `.venv\Scripts\dba.exe` with JSON and a temporary HTML report returned `CredentialUnavailable: Interactive terminal required to unlock credential store` before connecting. No database identity query, metric collection, raw DMV query, or write was made by this Codex process; Encrypted Local Store was not unlocked. This is a process-context credential limitation, not a database availability finding. No workload or database object was created, and there is no cleanup work.

**Prepared live-verification helper.** Added `scripts/acceptance/verify_sqlserver_top_sql.py` for the user to run in the normal interactive PowerShell context where the existing alias works. The helper strictly limits itself to Inventory target `sqlserver2022` at `127.0.0.1:1433` / `master`, requires live SQL Server 2022 identity, uses the existing OS credential alias without fallback to the encrypted store, and connects with read-only/autocommit options. Its bounded independent reference query reads cache DMV statistics only; it generates no load, DDL, DML, business SQL, or persistent files, and deletes only its own temporary HTML directory on exit. The sanitized JSON output compares CPU, logical reads, physical reads, execution count, cumulative elapsed and both average metrics against adjacent raw DMV snapshots; checks Top 10/Top 3 ordering, statement/plan identity and generation, scope, permissions, CLI/JSON/HTML metric parity, and reports whether any non-zero physical-read sample existed. The helper contains no password and suppresses raw driver exception text. It was syntax-checked and linted, but has not yet been executed against the database.

**Automated gates.** Focused `.venv\Scripts\python.exe -m pytest tests\test_top_sql_contract.py tests\test_top_sql_sqlserver.py -q` — **41 passed**. Full suite using a unique per-run pytest temp root to avoid the shared `.tmp\pytest` cleanup race — **813 passed, 2 skipped**. `.venv\Scripts\mypy.exe dba` — passed (64 source files). The first full-suite attempt overlapped a pytest temp cleanup and produced one fixture setup error; it is not counted. A subsequent isolated-temp serial full run passed. `.venv\Scripts\ruff.exe check .` and helper compilation passed; `git diff --check` passed with only existing LF-to-CRLF notices. These automated checks do not replace the pending live metric-to-DMV E2E.

**Identity and limitations to resolve from helper output.** The same visible `sql_handle`/statement offsets/`query_hash` appearing in different ranked rows must remain separate if `plan_handle` or plan generation differs; do not merge or file a product bug without matching live raw DMV identities. The shared CLI and HTML table emphasize SQL statement identity and metric value, while JSON carries the separate native plan identity; review rank/value parity across all three and plan identity in the structured result. CPU/elapsed DMV time has millisecond source resolution despite microsecond units; logical/physical reads are page counters, and physical reads are not device I/O. Cache counters are cumulative only for a plan's current cache lifetime and can reset on recompile, eviction, restart or cache removal; no cache flush is permitted. Zero physical reads is a valid observation and should remain a limitation/sample note, not a failure or fabricated positive result.

**Conclusion.** `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING`; current-session evidence does not yet prove metric values/ranks against raw DMV rows, Top N live behavior, native plan distinctions, or HTML parity. User-reported CPU smoke is preserved without overstating it as full E2E. Next action is for the user to run `scripts/acceptance/verify_sqlserver_top_sql.py` in normal PowerShell and return its sanitized JSON. The Backlog records human CLI readability and bounded/redacted SQL summary/detail viewing as requirements for the future comprehensive-diagnosis integration task, which remains deferred until all four engine collectors are VERIFIED; no separate Task ID is created now. No PostgreSQL Collector, comprehensive-diagnosis integration, commit or push was started.

**Acceptance-helper AttributeError hardening (2026-10-22).** The user's `{"result":"BLOCKED","error_type":"AttributeError"}` output did not include a traceback or source location, so the original runtime line cannot be truthfully identified from that output alone. Static interface review confirmed `Inventory.get`, `KeyringCredentialStore.get`, `SQLServerSettings.load`, and the CLI's `top_sql` JSON envelope are present in current source. It also identified an unchecked JSON-shape access in `_run_toolkit`: a valid non-object JSON response could reach `.get()` before type validation and raise `AttributeError`. The helper now validates the CLI envelope/result/item/identity/profile shapes before field access, and its failure JSON reports an allow-listed stage, script basename, script line, exception class and fixed summary; raw exception text, DSN, credentials and SQL are never emitted. Credential lookup remains a direct OS-store alias read with no encrypted-store fallback. This is helper-only hardening and does not modify the Collector or normal `dba ... top-sql` path. Credential/result-shape/exception-path Mock regressions: 10 passed; Ruff passed; MyPy passed for the acceptance test and 64 `dba` source files; `git diff --check` passed with existing line-ending notices. No database connection was attempted and no live E2E evidence was added. The user should rerun the helper and provide its sanitized JSON if it remains blocked; the Top SQL task status is still `E2E_PENDING`.

**Raw DMV helper timeout defect (2026-10-22).** The user's sanitized report identifies `stage=raw_dmv`, line 200. At that line `_raw_rows()` assigned `cursor.timeout = 10`. The connection is the native object returned by `pyodbc.connect()` (`pyodbc.Connection`); its `.cursor()` returns a native `pyodbc.Cursor`. Local pyodbc 5.3.0 API introspection (no DB connection) confirmed `Connection.timeout` exists while `Cursor.timeout` does not; Cursor `.execute`, `.description`, and `.fetchall` do exist. The project's `SQLServerAdapter` also sets `self._connection.timeout` before creating the cursor. The exact cause is therefore an invalid Cursor attribute assignment, not a dict/tuple conversion problem. The helper now sets `connection.timeout = 10` before creating the cursor. It still runs the same raw DMV query, reads `description` column tuples and `fetchall()` row tuples, and maps those tuples to dictionaries for rank/value/identity comparisons. A strict-slot Mock regression models the pyodbc object shape and verifies timeout placement, bounded Top N parameter, original DMV query, tuple mapping, and cursor closure. Targeted tests: 25 passed; Ruff, MyPy, and `git diff --check` passed (existing line-ending warnings only). No database was accessed or modified; this does not establish live E2E. Task remains `E2E_PENDING` pending user rerun.

**Toolkit JSON failure localization (2026-10-22).** The user's later report identifies `stage=toolkit_json`, line 305, which maps to the `RuntimeError` raised when `json.loads(completed.stdout)` fails. Thus the confirmed immediate condition is non-parseable CLI stdout (the caught cause is `JSONDecodeError` or `UnicodeError`); the old report cannot establish whether stdout was empty, contained non-JSON text, or had an encoding issue. It does not implicate DMV value drift because no DMV comparison has run at that point. The helper now distinguishes nonzero child exit, empty stdout, JSON syntax/encoding failure, invalid JSON envelope/result/profile, and a Toolkit-reported failure using fixed `check_id` and `failure_reason` values; JSON syntax errors may include numeric line/column only. It never includes stdout/stderr text or exception messages. Once output parses, per-metric `failure_checks` now separately classify Collection, Permission, Visibility, completeness, Top-N limit, order, identity, average formula, CLI/HTML parity, and raw DMV comparison. Each raw comparison is bracketed by timestamps and reports whether the top-N cache identities/counters changed between snapshots. A changed snapshot is labeled `DMV_STATS_CHANGED_DURING_WINDOW` only when neither before nor after sample matches; it remains a failed comparison, with no percentage tolerance or forced pass. The Top 3 count remains exact; Top 10 is an upper limit because a valid cache may contain fewer rows. New Mock coverage exercises the reported JSON parse path, exit/contract classifications, sanitized location, quality failures, and changing DMV identities/counters. Targeted tests: 30 passed; Ruff, MyPy, and `git diff --check` passed. No database connection or Toolkit product code change occurred. Whether this Windows CLI subprocess actually emitted empty output, console text, or invalid JSON is still unconfirmed until the user reruns the helper; no Toolkit product Bug is established. The task remains `E2E_PENDING`.

**PARTIAL result targeted static analysis and helper correction (2026-10-22; no database access).** The user supplied aggregate counts only (7 `CLI_HTML_PARITY`, 4 `RAW_DMV_COMPARISON`, 2 `AVERAGE_FORMULA`, 1 `HUMAN_CLI_PARITY`); the per-row sanitized output was not available, so the historical raw/average failures cannot be conclusively adjudicated from those counts alone. Static source review establishes that each metric's JSON and HTML are emitted by the same `dba diagnose ... top-sql --json --report ...` subprocess invocation and therefore share one Toolkit report snapshot. The helper's HTML parser had expected English headers while the current offline renderer localizes the table headers (`排序`, `SQL 原生身份`, `指标值`); its seven HTML parity failures are explained by this helper parser mismatch, not evidence of seven Collector data divergences. The parser now accepts both localized and English headers and compares rank, native SQL identity, and numeric value exactly by decimal equivalence (format scale such as `100` versus `100.0000` is not a tolerance).

The separate human CLI command is a distinct invocation and therefore a distinct cache snapshot. The helper now brackets that invocation with raw DMV samples; exact output parity is distinguished from a snapshot-consistent value change (`INCONCLUSIVE`) and a real representation/identity mismatch (`MISMATCH`). The raw DMV reference is also bracketed around each JSON/HTML invocation and compares complete statement/compiled-plan identity (`sql_handle`, offsets, database context, query/plan hashes, `plan_handle`, generation) plus visible counters. Stable differing results remain failures; if identities/counters/top-N membership changed and neither adjacent snapshot matches, the outcome is `INCONCLUSIVE`, with entering/leaving top-N/cache membership explicitly ambiguous. No arbitrary percentage tolerance is used.

Average checks now join observations by complete native statement/plan identity rather than rank position, validate cumulative total divided by `execution_count` using the returned SQL decimal scale and exact rounded value, and report `INCONCLUSIVE` only when no adjacent snapshot can establish the same identity while DMV data changed. The SQL Registry expression remains `CONVERT(decimal(38, 4), total) / NULLIF(execution_count, 0)`; no product formula defect was established from the aggregate failure count. The helper's previous rank-position matching and permissive numeric tolerance are removed, not used to force a pass.

The prior helper's permission observation used a different `HAS_PERMS_BY_NAME` scope from `sql/sqlserver/monitor_capabilities.sql` and collapsed SQL `NULL` to false. It now uses the same `HAS_PERMS_BY_NAME(NULL, NULL, ...)` probes and preserves `NULL` as unknown. For SQL Server 2022, `VIEW SERVER PERFORMANCE STATE` is the required Top SQL permission; `VIEW SERVER STATE` is not required by this feature. Thus `VIEW SERVER STATE=false` alone is compatible with `READY`, but a same-snapshot `VIEW SERVER PERFORMANCE STATE=false` conflicts with a `READY` profile and must remain a reported mismatch; no `sysadmin` grant or permission change is proposed. The supplied prior values are not a fresh probe and cannot resolve whether the old scope caused the reported discrepancy.

These are acceptance-helper corrections only; no Collector, Registry, Adapter, Permission Profile, or Toolkit Presenter was changed. Mock regressions: `.venv\Scripts\python.exe -m pytest tests/test_sqlserver_top_sql_acceptance.py -q` — 26 passed. Ruff passed for the helper/tests; MyPy passed for the acceptance tests and `dba` (64 files); `git diff --check` passed with existing line-ending normalization notices. An initial test collection caught an indentation error in the helper's average calculation; it was corrected before the passing run. No database connection, workload, Toolkit query, or permission operation was performed. The reported four raw comparisons and two average checks must be rerun by the user: the helper will report stable mismatches as failures and changing-cache comparisons as `INCONCLUSIVE`. `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING`; this static/Mock result is not real E2E.

**Follow-up to latest user-reported PARTIAL (2026-10-22; static review and Mock regressions only).** The user's latest JSON reports all seven `HTML_JSON_PARITY` checks failing after the localized-header support was added. The remaining common mismatch is the UI identity projection: the shared HTML Presenter emits only `sql_identity` (not `plan_identity`), redacts/bounds each native field to 100 characters, then bounds the whole cell; the helper had compared the full JSON SQL identity string. SQL Server's `sql_handle` can render to 130 characters, so that exact comparison can fail even when the displayed projection is correct. This is not evidence of a Presenter defect: HTML is a bounded DBA-facing view, while full SQL and compiled-plan identities are retained and validated in JSON. The helper now mirrors the Presenter projection for HTML parity, separately checks complete SQL/plan identity in JSON, and returns fixed reasons `PARSER_MISMATCH`, `ROW_NOT_FOUND`, `IDENTITY_MISMATCH`, or `VALUE_MISMATCH`; it also records the HTML identity display contract. Tests compare against the actual shared Top SQL section, including a long handle and distinct plan identity. No Presenter change was needed.

The counter diff now checks monotonicity only for cumulative totals (`total_cpu_time`, logical/physical reads, execution count, total elapsed time). A decrease in derived average fields is reported separately as `NORMAL_AVERAGE_CHANGE`; it cannot produce `COUNTER_DECREASED_OR_RESET`. A numeric regression mirrors the reported pattern: CPU/elapsed/count totals increase while average CPU and average elapsed decrease.

The two previous average `INCONCLUSIVE` results also had a precision-contract defect: JSON `metric_value` is the ranking value explicitly cast to `decimal(38,4)`, while the adjacent DMV's raw `average_*` expression has finer division precision (commonly six decimal places). The helper incorrectly compared those two distinct representations. It now compares JSON to DMV `ranking_value`, and independently recomputes raw average from cumulative total/execution count at the returned SQL decimal scale. This removes the false precision mismatch without a tolerance; formula disagreement remains `MISMATCH`, and no matching identity/snapshot remains `INCONCLUSIVE`. The Registry formula was inspected and no product formula bug is indicated by the current static evidence.

Human CLI remains a separate invocation: when JSON and human output each match their own adjacent DMV snapshot but the counter population changes between invocations, the helper reports both observations and keeps cross-time exact parity `INCONCLUSIVE`. It does not erase that state or turn it green. Targeted tests: **33 passed**; full pytest: **846 passed, 2 skipped**; Ruff passed for helper/tests; MyPy passed for helper tests and all 64 `dba` source files; `git diff --check` passed with existing line-ending notices. No database access, load, permission change, or Toolkit write occurred. Software remains `0.9.0`; `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING` until the user reruns the live helper and the real result supports closure.

**Follow-up to latest seven HTML and two average inconclusive checks (2026-10-22).** Further static comparison against `dba/reporting/presenters.py` confirmed the HTML table is a bounded presentation contract: it renders `sql_identity` only, applies redaction and a 100-character cap to each native value, then caps the full identity cell; it does not render `plan_identity`. SQL Server handles can be 130 characters. The prior helper's full-string comparison therefore falsely marked a valid shortened display as `IDENTITY_MISMATCH`; it now mirrors the exact display projection. JSON is separately checked for full SQL and plan identities; no claim is made that HTML exposes plan identity. HTML parity now reports `PARSER_MISMATCH`, `ROW_NOT_FOUND`, `IDENTITY_MISMATCH`, or `VALUE_MISMATCH`, and persists a display-contract label. A full Presenter-to-helper regression verifies long-handle truncation and plan identity separation. No product Presenter change was justified.

Average checking now distinguishes the raw SQL division result from the ranked result: Registry ranking wraps the division in `CONVERT(decimal(38,4), ...)`, while the raw average retains finer scale. JSON must match raw `ranking_value`; the independent formula check compares cumulative total / execution count with the unrounded DMV average at that DMV value's exact scale. Formula mismatch remains a failure; snapshot/value drift remains `INCONCLUSIVE`, with fixed reason and before/after statuses. A regression with total CPU `1,000,000`, count `3`, raw average `333333.333333`, and ranked value `333333.3333` verifies precision separation without tolerance. The diff routine now treats only cumulative totals/counts as monotonic counters and labels changing derived averages `NORMAL_AVERAGE_CHANGE`; a test mirrors increasing CPU, elapsed, reads/counts alongside declining averages.

The helper/Mock changes and tests passed: focused **33 passed**, full **846 passed, 2 skipped**, Ruff `check .`, MyPy for the acceptance tests and all 64 source files, and `git diff --check` (line-ending notices only). Human CLI remains a different sampling instant; matching each output to its bracketed DMV sample does not establish cross-time exact equality and remains `INCONCLUSIVE` when counters change. No database connection, workload, write, permission change, or captured SQL execution occurred. This is not live E2E; task status remains `E2E_PENDING`.

**Latest user-reported `PARTIAL` follow-up (2026-10-22; static analysis and helper regression only).** The latest run reports seven `HTML_JSON_PARITY / IDENTITY_MISMATCH`, one `average_elapsed_time / RAW_DMV_AVERAGE_FORMULA_MISMATCH`, and one `HUMAN_CLI_PARITY / CLI_OUTPUT_PARITY_MISMATCH`. This disproves the earlier working hypothesis that the remaining seven HTML failures were explained by comparing full JSON identity to the intended bounded display: the prior run's result did not include the actual sanitized cells, and the current repository contains no retained report artifact. The helper deletes its temporary HTML on exit. Therefore the first differing HTML field and actual average operands cannot be reconstructed from the aggregate result; no Presenter, SQL arithmetic, or CLI product defect is established yet.

The helper now computes the expected HTML row by invoking the actual shared `_top_sql_section` Presenter, then compares the parsed DOM cell and numeric value exactly. On an identity mismatch it emits only a fixed classification, first differing field name, field-name lists, and cell lengths—never identity values. The human CLI mismatch similarly gets a fixed first-difference classification (row shape/count, rank, identity field, or numeric value); its full stdout remains suppressed. Average mismatch output now records the matched full SQL/plan identity field set and numeric-only operands (raw cumulative elapsed, execution count, raw DMV average, Toolkit rank value, and DMV ranking value) for both adjacent snapshots. This can distinguish a formula issue from representation or result mismatch on the next local run without exposing SQL or credentials.

The Registry and raw-DMV reference both use `CONVERT(decimal(38,4), total_elapsed_time) / NULLIF(execution_count, 0)`; ranking is separately converted to `decimal(38,4)`. SQL Server decimal division precision rules yield a `decimal(38,6)` raw average for this bigint denominator when the integral-result precision exceeds 32 digits; the ranked output remains four decimal places. The helper now checks six-place `ROUND_HALF_UP` for the raw formula and exact numeric equality against the four-place ranking value; no tolerance was added. This type derivation follows [Microsoft's precision/scale rules](https://learn.microsoft.com/en-us/sql/t-sql/data-types/precision-scale-and-length-transact-sql?view=sql-server-ver15). Whether the supplied real mismatch is caused by operands, a type difference, or an incorrect expression remains unproven because those per-row numbers were not retained in the provided result.

Targeted acceptance-helper regressions: **35 passed**; full pytest: **848 passed, 2 skipped**; `ruff check .` passed; MyPy passed for the acceptance tests and all 65 discovered source files; `git diff --check` passed (Git emitted only existing LF-to-CRLF normalization notices). No database connection, workload, permissions, Collector, Registry, CLI Formatter, Presenter, or product code was changed. Task remains `E2E_PENDING`; rerun the acceptance command in the user's PowerShell context and return its sanitized JSON, which now includes the precise diagnostics.

**Identity parity root cause confirmed from the user's next result and fixed offline (2026-10-22).** The user reports seven HTML `IDENTITY_MISMATCH` results with `expected_field_names=[]`, expected-cell length 20, and actual-cell lengths 195/217; the CLI expected identity is also length 20 while the actual is 185. Source tracing proves `_json_rows()` extracted `sql_identity.fields` and `plan_identity.fields` into convenience maps (`fields`, `plan_fields`) but discarded the original contract objects. The parity code then passed this flattened row to `_top_sql_section()` and `_expected_native_identity()`, both of which read the nested `sql_identity` member. With it missing they correctly emitted the 20-character fallback `identity unavailable`; the actual HTML/CLI contained the native identity. This is a shared acceptance-helper object-shape defect, not evidence of a Presenter/CLI product defect. The fix retains both original identity objects alongside the flattened DMV-comparison maps. Identity-diff parsing now excludes placeholder text from field-name extraction, and the CLI result keeps the outer `IDENTITY_MISMATCH` category separate from the more specific nested difference classification.

An offline fixed-JSON regression reproduced the exact 20-character fallback and empty expected field list with a long SQL Server handle, then verified the corrected parser against the actual `_top_sql_section` projection and `render_top_sql_human()` output, including full plan identity retention in the structured row. Focused acceptance tests: **37 passed**; full pytest: **850 passed, 2 skipped**; Ruff passed; MyPy passed for 65 source files; `git diff --check` passed with existing line-ending notices. No live database run was made. This resolves the script defect in the reported seven HTML checks and the shared CLI expected-identity mismatch at code level; the live result is not reclassified from the existing run. The remaining average-elapsed failure, the additional failure(s) within the reported total of 10, and the one inconclusive result still require their sanitized check details for precise disposition; no average rule or acceptance threshold was weakened. `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING`.

**Average formula precision correction (2026-10-22; offline fixed operands only).** The user supplied exact SQL Server 2022 values, which resolve the earlier open average-formula question without another database run: for `total_cpu_time=1,494,425` and `execution_count=41`, exact division is `36,449 + 16/41 = 36,449.390243902…`; the DMV's raw `decimal(38,6)` expression returns `36,449.390243`, while the separately cast `decimal(38,4)` ranking is `36,449.3902`. For `total_elapsed_time=1,528,406` and `execution_count=42`, exact division is `36,390 + 26/42 = 36,390.619047619…`; the raw DMV result is `36,390.619047`, and the ranking value is `36,390.6190`. The numerator is `decimal(38,4)` and the denominator `bigint` (`decimal(19,0)`); the division precision formula initially yields precision 58, scale 24, and SQL Server's maximum-precision reduction produces a `decimal(38,6)` result when the integral part requires more than 32 digits. The observed raw values are truncated at six places, not rounded half-up. A subsequent conversion to `decimal(38,4)` is a distinct operation; its result is validated separately against the observed ranking value. Microsoft's documentation describes the precision/scale derivation and scale reduction ([precision, scale, and length](https://learn.microsoft.com/en-us/sql/t-sql/data-types/precision-scale-and-length-transact-sql?view=sql-server-ver15)) and default rounding when converting to a lower decimal scale ([decimal and numeric](https://learn.microsoft.com/en-us/sql/t-sql/data-types/decimal-and-numeric-transact-sql?view=sql-server-ver17)); the specific division truncation above is established by the user's supplied SQL Server outputs. The helper's former `ROUND_HALF_UP` raw-division expectation was wrong; it is replaced with exact six-place truncation and a separate four-place cast check. Fixed offline regressions cover both operand sets, reject six-place rounded-up raw values, and independently reject incorrect ranking values. This corrects the prior paragraph's raw-average `ROUND_HALF_UP` expectation; it does not alter the Registry expression, Collector, database, or live E2E status. Current task remains `E2E_PENDING`; no database connection or workload was run in this follow-up.

**Offline verification for the average correction.** `tests/test_sqlserver_top_sql_acceptance.py`: **38 passed**. Full pytest: **851 passed, 2 skipped**. `ruff check .`: passed. `mypy dba`: passed (64 source files); explicit MyPy check for `scripts/acceptance/verify_sqlserver_top_sql.py` and its focused tests: passed (2 files). `git diff --check`: passed; only existing LF-to-CRLF normalization notices were printed. No SQL Server connection or test workload was run. The user need not resubmit any prior evidence; live E2E remains `E2E_PENDING` until a future run of the corrected helper supplies new results.

**Superseded offline parity investigation (2026-10-22–23).** An earlier real run reported seven HTML/JSON matches, seven average-formula matches, six raw-DMV metric matches, a CPU raw-DMV inconclusive result, and one human-CLI numeric mismatch. Subsequent offline analysis found the saved report did not contain paired human-CLI rows sufficient to establish whether that mismatch was due to cache movement; it did not establish a product formatter bug. A separate identity investigation likewise could not reconstruct the reported 184/186-character difference or prove the hypothesized rank swap from the saved summary. Fixed same-result formatter tests passed, but those tests did not reclassify either historical live report.

**Historical E2E sampling evidence (2026-10-23; source report unchanged).** The report recorded `COLLECTION_VALIDATION=PASS`, `OUTPUT_PARITY=PASS`, `PERMISSION_AND_SAFETY=PASS`, zero failures, and `RAW_DMV_VALIDATION=INCONCLUSIVE` with six unresolved checks. Complete after-snapshot matches were recorded for logical reads, physical reads, execution count, and elapsed time. Average CPU and average elapsed raw formula checks matched 10/10 rows at each recorded endpoint, while complete ranked-list validation remained inconclusive. These are historical auxiliary observations only; they do not establish the current run's correctness or change the original report result. The available report did not establish a SQL Server restart or cache-reset cause, and it has not been modified.

The earlier cross-invocation identity/rank-swap analysis and temporary multi-snapshot sampling procedure are historical and superseded. Their implementation and temporary entry point have been removed. Use only the current single-collection standard recorded at the start of this section. No live run was made during this offline cleanup; `V091-P2-TOPSQL-SQLSERVER-001` remains `E2E_PENDING`. After review, the single live evidence step is `.\.venv\Scripts\python.exe scripts\acceptance\verify_sqlserver_top_sql.py` in the authorized SQL Server 2022 PowerShell environment.

### Final SQL Server 2022 E2E closeout (2026-10-24)

The final available report is the local workspace artifact `top_sql_e2e_simplified.json` (not added to Git); it records `result=VERIFIED`, scope `SQLSERVER_2022_TOP_SQL`, software `0.9.0`, and SQL Server `16.0.4305.5`. It contains all seven requested metrics: `cpu_time`, `logical_reads`, `physical_reads`, `execution_count`, `elapsed_time`, `average_cpu_time`, and `average_elapsed_time`. Each metric is `COLLECTED` with Permission `READY`, Visibility and Completeness `COMPLETE`, 10 rows, valid Top N/order, complete native identity, and CLI/HTML/output parity `MATCH`. The three acceptance layers `COLLECTION_VALIDATION`, `OUTPUT_PARITY`, and `PERMISSION_AND_SAFETY` are `PASS`; there are zero failure checks. The report records no Toolkit write and no captured business SQL execution. The approved acceptance basis is one Collector collection per metric, with corresponding output formats evaluated from that result; cross-time DMV snapshot equality is not a required pass condition.

The user confirms the full test suite at `889 passed, 2 skipped, 0 failed`; this documentation update did not rerun tests or E2E. The user-provided confirmation and available final report support `VERIFIED` only for the recorded SQL Server 2022 `16.0.4305.5` scope. SQL Server 2008 R2 remains syntax-design/automated-compatibility coverage only, without real E2E. SQL Server 2025 and other untested versions are not inferred as verified. Backlog is the sole task-status source. Earlier failure, partial, inconclusive and pending notes above remain historical records and are not rewritten.

## 18. SQL Server live Active SQL Top N — V091-P2-ACTIVE-SQL-SQLSERVER-001 (2026-10-24)

This is a distinct current-request snapshot feature, not the cache-resident cumulative Top SQL collector in §17, and not a replacement for the original Slow/Blocking route. Software remains `0.9.0`. The command is `dba diagnose <database_id> active-sql --sort cpu|logical-reads|elapsed --top 10`; CPU is the default and Top N is bounded to 1–100. A single reviewed, read-only Registry statement derives the three SQL Server request rankings from `sys.dm_exec_requests`, excludes the Toolkit's own request, applies stable identity tie-breakers, unions and deduplicates by `(session_id, request_id, start_time)`, and leaves room for at most 300 candidates with `max_rows=301` so exactly 300 rows are not treated as truncated. Text is extracted at the current statement offsets and capped in SQL at 2,000 characters, with unavailable/truncated states and native SQL/plan identity retained. SQL text is not written to ordinary runtime logs.

The report keeps three separate rankings (CPU, logical reads, elapsed) and one deduplicated SQL Text collection; request rows reference SQL Refs, and HTML refs link to the corresponding text section. JSON carries structured request evidence without repeating text across rankings. Human CLI emphasizes the selected sort. The shared ReportModel, presenters, HTML renderer, anchor escaping, and bounded report path are reused. SQL Server permission routing is version-aware: earlier families use the existing server-state permission route and SQL Server 2022+ uses the performance-state route. Top N is parameter-bound through a strict integer allowlist, not SQL interpolation.

Automated evidence: `tests/test_active_sql_sqlserver.py` — 19 passed; targeted compatibility regression group (Active SQL, existing Plan, Permissions, Replication, cumulative Top SQL, SQL Server, and CLI output) — 221 passed. `ruff check .` passed and `mypy dba` passed for 68 files. The final full suite reported **888 passed, 2 skipped, 1 failed**. The one failure is `tests/test_sqlserver_top_sql_acceptance.py::test_acceptance_runs_exactly_one_toolkit_collection_per_metric`, whose fixed Top SQL fixture returned `CONTRACT_RANK_OR_VALUE_INVALID` instead of the expected `VERIFIED`. This is in the separate cache-resident cumulative Top SQL acceptance path, not the Active SQL scenario; it remains unresolved and the full suite is not described as passing.

No database was connected, no workload/DDL/DML or permission change was run, and no real output was recorded. SQL uses CTEs, `ROW_NUMBER`, `UNION`, `OUTER APPLY`, `SUBSTRING`, and ODBC `?` parameters compatible with the SQL Server 2008 R2-era syntax family; this is static/automated compatibility review only and does not verify any live version. SQL Server 2008 R2 and newer live behavior, real permission readiness, DMV field availability, actual SQL/Plan metadata visibility, runtime query cost, and current-HEAD CLI/JSON/HTML parity remain unverified. `V091-P2-ACTIVE-SQL-SQLSERVER-001` remains `E2E_PENDING` pending code review and a separately authorized lab E2E. The task state is maintained only in Backlog; no cumulative Top SQL state is changed.

**User-confirmed SQL Server 2022 E2E closeout (confirmation received 2026-10-24; execution timestamp not separately supplied).** The tested instance was SQL Server 2022 `16.0.4305.5`. The user confirms three independent sessions produced correct rankings for current-request CPU, logical reads, and elapsed time; SQL Ref to SQL Text associations were correct; JSON and HTML data matched; and SQL Text was stored once rather than repeated in ranking lists. Active SQL focused tests were `19 passed`; the user reports the full suite as `889 passed, 2 skipped, 0 failed`. No tests or database runs were repeated for this documentation-only closeout.

Scope remains exact: this result verifies the reported SQL Server 2022 `16.0.4305.5` scenarios only. SQL Server 2008 R2 has syntax-design compatibility review but no real E2E. Statement text longer than 2,000 characters is covered by offline tests but was not specifically verified in the live environment. The confirmation names JSON/HTML parity; it does not provide separate live human-CLI parity evidence. Package version remains `0.9.0`. Backlog records `V091-P2-ACTIVE-SQL-SQLSERVER-001` as `VERIFIED`; the separate cumulative cache-resident `V091-P2-TOPSQL-SQLSERVER-001` is now `VERIFIED` for the SQL Server 2022 scope recorded in §17.

## 19. PostgreSQL cumulative Top SQL — V091-P2-TOPSQL-POSTGRESQL-001 (2026-10-24)

**Implementation and automated evidence.** Software remains `0.9.0`. A PostgreSQL-specific collector now uses the optional, preconfigured `pg_stat_statements` source and the existing shared Top SQL contract, CLI entry point, permission/profile routing, and report presenters. Version/Profile and Registry routes are explicitly limited to PostgreSQL 14–18; PostgreSQL 13 is not included in this task's Top SQL route. The SQL is read-only, binds the metric and Top N, orders and limits on the server, returns at most 100 ranked rows with `max_rows=101`, and does not fetch query text or execute captured SQL. The statement identity retains `(dbid, userid, queryid, toplevel)`; the boolean is represented as integer `0/1` only to fit the existing scalar identity contract. Per-server statistics collection scope is `INSTANCE`, while each row's metric scope is `SQL_STATEMENT`.

The seven supported ranking metrics preserve their declared native meaning and unit: `total_exec_time` and `mean_exec_time` in milliseconds, `calls` in executions, `shared_blks_read`, `shared_blks_hit`, and `temp_blks_written` in blocks, and `wal_bytes` in bytes. `wal_bytes` retains exact integer precision through the adapter and collector. No SQL CPU metric is exposed; shared block reads are not described as physical device I/O; execution-time metrics exclude planning time. PostgreSQL documents the metric types/units, per-server statistics scope, and four-part statement key in the [`pg_stat_statements` documentation for PostgreSQL 14](https://www.postgresql.org/docs/14/pgstatstatements.html) and [PostgreSQL 18](https://www.postgresql.org/docs/18/pgstatstatements.html).

The source remains optional and is not a global PostgreSQL Adapter prerequisite. Read-only source discovery distinguishes extension/module/track absence (`SOURCE_NOT_CONFIGURED`), permissions (`PERMISSION_DENIED` or partial permission evidence), unavailable source, failed reads, and a configured but empty result. It does not run `CREATE EXTENSION`, alter `shared_preload_libraries` or other settings, restart PostgreSQL, or reset statistics. Only the fixed `public` extension schema is queried; a non-default schema is reported unavailable without constructing SQL from a server-sourced identifier. `stats_reset` and `dealloc` are retained where available, with explicit reset/eviction limitations; `queryid` is not claimed stable across major versions.

Automated evidence on the current workspace: focused PostgreSQL/Top SQL and compatibility group — **201 passed**; full pytest — **927 passed, 2 skipped**; `ruff check .` passed; `mypy dba` passed for 68 source files and the focused PostgreSQL Top SQL test file passed a separate MyPy check. `git diff --check` is recorded with the final task closeout. The first full-suite attempt exposed legacy shared-contract fixtures missing `toplevel` and a system-temp-directory permission failure; fixtures were updated to the new identity contract and the final full suite used an isolated temporary directory inside the repository. No database connection, workload, permission change, extension installation, or database configuration change was performed.

**Implementation-stage E2E status (before the later closeout below).** At this point the implementation and automated compatibility coverage existed, but no database had been verified. The planned PostgreSQL 16 Docker E2E was to check actual server version, extension readiness, Profile and `pg_read_all_stats` permission preflight, metrics, native identities, ranking/Top N, source degradation, and CLI/JSON/offline-HTML parity. This is a dated implementation-stage record; the later PG16.15 evidence and current scoped status are recorded below.

### PostgreSQL 16.15 real E2E closeout (user-provided evidence)

The user reports PostgreSQL `16.15` with `pg_stat_statements` `1.10`. `total_exec_time` was collected through the real CLI and returned 9 rows in descending order. For `wal_bytes`, JSON and HTML from the same collection matched across Top 10 rank, metric value, and native identity: rank 1 was `299083` bytes and ranks 2–10 were `0`. Both collections reported `COLLECTED`, `Completeness=COMPLETE`, `Permission=READY`, and `Visibility=COMPLETE`. The `wal_bytes` result had `dealloc=0` and `limitations=[]`, confirming the prior `CACHE_EVICTED` false positive is absent in this run. This evidence does not establish JSON/HTML parity for `total_exec_time` or live results for the other five metrics.

The task status in Backlog is `VERIFIED` for the reported PostgreSQL 16.15 scenarios above. This is not blanket verification of PostgreSQL 14–18: PG14, PG15, PG17, and PG18 have code compatibility coverage only, without real E2E. `mean_exec_time`, `calls`, `shared_blks_read`, `shared_blks_hit`, and `temp_blks_written` were not individually live-tested. The exact E2E HEAD and execution timestamp were not included in the supplied evidence. Software version remains `0.9.0`.

Offline test runs are separate: implementation-stage full pytest reported **927 passed, 2 skipped**; after the subsequent `dealloc` fix, the PostgreSQL Top SQL focused suite reported **41 passed**. These counts are not a single combined run. No tests or database E2E were run during this documentation closeout.

## 20. MySQL cumulative Top SQL implementation — V091-P2-TOPSQL-MYSQL-001 (2026-10-24)

The current dirty worktree adds a MySQL-specific cumulative digest collector and reuses the shared Top SQL result, CLI, Permission/Profile routing, JSON, human, and offline HTML contracts. The reviewed source is `performance_schema.events_statements_summary_by_digest`; row identity is `(SCHEMA_NAME, DIGEST)`. A NULL schema with a non-NULL digest is preserved as a valid identity; the special `DIGEST IS NULL` aggregate bucket is excluded from ordinary rankings and marks source coverage/identity partial. The collector does not fetch digest SQL text.

The SQL is common across the declared MySQL 5.7, 8.0, and 8.4 routes and uses legacy-compatible SELECT, CASE, ORDER BY, and LIMIT syntax; both metric and Top N are bound through a dedicated allowlisted Adapter method. Server-side ordering uses native metric counters and deterministic schema/digest tie-breaks; Top N is 1–100 and Registry `max_rows=101`. The Profile resolver has separate MySQL 8.0 pre-8.0.22 and 8.0.22+ route variants, but the query deliberately references only fields common to 5.7/8.0/8.4. No `SUM_CPU_TIME`, memory field, recent-statement identity, or Active SQL behavior is included.

Seven cumulative metrics are implemented: `sum_timer_wait` (`SUM_TIMER_WAIT`, milliseconds), `avg_timer_wait` (`AVG_TIMER_WAIT`, milliseconds/execution), `count_star` (`COUNT_STAR`, executions), `sum_rows_examined` and `sum_rows_sent` (rows), `sum_created_tmp_disk_tables` (tables), and `sum_no_index_used` (executions). Timer counters are converted from native picoseconds to milliseconds for display while the SQL ranks by the raw native counter; `SUM_TIMER_WAIT` is not labeled CPU time. MySQL documents the digest summary fields and `(SCHEMA_NAME, DIGEST)` key in its [Performance Schema statement summary tables reference](https://dev.mysql.com/doc/mysql-perfschema-excerpt/5.7/en/performance-schema-statement-summary-tables.html), and the timer normalization in [Performance Schema timing](https://dev.mysql.com/doc/refman/8.2/en/performance-schema-timing.html).

Read-only source probes check `@@performance_schema`, the `statements_digest` consumer, statement instrument enabled/timed coverage, summary-table availability, `Performance_schema_digest_lost`, and presence of the null-digest catch-all. Disabled/unavailable source is not reported as a complete empty ranking. Digest loss and null catch-all evidence mark coverage incomplete; neither is mislabeled as plan-cache eviction. The source notes the unknown counter epoch rather than inventing a reset timestamp. The digest-loss status variable indicates statements that could not be instrumented because the digest table was full; see the [MySQL status variable reference](https://dev.mysql.com/doc/mysql-perfschema-excerpt/5.7/en/performance-schema-status-variables.html). The Toolkit does not enable consumers/instruments, change configuration, grant permissions, or execute captured statements.

Offline verification at HEAD `469083a3cf0680ad24aaad7a496740722e5b45a` (worktree already dirty): MySQL/Top SQL focused compatibility group — **210 passed**; full pytest rerun with the temporary directory pointed inside the existing `.venv` — **966 passed, 2 skipped**; `ruff check .` passed; `mypy dba` passed for 68 files; `git diff --check` passed before this documentation addition and is rerun after it. The first full-suite attempt hit a Windows sandbox `PermissionError` while a pre-existing SQL Server acceptance test wrote its temporary HTML into the restricted OS temp directory; the in-repository-temp rerun passed. No MySQL connection, Docker command, database workload, or permission/configuration change was performed for this task.

This is implementation and automated compatibility evidence only. Real MySQL 8.4 Docker E2E remains pending: all seven live metric rankings, source/instrument visibility, permission preflight, and live CLI/JSON/HTML parity have not yet been verified. Automated route coverage for MySQL 5.7/8.0/8.4 is not live E2E for those versions. Package version remains `0.9.0`; canonical task state is maintained in Backlog.

### MySQL 8.4.11 real E2E closeout (2026-10-24; user-provided evidence)

The implementation-stage pending statement immediately above records the state before the user's live run. The later evidence closes `V091-P2-TOPSQL-MYSQL-001` as `VERIFIED` in Backlog for the MySQL 8.4.11 Docker environment and the scenarios listed here; it does not imply fully complete digest-source coverage or verification of every metric/version.

`sum_timer_wait` was collected successfully (CLI exit code 0), returned 10 rows in descending order, displayed milliseconds, and had matching JSON and HTML generated from the same collection. `count_star` was collected successfully (CLI exit code 0), returned 10 rows in descending order with executions as the unit, and rendered correctly in HTML; a JSON artifact was not independently checked for this metric. A valid row with `SCHEMA_NAME=NULL` and a non-NULL digest was retained. The source probe observed `digest_lost=0` and no NULL-DIGEST aggregate bucket. Of 214 statement instruments, 15 had both `ENABLED=NO` and `TIMED=NO`; consequently, source completeness remained `PARTIAL`, with `COUNTER_EPOCH_UNKNOWN` and `SOURCE_COVERAGE_INCOMPLETE`. These limitations describe observed instrumentation coverage and an unknown cumulative-statistics epoch, not a collector failure or proof that all historical statements are represented.

The other five metrics were not individually verified against a live MySQL instance. MySQL 5.7 and 8.0 have offline compatibility coverage only, not real E2E. The earlier implementation-stage automated evidence remains separately attributed to its prior runs: focused Top SQL tests **210 passed**; full suite **966 passed, 2 skipped**; Ruff and MyPy passed. Those results were not rerun as part of this documentation closeout. No new database access or test run was performed for this closeout; software version remains `0.9.0`.

## 21. Oracle cumulative Top SQL implementation — V091-P2-TOPSQL-ORACLE-001 (2026-10-24)

The Oracle implementation reads cumulative counters from the currently visible `V$SQL` child-cursor rows. It does not aggregate by `SQL_ID` or `PLAN_HASH_VALUE`: statement identity retains `SQL_ID`, `CHILD_NUMBER`, and the connected instance's `INST_ID`; on 12c and newer it also retains the current container's `CON_ID`. A positive `PLAN_HASH_VALUE` is attached only as auxiliary plan identity, so different children remain distinct even when they share a plan hash. A null or zero plan hash yields no plan identity. `CHILD_NUMBER` is a current cursor identity, not a durable identifier across cursor invalidation, reload, or aging.

The 11gR2 Registry route uses reviewed `V$SQL` columns, bind parameters and the ordered inline view followed by `ROWNUM`; it contains neither `CON_ID` nor `FETCH FIRST`. The 12c+ route reads the current `CON_ID`/`CON_NAME` from `SYS_CONTEXT` and binds that container ID into the `V$SQL` filter. It retains native `CON_ID=0` when returned and permits an unavailable container name to remain null; it does not substitute another container identity. It uses local `V$SQL`, not `GV$SQL`, and therefore reports only the connected instance, not an RAC-wide merge. The current container is exposed in source metadata and child identities. Top N is bound and limited to 1–100; Registry fetch allowance is 101 rows. No user value is interpolated into SQL.

Implemented metrics are `cpu_time` and cumulative `elapsed_time` (microseconds), `buffer_gets` (gets), `disk_reads` (reads), and `executions` (executions). Results use `CUMULATIVE` semantics and `CHILD_CURSOR` metric scope. Elapsed time is explicitly marked as cumulative and not a single-execution wall-clock duration. Counter epoch remains unknown because child-cursor statistics begin with cursor loading and can be reset by invalidation/reload or disappear when cursors age out; no `CACHE_EVICTED` claim is made without direct eviction evidence. No SQL text is captured. The permission profile requires the existing direct common-monitor object grants including `SELECT SYS.V_$SQL`; no AWR, ASH, ADDM, Tuning Pack, role escalation, or privilege change is introduced.

Oracle documents `V$SQL` as one row per child cursor; its CPU and elapsed counters are in microseconds, and elapsed time is database time (including parse/execute/fetch), not wall-clock duration. The 12c+ `SYS_CONTEXT('USERENV', ...)` route uses the documented current container identity. See the [Oracle V$SQL reference](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/V-SQL.html) and [Oracle CDB administration reference](https://docs.oracle.com/database/121/ADMIN/cdb_admin.htm).

Offline verification at HEAD `469083a3cf0680ad24aaad7a496740722e5b45a` (the worktree remains dirty): Oracle/Top SQL-focused compatibility group — **281 passed**; Ruff — passed; MyPy — passed for 68 source files. The first full pytest run had one Windows sandbox `PermissionError` when an existing acceptance test wrote into the OS temp directory; rerunning with `TEMP`/`TMP` directed to `.venv/test-tmp` passed — **994 passed, 2 skipped**. These are deterministic/offline tests, not database evidence. No Oracle database, Docker, workload, grants, or database objects were accessed or changed. Software version remains `0.9.0`.

Oracle Free 26ai real E2E remains pending. No live Oracle version has been verified by this implementation turn; automated route coverage for 11gR2, 12c, 18c, 19c, 21c, 23ai, and 26ai is not live compatibility evidence. The canonical task state is maintained in Backlog.

### Oracle Top SQL HTML identity rendering follow-up (2026-10-24)

The user reports that the Oracle Free 26ai E2E JSON contains SQL_ID, CHILD_NUMBER, INST_ID, CON_ID, and valid PLAN_HASH_VALUE values for some rows; the reported gap was the HTML projection. The shared Top SQL HTML presenter now includes the existing Oracle CON_ID from each statement identity. It reads PLAN_HASH_VALUE only from that item's Oracle PLAN identity, and displays the value only when it is a positive integer; missing or zero values display —. The 11gR2 statement identity has no CON_ID, and the renderer does not synthesize one. This uses the already collected JSON result and adds no database query. SQL Server, PostgreSQL, and MySQL Top SQL render paths remain unchanged.

Offline regression evidence: the focused four-engine Top SQL rendering group passed **153 tests**; Ruff passed; mypy dba passed for 68 source files; git diff --check reported no whitespace errors (only existing Windows line-ending notices). No database or Docker was accessed. The user requested that the Oracle Free 26ai HTML route remain pending; this follow-up does not independently verify the reported JSON/live HTML parity, so V091-P2-TOPSQL-ORACLE-001 remains E2E_PENDING.

### Oracle Free 26ai E2E closeout (2026-10-24; user-provided evidence)

The user reports a real Oracle Free 26ai E2E against service FREEPDB1, with INST_ID=1 and CON_ID=3. The cpu_time metric passed. The buffer_gets metric passed with CLI exit code 0, unit gets, CHILD_CURSOR metric scope, and a correctly descending Top 10. JSON and HTML were produced from the same collection: all 10 ranks, metric values, SQL_ID, CHILD_NUMBER, INST_ID, and CON_ID values matched. Seven rows had valid PLAN_HASH_VALUE values and all seven matched; the remaining three had no valid plan hash and HTML displayed an em dash rather than inventing plan identity.

Both reported collections were COLLECTED with COMPLETE completeness, READY permission status, and COMPLETE data visibility. COUNTER_EPOCH_UNKNOWN remains the known cursor-statistics epoch limitation; it does not indicate collection failure. This closes V091-P2-TOPSQL-ORACLE-001 as VERIFIED only for the reported Oracle Free 26ai FREEPDB1 scenarios and the cpu_time and buffer_gets metrics. No real E2E is claimed for other Oracle versions or the other three Oracle metrics.

The prior four-engine Top SQL focused regression result of 153 passed, Ruff, and MyPy passed is historical evidence from the HTML renderer follow-up and was not rerun for this closeout. No tests, database, or Docker were run in this documentation-only closeout. Software version remains 0.9.0.

## 22. Oracle cached execution plan MVP — PLAN-PC-001 (2026-10-25)

### Scope and implementation evidence

The current uncommitted worktree implements Oracle `diagnose <database_id> plan` for a caller-selected current shared-pool child cursor. The source is limited to reviewed `V$SQL` and `V$SQL_PLAN` Registry queries; it does not execute captured/business SQL, use `EXPLAIN PLAN`, collect runtime row-source statistics, inspect `V$SQL_SHARED_CURSOR`, read optimizer object statistics, or access AWR/ASH/ADDM/SQL Monitoring.

The routed reviewed scripts are `sql/oracle/oracle_plan_identity_11g.sql`, `oracle_plan_rows_11g.sql`, `oracle_plan_context_modern.sql`, `oracle_plan_identity_modern.sql`, and `oracle_plan_rows_modern.sql`, each paired with READ_ONLY, reviewed metadata. Identity and row scripts use binds and bounded result counts. The identity query requires an exact `SQL_ID` and `CHILD_NUMBER` on the current connection instance; the plan query rechecks the immediately observed `ADDRESS`, `HASH_VALUE`, `CHILD_ADDRESS`, `CHILD_NUMBER`, `PLAN_HASH_VALUE`, and, on 12c+, `CON_ID`. It does not search another child or plan hash. The extra 501st row detects plans larger than the 500-operator maximum; oversized plans are unavailable and no partial tree is presented.

11gR2 SQL does not reference `CON_ID`; current instance identity comes from the existing Oracle preflight. 12c+ first reads the current session instance/container using a reviewed Registry query, checks an optional caller-supplied instance/container, and then binds the resulting current `CON_ID` into both cursor queries. The routes cover declared families 11gR2, 12c, 18c, 19c, 21c, 23ai, and 26ai. Compatibility is source/route validated only; no old-version database was contacted.

`dba/oracle.py` provides validated identity value objects and dedicated bind-only methods for the exact reviewed plan scripts. `dba/oracle_plan.py` validates current cursor identity, detects conflicts and missing cache evidence, returns structured statuses, preserves the operator parent/child IDs, and leaves non-contiguous plan node IDs intact. `dba/oracle_plan_profiles.py`, `dba/versioning.py`, and `dba/permissions.py` define routes and require existing Oracle monitor capabilities plus direct `SELECT SYS.V_$SQL_PLAN`; permissions output remains reference-only. The CLI supports `--sql-id`, `--child-number`, optional `--inst-id`, `--con-id`, and `--plan-hash`, while retaining SQL Server `--session`. JSON uses the report's structured data and the shared HTML renderer shows the Oracle plan tree and identity separately from SQL Server Showplan XML.

The field contract was cross-checked against Oracle's [11g Release 2 `V$SQL_PLAN` reference](https://docs.oracle.com/cd/E18283_01/server.112/e17110/dynviews_3054.htm) and [21c `V$SQL_PLAN` reference](https://docs.oracle.com/en/database/oracle/oracle-database/21/refrn/V-SQL_PLAN.html). These document the cache-resident child-cursor plans, `CHILD_ADDRESS`, `ADDRESS`/`HASH_VALUE`/`CHILD_NUMBER` association, optimizer estimate meaning, 12c+ `CON_ID`, and additional adaptive-plan rows. This documentation review is not a live version compatibility test.

### Identity and interpretation limits

The report identifies the current cache source and reports cursor/plan mismatch, ambiguity, no cached cursor, no cached plan, timeout, permission failure, or operator-limit outcomes without falling back to another child. A supplied non-zero Plan Hash must equal the current `V$SQL.PLAN_HASH_VALUE`. A missing requested Plan Hash is not fabricated: the report can show a valid *current* plan hash, but explicitly states that historical cursor load generation is not captured and cannot prove that a historical Top SQL sample came from this same cursor generation. Current RAC instance only is supported; no `GV$` scan occurs.

`CARDINALITY`, `COST`, and `BYTES` are labeled optimizer estimates. No A-Rows, actual elapsed time, or estimate/actual comparison is produced. Plan node IDs and parent IDs are retained. Adaptive plan candidate/final branches are not classified, and the report says so. SQL text, `OTHER_XML`, outline data, and bind values are not collected.

### Offline verification

- Focused command: `python -m pytest tests/test_oracle_plan.py tests/test_execution_plan.py tests/test_oracle.py -q` — **87 passed**. A targeted combination with `tests/test_permissions.py` was **67 passed**.
- Full suite: `python -m pytest -q` — **1012 passed, 2 skipped**. The run used a temporary directory below the project `.tmp` because the default Windows sandbox Temp path denied a pre-existing acceptance test's temporary file write. No test was removed or weakened.
- Ruff: `ruff check dba tests/test_oracle_plan.py` — passed.
- Strict MyPy: `mypy dba` — passed, 70 source files.
- Registry/Profile validation: `dba validate --json` — passed, 119 SQL scripts, no warnings.
- `git diff --check` — passed with only Git line-ending normalization notices.

No Oracle connection, Docker command, permission change, test workload, commit, or push occurred. These results establish source and offline implementation only, not live Oracle execution. Oracle Free 26ai E2E, permission-denied behavior on a real monitor account, and actual compatibility on 11gR2/12c/18c/19c/21c/23ai remain unverified. PLAN-PC-001 therefore remains `E2E_PENDING` in Backlog. Software version remains 0.9.0.

### Oracle Free 26ai plan-root and incremental-grant correction (2026-10-25)

The user-reported 26ai three-row plan was rejected because `_validate_plan_rows()` required every `POSITION` to be an integer. Oracle's valid root `SELECT STATEMENT` has `PARENT_ID=NULL` and `POSITION=NULL`; null estimates were already preserved, and node uniqueness is based on `ID`, not `POSITION`. Validation now accepts a null `POSITION` only for a root at depth zero, continues to permit repeated positions across different parents, and rejects duplicate IDs, missing parents, self-parenting, and parent/child depth conflicts. Per-row SQL_ID, child, instance, container, cursor-match count, and plan-hash checks remain unchanged. HTML regression asserts all three operations and renders null estimates as an em dash.

The Oracle Plan profile already required direct `SELECT SYS.V_$SQL_PLAN`, and the Oracle runtime preflight correctly detected its absence. README's Oracle monitor setup text omitted the Plan view. It now distinguishes the common initialization grants from the additional Plan MVP grant. `render_profile_sql()` keeps its existing default of rendering the complete profile grant set; an optional validated subset supports incremental suggestions. The Plan CLI now supplies only `missing_required` when preflight identifies missing privileges, and a later Plan-view permission failure suggests only `SELECT SYS.V_$SQL_PLAN`. No new privilege, role, or automatic GRANT path was added. `docs/MONITOR_PERMISSIONS.md` documents the full-initialization versus missing-only behavior.

Offline verification for this correction: `tests/test_oracle_plan.py tests/test_permissions.py tests/test_oracle.py` — **100 passed**, including duplicate node, invalid parent/depth, and per-row cursor identity conflict cases; `ruff check .` passed; `mypy dba` passed (70 source files); `dba validate --json` passed (119 scripts, no warnings); `git diff --check` passed (only existing LF-to-CRLF notices). No Oracle connection or Docker command was run. The supplied 26ai output remains user-reported evidence; corrected parsing, rendered report, missing-only grants, real monitor-account permission behavior, and other Oracle version families require another real E2E. PLAN-PC-001 remains `E2E_PENDING`; software remains 0.9.0.

### Oracle Plan report closeout (2026-10-25)

The user reports positive Oracle Free 26ai E2E: the requested current cursor/Plan Hash matched, the three-node plan was available and complete, and JSON/HTML node counts and nodes matched. The prior “no Oracle E2E” note above reflects the earlier offline-only correction turn; this positive run is user-reported, not rerun in this turn. This turn's report regression confirms the Oracle-only plan tree displays Estimated Rows, Estimated Cost, and Estimated Bytes from existing report data, renders NULL estimates as `—`, and does not add Actual Rows or runtime statistics. The cost/row/bytes columns were already populated by the Oracle-specific presenter; the rows label was clarified and the rendered values are now asserted.

The shared Incident Summary had treated complete Plan-only evidence as an all-clear snapshot: generic complete-sample logic set `HEALTHY`, `NOT_DETECTED`, and an immediate action stating no active issue was detected. Oracle Plan is cache-only and makes no database-health assessment. Its summary now uses the existing `UNKNOWN` / `NOT_ASSESSED` semantics used for isolated SQL evidence and omits the all-clear immediate action. SQL Server current-request Plan and other diagnosis status paths are unchanged.

Offline verification: `tests/test_oracle_plan.py tests/test_incident.py tests/test_execution_plan.py` — **63 passed**; `ruff check .` passed; `mypy dba` passed (70 files); `git diff --check` passed with existing line-ending notices. No database or Docker was used. PLAN-PC-001 remains `E2E_PENDING` for the requested wrong-Plan-Hash reverse real E2E and other unverified version families; software remains 0.9.0.

### Final E2E acceptance and status (2026-10-25; user-provided evidence)

The user reports both positive and reverse real E2E on Oracle Free 26ai, service/PDB `FREEPDB1`, `INST_ID=1`, `CON_ID=3`, `SQL_ID=0sbbcuruzd66f`, `CHILD_NUMBER=2`:

- Positive target `PLAN_HASH_VALUE=2239883476`: `plan_available=true`, `plan_status=AVAILABLE`, `identity_validation=MATCHED_CURRENT_CURSOR_AND_PLAN_HASH`, `permission_readiness=READY`; all three real plan nodes are present, including root `POSITION=NULL`, and JSON and HTML agree.
- Reverse target deliberately requests `PLAN_HASH_VALUE=2239883477`. The current `V$SQL` identity read returns `PLAN_HASH_VALUE=2239883476` with `cursor_match_count=1`; Toolkit returns `PLAN_HASH_MISMATCH`, `plan_available=false`, `operator_count=0`, and `identity_validation=NOT_VERIFIED`. It does not read a plan for another child. JSON and HTML agree. Plan-only summary remains `UNKNOWN / NOT_ASSESSED` and does not claim database health.

Based on these supplied positive and negative E2E results, `PLAN-PC-001` is **VERIFIED only for the Oracle Free 26ai scope and identities stated above**. This does not verify other Oracle versions. Runtime row-source statistics, shared-failure analysis, and optimizer statistics remain outside the MVP.

Non-blocking display follow-up: in the wrong-hash case, raw collection has the observed current hash `2239883476`, but the report's `current_plan_hash_value` is `NULL`. Record this for a future report-presentation fix; it does not change the mismatch rejection or current acceptance. No code change was made for it.

This documentation-only closeout did not rerun tests, invoke the database, start Docker, change source, commit, or push. The earlier **63 passed**, Ruff and MyPy results above are historical and were not rerun. Software version remains `0.9.0`.

## 23. MySQL active-statement Plan feasibility gate — PLAN-PD-MYSQL-001 (2026-10-25)

### Verified local target and credential separation

The live checks ran from the user's Windows host and project `.venv`, not the Codex isolated loopback. Docker reported the existing `mysql84` container using image `mysql:8.4` and mapping local port 3306; a read-only Toolkit `test-connection mysql84 --json` succeeded against MySQL `8.4.11`. The Windows Credential Manager alias used by Inventory was readable, and Toolkit preflight identified the account as non-privileged. The simulator's `local_config.py` was parsed as a Python literal only and used solely by a separate PyMySQL test-driver connection; its credential was never passed to Toolkit, printed, or written. The tested statements were bounded `SELECT SLEEP(...)`; each completed naturally. No data, account, grants, server setting, or Performance Schema configuration was changed.

### Performance Schema event identity

The monitor account read `performance_schema.threads` joined to `performance_schema.events_statements_current`. Live rows supplied `PROCESSLIST_ID`, `THREAD_ID`, `EVENT_ID`, `END_EVENT_ID`, `EVENT_NAME`, SQL text, and digest. For the controlled active event, `END_EVENT_ID` was NULL both before and after EXPLAIN, and PROCESSLIST_ID, THREAD_ID, EVENT_ID, event name, SQL-text fingerprint, and digest matched. Thus the requested event fields and the same-event check are available on this 8.4.11 instance. This is a pair of observations, not an atomic association between EXPLAIN and the event; a statement can still change between the snapshots and the EXPLAIN request.

### EXPLAIN and permission result

Using two connections authenticated as the same dedicated monitor account, `EXPLAIN FORMAT=TRADITIONAL FOR CONNECTION <validated live connection id>` returned one plan row, and the surrounding event snapshots matched. This confirms that MySQL 8.4.11 accepts `FORMAT=TRADITIONAL` for the tested statement.

Using a separate local test-driver account for the same controlled `SELECT SLEEP(...)`, the monitor account could see and identify the live event. Both `EXPLAIN FOR CONNECTION <id>` and `EXPLAIN FORMAT=TRADITIONAL FOR CONNECTION <id>` failed with MySQL error 1045 and a generic access-denied message. A read-only inspection of the monitor account's current grants found PROCESS and global SELECT; its normal Toolkit preflight still classified it `NON_PRIVILEGED`. The failure did not identify a particular missing privilege or object, so no GRANT suggestion is asserted. The test did not exercise a table-specific SELECT or SHOW VIEW target. The same-account pass and cross-account denial make the requested cross-user capability unverified and prevent proceeding with a general active-session Plan MVP until a DBA resolves the authorization behavior.

The official [MySQL 8.4 EXPLAIN FOR CONNECTION documentation](https://dev.mysql.com/doc/refman/8.4/en/explain-for-connection.html) states that the statement applies to a currently executing explainable statement, requires PROCESS to name another user's connection, and also requires sufficient privileges to explain the target query; prepared statements are unsupported. The [events_statements_current reference](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-events-statements-current-table.html) defines `(THREAD_ID, EVENT_ID)` as the unique event key and says `END_EVENT_ID` is NULL while an event is active. These documented facts do not explain the observed 1045 under the grants present in this lab.

### Decision

The real feasibility gate is **PARTIAL / BLOCKED**: Performance Schema event capture, same-account EXPLAIN, event stability, and `FORMAT=TRADITIONAL` passed on MySQL 8.4.11; cross-user EXPLAIN required for general monitoring did not. The denial's root cause remains unknown. No source, SQL Registry, permission definition, or product behavior was changed; no grants were applied. No MySQL 5.7 or 8.0 compatibility was tested. No pytest, Ruff, MyPy, or Registry validation was run because implementation did not start. PLAN-PD-MYSQL-001 is `BLOCKED` in Backlog pending DBA investigation of the 1045 or separate user approval for a narrower same-account-only contract. Software version remains `0.9.0`.

### Exact MySQL 8.4.11 authorization root cause and feasibility decision (2026-10-25)

This source review supersedes the preceding statement that the 1045 root cause was unknown. It is pinned to the MySQL Server `mysql-8.4.11` source, not inferred from 8.0 or trunk. In [`Sql_cmd_explain_other_thread::execute()`](https://github.com/mysql/mysql-server/blob/mysql-8.4.11/sql/opt_explain.cc#L2255-L2321), the observer is restricted to its own effective username when its main security context does not pass `check_access(GLOBAL_ACLS & ~GRANT_ACL, db_name)`, or when the main and active security contexts differ in privileged user/host. For a different target username, the later check raises `ER_ACCESS_DENIED_ERROR` (1045). [`GLOBAL_ACLS`](https://github.com/mysql/mysql-server/blob/mysql-8.4.11/sql/auth/auth_acls.h#L904-L918) contains the static global privilege mask, including data-changing and administrative privileges such as INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, SHUTDOWN, FILE, and SUPER; [`check_access()`](https://github.com/mysql/mysql-server/blob/mysql-8.4.11/sql/auth/sql_security_ctx.cc#L2696-L2714), whose default `match_any` is false ([declaration](https://github.com/mysql/mysql-server/blob/mysql-8.4.11/sql/auth/sql_security_ctx.h#L228-L240)), requires the full requested mask. Thus `PROCESS` permits naming another connection in the documented interface but does not pass this separate cross-username visibility gate. This explains why the observed monitor grants (`PROCESS` and global `SELECT`) were insufficient for the local cross-username EXPLAIN. This is a source-based explanation of the observed case, not a claim about MySQL 5.7/8.0.

The read-only local account review confirmed that the Toolkit monitor connection authenticated successfully as its monitor principal, had no active role, and had the observed `PROCESS` plus global `SELECT` grants. The separate test-driver target authenticated as a different principal (`root` matched through a wildcard Host account) and had broad administrative/write grants. Both accounts connected and returned `USER()`, `CURRENT_USER()`, `CURRENT_ROLE()`, and `SHOW GRANTS`; the 1045 arose from the server-side `EXPLAIN FOR CONNECTION` authorization check, not login authentication. Toolkit continued to classify the monitor account as `NON_PRIVILEGED`. No credential values were recorded. No GRANT/REVOKE, account/configuration change, or Toolkit code change occurred.

**Decision: NO-GO for a general cross-username MySQL active-session Plan Collector.** The 8.4.11 source path offers no least-privilege way for the separate monitor username to inspect arbitrary application usernames: satisfying the static global ACL mask would grant write/admin capabilities. Do not recommend or apply that grant set. A same-username EXPLAIN was observed to work, but making application sessions share the monitoring identity is not adopted as a general DBA monitoring design; any narrower same-username product contract needs separate approval. MySQL 5.7 and 8.0 remain untested. Existing Performance Schema statement identity/digest evidence is not an execution plan and does not provide a trustworthy alternative plan source without re-executing SQL or changing the permission boundary. No plan Collector was implemented. The feasibility task is closed as `DONE` in Backlog, not `VERIFIED`; this NO-GO does not block independent Phase 3 tasks. Software version remains `0.9.0`.

## 24. PostgreSQL pre-existing Plan evidence feasibility — PLAN-PD-PG-001 (2026-10-25)

### Assessment result

The assessment result is **CONDITIONAL GO** only if an existing plan log or extension evidence source is already available to the monitoring workflow and can be linked to the target statement with sufficient identity and time evidence. The Toolkit will not enable or configure a source for acceptance. There is no current PostgreSQL Plan Collector, and `pg_stat_statements` provides statement-level aggregate statistics, not an execution-plan tree. `auto_explain` is useful only when it was already loaded/configured before the relevant workload and emitted plan evidence; this project must not load it, change PostgreSQL settings, or re-execute captured SQL with `EXPLAIN (ANALYZE)`.

### Local environment and scope limits

The prior local PostgreSQL 16.15 connection attempt stopped at `CredentialSessionUnavailable` / named-pipe access denied before a database session was established. Therefore `auto_explain`, logging destinations, existing plan logs, extension availability, and monitor visibility were not inspected in the live database. This is a local credential-session access blocker, not evidence that PostgreSQL lacks the capability. No PostgreSQL settings, permissions, source, or database state changed. No other PostgreSQL version has real Plan E2E evidence.

No existing evidence source has been accepted for implementation. Any future source must prove database/user/session/time association, avoid ambiguous PID reuse or queryid-only joins, bound and redact imported log text, and distinguish an actual plan recorded during normal execution from an estimated plan regenerated later. The feasibility assessment is complete; no feature support is claimed. Backlog owns the task state. Software version remains `0.9.0`.

## 25. SQL Server Query Store historical-plan source feasibility — PLAN-QS-001 (2026-10-25)

### Roadmap scope and source review

The original Backlog scope is to assess Query Store as a separate, already-enabled historical source and record its state, access, retention, and identity contract. The user database's configuration is not inferred from server version. Query Store has existed since SQL Server 2016; 2016, 2017, and 2019 do not enable it by default, while SQL Server 2022 enables it in `READ_WRITE` for newly created databases. Query Store cannot be enabled for `master` or `tempdb`. Query Store is a persistent historical source distinct from the existing SQL Server cache/active-request plan route.

| SQL Server | Query Store support and default behavior | Capture/version notes | Read-only permission baseline |
|---|---|---|---|
| 2016 | Introduced; not enabled by default. | `ALL` is the documented default capture mode; interval runtime aggregation is available. | Query Store database catalog views require `VIEW DATABASE STATE`; `sys.query_store_query_text` separately documents server-level `VIEW SERVER STATE`. |
| 2017 | Supported; not enabled by default. | `ALL` default; Query Store wait-stat capture was introduced in 2017. | Same 2016–2019 permission split. |
| 2019 | Supported; not enabled by default. | `AUTO` default; custom capture policy introduced. | Same 2016–2019 permission split. |
| 2022 | Supported; enabled `READ_WRITE` by default only for newly created databases. | `AUTO` default; Query Store for secondary replicas is a distinct state/path and is not covered here. | Query Store database views require `VIEW DATABASE PERFORMANCE STATE` (or greater `VIEW DATABASE STATE`); query text separately requires server-level `VIEW SERVER PERFORMANCE STATE`. |

`sys.database_query_store_options.desired_state_desc` is the requested mode, while `actual_state_desc` is the effective mode and may be `OFF`, `READ_ONLY`, `READ_WRITE`, `ERROR`, or (on supported secondary-replica scenarios) `READ_CAPTURE_SECONDARY`. `readonly_reason` is meaningful when the desired state is `READ_WRITE` but actual state is `READ_ONLY`; its bit flags explain conditions such as database read-only/single-user/emergency, secondary replica, storage quota, memory pressure, pending flush, or database disk space. A read-only assessment should also record actual/current storage versus the configured maximum, capture mode, interval, retention/cleanup settings, and whether query capture is `NONE`. These settings explain evidence gaps; none proves that a particular statement was captured or retained.

The catalog relationships are `query_store_query_text.query_text_id` → `query_store_query.query_text_id`, `query_store_query.query_id` → one or more `query_store_plan.query_id` rows, and `query_store_plan.plan_id` → runtime-stat rows and their `runtime_stats_interval_id`. One query can have multiple plans. The same plan can have statistics over multiple time intervals and execution types. The current interval can expose multiple runtime-stat rows for the same `(plan_id, execution_type, interval_id)` because disk-flushed and in-memory values coexist; documented aggregation groups those keys. These are interval aggregates, not per-invocation measurements. Plans and statistics can be removed by retention or size-based cleanup. `query_store_plan.query_plan` is stored Showplan XML equivalent to an estimated compile plan, not an actual runtime row-source plan.

### Toolkit reuse and identity boundary

Current SQL Server Top SQL reads cache-resident `sys.dm_exec_query_stats`, exposing statement identity from `sql_handle`, statement offsets, database ID, and `query_hash`; its plan identity uses `plan_handle`, `query_plan_hash`, and `plan_generation_num`. Current Plan diagnosis reads current requests/cache through `plan_request` and `plan_cached`, joins the selected session to current DMV rows, and parses bounded Showplan XML. Query Store has no Registry script, Collector/Profile, permission route, or CLI identity path in the current source; SQL Server Plan CLI currently requires a session ID. The existing Showplan XML parser, bounded plan-node model, ReportModel, and HTML/JSON rendering are reusable candidates, but the source/evidence and identity model must remain distinct and report estimates/history explicitly.

Query Store `query_id` and `plan_id` are Query Store catalog identifiers, not DMV `sql_handle` or `plan_handle` values. `query_hash` and `query_plan_hash` describe query/plan shapes and can narrow candidates, but hashes alone are not exact execution identity. Query Store also separates the same query text across plan-affecting context settings and module/object contexts, can retain multiple plans for a query, and aggregates runtime measurements by interval. `sys.query_store_query_text.statement_sql_handle` is a potential statement-level bridge; however, the current Toolkit Top SQL Registry does not collect that field, and its existing result must not be retroactively described as an exact Query Store match. A future collector would need an audited bridge (database scope, statement handle where available, context/module and plan identity checks), unique-candidate enforcement, and `AMBIGUOUS`/`UNAVAILABLE` degradation whenever evidence is incomplete. A matching hash, similar text, or latest timestamp alone must not promote a historical plan to the exact plan of one execution.

### Local state, permissions, and decision

The only confirmed local SQL Server test asset targets `master`. The current read-only `dba test-connection sqlserver2022 --json` attempt returned `CredentialUnavailable` / “Interactive terminal required to unlock credential store” before database connection. No server/database Query Store state or contents were queried. The known `master` target cannot be used for Query Store plan E2E even after credential access is restored; no alternate database was guessed or scanned. This is an environment access limitation, not a claim about the SQL Server instance's other user databases.

The existing SQL Server Plan Permission Registry covers current plan/request DMVs (`VIEW SERVER STATE` on legacy versions and `VIEW SERVER PERFORMANCE STATE` for SQL Server 2022), not Query Store. A future Query Store route needs separate preflight for the per-database and query-text visibility permissions above; it must not require `sysadmin`, broad write grants, or automatic grants. Query text and Showplan XML can expose sensitive application SQL, so collection needs strict row/byte bounds, redaction, and protected reports. Disabled, empty, not-captured, retention-cleaned, permission-denied, and identity-ambiguous evidence must remain distinguishable and must never trigger automatic setting changes, cleanup, or plan forcing.

**Decision: CONDITIONAL GO for a separate minimal MVP only on a specifically selected user database where Query Store is already enabled and contains retained plans.** The source is operationally valuable for historical plan comparison and plan changes after cache eviction, but cannot guarantee a record for every query or bind interval aggregates to an individual execution. A future implementation should start with read-only status and bounded plan retrieval, use the shared Showplan parser only after identity checks, and report candidates/history rather than asserting an exact execution where the bridge is insufficient. No Collector, Registry, permission, database, or product code was changed. No real Query Store state was verified. SQL Server 2016/2017/2019/2022 behavior is based on official documentation and source review, not four live E2E runs. Backlog owns task state. Software version remains `0.9.0`.

### Reviewed source pointers

- Current adapter, active/slow evidence, Top SQL identity, cache Plan Registry, and Showplan parser: `dba/sqlserver.py`, `dba/slow.py`, `dba/top_sql.py`, `dba/top_sql_collectors.py`, `sql/sqlserver/top_sql.sql`, `sql/sqlserver/plan_request.sql`, `sql/sqlserver/plan_cached.sql`, `dba/execution_plan.py`, `dba/execution_plan_profiles.py`.
- Current version routing, permission preflight/registry, CLI Plan identity, and shared plan report: `dba/versioning.py`, `dba/permissions.py`, `dba/cli.py`, `dba/reporting/presenters.py`.

### Official references

- [Monitor performance by using the Query Store](https://learn.microsoft.com/en-us/sql/relational-databases/performance/monitoring-performance-by-using-the-query-store?view=sql-server-ver16)
- [Best practices for managing the Query Store](https://learn.microsoft.com/en-us/sql/relational-databases/performance/manage-the-query-store?view=sql-server-ver16)
- [How Query Store collects data](https://learn.microsoft.com/en-us/sql/relational-databases/performance/how-query-store-collects-data?view=sql-server-ver16)
- [sys.database_query_store_options](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-database-query-store-options-transact-sql?view=sql-server-ver17), [sys.query_store_plan](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-query-store-plan-transact-sql?view=sql-server-ver17), [sys.query_store_query_text](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-query-store-query-text-transact-sql?view=sql-server-ver16), [sys.query_store_runtime_stats](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-query-store-runtime-stats-transact-sql?view=sql-server-ver15).

## 26. SQL Server current Memory Grant Queue MVP — V091-P4-MEMORY-GRANT-SQLSERVER-001 (2026-10-25)

### User-approved conditional closure and deferred acceptance — 2026-10-28

The user accepted the implemented scenario and SQL Server 2022 basic empty-sample E2E within the recorded scope, and approved moving the specialized real-environment checks below to this independently tracked checklist. Canonical task status is in Backlog. `DONE` here does not mean full production verification, and none of the deferred items is a PASS. Before formal production release of the relevant capability, each gate requires its recorded real verification or a separate explicit user-approved risk decision identifying the affected scope.

Earlier statements in this section that the parent task remained `E2E_PENDING` describe the status at the time of those observations and are superseded by this decision; the individual outstanding observations remain unchanged.

Evidence already established: SQL Server 2022 16.0.4305.5 real connection and monitoring permission preflight, successful complete empty waiting/granted samples, and historical focused offline tests. One bounded real AdventureWorks query's actual plan showed Sort over 5000 rows, grant 1232 KB and max usage 344 KB. That plan is not a Toolkit nonempty grant sample. Previously recorded test results were not rerun for this documentation change.

| Deferred item and original state | Existing evidence and risk | Next test conditions | Reacceptance standard |
|---|---|---|---|
| Granted nonempty, `BLOCKED` | Simulated rows passed offline; actual plan grant exists but Toolkit captured no target row. Live collector behavior is unproven. | Authorized isolated lab, bounded workload and monitored interval with recorded overlap. | Same real Toolkit sample contains the workload grant, non-null grant time and valid granted KB; record version, timing, completeness and truncation. |
| Waiting nonempty, `BLOCKED` | Empty real sample and simulated waiting paths only; live waiting classification unproven. | A naturally occurring or separately approved safe controlled wait; no unsafe pressure merely to force it. | Real Toolkit waiting row with null grant time and correct reported waiting state, scope and evidence. |
| Nonempty session/request/handle identity, `BLOCKED` | Offline association and conflict cases only; wrong live association could mislead diagnosis. | A captured nonempty sample with independently observed session/request identity and available handles. | Same-sample IDs and each available handle agree; missing handles remain unknown and conflicts degrade safely. |
| Nonempty JSON/HTML parity, `BLOCKED` | Empty report E2E and offline rendering evidence only; output divergence could mislead. | One real nonempty collection rendered into both formats. | Shared fields and target rows agree for the same collection; document fields exclusive to JSON and omissions. |
| Real permission denial, `BLOCKED` | READY preflight observed; missing-permission path has source review but no dedicated diagnose-level regression identified. Failure clarity in a real denial is unproven. | Separately authorized least-privilege test account or existing safe denial; no automatic GRANT/REVOKE. | Correct missing capability, safe degraded state and manual-only minimum grant advice; no credential leakage. |
| Real timeout, `BLOCKED` | Deterministic timeout mapping only; driver/server cleanup and real classification unproven. | Authorized safe timeout test without harmful load or configuration change. | Real timeout is classified and reported, partial evidence retained correctly, and connections released. |
| Real truncation, `BLOCKED` | Offline 101-row sentinel and limit tests only; real boundary and flags unproven. | Safe lab with naturally available sufficient rows or separately approved bounded setup. | Real cap and truncation flag are correct in collector and reports without false completeness. |
| Other SQL Server versions, `BLOCKED` | Offline routing; real Memory Grant evidence is limited to SQL Server 2022 16.0.4305.5. Version/permission compatibility remains unproven. | Individually authorized supported-version instances and correct least-privilege profiles. | Record exact server/driver/profile/permissions and representative real collection per tested version; no extrapolation. |

The latest 5000-row attempt sampled before the workload actually started. This is a **test scheduling failure**, not a proven Collector defect. Any future nonempty re-test must establish monitor readiness and record precise start/end times for both workload and each relevant sample, demonstrating actual overlap; merely launching both processes is insufficient. No further load enlargement is authorized by this decision.

### Offline implementation scope

The approved Phase 4 scenario is limited to current SQL Server memory grant wait and granted-request evidence. `dba diagnose <database_id> memory-grant` issues two separate reviewed read-only Registry queries against `sys.dm_exec_query_memory_grants`, each left-joined in the same statement to `sys.dm_exec_requests` on `(session_id, request_id)`. Each query excludes the Toolkit's own session, has a five-second metadata timeout, and fetches at most 101 rows: 100 displayed rows plus one sentinel to prove truncation. The waiting and granted subsets are sampled separately, without an unnecessary sort, aggregation, SQL text, Showplan, cumulative Top SQL, polling, or remediation.

The report preserves raw KB values and displays requested, required, granted, used, and maximum-used values as separate measures. `grant_time IS NULL` is observed as a waiting grant; `RESOURCE_SEMAPHORE` is shown only when the same-sample current-request join supplies that wait type. Missing request context preserves the source grant but lowers completeness. Conflicting or duplicate `(session_id, request_id)` identity, query failure, timeout, or a truncation sentinel also lowers completeness. Session/request IDs are time-scoped and the two samples are sequential, not atomic. No conclusion attributes a unique root cause to a granted request.

Console output, JSON, and the existing HTML report use the same result data. Console output is bounded to 20 rows per sample; HTML/JSON contain at most 100 rows per sample and show per-sample timestamps, source row count, truncation and errors. Memory-Grant-only output reports `health_assessment=NOT_ASSESSED`; an empty complete wait sample means only that no wait was observed during that sample.

### Permission and version route

The versioned Permission Registry and Preflight contract uses `VIEW SERVER STATE` for SQL Server 2008 R2 through 2019 and `VIEW SERVER PERFORMANCE STATE` for SQL Server 2022. Reviewed Memory Grant Registry scripts are routed through the matching verified SQL Server profile. A SQL Server 2025 binding is retained as `UNKNOWN`, not claimed as supported. `dba permissions <database_id> --feature memory-grant` exposes the feature readiness; any suggested SQL is text for DBA review only. Toolkit has no grant execution path for this feature.

### Offline verification and remaining acceptance

The implementation-stage deterministic Memory Grant suite previously covered separate waiting/granted samples, multiple requests in one session, same-query identity association, missing and incomplete request context, handle conflicts, duplicate identity, timeout/error redaction, row-limit sentinel, empty samples, permission/profile/Registry alignment, shared JSON/HTML rendering, CLI routing, and the Plan-only/Memory-Grant-only non-health semantics. Historical gates from that implementation turn were: focused suite **13 passed**; full suite **1036 passed, 2 skipped**; Ruff **PASS**; strict mypy over `dba` **PASS (73 source files)**; `dba validate` **PASS (4 instances, 121 SQL scripts)**; `git diff --check` **PASS**. They are historical and are not represented as rerun for this closeout.

### Windows-local real E2E — SQL Server 2022 empty sample

**Evidence class: `WINDOWS_LOCAL_REAL_E2E`.** The user-provided result is from the project's Windows-local environment and the single `environment=test` Inventory target `sqlserver2022`, using project `.venv`; this turn did not reconnect or rerun E2E. Reported SQL Server version was **16.0.4305.5**. Permission readiness was `READY`. Waiting and granted DMV samples both succeeded with **0 rows**, `COMPLETE` completeness, no truncation, and no identity conflicts. JSON and HTML came from the same collection and matched; health remained `NOT_ASSESSED`. This verifies the normal empty-sample path only.

The initial JSON reported `elapsed_ms=0.0` for both queries despite nonzero client sample timestamp intervals: waiting `2026-10-25T14:57:21.669087Z`–`2026-10-25T14:57:21.674304Z` (5.217 ms by timestamp difference), and granted `2026-10-25T14:57:21.675330Z`–`2026-10-25T14:57:21.679382Z` (4.052 ms by timestamp difference). Those differences describe client sample intervals, not server-side SQL execution time. The monotonic high-resolution client timer and scope label were added to correct this defect; their subsequent live verification is recorded below.

The initial exported diagnosis JSON exposed the `report.instance.credential_alias` value. The export boundary now replaces it with `[REDACTED]` only in the serialized diagnostic JSON copy; the internal report and credential lookup retain the alias. No existing local report was edited. The post-fix live verification is recorded below.

### Post-fix Windows-local real E2E — SQL Server 2022 empty sample (2026-10-25)

**Evidence class: `WINDOWS_LOCAL_REAL_E2E`.** On Windows against SQL Server **16.0.4305.5**, the fixed build completed both empty samples successfully: Waiting returned 0 rows with `elapsed_ms=2.1261`; Granted returned 0 rows with `elapsed_ms=2.0110`. Both reported `elapsed_scope=CLIENT_END_TO_END`. JSON and HTML timing semantics matched. Permission was `READY`, completeness `COMPLETE`, identity conflicts `0`, snapshot consistency `SEQUENTIAL_BEST_EFFORT_NOT_ATOMIC`, and health `NOT_ASSESSED`. JSON/HTML evidence was consistent. The exported JSON contained `credential_alias=[REDACTED]`.

This post-fix run verifies both previously observed defects on the normal empty-sample path: elapsed time no longer appears as a hard-coded `0.0`, and the exported JSON no longer reveals the credential alias. Client elapsed values are not server-side SQL execution durations. `version_profile.version_status=VERIFIED` means only that the SQL Server version/profile route was verified; it does **not** mean the Memory Grant feature is fully verified.

Nonempty waiting/granted rows, live request identity association, unmatched/NULL-field behavior on real rows, actual timeout, and actual truncation remain unverified. No load was manufactured and no database permission/configuration was changed. The real empty-sample path is verified, but the feature remains `E2E_PENDING` and is not marked `VERIFIED`. Software version remains `0.9.0`.

### Current closeout offline checks

The preceding code closeout recorded `tests/test_memory_grant_sqlserver.py`: **14 passed**; Ruff: **PASS**; `mypy --strict dba`: **PASS (73 source files)**; and `git diff --check`: **PASS** (LF-to-CRLF working-copy notices only). These are prior offline results, not rerun for this documentation-only update. No full pytest, `dba validate`, database connection, or Docker operation was performed for this documentation update.

### Current targeted recheck — 2026-10-26

**Evidence class: `ISOLATED_OFFLINE_TEST`.** After omitting generic `impact` text for a READY Memory Grant permission result, `tests/test_memory_grant_sqlserver.py` passed **14 tests** (0.90 seconds); Ruff passed for `dba/cli.py` and that test module; strict mypy passed for `dba/cli.py`; `git diff --check` passed (working-copy LF-to-CRLF notices only). No full suite, Registry revalidation, database access, or Docker operation was run.

### Remaining E2E verification attempt — Windows access gate (2026-10-26)

**Database E2E status: `NOT_EXECUTED`.** This attempt verified Windows PowerShell, the project working directory and `.venv` paths, and the single Inventory target `sqlserver2022` (`127.0.0.1:1433`, `master`, `environment=test`, enabled). It did not establish a database session. `Test-NetConnection 127.0.0.1 -Port 1433` returned false; Docker context `desktop-linux` returned access denied on the Docker Desktop engine named pipe; Credential Store read returned `CredentialUnavailable` because an interactive terminal is required to unlock it. These are access/precondition observations from the current process, not evidence that SQL Server itself is faulty. No `test-connection` or Memory Grant SQL was run, and no credentials were read or bypassed.

**Offline evidence class: `ISOLATED_OFFLINE_TEST`.** The current targeted run of `tests/test_memory_grant_sqlserver.py` passed **14 tests** in 1.29 seconds. These tests use `FakeDriver` and are not real database evidence. They cover empty samples; simulated nonempty waiting/granted rows; same-sample `(session_id, request_id)` association; SQL/plan handle match and conflict; missing request context; timeout mapping to `QueryTimeout`; the 5-second cursor timeout; and the 101st-row truncation sentinel. The Registry is fixed read-only SQL with `TOP (101)`, five-second timeout, and 100 displayed rows. SQL text is intentionally not collected: the correlation evidence is DMV join identity and SQL/plan handles, not readable SQL text.

The existing focused suite checks Permission Registry/profile alignment and a READY permission listing, but has no dedicated `diagnose memory-grant` regression for the MISSING-permission branch. Source inspection shows that branch returns `UNAVAILABLE`/`UNKNOWN` evidence and renders a text-only grant suggestion from missing privileges; neither missing-permission behavior nor its suggested grant was verified against a real denied account in this attempt. Version routing is deterministically defined for SQL Server 2008 R2 through 2022, while the 2025 route remains `UNKNOWN`; real evidence is limited to SQL Server 2022 16.0.4305.5. No older-version E2E was performed.

The already recorded post-fix SQL Server 2022 empty-sample `WINDOWS_LOCAL_REAL_E2E` remains valid: both collectors succeeded with zero rows and Permission READY. Still blocked/unverified are real nonempty waiting and granted rows, live request/session/handle association, a genuine permission denial, an actual query timeout, and actual row-limit truncation. Waiting data will not be manufactured by changing server settings or creating memory pressure. The task remains `E2E_PENDING`.

### User-provided Windows-local rerun — SQL Server 2022 empty sample (2026-10-26)

**Evidence class: `WINDOWS_LOCAL_REAL_E2E` (user-provided).** The user reports a successful run from local PowerShell against the test asset `sqlserver2022`: SQL Server **16.0.4305.5**, Permission `READY`, Collection `COLLECTED`, Completeness `COMPLETE`, Waiting **0 rows**, Granted **0 rows**, `truncated=false`, and health `NOT_ASSESSED`. The HTML report is `reports/sqlserver2022_memory-grant_20261026_213521.html`. This adds a current normal empty-sample result; it does not establish nonempty Waiting/Granted collection or live request association. The report was not opened in this execution context, and no database connection was attempted here.

### Collector SQL and association review

Static review of `sql/sqlserver/memory_grant_waiting.sql`, `sql/sqlserver/memory_grant_granted.sql`, their reviewed metadata, and `dba/memory_grant.py` found no evidence of an accidental nonempty-row filter. The scripts use independent `grant_time IS NULL` / `grant_time IS NOT NULL` predicates, exclude only the Toolkit's own `@@SPID`, and left-join current requests on `(session_id, request_id)`. The parser retains a grant if its request join is absent, but marks context `REQUEST_NOT_FOUND`; mismatched IDs/handles and duplicate grant identities are flagged and lower completeness. `TOP (101)` is bounded and intentionally has no ordering; each side is sampled separately. The Collector does not fetch SQL text. Consequently, a matched row confirms same-sample request/session and available handle consistency, not a readable SQL statement identity or an atomic snapshot.

### Permission readiness presentation correction

Source review found that `diagnose ... memory-grant` copied the generic permission `impact` text into `permission_readiness` even when status was `READY`. The CLI now omits that impact field for `READY`; non-READY paths retain it. A deterministic CLI regression asserts the omission in both exported readiness locations. This is an offline code fix, not database evidence.

### Windows host connection recheck — 2026-10-28

**Bounded 5000-row execution, `WINDOWS_LOCAL_REAL_E2E`:** The revised user-approved boundary permits estimated reads above 5000 when real access is verified. The estimated plan placed the clustered scan under Top and Sort; a single `MAXDOP 1` SELECT then returned 5000 rows. Actual plan: scan `ActualRowsRead=5000`, `ActualRows=5000`, 62 logical and 0 physical reads; Top and Sort each output 5000 rows, confirming Sort input via its single Top child. `GrantedMemory=1232 KB`, `MaxUsedMemory=344 KB`; SQL Server elapsed/CPU 4/4 ms, client end-to-end 17.30 ms. Actual-plan grant establishes past grant only, not Toolkit live capture.

Workload interval was 15:35:05.552964–15:35:05.570211 UTC. The three Toolkit Granted samples were 15:34:56.294937–56.297005, 15:34:58.961524–58.963592, and 15:35:01.665860–01.667936 UTC; all returned zero and all ended before the workload started. The monitor was started first but did not sample during the workload. This explains the missed overlap and gives no conclusion about Collector behavior under a live grant. The existing helper produced three JSON/HTML pairs under `reports/sqlserver2022_memory-grant-acceptance_20261028_233455_701892_*`; no target row existed for per-row report parity or identity validation. Workload connection closed; no permission change occurred. The task remains `E2E_PENDING`.

Proposed acceptance adjustment, pending user decision: retain normal empty-path real E2E and deterministic nonempty/identity/timeout/truncation regressions as separately labeled evidence; retain real nonempty Granted and real Waiting as explicit deferred field-validation items, rather than representing them as PASS or indefinitely blocking unrelated work. Any closure or change to Phase 4 ordering requires user approval.

**Later bounded-plan review, `WINDOWS_LOCAL_REAL_E2E`:** The earlier three granted samples ran at 15:26:45.025117–45.028777, 15:26:47.660903–47.662988, and 15:26:50.303575–50.305647 UTC. The workload start/end timestamps and actual Sort input were not recorded, so sample overlap and the presence of a live grant cannot be established. The previous fixed `SalesOrderID=68511` predicate returned three matching rows; that is not proof of actual Sort input. The monitoring helper had reported it was started before the workload trigger, but exact overlap is unknown.

The newly proposed `TOP (5000)` ordered clustered-key subquery with outer `LineTotal` sort was examined using session-only `SHOWPLAN_XML` on the authorized test instance. Sort estimated 5000 input rows and 512/1232 KB required/desired memory; however, the clustered index scan estimated 121317 rows read. These estimates do not establish a physical 5000-row read cap. In accordance with the requested resource boundary the workload and new concurrent sampling were not run. This is not evidence of a Collector defect. Real nonempty grant and identity validation remain blocked; the task remains `E2E_PENDING`.

Subsequent authorized Windows-host preflight used the user-designated lab configuration in memory to authenticate as `sa`; server version `16.0.4305.5` and `AdventureWorks2022` database ID 5 were confirmed. Direct server grants to `dba_monitor` were `CONNECT SQL`, `VIEW ANY DATABASE`, and `VIEW SERVER STATE`; no GRANT or REVOKE was executed. A session-scoped `SHOWPLAN_XML` estimate for the fixed `SalesOrderID=68511` candidate showed Sort and Clustered Index Seek, estimated 2.63448 rows and estimated 512 KB required / 544 KB desired memory. These are estimates, not measured reads or proof of a granted sample. A subsequent single read-only execution returned three rows on a separate sa connection while the existing monitoring helper took three bounded samples. The helper reported `NO_MATCHING_GRANTED_SAMPLE`; it saved three JSON/HTML pairs under `reports/sqlserver2022_memory-grant-acceptance_20261028_232644_416139_*`. The test connection closed. No matching nonempty Granted sample or request identity was verified; do not enlarge the workload.

**Evidence class: `WINDOWS_LOCAL_REAL_E2E` (connection preflight only).** An authorized non-isolated Windows process used the project's `.venv`; the selected `sqlserver2022` Inventory entry was enabled, `environment=test`, and pointed to `127.0.0.1:1433` / `master`. The local port and existing monitor credential store were accessible. Toolkit `test-connection` succeeded against SQL Server `16.0.4305.5`, and reported monitor capability and permission preflight success. Its account classification was `UNKNOWN`, with no listed high-risk roles or permissions; this is not a complete account audit.

No accessible authenticated `sa` session was established for this run. Accordingly, administrator-side permission inventory, plan/access verification, and concurrent nonempty Granted acceptance were **not executed**. No GRANT or REVOKE was issued and no permission was changed; no rollback is applicable. The earlier user-observed empty Memory Grant samples remain the only real Memory Grant E2E evidence. The three-row candidate's Seek, Sort, actual reads and memory grant remain unverified; do not increase the workload to compensate. The task remains `E2E_PENDING`.

### User-provided AdventureWorks preflight — 2026-10-28

The user reported existing `AdventureWorks2022.Sales.SalesOrderDetail` metadata: approximately **121,317** rows and a unique clustered primary key with leading columns `SalesOrderID, SalesOrderDetailID`. A user-run, fixed-key `FORCESEEK` SELECT with a 501-row counting cap returned **3** rows for `SalesOrderID=68511`. This is a snapshot of matching rows, not a measurement of physical reads or a guarantee against concurrent data changes.

The user manually ran the candidate three-row SELECT and reported `dba diagnose sqlserver2022 plan --session 57 --json`: `plan_status=PLAN_NOT_AVAILABLE`, `plan_available=false`, `request_found=false`, and both SQL/Plan handles were null. The cached probe returned one connection row without a matching plan; this does not establish whether the plan was evicted or why the match was absent. Seek, Sort and actual rows read are **not verified**. Memory Grant Granted E2E remains `NOT_EXECUTED` / `BLOCKED` pending appropriate evidence; do not enlarge the workload.

The user-selected lab boundary is manual DBeaver `sa` workload and the existing `dba_monitor` Toolkit credential. No `sa` password or workload alias is given to the helper. `scripts/acceptance/inspect_sqlserver_memory_grant.py` prints fixed metadata SELECTs, while `scripts/acceptance/monitor_sqlserver_memory_grant.py` runs at most three ordinary Toolkit Memory Grant diagnoses during a 30-second sampling window after verifying the `sa` session. The monitor saves same-invocation JSON/HTML, checks common HTML fields and same-sample request identity, and labels plan/actual access and DBeaver session release unverified. It does not execute the workload or promote this task to verified.

Offline helper and existing Memory Grant tests were run separately from the lab: **29 passed**; Ruff and strict mypy passed for the two helper scripts. No database, Docker, or real Memory Grant E2E was accessed by the agent during this work.

| Acceptance item | Status | Evidence |
|---|---|---|
| Real nonempty Waiting and Granted sample collection | `BLOCKED` | User-provided 2026-10-26 PowerShell run and prior real run both returned zero rows for both samples; no nonempty record was observed. |
| Real grant ↔ request/session/SQL identity association | `BLOCKED` | No live rows to correlate. Offline tests verify `(session_id, request_id)` and matching SQL/plan handles; SQL text is intentionally not collected. |
| Empty/no-record result is successful, not an error | `PASS` | User-provided 2026-10-26 SQL Server 2022 run: both collectors succeeded with 0 rows, `COLLECTED` / `COMPLETE`, and no truncation. |
| Real permission-denied message and degradation | `BLOCKED` | Current live permission was previously READY; no denied account tested. Source has a `MISSING` → `UNAVAILABLE` path and missing-only grant suggestion, but no dedicated automated diagnose-missing-permission regression was found. |
| Real query timeout, 100-row cap, and truncation marker | `BLOCKED` | Offline tests cover 5-second timeout mapping and the 101st-row sentinel; no real timeout/truncation was induced. |
| SQL Server version compatibility | `BLOCKED` | Deterministic routing covers 2008 R2–2022 and leaves 2025 `UNKNOWN`; real evidence is SQL Server 2022 only. |
| Existing automated coverage | `PASS` | Current focused FakeDriver suite is 14/14. It covers simulated nonempty rows, association/identity conflicts, empty samples, timeout, and truncation; the specific diagnose-level missing-permission path lacks a focused regression. |

Next safe action: resolve the missing plan/access evidence for the fixed three-row AdventureWorks candidate, then use the bounded monitor only if the approved safety conditions can be met. If no Granted row is captured, do not enlarge the workload. Any Waiting sample must occur naturally. Software version remains `0.9.0`.

## 27. PostgreSQL WAL retention — V091-P4-WAL-RETENTION-POSTGRESQL-001 (2026-10-28)

Implementation in progress. Existing disk-growth slot and archive collectors are reused; role, WAL position and settings use one reviewed bounded query. The PG17/18 slot result name mismatch was reproduced by an offline deterministic regression and addressed in the analyzer. Slot `lost`/`unreserved` are observations requiring review; LSN distance is not actual or exclusive filesystem size. Samples are sequential.

`ISOLATED_OFFLINE_TEST`: focused disk-growth, replication, permissions and HTML suites: 134 passed; targeted Ruff and mypy passed; validate reported 122 SQL scripts. These results do not prove real nonempty slot or cross-version behavior.

`WINDOWS_LOCAL_REAL_E2E`: selected pg16 Inventory is test at local port 5432. Existing monitor credential worked; PostgreSQL 16.15 connection preflight reported read-all-stats and read-all-settings true. Final disk-growth returned successful empty slot evidence, primary role, settings and archive evidence. Same-run reports: `reports/pg16_wal_final_20261028_234642.json` and `.html`; selected common values (version, current LSN and three settings) matched, JSON alias was redacted. This does not verify nonempty slot rendering or all report fields. No database grants, slots, configuration or data were changed.

Remaining: complete evidence/status semantics and test coverage including denied/timeout/truncation, archive and complete JSON/HTML parity; verify nonempty physical and logical slots only when safely available, and other versions independently. Keep task in progress until implementation scope is complete; do not claim full feature verification or Memory Grant deferred-gate closure.

### Evidence contract follow-up — 2026-10-28

`ISOLATED_OFFLINE_TEST`: fixed `slot_count_observed=0` for failed or uncollected slots; these now return NULL while a successful empty collection returns 0. Truncated samples retain their observed count and explicit status. Preserved PostgreSQL setting strings without unsupported byte conversion; added notes for `-1` and safe-WAL NULL ambiguity. Focused disk-growth, replication, permission and HTML suites: 139 passed; targeted Ruff and mypy passed. New tests distinguish empty/uncollected/failed/truncated, preserve NULL, independent slot distances, and primary/standby source names. Prior status and version fixture tests remain applicable; not all denial/timeout or full nonempty JSON/HTML combinations were tested in this follow-up.

`WINDOWS_LOCAL_REAL_E2E`: after the fix, project venv and test-only pg16 monitor were used from the authorized Windows host; PG 16.15 was primary, slots EMPTY (0), settings and archiver AVAILABLE, and sampled common version/LSN/settings values matched JSON and HTML. JSON credential alias redacted. Reports: `reports/pg16_wal_contract_20261028_234939.json` and `.html`. No slot, database, configuration or permission changes. Nonempty physical/logical slots, standby, real denial/timeout/truncation and other versions remain unverified. Task remains IN_PROGRESS; conditional closure requires user approval after remaining quality checks.

Additional offline failure classification: PostgreSQL adapter maps SQLSTATE 42501 to `QueryDenied` and 57014 to `QueryTimeout`; WAL evidence now reports PERMISSION_DENIED and TIMEOUT respectively instead of generic FAILED. The subsequent final focused suite passed 142 tests. Real denial and timeout remain unverified; the Windows empty-path run above preceded this failure-only classification adjustment.

### Final focused review and proposed conditional acceptance — 2026-10-28

`ISOLATED_OFFLINE_TEST`: focused disk-growth, replication, permissions and HTML suites **157 passed**; targeted Ruff and mypy passed; Registry validate: **122 scripts**. Tests exercise physical/logical slot identities, four WAL states, null fields, truncated/failed/uncollected versus empty counts, error classification, source names, no distance summing, and PG14/15/17/18 routing. Report-contract fixtures compare structured values and presenter tables; no fixture is real E2E. Fixed HTML dropping entirely NULL slot columns, presenting zero slots when collection was unavailable, and omitting interpretation for `-1`, NULL safe WAL and unknown total after truncation. The fixed route selects one slots query per profile; replication regression suite passed. No write path or additional diagnosis command was added.

`WINDOWS_LOCAL_REAL_E2E`: after report changes, authorized project venv and pg16 monitor produced PostgreSQL 16.15 primary, slots EMPTY/0, settings and archive AVAILABLE. Checked common version, current LSN and three settings in same-run JSON/HTML; alias redacted. Reports: `reports/pg16_wal_closeout_20261028_235425.json` and `.html`. No permissions, slots or configuration were changed. This establishes the normal empty-sample path only; nonempty report parity is fixture evidence.

Proposed conditional closure, subject to user approval only: defer the following live evidence; none is PASS. Before formal production release of each affected capability, complete its gate or obtain separate explicit approval of the scoped risk.

| Deferred real E2E | Existing evidence / risk | Future acceptance condition |
|---|---|---|
| Nonempty physical slot | Fixture only; real slot fields and interpretation may differ. | Safe existing physical slot; same-sample fields, LSN and report parity verified. |
| Nonempty logical slot | Fixture only; confirmed flush and NULL handling may differ in a live slot. | Safe existing logical slot; verify identity, confirmed flush, NULL and reports. |
| Standby | Fixture and reviewed SQL only; receive LSN may be NULL or differ from primary. | Authorized standby and existing slots; verify role, source and NULL semantics. |
| Permission denial / timeout / truncation | Error and truncation fixtures only; actual driver/server degradation and cleanup unproven. | Separately approved safe lab conditions; confirm classified error, partial evidence, row bound and connection cleanup. |
| PG14/15/17/18 compatibility | Profile and field fixtures; no real run for these versions. | Individually authorized instances; verify exact version/profile, collection and reports. |

The task remains IN_PROGRESS in Backlog pending user decision. SQL Server Memory Grant deferred gates in §26 are unchanged.

### Conditional closure approved — 2026-10-28

The user accepted implementation and scoped PG16.15 empty-slot baseline and authorized Backlog `DONE`, with the preceding deferred checklist and production-release gates unchanged. No nonempty slot, standby, real fault or other-version E2E is promoted to PASS. The last HTML unavailable-collection wording fix has offline verification only; the previous real empty-path report preceded that final fix. SQL Server Memory Grant §26 remains unchanged. Four-engine integration regression is separately authorized; it does not imply production release approval.

## 28. V0.9.1 integration inventory and regression — 2026-10-28–29

See `docs/VERIFICATION_V0_9_1_INTEGRATION.md` for four-engine capability, scoped Windows-local E2E, same-run limited report parity, sandboxed full-suite failures, subsequent targeted reruns, scanner classification, and known defects. Full pytest has not passed; two inventory-show regressions reproduce credential IPC dependency, and eight original failed cases remain individually unadjudicated. PostgreSQL WAL conditional closure in §27 and SQL Server Memory Grant conditional closure in §26 retain their separate deferred production gates. This record does not approve a production release.

Follow-up: the eight remaining originally failed tests were individually reproduced as seven sandbox named-pipe access denials and one sandbox Temp WinError 5; the two SQL Server inventory-show failures remain a confirmed unrelated IPC dependency in that condition. Eight other original failures passed individually. The original 18-failure full run remains recorded; no fresh full-suite pass is claimed.

## Credential SAVE prototype and isolated IPC — 2026-10-29

ISOLATED_OFFLINE_TEST only. The credential integration tests now launch a disposable agent on a unique endpoint shared explicitly with the parent and lookup subprocess. The two credential unit modules use unused private endpoints and cannot implicitly launch the default user agent. No real user credential file, alias or session was accessed or modified.

A test-only SQLite/AES-GCM prototype exercised Windows file identity binding, a no-delete-sharing handle, serialized transactions, concurrent process saves, replacement attempts inside a transaction, pre/post-commit process exits, hot-journal recovery and authentication/corruption refusal. Focused results: 59 passed, 1 skipped (POSIX permissions on Windows); includes nine prototype cases and two isolated IPC cases. Targeted Ruff, strict MyPy for the prototype and git diff --check passed. No full-suite result or product SAVE fix is claimed. V0.9.1 RC remains blocked; Memory Grant and WAL deferred gates are unchanged. See docs/design/credential-sqlite-transaction-prototype.md for threat limits and the separately approved migration proposal required before integration.

## Credential SQLite Provider integration — 2026-10-30

User approved production-source integration and explicit legacy-to-SQLite migration development, with testing restricted to disposable Stores. Product version remains 0.9.0. No real user Store, new-alias, database account, permission or database was read, migrated, reset or modified. No commit/push occurred.

Implementation: dba/sqlite_credentials.py (opt-in Windows SQLite Provider, AES-GCM/Argon2id, private SID/SYSTEM ACLs, file-ID/UUID binding, DELETE journal and synchronous FULL transactions, mutation/rekey and quarantine-aware copy/verify migration); dba/credential_session.py (protocol 2, server-side target checks, bound SQLite GET/SAVE/SET/DELETE/metadata); dba/cli.py and dba/encrypted_credentials.py (existing command/Resolver routing, no automatic Provider switch). Migration publishes authenticated readiness through a transaction, not a post-validation rename. Old source bytes remain unchanged; failed/incomplete targets are private and rejected. See docs/design/credential-sqlite-provider.md for manual activation, verification, rollback restrictions and exact threat boundaries.

Targeted credential/repository suite: 145 passed, 1 skipped before the final full run; subsequent final-source full regression includes the last safety checks. Final Windows-host run, using C:\path\to\DBAToolKit\.venv\Scripts\python.exe: 1157 passed, 2 skipped, 0 failed, 43.17 seconds. Log: .tmp/credential-stabilization-full-pytest-final.txt. The skips are POSIX permission bits on Windows and the opt-in real database system-test (DBA_TOOLKIT_SYSTEM_TESTS not enabled). Both original failing credential tests now pass. Session IPC tests are isolated and run, not skipped due to an existing user Session. Credential file/IPC tests are ISOLATED_OFFLINE_TEST; no real database E2E was executed.

Product fixtures cover normal/new and legacy reads, unchanged aliases across four database IDs, cross-Store GET/SAVE/SET/DELETE/metadata refusal with unchanged wrong-target bytes, legal SAVE then GET, password/key and payload authentication failure, unsupported format, busy transactions, three concurrent process writers, external replacement attempts, interrupted pre/post-commit transactions and actual hot-journal recovery, rekey, migration interruption/readback failure quarantine and the temporary CLI lifecycle. Prototype fault tests remain separate historical supporting evidence.

Final gates: Ruff PASS; strict MyPy PASS (74 source files); dba validate PASS (4 configured instances, 122 SQL scripts); git diff --check PASS. Secret Scanner PASS on Git-index content, plus explicit current-working-tree scan of the 18 implementation/test/security-document files: zero findings. This is not a claim that every unrelated untracked/worktree file was audited. SQLite main and journal/WAL/SHM filenames are ignored and rejected by scanner tests; no whitelist expansion was made.

RC remains BLOCKED for unrestricted/default credential write use: legacy JSON mutation still has its known replacement window. SQLite is not default and currently rejects non-Windows use. Same-user arbitrary writes/process inspection/valid rollback, parent-directory replacement, power loss/hardware failures and network-filesystem behavior remain outside verified protection. A follow-up release policy/security decision is required; full pytest success does not settle these risks. SQL Server Memory Grant and PostgreSQL WAL conditional closures and all deferred gates remain unchanged. No production release is approved.

## 29. Credential Provider release-policy closure — 2026-10-30

The user approved a release-mode policy in which the old JSON encrypted Provider remains an authenticated, target-bound read/migration source only, while SQLite is the explicit writable encrypted-file backend. The SQLite Provider is not the default and is never discovered by `auto`; Windows Credential Manager behavior and Inventory `credential_alias` are unchanged. No automatic migration, Provider switch, real Store write, `new-alias` operation or database E2E was performed.

Legacy JSON write rejection is enforced at the Provider boundary for `initialize`, `set`, `set_if_absent`, `delete`, `rekey`, `persist_session_credentials`, `_atomic_write`, and `_write_changed`. The Session server independently rejects SAVE/SET/DELETE for legacy sessions; CLI init/set/delete/rekey with `--backend encrypted` is rejected before prompts. Resolver JIT now propagates `CredentialStoreReadOnly` directly instead of wrapping it or offering an ephemeral fallback after the user selected persistence. Legacy reads and migration remain authenticated and Store-bound. Disposable tests verify that denied operations leave source bytes unchanged, no fallback Store is created, and verified migration/new SQLite writes do not alter or alias-merge the source.

`ISOLATED_OFFLINE_TEST`: credential/IPC and old/new Provider focused set: **165 passed, 1 skipped**. This includes both previously failing credential tests, explicit SQLite CLI lifecycle, legacy read-only entry points, migration, source immutability, fail-closed errors, Session binding, crash/journal recovery, and SQLite-backed JIT SAVE followed by Resolver reuse. The one skip is a POSIX-only permission-bit test on Windows.

`WINDOWS_HOST_AUTOMATED_REGRESSION` (not database E2E): normal host context verified as Windows 11, project `.venv` Python 3.12.14, repository root `C:\path\to\DBAToolKit`; full pytest: **1175 passed, 2 skipped, 0 failed** in 44.40 seconds. The skips are POSIX permission bits unavailable on Windows and the opt-in real database system-test (`DBA_TOOLKIT_SYSTEM_TESTS` was unset). No database connection was attempted. Log: `.tmp/credential-readonly-full-pytest-final.txt`.

Quality gates: Ruff PASS; strict MyPy PASS for 74 source files; `dba validate` PASS (4 configured instances, 122 SQL scripts); `git diff --check` PASS (Git emitted existing LF-to-CRLF normalization warnings only). Secret Scanner PASS on the Git index. A separate current-working-tree scan of the 21 credential implementation, test, security and status files reported 0 findings; unrelated untracked files are outside that supplemental scan. No scanner exception or whitelist was broadened.

SQLite uses DELETE rollback journal and sets `synchronous=EXTRA` on each opened connection, verifying PRAGMA value 3. The [SQLite synchronous pragma](https://www.sqlite.org/pragma.html#pragma_synchronous) documents EXTRA as FULL plus a directory sync after rollback-journal unlink in DELETE mode. `ISOLATED_OFFLINE_TEST` microbenchmark (SQLite 3.53.1; 120 single-row transactions each): FULL median/mean 1.4550/1.4670 ms; EXTRA 1.5019/1.4983 ms; both integrity checks were `ok` and committed 120 updates. This tiny benchmark is not representative or a durability guarantee. Process-exit/hot-journal tests are not power-loss tests; SQLite atomicity depends on OS/filesystem/storage sync behavior ([SQLite atomic commit](https://www.sqlite.org/atomiccommit.html)).

Compatibility: existing legacy JSON aliases can still be read and explicitly copied to SQLite, but the old Store cannot be initialized, changed, deleted from or rekeyed by this release. Users must explicitly select SQLite for encrypted writes or use the existing OS credential provider. `auto` will not select SQLite. No default-path migration occurs. Windows Credential Manager remains unaffected. Product SQLite remains Windows-only; Linux/macOS SQLite binding is unsupported.

Unverified risks retained: an older Toolkit release can still write a legacy JSON Store; same-user malicious replacement/memory inspection, parent-directory replacement races, authenticated rollback to an older SQLite file, sudden power loss, underlying fsync/FlushFileBuffers failures, network filesystems and other platforms have not been fully tested. No real user Store, including existing aliases, was opened or modified. SQL Server Memory Grant and PostgreSQL WAL deferred acceptance entries are unchanged.

**Assessment:** the known writable legacy JSON replacement race is closed in this release because every current product write path rejects that backend. The changes and Windows automated evidence are eligible for a **SQLite-scoped RC acceptance review**, limited to explicit Windows SQLite/OS-store use and the listed threat boundaries. This is not approval to migrate real credentials or declare V0.9.1 production-ready. `CRED-MAINT-001` remains `IN_PROGRESS` pending the user’s RC decision; no commit or push occurred.

Final source recheck after the SQLite JIT Session regression: targeted credential/IPC/Provider suite **165 passed, 1 skipped**; normal Windows full pytest **1175 passed, 2 skipped, 0 failed**. The added regression uses an isolated temporary Store and IPC endpoint, verifies JIT SAVE followed by a new Resolver read, and checks the credential is absent from the SQLite file as plaintext. No product source changed after that full run; final static/security gates are recorded in the current Progress and integration verification entries.

## 30. SQLite-scoped RC review — 2026-10-30

Scope reviewed: Windows-only local SQLite Store, explicit Provider selection, existing read-only database diagnostics, and authenticated read-only compatibility for legacy JSON. Excluded: Linux/other platforms, network filesystems, automatic migration or activation, production deployment, and real user Store operations. No product code changed in this review; only disposable-store acceptance regressions and this evidence ledger were updated.

`ISOLATED_OFFLINE_TEST` credential RC set: **152 passed, 1 skipped**. Added direct regressions for same alias/different Store isolation, wrong-password and damaged-source migration refusal, explicit CLI migration with `provider_switched=false`, explicit SQLite unlock/read after migration, rejection of a stale re-migration over an existing target after a new target write, required explicit backend on initialization, and Windows DACL checks for the parent directory, main database and live rollback journal. Existing CLI dispatch lifecycle test covers init/unlock/status/set/list/show/test/delete/rekey/lock; JIT SAVE followed by a new Resolver read is covered over an isolated Session endpoint. The one skip is POSIX-only permission-bit behavior unavailable on Windows. CLI tests call the real `cli.main` dispatcher with prompt functions injected and a disposable subprocess Session Agent; they do not automate the packaged `dba.exe` console prompt.

Current-tree Windows offline full pytest: **1181 passed, 2 skipped, 0 failed** in 50.66 seconds, using the project `.venv` and a task-local `.tmp/pytest-temp-rc` because the default Codex temporary directory denied cleanup with WinError 5. The failed first attempt was only a sandbox Temp cleanup denial; rerun passed. Skips: POSIX permission bits unavailable on Windows and opt-in real DB tests (`DBA_TOOLKIT_SYSTEM_TESTS` unset). This is not database E2E. The previously recorded authorized Windows-host product full regression **1175 passed, 2 skipped** remains separate evidence; no database E2E was repeated here.

Ruff PASS; strict MyPy PASS (74 source files); `dba validate` PASS (4 instances, 122 SQL scripts); `git diff --check` PASS (existing LF/CRLF notices only). Repository Secret Scanner PASS on Git-index content. Supplemental current-worktree scan checked 21 credential implementation/test/security/status files and found 0 findings; unrelated untracked files were not scanned. No scanner exceptions were broadened.

Four-engine diagnostic evidence was reviewed from the prior `WINDOWS_LOCAL_REAL_E2E` matrix and same-run report checks in `docs/VERIFICATION_V0_9_1_INTEGRATION.md`; it was not rerun. The evidence remains valid for its original monitor-provider and feature scope, but does not prove a new SQLite-backed credential can connect to any live database. No authorized disposable monitor credential was available for that integration check, and real user credential files/configuration were not read. Prior reports provide limited shared-field parity, not universal full JSON/HTML equivalence. Existing Oracle/MySQL PARTIAL findings and SQL Server Memory Grant/PostgreSQL WAL deferred gates remain unchanged.

Migration/readback, interrupted-target quarantine, wrong password, damaged source, source-byte preservation, manual explicit SQLite selection, and refusal to overwrite an existing migrated destination after a new write passed with disposable Stores. There is no automatic provider switch or rollback command. An operator can still explicitly select the old JSON source for a read; after SQLite writes, avoiding stale credential use depends on following the documented reconcile-both-Stores procedure. This is an explicit operator gate, not a cross-Store freshness detector. The SQLite README/design continues to disclose same-user tampering, parent-directory replacement, authenticated old-file rollback, older Toolkit downgrade, power-loss/storage-sync failures, network filesystems, and non-Windows support limits.

**RC assessment:** no confirmed P0/P1 product defect remains within the approved Windows-local, explicit-SQLite scope. The scoped acceptance evidence is sufficient for a **conditional close recommendation** for `CRED-MAINT-001`, limited to this RC boundary and subject to the explicit operator migration/rollback procedure and listed threat limits. At the time of this review, Backlog remained `IN_PROGRESS`; the subsequent user approval and current `DONE` status are recorded in §31. This is not production approval. No real user Store, `new-alias`, local high-privilege test config, database, commit or push was accessed or changed. SQL Server Memory Grant and PostgreSQL WAL deferred real-E2E gates remain unchanged.

## 31. CRED-MAINT-001 formal conditional closure — 2026-10-30

The user formally approved conditional closure of `CRED-MAINT-001`. The Backlog is the canonical status source and now records `DONE`. This means the approved implementation and SQLite-scoped RC review are complete; it does not mean all credential-storage threats are eliminated or that V0.9.1 is approved for production.

Accepted scope: Windows-local use of the encrypted SQLite Provider only when explicitly selected; `auto` does not select SQLite. The legacy JSON Provider remains an authenticated, target-bound read-only source. Existing Inventory `credential_alias` behavior and Windows Credential Manager are unchanged. No real user Store was migrated, enabled, opened, or modified; real credential migration remains unapproved. SQLite-backed credentials connecting to live databases were not verified.

The evidence is the RC review in §30, including 152 focused tests passed / 1 skipped, current-tree Windows offline pytest 1181 passed / 2 skipped / 0 failed, and the recorded Ruff, strict MyPy, `dba validate`, diff-check, indexed Secret Scanner, and scoped worktree scan results. These are previously recorded results, not tests rerun during this documentation-only update. The skipped tests and scanner coverage limits remain as described in §30.

Unresolved boundaries remain explicit: a same-user process with file or memory access may tamper with the Store; parent-directory replacement, authenticated rollback to an older Store, sudden power loss and underlying storage-sync failure, and network filesystems are not fully verified; an older Toolkit version may still write a legacy JSON Store. Manual selection of a stale legacy source after SQLite writes remains an operator risk. No automatic migration, provider switch, or silent fallback is approved.

The four-database E2E matrix remains valid only for its previously recorded credential/provider and feature scope; it does not prove SQLite-to-database credential integration. SQL Server Memory Grant and PostgreSQL WAL deferred acceptance gates in §§26–27 are unchanged. Package version remains `0.9.0`; no production release, commit, or push is approved or performed.

## 32. LAB-CREDENTIAL-OUTPUT-001 implementation intake — 2026-10-31

The user authorized implementation; Backlog status is `IN_PROGRESS`. Package version remains `0.9.0`. This is an intake record, not implementation completion.

Repository review: `docs/PROJECT_RULES.md` permits authorized lab-only test-driver credentials to be consumed in-process while forbidding their output or persistence. Searches across `dba/`, `tests/` and `scripts/` found no consumer of an external local lab Python configuration, no corresponding field schema, and no safe configuration reader. The existing `dba.logging.redact` function is output redaction, not an allowlist read boundary. Secret Scanner tests cover scanner no-echo behavior; credential CLI tests cover stored-password output, but neither exercises a local-config reader's normal and error paths.

No real external Python configuration was read, imported, executed, copied, changed, or printed. No database, Docker, Credential Store, Inventory credential, or real test identity was accessed. No product source or tests were changed and no tests are claimed for this intake review.

Implementation is held at the field-contract boundary: the task explicitly prohibits guessing fields, but the repository contains no consumer interface or field/nesting schema from which to derive an allowlist. A sanitized synthetic Python assignment/tree example preserving field names and nesting (all values replaced with non-secret placeholders), plus the consumer entry point and required non-sensitive fields, is needed. Once supplied, implementation can use a restricted AST/literal-only parser and test with synthetic secrets; no import/exec/eval path will be added.

Documentation gates for this intake: the official Secret Scanner returned PASS for Git-index content; a supplemental worktree scan of the four updated documentation files also returned PASS; `git diff --check` returned exit code 0 (Git emitted existing LF-to-CRLF advisory notices). No pytest, Ruff, or MyPy run is claimed because no product source or tests were changed and the task-specific interface is not yet defined.

The task remains `IN_PROGRESS`. The following remain unverified: allowlist contents against an actual consumer contract, output/error paths of that consumer, and any external consumer adaptation. No real-config or database E2E is required or authorized for this intake.
## 33. LAB-CREDENTIAL-OUTPUT-001 implementation evidence — 2026-10-31

The task remains `IN_PROGRESS` in the canonical Backlog. Package version remains `0.9.0`; this is implementation evidence, not task closure.

`dba.lab_config` now statically parses only a single literal `DATABASES` dictionary and accepts exactly the four reviewed database entries and their fixed schema. The public API exposes only `host`, `port`, `database`, and `user`; callers cannot select arbitrary fields. The password is available only through the dedicated connection profile as a `Secret` whose `str`, `repr`, and formatting are redacted and which rejects serialization. Parse, schema, and driver errors use fixed sanitized messages. The parser does not import or execute the configuration and writes no copy.

The authorized external consumers `incident_lab.py` and `oracle_lab.py` now use the reader. `incident_lab` obtains public fields and its Secret from one parse snapshot; its generic connector path passes the value only for connection setup and suppresses driver exception text. The Oracle SYSDBA path supplies the unwrapped value only in the driver password argument. Existing CLI flags are unchanged. Static inspection found no `--password` option in the prior consumer; programmatic `config_password` overrides remain supported and take precedence when supplied. `local_config.py` itself was not read, executed, copied, or modified during implementation.

Synthetic regression evidence: `tests/test_lab_config.py` plus `tests/test_repository_safety.py`: **38 passed**. The external Oracle lab driver unit suite: **4 passed**. Synthetic CLI/connection callback and Oracle driver-argument smoke checks passed without a database connection. Repository Ruff passed; import/unused-import checks passed for the external consumers. Strict MyPy passed for 75 `dba` source files. The full Ruff check on the external lab scripts still reports 15 pre-existing `E701` compact-statement style findings outside this change.

`git diff --check` passed with existing Git LF-to-CRLF advisories. The official Secret Scanner passed for Git-index/tracked content. Supplemental `_line_findings` scanning of 8 task source, test, documentation and external-consumer files reported 0 findings; the real local configuration was excluded. Neither result covers the real local configuration file, which was deliberately excluded.

No database, Docker, Credential Store, Inventory, real configuration value, or database secret was accessed. No full pytest run is claimed. Remaining acceptance work: review this implementation, close any relevant output path gaps found in review, and decide task status; no subsequent Backlog task has started.

## 34. LAB-CREDENTIAL-OUTPUT-001 final security validation — 2026-10-31

The task remains `IN_PROGRESS` in Backlog pending formal user acceptance. Package version remains `0.9.0`. All evidence below is `ISOLATED_OFFLINE_TEST` run from the Windows-local project `.venv`; no database or Docker connection was made.

The fixed public allowlist is exactly these 16 fields: `DATABASES.{mysql,pg,oracle,sqlserver}.{host,port,database,user}`. The ordinary `LabDatabaseConfig` has no password field. The dedicated connection-profile API returns a redacted `Secret` from the same parsed AST snapshot. Dynamic expressions remain rejected; no configuration module is imported or executed. Synthetic tests confirm the source bytes/hash do not change and no copy is created. The actual `local_config.py` was not read, imported, executed, copied, changed, or scanned.

The actual consumers are `mysql_lab.py` → PyMySQL `connect(password=...)`, `pg_lab.py` → psycopg `connect(password=...)`, and `sqlserver_lab.py` → pyodbc `connect(pwd=...)`; all three are called through `incident_lab.run()`'s `connect_with_lab_secret` boundary. `oracle_lab.py` routes both ordinary Oracle and SYSDBA connections through the same connection-boundary helper and passes the unwrapped value only as the driver password argument. Its disposable-actor password remains a `Secret` in the argument object; the Oracle CREATE USER failure and connection-close paths suppress raw driver details. Fake Driver tests verify each adapter receives the configured synthetic password and that failing drivers cannot leak it through returned errors, exception context, traceback, stdout, stderr, or logging.

The external lab scripts do not generate JSON or HTML reports. Therefore report-path coverage is `NOT_APPLICABLE`; no report system was added. The existing JSON serialization and HTML escaping tests cover only the public non-secret config projection, not an actual lab report. A nested intentionally failing pytest process verifies that sanitized driver errors do not expose its synthetic password in pytest failure output. Configuration parse failures also leave no raw parser/path exception in the sanitized exception context.

Evidence: task-targeted project tests (`tests/test_lab_config.py` and `tests/test_repository_safety.py`) **39 passed**; external Fake Driver/Oracle consumer tests **14 passed**; Windows full pytest **1203 passed, 2 skipped, 0 failed**. The two existing skips are the Windows-inapplicable POSIX permission-bit case and the opt-in real-database system test, which was not enabled. Repository Ruff passed; strict MyPy passed for 75 `dba` sources; `dba validate` passed for 4 configured instances and 122 SQL scripts. External consumer Ruff `I,F401` checks passed; full external Ruff still reports the same 15 pre-existing `E701` compact-statement style findings, with no unrelated formatting cleanup performed.

The official Secret Scanner passed for Git-index paths/content only. A supplemental scan of exactly 10 task source, test and documentation files in the worktree reported 0 findings; unrelated untracked files and the excluded real local config were not scanned by that supplemental pass. `git diff --check` passed with existing LF-to-CRLF advisory notices.

No database, Docker, Credential Store, Inventory, real credential, or real configuration value was accessed. No commit, push, or subsequent task occurred. The task remains `IN_PROGRESS` awaiting user acceptance.

## 35. LAB-CREDENTIAL-OUTPUT-001 formal conditional closure — 2026-10-31

The user formally approved conditional closure. The canonical Backlog status is `DONE`. This is a task close within the approved lab-configuration output scope; it does not assert that every downstream or live database path has been exercised. Package version remains `0.9.0`.

The validation evidence is the final run recorded in §34 and was not rerun for this documentation-only approval update: project-targeted pytest **39 passed**; external lab-consumer Fake Driver tests **14 passed**; Windows-local full pytest **1203 passed, 2 skipped, 0 failed**; repository Ruff, strict MyPy (75 `dba` sources), `dba validate` (4 instances / 122 SQL scripts), and `git diff --check` passed. External consumer `I,F401` Ruff checks passed; the 15 pre-existing external-script E701 findings remain unmodified. The official Secret Scanner covered Git Index content and passed; the supplemental scan covered exactly 10 named worktree files and reported 0 findings. This does not claim that unrelated worktree files were scanned.

Acceptance boundaries: all four database password consumer paths were tested with synthetic configuration and Fake Drivers, not real database connections (`ISOLATED_OFFLINE_TEST`, not `WINDOWS_LOCAL_REAL_E2E`). The external lab scripts have no JSON/HTML report-generation path (`NOT_APPLICABLE`); tests cover only public-field JSON serialization and HTML-escaped projection. The real `local_config.py` was not read, executed, copied, modified, or scanned. No database, Docker, Credential Store, Inventory, or real credential was accessed. No commit or push occurred.

Roadmap task order and phase boundaries are unchanged. The next Backlog item is `INVENTORY-CONNECTION-001`; it has not started and remains subject to separate user approval. This conditional closure does not approve production release or expand the task scope.

## 36. INVENTORY-CONNECTION-001 first-phase version_family output fix — 2026-10-31

The approved first phase removes rejected `version_family` input values from validation errors while preserving database type, field name, source location, and supported canonical families. Regression coverage checks ordinary invalid strings, password/token-marked strings, a raw synthetic secret, a wrong-type value, exception string/repr/context/traceback, CLI human and JSON output, stderr, and logging. Inventory and Versioning targeted tests passed: **86 passed**. Ruff passed for the touched source/test modules; strict MyPy passed for `dba/versioning.py`; `git diff --check` passed. The official Secret Scanner passed for Git-index content; a supplemental current-worktree scan of the three reviewed source/test files found 0 findings. This evidence does not cover unrelated worktree files.

No database, Docker, Credential Store, or real Inventory asset was accessed. No full test suite was run. The task remains `IN_PROGRESS`; this records only the first approved security fix, not overall Inventory task completion. No other Inventory policy or path-handling issue was changed.

## 37. INVENTORY-CONNECTION-001 lightweight Inventory security policy — 2026-10-31

At evidence capture this implementation slice was `IN_PROGRESS`; the Backlog remains the sole status source. Package version remains `0.9.0`. The later formal conditional closure is recorded in §38.

Directory-mode Inventory discovery now uses non-following traversal, prunes symbolic-link directories, and skips symbolic-link `.yaml`/`.yml` files with a concise warning. Explicit single-file loading retains its direct-read path. Deterministic traversal tests verified that linked targets were not passed to the YAML loader and that the explicit-file route remains available. The native Windows symlink test was skipped because the process token returned the documented privilege limitation (`WinError 1314`); this is an environment limitation, not a product pass for native symlink creation.

For connection-oriented CLI commands, `enabled: false` is checked immediately after Inventory lookup and before Resolver construction or any `get_credential` call. Fake Resolver and Fake Driver tests cover `test-connection`, `diagnose`, `collect`, permission-script generation, `check`, and `status`; each is rejected with `InstanceDisabled` and performs zero credential or driver calls. `list` and `show` continue to expose the disabled asset's basic state without resolving credentials. Existing top-level password/secret/token rejection and nested/unknown connection-option rejection are covered without changing the legal Inventory field set.

Evidence (`ISOLATED_OFFLINE_TEST`): focused new policy regressions **14 passed, 1 skipped**; Inventory and connection/credential suites **99 passed, 1 skipped**; Windows-local full pytest **1231 passed, 3 skipped, 0 failed**. The three full-suite skips were: native symbolic-link creation unavailable to this process token; opt-in real local system tests not enabled; POSIX permission-bit test not applicable on Windows. Ruff passed on the four changed Python files; strict MyPy passed for `dba/inventory.py` and `dba/cli.py`; `dba validate` passed against a temporary synthetic Inventory (1 synthetic asset, 122 SQL scripts); `git diff --check` passed with existing LF-to-CRLF advisories. The official Secret Scanner passed for Git-index content. A supplemental scan of the exact eight changed source, test, and planning files reported 0 findings; unrelated worktree files and real Inventory were outside that supplemental scope.

No real Inventory values, Credential Store, database, or Docker were accessed. No database permissions, aliases, versions, or product behavior outside this slice were changed. Broader connection-precedence and local asset-preservation review remains open. Native Windows symbolic-link behavior could not be exercised under this process token. No commit or push was performed. At this evidence checkpoint the implementation was awaiting user review; formal conditional closure is recorded in §38.

## 38. INVENTORY-CONNECTION-001 formal conditional closure — 2026-10-31

The user formally approved conditional closure. The canonical Backlog status is now `DONE`. This approval covers the first-phase `version_family` error-output fix and the approved lightweight policy scope in §37; it does not claim that the broader connection-precedence or local asset-preservation review has been completed. At this 2026-10-31 checkpoint, `PERMISSION-PREFLIGHT-001` was still `BACKLOG`; the task was separately approved and started on 2026-11-01, as recorded in §39.

The approval relies on the evidence recorded in §37; no tests were rerun for this documentation-only update. Recorded results: Inventory/connection targeted pytest **99 passed, 1 skipped**; Windows full pytest **1231 passed, 3 skipped, 0 failed**; Ruff, strict MyPy for the two changed source files, synthetic-Inventory `dba validate`, `git diff --check`, indexed Secret Scanner, and the eight-file supplemental worktree scan passed. The native Windows symlink integration test remains unverified because this process token lacked symlink creation privilege (`WinError 1314`); the deterministic simulated-link traversal test passed. Other full-suite skips were the opt-in real local system tests (not enabled) and POSIX permission-bit validation (not applicable on Windows).

No real Inventory, Credential Store, database, or Docker was accessed. No source, tests, inventory values, aliases, credentials, or database permissions were changed in this closeout. No commit or push occurred. This is conditional task closure, not a claim that native symlink E2E or the broader Inventory task goal is fully verified.

## 39. PERMISSION-PREFLIGHT-001 — unified deployment permission guide and runtime simplification — 2026-11-01

At the implementation evidence checkpoint, the approved scope was complete and the canonical Backlog status was `IN_PROGRESS`. The user subsequently approved conditional closure on 2026-11-01; Backlog now records `DONE`. Package version remains `0.9.0`. The sole substantive permission guide is [`docs/DATABASE_PERMISSIONS.md`](../DATABASE_PERMISSIONS.md), linked from README. It documents reviewed read-only grant requirements for SQL Server, Oracle, MySQL, and PostgreSQL, with version-specific syntax where required, merged duplicate grants, `dba_monitor` examples, optional evidence notes, and explicit cautions against broad administrative roles. SQL statements are grounded in the current Registry/collector sources; the guide distinguishes required connection/diagnostic reads from optional evidence and configuration prerequisites.

Runtime Permission Registry, per-feature grant/readiness calculations, missing-grant results, generated GRANT SQL, and the `permissions` CLI command are removed. On database access denial, the runtime preserves sanitized vendor error evidence and points to the guide; it does not infer an unobserved missing grant or execute GRANT/REVOKE. Connection/version detection, reviewed read-only SQL enforcement, bounded collectors, and narrowly scoped dangerous-account/write-capability checks remain. The latter checks classify unsafe monitoring identities and are not feature-permission readiness.

Regression evidence (`ISOLATED_OFFLINE_TEST`): focused four-engine permission/adapter/preflight/diagnostic/report/plan suites **422 passed** after fixes. Final Windows-local offline full pytest: **1154 passed, 3 skipped, 0 failed**. Skip reasons: POSIX permission bits are unavailable on Windows; Windows symlink creation was unavailable to this process token; opt-in real local system tests were not enabled. No database E2E was attempted. Ruff passed on `dba` and `tests`; strict MyPy passed for 75 source files; `dba validate` passed against the example Inventory (11 assets, 118 SQL scripts); `git diff --check` passed with the worktree's existing LF-to-CRLF advisory notices. Secret Scanner passed for Git-index paths/content only; this scan does not cover all untracked or unstaged working-tree files. No expanded worktree scan is claimed.

The first full pytest attempt used the sandbox-managed system Temp location and encountered Windows access-denied errors along with stale tests after the approved API/CLI simplification. Tests were rerun with `TEMP`/`TMP` directed to the ignored project `.venv/test-tmp`; after aligning obsolete test calls and future SQL Server 2025 unverified routes with the current fail-closed contract, the final complete suite passed. No failing test was skipped or assertion weakened to obtain the result; the three existing platform/opt-in skips above remain.

Authorization text was statically reviewed against SQL Registry and version profiles. It has not been executed or independently accepted against every supported vendor release. Cross-version permission behavior and all live permission-denial scenarios remain unverified. No database, Docker, real Inventory, Credential Store, or credentials were accessed; no grants were changed. No commit or push occurred. Roadmap order is unchanged; `SQL-REGISTRY-SAFETY-001` remains `BACKLOG` and has not started pending separate approval.

### Formal conditional closure — 2026-11-01

The user approved conditional closure. The canonical Backlog status is `DONE`. This closeout only synchronized documentation and ran `git diff --check`; the recorded test and quality evidence above was not rerun. Authorization SQL remains statically checked against current source and version routing only, not executed against all supported database versions. No production release or blanket cross-version permission verification is implied.
## 40. SQL-REGISTRY-SAFETY-001 — reviewed SQL provenance and bounded execution — 2026-11-01

Implementation is in progress pending user acceptance; Backlog remains the canonical status source and records `IN_PROGRESS`. The Registry now validates bounded UTF-8 SQL and YAML source sizes, uses duplicate-key-safe metadata parsing, restricts timeout to 1–60 seconds, rows to 1–10,000, and decoded result payload to at most 16 MiB. Metadata includes an optional `max_result_bytes` with that default ceiling. Runtime authorization rereads the registered SQL and metadata, verifies the digest and parsed script still match the issued capability, and reruns the read-only static check. Issued script capabilities are weakly held and released when callers drop them, avoiding unbounded retention from repeated authorization checks.

The SQL static check now rejects a second SQL statement after a terminator while allowing semicolons in quoted literals/identifiers, supported PostgreSQL dollar quotes, Oracle alternative quoting, and trailing comments. SQL Server, MySQL, PostgreSQL, and Oracle adapters use the shared bounded fetch helper, record returned payload bytes and the configured byte ceiling in internal audit events, and retain cursor cleanup in their existing `finally` paths. The byte ceiling measures decoded cell payload returned to the Toolkit; it cannot prevent a driver from materializing a single large cell internally before the client-side check. This is a bounded-output control, not a wire-size or server-work guarantee. Static review remains an additional safeguard and does not replace least-privilege database accounts or the database's own access controls.

`ISOLATED_OFFLINE_TEST` evidence: `tests/test_toolkit.py` plus SQL Server, MySQL, PostgreSQL, Oracle, versioning, and Oracle Top SQL regression suites: **371 passed**. Ruff passed on the six changed source modules and six related test files. Strict MyPy passed on the six changed source modules. `dba validate` passed for the selected local Inventory: **4 assets, 118 SQL scripts**. No database or Docker was contacted. `git diff --check` is recorded after this documentation update. The initial Ruff pass found only import ordering in `tests/test_toolkit.py`; that import block was sorted and the final Ruff run passed. No assertions were weakened and no tests were skipped for this work.

The confirmed gaps addressed were the absence of a general one-statement gate (allowing multiple read-only statements in one reviewed script), missing universal upper bounds for Registry timeout/row/returned-byte budgets, and strong retention of every issued script capability. Remaining boundaries: the decoded-byte cap is applied after driver fetch/materialization; no claim is made about maximum server-side work or transport bytes. No new SQL or database permission was added, and the DATABASE_PERMISSIONS guide remains unchanged.

### Final acceptance verification — 2026-11-01

The user requested a current-source full-suite check. The first Windows run reported **1159 passed, 3 skipped, 5 failed**. One failure was a test writing under the sandbox-managed Windows Temp location (`PermissionError`); four assertions still expected the previous bulk `fetchmany(101/301)` call size. The latter covered Active SQL result ranking and the exact-300 boundary, Memory Grant waiting/granted rows, and SQL Server Top SQL. The shared fetcher intentionally requests one row per pull so it can apply the decoded-byte budget before retaining each subsequent row. Those four call-shape assertions were updated to require one-row pulls; the result counts, truncation/completeness, identity, ranking, and data assertions were retained.

The complete suite was rerun with `TEMP` and `TMP` set to `.venv/test-tmp`: **1164 passed, 3 skipped, 0 failed**. The three skips are the opt-in real local system test (not enabled), POSIX permission-bit validation (not applicable on Windows), and native Windows symlink creation unavailable to this process token. This is offline evidence; no database E2E was attempted. Ruff passed on the six Registry/security/adapter source modules and nine related test files; strict MyPy passed on the six source modules; `dba validate` passed (4 configured assets, 118 SQL scripts); and `git diff --check` passed. The validation process was Win32, ran from the project directory and used the project `.venv`; no database, Docker, Credential Store or production asset was accessed. The first-run TEMP failure did not reproduce with the project-local temp directory.

The four database adapters remain on reviewed Registry SQL, execute the same reviewed script text whose source and metadata digest were revalidated immediately before execution, and use the bounded fetch path. Static checks prevent multiple statements and reject known modifying tokens; they supplement the reviewed-script boundary and database-side least privilege rather than prove arbitrary SQL semantics. The runtime result rows are capped by row and decoded-payload budgets; a driver can still materialize one oversized cell before Toolkit checks it. No actual business-data or schema modification risk was observed in the current-source review or test run. Task state remains `IN_PROGRESS` pending user acceptance; no following Backlog task started and no commit/push occurred.

## V091-P5-WORKBENCH-001 — approved Slow Diagnose HTML integration — 2026-11-02

The user approved integrating `docs/templates/slow_diagnose_report_template.html` as the sole visual and interaction reference. Hallmark audited the approved template before implementation and the generated synthetic page afterward. The accepted deep navy incident banner, red/green status system, KPI cards, root assessment, blocking chain, manual reference area, collapsible evidence, searchable session appendix, copy, expand/collapse, print/PDF and responsive structure are retained. The approved source file is design input only; the packaged `dba/reporting/templates/slow_report.html` is a dynamic, self-contained runtime template. No demo instance, fixed session, sample timestamp or canned incident evidence is supplied by the renderer.

The shared `ReportModel`, `ReportPresenter`, `HumanReportPolicy`, and `HtmlReportRenderer` remain the reporting path. Slow rows get a red label only when their native session identity is in the verified blocker graph, green only when the relevant blocking evidence is complete and confirms no relation, and amber when collection, truncation, or identity leaves membership unknown. MySQL PROCESSLIST_ID and THREAD_ID, PostgreSQL PID, SQL Server session IDs, and Oracle INST_ID/SID/SERIAL# remain distinct. Current SQL and recent SQL stay separate. Reference commands are rendered as escaped text only; no execution route was added. Collectors and SQL Registry were not changed.

`ISOLATED_OFFLINE_TEST`: targeted HTML/report-presentation tests passed **29/29**; current full pytest passed **1175 passed, 3 skipped, 0 failed**. Ruff passed on the changed report and test files; strict MyPy passed for 75 `dba` source files; `dba validate` passed (4 configured assets, 118 SQL scripts). `git diff --check` was run after the evidence update. Generated HTML passed content/escaping, absence-of-demo-data, self-contained asset, responsive-CSS, action-text-only, and state-contract checks. A generated page was visually reviewed in Chrome at 1280px and compact 500px; semantic text/token color pairs meet WCAG AA contrast. Browser emulation at 320–414px was unavailable; the source breakpoints and narrow layout were inspected, but those exact widths are not claimed visually verified.

`WINDOWS_LOCAL_REAL_E2E`: **NOT EXECUTED for the integrated Renderer**. The selected lab assets were confirmed as enabled test assets, but Credential Session status was `LOCKED`; the managed execution context did not provide a completed authorized connection check. No database failure is inferred and the connection attempt yielded no valid E2E result. Existing four-database Slow Diagnose acceptance evidence remains historical evidence for its original code revision and does not verify this new HTML renderer. No simulator workload was started and no database, simulator object, Inventory, Credential Store, or permission was changed.

Remaining acceptance items: current-HEAD JSON/HTML parity from four real lab collectors; real four-database Slow report rendering under the authorized Toolkit monitor accounts; browser visual inspection below 500px; automated browser interaction checks for search/copy/expand/print; and any cases not represented by the current deterministic fixtures. Backlog status is `IN_PROGRESS`; the user approved the accumulated development checkpoint commit and push on 2026-11-02. No next task has started. Package version remains `0.9.0`.

## OFFLINE-RUNTIME-001 — Windows portable runtime and independent driver management intake (2026-11-02)

The user authorized implementation of a Windows x64, fully offline end-user ZIP with explicitly replaceable database drivers, protected user data, setup guidance, and manual Windows build workflow. This work is tracked under the existing OFFLINE-RUNTIME-001; Roadmap task order and package version 0.9.0 are unchanged. Initial source review found an existing local-only venv bootstrap and runtime manifest, dynamic `importlib` loading for all four relational drivers, no staged wheelhouse and no portable build workflow. An isolated offline probe copied the installed pure-Python PyMySQL module into a temporary external directory and verified it loads from that external path under Python `-S`. This is a module-path proof only, not portable-runtime, binary-driver, package-install, or database E2E evidence.

### Implementation checkpoint — 2026-11-02

The approved implementation adds an explicit local driver manager and CLI, routes all four database adapters through its selected package loader, and adds a CPython 3.13 Windows x64 portable build, verification, packaging, upgrade, driver bundle, setup guide, hash-pinned locks, and manual workflow. The Backlog remains `IN_PROGRESS`; package version remains `0.9.0`.

`ISOLATED_OFFLINE_TEST`: `tests/test_driver_manager.py` plus `tests/test_sqlserver.py` passed **102 tests**. After aligning the legacy SQL Server lazy-import test with the new shared loader, the Windows-local full offline pytest passed **1186 passed, 3 skipped, 0 failed**. Ruff passed for `dba`, `tests`, `app`, and the two portable Python utilities; strict MyPy passed for **76 source files**; `dba validate` passed against the example Inventory (**11 assets, 118 SQL scripts, 0 warnings**); the four PowerShell deployment scripts parsed successfully; and `git diff --check` passed. No database, Docker, real Inventory, Credential Store, or credential was accessed.

Portable artifact construction was attempted with the local CPython 3.13.17 embeddable runtime and the prepared Windows x64 wheelhouse. The official-runtime SHA check and offline core dependency installation passed, but the build safely stopped while creating the MySQL driver bundle: the PyMySQL wheel metadata reports `1.1.3` while its module reports `__version__ = 2.2.8`. The downloaded wheel SHA256 matches the [official PyPI artifact record](https://pypi.org/project/PyMySQL/1.1.3/), so this mismatch is in the published package contents, not a local wheel alteration. The package builder rejected the mismatch; no check was weakened. This matches the separately tracked `MYSQL-DRIVER-PROVENANCE-001` issue. Therefore a complete ZIP, package smoke test, and upgrade/rollback test are **not verified**. The first attempt also hit the restricted system TEMP directory; retrying with a project-local TEMP passed that stage and reached the driver provenance check.

`WINDOWS_LOCAL_REAL_E2E`: **NOT EXECUTED**; this packaging task does not require database access. Four-database diagnostic E2E evidence is unchanged. Historical review ZIPs, patch files, and Top SQL JSON outputs remain local-only and are excluded from the source commit. The portable work is a development checkpoint, not a completed offline deployment acceptance or release claim.

## OFFLINE-RUNTIME-001 — final Windows portable package evidence (2026-11-02)

The PyMySQL mismatch was reproduced with the exact wheel in a new CPython 3.13.17 x64 runtime and an isolated target. The wheel SHA256 is `8164ba62c552f6105f3b11753352d0f16b90d1703ba67d81923d5a8a5d1c5289`; its METADATA and `importlib.metadata.version("PyMySQL")` are `1.1.3`. Import resolves to that wheel's `pymysql` module, whose `VERSION`/`VERSION_STRING` identify release `1.1.3`, while `__version__` and `version_info` expose the documented mysqlclient-compatibility identity `2.2.8`. `packages_distributions()` maps `pymysql` to `PyMySQL`. The same dual identity appears in the project environment, so this was not a local module collision. The [official PyPI 1.1.3 release record](https://pypi.org/project/PyMySQL/1.1.3/) carries the matching wheel hash. The builder had compared the compatibility alias directly with the distribution release and rejected a valid wheel. The fix now validates the release fields against distribution metadata and separately validates the compatibility alias; it does not disable version checks.

Implementation files: `dba/driver_versions.py` (new shared release-identity validator); `dba/driver_manager.py`; `deployment/windows/write_driver_bundle.py`; `deployment/build_portable.ps1`; `deployment/windows/PORTABLE_README.md`; and `tests/test_driver_manager.py`. The Oracle driver range was represented as separate safe manifest labels so the bundle validator accepts and retains both compatibility notes. The build includes the portable verifier/upgrader and README markers for user data directories.

The final CPython 3.13.17 Windows x64 package is `dist/portable-final-20261102-r5/DBAToolkit-0.9.0-Windows-x64-Portable.zip`, **79,417,598 bytes**, SHA256 `13fdf95e0b054b6bb9165d552736d4fb3d27c30a7232127b05cb8097e4ac020d`; the adjacent `.sha256` sidecar matched. This artifact is a local build output, not committed or pushed. Its bundled driver check reported PyMySQL 1.1.3, psycopg 3.3.6, oracledb 3.4.2, and pyodbc 5.3.0 as managed and `READY`; the host's ODBC Driver 17 was selected. This is client package loading only, not database-version compatibility or database E2E. The package includes the full Python runtime, SQL Registry, examples, setup docs and four driver bundles. SQL Server's Microsoft ODBC Driver remains separately installed by the user; Oracle defaults to Thin mode, with Thick/Instant Client guidance documented. The README advises keeping the extracted folder path reasonably short because deep paths can exceed Windows native-module path limits.

`WINDOWS_LOCAL_REAL_E2E` (portable application smoke only; no database connection): the final r5 ZIP was extracted to a short path. `verify_portable.ps1` passed bundled runtime `--version`, `validate` (118 scripts, no warnings), and four-driver checks. The no-argument launcher path (the double-click entry point) displayed CLI help and exited successfully with EOF supplied to its final pause; `启动.bat --version`, sample Inventory `list --json` (four disabled synthetic demo assets), bundled `validate --json`, and `driver check --json` also succeeded with `PATH` restricted to Windows `System32`; `where.exe python` found no Python on that PATH, and launched commands used only the ZIP's `runtime/python.exe`. A synthetic SlowReport fixture generated a **38,013-byte** self-contained HTML report from this extracted runtime; it has inline CSS/JS and no external references. No internet-dependent operation was invoked. Build dependency installation used only the local wheelhouse with `--no-index`.

Driver lifecycle tests on the extracted package passed for all four drivers. For each database, a compatible synthetic local ZIP was installed, the newly selected driver loaded as `READY`, an intentionally inconsistent version bundle was rejected without changing active state, rollback restored the baseline version, and the other three driver states remained unchanged. Baselines were PyMySQL 1.1.3, psycopg 3.3.6, oracledb 3.4.2, and pyodbc 5.3.0. No real driver ZIP or database credentials were used.

The final r5 portable upgrade test used a short disposable installation root and synthetic files only. The upgrade replaced application/runtime launch components while preserving the hashes of six synthetic user files: two config files, a SQLite file containing only synthetic ciphertext marker data, two report files, and one log file. It also preserved all **5,331** files under `drivers/` and `driver-packages/`; a program backup was retained. An earlier staging attempt used an artificially deep `dist` path and failed driver imports because nested Windows paths exceeded the practical legacy path limit; it stopped before replacing the installation. The short-root retry passed. Avoid deeply nested install paths on Windows.

`ISOLATED_OFFLINE_TEST`: `tests/test_driver_manager.py` passed **14 tests**; the current Windows-local full pytest passed **1189 passed, 3 skipped, 0 failed** in 53.14 seconds. Skip reasons: POSIX permission-bit validation is unavailable on Windows; native symlink creation was denied to the process token; the real local system test is opt-in and was not enabled. Ruff passed for `dba`, `tests`, `app`, and `deployment`; strict MyPy passed for **77** `dba` source files; `dba validate` passed against the synthetic example Inventory (4 assets, 118 SQL scripts, zero warnings); PowerShell parser checks passed for all seven deployment PowerShell scripts; `git diff --check` passed. The project Secret Scanner passed for Git-index paths/content. A supplemental worktree scan passed for these nine changed task files: driver manager/version helper, portable build script, driver setup README, bundle writer, driver-manager tests, and Backlog/Acceptance/Progress. Neither scan covered unrelated untracked review archives or diagnostic JSON. No scanner allowlist was changed.

The host still has system Python installed. The PATH-restricted run proves the portable launch path did not resolve Python through PATH, but a genuinely Python-absent Windows machine/Sandbox was unavailable; that strict condition remains **NOT VERIFIED**. The host network was not forcibly disconnected; the tested build/runtime paths did not invoke network access, and build pip resolution was explicitly `--no-index`. `WINDOWS_LOCAL_REAL_E2E`: no database diagnostic E2E was run because this task did not require one. No database, Docker, Credential Store, real Inventory or real credentials were accessed or modified. No source artifacts were committed or pushed; pre-existing untracked review ZIPs and diagnostic JSON files were left untouched and are not part of the portable ZIP.

Backlog status is `E2E_PENDING`, not `VERIFIED` or `DONE`, because the strict no-system-Python acceptance condition is still unverified. Package version remains `0.9.0`; Roadmap order and other task states are unchanged.

## PORTABLE-UPDATE-001 — Full/Update packages and Chinese manuals (2026-11-02)

The approved release documentation is present in both Full and Update archives, the standalone `使用说明.md` asset, and the Windows Release workflow asset list. The five required UTF-8 manuals are: `使用说明.md`, `docs/用户操作手册.md`, `docs/DRIVER_SETUP.md`, `docs/UPDATE_GUIDE.md`, and `docs/故障排查.md`. The package verifier checks archive contents, manual presence/Chinese text, exact release checksums, and rejects bytecode caches. Local links and representative commands were checked against the bundled CLI help. The CLI's no-argument launcher, `--version`, help, sample Inventory listing, packaged validation, four driver checks, and a synthetic self-contained HTML report were exercised from the extracted Full package.

Current candidate artifacts in `dist/portable-release-20261102-r3/`:

| Artifact | Size | SHA256 |
|---|---:|---|
| `DBAToolkit-0.9.0-Windows-x64-Full.zip` | 99,929,060 bytes | `7fcc6c6027b9dffeb16ec3c636017345425394172101853aa954dae549217c74` |
| `DBAToolkit-0.9.0-Windows-x64-Update.zip` | 45,426,426 bytes | `e6c7bc9fc0c972d94ae93fc09e6e02fd6313db24cd158aacfd2fb92d53c97928` |
| `使用说明.md` | 2,929 bytes | `0be985858e1cbb4319dfce41a4b963ea1cd1e27cba7d85d0ba00f2d75d74bec9` |

`WINDOWS_LOCAL_REAL_E2E` (package-only smoke, no database): `verify_release.ps1` validated Full, Update, standalone manual, and SHA256SUMS; `verify_portable.ps1` used only the bundled CPython 3.13 runtime and reported 118 Registry scripts valid and all four Python drivers READY. With PATH restricted to Windows `System32`, `where.exe python` found none; the launcher, CLI version/help, and disabled example Inventory listing worked. This is PATH isolation only: the host has Python installed, so a genuinely Python-absent Windows host was not tested. A synthetic report rendered to 30,198 bytes with zero external asset references. No network was forcibly disconnected; build dependency resolution used the local wheelhouse with `--no-index`, and the tested runtime path made no network-dependent call. No GitHub Release/tag was created or published.

`ISOLATED_OFFLINE_TEST`: `tests/test_portable_update.py` passed **14 tests**. Current full Windows-local pytest passed **1203 passed, 3 skipped, 0 failed**. Ruff passed for the repository; strict MyPy passed for 79 source files; `dba validate` against the example Inventory passed (11 assets, 118 SQL scripts); all 15 deployment PowerShell scripts parsed; official Secret Scanner passed for Git-index paths/content; an additional worktree scan of the named portable source, test, and documentation files reported no findings. `git diff --check` passed with Git's existing LF-to-CRLF advisories. The three pytest skips remain the Windows-inapplicable POSIX permission check, native symlink creation unavailable to the current process token, and opt-in system test not enabled.

No database, Docker, real Inventory, Credential Store, `local_config.py`, or credential was accessed. Historical `dist` candidates were not deleted. The package's portable driver installation/rollback and synthetic user-data preservation evidence remains in the preceding OFFLINE-RUNTIME-001 §43 record; current r3 ZIP checks verify the current documentation and archive contents. Package version remains `0.9.0`. Backlog `PORTABLE-UPDATE-001` is `E2E_PENDING`; OFFLINE-RUNTIME-001 remains `E2E_PENDING`. Roadmap order is unchanged.

## 45. OFFLINE-RUNTIME-001 / PORTABLE-UPDATE-001 — final portable acceptance (2026-11-02)

This acceptance rechecked the requested r3 artifacts and, after finding an Update preflight defect, rebuilt a corrected r4 candidate. The r3 files remain untouched. The final candidate is r4; package version remains `0.9.0`. No database, Docker, real Inventory, Credential Store, or credential was accessed.

### Environment and artifact identity

The Windows host reports build `10.0.26200.9711`, PowerShell `7.6.5`, and a 64-bit process. Windows Sandbox was not available to this process; no usable Hyper-V/VirtualBox/VMware VM command was found. The read-only DISM feature query required elevation and was not elevated. The host has Miniconda Python installed. A PATH restricted to Windows `System32` was used for package smoke checks, but this is not a genuinely Python-absent environment.

| Artifact | Bytes | SHA256 | Result |
|---|---:|---|---|
| r3 Full ZIP (tested) | 99,929,060 | `7fcc6c6027b9dffeb16ec3c636017345425394172101853aa954dae549217c74` | PASS package structure and bundled-runtime smoke |
| r3 Update ZIP (tested) | 45,426,426 | `e6c7bc9fc0c972d94ae93fc09e6e02fd6313db24cd158aacfd2fb92d53c97928` | FAIL before replacement; staging validation wrongly depended on Inventory absent from Update payload |
| r4 Full ZIP (corrected candidate) | 99,926,205 | `afef502bfd915254b9d0ff176a8e2c908766b0a633b964aeab95fd1f546f4894` | PASS release verifier and package smoke |
| r4 Update ZIP (corrected candidate) | 45,425,278 | `792e16c0d0246016f345e6b7190393e1f8ca727dbd2dbe920463180777722524` | PASS release verifier and disposable install upgrade |
| r4 standalone `使用说明.md` | 2,929 | `0be985858e1cbb4319dfce41a4b963ea1cd1e27cba7d85d0ba00f2d75d74bec9` | PASS included in SHA256SUMS |

Both r4 manifests identify Full/Update `0.9.0`, build `manual-20261102-r4`, minimum supported version `0.9.0`, no maximum, CPython `3.13`, x64, and schema version 1. r2 Full (`0.9.0`, build `manual-20261102-r2`) was used only as a disposable same-version/different-build updater baseline. This does not verify a formal cross-version upgrade. The release verifier passed for r4 Full, Update, manuals, and checksums.

### Acceptance results

| Check | Result | Evidence / boundary |
|---|---|---|
| Truly system-Python-absent Windows | NOT_EXECUTED | No clean Sandbox/VM was available; host Python remains installed. System32-only PATH is not equivalent. |
| Fully disconnected network | NOT_EXECUTED | Host networking was left unchanged. Package smoke made no network-dependent call, but physical/network isolation was not proven. |
| r4 Full bundled runtime and launch path | PASS, host-only | `verify_portable.ps1`, no-argument `启动.bat` path, `dba.bat --version`, `driver check`, sample Inventory list/validate passed with PATH restricted to System32; 118 SQL scripts validated. Not a clean-machine acceptance. |
| `doctor` command | NOT_EXECUTED | Not run to avoid any possible access to the real user Credential Session. |
| Synthetic Slow HTML report | PASS | r4 bundled runtime rendered a synthetic report; on-disk file was 30,509 bytes with inline assets and no external HTTP(S) asset references. |
| Four packaged Python drivers | PASS, import/check only | MySQL, PostgreSQL, Oracle, and SQL Server driver checks were READY. This is not database connectivity evidence; SQL Server still requires the user-installed Microsoft ODBC driver. |
| Independent driver replacement/rollback | PASS, synthetic | On a clean r4 extraction, a synthetic MySQL package loaded READY, rollback returned the active state to the bundled baseline, and PostgreSQL/Oracle/SQL Server status/version did not change. No database was contacted. |
| r3 Update preflight | FAIL, diagnosed | `portable_entry.py` changes CWD to the staged payload; Update intentionally omits user `config/`, but preflight ran `validate` without an explicit Inventory. It therefore failed before program replacement. |
| r4 Update same-version build transition | PASS, internal only | Disposable r2 Full upgraded to r4 Update. Staged validation now uses a temporary empty synthetic Inventory outside the payload; no install Inventory is copied or read. Program backup was created. |
| Same-build update protection | PASS | Reapplying r4 to its installed r4 build was rejected as already installed; program and synthetic user-file hashes stayed unchanged. |
| Synthetic user data and custom driver preservation | PASS | Eight files (Inventory, site config, synthetic SQLite ciphertext-marker DB, HTML, JSON snapshot, custom MySQL driver state/package/metadata) had identical SHA256 before and after r4 update. |
| Manual rollback | PASS | Rollback restored r2 build and the CLI/Registry remained usable; synthetic user data and custom MySQL driver hashes were unchanged. |
| File-locked replacement recovery | PASS, disposable | An exclusive handle on the old entry point forced failure after runtime movement; the update retained a backup/quarantine, restored r2, and program/user-file hashes were unchanged. |
| Corrupt ZIP / missing manifest | PASS, actual r4-derived copies | Truncated ZIP and repacked copy without `update-manifest.json` were rejected; program and synthetic user hashes were unchanged. |
| Payload SHA mismatch | PASS, actual r4-derived copy | A valid repacked ZIP with one payload byte changed but the original file manifest was rejected before replacement; hashes stayed unchanged. The external SHA256SUMS comparison remains an operator preflight, as documented; it is not a publisher signature. |
| Unsupported version / required file missing / startup check failure | PASS, Windows PowerShell regression | Covered by `tests/test_portable_update.py` against disposable synthetic update packages; no test was skipped or weakened. |
| Chinese manuals | PASS, archive validation | Both r4 packages and standalone quick start passed release checks for required UTF-8 Chinese manuals and their packaging. The release verifier checks required files and representative package metadata. |
| Doctor, real credentials, real DB, Docker | NOT_EXECUTED | Explicitly excluded from this acceptance. |

The r4 Update correction is in `deployment/windows/update-tools/apply-update.ps1`: staged Registry validation receives an explicit temporary `databases: []` Inventory in the staging root and deletes it after validation. It never copies the user's Inventory into staging. `tests/test_portable_update.py` now enforces that the updater supplies this disposable Inventory. The r3 archive failed the first real-package attempt as described; r4 was rebuilt and its actual Update path passed.

## 46. Windows Actions pytest temporary-directory failure (2026-11-03)

The Windows package workflow's pytest failure was reproduced locally with the same `pytest.ini` behavior: `pyproject.toml` set `--basetemp=.tmp/pytest`, but a clean checkout has no `.tmp` parent. A test requesting `tmp_path` then failed while pytest attempted to create the nested base directory (`FileNotFoundError`, Windows `WinError 3`). A trivial test without `tmp_path` did not trigger the failure, matching the fixture-dependent CI symptom. `tests/conftest.py` did not set `TEMP`, `TMP`, or `basetemp`; the repository-relative path came from `pyproject.toml`.

The fixed configuration removes the repository-relative basetemp. The Windows Actions test step validates `RUNNER_TEMP`, creates dedicated pytest and process-temp directories beneath it, confirms both exist, sets `TEMP` and `TMP` in the PowerShell process before pytest (so child processes inherit them), and passes the prepared pytest directory via `--basetemp`.

Local verification: a minimal clean-checkout fixture reproduced the prior failure (`tmp_path` setup raised `FileNotFoundError [WinError 3]` when `.tmp` was absent); the same repro without `tmp_path` passed. After the fix, the new CI-contract regression and repository safety tests passed (**18 passed**), the full Windows pytest suite passed (**1204 passed, 3 skipped, 0 failed**), Ruff passed, strict MyPy passed for 77 source files, and the workflow's PowerShell test block parsed successfully. These are local results, not a GitHub Actions run. Fix Commit `ac813ab8f0c4c6639cb9a881de514a3735ded318` was pushed to `origin/main`. The Actions run was not verifiable in this session: the GitHub API request returned EOF, so no Actions pass/fail is claimed.

### Automated and static checks

`ISOLATED_OFFLINE_TEST`: `tests/test_portable_update.py` — **14 passed**; Windows-local full pytest — **1203 passed, 3 skipped, 0 failed**. The skips were the POSIX permission-bit check (not applicable on Windows), native symlink creation unavailable to this process token, and opt-in system/real-database test not enabled. Ruff passed repository-wide; strict MyPy passed for 77 `dba` files; `dba validate --inventory examples/inventory` passed (11 assets, 118 SQL scripts, 0 warnings); all 15 deployment PowerShell scripts parsed; `git diff --check` passed (Git emitted existing LF-to-CRLF advisory notices).

Secret Scanner was **NOT_EXECUTED**: no project scanner script or scanner executable was available in this execution context. No scanner PASS is claimed. The real GitHub Release, tag, external checksum distribution, no-Python host, disconnected-network test, and formal `0.10.0` cross-version Update test remain unverified. No version, Roadmap order, or task status was changed; no commit, push, or release was performed.

## 47. BUG-WINDOWS-CI-FAILURES-001 — 198-failure root-cause repair (2026-11-03)

### Failed-run identity and classification

Input evidence was `github_actions_failed.log` from GitHub Actions commit `6ff0fba0f7b629b5fb347c6c5fdad515b618b471`, Windows Server 2025, CPython 3.13.17 x64, PowerShell 7 workflow, and Windows PowerShell 5.1 updater subprocesses. The run reported **198 failed, 1007 passed, 2 skipped**. The failed-node list and tracebacks classify exactly as follows:

| Root cause | Failed tests | Evidence and causal chain |
|---|---:|---|
| Registry/Fake Driver newline mismatch | 170 | Git clean checkout supplied CRLF SQL. `SqlScriptRegistry` intentionally reads exact UTF-8 bytes and preserved CRLF, while SQL Server/MySQL/PostgreSQL Fake Drivers used `Path.read_text()`, which normalized Windows newlines to LF. Exact-string dictionary lookup raised `KeyError`; adapter redaction converted that test-double exception into the observed sanitized QueryFailed/Preflight messages. The error detail contained SQL text only, not a password, connection string, or credential value. Production Preflight and Registry authorization were functioning and were not bypassed. |
| SQLite Credential Store Owner initialization | 21 | Initialization protected the DACL but did not set Owner. The hosted Runner token could create the directory/file with a default Owner other than the current user or SYSTEM, so the immediately following unchanged strict check rejected it at `owner_text.value not in {sid, "S-1-5-18"}`. |
| Portable Update hash cmdlet availability | 5 | Windows PowerShell 5.1 inherited the workflow process environment/module path and could not resolve `Get-FileHash`; failure occurred before payload replacement. SHA256 policy itself was correct and remains mandatory. |
| Missing IANA timezone data | 1 | Standard Windows Python had no `Asia/Shanghai` data and the editable `.[dev]` install did not install `tzdata`, although the portable CPython lock already pinned it. |
| Local Inventory test dependency | 1 | `test_doctor_uses_inventory_and_runtime_info_does_not_load_it` referenced ignored local `config/inventory.yaml`, which is intentionally absent from a clean checkout. |
| **Total** | **198** | Five independent environment/fixture defects; no evidence supported weakening database, credential, update, or Registry safety policy. |

### Repairs and preserved boundaries

- The three Fake Drivers build their SQL lookup maps from `read_bytes().decode("utf-8")`, matching the Registry's exact source text. A deterministic regression copies each engine Registry to a temporary CRLF tree and proves the formal Registry-issued script matches the Fake Driver key. No Adapter, Preflight, permission policy, Registry digest, reviewed SQL, bind contract, timeout, row bound, or cleanup behavior changed.
- Windows SQLite initialization SDDL now includes `O:<current-user-SID>` and applies `OWNER_SECURITY_INFORMATION` together with the existing protected DACL. The accepted Owner set remains exactly current user or SYSTEM; DACL trustees remain current user and SYSTEM. It does not make ACL validation optional or accept Administrators/Users.
- `update-bootstrap.ps1`, `update-tools/apply-update.ps1`, and `upgrade-portable.ps1` compute SHA256 with `System.Security.Cryptography.SHA256` and compare the full 64-hex digest. No integrity comparison was removed. `restore-update.ps1` has no hash calculation to replace. Direct synthetic transitions `0.9.0 -> 0.10.0`, `0.9.0 -> 0.11.0`, and `0.10.0 -> 0.11.0` run under both Windows PowerShell 5.1 and PowerShell 7; failure recovery, manual rollback, user files, and custom drivers remain covered.
- `tzdata>=2026.1,<2027` is now a base dependency and generic offline lock entry, aligned with the existing Windows CPython 3.13 portable pin `tzdata==2026.5`.
- The doctor/runtime test uses committed disabled example Inventory rather than ignored site-local Inventory. No real Inventory was read or committed.
- The Windows workflow logs the current-user SID and the pytest/process-temp Owner and DACL trustee SIDs on its next execution. It continues to use separate directories under `RUNNER_TEMP` and passes an explicit pytest basetemp.

### Windows-local ACL evidence

`ISOLATED_OFFLINE_TEST`: the current Windows process SID was `S-1-5-21-3088435879-1396255872-2259687785-1001`. The retained synthetic ACL fixture's `acl-vault` directory and `credentials.db` both had that exact Owner SID, protected DACLs, and exactly two non-inherited FullControl trustees: the current-user SID and SYSTEM (`S-1-5-18`). The pytest base directory had broader test-runner ACLs, demonstrating that the Store initialization itself established the stricter boundary. The failed GitHub log did not record the hosted Runner's actual user/Owner SID, so that historical value is not guessed; the workflow diagnostic will capture it on the next run.

### Automated and static evidence

All evidence below is `ISOLATED_OFFLINE_TEST`; no database or Docker endpoint was contacted.

- Required first-test checks were executed separately with `pytest -vv -x`: SQL Server `test_connect_credential_options_cleanup`, MySQL `test_adapter_preflight_credentials_timeout_registry_and_cleanup`, and PostgreSQL `test_adapter_preflight_credentials_timeout_and_close` each passed locally. Local SQL files were LF, so these runs establish the local/CI divergence; the new forced-CRLF regression establishes the identified clean-checkout cause.
- Database/Preflight focused group: **485 passed, 1 skipped**. Credential Store focused group: **44 passed, 1 skipped**. Portable Update final focused group: **18 passed**. Timezone, Inventory, dependency and forced-CRLF checks: **6 passed**.
- Final Windows-local full pytest: **1211 passed, 3 skipped, 0 failed** in 76.91 seconds. Skips are unchanged: POSIX permission-bit validation is inapplicable on Windows, native symlink creation is unavailable to this process token, and the real database system test is opt-in and was not enabled. No skip, xfail, ignored exception, relaxed assertion, or disabled security check was added.
- Ruff passed repository-wide. Strict MyPy passed for **77 source files**. `dba validate --inventory examples/inventory` passed with **11 instances, 118 reviewed SQL scripts, 0 warnings**. All **15** deployment PowerShell scripts parsed. `git diff --check` passed with existing LF-to-CRLF advisory messages only.
- The official Secret Scanner passed for Git-index paths/content. A supplemental named-file worktree scan passed for the 17 task files (workflow, SQLite ACL implementation, three updater scripts, dependency metadata/lock, seven test modules, and Backlog/Progress/Acceptance); unrelated untracked review archives, the supplied failure log, and diagnostic JSON files were not scanned or modified. No scanner rule or allowlist changed.

### Final targeted acceptance follow-up — 2026-11-04

`ISOLATED_OFFLINE_TEST`; only the Credential Store ACL, tzdata runtime, and Portable Update areas were rechecked. The full suite was not rerun.

- Credential Store ACL: `test_sqlite_private_acl_covers_database_directory_and_journal` passed. Windows `Get-Acl` evidence asserts Owner separately from DACL: the directory and database Owner is limited to the current user or SYSTEM; DACLs contain only Allow/FullControl entries for the current user and SYSTEM, and the directory/database DACLs are protected. The live rollback journal is checked separately for Owner and DACL principals/rights. Production validation remains unchanged and fail-closed.
- tzdata: `test_dependency_extras_and_lock_files_are_present` and the existing Asia/Shanghai timezone regression passed. The Full builder installs the hash-pinned Windows CPython lock with `--no-index --require-hashes` into `app/vendor`, the embedded runtime `._pth` path includes `..\app\vendor`, and the Full package step archives the complete package tree. Read-only inspection of the existing r5 Full ZIP confirmed `app/vendor/tzdata/zoneinfo/Asia/Shanghai`; its bundled CPython resolved that zone after `zoneinfo.reset_tzpath([])` using tzdata 2026.5 (`2026-10-19T07:30:00+08:00`). No package was rebuilt in this follow-up.
- Portable Update: the focused selection passed **29 tests**. Both Windows PowerShell 5.1 and PowerShell 7 passed all three version transitions, external Full-package SHA256 mismatch rejection, payload SHA256 mismatch rejection, startup-failure automatic restoration, and manual rollback. The transition/rollback checks retained user files and custom-driver state. Exact host versions: Windows PowerShell 5.1.26100.9711 and PowerShell 7.6.5.
- A supplementary runtime-lock/timezone invocation passed **2 tests** (the timezone regression was repeated). Targeted Ruff passed for the two changed test modules; `git diff --check` passed with only Git's existing LF-to-CRLF advisory messages. No skip/xfail, assertion relaxation, or security-check removal was introduced.

### Boundaries and remaining verification

`WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**; this task deliberately used Fake Drivers and synthetic credential/update files only. No real database, Docker, production asset, real Inventory, real Credential Store, credential, or `local_config.py` was accessed. Package version remains `0.9.0`; no tag or GitHub Release was created. No commit or push occurred.

The post-fix GitHub Actions run is **NOT_EXECUTED** because the user requires review before Commit and Push. Therefore `BUG-WINDOWS-CI-FAILURES-001` is `CODE_COMPLETE`, not `VERIFIED`. The next task is the user-approved commit/push followed by the Windows Actions rerun; after remote verification, resume the paused `PORTABLE-UPDATE-001` closeout rather than starting a new feature.

## 48. Phase 2 Top SQL mainline recovery and integration scope audit — 2026-11-04

### Baseline and authoritative task state

The checkout is on branch `main` at `1c0274af1fea439ce93fa6e8abbb2a7134e8fe5f`, matching the user-specified baseline. No tracked modifications or staged changes were present before this documentation update. The seven existing untracked review/archive/diagnostic artifacts were retained without reading or changing them. Package version remains `0.9.0`.

Backlog is authoritative. `V091-P2-TOPSQL-CONTRACT-001`, `V091-P2-TOPSQL-SQLSERVER-001`, `V091-P2-TOPSQL-POSTGRESQL-001`, `V091-P2-TOPSQL-MYSQL-001`, and `V091-P2-TOPSQL-ORACLE-001` are all `VERIFIED` within their respective recorded scopes. The four collectors do not imply every supported database version or metric has live E2E; the exact boundaries remain in Acceptance §§17, 19, 20, and 21. Thus SQL Server is not an unfinished Collector task and must not be reimplemented or repeated.

The previous Progress pointer to `BUG-WINDOWS-CI-FAILURES-001` was stale relative to its own prior acceptance (code complete; remote Actions rerun pending), and deployment work is frozen by current user direction. Other existing Backlog items such as `V091-P5-WORKBENCH-001` and `SQL-REGISTRY-SAFETY-001` retain their recorded statuses; this scope recovery did not close, reopen, or modify them.

### Current source reuse and actual gap

- `dba/cli.py` has a standalone `diagnose ... top-sql` route that resolves the versioned Top SQL profile and invokes the existing SQL Server, PostgreSQL, MySQL, or Oracle Collector. It produces a `TopSQLResult` for one selected metric and requested Top N.
- The existing `slow` route separately runs staged, bounded Slow/P0 collection and triage. It does not invoke a Top SQL Collector. Therefore the missing capability is orchestration into the comprehensive report, not another engine Collector, Adapter, or SQL Registry implementation.
- The shared Presenter already accepts `details.top_sql` and renders the result as a bounded Top SQL section. It displays the metric contract and native identity; current Top SQL item/presenter paths do not include captured SQL text. Existing Slow SQL details remain separately bounded/redacted.
- Existing roadmap sequencing requires explicit integration approval after all four engine collectors are verified. It also requires readable human CLI output and safe SQL summary/detail handling without expanding sensitive SQL collection by default.

### Proposed next task

`V091-P2-TOPSQL-INTEGRATION-001` is defined in Backlog as `BACKLOG`, awaiting user review of the integration scope. The proposal keeps Top SQL explicit opt-in; leaves default Slow collection and first triage timing unchanged; runs one selected native cumulative metric once through the existing reviewed route only after core Slow evidence is complete; preserves per-engine metric, source, identity, scope, completeness, and limitation semantics; and marks optional failures PARTIAL/INCONCLUSIVE without converting missing evidence to zero or suppressing the core diagnosis. It prohibits unsupported attribution of cache/digest/cursor rankings to current blockers, adds no SQL source/permission/configuration change, and keeps Top SQL output text-free. The human CLI should show a readable summary and safe detail state; SQL detail may use only bounded/redacted text already returned by Slow collectors and exactly matched to a Top SQL row by native identity. Similar SQL text or a hash alone is not an association; when the exact match is unavailable, report that state without another query.

### Evidence and limits for this review

This is a static documentation/source audit only. No tests, database, Docker, Inventory, credential provider, real credentials, or local lab configuration were accessed in this review. No Top SQL integration implementation or runtime verification is claimed; integration E2E is `NOT_EXECUTED`. Approval of the proposed scope is a prerequisite to changing product code. No product version, Git history, or deployment/CI file was changed.

## 49. V091-P2-TOPSQL-INTEGRATION-001 — opt-in implementation and offline acceptance — 2026-11-04

### Implementation

- Added `dba diagnose <database_id> slow --include-top-sql`; the flag defaults off and is rejected on other scenarios. With the flag enabled, the existing version/profile resolution and one engine-specific Top SQL Collector call happen after the complete Slow core capture and after Slow priority/triage have been computed. The result is attached to the same report details used by JSON and the shared ReportModel/HTML renderer. Console output renders the Top SQL section from that ReportModel.
- SQL Server, PostgreSQL, MySQL, and Oracle dispatch to their existing Collector classes and native default metrics. No Top SQL SQL source, Registry script, permission profile, or Collector implementation was changed. The standalone `top-sql` route retains its prior output shape.
- Top SQL is a separate report section. Its warning identifies the ranking as cumulative database-statistics evidence, not a current root cause. Adding it does not alter Slow priority candidates, triage, `incomplete`, or `success`.
- SQL text is attached only from Slow `active_requests`/`all_requests` rows when every required native statement identity field matches exactly, and, where a Top SQL plan identity exists, every plan identity field matches too. SQL Server correlation requires plan handle, query-plan hash, and plan generation; PostgreSQL requires query ID/database/user/top-level; MySQL requires digest/schema; Oracle requires SQL ID/child/instance and container when supplied. Duplicate matches, missing identity, identity mismatch, or truncated source text produce `UNMATCHED` with no SQL detail. Matched text is redacted and bounded by `HumanReportPolicy`.
- Unsupported profiles, denied access, empty/partial results, and Collector exceptions retain their existing public collection statuses. A core-incomplete report records `NOT_COLLECTED` and makes no Top SQL call. Add-on failures do not convert successful core Slow evidence into a failed diagnosis.

### Automated evidence

- `ISOLATED_OFFLINE_TEST`: **PASS**. The Top SQL integration plus four Collector regression modules passed **170 tests**. These cover all four existing Collector routes, default no-call, incomplete-core no-call, exact identity matches and mismatches (including database/query/plan generation), truncation, long SQL redaction/bounds, permission/unsupported/empty/partial/failure statuses, and ReportModel CLI/HTML consistency. The PostgreSQL Fake Driver CLI integration verifies the collector runs after Slow core evidence, once, and leaves candidate/triage results unchanged (excluding their independent snapshot timestamps).
- Slow and HTML regression selection passed **342 tests**. The full Windows-local pytest run passed **1240**, with **3 skipped** and no failures. No skip/xfail was added and no assertion or security check was relaxed.
- Ruff repository-wide: **PASS**. Strict MyPy (`mypy dba`, 78 source files): **PASS**. `dba validate --json`: **PASS**, 4 configured Inventory entries, 118 SQL scripts, 0 warnings. `git diff --check`: **PASS**, with Git's LF-to-CRLF advisory notices only.
- Simulated database-server E2E: **NOT_EXECUTED**. The CLI/Fake Driver integration is reported only as `ISOLATED_OFFLINE_TEST`, not as a database E2E. `WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**; no single `environment=test` Inventory target was selected for a real connection. The explicitly required `dba validate --json` loaded the configured Inventory metadata and emitted only its aggregate instance count and warnings; no credential values were read, and no asset was selected for connection. No Docker or database was started or accessed.

### Status and boundaries

Backlog remains the status authority and keeps `V091-P2-TOPSQL-INTEGRATION-001` at `IN_PROGRESS` pending the scoped simulator/real-database E2E decision. Complete native identities are absent from some current Slow evidence, so those rows remain explicitly unmatched; no new query was added to force a text association. Software version remains `0.9.0`. No commit, push, tag, or release was created. The seven pre-existing untracked files were retained unchanged.

## 50. V091-P2-TOPSQL-INTEGRATION-001 — scoped real E2E attempt and simulator safety review — 2026-11-04

### Host, targets, and credential-session gate

`WINDOWS_LOCAL_REAL_E2E` preflight confirmed a native Windows process (`Win32NT`, host `[Windows lab host]`, user SID recorded only in the local command output), PowerShell 7.6.5 x64, working directory `C:\path\to\DBAToolKit`, and the repository `.venv` Python 3.12.14 plus `.venv\Scripts\dba.exe`. Only the four user-named assets were inspected using read-only `dba show` metadata: `sqlserver2022` (SQL Server, `environment=test`, loopback:1433, configured family 2022), `mysql84` (MySQL, `environment=test`, loopback:3306, family 8.4), `pg16` (PostgreSQL, `environment=test`, loopback:5432, family 16), and `oraclefree` (Oracle, `environment=test`, loopback:1521, service `freepdb1`, family 26ai). No other Inventory assets were enumerated or selected.

The read-only `dba credentials status --json` check returned `status=LOCKED`, `agent=STOPPED`. The configured aliases could not be served by an active credential session. No unlock, credential read, password prompt, connection attempt, or alternative credential source was used. This is a credential-session availability block, not a database login or database permission result.

### Per-asset result

| Asset | Connection / actual DB version | Slow core | Integrated Top SQL / metric | Native SQL association | CLI / JSON / HTML parity | Failure / permission evidence |
|---|---|---|---|---|---|---|
| `sqlserver2022` | **BLOCKED** before connection; actual server version **NOT_EXECUTED** (Inventory family is 2022) | **NOT_EXECUTED** | **NOT_EXECUTED**; metric values, units, and live semantics unavailable | **NOT_EXECUTED** | **NOT_EXECUTED** | Credential session `LOCKED` / agent `STOPPED`; no database permission check ran |
| `mysql84` | **BLOCKED** before connection; actual server version **NOT_EXECUTED** (Inventory family is 8.4) | **NOT_EXECUTED** | **NOT_EXECUTED**; metric values, units, and live semantics unavailable | **NOT_EXECUTED** | **NOT_EXECUTED** | Credential session `LOCKED` / agent `STOPPED`; no database permission check ran |
| `pg16` | **BLOCKED** before connection; actual server version **NOT_EXECUTED** (Inventory family is 16) | **NOT_EXECUTED** | **NOT_EXECUTED**; metric values, units, and live semantics unavailable | **NOT_EXECUTED** | **NOT_EXECUTED** | Credential session `LOCKED` / agent `STOPPED`; no database permission check ran |
| `oraclefree` | **BLOCKED** before connection; actual server version **NOT_EXECUTED** (Inventory family is 26ai) | **NOT_EXECUTED** | **NOT_EXECUTED**; metric values, units, and live semantics unavailable | **NOT_EXECUTED** | **NOT_EXECUTED** | Credential session `LOCKED` / agent `STOPPED`; no database permission check ran |

For every asset, the requested base Slow command, opt-in `slow --include-top-sql --top 10`, and independent `top-sql --top 10` were **NOT_EXECUTED** because the credential-session gate failed first. Consequently there is no live core-evidence comparison, report parity, identity-match result, Collector failure-isolation observation, or database permission conclusion. No database SQL ran; Toolkit writes, DDL, remediation, and database side effects were therefore **NOT_EXECUTED**, not inferred PASS.

### Simulator static review

The existing simulator documentation and script names support SQL Server, MySQL, PostgreSQL, and Oracle scenarios named ROOT, MIDDLE, LEAF, and SLOW. Static inspection found these are state-changing lab scripts: setup creates tables/data; simulations perform updates/inserts or transaction changes and synthesize row locks/waits or long sleeps; cleanup drops tables (and Oracle setup includes user/grant administration). They therefore violate this round's no-write/no-DDL/no-manufactured-blocker boundary. The scripts were **NOT_EXECUTED**; no local config or credential file was opened. This is static capability/safety evidence only, not simulator E2E.

### Status

Overall live database E2E is **BLOCKED** for all four selected assets at the credential-session gate. `V091-P2-TOPSQL-INTEGRATION-001` remains `IN_PROGRESS` in Backlog. Resume only after the already-authorized test credential session is available through the normal user-controlled process; no credential-store unlock was attempted. The previously accepted `ISOLATED_OFFLINE_TEST` evidence remains recorded separately in §49 and is not represented as live E2E. Version remains `0.9.0`; no commit, push, tag, or release occurred.

## 51. V091-P2-TOPSQL-INTEGRATION-001 — continuation blocked at Credential Session IPC — 2026-11-04

### Pre-connection gate

The current PowerShell process reports native `Win32NT`, host `[Windows lab host]`, user `[Windows lab host]\xieji`, PowerShell 7.6.5 x64, working directory `C:\path\to\DBAToolKit`; the repository `.venv` Python and CLI executables exist. This confirms process facts but does not establish that this process can access the Credential Session Agent.

The user reported that the session had been unlocked through the normal process. The required read-only `dba credentials status --json` check returned exit code 1 with `CredentialSessionUnavailable` and the sanitized message `Credential session pipe access was denied.` The session's current state therefore could not be confirmed by this process. I stopped before Inventory re-selection, credential resolution, or any database connection; this is not evidence of a bad password, database outage, or database permission denial. No credential was printed, no store was opened/unlocked, and no database SQL ran.

CLI help was safe to inspect and confirms the independent route is `dba diagnose <database_id> top-sql [--top N]` (with optional `--metric`). The supplied integrated route is `dba diagnose <database_id> slow --include-top-sql --top 10`. Help inspection does not count as runtime E2E.

### Four-asset result matrix

The latest continuation performed no target-specific command because the required session gate failed first. The exact asset metadata last verified in §50 is not represented as a fresh connection check here.

| Item | SQL Server (`sqlserver2022`) | MySQL (`mysql84`) | PostgreSQL (`pg16`) | Oracle (`oraclefree`) |
|---|---|---|---|---|
| Real connection | **BLOCKED** before connection | **BLOCKED** before connection | **BLOCKED** before connection | **BLOCKED** before connection |
| Actual DB version | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| Slow Diagnose | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| Integrated Top SQL | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| Independent Top SQL | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| Metric semantics and SQL identity match | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| JSON / HTML parity | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** | **NOT_EXECUTED** |
| Permission result | **NOT_EXECUTED**; no DB query | **NOT_EXECUTED**; no DB query | **NOT_EXECUTED**; no DB query | **NOT_EXECUTED**; no DB query |

No SQL Server, MySQL, PostgreSQL, or Oracle command was launched; there is no per-database PASS/FAIL or `PERMISSION_DENIED` evidence. No natural blocker/slow-SQL scenario was inspected, so those cases remain **NOT_COVERED**. No new database, data, table, transaction, or lock was created. No offline tests were repeated.

### Status and handoff

Overall real database E2E remains **BLOCKED** before connection because this execution process cannot read Credential Session status. Per the Windows E2E rule, do not retry the same denied IPC from this context or inspect credential files. Backlog stays `IN_PROGRESS`; resume only in the normal Windows PowerShell process where the user's session reports `UNLOCKED`. Version remains `0.9.0`; no commit, push, tag, release, or Top SQL Delta work occurred.

## 52. V091-P2-TOPSQL-INTEGRATION-001 — report presentation repair — 2026-11-05

### Changes and evidence

Presentation-only corrections were made in the report Presenter, HTML renderer/templates, and report display tests. MySQL Top SQL collection success is now separate from completeness/visibility; the Collection Quality row reports `PARTIAL` or `UNKNOWN` without promoting unknown evidence, and preserves the 214/15/15 statement-instrument counts and limitation reasons. MySQL recent-SQL empty/no-candidate output is distinct from a failed collector. PostgreSQL numeric version display is normalized to a readable major/minor (or legacy major/minor/patch) while the raw `server_version_num` remains visible. Slow-report IDs are unique and navigation anchors resolve. Standalone Top SQL has a statistics-specific summary; integrated Top SQL remains a separate evidence section whose quality does not alter Slow core completeness. MySQL current-wait visibility is reported separately from active-request collection, and a clear PostgreSQL snapshot is worded as a single-snapshot observation.

`ISOLATED_OFFLINE_TEST`: `tests/test_html_reports.py`, `tests/test_top_sql_integration.py`, and `tests/test_top_sql_contract.py` — **96 passed**. Synthetic report models exercised MySQL/PostgreSQL Slow and independent Top SQL HTML generation, COMPLETE/PARTIAL/UNKNOWN distinctions, empty and failed recent-SQL states, version formatting, unique IDs/navigation, current-wait visibility, output self-containment, and unmatched SQL-text suppression. Ruff passed for the three changed reporting modules and report test file. Strict MyPy passed for the three reporting modules. `git diff --check` passed (Git emitted only existing LF-to-CRLF normalization notices).

`WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED** for this presentation repair. No database connection, Inventory selection, credential resolution, Collector, Registry SQL, connection code, or database test data was accessed or modified. The four captured 2026-11-04 HTML reports remain unchanged evidence of the original presentation defect; this offline run does not claim that those live reports were regenerated. Section §51 remains the authoritative blocked live-E2E record, and Backlog remains `IN_PROGRESS`.

### Final repository regression audit — 2026-11-05

Full `.venv` pytest ran twice with existing opt-in system tests disabled to prevent real database access: each run reported **1263 passed, 1 failed, 3 skipped**. The repeated failure is `tests/test_portable_update.py::test_startup_failure_restores_program_and_keeps_user_files[...WindowsPowerShell...powershell.exe]`; in full-file and full-suite order it stops during the synthetic managed-MySQL-driver update path and the output lacks the expected `automatically`/`restored` phrase. The isolated case passed once. Its precise order-dependent cause is undetermined; it is outside this presentation-only repair and was not changed. Do not claim a green full suite.

Full Ruff (`ruff check .`) passed. Project-configured Strict MyPy (`mypy dba`) passed for 78 source files. `git diff --check` passed with only line-ending normalization notices. No corresponding JSON/model file exists for the four named 2026-11-04 HTML reports, so no HTML regeneration was performed; unrelated historical JSON files were not substituted.

The worktree already contained the §49 opt-in Top SQL integration changes when this final audit began, including the `dba/cli.py` call-through and untracked `dba/top_sql_integration.py`; this audit did not change them. The full diff therefore includes approved collection orchestration that calls the existing four collectors once when opted in. No Collector implementation, SQL Registry query, database connection/authentication, permission, Inventory, or Credential Store file is in the tracked diff. Static inspection of the existing integration helper found no database write statement or direct SQL execution.

### Portable Update failure follow-up — 2026-11-05

`ISOLATED_OFFLINE_TEST`: pytest collection confirmed the Windows PowerShell 5.1 startup-failure case follows all collected items in `test_performance_triage.py`, the three tests in `test_permissions.py`, and the first 12 collected items in `test_portable_update.py`. The exact test passed alone (1 passed); the Portable Update module passed (27 passed); the nearest predecessor-file combination (`test_performance_triage.py`, `test_permissions.py`, and `test_portable_update.py`) passed (47 passed). A subsequent full suite passed: **1264 passed, 3 skipped, 0 failed**. This run did not reproduce the two failures recorded above.

The earlier failing runs' original child-process stdout/stderr was not retained. Read-only inspection of their surviving synthetic pytest artifacts shows the installed manifest returned to `0.9.0`, the application entry file hash matches the original installed copy, the MySQL active driver remains at its original `1.0.0` identity, and the program backup plus staged failed-program quarantine exist. An instrumented successful Windows PowerShell 5.1 reproduction captured the expected `prior program was restored` message in stderr and exit code 1. This confirms restoration in the retained failure artifacts and the current reproduction, but it does not establish why the earlier assertion at `tests/test_portable_update.py:389` did not find its expected phrase.

Root cause remains **UNCONFIRMED**. No test assertion, updater script, or product code was changed; no skip/xfail was added. Preserve the earlier failure records and investigate further if the same failure recurs with child stdout/stderr captured. No database, Inventory, or credential was accessed.

## 53. V091-P2-TOPSQL-INTEGRATION-001 — SQL Server/Oracle E2E stopped at host-context gate — 2026-11-05

### Pre-connection gate and evidence

NOT_EXECUTED for WINDOWS_LOCAL_REAL_E2E. The current process reported PowerShell 7.6.5 Core with an executable under the Codex runtime; the project working directory and .venv paths were present, but that does not establish the authorized Windows host context. Get-CimInstance Win32_OperatingSystem failed with “cannot access CIM resources”, so the host identity/OS could not be verified under the project rule.

The host gate failed before reading Credential Session status or either selected Inventory asset. Current Credential Session, sqlserver2022/oraclefree host/port/environment, connection checks, and all database scenarios are therefore NOT_EXECUTED. No credential file or value was read. No database connection or query was attempted. Historical SQL Server 10054/08001 and Oracle ORA-12514 reports were not retried and are not current-HEAD results.

Only offline CLI help was inspected. It confirms the integrated route dba diagnose <database_id> slow --include-top-sql --top 10 and independent route dba diagnose <database_id> top-sql --top 10; top-level dba top-sql is not a valid command.

| Item | SQL Server (sqlserver2022) | Oracle (oraclefree) |
|---|---|---|
| Authorized Windows host and project .venv | BLOCKED — host context not established | BLOCKED — host context not established |
| Credential Session / asset environment | NOT_EXECUTED | NOT_EXECUTED |
| Connection and actual DB version | NOT_EXECUTED | NOT_EXECUTED |
| Slow baseline | NOT_EXECUTED | NOT_EXECUTED |
| Slow + Top SQL integration | NOT_EXECUTED | NOT_EXECUTED |
| Independent Top SQL | NOT_EXECUTED | NOT_EXECUTED |
| Native identity association / ambiguity | NOT_EXECUTED | NOT_EXECUTED |
| CLI / JSON / HTML parity | NOT_EXECUTED | NOT_EXECUTED |

The user-provided MySQL 8.4.11 and PostgreSQL 16.15 results were not repeated; their 10-row collections and UNMATCHED identity associations remain as previously reported evidence. No SQL-text matching rule was changed. No tests were run because no code changed and the requested real-DB checks could not safely start. Version remains 0.9.0; no commit, push, tag, release, database write, permission change, or Top SQL Delta work occurred.

### Status

Backlog is BLOCKED, not VERIFIED, pending execution from the authorized Windows host after Credential Session = UNLOCKED and confirmation that each selected asset is environment=test. No current-HEAD SQL Server or Oracle real-E2E pass is claimed.

## 54. Oracle Slow Collector error preservation — 2026-11-06

`ISOLATED_OFFLINE_TEST`: four Fake Driver regressions cover ordinary Slow required and optional Collector failures, plus staged Slow required and optional failures after the Oracle Adapter closes its connection. Before the fix, the ordinary required-Collector case retained its original `QueryDenied`/`ORA-01031`, while the other three cases reproduced a later `PreflightRequired` masking the Collector error. After the fix, all four preserve the original Collector result or error and report metadata from the snapshot captured while the connection was valid. Existing error cleanup and readiness guards remain enabled.

Final targeted Oracle/report suite (`tests/test_oracle.py`, `tests/test_oracle_plan.py`, `tests/test_top_sql_oracle.py`, `tests/test_slow_profiles.py`, `tests/test_html_reports.py`): **157 passed**. Ruff passed for the changed source and test; strict MyPy passed for all 78 `dba` source files; `git diff --check` passed with Git line-ending advisory notices. No Collector SQL, Registry, connection lifecycle, permissions, Inventory, or Credential Store code was changed.

`WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**. No database, Docker, Inventory asset, Credential Session, or credential was accessed. This offline regression is not Oracle database E2E and does not change the parent `V091-P2-TOPSQL-INTEGRATION-001` status: Backlog remains `BLOCKED`, not `VERIFIED`, pending the authorized Windows host gate and required live evidence.

## 55. V091-P5-WORKBENCH-001 — inline interaction regression checkpoint — 2026-11-06

The existing Slow Diagnose HTML tests checked report content, escaping, responsive CSS, and keyboard-oriented markup but did not execute the inline interaction script. Added `tests/test_slow_report_interactions.py`. It reads the production `slow_report.html` template, extracts its inline JavaScript, and runs that exact script in Node's built-in `vm` with a minimal synthetic DOM. The test verifies search filtering and the no-results state, expand/collapse labels and accessibility state, print-time expansion and restoration of the prior search/detail state, and clipboard fallback while keeping copied content as text marked “未执行”. No third-party browser or Python dependency was added, and the product template was not changed.

`ISOLATED_OFFLINE_TEST`: `tests/test_slow_report_interactions.py` and `tests/test_html_reports.py` passed **50 tests**. Ruff passed on the new test. Strict MyPy passed for **78 `dba` source files** and the new test file. `git diff --check` passed; Git emitted only its existing LF-to-CRLF working-copy advisories for previously modified files. The initial pytest attempt failed before test setup because the sandbox-managed Windows Temp path denied directory creation. The same focused suite was rerun with `.tmp/pytest-temp-p5-workbench-20261106` as basetemp and passed. No test was skipped or weakened.

The requested narrow-width visual review was attempted through the managed browser, but opening the local workspace file returned `ERR_BLOCKED_BY_ADMINISTRATOR`; no CSS or page layout loaded. This attempted route is superseded by the local-browser evidence in §56. At that checkpoint, 320px/414px visual behavior remained **NOT_EXECUTED**. No database, Docker, Credential Session, Inventory, Credential Store, or credential was accessed. `WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED** for the integrated report; four-database report rendering and JSON/HTML parity remain outstanding. Backlog remains `IN_PROGRESS` pending the remaining scoped acceptance.

## 56. V091-P5-WORKBENCH-001 — local Chrome narrow-viewport visual acceptance — 2026-11-06

### Browser and sample

Rendered the existing self-contained Slow Report HTML template through the installed local Google Chrome **157.0.8103.38** at **320×844** and **414×844** CSS viewport sizes. The sample is synthetic and reproducible from `tests/test_html_reports.py::_report()`, rendered into `.tmp/p5-visual-sample-20261106-e/index.html`; it contains no real database report or credentials. Python's standard-library `http.server` served only that directory's single `index.html`, bound to `127.0.0.1:8312` for the run. Chrome DevTools Protocol set and verified each CSS viewport; the screenshot pixel dimensions also match each requested viewport.

### Visual and interaction results

| Check | 320×844 | 414×844 |
|---|---|---|
| `window.innerWidth` / `document.documentElement.scrollWidth` | 320 / 320; no page horizontal overflow | 414 / 414; no page horizontal overflow |
| Page title and toolbar | Title readable; all three toolbar buttons visible within x=10…310 | Title readable; all three toolbar buttons visible within x=10…404 |
| Navigation | Seven links; `overflow-x:auto`, max `scrollLeft=239`; last link reached and fully visible | Seven links; `overflow-x:auto`, max `scrollLeft=145`; last link reached and fully visible |
| Core diagnosis evidence | Root session 57 and blocked sessions 59/67 present; no overlap or off-screen page element | Root session 57 and blocked sessions 59/67 present; no overlap or off-screen page element |
| Tables | Eleven table wrappers use local horizontal scrolling (276px wrapper for 650px table); page width remains 320px | Eleven table wrappers use local horizontal scrolling (370px wrapper for 650px table); page width remains 414px |
| SQL / code blocks | Six sample blocks (8–48 characters) use `pre-wrap` / `overflow-wrap:anywhere`; each `scrollWidth` equals `clientWidth` | Same; no code-block width overflow |
| Evidence expand/collapse | All 16 expandable details open and close through the page control; expanded evidence screenshot reviewed | Same |
| Search | `57` returns 1 of 4 displayed rows; no-match query shows 0/4 and the empty-state message | Same |
| Remaining layout | Main panels stay within 10px side gutters; no unexpected overlap or out-of-bounds element | Main panels stay within 10px side gutters; no unexpected overlap or out-of-bounds element |

The page-level width check intentionally distinguishes the horizontally scrollable navigation and table containers from document overflow. Their content remains reachable within those containers. Buttons, title, core diagnosis, evidence sections, search field, matching row, and empty-search state were visually inspected in Chrome screenshots. No responsive defect was found, so no CSS/template change or additional regression test was needed.

The reusable synthetic fixture contains no unusually long SQL or string value. The observed wrapping applies to the six included snippets; extreme-length content was not stress-tested.

### Screenshot and audit evidence

The 22 viewport-sized screenshots and structured DOM audit are local, ignored artifacts under `.tmp/p5-visual-evidence-20261106-r8/`; they are not report outputs or source changes. `dom-audit.json` records the browser version, exact viewport metrics, link reachability, button bounds, section bounds, table/SQL overflow behavior, core evidence, and interaction results. For each width, screenshots cover `page-top`, `summary`, `root-assessment`, `blocking-chain`, `manual-action`, `evidence`, `collection-quality-panel`, `session-appendix`, `evidence-expanded`, `search-match`, and `search-no-results`.

### Scope and remaining acceptance

This was local browser visual verification only. No database connection or SQL ran; no credential, Inventory, permission, Credential Store, or Docker state was read or changed; no dependency was installed. `WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**. Four-database real report generation and parity against real reports remain **NOT_EXECUTED**; the offline synthetic parity checkpoint is recorded in §57. No Top SQL Delta work was started. Backlog remains `IN_PROGRESS`; this visual checkpoint does not close P5.

## 57. V091-P5-WORKBENCH-001 — four-engine offline JSON/HTML report parity — 2026-11-06

### Same-snapshot report paths

The tests reuse the existing `SlowReport` fixture from `tests/test_html_reports.py::_report()`, the production `TopSQLResult` contract, `associate_slow_sql_text`, the CLI JSON redaction/serialization path (`asdict` → `_redact_diagnostic_json` → `render_json`), and the shared HTML path (`build_report_model` → `render_bounded(HtmlReportRenderer.render)`). JSON and HTML are generated from the same in-memory `SlowReport` snapshot and incident summary. No second report model or fixture schema was introduced.

### Four-engine status matrix

`ISOLATED_OFFLINE_TEST`: `tests/test_slow_report_json_html_parity.py` passed **25 cases**: 16 engine/state parity cases, four long-content boundary cases, four sparse/missing-field cases, and one long-string URI-redaction regression. The combined focused report regression run passed **124 tests**.

| Database | Metric / unit | Metric scope / collection scope | Complete + matched | Partial + unmatched | Collected, no rows | Collector failed |
|---|---|---|---|---|---|---|
| SQL Server | `total_cpu_time` / microseconds | `SQL_STATEMENT` / `INSTANCE` | PASS | PASS | PASS | PASS |
| MySQL | `sum_timer_wait` / milliseconds | `DIGEST` / `INSTANCE` | PASS | PASS | PASS | PASS |
| PostgreSQL | `total_exec_time` / milliseconds | `SQL_STATEMENT` / `INSTANCE` | PASS | PASS | PASS | PASS |
| Oracle | `cpu_time` / microseconds | `CHILD_CURSOR` / `DATABASE` | PASS | PASS | PASS | PASS |

All four engines preserve `CUMULATIVE` metric semantics. The JSON and HTML top-SQL fields agree on engine/version, collection status, completeness, data visibility, record count, metric name/unit/semantics/scopes, limitations, and SQL-text association. The collection-quality row also reports `PARTIAL` for partial Top SQL and `FAILED` for failed collection; partial data is not promoted to complete. A complete empty collection remains `COLLECTED`, reports zero rows, and renders the no-record message rather than a connection failure. A failed collection remains `FAILED` with unknown completeness, unavailable visibility, and `COLLECTION_FAILED`; it does not change the Slow report success/core incident summary.

For every engine, one exact native identity match attaches its SQL text and the HTML code block matches the JSON association content. A source row missing one required identity field remains `UNMATCHED`; its candidate SQL is absent from the Top SQL association and from the generated report JSON/HTML. The Slow core status, severity, primary problem, root-cause candidate, data completeness, and overall success remain equal to the same fixture without Top SQL.

### Boundary coverage and fixes

- **Long SQL:** a multiline SQL value exceeding the 16,384-character human-report limit remains bounded in both formats and carries the same omission marker. This caught and fixed a Presenter defect where HTML's second truncation removed the marker already present in JSON.
- **Long identifiers:** 40,000-character native identity values remain intact in structured JSON; HTML displays at most the 100-character native identity value and remains below the configured report-size limit.
- **Special characters and SQL safety:** `<script>`, ampersands, quotes, and multiline SQL are escaped in HTML source while preserving their literal text in the rendered code block. Unrendered SQL tail content is omitted.
- **Empty/missing fields:** empty result sets and snapshots without optional `source_metadata` or `database_error` remain renderable. Missing native identity fields prevent a SQL-text association.
- **Redaction performance:** a 40,000-character ordinary identifier followed by two credential-bearing URI values is handled without regex backtracking; both URI user-info values remain `[REDACTED]`, while a URI without a password is preserved.

The URI redactor in `dba/logging.py` now scans for `://` delimiters instead of applying a backtracking scheme regex to every position in arbitrary text. This keeps the existing URI credential-redaction behavior while making long JSON identity values practical. `dba/reporting/presenters.py` now preserves the SQL omission marker when the HTML report applies its own content limit. No Collector, SQL Registry, permission, connection, or database code changed.

Focused regression command covered the new parity tests, `tests/test_top_sql_contract.py`, `tests/test_top_sql_integration.py`, `tests/test_html_reports.py`, the logging-redaction regression, and the secret-form safety test: **124 passed**. Ruff passed on both changed modules and the new test file. Strict MyPy passed for **79 source/test files**. `git diff --check` passed with the existing LF-to-CRLF working-copy advisories. A local `tests` namespace alias was used for pytest because an installed `tests` package otherwise shadows this repository's namespace during collection; no dependency was installed.

`WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**. These are synthetic offline report snapshots, not evidence that any database was contacted or that captured real reports were regenerated. Real SQL Server, MySQL, PostgreSQL, and Oracle report generation and JSON/HTML parity on those real reports remain outstanding. Product version remains `0.9.0`; no commit, push, tag, or release occurred. Backlog remains `IN_PROGRESS`; P5 is not closed by this offline parity result.


## 58. SQL-REGISTRY-SAFETY-001 — final static safety audit — 2026-11-07

Final audit rechecked Registry loading and execution authorization, the four adapters, read-only checks, metadata budgets, query timeouts, bounded fetch, and cleanup paths. The static checker and runtime digest revalidation remain additional safeguards: execution still requires a Registry-issued reviewed script for the target database, and the database monitor account must remain least-privileged. Static SQL checks are not a proof against every vendor function or server-side workload.

The audit confirmed coverage for modifying statements, multi-statement input, PostgreSQL session-state mutation and advisory locks, PostgreSQL/MySQL locking reads, SQL Server locking hints and sequence allocation, query timeout configuration, row and decoded-result-byte caps, digest/source/metadata mutation, and adapter cleanup. The tests also model MySQL streaming cursor close draining remaining rows; when a streaming result is truncated or fails, the MySQL adapter now closes its connection before closing that cursor. This preserves the fetch bound under the PyMySQL unbuffered-cursor cleanup behavior.

A final parser probe found one actual bypass: the scanner treated any `--` as a MySQL line comment, although MySQL requires whitespace or a control character after the second dash. Consequently `SELECT 1--x; SELECT 2` was accepted by the scanner. `dba/security.py` now applies that MySQL-specific rule; the same multi-statement input is rejected, while `-- ` comments remain supported. SQL Server `#temp` identifiers are also parsed using their database context and cannot hide a following statement. MySQL's comment rule is documented in the [MySQL 8.4 comments reference](https://dev.mysql.com/doc/refman/8.4/en/comments.html); the rejected row-lock forms and SQL Server locking hints are documented in the [PostgreSQL SELECT reference](https://www.postgresql.org/docs/17/sql-select.html) and [SQL Server table hints reference](https://learn.microsoft.com/en-us/sql/t-sql/queries/hints-transact-sql-table?view=sql-server-ver16).

`ISOLATED_OFFLINE_TEST`: `tests/test_toolkit.py` passed **88 tests**, including Registry size/duplicate-key/digest checks and dangerous SQL/parser regressions. The four-adapter timeout, Registry authorization, cleanup, bounded-fetch, and MySQL streaming truncation selection passed **26 tests** (`191 deselected`). Direct offline validation accepted **118 reviewed Registry scripts**. Ruff passed on the changed Registry/security/adapter/test files; strict MyPy passed for **78 source files**. The first pytest invocation could not create fixtures under the managed Windows TEMP path; rerunning with the existing project-local `.venv/test-tmp` completed successfully. `git diff --check` is recorded after this evidence update.

`WINDOWS_LOCAL_REAL_E2E`: **NOT EXECUTED**. No database, Docker, Inventory, Credential Store, or credential was accessed. No permission was changed and no KILL/terminate/cancel operation was issued. No version, commit, push, tag, or release was changed. The decoded-byte limit remains post-fetch/materialization and does not bound server work or wire bytes; MySQL `read_timeout` remains a socket-read timeout rather than a full-query wall-clock deadline. Backlog status is `VERIFIED`, pending formal user acceptance for `DONE`.

## 59. V0.9.1 Chinese presentation and release candidate — 2026-11-07

The user explicitly authorized the `0.9.0` to `0.9.1` product version change and a normal commit, push, tag, and GitHub Release in this request. The source version is synchronized in `pyproject.toml`, `dba/__init__.py`, portable build defaults, README, and release/update instructions. MongoDB DB-GPT, MCP, and Agent integration remain outside scope.

### Chinese presentation and report changes

Added a shared human-display mapping in `dba/reporting/normalizers.py` and reused it in terminal presentation, JSON human-readable message fields, HTML Presenter/Renderer output, statuses, report metadata, and fixed error guidance. Internal enums remain machine-readable in JSON. Native database names and identities, SQL, paths, protocol terms, and vendor error evidence are retained. The slow report keeps its diagnostic conclusion and source evidence while presenting localized headings, statuses, labels, search/interaction controls, and quality reasons. `tests/test_chinese_presentation.py` checks CLI help/errors, the slow report, four-engine synthetic report states, retained native terms, and untranslated UI phrases.

`ISOLATED_OFFLINE_TEST`: the complete suite passed **1324 tests**, with **3 skipped** and **0 failures**. The focused Chinese display and Slow Report interaction/parity run passed **29 tests**. Ruff passed; strict MyPy passed for **78 source files**; `dba validate --json` reported success, four example instances, 118 SQL scripts, and no warnings. All deployment PowerShell scripts and the seven inline workflow PowerShell blocks parsed successfully. `git diff --check` passed (Git emitted only its LF-to-CRLF working-copy notices).

### Prior GitHub Actions failure evidence

The latest historical workflow run available before this release attempt is [Actions run 42054877407](https://github.com/XieZaki/DBAToolKit/actions/runs/42054877407), at source `af3474558f28a236a1912435f0d133df1f61bbcc`. Its Windows pytest step reported **1263 passed, 2 skipped, 3 failed**. The three failures were the SQLite credential-store ACL cases `test_sqlite_private_acl_covers_database_directory_and_journal`, `test_product_concurrent_saves`, and `test_product_crash_recovery[before-73-False]`. The observed failure was that the hosted Windows runner's elevated token created a transient SQLite `-journal` with `BUILTIN\Administrators` as Owner; the owner validator allowed only the current user or SYSTEM, so the credential store rejected the journal despite its validated private DACL. This was a Windows pytest/SQLite ACL failure, not a Ruff/MyPy, dependency download, runtime download/hash, Full/Update build, artifact, Release creation/upload, or token failure: those later jobs/steps were skipped and produced no evidence.

The scoped fix validates the journal's private DACL, accepts the Administrators Owner only for the transient journal while running as an administrator, and repairs its Owner to the current user before the write transaction commits. Persisted credential-store files still require current-user/SYSTEM ownership. The SQLite regression tests cover journal inspection, the narrow Owner exception, and repair; no credential values were accessed. The earlier pytest temporary-directory issue was already fixed and was not repeated.

### Local package build and portable verification

`ISOLATED_OFFLINE_TEST`: `deployment/build_release.ps1` built and validated a local candidate from the existing pinned CPython 3.13.17 archive and local hash-locked wheelhouse. The first attempt was stopped when the managed system TEMP path denied pip's temporary-target creation. It did not proceed to archive creation. The retry used the existing project-local `.venv/test-tmp` and a new output directory, leaving the failed attempt and all pre-existing artifacts untouched. The successful candidate is `dist/release-0.9.1-codex-20261107-retry01`; Build ID is `local-v0.9.1-20261107`.

| Candidate asset | Size | SHA256 |
|---|---:|---|
| `DBAToolkit-0.9.1-Windows-x64-Full.zip` | 99,938,941 bytes (95.31 MiB) | `4a809c73a3f3d9a0f1af4db24c1307f81509131231fc9b87ea61c0dcbd1958e2` |
| `DBAToolkit-0.9.1-Windows-x64-Update.zip` | 45,444,289 bytes (43.32 MiB) | `ecdad107dc9d9b2ccd855f8616ff5bcf2957a2583f9308a4351486485a7a1c2f` |
| `使用说明.md` | 2,929 bytes | `6706c5c1cd1c74c6401610811c8fde46bc446f4376c3bdc4babfb0747167289d` |

`verify_release.ps1` confirmed the exact three-entry SHA256SUMS manifest and validated Full/Update archive contents. Full ZIP CRC and every ZIP entry read passed (5,504 entries); Update archive manifest and payload hashes passed (1,367 entries). `deployment/verify_portable.ps1` then ran the extracted Full package's bundled Python 3.13.17 runtime, version command, SQL Registry validation (118 scripts), and managed-driver checks. It reported success with zero configured instances and no warnings. This is portable offline smoke validation only; it is not a database compatibility claim.

### Release boundary

The local candidate build is not the public release. GitHub Actions and uploaded Release asset verification are pending the authorized commit, push, and `v0.9.1` tag. Cross-version Update on a disposable host without Python remains unverified, so `PORTABLE-UPDATE-001` remains `E2E_PENDING`. `V091-P5-WORKBENCH-001` remains `IN_PROGRESS`; four-database real report generation and real-report JSON/HTML parity remain `NOT_EXECUTED`. No database, Docker, Inventory, Credential Store, or credential was accessed in this work.

## 60. V0.9.1 GitHub Release publication and workflow repair — 2026-11-07

The user authorized the `0.9.1` release, including normal commit, push, tag, and publication. The existing tag `v0.9.1` was not moved or recreated. The recovery workflow checked that product source remained equivalent to the tagged release while allowing only the release workflow and its packaging/test helpers to differ. Main commit `790c0196cb8ecce2e21725dec65d0a452f6b9047` contains the release repairs. Recovery Actions run [#14](https://github.com/XieZaki/dbatoolkit/actions/runs/42345786728) built and published the existing tag. The public [v0.9.1 Release](https://github.com/XieZaki/DBAToolKit/releases/tag/v0.9.1) is marked Latest and exposes exactly four custom assets.

### Evidence-based failure analysis

- The first tagged workflow stopped in the pinned CPython step because it tested `$LASTEXITCODE` after `Invoke-WebRequest`. That PowerShell cmdlet does not set the native-process exit code. The faulty guard could therefore reject a successful download before the file existence and SHA256 checks; the observed evidence does not establish a network download or hash failure. The workflow now uses terminating web-request errors, checks the downloaded file, and validates its SHA256. The successful run completed this step.
- Recovery run [#10](https://github.com/XieZaki/dbatoolkit/actions/runs/42344241929) failed before tests because PowerShell expanded a duplicated tag path into `refs/tags//tags/v0.9.1`. The workflow now builds an explicit `refs/tags/v0.9.1:refs/tags/v0.9.1` refspec. The later source/tag gate passed.
- Recovery run [#11](https://github.com/XieZaki/dbatoolkit/actions/runs/42344441499) built the packages and uploaded a draft, then failed when `gh release view` had no repository context because the publish job did not check out source. The verifier now passes the explicit repository to `gh release view`, `gh release download`, and `gh release edit`.
- The earlier action upload represented the Chinese-named manual as `default.md`; this was visible in the failed draft asset list. The external asset now uses the ASCII name `DBAToolkit-0.9.1-User-Guide.md` while retaining Chinese content. Before retrying, the workflow checked the existing draft was still a draft and contained exactly the known four-asset set, then removed only `default.md`. The action uploaded the corrected name and the verifier confirmed the exact four-asset set.
- The failures above were workflow guard/refspec/repository-context/asset-name problems. Run #14 completed Windows pytest, Ruff, strict MyPy, dependency wheelhouse preparation, pinned runtime SHA256 verification, Full/Update packaging, candidate upload, Release upload, downloaded-asset size/hash verification, and publication. There is no evidence of a dependency download, runtime SHA256, Full/Update build, artifact transfer, or GitHub token permission failure in the successful run. The earlier Windows pytest ACL/fixture failures and their scoped repairs remain documented in §§46–47; they did not recur in run #14.

| Release stage | Observed evidence |
|---|---|
| Windows pytest | Earlier failures are separately recorded in §§46–47; run #14 passed 1325 tests with 2 skipped. |
| Ruff / Strict MyPy | Run #14 passed both; MyPy reported 78 source files. |
| Locked dependencies | Wheelhouse preparation completed successfully in run #14. |
| CPython runtime download / SHA256 | Initial failure was the PowerShell exit-code guard, not an evidenced network or hash mismatch; run #14 passed the pinned runtime step. |
| Full / Update build | Both packages and the local manifest validation completed successfully. |
| Workflow artifact transfer | Candidate artifact upload and publish-job download both succeeded. |
| Release creation / asset upload | The earlier run created a draft and uploaded files before the later verifier failed. Run #14 uploaded the corrected assets and passed verification. |
| GitHub token / permissions | Publish job had `contents: write`; its cleanup, upload, remote download, and publish operations succeeded. No permission failure was observed. |

### Published artifacts and verification

Run #14 reported `1325 passed, 2 skipped`, Ruff `All checks passed!`, and strict MyPy success for 78 source files. Full, Update, and the Chinese manual were built, the pinned runtime hash was checked, and the uploaded assets were downloaded and compared against their declared sizes and SHA256 values before the Release was made public.

| Published asset | Size | SHA256 |
|---|---:|---|
| `DBAToolkit-0.9.1-Windows-x64-Full.zip` | 98,072,636 bytes | `662680268ae3e5f8ef24bd4bdae2fb7b2e376cb140f691613eb6c1ae87c080cf` |
| `DBAToolkit-0.9.1-Windows-x64-Update.zip` | 44,513,423 bytes | `c8a9f5170661ca693931f0125feb96acf1cc2a259d66f6f9234661cbdea091bc` |
| `DBAToolkit-0.9.1-User-Guide.md` | 2,990 bytes | `e5f6943a5e15f0af88869c37a4b657f7a500c5b9758bd544801c560e08cdc981` |
| `DBAToolkit-0.9.1-SHA256SUMS.txt` | 310 bytes | Downloaded size and SHA256 matched the build output before publication. |

The four uploaded names matched exactly; remote asset sizes and SHA256 checks passed before the workflow changed the Release from draft to public. A separate local rebuild with the corrected ASCII manual filename passed `verify_release.ps1`; its local hashes differ because that candidate has a different build ID and is not the published artifact. The public Release page independently shows all four assets and marks the release Latest.

### Remaining acceptance limits

`PORTABLE-UPDATE-001` remains `E2E_PENDING` in Backlog because the formal cross-version Update transition on a disposable Windows host without Python remains unverified. `V091-P5-WORKBENCH-001` remains `IN_PROGRESS`; four-database real report generation and JSON/HTML parity on real reports are still `NOT_EXECUTED`. `OFFLINE-RUNTIME-001` still lacks acceptance on a genuinely Python-absent host and with a physically disconnected network. `WINDOWS_LOCAL_REAL_E2E`: **NOT EXECUTED**. No database, Docker, Inventory, Credential Store, or credential was accessed; no database permission was changed and no unverified database version was claimed.


## 61. SQL Server Top SQL identity linkage repair — offline implementation — 2026-11-08

The authorized scope repairs only the SQL Server identity chain between the existing Top SQL collector and Slow `active_requests`. Other database collectors and SQL were not changed. The existing shared report and native identity models remain in use.

`sql/sqlserver/top_sql.sql` continues to prefer `st.dbid` and falls back to `sys.dm_exec_sql_text(qs.plan_handle).dbid` only when it is NULL. `SQLServerTopSQLCollector` preserves a missing/non-integer `database_id` as incomplete identity, marks the collection PARTIAL with `IDENTITY_AMBIGUOUS`, and does not invent a value. The existing active-request Registry script now returns database ID, statement offsets, SQL/plan handles, query and plan hashes, and plan generation. A LEFT JOIN to `sys.dm_exec_query_stats` matches the existing request by native handles, hashes, and statement offsets; the outer LEFT JOIN retains the request even with no statistics match. The generation is populated only for exactly one matching statistics row. The row count counts all matching rows, so multiple rows with the same generation remain ambiguous rather than being collapsed as a false unique match. The downstream matcher also requires the match-count field itself to be present as a strict integer; missing metadata cannot be promoted to `MATCHED`.

SQL Server association still requires exact statement identity (SQL handle, both offsets, database ID, and any additional identity fields present) and exact plan identity (plan handle, query plan hash, and generation). It does not use SQL text similarity or query-hash-only matching. Synthetic tests distinguish `MATCHED`, `UNMATCHED`, `IDENTITY_INCOMPLETE`, and `NO_ACTIVE_REQUESTS`; incomplete or unmatched associations do not carry candidate SQL text into JSON/HTML. The HTML Presenter renders a localized SQL Server association reason. The shared report model is reused.

`ISOLATED_OFFLINE_TEST`: focused command `.venv\Scripts\python.exe -m pytest tests/test_top_sql_sqlserver.py tests/test_sqlserver.py tests/test_top_sql_integration.py tests/test_slow_report_json_html_parity.py -q --basetemp .tmp\pytest-sqlserver-identity-20261108-final` passed **170 tests**. Ruff passed on all changed Python modules and tests. Strict MyPy passed for **78 `dba` source files**. `.venv\Scripts\python.exe -m dba validate` passed for **4 example assets and 118 SQL scripts**. `git diff --check` passed; Git emitted only its LF-to-CRLF working-copy advisories.

The SQL Server permissions guide already documents the version-specific permission required for `sys.dm_exec_query_stats`; no permission guide change or database permission change was needed. `WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED** at the user's direction. No real database connection, Docker, Inventory, Credential Store, credential, or database permission was accessed or changed. Actual SQL Server runtime DMV values and driver scalar types remain unobserved on this HEAD; synthetic tests cannot establish live DMV behavior.

Backlog status is `E2E_PENDING`: offline code and regression gates are complete, while current-HEAD SQL Server live verification remains deferred until the authorized Windows host preflight and a later user instruction. No version, commit, push, tag, or release was changed.


## 62. SQL Server Top SQL identity E2E attempt — user-provided pre-login failure — 2026-11-08

Evidence source: user-provided Windows PowerShell output for `.venv\Scripts\dba.exe diagnose sqlserver2022 slow --include-top-sql --metric cpu_time --top 10 --json`. The command reported `ConnectionFailed` with ODBC Driver 17, SQLSTATE `08001`, vendor code `10054`, and a pre-login handshake failure / remote socket reset. The output reported effective TLS as `Encrypt=no; TrustServerCertificate=yes`; the credential alias is intentionally omitted here.

The connection did not reach login, so neither the Slow `active_requests` Collector nor the Top SQL Collector executed. This attempt supplies no live evidence for database ID fallback, DMV field types, query-stats matching, or JSON/HTML identity association. The output does not establish which transport, protocol, server availability, or resource condition caused the pre-login failure. No product or TLS configuration change was made in response.

Evidence label: `WINDOWS_LOCAL_REAL_E2E` — user-provided failed connection attempt, not independently verified. The output shows a PowerShell prompt and project-local executable path, but does not include the mandatory selected Inventory asset `environment=test` and credential-session preflight evidence; no independent host-preflight claim is made.

The SQL Server identity task remains `E2E_PENDING` in Backlog. Resume live verification only after the required authorized Windows host preflight and after the connection issue is resolved through the normal approved environment path. No further connection attempt was made by this agent.


## 63. SQL Server Slow normalized session identity enrichment — offline and SQL Server 2022 Docker acceptance — 2026-11-08

The user reported a successful pre-fix SQL Server E2E sample where `all_sessions` contained session identity values but corresponding `top_requests` / `all_requests` `normalized` identity fields were null. The concrete reported mismatch was non-null source session values versus null normalized request values; values are not copied into this acceptance record.

The gap was in `build_slow_report`: request normalization had already happened in the Collector, while report assembly only supplemented top-level request fields from `active_sessions`. It did not synchronize the four normalized identity fields. `dba/slow.py` now fills only missing/empty normalized `username`, `client_host`, `client_ip`, and `application` from the unique `all_sessions` row with the same integer `session_id`. A missing/ambiguous session or null/absent source column leaves the normalized value null. Existing non-empty normalized values are preserved. The report builder copies the nested normalized dictionary before filling it, so the source Collector row and its raw DMV fields remain unchanged. Since `top_requests` and `all_requests` are views of the same assembled request rows, the normalized values are equal in both. No SQL, Collector, identity matching, or diagnostic conclusion logic changed.

`ISOLATED_OFFLINE_TEST`: `tests/test_slow.py`, `tests/test_slow_report_json_html_parity.py`, and `tests/test_html_reports.py` passed **121 tests**. Regressions cover all four fields, unmatched session, missing source fields, existing-value preservation, unmodified source normalized data, top/all parity, and JSON/HTML display consistency. Ruff passed on all changed Python modules/tests. Strict MyPy passed for **78 `dba` source files**.

The pre-fix observation and post-fix acceptance evidence are distinct. The user supplied the following `WINDOWS_LOCAL_REAL_E2E` result for SQL Server 2022 in the Docker test environment: server version `16.0.4305.5`, asset `sqlserver2022`, `environment=test`, report `reports\sqlserver2022_slow_20261108_125142.html`. The report had one successful `active_requests` row for `session_id=55` in `running` state; SQL text was captured without truncation. The normalized values were `username=sa`, `client_host=[Windows lab host]`, `client_ip=[private client IP]`, and `application=Navicat`. `top_requests` and `all_requests` carried equal normalized identity fields. Top SQL rank 3 had `sql_text_association.status=MATCHED` and `source=slow.active_requests`; collection completeness was normal and the `active_requests` sample had `success=true`.

The first sample returned zero active requests due to sampling timing and is a normal empty sample, not a collection failure. A second sample used a sustained test SQL statement and verified the positive Top SQL association. This is user-provided real E2E evidence; the agent did not repeat the database connection. It verifies SQL Server 2022 `16.0.4305.5` only and does not establish real compatibility for SQL Server 2008 R2 or other versions/databases.

`ISOLATED_OFFLINE_TEST` remains separate: 121 targeted tests passed; Ruff and Strict MyPy (78 `dba` source files) passed; `git diff --check` passed. These automated checks are not counted as real E2E. The HTML report was generated, but no additional manual visual inspection was performed. No database connection, Docker action, Inventory or Credential Store access, credential read, or permission change was performed by the agent during this documentation closeout. The task is formally accepted as `DONE` in Backlog for the approved integration scope, with the live version scope limited as stated above.

## 64. Slow current active-request evidence and HTML grouping — offline checkpoint — 2026-11-08

This checkpoint extends the shared Slow report presentation. It does not change Top SQL collection, identity matching, findings, or database permissions.

The SQL Server `active_requests` Registry query now returns the request ID and a bounded `statement_sql_text` derived from `statement_start_offset` / `statement_end_offset` byte offsets in `sys.dm_exec_sql_text`. It keeps the original bounded batch text in JSON, marks statement truncation, and returns an explicit reason when the current statement offsets or text are unavailable. The `active_sessions` query already reads `sys.dm_exec_connections` once; it now also returns original login, Windows user, client interface, and client process ID. The Slow report fills only absent request-source fields from one unique row with the same `session_id`; duplicate session IDs are treated as ambiguous and are not used for identity enrichment.

The HTML report has a current-activity panel before blocker analysis. It presents each request separately with its SQL text, database/session/request identity, available execution-source fields, request-level time/resource metrics, wait, and blocker in one record. Multiple requests from one session remain separate. SQL Server batch text is not presented as the active statement when statement offsets exist but the statement projection is missing. An empty successful sample is distinguished from a failed or unknown sample. Other engines use only fields already exposed by their current collectors; no additional cross-engine Collector query was added. JSON retains the raw batch and native DMV fields alongside the statement projection.

`ISOLATED_OFFLINE_TEST`: focused Slow, SQL Server, HTML, and four-engine JSON/HTML Parity tests passed **221 tests**. Targeted Ruff passed; Strict MyPy passed for all **78 `dba` source files**. The complete default suite reported **1346 passed, 3 skipped, 9 failed**. All nine failures are in the pre-existing Top SQL HTML presentation worktree: those assertions still expect internal native identity labels in HTML, while the existing local presentation change omits them. This Slow checkpoint did not change that Top SQL presentation contract. No test failure was in the Slow/SQL Server current-request test set.

Real database evidence is `NOT_EXECUTED`. The shell reported Windows 10 and machine `[Windows lab host]`, but the PowerShell executable was under `[Codex-managed runtime path]`; this does not establish the authorized Windows host context. No Inventory asset was inspected for an E2E run, no credential-session status was queried, and no database or Docker connection was attempted. SQL Server 2022 live statement-offset behavior and active-request source values remain unverified on this worktree. `V091-P5-WORKBENCH-001` remains `IN_PROGRESS` in Backlog.

## 65. V091-P5-WORKBENCH-001 — separate current Slow diagnosis from cumulative Top SQL — 2026-11-09

`diagnose slow` no longer calls the Top SQL integration or emits its section in HTML or its payload in newly collected JSON. The retired `--include-top-sql` option and explicit `--metric` or `--top` on Slow return an argument error before Inventory access, with migration guidance in console and JSON output. The independent `diagnose top-sql` retains its ranking parameters and results. `active-sql` retains its row limit option.

The SQL Server `active_requests` Registry script no longer reads `sys.dm_exec_query_stats` or returns plan generation and match counts. It retains current DMV identities, statement offsets, statement and batch text, truncation indicators, and request runtime and wait evidence. Session identity and blocker analysis retain their existing rules. Top SQL Collectors, ranking SQL, and strict SQL/plan association rules for previously captured evidence were left in place. The other three engines retain their current Collector routes and incident context.

The nine earlier HTML failures reproduced together. Seven tests expected native identifiers in standalone HTML. The revised assertions verify that JSON preserves those identities, HTML keeps its statistics and container context, and native identities stay out of standalone HTML. The other two tested the retired Slow route and now verify that Slow does not collect or display cumulative Top SQL. Coverage also checks that rendering a legacy snapshot leaves its stored association evidence intact.

`ISOLATED_OFFLINE_TEST`: **489 focused tests passed**. Full pytest: **1359 passed, 3 skipped** with the real system test opt-in disabled. Ruff passed for tracked Python files. The broad `ruff check .` found five lint issues in the existing untracked load-test directory. Strict MyPy passed for 78 source files. `dba validate` passed for 4 example assets and 118 Registry scripts. `git diff --check` passed.

Real database E2E is `NOT_EXECUTED`. The PowerShell process was part of the Codex-managed runtime and did not establish authorized Windows host access. The unique test asset and credential-session gates were not attempted. The historical live evidence in §63 remains recorded for that checkpoint. Current-head live SQL Server behavior and four-engine real-report parity still require verification. Backlog retains `V091-P5-WORKBENCH-001` as `IN_PROGRESS`.

## 66. V091-P5-WORKBENCH-001 — SQL Server Slow session query and database error propagation — 2026-11-09

The reported SQL Server 2022 failure was native error 207 with SQLSTATE `42S22`. Both active session Registry variants now obtain `client_interface_name` from `sys.dm_exec_sessions`, including the missing-value check. `sys.dm_exec_connections` continues to provide the client network address. The SQL Server 2022 offline fixture asserts all ten requested fields with two distinct sessions and reversed request order.

The SQL Server query wrapper had omitted normalized driver details from general failures and timeouts. The four adapters now serialize the same sanitized error structure for Collector results and populate the existing JSONL fields from it. Tests exercise native SQL Server errors 207 and 229, PyMySQL numeric errors, psycopg SQLSTATE and diagnostic fields, and python-oracledb codes. Unknown codes and SQLSTATE values remain null.

The staged SQL Server, PostgreSQL and MySQL report builders use the version captured before any query can close the adapter. Oracle already captured its report context before collection. The CLI preserves successful samples and the first Collector error. JSON includes a separate report error when assembly or rendering fails. HTML and terminal output identify the failed Collector and show the sanitized vendor error. Tests also verify incomplete incident status and credential redaction.

`ISOLATED_OFFLINE_TEST`: **390 focused tests passed**. Full pytest reported **1371 passed and 3 skipped** with the real-system opt-in disabled. Ruff passed for tracked Python sources and the new regression test. Strict MyPy passed for 78 source files. `dba validate` passed for 4 example assets and 118 Registry scripts. `git diff --check` passed.

`WINDOWS_LOCAL_REAL_E2E`: **NOT_EXECUTED**. PowerShell ran from the Codex managed runtime, so the authorized host, unique asset and credential-session gates were not established. No database connection was attempted. P5 remains `IN_PROGRESS`.

### Windows SQL Server 2022 verification

Run the preflight in Windows PowerShell on the authorized host. Select the existing Inventory YAML for the one approved test asset. Confirm the actual host, project executables, approved Docker host and port, `environment=test`, and monitoring credential session before continuing.

```powershell
Set-Location C:\path\to\DBAToolKit
$repo = (Get-Location).Path
(Get-Process -Id $PID).Path
[System.Runtime.InteropServices.RuntimeInformation]::OSDescription
Resolve-Path .\.venv\Scripts\python.exe, .\.venv\Scripts\dba.exe
$dba = (Resolve-Path .\.venv\Scripts\dba.exe).Path
$sql = (Resolve-Path .\sql).Path
$testInventory = (Resolve-Path -LiteralPath (Read-Host 'Approved single-asset test Inventory YAML')).Path
& $dba --inventory $testInventory show sqlserver2022 --json
& $dba credentials status --json
```

Continue in the same PowerShell session after the gates pass. The unique output directory keeps reports and JSONL separate from earlier evidence.

```powershell
$verification = Join-Path $repo ('.tmp\slow-207-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $verification -ErrorAction Stop | Out-Null
Set-Location $verification
$connection = & $dba --inventory $testInventory --sql-root $sql test-connection sqlserver2022 --json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $connection.success -or $connection.version -ne '16.0.4305.5') {
    throw 'Test connection or expected SQL Server build did not pass.'
}
$check = & $dba --inventory $testInventory --sql-root $sql check sqlserver2022 --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $check.status -ne 'READY') { throw 'check did not report READY.' }
& $dba --inventory $testInventory --sql-root $sql diagnose sqlserver2022 slow --json --report slow.html |
    Set-Content -Encoding utf8 slow.json
if ($LASTEXITCODE -ne 0) { throw 'Slow returned a failure. Inspect slow.json.' }
$slow = Get-Content -Raw slow.json | ConvertFrom-Json
$sessions = $slow.report.details.samples | Where-Object collector -eq 'active_sessions'
if (-not $sessions.success) { throw 'active_sessions did not succeed.' }
```

Compare the request cards in `slow.html` to `all_requests` in `slow.json` by session and request ID. Compare source fields to the corresponding `all_sessions` row. A sample with no active request cannot verify live request/source parity.

To reproduce error 207 on the same verified asset, restore the invalid alias in a disposable copy of the read-only Registry query.

```powershell
$faultRegistry = Join-Path $verification 'registry-fault-207'
Copy-Item -LiteralPath $sql -Destination $faultRegistry -Recurse -ErrorAction Stop
$faultScript = Join-Path $faultRegistry 'sqlserver\active_sessions.sql'
$original = [IO.File]::ReadAllText($faultScript)
if ([regex]::Matches($original, '\bs\.client_interface_name\b').Count -ne 2) {
    throw 'Expected exactly two session aliases in the copied SQL.'
}
[IO.File]::WriteAllText($faultScript, $original.Replace('s.client_interface_name', 'c.client_interface_name'))
$example = Join-Path $repo 'examples\inventory\demo.yaml'
& $dba --inventory $example --sql-root $faultRegistry validate
if ($LASTEXITCODE -ne 0) { throw 'Fault Registry failed static validation.' }
& $dba --inventory $testInventory --sql-root $faultRegistry diagnose sqlserver2022 slow --json --report slow-207.html |
    Set-Content -Encoding utf8 slow-207.json
if ($LASTEXITCODE -ne 1) { throw 'Expected a failed Slow collection.' }
$fault = Get-Content -Raw slow-207.json | ConvertFrom-Json
if ($fault.collector_name -ne 'active_sessions' -or $fault.error_type -ne 'QueryFailed' -or
    $fault.error.sqlstate -ne '42S22' -or $fault.error.vendor_code -ne 207) {
    throw 'The original SQL Server error was not preserved.'
}
$fault.error | Select-Object error_type, database_type, driver, sqlstate, vendor_code, message
Get-Content .\logs\dba.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.sql_script -eq 'active_sessions' -and $_.vendor_code -eq 207 } |
    Select-Object sql_script, error_type, driver, sqlstate, vendor_code, sanitized_message
```

## 67. V091-P5-WORKBENCH-001 — SQL Server Slow WAITFOR classification — 2026-11-09

- Prior SQL Server 2022 evidence supplied by the user shows that `WAITFOR DELAY '00:00:30';` was captured with its SQL text and execution source. The prior diagnostic classified it as `WAITING_SQL` and reported `DEGRADED`. This evidence records the original behavior.
- `ISOLATED_OFFLINE_TEST` — SQL Server requests with both `command` and `wait_type` set to `WAITFOR` retain their SQL text, execution source, resource metrics and wait details. A standalone wait with sufficient evidence of no blocking or open transaction is excluded from the abnormal-wait and long-running findings. Missing or truncated blocking and transaction evidence keeps the result incomplete and the incident `UNKNOWN` when no independent issue is observed.
- Regression cases cover idle and short-running snapshots, WAITFOR with open transaction or blocking evidence, native lock, I/O and memory grant waits, the existing slow threshold, the blocking chain, and failed or incomplete collectors. A mixed snapshot retains the genuine abnormal wait in the finding, triage and incident summary while the WAITFOR stays in the current-request display. Unproven causes remain undetermined.
- Before changes, the focused baseline passed 181 tests. After changes, the focused suite passed 200 tests and full pytest passed 1390 tests with 3 skipped. Ruff, strict MyPy (78 source files), `dba validate` (4 example assets, 118 Registry scripts) and `git diff --check` passed.
- New `WINDOWS_LOCAL_REAL_E2E` — `NOT_EXECUTED`. This checkpoint used offline fixtures only. A live retest of the revised classification and the existing four-engine real-report and parity requirements remain open.

## 68. V091-P5-WORKBENCH-001 — SQL Server Slow mixed WAITFOR report consistency — 2026-11-10

- The supplied `sqlserver2022_slow_mixed_waitfor_blocking.html` records the pre-fix symptoms. The transaction table contains active rows while the incident KPI is blank. The root candidate has a transaction ID and confirmed identity quality but unknown observation and verification fields. The blocking graph shows a root and a victim while the priority table labels their blocking status unconfirmed.
- The SQL Server Slow incident KPI counts distinct transaction IDs in the existing unfinished user-transaction classification from the transaction collector. A complete empty sample yields zero. Failed, truncated or unidentifiable transaction evidence leaves the exact KPI unknown. Session open counts remain separate, and the transaction table retains the collected rows.
- The root candidate reuses the analyzer's observed and verified fields. Verification requires a complete session and transaction sample, a unique eligible transaction for the session, matching login time and a valid native transaction ID. An ambiguous or mismatched observation cannot establish ownership. Priority badges use the same observed blocking graph as the request and transaction sections. An observed edge establishes blocking participation; it does not establish transaction ownership or current identity for manual action.
- `ISOLATED_OFFLINE_TEST` — 191 focused baseline tests and 195 focused regression tests passed. Full pytest passed 1394 tests with 3 skipped. Coverage includes standalone WAITFOR, WAITFOR with an unfinished transaction, WAITFOR with downstream blocking, deduplicated transaction IDs, unavailable transaction evidence and JSON/HTML consistency. Ruff, strict MyPy, `dba validate` and diff check passed.
- `WINDOWS_LOCAL_REAL_E2E` — `NOT_EXECUTED` for this fix. The existing report was inspected without changing it. A fresh report from the authorized SQL Server 2022 test asset remains pending.

## 69. V091-P5-WORKBENCH-001 — Slow HTML workbench presentation — 2026-11-10

- `ISOLATED_OFFLINE_TEST` — Slow HTML now groups the incident overview, finding-backed diagnosis, active SQL, blocking, transactions, performance and advanced evidence. Request cards own current SQL and show a preview for longer text. The transaction table links to current activity, uses the collected transaction start and separates confirmed association from a same-session review link. Evidence paths, rules, sample windows and native details remain in the default-collapsed appendix. The independent diagnosis, CLI, JSON serialization, Collector, SQL Registry and manual-action safety contracts remain unchanged.
- SQL Server mixed-WAITFOR synthetic output is `reports/slow_workbench_synthetic_sqlserver_20261110.html`. It shows the fixture sessions 51 and 55, the 11-second downstream lock wait, transaction ID and actual fixture begin time. The diagnosis states that the statement which acquired the lock cannot be identified from the current WAITFOR. The original real HTML is unchanged. Edge opened the local fixture and produced a screenshot. The exact narrow-viewport probe did not complete.
- Focused pytest passed 330 tests across Slow, HTML, Incident, Triage, Oracle, MySQL and PostgreSQL. Full pytest passed 1403 tests with 3 skipped. Ruff, strict MyPy (78 source files), `dba validate` (4 example assets and 118 Registry scripts) and `git diff --check` passed. Automated parity checks verify that HTML mapping does not mutate the diagnosis or summary, and preserve native identities, transaction time and incomplete evidence.
- `WINDOWS_LOCAL_REAL_E2E` — `NOT_EXECUTED`. The terminal process was the Codex-managed PowerShell runtime. No local lab credential file, database or Docker endpoint was opened. P5 remains `IN_PROGRESS` while the new SQL Server 2022 real JSON/HTML acceptance and real-report parity across four engines remain open.

### Windows 本地复验命令

从已授权 Windows 宿主机打开 Windows PowerShell。准备既有隔离混合 WAITFOR 场景，并在下游请求处于锁等待时运行。诊断使用既有独立监控身份，测试驱动身份由实验室单独管理。

```powershell
Set-Location -LiteralPath 'C:\path\to\DBAToolKit'
$processPath = (Get-Process -Id $PID).Path
if ($PSVersionTable.PSEdition -ne 'Desktop' -or
    $processPath -notlike "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe") {
    throw 'Run from the authorized Windows host PowerShell.'
}
$dba = (Resolve-Path .\.venv\Scripts\dba.exe).Path
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$sql = (Resolve-Path .\sql).Path
& $python -c 'import sys; print(sys.executable)'
$testInventory = (Resolve-Path -LiteralPath (Read-Host 'Approved single-asset test Inventory YAML')).Path
$asset = & $dba --inventory $testInventory show sqlserver2022 --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $asset.enabled -or $asset.environment -ne 'test' -or
    $asset.db_type -ne 'sqlserver' -or $asset.host -ne '127.0.0.1' -or $asset.port -ne 1433) {
    throw 'The approved SQL Server 2022 test asset could not be confirmed.'
}
$session = & $dba credentials status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $session.status -ne 'UNLOCKED') {
    throw 'Confirm the existing monitoring credential session from this Windows host.'
}
$connection = & $dba --inventory $testInventory --sql-root $sql test-connection sqlserver2022 --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $connection.success -or $connection.version -ne '16.0.4305.5') {
    throw 'Connection or expected SQL Server build could not be confirmed.'
}
$check = & $dba --inventory $testInventory --sql-root $sql check sqlserver2022 --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $check.status -ne 'READY') { throw 'Test asset is not READY.' }
$output = Join-Path (Resolve-Path .\reports).Path ('slow-workbench-live-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output -ErrorAction Stop | Out-Null
$html = Join-Path $output 'mixed-waitfor.html'
$json = Join-Path $output 'mixed-waitfor.json'
& $dba --inventory $testInventory --sql-root $sql diagnose sqlserver2022 slow --json --report $html |
    Set-Content -Encoding utf8 -LiteralPath $json
if ($LASTEXITCODE -ne 0) { throw 'Inspect the sanitized diagnostic JSON for the failure.' }
Get-Item -LiteralPath $html, $json | Select-Object Name, Length
```

在同一快照中核对真实会话、事务 ID、开始时间、根阻塞、受阻请求、锁等待时间和证据边界。HTML、JSON 与实验室产物只保存在本地。

## 70. V091-P5-WORKBENCH-001 — SQL Server Slow 事务分诊证据一致性 — 2026-11-10

- `ISOLATED_OFFLINE_TEST`。SQL Server 根会话事务分诊复用已有会话与事务关联结果，以及 SQL Server 空间诊断已有的 3600 秒活动长事务阈值。完整采样且关联已核验时，20 秒及 3599 秒为 `NOT_TRIGGERED`，达到 3600 秒为 `CONFIRMED`。持续时间缺失、关联歧义或事务采样截断时保留 `UNKNOWN`。
- 合成快照中的 Session 55 事务关联保持未核验。JSON 和 HTML 使用同一分诊结论，原始证据保持不变。主报告使用「根阻塞关联证据」，锁来源语句仍未确认。
- 串行定向测试 420 项通过，全量 pytest 1420 项通过、3 项跳过。Ruff、严格 MyPy、`dba validate` 和 `git diff --check` 通过。基准 HTML 的 SHA-256 保持 `1B238E24694E1370B3BB4DC8CB12EE303DDD58DAF595FDA690578B000E07D7FF`。
- `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。等待 DBA 在获准宿主机上验收。

## 71. V091-P5-WORKBENCH-001 — Slow HTML 第 06 节 SQL 资源消耗与等待分析 — 2026-11-11

- `ISOLATED_OFFLINE_TEST`。第 06 节按当前请求展示资源计数、等待和性能解释。SQL 摘要链接到第 03 节对应请求。SQL Server 使用请求级毫秒、读页和写入次数，逻辑读按 8 KiB/页换算。MySQL 使用采样语句耗时、检查行数、影响行数和可见等待。PostgreSQL 使用查询运行时间与可见等待。Oracle 单列请求运行时长估计和 `V$SQL` 子游标累计指标。零值保留，未取得的指标保持缺失。
- 脱敏样例为 `reports/slow_workbench_synthetic_sqlserver_resources_20261111.html`。会话 55 显示 `LCK_M_X` 等待 02:40:00，阻塞者为会话 51。会话 51 的 WAITFOR 运行 00:01:31，其已核验事务持续 02:40:00。取得锁的语句仍待核查。生成的 HTML 有两张资源卡，13 个页内链接均可定位。上一版合成报告与混合 WAITFOR 实机报告的 SHA-256 与修改前一致。
- 定向 pytest 340 项通过，全量 pytest 1424 项通过、3 项跳过。全量首跑的模拟 Top SQL 验收因沙箱临时目录权限失败，复跑把 Python 临时目录和 pytest basetemp 放在工作区内。跳过原因为 Windows 无 POSIX 权限位、当前进程令牌无法创建符号链接、实机系统测试需显式启用。Ruff、严格 MyPy（78 个源文件）、`dba validate`（4 个示例资产、118 个脚本）及 `git diff --check` 通过。
- `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。本轮仅验证 HTML 展示和同一诊断对象的 JSON/HTML 一致性。后续仍需 DBA 审阅和四库真实报告验收。

## 72. V091-P5-WORKBENCH-001 — Slow HTML 第 03–06 节展示收尾 — 2026-11-11

- `ISOLATED_OFFLINE_TEST`。第 03 节显示当前语句、执行身份、状态和请求运行时间，连接详情可展开。第 04 节显示会话角色、直接阻塞者、层级、状态和请求链接，原始阻塞边与等待资源可在附录查阅。第 05 节保留事务时间、标识、状态、身份核验和 SQL 链接。第 06 节按请求显示资源、等待和基于样本的中文解释，展示范围说明位于标题下。
- 脱敏样例为 `reports/slow_workbench_synthetic_sqlserver_p5_display_closeout_20261111.html`。会话 51 的写入采样值为 0 次，WAITFOR 请求运行 91 秒，事务已核验。会话 55 的写入未采集，CPU 为 0 ms，逻辑读为 2 页，物理读为 0 页，受会话 51 阻塞并出现 `LCK_M_X` 等待 02:40:00。样例的 13 个页内链接均有目标。Edge 157.0.4341.42 的桌面验证页显示两张资源卡。
- 定向 pytest 339 项通过，全量 pytest 1424 项通过、3 项跳过。Ruff、严格 MyPy（78 个源文件）、`dba validate`（4 个示例资产和 118 个 SQL 脚本）及 `git diff --check` 通过。原实机报告 `sqlserver2022_slow_mixed_waitfor_blocking.html` 的 SHA-256 保持 `D043F6DA2990BED74DDC7F4C1657837C928926AFD7471AB9DF548E7814CCFAE4`。
- `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。等待 DBA 审阅完整报告与四库真实报告验收。

## 73. V091-P5-WORKBENCH-001 — Slow HTML 展示冻结 — 2026-11-11

- `ISOLATED_OFFLINE_TEST`。第 06 节将 SQL Server CPU、逻辑读、物理读和写入标为当前请求自开始至采集时刻的累计值。当前等待时长沿用等待事件口径。会话 51 累计写入为 0 次，会话 55 的累计写入未采集。第 03、06 节普通 SQL 卡片省略通用提示徽标，异常徽标保留。第 05 节的「SQL Server 事务 DMV」链接至附录中的 `sys.dm_tran_active_transactions.transaction_begin_time` 来源说明。
- 新脱敏样例为 `reports/slow_workbench_synthetic_sqlserver_p5_final_presentation_20261111.html`。Edge 157.0.4341.42 检查了 1440×960、414×844 和 320×844 视口。含滚动条的浏览器视口宽度分别为 1440、414 和 320，页面内容宽度与可用宽度分别为 1425、399 和 305，折叠附录展开后仍无页面级横向溢出。字段和性能说明为 12px，表头与表格正文为 11px。Edge 生成的 7 页打印样张保留事务表、SQL 资源卡和完整来源路径，搜索控件和空结果提示不进入打印页。
- 定向 pytest 340 项通过，全量 pytest 1425 项通过、3 项跳过。回归覆盖四库指标口径和 JSON 原始对象不变。样例的 14 个页内链接均有目标，Edge 检查通过 SQL 跳转、来源跳转、复制文本和打印展开恢复。Ruff、严格 MyPy（78 个源文件）、`dba validate`（4 个示例资产和 118 个 SQL 脚本）及 `git diff --check` 通过。572 份既有 HTML 的 SHA-256 与执行前一致。原实机报告 SHA-256 为 `D043F6DA2990BED74DDC7F4C1657837C928926AFD7471AB9DF548E7814CCFAE4`。
- Slow HTML 第 03–06 节结构与视觉布局在本离线检查点冻结。后续优先验收 SQL Server 2022、MySQL 8.4、PostgreSQL 16、Oracle 23 的真实报告与 JSON/HTML 一致性。本轮 `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。

## 74. V091-P5-WORKBENCH-001 — SQL Server 2022 高并发报告定向修复 — 2026-11-11

- 既有实机证据来自 `reports/sqlserver2022_slow_20261111_193108.html`，SHA-256 为 `3a8af9a873f3cdf7378a083040a3f5e0138ee829ef99a648627131bb28e57251`。89 张活动请求卡与资源卡逐条对应。Q4 为 63 条、8,081 ms、305,776 页、48 条 RUNNABLE；Q2 为 18 条、473 ms、17,906 页、13 条 RUNNABLE；Q3 为 8 条、314 ms、13,156 页、6 条 RUNNABLE。共 67 条 RUNNABLE、22 条 RUNNING。原报告未找到配对 JSON。
- 原报告正文与复制文本逐条相同。静态读取三个 LoadTest 模板，89 条语句与模板相比分别只缺最后的右括号。旧采集 SQL 对非 `-1` 的结束偏移漏计包含的最后一个 UTF-16 码元。离线对照 Microsoft 的包含末尾示例，并用实际 Registry 截取表达式验证完整模板。新查询按字节切分，保留 `-1` 到批次末尾、语句偏移及 UTF-16 边界，显式标记超过 32 KiB 的文本。原始 SQL 字段结构保持原口径。 [sys.dm_exec_requests 示例 E](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-requests-transact-sql?view=sql-server-ver16) · [SUBSTRING](https://learn.microsoft.com/en-us/sql/t-sql/functions/substring-transact-sql?view=sql-server-ver16)
- 第 02 节新增有界高并发聚合。语句键由数据库 ID、批次句柄与起止偏移构成，缺少可信键的请求独立列示。指标为可见请求的累计值加总，缺失指标保留缺失及覆盖范围。RUNNABLE 指向待核验的 CPU 调度竞争，当前快照缺少实例 CPU 使用率和调度器运行队列。INDEX(0)、JOIN 条件中的 CONVERT、多表 JOIN、GROUP BY 与 ORDER BY 为人工复核线索。 [SQL Server 调度器 DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-os-schedulers-transact-sql?view=sql-server-ver16)
- `ISOLATED_OFFLINE_TEST`。脱敏回放文件为 `reports/slow_workbench_synthetic_sqlserver_load_20261111.html`。请求数、状态与资源计数从旧报告重算，数据库、SQL 对象、会话 ID 和句柄为合成值。89 条 JSON、HTML 正文和复制文本逐条一致。三个真实模板的原始 DMV 偏移、采集后文本及真实 JSON/HTML parity 待新采样核验。
- 定向 pytest 466 项通过，全量 pytest 1440 项通过、3 项跳过。Ruff、严格 MyPy（78 个源文件）、`dba validate`（4 个示例资产、118 个脚本）及 `git diff --check` 通过。回归覆盖非 `-1` 包含末尾、`-1`、多语句、中文和代理对、长度上限、四库 HTML/JSON 语义及不完整样本。SQLite 仅执行移植的字节截取表达式，实际 SQL Server DMV 调用待实机验证。
- 修复后的 `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。当前进程为 Codex 管理的 PowerShell Core，宿主机门禁在连接前停止。验收脚本已通过 PowerShell 解析、宿主机门禁和 A–D 的合成场景断言。

### 授权 Windows 宿主机验收

在正常 Windows PowerShell 使用下列采集脚本。脚本只针对明确提供的单资产测试 Inventory 选择 `sqlserver2022`。它校验 loopback 测试目标、独立监控凭据会话、SQL Server 2022 指定构建和 READY 状态。每次诊断将同一快照的 HTML 与 JSON 写入独立本地目录。

```powershell
Set-Location -LiteralPath 'C:\path\to\DBAToolKit'
$approvedInventory = (Resolve-Path -LiteralPath (Read-Host 'Approved single-asset test Inventory YAML')).Path
$slowAcceptance = '.\scripts\acceptance\verify_sqlserver_slow_snapshot.ps1'
& $slowAcceptance -Inventory $approvedInventory -Scenario A -PreflightOnly
```

若资产有明确获准的运行时设置，在所有脚本调用中传入同一个 `-Settings <路径>`。

**A · 并发 SQL**。预检通过后在单独终端使用既有普通测试驱动账号。指定已经批准的现有配置路径并用静态读取器验证元数据，再启动已审阅的 LoadTest。每轮固定并发、持续时间与超时。先以 10 会话试跑，下列 60 会话采样限于已批准的实验室预算。未采到三个模板或未触发高并发规则时保留本轮证据。

```powershell
# 负载终端，同一授权 Windows 宿主机
Set-Location -LiteralPath 'C:\path\to\DBAToolKit'
$env:DBA_TOOLKIT_CONFIG = (Resolve-Path -LiteralPath (Read-Host 'Approved existing load-test config')).Path
& .\.venv\Scripts\python.exe -c 'import sys; from dba.lab_config import read_lab_database_config; c=read_lab_database_config(sys.argv[1], ''sqlserver''); assert c.host==''127.0.0.1'' and c.port==1433 and c.database==''AdventureWorks2022''' $env:DBA_TOOLKIT_CONFIG
if ($LASTEXITCODE -ne 0) { throw 'Load-test target did not match the approved lab.' }
& .\.venv\Scripts\python.exe .\DBAToolkit-LoadTest\run_sqlserver_load.py --sessions 60 --duration 25 --pause-ms 150 --connect-timeout 5 --query-timeout 20
```

```powershell
# 诊断终端，压测运行期间
$a = & $slowAcceptance -Inventory $approvedInventory -Scenario A
$a
```

**B · 非主动等待长 SQL**。用一个普通测试会话执行已审阅的业务型 SELECT，设置客户端查询超时并记录 SPID。在执行超过 10 秒且仍在运行时采集。验收检查其超过阈值、无上游阻塞、非 WAITFOR、语句文本完整。核对 CPU、逻辑读、当前等待和运行时长。若预算内未观测到超过 10 秒的业务请求，记录 `NOT_OBSERVED` 并保留现场结果。

```powershell
$workloadSpid = [int](Read-Host 'B test workload SPID')
& $slowAcceptance -Inventory $approvedInventory -Scenario B -WorkloadSessionId $workloadSpid
```

**C · 锁阻塞**。复用已审阅的实验室事务夹具。根会话在有界 WAITFOR 期间保留其未提交事务，下游 UPDATE 使用有限锁等待。记录两条会话的 SPID，在等待发生后采集，按原夹具路径结束并回滚测试事务。核对根身份、事务 ID、下游锁等待，以及当前 WAITFOR 与实际取得锁语句的区别。

```powershell
$rootSpid = [int](Read-Host 'C root test SPID')
$victimSpid = [int](Read-Host 'C victim test SPID')
$c = & $slowAcceptance -Inventory $approvedInventory -Scenario C -RootSessionId $rootSpid -VictimSessionId $victimSpid
$c
```

**D · 解除异常**。等待压测正常结束和测试事务回滚后重新采集，分别对照 A 和 C 的基线。确认活动请求数下降、测试 SQL 消失，旧阻塞根和事务 ID 不残留。

```powershell
& $slowAcceptance -Inventory $approvedInventory -Scenario D -BaselineJson $a.Json
& $slowAcceptance -Inventory $approvedInventory -Scenario D -BaselineJson $c.Json
```

在 DBA 原生客户端可补充以下只读调度器样本。记录多个时点的运行队列与宿主机 CPU 指标。SQL Server 2022 要求已有的 `VIEW SERVER PERFORMANCE STATE`，由 DBA 审核监控权限。

```sql
SELECT scheduler_id, cpu_id, current_tasks_count, runnable_tasks_count, work_queue_count
FROM sys.dm_os_schedulers
WHERE status = N'VISIBLE ONLINE'
ORDER BY runnable_tasks_count DESC;
```

## 75. V091-P5-WORKBENCH-001 — SQL Server 2022 实机门禁与 SQL 文本边界 — 2026-11-11

- 开始时为 `feature/top-sql-readable-report-20261108`、HEAD `f02b621b0e5c95d724dd89238d28c8170ed3faa3`。原有未提交修改与未跟踪验收脚本已保留。旧实机报告和 §74 脱敏回放的 SHA-256 与开始时一致。
- `WINDOWS_LOCAL_REAL_E2E` 为 `NOT_EXECUTED`。宿主机验证为 `BLOCKED`，因为当前进程来自 Codex 管理的 PowerShell Core。脚本在读取 Inventory、查询凭据元数据或建立 SQL 连接之前停止。本轮 A 基线、B 高并发、C 长 SQL 与主动等待、D 混合阻塞与解除均未执行。16.0.4305.5、`sqlserver2022`、`127.0.0.1:1433`、`master` 和 `dba_monitor` 是待宿主机核验的预期值。实机计数、聚合、采集完整性、同次报告和遗留会话均无本轮数据。
- `ISOLATED_OFFLINE_TEST` / `OFFLINE_PASSED`。补充四个精确上限回归。以 32 KiB 和多一个 BMP 码元为输入，各测试显式包含式结束偏移和 `-1`，同时覆盖零与非零起始偏移、后续语句隔离、中文与 HTML 转义。32 KiB 的末字符完整，32 KiB + 2 字节只保留前 32 KiB，JSON、HTML 正文、复制文本与截断标记一致。既有用例覆盖右括号、UTF-16 代理对、跨上限代理对、空文本和无效偏移。SQLite 执行现有 Registry 字节表达式的离线移植，SQL Server DMV 原始偏移和实际执行仍待宿主机核验。
- 定向 pytest 294 项通过。Ruff、Strict MyPy（78 个源文件）、`dba validate`（4 个示例资产、118 个脚本）及 `git diff --check` 通过。PowerShell 脚本通过解析和宿主机门禁验证。六份标记为离线的同对象 JSON/HTML 样本通过比较，覆盖空闲基线、高并发、长 SQL 与正常 WAITFOR、文本边界、混合阻塞、解除阻塞。保留旧测试会话的负向样本被拒绝。证据位于 `.tmp/p5-sqlserver-boundary-evidence-20261111/`，以及同目录层级的 `p5-sqlserver-boundary-pytest-20261111.log`、`p5-sqlserver-pair-verifier-20261111.log` 和 `p5-sqlserver-windows-host-gate-20261111.log`。
- 本轮改动限于验收脚本、边界测试和本地记录。采集 SQL、诊断规则、权限策略及冻结的 03–06 节布局保持原样。单次 RUNNABLE 仍仅作为调度竞争线索，持续 CPU 瓶颈待核验。请求累计 CPU 和逻辑读按样本加总。P5 保持 `IN_PROGRESS`。

### Windows 宿主机续跑命令

本节采用本轮的 A–D 场景名称。脚本沿用 §74 的参数值并增加 `Baseline`。只有在获准 Windows 宿主机的正常 Windows PowerShell 中运行。先验证本地测试资产、基础连接数据库、现有监控凭据的用户名与 SQL Server 构建。诊断一次同时生成 JSON 与 HTML，验证通过后在同一目录写入 `verification.json`。验证失败时保留已经生成的报告。

```powershell
Set-Location -LiteralPath 'C:\path\to\DBAToolKit'
$slowAcceptance = '.\scripts\acceptance\verify_sqlserver_slow_snapshot.ps1'
$labArgs = @{ Inventory = (Resolve-Path -LiteralPath (Read-Host 'Approved single-asset test Inventory YAML')).Path }
# 如果资产需要已有获准的运行时设置，追加 $labArgs.Settings 为该设置文件路径。
& $slowAcceptance @labArgs -Scenario Baseline -PreflightOnly

# A · 基线。测试负载未运行。
$baseline = & $slowAcceptance @labArgs -Scenario Baseline
$baseline
```

**B · 高并发**。A 通过后，在同一宿主机的另一终端用批准的普通测试驱动账号启动既有 LoadTest。先确认本轮并发和时长符合批准的实验室预算。驱动配置只通过现有机制使用，元数据检查不输出配置。

```powershell
Set-Location -LiteralPath 'C:\path\to\DBAToolKit'
$env:DBA_TOOLKIT_CONFIG = (Resolve-Path -LiteralPath (Read-Host 'Approved existing load-test config')).Path
& .\.venv\Scripts\python.exe -E -c 'import sys; from dba.lab_config import read_lab_database_config; c=read_lab_database_config(sys.argv[1], ''sqlserver''); assert c.host==''127.0.0.1'' and c.port==1433 and c.database==''AdventureWorks2022'' and c.user.casefold() not in (''sa'', ''dba_monitor'')' $env:DBA_TOOLKIT_CONFIG
if ($LASTEXITCODE -ne 0) { throw 'Load-test target or identity did not match the approved lab.' }
& .\.venv\Scripts\python.exe .\DBAToolkit-LoadTest\run_sqlserver_load.py --sessions 100 --duration 45 --pause-ms 100 --connect-timeout 5 --query-timeout 20
```

```powershell
# 诊断终端，负载稳定时采集。请求数取自快照。
$busy = & $slowAcceptance @labArgs -Scenario A
$busy
```

**C · 长 SQL 与主动等待**。在批准的测试库使用有限查询超时，执行一个已审阅的、预计超过 10 秒的业务型 SELECT。在另一普通会话运行独立有界 WAITFOR，保持两者同时可见。记录实际 SPID。无符合条件的样本时保留证据并记录未验证，按采集结果复核请求运行时间与累计 CPU。

```powershell
$longSpid = [int](Read-Host 'Business SQL test SPID')
$waitSpid = [int](Read-Host 'Independent WAITFOR test SPID')
$long = & $slowAcceptance @labArgs -Scenario B -WorkloadSessionId $longSpid -WaitforSessionId $waitSpid
$long
```

**D · 混合阻塞与解除**。复用已审阅且归属明确的隔离事务夹具。根会话在有界 WAITFOR 期间保留事务，下游更新使用有限锁等待。按夹具流程回滚并正常关闭测试会话，待 LoadTest 退出后再次采集。旧 Windows 模拟器含长时间 WAITFOR 及删除同名测试表的路径，当前未验证其运行对象归属。使用已经批准的有界夹具。

```powershell
$rootSpid = [int](Read-Host 'Blocking root fixture SPID')
$victimSpid = [int](Read-Host 'Blocked fixture SPID')
$blocked = & $slowAcceptance @labArgs -Scenario C -RootSessionId $rootSpid -VictimSessionId $victimSpid
$blocked

# 正常回滚并关闭测试会话后，分别核对阻塞与并发基线。
& $slowAcceptance @labArgs -Scenario D -BaselineJson $blocked.Json
& $slowAcceptance @labArgs -Scenario D -BaselineJson $busy.Json
```

保留每次采样的完整结果。对照已提交给驱动的已知 SQL 文本核对 Q2、Q3、Q4 的末尾右括号和偏移。实际 DMV 长文本边界只有在可稳定构造并核对原始字节时记录实机通过。若未能在授权宿主机验证指定资产或既有凭据，实机状态保持 `NOT_EXECUTED`。

## 76. V091-P5-WORKBENCH-001 — 四库双会话事务阻塞夹具 — 2026-11-12

`DBAToolkit-LoadTest` 本地新增四库 `run_*_blocking.py` 入口，共用事务回滚与连接关闭流程。SQL Server 对 `AdventureWorks2022.Production.ProductCategory` 的同一行执行可回滚更新，持锁会话在有界 `WAITFOR` 内保留事务。MySQL、PostgreSQL 和 Oracle 对各自样例表的同一行执行 `SELECT FOR UPDATE`。README 第 8 节记录单资产预检、负载与诊断双窗口操作、会话 ID 核对和解除阻塞复查。

`ISOLATED_OFFLINE_TEST`：四个入口的 `--dry-run` 成功，未访问配置或数据库。夹具和 `tests/test_lab_config.py` 定向 pytest 共 34 passed，其中新增 12 项覆盖场景预览、无效资产门禁、回滚顺序与中断关闭。新增 Python 文件 Ruff、Strict MyPy 通过，README 的 11 个 PowerShell 代码块语法检查通过。

`NOT_EXECUTED`：四库阻塞关系和解除后的真实采样仍待获准 Windows 宿主机验证。本轮未连接数据库、读取实验室凭据或更改产品诊断。§75 SQL Server A–D 实机验收仍待执行。

## 77. V091-P5-WORKBENCH-001 — SQL Server 请求状态与诊断耗时 — 2026-11-12

**既有实机报告复核。** `sqlserver2022_slow_20261111_211100` 的 HTML 中存在 `runnable`，并发概述也直接使用 `RUNNABLE`、`RUNNING`。同次 JSON 的采集时间戳间隔为 438.829 ms，原 `collection_ms` 为 438，`full_collection_ms` 为 1766，`total_report_ms` 为 2250。原完整流程的起点在 Inventory 加载前，早于应急采集，已包含连接准备和版本探测。`diagnosed_at` 来自诊断对象组装，至 `report_written_at` 的 537.081 ms 包含后续诊断处理和 HTML 生成，无法用作文件写入耗时。原 `report_write_ms` 固定为 null，`report_write_duration_ms` 已测量打开、写入和关闭文件，但受时钟分辨率与整数舍入影响。当前 Windows Python 的 `monotonic()` 分辨率为 15.625 ms，`perf_counter()` 为 0.0001 ms。

**修复口径。** SQL Server 状态统一显示为可运行、运行中、挂起、空闲，未知原生状态按原值展示，诊断 JSON 保留数据库原始状态。`collection_ms` 从应急采集开始到采集阶段结束，含交错的数据处理和分诊。`full_collection_ms` 从 Inventory 加载前计到诊断组装结束。`report_write_duration_ms` 从打开 HTML 文件到正文刷新完成，`report_write_ms` 保留为同值兼容字段。`total_report_ms` 从 Inventory 加载前计至正文刷新完成，计入 HTML 组装。计时使用单调高分辨率时钟，正耗时不足 1 ms 显示为 1 ms。计时字段回填、文件关闭、最终 JSON 序列化及标准输出在总耗时终点之后。`report_written_at` 仍记录文件成功关闭后的业务时间。没有采集的耗时使用 null 或「未测量」。Slow 的计时说明位于高级诊断附录，`disk-growth` 复用同一计时口径。

**历史文件核验。** 参考 HTML SHA-256 为 `5edcaeee9e185771806e3b8c8806378e00b8897e562558f41ee57bee65ec1e53`，JSON 为 `2ae98e371f8d0349215437342c89073fa119d11e32ea87a42cc1cbf3052cf856`，均与修改前一致。离线兼容性回归覆盖旧数值、null、缺失字段以及历史 0 ms。

`ISOLATED_OFFLINE_TEST`：修改前定向 pytest 284 passed，修改后 298 passed。新增用例覆盖四种状态、大小写、未知状态及转义、原始 JSON 状态不变、初始化与采集时间区间、短写入、写入失败清理、覆盖保护和 HTML/JSON 计时一致性。全量 pytest 1458 passed、3 skipped、0 failed。跳过原因分别为 Windows 缺少 POSIX 权限位、当前令牌无法创建符号链接、真实系统测试未启用。全量运行将临时目录设在工作区 `.tmp`，避开隔离环境默认临时目录的写入限制。Ruff、Strict MyPy、`dba validate`（示例 Inventory，118 个 SQL 脚本）、`git diff --check` 通过。

`NOT_EXECUTED`：本轮修复后的真实数据库复验。现有报告只作为问题复现依据，P5 状态以 Backlog 为准。

## 78. V091-P5-WORKBENCH-001 — Slow 离线报告按 V1 工作台原型适配 — 2026-11-12

**范围。** 以 `docs/design/DBAToolkit_Incident_Workbench_V1_Final_Prototype.html` 为界面基线，只修改 `slow_report.html`、Presenter 与共享 Renderer。报告仍为离线 HTML：页签、会话详情抽屉、人工处置参考弹窗、展开、检索、复制和打印均在页内完成，不连接数据库，不自动刷新，不切换样本。未修改任何 Collector、Registry SQL 或 JSON 结构；`ReportModel`、`HumanReportPolicy`、finding/evidence 关联与 ReferenceCommand 纯文本机制保持不变。原有 DOM 锚点顺序与 `#current-requests`、`#blocking-chain`、`#session-appendix` 等 ID 保留。

**口径。** SQL Server 请求级 CPU、逻辑读、物理读、写入标注「自请求开始累计」，并注明单次采集、不是采样区间增量；MySQL 检查行数和影响行数标注「当前语句截至采集」。Oracle 当前请求不再展示 V$SQL 子游标累计 CPU、Elapsed、Buffer Gets、Disk Reads 和 Executions，改为「未采集」并说明原因；JSON 中的原始 `sql_cumulative_*` 字段和 Top SQL 功能不变。PostgreSQL 请求级资源计数显示「不支持」。客户端源端口：PostgreSQL 展示 `client_port`，SQL Server 与 Oracle 显示「未采集」及原因，MySQL 说明端口包含在 `PROCESSLIST_HOST` 原值中。新增「执行计划状态」：每条当前请求按原生标识关联，计划状态均为「未获取」，并给出独立 `dba diagnose … plan` 命令；没有当前请求时不展示历史计划。新增「资源证据采集范围」，说明采样区间增量、实例级资源压力和主机资源均未采集，不显示为 0。版本缺失时实例标签显示「（版本未知）」。

`ISOLATED_OFFLINE_TEST`：定向 pytest（`test_html_reports.py`、`test_slow.py`、`test_slow_report_interactions.py`）180 passed。新增四库参数化用例覆盖计划状态、计划命令、端口状态、资源范围、连接地址与页签；无当前请求时的计划状态单独覆盖。Node VM 交互用例实际执行，覆盖页签切换、跳转时展开所在折叠块、会话抽屉、Esc 与遮罩关闭、处置参考克隆去除 ID、委托复制、检索和打印。全量 pytest 1452 passed、3 skipped、0 failed；HEAD `a84812c` 收集 1450 项，本轮收集 1455 项。跳过原因同 §77。Ruff、Strict MyPy、`dba validate`（4 个资产，118 个 SQL 脚本）、`git diff --check` 通过。

`ISOLATED_OFFLINE_TEST`（人工查看用样例）：用合成数据生成 `reports/offline-samples/slow_workbench_{sqlserver,oracle,postgresql,mysql}_synthetic.html`（目录被 `.gitignore` 忽略）。数据全部为合成夹具，不是数据库采样。在浏览器中检查了 SQL Server 总览、执行计划页、会话抽屉和 Oracle 资源页。

`NOT_EXECUTED`：真实数据库报告复验，包括 §75 SQL Server 2022 A–D 和 `scripts/acceptance/verify_sqlserver_slow_snapshot.ps1` 对新模板 DOM 的宿主机解析。

## 79. V091-P5-WORKBENCH-001 — Slow HTML 回归 V1 Demo 六页工作台 — 2026-11-12

**范围。** 在 §78 基础上，以 `docs/design/DBAToolkit_Incident_Workbench_V1_Final_Prototype.html` 为唯一界面基线，重写 `slow_report.html`。报告分为诊断总览、活跃会话、阻塞分析、当前执行 SQL、执行计划、资源与引擎诊断六页。本轮修改 `slow_report.html`、`presenters.py`、`html_renderer.py` 和相关测试，并在 `docs/TODO.md` 登记待开发项。Collector、Registry SQL、诊断规则、JSON 契约、权限与凭据体系均未改动。Backlog 状态未改。已保留的锚点包括 `#summary`、`#root-assessment`、`#current-requests`、`current-request-NNN`、`#blocking-chain`、`#manual-action`、`#transactions`、`#evidence` 和 `#session-appendix`，以及验收脚本依赖的 status-pill、hero-description、请求卡片首个 `<code>`/`data-copy` 和截断标记。§78 的「资源证据采集范围」(`resource-context`) 已由「资源概况」(`wb-resources`) 取代；「执行计划状态」改为按影响排序的 TOP 5，状态显示为「待开发」或「不支持」，不再使用「未获取」。

**已实现（仅使用已采集证据）。**
- **诊断总览**：KPI 卡、首要结论与优先级、业务影响 TOP 3（点击打开会话抽屉）、关键资源指标、实例与采集信息。总览不重复 SQL、事务或来源。
- **活跃会话**：列表（Session、状态、用户、主机、客户端、程序、运行时间、CPU、逻辑读、物理读、等待事件、阻塞者），支持检索与分类筛选；行点击打开抽屉，展示完整来源、事务与 SQL。
- **阻塞分析**：根阻塞横幅、分层阻塞关系图、根阻塞者详情（事务、来源、当前 SQL 及可靠来源的上一条 SQL、原始锁等待资源）、下游受阻会话表和人工处置参考弹窗。
- **当前执行 SQL**：按影响排序的列表，可检索；行点击切换右侧请求详情和资源卡。
- **执行计划**：左侧 TOP 5、右侧 SQL、计划标识与计划树占位，并附独立 `plan` 命令及复制按钮。
- **资源与引擎诊断**：CPU、内存、I/O、并发、采样方式、区间增量六张卡，以及按引擎的专项信号。

上一条 SQL 只取会话绑定的可靠来源：SQL Server 根阻塞者 `last_sql`、Oracle 根阻塞者 `previous_sql`、PostgreSQL 根阻塞者 `last_query` 和非 active 会话的 `query`。其他会话显示「待开发」或「未采集」。

**状态口径。** 只使用「待开发」（功能未实现）、「未采集」（功能存在但本次无数据）、「不支持」（引擎或版本不提供）三种状态，0 只用于真实采集到的 0。
- 显示「待开发」的项目：实例与主机 CPU/内存/I/O、区间增量、锁对象（库/表/索引）、锁模式、内嵌计划与运行统计（SQL Server、Oracle）、SQL Server/Oracle 非根会话及 MySQL 的上一条 SQL、Oracle 当前执行 CPU/读取、MySQL 单语句 CPU、高并发无主导慢 SQL 诊断和各引擎专项信号。
- 显示「不支持」的项目：PostgreSQL/MySQL 的计划，PostgreSQL 的 CPU/读取，MySQL 的逻辑读/物理读。

这些项目已登记在 `docs/TODO.md`「Slow HTML 工作台中显示「待开发」的能力」，区间增量和资源契约只引用 V091-P2-TOPSQL-DELTA-001 与 V091-P2-RESOURCE-CONTEXT-CONTRACT-001。

`ISOLATED_OFFLINE_TEST`：全量 pytest 1457 passed、3 skipped、0 failed，跳过原因同 §77。
- **更新的测试**：21 个原有用例按新结构更新断言，即 KPI 取值、计划表结构、资源概况、SQL 详情与资源卡位置和导航标题；数据不变式保持不变。
- **新增的用例**：
  - PostgreSQL、MySQL、Oracle 三个参数化用例，覆盖上一条 SQL 来源、计数器状态、资源与专项信号状态、总览不含 SQL、抽屉明细数量与会话筛选；
  - SQL Server 根阻塞者有、无 `last_sql` 两个用例，覆盖上一条 SQL 只出现一次 `<code>`、锁对象与锁模式为「待开发」以及受阻会话表。
- **Node VM 交互用例**：实际执行并已重写，覆盖页签与锚点跳转、展开全部、会话检索与分类筛选组合、SQL 行选择、键盘操作与检索、计划选择、抽屉克隆去除 ID、TOP 3 打开会话、抽屉内链接关闭并跳转、Esc 与遮罩关闭、处置参考弹窗、复制和打印前后状态恢复。
- **静态检查**：Ruff、Strict MyPy（78 个源文件）、`dba validate`（4 个资产，118 个 SQL 脚本）、`git diff --check` 通过，仅有行尾提示。
- **验收脚本**：静态核对 `scripts/acceptance/verify_sqlserver_slow_snapshot.ps1` 的 DOM 解析依赖，status-pill、hero-description、`current-requests` 至 `blocking-chain` 区间内的请求卡片顺序、首个 `<code>`/`data-copy` 和截断标记均保留；未在宿主机运行该脚本。

`ISOLATED_OFFLINE_TEST`（合成样例）：重新生成 `reports/offline-samples/slow_workbench_{sqlserver,oracle,postgresql,mysql}_synthetic.html`，大小分别为 89,679、68,028、67,943、68,291 字节，SHA-256 前 16 位为 `85190E69F81EAB37`、`5D6A450F79D37189`、`8CC78CD610980BA1`、`C9425658600B5671`。数据全部为合成夹具。本地浏览器检查了 SQL Server 六页与会话抽屉、MySQL 资源页和 PostgreSQL 执行计划页（「不支持」状态）。浏览器中按 Esc 关闭抽屉未能在自动化中确认，改由 Node VM 用例覆盖。

`NOT_EXECUTED`：真实数据库报告复验，§75 SQL Server 2022 A–D，以及验收脚本在宿主机上对新模板的实际解析。

## 80. V091-P5-WORKBENCH-001 — 资源诊断边界：数据库内部指标与主机指标分离 — 2026-11-12

**范围。** 按用户确认的资源边界，只调整资源诊断的展示和文档；六页布局、颜色、组件、Collector、Registry 和数据契约均未改动，Backlog 状态未改。主机 CPU、MEM、IO 不在 Toolkit 范围，不开发 Zabbix API、操作系统 Agent 或主机资源采集，报告中也不再显示为「待开发」。`docs/TODO.md` 删除了「实例与主机 CPU、内存、I/O 资源卡」和泛化的「数据库引擎专项信号」两项，「高并发无主导慢 SQL」一项收窄为只依据数据库内部证据，并新增资源指标边界说明。§79 中关于主机资源、引擎专项信号和高并发诊断显示「待开发」的描述，以本节为准。

**展示。** 资源页和总览改为「数据库内部资源」，卡片按当前请求的数据库等待计数：
- **CPU 调度等待**：SQL Server 为可运行或 SOS_SCHEDULER_YIELD，Oracle 为 Scheduler 等待类；
- **内存授予等待**：SQL Server 为 RESOURCE_SEMAPHORE；
- **I/O 等待**：SQL Server 为 PAGEIOLATCH_*/WRITELOG/IO_COMPLETION，PostgreSQL 为 wait_event_type=IO，MySQL 为 wait/io/*，Oracle 为 User/System I/O 且正在等待；
- 原有的并发、采样方式和区间增量（待开发，归属 V091-P2-TOPSQL-DELTA-001）三张卡保留。

引擎视图不提供某类等待时显示「不支持」，活动请求采样不完整时显示「未采集」，采样完整时 0 为真实计数。

**资源判断规则。** 同类等待的请求不少于 2 个时，显示「数据库侧观测到资源竞争迹象」，并提示结合 Zabbix 或主机 CPU、MEM、IO 监控人工核实，同时说明单次采样不能确认容量瓶颈。未达到该条件时只显示「数据库侧未观测到足以判断资源瓶颈的竞争证据」；采样不完整时不给出判断。任何情况下都不给出扩容建议。引擎专项信号只保留有真实值或已有独立命令的项目，即 SQL Server 内存授予（memory-grant 命令）、PostgreSQL idle in transaction 计数和 WAL/复制槽（已有诊断命令）。其他引擎显示「本报告未提供该数据库的专项信号」。

`ISOLATED_OFFLINE_TEST`：全量 pytest 1460 passed、3 skipped、0 failed，跳过原因同 §77。新增 SQL Server 参数化用例覆盖三种情况：I/O 竞争迹象、证据不足和采样不完整。断言包括字段值、判断文本在总览和资源页均出现、不出现主机指标卡、不出现「扩容」。已有的四库用例更新为新卡片状态。Ruff、Strict MyPy（78 个源文件）、`dba validate`（4 个资产，118 个 SQL 脚本）、`git diff --check` 通过。重新生成的四个合成样例中，SQL Server 为「0 个请求」及「证据不足」；其余三库的合成夹具不含采样元数据，显示「未采集」且不给出判断。各样例均不含主机指标卡或「扩容」。样例大小分别为 88,990、67,407、67,635、67,603 字节，SHA-256 前 16 位为 `CCA9484AD35D9B29`、`5F09DB20E1C8A1FC`、`7BDF779D8010069A`、`11B0090216FAB558`。

`NOT_EXECUTED`：真实数据库复验；本轮未做浏览器视觉复查（仅文本与 DOM 断言）。

## 81. V091-P5-WORKBENCH-001 — 独立并发事务阻塞脚本

`DBAToolkit-LoadTest` 的四个阻塞入口现在从同目录 `local_config.py` 读取本机样例库的连接信息。启动负载无需 Toolkit CLI、Inventory、项目设置或监控凭据会话。`--sessions 100 --duration 45` 启动一个未提交的持锁事务及 99 个等待会话。`--role holder` 和 `--role waiter` 可在多个 PowerShell 窗口分开运行。持锁方回滚后，等待方完成冲突语句并各自回滚。README 第 8 节提供两种运行方式。

`ISOLATED_OFFLINE_TEST`：四库 100 会话预览及 PostgreSQL 两种分离角色预览均通过，使用 `python -S` 验证入口无需安装 Toolkit 即可启动预览。阻塞脚本与静态配置读取器定向 pytest 40 passed，覆盖仅使用合成 `local_config.py`、拒绝非本机或非样例库目标、并发等待及中断回滚。Ruff、Strict MyPy、README 的 PowerShell 语法检查和 `git diff --check` 通过。

`NOT_EXECUTED`：真实数据库阻塞与解除验证。实际本地配置和凭据未读取。

## 82. V091-P5-WORKBENCH-001 — SQL Server 请求状态分类与高并发/阻塞去重 — 2026-11-12

**问题来源。** 真实报告 `reports/sqlserver2022_slow_20261112_221142.html`（高并发）和 `reports/sqlserver2022_slow_20261112_221745.html`（阻塞）没有对应 JSON，本节据 HTML 分析。逐请求卡片已正确显示「挂起」，但并发聚合只统计可运行和运行中，其余 100 条显示为「其他或状态未采集」。`dba/slow.py` 只做原始小写字符串比较，没有 SUSPENDED 或 UNKNOWN 的计数。HIGH_CONCURRENCY 仅按请求数不小于 `high_concurrency_threshold` 触发，与阻塞图无关，所以在阻塞堆积中与 BLOCKING 重复出现。

**修复。**
- `sqlserver_request_status` 只规范化请求的 `status` 列：running、runnable、suspended、sleeping、background 映射为大写状态，NULL、缺失或无法识别的值记为 UNKNOWN，不使用 `wait_type` 推断状态。
- `details.concurrency` 新增以下字段，原有字段保持不变：
  - `request_status_counts` 和 `wait_type_counts`（LOCK、WAITFOR、OTHER、NONE）。两者分别统计，各自之和等于请求数；
  - `running_requests`；
  - `requests_in_blocking_graph`、`blocking_graph_complete` 和 `high_concurrency_explained_by_blocking`。
- 仅当同时满足以下条件时，不再生成 HIGH_CONCURRENCY，请求堆积改为计入 BLOCKING 的影响范围（在 BLOCKING 证据中追加活动请求数、图内请求数和阈值）：
  - 存在被阻塞请求；
  - 活动请求采样完整，且阻塞图没有异常；
  - 每条活动请求都在阻塞图中；
  - 请求数达到原有阈值。
- 未新增阈值，Finding ID 与 WAITFOR 和根阻塞可信度规则均未改动。
- 阻塞图不完整、存在异常，或有不在图中的请求时，保留 HIGH_CONCURRENCY，并在其证据中记录图内和图外请求数。
- HTML 的并发聚合、当前 SQL 列表摘要和资源页并发卡读取与 JSON 相同的计数，未知状态显示为「状态未知」。
- 其他引擎的逻辑未改动。

**行为变化。** 在场景 B（完整阻塞图覆盖全部请求）中，「高并发 SQL 聚合分析」小节和高并发问题不再出现。请求状态与等待类型计数保留在当前 SQL 列表摘要和资源页中。

`ISOLATED_OFFLINE_TEST`（合成夹具，非真实采集）：新增 `tests/test_slow_status_concurrency.py`，共 18 个用例。
- **A**：85 条请求（可运行 69、运行中 16），无阻塞，CPU 调度等待 69，101 个会话不计为请求。
- **B**：100 条请求全部挂起，根阻塞者为 55，被阻塞 99，LCK_M_X 99 条，WAITFOR 1 条，无重复 HIGH_CONCURRENCY。
- **C**：分别为另有 60 条独立可运行请求、活动请求截断、阻塞图异常三种情况，均保留 HIGH_CONCURRENCY。
- **状态规范化**：9 种取值。
- **D**：状态缺失记为 UNKNOWN，不推断状态，采样完整与截断两种情况下完整性均正确。
- **E**：在 A 和 B 中核对 HTML 与 JSON 的总数、状态、等待、根阻塞与被阻塞数、问题与严重度、结论和完整性是否一致。

`test_concurrency_and_ranking` 的精确期望字典只追加了新字段，原有断言未删改。全量 pytest 1478 passed、3 skipped、0 failed。Ruff、Strict MyPy（78 个源文件）、`dba validate`（4 个资产，118 个 SQL 脚本）和 `git diff --check` 通过。

生成的合成报告位于 `reports/offline-samples/`，JSON 已对 `credential_alias` 脱敏：

| 文件 | 字节 | SHA-256 前 16 位 |
|---|---|---|
| `slow_status_fix_high_concurrency_synthetic.html` | 537,204 | `476CF063664E3B76` |
| `slow_status_fix_high_concurrency_synthetic.json` | 56,950 | `163BE31C29699959` |
| `slow_status_fix_blocking_synthetic.html` | 674,290 | `F9F35BE79ABC4432` |
| `slow_status_fix_blocking_synthetic.json` | 202,190 | `65B97F87D4AAB5C8` |

`NOT_EXECUTED`：在 SQL Server 2022 上重新采集两种真实场景，并核对同次采集的 HTML 与 JSON。原两份真实报告没有 JSON，无法离线重渲染。

## 83. V091-P5-LOCK-OBJECT-SQLSERVER-001 — SQL Server 锁对象解析 — 2026-11-12

**基线。** 分支 `feature/top-sql-readable-report-20261108`，HEAD `a84812c`，开始时保留了原有未提交修改与未跟踪文件。Backlog 中没有同类任务，因此新登记为 `V091-P5-WORKBENCH-001` 的子任务，与资源上下文契约任务没有冲突。

**根因。** Slow 只从 `sys.dm_exec_requests.wait_resource` 取得等待方的原始资源字符串，没有采集 `sys.dm_tran_locks`。因此无法得到对象名，也无法区分持有方和等待方的锁模式，HTML 中这两项显示「待开发」。

**实现。**
- 新增经审查的只读 Registry 脚本 `lock_resources`，`max_rows` 2000，超时 10 秒。
  - 只读取存在 WAIT、CONVERT 或低优先级等待请求的资源，以及这些资源上的全部请求。
  - 资源身份包括资源类型、数据库 ID、关联实体 ID、资源描述和锁分区。
- 对象映射规则：
  - OBJECT 的实体 ID 就是 object_id，名称来自 `OBJECT_NAME`/`OBJECT_SCHEMA_NAME(id, resource_database_id)`，这两个函数接受数据库 ID，不需要动态 SQL。
  - KEY、PAGE、RID、HOBT、OIB 通过 `sys.partitions.hobt_id` 精确关联。
  - ALLOCATION_UNIT 先查 `sys.allocation_units`：type 1、3 按 hobt_id 关联分区，type 2 按 partition_id 关联。
  - 再由分区关联 `sys.objects`、`sys.indexes` 与 schema。
  - 所有目录视图的关联条件都限定 `resource_database_id = DB_ID()`。连接数据库以外的 HoBT 或分配单元资源明确降级，不切换数据库，不扫描实例。
- 两个 SQL Server Slow 配置（2008 R2 与 2012–2022）都把它路由为最后一个可选采集器，仅在完整阶段采集，不进入应急首轮。
- 结果写入 `details.lock_resources`：
  - 记录状态，以及原生资源字段、对象、解析结果与原因；
  - 列出每条请求的会话、模式、状态与角色（持有、等待、转换中、未知）；
  - 对等待方，记录其直接阻塞者是否持有同一资源；
  - 记录未匹配的锁等待。
- 每个根阻塞者增加 `held_lock_resource_ids`。HTML 阻塞页的「锁对象与影响范围」卡片和 CLI 根阻塞候选使用同一组文本，原有的「锁等待资源（原始值）」保留。
- 锁证据单独记录完整性：失败、截断或缺失只影响锁对象字段，不改变原有阻塞、事务、根阻塞、人工处置和总体完整性结论。CLI 出错时也不再用锁采集失败替代真正的错误。
- 部署权限文档补充了锁 DMV 与元数据可见性边界，未新增任何授权。

**测试调整。** 两项已有断言原先把 SQL Server 锁对象和锁模式固定为「待开发」，现改为「未采集」并附原因；这些夹具没有锁 DMV 样本。CLI 测试中允许执行的脚本清单加入了 `lock_resources`。其余已有断言没有改动。

`ISOLATED_OFFLINE_TEST`（合成夹具，非真实采集）：
- 新增 `tests/test_sqlserver_lock_resources.py`，共 40 个用例。
  - T1–T14 全部覆盖：OBJECT、KEY、PAGE/RID、HOBT/OIB、ALLOCATION_UNIT 的 5 种类型值、堆表、分区表、跨数据库（含 SQL 中每个目录关联都受数据库限定的静态检查）、元数据不可见、HoBT 缺失、8 种锁状态与持有/等待/转换分离、多资源不合并、未知或非对象资源类型。
  - 同一份 JSON 生成的 HTML 与 CLI 文本一致。
  - 锁证据缺失、失败或为空时，发现项、根阻塞、阻塞图、事务、可见性与完整性都与无锁证据的基线一致。
  - 截断和未匹配的锁等待都会标记关联不完整。
  - 版本路由校验通过，且只路由到 SQL Server。
- 全量 pytest 1518 passed、3 skipped、0 failed。Ruff、Strict MyPy（79 个源文件）、`dba validate`（4 个资产，119 个 SQL 脚本）、`git diff --check` 通过。
- 本地浏览器查看了已解析样例的阻塞页卡片。

合成样例位于 `reports/offline-samples/`，JSON 已对 `credential_alias` 脱敏：

| 文件 | 字节 | SHA-256 前 16 位 |
|---|---|---|
| `slow_lock_object_resolved_synthetic.html` | 89,089 | `EDB7D7F51D718B29` |
| `slow_lock_object_resolved_synthetic.json` | 48,298 | `8A8F47B24F3AB249` |
| `slow_lock_object_resolved_synthetic.txt`（CLI） | 3,952 | `164541605F0FF997` |
| `slow_lock_object_unresolved_synthetic.html` | 89,400 | `1A7431581367C5B5` |
| `slow_lock_object_unresolved_synthetic.json` | 50,150 | `7DBDC437F50123F7` |
| `slow_lock_object_unresolved_synthetic.txt`（CLI） | 4,263 | `F38C9DA7653FD1E7` |

**版本与权限边界。** 2008 R2–2019 仅为语法与路由层面的兼容目标，没有实机证据。§75 记录的待核验资产连接数据库为 `master`，而实验室阻塞负载位于 `AdventureWorks2022`。按本节规则，该负载产生的 KEY 锁会显示「资源位于其他数据库」，不会给出表名。只有资产连接到资源所属数据库，且监控账号在该库具备元数据可见性时，才能解析到表和索引。是否调整资产或授权由用户决定，Toolkit 未做任何更改。

`NOT_EXECUTED`：SQL Server 2022 真实阻塞 E2E，包括解析到具体库、表和索引，持有锁与等待锁，跨库或元数据不可见时的降级，以及同次采集的 HTML、JSON 与 CLI 一致性。

## 84. SQL-TIMEOUT-CONFIG-002 — 全局诊断 SQL 超时配置与有效超时计算 — 2026-11-13

**证据类型：** `ISOLATED_OFFLINE_TEST`（Windows 本机 `.venv`，Fake 驱动与单元测试；未连接任何数据库）。基线 HEAD `a84812c`，工作树含此前未提交的 P5 修改。

**范围。** 依用户 2026-11-13 批准的 C-11：新增 `diagnostic_sql_timeout_seconds`（默认 30、范围 1..60，仅 `diagnostics:` 段）；有效超时 = min(脚本 timeout_seconds, 全局值[, 可选的 query_timeout/call_timeout 上限])；`SqlDeadline` 绝对期限基础实现（未接入 Fetch）。四个 Adapter 改用共享解析；本机 `config/runtime.yaml` 删除 10 秒 query_timeout/call_timeout（用户选择；该文件被 git 忽略，新部署由代码默认值 30 秒生效）。

| 检查 | 结果 |
|---|---|
| `tests/test_sql_timeout.py`（新增） | 74 passed |
| 四引擎 Adapter 超时接入测试（新增） | 12 passed |
| 完整 pytest | 1604 passed，3 skipped |
| Ruff | All checks passed |
| MyPy strict（80 个源文件） | 无问题 |
| `dba validate` | 4 个资产，119 个 SQL 脚本通过 |
| `git diff --check` | 无空白错误 |

**覆盖的断言。** 默认 30 秒；较短脚本限制保留；全局值为上限；配置的旧上限只能缩短；非法值（0、61、布尔、字符串、null、浮点）被拒绝；引擎段和 Inventory 不能重定义全局值；`diagnostics:` 段可单独或与 slow 阈值一起配置；代码默认设置下 119 个脚本有效超时均为 min(脚本, 30)；本机 `config/runtime.yaml`（被 `.gitignore` 忽略，不随仓库发布）经一次性加载核对，四引擎全局值均为 30 且无旧上限，该核对不属于自动化测试；Deadline 在 Execute 与后续 Fetch 间共享，不重新获得完整时长；SQL Server/Oracle 按脚本下发，PostgreSQL/MySQL 连接级值与审计 `timeout_seconds`、`effective_timeout_seconds` 一致。

**测试调整。** `tests/test_sqlserver_top_sql_acceptance.py` 的设置替身由 `SimpleNamespace` 改为真实 `SQLServerSettings(query_timeout=10)`，因为验收脚本现在调用共享解析方法；超时值不变。

**行为变化。** 默认部署下 SQL Server 与 Oracle 的 3 个 Top SQL 脚本（声明 15 秒）实际下发值由 10 秒变为 15 秒。PostgreSQL/MySQL 连接级值仍为 5 秒。

`NOT_EXECUTED`：任何真实数据库执行；超时分类、Fetch 期限接入、PostgreSQL 网络保护、MySQL `max_execution_time` 与 `KILL QUERY`、数据库端取消验证（后续 `SQL-TIMEOUT-*` 任务）。

**人工验收决定（2026-11-13）。** 用户正式接受 `SQL-TIMEOUT-CONFIG-002` 的上述实现与配置决定：全局默认 30 秒；有效超时取单脚本、全局及可选更严格上限中的最小值；SQL Server/Oracle Top SQL 保持 15 秒；不恢复本地 runtime.yaml 的 10 秒限制；PostgreSQL/MySQL 当前 5 秒限制暂时保留。CONFIG-002 状态依 Backlog 规范更新为 `DONE`，父任务 `SQL-TIMEOUT-001` 仍为 `IN_PROGRESS`。本人工验收只依据已记录的实现和历史证据，不表示本轮重新执行了 §84 中的测试；§84 的 `1604 passed, 3 skipped` 等结果继续标记为原有 `ISOLATED_OFFLINE_TEST` 证据。

## 85. SQL-TIMEOUT-CLASSIFY-003 — 四种数据库错误与取消分类 — 2026-11-13

**证据类型：** `ISOLATED_OFFLINE_TEST`（Windows 本地 `.venv`，Fake Drivers/合成异常；未连接真实数据库）。

**实现范围。** 在现有 `DatabaseErrorDetail` / `ToolkitError.database_error` 中增量添加 `error_category` 和 `timeout` 证据对象；未创建并行错误体系，未修改 `SqlDeadline` 或全局有效超时算法。超时证据包含 Execute/Fetch 阶段、已应用/有效/脚本超时秒数、实际耗时、取消状态、连接处置、相对 SQL 脚本路径及适用的取消机制和原因。原有数据库类型、驱动、原生错误码、SQLSTATE、分类、消息、detail/hint/context 字段保留。相同的错误对象由 HTML、JSON、CLI 与 JSONL 路径呈现；固定机密与凭据继续脱敏，数据库驱动原始消息以清理后的形式保留。

**分类规则。**

- SQL Server：查询阶段 HYT00 → `TIMEOUT`，记录实际 Execute/Fetch 阶段及驱动报告；连接阶段 HYT01 → `TIMEOUT`，记录 `execution_phase=Connection`、连接超时机制，取消状态为 `NOT_APPLICABLE`。两者均不宣称已确认数据库停止；HY008 → `CANCEL_STATUS_UNKNOWN`；SQLSTATE 08 类 → `CONNECTION_LOST`；已知权限拒绝原生码 → `PERMISSION_DENIED`；其余查询错误 → `SQL_EXECUTION_ERROR`。
- PostgreSQL：57014 始终表示服务端确认 query_canceled，但不单独说明原因。服务端原始诊断消息明确报告 `statement timeout` / `statement_timeout` 时，才归类为 `TIMEOUT` 并保留已确认取消；明确报告用户请求或恢复冲突时保留对应原因。其余情况保持 `CANCELLED_CONFIRMED`、原因未知并保留脱敏后的原始错误，不根据配置阈值和耗时推断来源。08 类 → `CONNECTION_LOST` + 取消未确认；客户端网络超时且无服务端 57014 时 → `TIMEOUT` + `CANCEL_STATUS_UNKNOWN`。
- MySQL：3024 → `TIMEOUT` + 服务端 `max_execution_time` 确认；1317 → `CANCELLED_CONFIRMED`（服务端报告查询中断，取消来源未知；不归因于 Toolkit，也不声称执行了 KILL）；2013/2006 → `CONNECTION_LOST` + 取消状态未知；1205 → `TIMEOUT`；客户端 socket timeout → `TIMEOUT` + 服务端停止未确认。没有执行 KILL 或新增取消机制。
- Oracle：DPY-4024 → `TIMEOUT` + `DRIVER_REPORTED`；DPY-4011/DPY-1001/驱动明确报告 session dead → `CONNECTION_LOST` + 取消未确认；ORA-01013 → `CANCELLED_CONFIRMED` 且取消来源未知；已知权限错误 → `PERMISSION_DENIED`；code=0 不会单独推导超时/取消。
- `UnsupportedVersion` 映射为 `VERSION_UNSUPPORTED`；普通未识别数据库执行错误映射为 `SQL_EXECUTION_ERROR`。已应用适配器错误路径会记录连接是否已丢弃；关闭失败记为 `UNKNOWN`。保留当前适配器遇到查询错误时关闭连接的既有生命周期行为。

**测试与验证。**

| 检查 | 结果 |
|---|---|
| 初始分类测试轮次 | 34 passed；该结果早于本节末尾的最终验收前核对 |
| 最终验收前针对性核对 | 49 passed；该结果早于本轮 PostgreSQL 来源判定修正 |
| 全量 pytest（本轮最终验证） | **1650 passed，3 skipped，0 failed** |
| 完整 pytest（此前实现检查点） | 1640 passed，3 skipped；保留为历史结果 |
| Ruff（`dba`、`tests`） | All checks passed |
| Strict MyPy | 80 个源文件，无问题 |
| `dba validate` | 4 个资产、119 个 SQL 脚本通过 |
| `git diff --check` | 通过；仅有 Git 既存 LF→CRLF 提示 |

检查属于 Fake/合成离线验证，不是真实数据库端取消验证。原有 **1640 passed，3 skipped** 和先前 **49 passed** 均属于本轮最终验证之前的检查点；§84 的 **1604 passed，3 skipped** 仍是 CONFIG-002 的历史结果。此次最终复核发现 PostgreSQL 57014 曾在仅配置阈值与耗时达到时被推断为 `statement_timeout`；现已改为要求服务端诊断消息提供该原因证据，否则保留 `CANCELLED_CONFIRMED`、原因未知。最终全量 pytest **1650 passed，3 skipped**；Ruff（`dba`、`tests`）、Strict MyPy（80 个源文件）、`dba validate`（4 个资产、119 个 SQL 脚本）和 `git diff --check` 全部通过。没有连接数据库，系统测试 opt-in 已关闭；没有读取真实凭据、执行 GRANT/REVOKE/KILL、提交、推送或发布。此前工作区未提交内容均保留。

**未实现与边界。** 在本节实现检查点，`SqlDeadline` Fetch 接入和 PostgreSQL 客户端网络保护仍属 `DEADLINE-004`；MySQL `max_execution_time`/受控 `KILL QUERY` 仍属 `MYSQL-005`；各引擎真实服务端停止确认仍属后续取消验证任务。HYT00 与 DPY-4024 记录为驱动报告；MySQL 2013、网络错误和 socket 超时均不证明数据库端查询停止。未做真实 E2E，标签为 `NOT_EXECUTED`。CLASSIFY-003 代码实现时在 Backlog 标为 `CODE_COMPLETE`；其后用户已正式验收，Backlog 现为 `DONE`。父任务 `SQL-TIMEOUT-001` 继续为 `IN_PROGRESS`。

## 86. SQL-TIMEOUT-DEADLINE-004 — 四种数据库 Execute+Fetch 共享绝对 Deadline — 2026-11-13

**证据类型：** `ISOLATED_OFFLINE_TEST`（受控单调时钟、Fake Drivers 与合成错误；未连接真实数据库）。

**实现范围。** 在已经验收的 CONFIG-002 `SqlDeadline` 和 CLASSIFY-003 `database_error` 基础上增量接入 SQL Server、Oracle、PostgreSQL、MySQL 的单条诊断 SQL 执行流程。一条 SQL 从 Execute 开始到所有 Fetch 完成共用同一绝对 deadline；每次 Fetch 前检查剩余预算，不重新获得完整超时。Execute 后已耗尽时不 Fetch；Fetch 超时会通过现有 shared error 结构记录 `TIMEOUT`、阶段、有效/执行超时证据、取消状态未知和连接处置，并关闭/丢弃状态不确定的连接。未重构 Collector 或错误体系。

**四库行为和边界。**

- SQL Server：在新建 statement cursor 前应用本条 SQL 有效时间预算；Execute 和 Fetch 共享逻辑 deadline，Fetch 前检查剩余量。HYT00 仍按驱动报告处理；本地 deadline 耗尽不声称服务端已停止。ODBC 规范说明 SQLFetch 也可返回 HYT00，且该超时与 `SQL_ATTR_QUERY_TIMEOUT` 关联，故保留 Fetch 阶段的驱动超时分类。[SQLFetch Function — ODBC API Reference](https://learn.microsoft.com/en-us/sql/odbc/reference/syntax/sqlfetch-function?view=sql-server-ver17)
- Oracle：Execute 前及每次 Fetch 前根据同一 deadline 的剩余毫秒数动态设置 `call_timeout`，避免多次 round trip 重置完整预算；本地 deadline 到期时关闭连接，取消状态保持未确认。
- PostgreSQL：连接保留现有 `statement_timeout` 上限（取 REQUIRED 脚本配置的最严格值）；若单条 SQL 的有效 Deadline 更短，则执行前通过固定 `pg_catalog.set_config` 将服务端阈值收紧到同一个值，并用 Deadline 限制 psycopg 同步网络等待。没有实现协议级 CancelRequest；服务端返回 57014 才算服务端确认取消，本地等待到期则 `CANCEL_STATUS_UNKNOWN` 并丢弃连接。现有 5 秒连接级限制保留。
- MySQL：对客户端 socket 读写按共享 deadline 重新设定剩余等待上限，Fetch 阶段通过同一个 deadline 检查；超时为客户端等待到期，不能证明 MySQL 停止查询，连接会关闭/丢弃。保留现有 5 秒连接级限制；未实现 `max_execution_time`、控制连接或 `KILL QUERY`，这些仍属于 MYSQL-005。

**自动化测试覆盖。** 新增受控 fake-clock/fake-driver 测试，覆盖正常 Execute、Execute 消耗部分预算后 Fetch 仅使用余量、多次 Fetch 不重置、Execute 完成时 deadline 已耗尽、Fetch 和多批次累计耗时耗尽、脚本上限低于全局及全局低于脚本的有效值、四个 Adapter 的剩余时间应用和超时连接丢弃，以及超时后 HTML/JSON/CLI/JSONL 保留先前有效结果并一致输出分类/脱敏信息。没有用 sleep 作为唯一的驱动机制验证，也未连接真实数据库。

| 检查 | 结果 |
|---|---|
| 全量 pytest | **1663 passed，3 skipped** |
| Ruff (`dba tests`) | All checks passed |
| Strict MyPy (`dba`) | 80 个源文件，无问题 |
| `dba validate` | 4 个资产、119 个 SQL 脚本通过 |
| `git diff --check` | 通过；Git 报告既有 LF→CRLF 规范化提示 |

全量测试的 pytest 临时目录被限定在工作区下，避免既有 SQL Server acceptance 测试写入隔离环境不可写的系统 `%TEMP%`；在该配置下完整重跑通过。此次修改保留所有既有未提交内容，没有修改 Inventory、执行真实数据库连接、KILL、GRANT、REVOKE、提交、推送或发布。真实数据库执行和数据库端停止行为观测为 `NOT_EXECUTED`；Fake Driver 结果只证明离线逻辑和等待上限，不证明服务端停止。

**遗留边界。** PostgreSQL 没有协议级 CancelRequest；MySQL 服务端 `max_execution_time`/受控 `KILL QUERY` 留给 MYSQL-005；四引擎真实服务端停止验证留给对应取消验证任务。若 MySQL 所用真实驱动不暴露可安全包裹的 socket/read 路径，deadline 保护会 fail closed 并返回错误，而不会静默执行无界等待。Collector 模块隔离及独立模块超时后重连仍属于后续任务。DEADLINE-004 在 Backlog 标记 `CODE_COMPLETE`，等待人工验收；`SQL-TIMEOUT-001` 父任务保持 `IN_PROGRESS`。

## 87. SQL-TIMEOUT-MYSQL-005 — MySQL 服务端 SELECT 超时与 SHOW 取消边界

用户接受 DEADLINE-004 的 PARTIAL 交付并授权本任务。DEADLINE-004 最终复核的历史证据为 1667 passed / 3 skipped，Oracle 的驱动调用累计上限、ODBC 的累计上限和物理连接丢弃仍有未验证边界。

**SELECT。** 首次受审查 Preflight 读取 VERSION()、@@version_comment 和 CONNECTION_ID()，校验后把 ID 与当前 Adapter 连接对象绑定。通过账号安全检查后，识别到的 MySQL 5.7.8 至 5.7 系列末版、8.0 和 8.4 使用固定、参数化的 Adapter 内部 SESSION SET。旧版 5.7 和产品未知时仍用客户端保护，MariaDB 走现有拒绝路径。维持 Registry 的 SQL 检查和原脚本文本。服务端值按 SET 前所剩 Deadline 向下取整至整数毫秒，小于 1 ms 直接超时。SET 往返会进一步消耗客户端预算，发送 SELECT 前复核；SELECT 和全部 Fetch 仍共享原客户端 Deadline。服务端相对计时从语句执行开始，不能扣除 SET 本身的网络往返。

**SHOW 与连接身份。** 四条 SHOW 保留客户端上限。KILL 仅以当前数字 ID 在服务器查找线程；连接断开后的 ID 复用可能发生于核验和发送之间，且同账号权限也可能覆盖其他会话。当前没有能把目标身份与 KILL 原子绑定的受审查机制，故不创建控制连接或提交取消。超时和连接丢失按 CANCEL_STATUS_UNKNOWN 处理并丢弃连接。3024 是服务端超限确认，1317 是服务端中断但来源未知，2006/2013 不能确认停止。CANCEL_REQUEST_ACCEPTED 和 CANCEL_FAILED 无执行路径，相关控制连接测试不适用。独立查询可通过新连接和完整 Preflight 执行；自动调度属于 ISOLATION-006。

**证据。** ISOLATED_OFFLINE_TEST 覆盖版本门控、MariaDB 识别、ID 绑定及清理、毫秒取整、SET 耗尽预算、3024/1317/2006/2013、SHOW 超时不创建控制连接、重连后的独立查询，以及 HTML/JSON/CLI/JSONL 一致性和错误脱敏。真实 MySQL、控制连接实际授权、KILL 和服务端停止观测均为 NOT_EXECUTED。未执行 GRANT/REVOKE。

最终全量 pytest **1682 passed，3 skipped**。Ruff、Strict MyPy（80 个源文件）、dba validate（4 个资产、119 个脚本）和 git diff --check 通过。

## 88. Real database acceptance preflight — 2026-11-14

MYSQL-005 and V091-P5-LOCK-OBJECT-SQLSERVER-001 remain NOT_EXECUTED for this run. The Codex-managed PowerShell Core process could not access the Windows host CIM provider. No asset lookup, Credential Session lookup, database connection, workload, permission change or cancellation was attempted. Existing historical evidence remains recorded above.
