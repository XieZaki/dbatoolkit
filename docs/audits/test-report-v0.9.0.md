# DBA Toolkit v0.9.0 Test Report

**Test date:** 2026-10-17

**Version:** 0.9.0

**Source commit before test-strategy changes:** `f77d077`

**Environment:** Windows 11, Python 3.12.14, project `.venv`

**Local runtime:** ODBC Driver 17 fallback; PostgreSQL, MySQL, and Oracle Python drivers available; MongoDB and Redis drivers not installed. `dba doctor` returned `READY_WITH_WARNING`.

## Results by layer

| Layer | Result | Details |
|---|---|---|
| Unit | PASS | 499 passed, 1 skipped, 7 deselected (the 6 separately marked integration tests and 1 system test). |
| Integration | PASS | 6 passed, 501 deselected. All use fake drivers and temporary files; no database connections. |
| System | SKIPPED | 1 test skipped because system opt-in was not enabled. Active local Inventory entries are not labeled `local-test`/`test`/`docker`; no real database was contacted. |
| Regression | PASS | 21 passed, 486 deselected. Regression is a cross-cutting marker and overlaps Unit/Integration. |
| Full pytest | PASS_WITH_GAPS | 505 passed, 2 skipped in 64.08 seconds. |

The other full-suite skip is the POSIX file-permission behavior test, which is not applicable on Windows. The suite contains 507 collected test cases after parameter expansion.

## Regression scenarios exercised

- MySQL global `SELECT` scope remains non-privileged while independent feature permissions remain independent.
- MySQL `SSCursor` is closed before its connection on truncation and permission-query failure.
- Vendor database error evidence and secret redaction remain intact.
- Replication handles multiple channels, NULL lag, no configured replica, old SQL Server capability limits, and Oracle vendor errors without false health claims.
- SQL Server Driver 17 honors explicit TLS configuration; inherited public permissions do not become privileged-account findings.
- Encrypted credential corruption, wrong password, tampering, and unsupported store states fail closed.
- Inventory duplicate IDs include both source files; credential CLI never prints passwords.

## Quality and security gates

| Check | Result |
|---|---|
| Ruff | PASS (`ruff check .`) |
| strict mypy | PASS (`mypy dba`, 56 source files) |
| `dba validate` | PASS (4 configured entries, 83 SQL scripts) |
| `dba doctor` | PASS_WITH_WARNING (ODBC Driver 17 selected; MongoDB/Redis drivers absent; Windows Credential Manager unavailable, encrypted local store available) |
| Secret Scanner | PASS (`check_repository_secrets.py`: no tracked artifacts or likely literal secrets found). |
| Mutation safety | PASS for this run: unit/integration tests used fake adapters; system tests did not run; no database mutation or permission change was attempted. |
| Credential leakage | PASS in regression and full suite. Test artifacts contain only command metadata, not output or credential material. |

## Known gaps and risks

- No real SQL Server, PostgreSQL, MySQL, or Oracle System E2E ran in this report. Existing configured assets were deliberately not used because they lack an explicit local-test label.
- The report's System runner accepts one or more explicitly selected IDs, requires each asset to be enabled and marked local-test/test/docker, closes stdin, and only invokes read-only Toolkit commands. A real run still requires pre-provisioned secure credentials and a disposable local database.
- No live compatibility run was made against SQL Server 2008 R2/2012, PostgreSQL 13, MySQL 8.0, or Oracle 12c. Version behavior is covered with fake capability responses.
- Linux path, keyring, permission, and IPC behavior is partially mocked from Windows and needs a real Linux run. The Windows-only skip was the POSIX permission-bit test.
- Concurrency coverage is limited to deterministic credential-session reuse and report filename collision behavior; there is no concurrent database load test.
- The system suite currently treats a non-zero CLI exit as a test failure. Feature-level unsupported or missing-permission results need review before using it with a deliberately partial test account.

**Conclusion: PASS_WITH_GAPS.** Unit, fake-driver integration, regression, lint, type, and inventory validation gates pass. Real database acceptance remains a manual/opt-in step gated on an explicitly isolated local-test Inventory asset. No application defect was found in this test run.
