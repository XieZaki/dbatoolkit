# DBA Toolkit V0.9.1 integration regression and capability inventory

Date: 2026-10-28. Package version: 0.9.0. Dirty working tree; results describe this working tree, not a clean release. SQL Server Memory Grant and PostgreSQL WAL conditional closures do not approve a production release.

## Capability inventory

| Engine | Version targets / reviewed routes | Current capabilities | Principal limitations and monitor requirements |
|---|---|---|---|
| SQL Server | Target 2008 R2+; reviewed families through 2022 | Basic collectors, slow, disk-growth, replication (2012+), active-sql, top-sql, cached plan, memory-grant | DMV permissions depend on version: VIEW SERVER STATE on older versions and VIEW SERVER PERFORMANCE STATE on 2022 for appropriate features. Memory Grant nonempty live paths and fault/version gates remain deferred in Acceptance §26. Driver 17 lab fallback and TLS exception are not a production TLS claim. |
| PostgreSQL | Target 14+; relevant core profiles 13–18 (13 retained code, not product target); top-sql 14–18 | Basic collectors, slow, disk-growth including WAL retention, replication, top-sql | pg_monitor/CONNECT for core features; top-sql needs pre-existing pg_stat_statements and pg_read_all_stats. No supported cached plan collector. WAL live nonempty/standby/fault/cross-version gates remain in §27. |
| MySQL | Target 5.7 and 8.0+; reviewed 5.7/8.0/8.4 families | Basic collectors, slow, disk-growth, replication, digest top-sql | PROCESS and performance_schema visibility as feature requires; REPLICATION CLIENT for relevant binlog/replication evidence. Digest and current connection identities differ. Cross-user plan route has upstream privilege limitation and is not implemented. |
| Oracle | Target 11gR2+; reviewed listed 11gR2 through 26ai families | Basic collectors, slow, disk-growth, replication, child-cursor top-sql, cached plan | Thin mode; CREATE SESSION and feature-specific direct object SELECT. Oracle plan MVP reads current cursor/cache only, not historical identity or actual row-source stats. Other versions and optional objects are not automatically live-verified. |

Target families are not universal compatibility guarantees; actual route and permissions are feature/version-specific. CLI supports list, show, validate, doctor, runtime-info, credentials, check, status, test-connection, collect, permissions and diagnose slow/plan/active-sql/top-sql/memory-grant/disk-growth/replication; individual engines reject unsupported scenarios. `plan` requires an explicit SQL Server session or Oracle SQL_ID/child identity. Toolkit produces reference actions only and does not execute KILL, GRANT, parameter changes or slot deletion.

## Automated checks — ISOLATED_OFFLINE_TEST

- Full pytest: **1070 passed, 18 failed, 2 skipped** in 98.09 seconds. Several failures explicitly show `CredentialSessionUnavailable` / Windows named-pipe access denied or sandbox Temp access denied; individual failures are not all root-caused. This is a failed suite, not a product-wide PASS. No tests were deleted or relaxed.
- Failed cases: `test_cli_show_and_test_connection_expose_only_effective_safe_fields`; four `test_credential_session` tests (encrypted resolver/session hits/IPC lifecycle/session save); three `test_encrypted_credentials` tests (JIT save and resolver fallback); `test_cli_group_tag_and_combined_filters`; four `test_jit_credentials` tests; `test_check_keeps_account_security_and_permission_readiness_separate`; `test_doctor_driver17_warning_and_oracle_thin`; two SQL Server check/status tests; `test_acceptance_runs_exactly_one_toolkit_collection_per_metric`. See pytest output for exact node IDs. The failing temporary-directory case reports WinError 5 in the sandbox; credential-session failures include the sandbox pipe boundary. Other failures require independent triage.
- Ruff: PASS. Strict mypy dba: PASS, 73 source files. `dba validate`: PASS, 4 assets and 122 scripts. `dba list`: PASS. `git diff --check`: PASS (line-ending warnings).
- Repository Secret Scanner: **FAIL, one candidate in `tests/test_performance_triage.py:205`**. It is not classified here as a real secret or false positive. A scoped private audit is required before committing or sharing; scanner output did not reveal the candidate value.

## Real CLI checks — WINDOWS_LOCAL_REAL_E2E

Authorized non-isolated Windows host; project .venv; each of the four specified Inventory assets was checked to be enabled, local, and `environment=test`. Existing monitor credentials were available. Connections and Toolkit commands are read-only; no new database, account, GRANT, data changes, or test load was introduced.

