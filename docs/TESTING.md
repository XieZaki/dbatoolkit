# DBA Toolkit Test Strategy

This project keeps the existing flat `tests/test_*.py` suite to avoid risky file moves. Pytest markers provide logical layers; the regression marker is cross-cutting and may overlap unit or integration tests.

## Layers and commands

| Layer | Meaning | Test files / marker policy | Command |
|---|---|---|---|
| Unit | Deterministic component behavior with fakes, mocks, or temporary files; no database or network access. | Existing tests are marked `unit` by default unless explicitly marked `integration` or `system`; system safety policy tests are unit tests. | `python -m pytest -m unit` |
| Integration | Multiple Toolkit components composed using fake drivers and local files. These tests do not require a database. | Explicitly marked CLI/pipeline tests in `test_mysql.py`, `test_postgresql.py`, `test_oracle.py`, `test_sqlserver.py`, and `test_database_errors.py`. | `python -m pytest -m integration` |
| System | The installed CLI connects to explicitly selected local test instances. | `tests/test_system_cli.py`; requires both opt-in variables and Inventory safety labels. | `$env:DBA_TOOLKIT_SYSTEM_TESTS='1'; $env:DBA_TOOLKIT_SYSTEM_DATABASES='local-id'; python -m pytest -m system` |
| Regression | Preserves previously fixed bugs and security boundaries. It is a cross-cutting marker and overlaps unit/integration. | Explicit markers on MySQL privilege/scope and SSCursor cases, replication state/capability cases, vendor error transparency, SQL Server TLS fallback/public inherited permission, and encrypted credential fail-closed tests. | `python -m pytest -m regression` |

All test files must use only declared markers (`--strict-markers` is enabled). Database markers (`mysql`, `postgresql`, `sqlserver`, `oracle`) select vendor-focused cases, for example `python -m pytest -m 'mysql and regression'`.

## Coverage matrix

| Area | Unit | Integration | System | Regression | Main coverage / current boundary |
|---|---|---|---|---|---|
| Inventory | Yes | Partial | Yes | Yes | `test_inventory_scale.py`, `test_toolkit.py`; CLI validation uses the active local Inventory but system DB commands require a safe label. |
| Version profiles | Yes | Yes | Optional | Yes | `test_versioning.py`; normalization, strict YAML values, detection through reviewed Registry scripts, status and atomic resolver bindings. No live version matrix is claimed by unit tests. |
| Credential providers, session, JIT | Yes | Partial | Partial | Yes | `test_connection_credentials.py`, `test_credential_session.py`, `test_jit_credentials.py`; system tests use existing OS/secure-store credentials and never pass passwords through environment variables. |
| Encrypted credential store | Yes | Partial | Partial | Yes | `test_encrypted_credentials.py`; corruption, tamper, wrong password, rekey, atomic update, and fail-closed paths. |
| Runtime and deployment | Yes | Partial | Yes | Partial | `test_runtime.py`; platform behavior is simulated on the current host, not claimed as real Linux validation. |
| Permissions and account safety | Yes | Yes | Yes | Yes | `test_permissions.py`, `test_mysql_privileges.py`, vendor adapter tests; GRANT output remains text-only. |
| SQL Registry and adapter lifecycle | Yes | Yes | Yes | Yes | Per-database adapter tests include reviewed SQL, timeouts, bounded fetch and cleanup; MySQL streaming cursor lifecycle is a named regression. |
| Slow diagnosis | Yes | Yes | Yes | Partial | `test_slow.py`, per-database fake CLI/diagnose coverage; no database is contacted by default. |
| Disk growth | Yes | Yes | Yes | Partial | `test_disk_growth.py` and database adapter tests. |
| Replication | Yes | Yes | Yes | Yes | `test_replication.py` and MySQL CLI tests cover configured/unconfigured state, version/capability behavior, lag uncertainty, and bounded reports. |
| Execution plan | Yes | Partial | SQL Server only | Partial | `test_execution_plan.py` covers version/profile permission routing, mismatch fail-closed, SQL applicability metadata, bounded parser and ambiguity; live plan collection requires a safe SQL Server asset and suitable session. Only SQL Server 2022 has recorded real plan E2E; older families remain unverified. |
| Database errors and redaction | Yes | Yes | Yes | Yes | `test_database_errors.py` and adapter tests; vendor evidence is retained in sanitized form. |
| Human, JSON, HTML reports | Yes | Yes | Yes | Yes | `test_presentation.py`, `test_html_reports.py`, `test_incident.py`; static and generated reports are checked for escaping and bounds. |
| Secret scanner / repository safety | Yes | Partial | N/A | Yes | `test_repository_safety.py` and `scripts/check_repository_secrets.py`; scanner evaluates tracked files only. |

