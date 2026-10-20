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