| Asset | Connection / server-info | slow | disk-growth | replication | top-sql | Additional |
|---|---|---|---|---|---|---|
| oraclefree | PASS / PASS; database banner Oracle AI Database 26ai Free 23.26.3.0.0 | command success, PARTIAL | command success, PARTIAL | command success, COMPLETE | command success, COMPLETE | Oracle historical SQL_ID/child/instance/container/hash target returned AVAILABLE; same-run HTML created. |
| mysql84 | PASS / PASS; 8.4.11 | command success, PARTIAL | command success, COMPLETE | command success, COMPLETE | command success, PARTIAL | MySQL plan unsupported; no cross-user permission escalation. |
| pg16 | PASS / PASS; 16.15 | command success, COMPLETE | command success, COMPLETE | command success, COMPLETE | command success, COMPLETE | Existing WAL real empty-slot evidence is in §27; no slots created. |
| sqlserver2022 | PASS / PASS; 16.0.4305.5 | command success, COMPLETE | command success, COMPLETE | command success, COMPLETE | command success, COMPLETE | active-sql and memory-grant commands succeeded with COMPLETE; actual nonempty Memory Grant gates remain deferred. SQL Server plan skipped because no verified current session target was provided. |

Command success and `COMPLETE` denote the returned report's observed collection state, not full proof of every parser field, permission, risk scenario or version. E2E HTML files were created under `reports/integration_v091_20261028/`. JSON was parsed in memory to check success/completeness; the automated orchestration did **not** preserve same-run JSON or compare all JSON/HTML fields, so cross-format parity for these calls is NOT VERIFIED. No claim of nonempty samples is made. Prior scoped evidence, including PG WAL and Oracle plan parity, is retained in Acceptance §§22, 26 and 27 and was not silently promoted to this full matrix.

## Security and remaining gates

Existing architecture uses dedicated monitor providers and reviewed Registry SQL; normal commands do not have automatic grant or KILL paths. This round did not execute administrator actions or alter permissions. Existing lab administrator changes and scope are recorded in Acceptance; this report is not an exhaustive historical grant audit. Scanner failure prevents asserting that the entire dirty working tree is free of secrets, and generated reports remain local/ignored.

- Memory Grant: nonempty Granted/Waiting, live identity/handles, nonempty JSON/HTML, actual denial/timeout/truncation, other SQL Server versions: §26 deferred and not PASS.
- PostgreSQL WAL: real physical/logical slots, standby, denial/timeout/truncation, PG14/15/17/18, and live nonempty report parity: §27 deferred and not PASS. Last HTML unavailable-state repair has offline verification only.
- Oracle/MySQL PARTIAL diagnosis needs permission/source-evidence review. Actual Oracle reported version strings differ by source (`23.0.0.0.0` driver vs database banner `23.26.3.0.0`); no incompatibility is inferred without further investigation.
- Priority: (1) privately classify the scanner finding and prevent exposure; (2) isolate the 18 test failures in an appropriate controlled environment without weakening credential protections; (3) determine PARTIAL evidence and permissions; (4) verify same-run JSON/HTML parity and scoped feature routes; (5) execute deferred production gates when safe environments exist.

Overall maturity: useful test-lab diagnostic coverage with major version, permission, nonempty and security/test audit gaps. **Not approved for production release.**

## Follow-up — 2026-10-29

The Git-index scanner finding was inspected without displaying the literal. It is a hard-coded value inside `test_evidence_references_never_copy_captured_sql_or_credentials`, and the identical value is used in a negative assertion to verify redaction; the immediate finding is a test-only fixture, not evidence of a production credential. The scanner nevertheless still returns FAIL; this classification does not assert that untracked or unstaged files were scanned or that the repository has a clean scan. No scanner or test was modified.

The initial four-asset parity attempt could not establish database connections because the local Docker engine was stopped. A helper incorrectly called the absence of a report object `ALIAS_NOT_REDACTED`; this was a helper classification error, not a demonstrated disclosure. Docker Desktop was started using the existing installation; the four existing containers started, and Oracle was retried only after its health status became healthy. No containers or database objects were created.