`Yes` means executable automated coverage exists at that layer, not exhaustive coverage of every server version or configuration. Existing `tests/integration/run_*` scripts are explicit Docker verification tools and are not collected by ordinary pytest. They may create disposable test objects and must only be run against their isolated test containers.

## System test safety

System tests skip unless `DBA_TOOLKIT_SYSTEM_TESTS=1`. They also require a non-empty, explicit comma-separated `DBA_TOOLKIT_SYSTEM_DATABASES` selection. Every selected instance must be enabled and have `group: local-test` or a `test`, `docker`, or `local-test` tag in Inventory; otherwise the run is skipped before launching the CLI. Never label a production asset for convenience. No Inventory-wide connection scan or target guessing is performed.

The system suite invokes only the CLI's read-only commands: version/list/show/validate/doctor/runtime-info, check/test-connection, permissions, collectors implemented for the selected database type, and slow/disk-growth/replication diagnoses. It uses the installed Credential Provider, closes stdin to prevent a hidden prompt, applies a command timeout, rejects traceback-style failures, and writes only command names, exit codes, duration, and output byte counts under ignored `.tmp/system-test/`. It does not save command output, reports, credentials, or database rows there. A diagnosis may return a non-zero status when it produces a valid HTML report and explicitly marks data completeness PARTIAL or INCOMPLETE; that is recorded as a diagnostic gap, not promoted to a healthy result. A structured Oracle `ORA-01045` / missing `CREATE SESSION` response is recorded as an expected permission blocker, and later commands for that unreachable instance are skipped. Other non-zero command exits fail the system test.

Run one approved test asset at a time. Example (PowerShell):

```powershell
$env:DBA_TOOLKIT_SYSTEM_TESTS = '1'
$env:DBA_TOOLKIT_SYSTEM_DATABASES = 'mysql84'
python -m pytest -m system -v
Remove-Item Env:DBA_TOOLKIT_SYSTEM_TESTS
Remove-Item Env:DBA_TOOLKIT_SYSTEM_DATABASES
```

On Linux, set and unset the same variables with the shell's `export`/`unset`. Credentials must already be available through the OS store, encrypted store, or an active secure credential session. Do not pass database passwords in environment variables, CLI arguments, or test fixtures. If selected Inventory is not explicitly local-test, the system run skips; do not infer safety from an address such as localhost.