`WINDOWS_LOCAL_REAL_E2E`: subsequent same-invocation disk-growth JSON/HTML results were saved under `reports/integration_v091_parity_20261029/`: pg16, mysql84 and sqlserver2022 were COMPLETE, oraclefree PARTIAL. In each report the database ID and version appeared in HTML and the exported JSON alias was redacted. This is a limited shared-field check, not full field or nonempty-row parity. Oracle optional sources `disk_fra`, `disk_fra_usage`, and `disk_archive_volume` returned `CapabilityUnavailable`; readiness was PARTIAL. No grant was applied.

`ISOLATED_OFFLINE_TEST`: eight initially failed tests were individually rerun and passed: four MySQL/runtime/SQL Server check-status tests and four JIT credential tests. Two initially failed SQL Server `show` tests still fail with `CredentialSessionUnavailable` when the credential session pipe is denied. Source trace: `dba/cli.py` show selects driver via `database_runtime_status`; `dba/runtime.py` delegates to full `doctor`, which calls `credential_store_status`, which calls credential session status. Thus a credential-independent SQL Server inventory show can fail when the unrelated credential IPC is inaccessible. This is a confirmed behavior defect under the reproduced access-denied condition; no product code was changed in this audit. The remaining eight originally failed test cases have not been individually adjudicated. The original full-suite result remains 1070 passed, 18 failed, 2 skipped; partial reruns do not convert it to a full-suite pass.

The eight remaining original failed cases were rerun individually in the same sandbox: seven stopped at `credential_session.py:303` with credential pipe access denied, and the SQL Server Top SQL acceptance helper stopped at sandbox Temp WinError 5. These are reproduced execution-context restrictions, not evidence of a real database outage. The two `show` failures still reveal an unnecessary dependency on credential IPC under that restriction; the other eight initially failing cases passed individually. The full-suite result remains unchanged and no clean full-suite pass is claimed.
# V0.9.1 Release stabilization — 2026-10-29

## P0: credential-independent inventory show

SQL Server `show` previously called `database_runtime_status`, which delegated to `doctor` and queried Credential Session IPC. Changed `dba/cli.py` to take the selected ODBC driver from `runtime_info` directly; full doctor remains unchanged. It does not connect to a database or ask for credentials. Focused tests cover four engines, denied/locked/ready session fixtures, Driver 18/17/no driver and existing output. Focused run: 51 passed, including scanner tests and the two original show failures. The normal-host full pytest also exercised them successfully.

## P0: indexed Secret Scanner finding

The index finding was a test-only fixed dummy literal in `test_evidence_references_never_copy_captured_sql_or_credentials`, also used as a negative assertion. `scripts/check_repository_secrets.py` now recognizes only that exact dummy value inside that exact test function and exact file. Tests prove other files, functions and values remain findings. No real credential value was displayed. The actual index-based scanner returned PASS; this does not scan every untracked or unstaged file and is not a full workspace secret audit.

## Full normal-host pytest

`WINDOWS_LOCAL` pytest using project .venv and a new independent basetemp: **1121 passed, 2 failed, 4 skipped**. The full suite is FAIL; the result is not equivalent to a clean release gate. The failures were identified and reproduced individually:

1. `tests/test_encrypted_credentials.py::test_jit_save_uses_initialized_encrypted_store_and_next_resolver_reuses_it`: active user encrypted session was used in place of that test's temporary encrypted store; assertion reports Encrypted Credential Session rather than Encrypted Local Store.
2. `tests/test_encrypted_credentials.py::test_encrypted_provider_does_not_fallback_when_store_is_corrupt`: active session refers to another store, so the temporary corrupt store was not checked and JIT prompt was reached. This is a potential cross-store isolation / fail-closed security defect, not a database failure. The resolver does not validate that the active session belongs to the specific store passed to it before lookup or enrollment. No unsafe workaround, skip, assertion relaxation or product credential-security fix was introduced in this round.

The session metadata lists the test alias `new-alias`. No pre-run alias baseline was captured, so the current evidence cannot determine whether this run created it or it already existed. No alias was removed or overwritten by the agent after discovering the issue. The user should review that alias in the normal secure credential workflow; do not delete it without ownership confirmation.

The four skips were: two IPC lifecycle/save tests deliberately skip to avoid replacing an active user credential session; one POSIX-mode permission test is inapplicable on Windows; one real system CLI suite is opt-in and intentionally not enabled by ordinary pytest. The previous run's two skips were not re-labelled as having been executed.

## Original 18 failures

- The two SQL Server show tests pass in the focused run and normal-host full run.
- Eight previously passing individual reruns also passed in the normal-host full run.
- Of the remaining eight, five pass in the normal-host full run; one session lifecycle test and one session save test are now SKIPPED because a real session is active, so they are not claimed PASS. The Top SQL temp-directory case passed on the normal host.
- One original test (`test_jit_save_uses_initialized_encrypted_store_and_next_resolver_reuses_it`) is among the two final normal-host failures; the other corrupt-store failure is newly exposed by the active session. Because the original 18 overlap the categories above, each is identifiable from the earlier original failure list and this outcome; no isolated subprocess pass is promoted to a full-suite pass.

## PARTIAL classification

Oracle slow: optional V$LOCK/V$LOCKED_OBJECT evidence unavailable, permission readiness and blocking visibility PARTIAL; required session/request/blocking fallback was observed, not a full permission pass. Oracle disk-growth: optional `disk_fra`, `disk_fra_usage`, `disk_archive_volume` returned CapabilityUnavailable, readiness PARTIAL. MySQL slow: `events_waits_current` consumer disabled, using reviewed NULL-wait fallback. MySQL Top SQL: permissions READY but Performance Schema visibility PARTIAL; reported disabled and untimed instruments. These are observed capability/configuration limitations, not permission grants or proof of product error. Existing four-database E2E evidence remains valid for its earlier exact scope; this round did not rerun databases.

Ruff, strict mypy (73 files), dba validate (122 SQL), git diff --check: PASS. Current status: **NOT READY for release-candidate acceptance** until cross-store session isolation and full-suite failures are resolved and retested. SQL Server Memory Grant and PostgreSQL WAL deferred gates are unchanged; no production release approval is inferred.

### Original 18 case-by-case disposition on normal-host full run

| Original test node (module::test) | Normal-host result |
|---|---|
| test_connection_credentials::test_cli_show_and_test_connection_expose_only_effective_safe_fields | PASS |
| test_inventory_scale::test_cli_group_tag_and_combined_filters | PASS |
| test_mysql::test_check_keeps_account_security_and_permission_readiness_separate | PASS |
| test_runtime::test_doctor_driver17_warning_and_oracle_thin | PASS |
| test_sqlserver::test_cli_check_and_status_human_and_json | PASS |
| test_sqlserver::test_check_version_mismatch_suppresses_permission_readiness | PASS |
| test_jit_credentials::test_missing_alias_jit_save_y_persists_and_original_command_gets_credential | PASS |
| test_jit_credentials::test_save_n_is_ephemeral_and_the_next_command_prompts_again | PASS |
| test_jit_credentials::test_secure_store_unavailable_uses_ephemeral_when_confirmed | PASS |
| test_jit_credentials::test_save_failure_requires_ephemeral_confirmation_and_secure_failure_defaults_no | PASS |
| test_credential_session::test_explicit_encrypted_resolver_uses_session_before_store_prompt | PASS |
| test_credential_session::test_session_hits_for_500_aliases_do_not_run_argon2id | PASS |
| test_credential_session::test_current_platform_ipc_lifecycle | SKIPPED: active per-user session |
| test_credential_session::test_active_session_can_securely_save_new_alias_and_invalidates_session | SKIPPED: active per-user session |
| test_encrypted_credentials::test_jit_save_uses_initialized_encrypted_store_and_next_resolver_reuses_it | FAIL: active session selected instead of temporary store |
| test_encrypted_credentials::test_resolver_uses_os_first_and_interactive_when_no_persistent_provider | PASS |
| test_encrypted_credentials::test_jit_save_y_persists_in_existing_os_store_and_save_n_does_not | PASS |
| test_sqlserver_top_sql_acceptance::test_acceptance_runs_exactly_one_toolkit_collection_per_metric | PASS |

Additionally, `test_encrypted_credentials::test_encrypted_provider_does_not_fallback_when_store_is_corrupt` failed in the normal-host run, though it was not among the previous 18. The two session tests are skipped, not passed. The original sandbox 18 remain a historical run and are not retroactively rewritten.

## Credential SQLite Provider integration — 2026-10-30

User approved production-source integration and explicit legacy-to-SQLite migration development, with testing restricted to disposable Stores. Product version remains 0.9.0. No real user Store, new-alias, database account, permission or database was read, migrated, reset or modified. No commit/push occurred.