## Standard quality commands

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest -m unit
.\.venv\Scripts\python.exe -m pytest -m integration
.\.venv\Scripts\python.exe -m pytest -m regression
.\.venv\Scripts\python.exe -m pytest -m system
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe dba
.\.venv\Scripts\dba.exe validate
.\.venv\Scripts\dba.exe doctor
.\.venv\Scripts\python.exe .\scripts\check_repository_secrets.py
```

System tests are opt-in and skip in the standard command unless both safety gates are satisfied. No coverage plugin is required. The strategy uses scenario matrices and boundary tests rather than adding a dependency to pursue a line-coverage percentage.

## Compatibility, failure injection, and gaps

Version capability behavior is exercised with faked server versions: SQL Server old-version unsupported behavior, PostgreSQL capability paths, and MySQL/Oracle version-sensitive results. This does not establish real compatibility for every listed server release. Unit and integration suites inject permission failures, vendor errors, timeouts, empty/NULL results, row truncation, cleanup failures, malformed responses, and incomplete evidence where covered by the relevant adapter tests.

## V0.9.1 P0 emergency diagnosis

The standard unit suite verifies the synchronous emergency/supplemental
collector split, result reuse, stable five-candidate ordering, required versus
optional evidence states, truncated/incomplete summaries, Chinese rendering,
and JSON-only output. Live E2E uses only explicitly selected local Docker
assets and creates no load or database objects. A multi-query capture is
BEST_EFFORT, never an atomic snapshot. See `docs/EMERGENCY_DIAGNOSIS.md` for
the operator workflow and safety boundaries.

Concurrency and performance coverage is limited to deterministic session reuse / report collision cases and bounded synthetic result sizes; it is not a load benchmark. Windows is the primary host. Linux path, permissions, and IPC behavior are partially mocked on Windows and require a real Linux deployment check. No live SQL Server 2008 R2, PostgreSQL 13, MySQL 8.0, or Oracle compatibility matrix is claimed. System coverage only describes instances selected and run during a particular test report.

V0.9.1 Phase B adds profile-routing regression and Fake Adapter integration
coverage for MySQL 5.7 and the 8.0.21/8.0.22 boundary, PostgreSQL 13–18,
SQL Server 2008 R2 unsupported and 2022 permission routing, and Oracle 11.2,
12.1, 12.2+ Data Guard SQL/grant/parser selection. These are not live version
compatibility claims. Oracle 11gR2 is explicitly NOT E2E VERIFIED. Replication
tests assert a PostgreSQL standalone primary is healthy/not configured when
all required evidence is available, and that MySQL zero-channel results remain
complete. No feature SQL executes for unsupported, auto-detected-only,
unverified, unknown, or mismatched routes.

V0.9.1 Phase C adds slow profile coverage for SQL Server 2008 R2 through 2022
(2025 remains UNKNOWN), MySQL 5.7/8.0/8.4, PostgreSQL 13–18, and Oracle 11gR2,
12c variants and 18c–26ai. Tests verify SQL/permission/parser alignment,
MySQL 5.7 legacy lock views, normalized evidence aliases, required versus
optional collector failures and fail-closed version gating. These tests use
Fake Adapters and do not replace live E2E verification.

Plan CLI returns success when the reviewed probe completed but no current or
cached plan exists; `plan_status=PLAN_NOT_AVAILABLE` and `plan_available=false`
describe that ordinary evidence state. Query, permission, version and parser
failures remain failures.

The initial Oracle 26ai system run recorded four missing required disk-growth
object grants before those core grants were provided. That result is historical,
not a statement of current readiness. Re-run `dba check oraclefree`,
`dba diagnose oraclefree disk-growth`, and
`dba permissions oraclefree --feature disk-growth` to assess current live
permissions; the system runner never changes database permissions.

V0.9.1 Phase D adds version-bound disk-growth SQL, permission and parser
contracts for SQL Server 2008 R2–2022, MySQL 5.7/8.0/8.4, PostgreSQL 13–18,
and Oracle 11gR2–26ai. On 2026-10-18 the opt-in system test passed against the
four local Docker assets. Disk-growth collection completed with FULL visibility
for `sqlserver2022` (SQL Server 16.0.4305.5), `pg16` (PostgreSQL 16.15), and
`mysql84` (MySQL 8.4.11). Oracle 26ai connection, principal, version and
permission checks passed; collection was correctly blocked before feature SQL
because four required core object grants are absent. The system policy accepts
only that exact known gap as an expected permission blocker. No old-version
live compatibility is implied by Fake Adapter coverage, and the suite does not
create disk pressure or modify database state.