Implementation: dba/sqlite_credentials.py (opt-in Windows SQLite Provider, AES-GCM/Argon2id, private SID/SYSTEM ACLs, file-ID/UUID binding, DELETE journal and synchronous FULL transactions, mutation/rekey and quarantine-aware copy/verify migration); dba/credential_session.py (protocol 2, server-side target checks, bound SQLite GET/SAVE/SET/DELETE/metadata); dba/cli.py and dba/encrypted_credentials.py (existing command/Resolver routing, no automatic Provider switch). Migration publishes authenticated readiness through a transaction, not a post-validation rename. Old source bytes remain unchanged; failed/incomplete targets are private and rejected. See docs/design/credential-sqlite-provider.md for manual activation, verification, rollback restrictions and exact threat boundaries.

Targeted credential/repository suite: 145 passed, 1 skipped before the final full run; subsequent final-source full regression includes the last safety checks. Final Windows-host run, using D:\DBA\DBAToolKit\.venv\Scripts\python.exe: 1157 passed, 2 skipped, 0 failed, 43.17 seconds. Log: .tmp/credential-stabilization-full-pytest-final.txt. The skips are POSIX permission bits on Windows and the opt-in real database system-test (DBA_TOOLKIT_SYSTEM_TESTS not enabled). Both original failing credential tests now pass. Session IPC tests are isolated and run, not skipped due to an existing user Session. Credential file/IPC tests are ISOLATED_OFFLINE_TEST; no real database E2E was executed.

Product fixtures cover normal/new and legacy reads, unchanged aliases across four database IDs, cross-Store GET/SAVE/SET/DELETE/metadata refusal with unchanged wrong-target bytes, legal SAVE then GET, password/key and payload authentication failure, unsupported format, busy transactions, three concurrent process writers, external replacement attempts, interrupted pre/post-commit transactions and actual hot-journal recovery, rekey, migration interruption/readback failure quarantine and the temporary CLI lifecycle. Prototype fault tests remain separate historical supporting evidence.

Final gates: Ruff PASS; strict MyPy PASS (74 source files); dba validate PASS (4 configured instances, 122 SQL scripts); git diff --check PASS. Secret Scanner PASS on Git-index content, plus explicit current-working-tree scan of the 18 implementation/test/security-document files: zero findings. This is not a claim that every unrelated untracked/worktree file was audited. SQLite main and journal/WAL/SHM filenames are ignored and rejected by scanner tests; no whitelist expansion was made.

RC remains BLOCKED for unrestricted/default credential write use: legacy JSON mutation still has its known replacement window. SQLite is not default and currently rejects non-Windows use. Same-user arbitrary writes/process inspection/valid rollback, parent-directory replacement, power loss/hardware failures and network-filesystem behavior remain outside verified protection. A follow-up release policy/security decision is required; full pytest success does not settle these risks. SQL Server Memory Grant and PostgreSQL WAL conditional closures and all deferred gates remain unchanged. No production release is approved.

## Credential release-policy stabilization update — 2026-10-30

The approved release-mode policy now blocks all old JSON Encrypted Provider mutations at the Provider boundary, with independent Session-server and CLI rejection. Old JSON remains an authenticated, target-bound read and migration source. SQLite is the explicit writable backend and is never selected by `auto`; Windows Credential Manager behavior and Inventory `credential_alias` are unchanged. No real Store migration or database E2E was performed.

After adding the isolated SQLite JIT SAVE → subsequent Resolver GET regression, the focused credential/IPC and old/new Provider suite passed **165 tests with 1 Windows-inapplicable skip**. Final normal Windows host full pytest: **1175 passed, 2 skipped, 0 failed** in 44.40 seconds. The other skip is the opt-in real database test because `DBA_TOOLKIT_SYSTEM_TESTS` was unset. Log: `.tmp/credential-readonly-full-pytest-final.txt`.

SQLite uses DELETE journal and verifies `synchronous=EXTRA` (numeric mode 3) for every opened connection. A small isolated benchmark found median transaction latency 1.4550 ms at FULL and 1.5019 ms at EXTRA; the sample is not representative. SQLite documents EXTRA as adding a directory sync after rollback-journal unlink in DELETE mode ([pragma reference](https://www.sqlite.org/pragma.html#pragma_synchronous)); this does not prove hardware or power-loss durability ([atomic commit assumptions](https://www.sqlite.org/atomiccommit.html)).

Ruff, strict MyPy (74 source files), `dba validate` (4 configured assets, 122 SQL scripts), `git diff --check` and the indexed Secret Scanner passed. The scanner covers Git-index content; a separate scan of 21 current-worktree credential implementation, test, security and status files found no findings. No new scanner exception was added. This supplemental scope does not cover every unrelated untracked file.

All credential verification used temporary Stores and isolated IPC; no real user Store or `new-alias` was accessed, migrated, modified or reset. No database connection was attempted. `CRED-MAINT-001` remains `IN_PROGRESS` pending the user’s SQLite-scoped RC decision. This is not unrestricted credential-backend approval or production release approval. Memory Grant and PostgreSQL WAL deferred lists remain unchanged; no commit or push occurred.

## SQLite-scoped RC review — 2026-10-30

Review is limited to a Windows-local, explicit SQLite Provider, legacy JSON read-only compatibility and existing read-only diagnostics. No real credential file, `new-alias`, local high-privilege test config, or database was accessed. SQLite-specific live database integration remains unverified; the four-engine E2E matrix above is earlier evidence under its original credential/provider scope.

Targeted RC tests: **152 passed, 1 skipped**. They cover CLI dispatch lifecycle with injected prompts and isolated subprocess IPC, JIT SAVE/Resolver reuse, old JSON writes denied without changing source bytes, read compatibility, same alias/different Store isolation, migration correct copy/manual SQLite selection, wrong-password/damaged-source refusal, interrupted/readback-failed quarantine, no stale re-migration over an existing target after new SQLite writes, rollback-journal recovery and DACL validation of the temporary Store parent/main/journal. The skip is Windows-inapplicable POSIX permission-bit validation.

Full current-tree Windows offline pytest: **1181 passed, 2 skipped, 0 failed** in 50.66 seconds. An initial run hit WinError 5 while cleaning a sandbox-managed system Temp directory; setting TEMP/TMP to `.tmp/pytest-temp-rc` allowed the full suite to finish. Skips: POSIX permission bits and the opt-in database test (`DBA_TOOLKIT_SYSTEM_TESTS` unset). The earlier authorized Windows-host full test after product-source stabilization remains 1175 passed / 2 skipped. No database E2E was run in this RC review.

Ruff, strict MyPy (74 source files), `dba validate` (4 assets/122 SQL scripts), `git diff --check`, and indexed Secret Scanner passed. Supplemental working-tree scanner checked 21 relevant credential/security/status files with 0 findings. Unrelated untracked files are outside that supplement; no scanner exception changed. No P0/P1 product defect was confirmed in scope. A conditional close recommendation is recorded in Acceptance §30. At the time of this review, Backlog remained `IN_PROGRESS` pending user approval; the subsequent approval and current `DONE` status are recorded below. Explicitly selecting a stale legacy Store after new SQLite writes remains a documented operator risk; there is no silent fallback or rollback command. Memory Grant/WAL deferred E2E lists remain unchanged. No commit/push occurred.

## CRED-MAINT-001 formal conditional closure — 2026-10-30

The user approved conditional closure; the canonical Backlog status is now `DONE`. The accepted boundary is Windows-local encrypted SQLite only when explicitly selected, plus authenticated, target-bound read-only access to legacy JSON. SQLite is not selected by `auto`. No real credential Store was accessed or migrated, and real migration remains unapproved. SQLite-to-live-database credential integration is unverified.

This close relies on the previously recorded RC evidence, not a rerun: 152 credential-focused tests passed / 1 skipped; current-tree Windows offline full pytest 1181 passed / 2 skipped / 0 failed; Ruff, strict MyPy, `dba validate`, `git diff --check`, indexed Secret Scanner and the 21-file supplemental worktree scan passed. The database E2E evidence remains limited to its original provider and feature scope.

Residual limits include same-user tampering, parent-directory replacement, authenticated file rollback, sudden power loss and storage-sync failures, older Toolkit versions that can write legacy JSON, and unsupported/unverified network filesystems and non-Windows SQLite. Explicitly selecting a stale legacy source after SQLite writes remains an operator risk. This is conditional task closure, not full security-risk elimination or V0.9.1 production release approval. SQL Server Memory Grant and PostgreSQL WAL deferred gates are unchanged. No commit or push occurred.

## LAB-CREDENTIAL-OUTPUT-001 conditional closure — 2026-10-31

The user formally approved conditional closure; Backlog status is `DONE` and the detailed acceptance record is in `docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md` §35. No product code or tests changed in this closeout, and the previously recorded evidence was not rerun: 39 targeted project tests passed, 14 external lab-consumer Fake Driver tests passed, and Windows-local full pytest reported 1203 passed / 2 skipped / 0 failed. Ruff, strict MyPy (75 `dba` sources), `dba validate` (4 instances / 122 SQL scripts), and `git diff --check` passed in the recorded validation. The 15 pre-existing E701 findings in external lab scripts remain unchanged.

The four database password paths were exercised with synthetic configuration and Fake Drivers only; this is offline evidence, not real database E2E. The lab scripts have no JSON/HTML report-generation path (`NOT_APPLICABLE`). The real `local_config.py` was not read, executed, copied, modified, or scanned. Secret Scanner passed for Git Index content; the supplemental worktree scan covered exactly 10 named task files and found no findings, not the entire worktree. No database, Docker, Credential Store, or Inventory was accessed. Roadmap order is unchanged; `INVENTORY-CONNECTION-001` is next in Backlog order but has not started and requires separate approval. No commit/push or production-release approval is implied.

## INVENTORY-CONNECTION-001 conditional closure — 2026-10-31

The user formally approved conditional closure; Backlog status is `DONE`. This scope covers the `version_family` error-output fix and approved lightweight Inventory policies: directory scans skip symbolic-link YAML and prune symbolic-link directories, explicitly selected single-file loading is unchanged, disabled assets remain visible through `list`/`show` and are blocked before credential resolution for connection-oriented CLI operations, and existing sensitive-field rejection has regression coverage. Broader connection-precedence and local asset-preservation review is not claimed complete. Roadmap order is unchanged.

Recorded verification from the implementation turn (not rerun for this documentation update): Inventory/connection targeted pytest **99 passed, 1 skipped**; Windows full pytest **1231 passed, 3 skipped, 0 failed**; Ruff and strict MyPy for the two changed source files passed; `dba validate` passed against a temporary synthetic Inventory (1 asset, 122 SQL scripts); `git diff --check` passed. The official Secret Scanner passed for Git-index content; a supplemental worktree scan passed for the eight task source, test, and planning files only. Native Windows symlink creation E2E remains unverified because the process token lacked symlink creation privilege (`WinError 1314`); deterministic simulated-link traversal passed. Other full-suite skips were the opt-in real local system test (not enabled) and Windows-inapplicable POSIX permission-bit test.

No real Inventory, Credential Store, database, or Docker was accessed. No tests were rerun and no source or test file changed for this closeout. At this 2026-10-31 checkpoint, `PERMISSION-PREFLIGHT-001` remained `BACKLOG`; it was separately approved and started on 2026-11-01, as recorded below. No commit or push occurred.

## PERMISSION-PREFLIGHT-001 implementation and offline validation — 2026-11-01

The approved simplification is implemented: database grants are deployment preparation in the single `docs/DATABASE_PERMISSIONS.md` guide, linked from README. Four-engine grant statements and version differences are sourced from current reviewed SQL/profile paths. The runtime Permission Registry, per-feature permission readiness/missing-grant calculation, GRANT SQL generation, and `permissions` CLI command are absent. Runtime still performs connection/version and account-safety checks, enforces reviewed read-only SQL, and preserves sanitized vendor access-denial details with a guide pointer. It does not auto-grant or revoke.

Offline evidence: affected focused suites **422 passed**; final full pytest **1154 passed, 3 skipped, 0 failed**. Ruff passed for `dba` and `tests`; strict MyPy passed for 75 source files; `dba validate` passed against `examples/inventory` (11 assets, 118 SQL scripts); `git diff --check` passed; the official Secret Scanner passed on Git-index content. The scanner is index-only and does not establish a full worktree audit. Skips: POSIX permission bits unavailable on Windows, native Windows symlink creation unavailable to this token, and opt-in local system tests not enabled. No real DB test was enabled. An initial run hit a Windows access denial in the sandbox-managed Temp directory; the final run used `.venv/test-tmp` and completed successfully.

No real database, Docker, Credential Store, Inventory, or production asset was accessed, and no grant was executed. Grant syntax has source-backed static review but was not live-tested across all vendor versions; real access-denial cases and version-specific permission behavior remain unverified. At this implementation checkpoint Backlog was `IN_PROGRESS`; the formal conditional closure is recorded below. Roadmap order is unchanged and no subsequent task has started. No commit or push occurred.

## PERMISSION-PREFLIGHT-001 formal conditional closure — 2026-11-01

The user approved conditional closure; the canonical Backlog status is now `DONE`. This documentation-only closeout relies on the implementation evidence recorded above and in Acceptance §39; tests and scanners were not rerun. Evidence remains: focused suites **422 passed**; full pytest **1154 passed, 3 skipped**; Ruff, strict MyPy, `dba validate`, and Git-index Secret Scanner passed. Authorization SQL was reviewed statically against source and version routing only; it has not been executed on every supported database version. No database grants were applied, and no database, Docker, Credential Store, Inventory, or credentials were accessed.

`SQL-REGISTRY-SAFETY-001` remains `BACKLOG` and has not started. Roadmap task order is unchanged; separate user approval is required before that work begins. No product source or tests changed, and no commit or push occurred.
## SQL-REGISTRY-SAFETY-001 implementation checkpoint — 2026-11-01

The user authorized implementation. Backlog remains `IN_PROGRESS`; this is not task closure. Reviewed SQL source and metadata are bounded and revalidated at execution by digest and parsed-contract equality; static checking rejects multi-statement scripts; execution limits now include timeout, row, and decoded result-payload byte ceilings; issued Registry capabilities are weakly held; and all four database adapters consume rows through the shared bounded fetch helper while preserving cursor cleanup. This improves reviewed SQL provenance and bounds without adding a general SQL execution API, grants, or database writes.

Offline evidence: targeted Registry and four-engine regression suites **371 passed**; Ruff passed on six source and six related test files; strict MyPy passed on six source modules; `dba validate` passed for the selected Inventory (4 assets, 118 SQL scripts). `git diff --check` was run after the documentation update. No full pytest, Secret Scanner, database, or Docker run is claimed. The byte ceiling applies to returned decoded cell payload after driver fetch; a driver may internally materialize an oversized individual cell first. No SQL/permission document changes were needed because no SQL was added. Roadmap order is unchanged, and no following task has started.

### Final acceptance verification — 2026-11-01

The requested Windows full pytest initially reported **1159 passed, 3 skipped, 5 failed**: one test could not write under the sandbox-managed Temp directory, and four tests asserted the old bulk `fetchmany(101/301)` size. The four stale call-shape assertions were updated to verify one-row pulls while retaining their output, completeness/truncation, ranking and identity checks. With `TEMP`/`TMP` set to `.venv/test-tmp`, the full suite passed: **1164 passed, 3 skipped, 0 failed**. Skips were the opt-in real local system test, POSIX permission-bit validation on Windows, and Windows symlink creation unavailable to the process token.

Ruff passed for the six changed source modules and nine related test files; strict MyPy passed for six source modules; `dba validate` passed (4 configured assets, 118 SQL scripts); `git diff --check` passed. These are Windows offline checks using the project `.venv`; no database or Docker test was run. The Registry and four adapters preserve reviewed read-only SQL execution and cursor cleanup with timeout, row and decoded result-payload limits. The byte limit is checked after the driver returns a row, so an individual oversized value may first be materialized internally. No business-data modification path or source/metadata mismatch was found. Backlog remains `IN_PROGRESS`, pending user acceptance; no next task started and no commit/push occurred.

## Slow Diagnose HTML template integration — 2026-11-02

`V091-P5-WORKBENCH-001` is `IN_PROGRESS` per Backlog. Hallmark audited the approved source template and the generated self-contained output. The shared model/presenter/policy/renderer path now renders Slow Diagnose using the approved navy incident report layout, preserves native four-database identity fields and current-versus-recent SQL evidence, marks blocker rows red, verified clear rows green, and uncertain rows amber. Reference commands remain copyable display text; no command execution path was added. Dynamic report values use the existing redaction and HTML escaping path; the source template is not a runtime dependency.

Offline evidence: focused report tests **29 passed**; final full pytest **1175 passed, 3 skipped**; Ruff, strict MyPy (75 source files), and `dba validate` (4 assets, 118 SQL scripts) passed. Chrome review covered a generated synthetic page at 1280px and 500px viewport widths; WCAG AA contrast was checked for semantic token pairs. Exact 320–414px browser emulation and browser-driven interaction checks for search/copy/expand/print remain unverified; the responsive breakpoints and corresponding inline JavaScript were reviewed and template/renderer tests passed. The generated page uses fixture data and proves rendering only.

No current-HEAD live database E2E was completed. Credential Session was locked, and the execution context did not yield an authorized database connection result. This is recorded as `NOT_EXECUTED`, not a database failure. Prior four-database Slow E2E evidence is historical and does not validate the newly integrated renderer. No Collector, SQL Registry, database, Docker, Inventory, Credential Store or permissions were modified. No commit or push occurred.
