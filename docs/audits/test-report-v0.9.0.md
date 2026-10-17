# DBA Toolkit v0.9.0 Test Report

**Test date:** 2026-10-17

**Environment:** Windows 11, Python 3.12.14, project `.venv`

**System targets:** only enabled loopback Inventory assets tagged `test`/`docker` or grouped `local-test`

**Final result:** `PASS_WITH_GAPS` — SQL Server, PostgreSQL, and MySQL completed real read-only CLI runs. Oracle's local container endpoint was reached, but its monitor account lacks `CREATE SESSION`, so Oracle collectors and diagnoses could not run.

## Safe local targets

| Database ID | Container / image | Host port | Database version | Inventory test marker | Result |
|---|---|---:|---|---|---|
| `sqlserver2022` | `sqlserver2022` / SQL Server 2022 Developer | 1433 | `16.0.4305.5` | tags `docker`, `test` (group remains `manufacturing`) | Real CLI E2E passed |
| `pg16` | `pg16` / `postgres:16` | 5432 | PostgreSQL `16.15` | group `local-test`, tags `docker`, `test` | Real CLI E2E passed with replication warning |
| `mysql84` | `mysql84` / `mysql:8.4` | 3306 | `8.4.11` | group `local-test`, tags `docker`, `test` | Real CLI E2E passed with slow-diagnosis evidence gap |
| `oraclefree` | `oraclefree` / Oracle Database Free | 1521 | Database version unavailable; listener reports `23.26.3.0.0` | group `local-test`, tags `docker`, `test` | Blocked: ORA-01045, missing `CREATE SESSION` |

All targets use `127.0.0.1`; Docker confirmed all four containers running (Oracle healthy). Oracle listener metadata confirmed service `freepdb1`. Local Inventory files remain ignored by Git. No production or remote target was selected.

## Real CLI outcomes

### SQL Server

- `check`, `test-connection`, all five implemented collectors, and all three requested feature permission checks returned success. Runtime selected ODBC Driver 17 fallback; `check` reported `READY_WITH_WARNING` and Account Security `UNKNOWN`, while slow, disk-growth, and replication permission readiness were all `READY`.
- `diagnose slow`: `HEALTHY`, complete snapshot, exit 0.
- `diagnose disk-growth`: `HEALTHY`, complete snapshot, exit 0.
- `diagnose replication`: `NOT_CONFIGURED`, healthy, complete snapshot, exit 0.
- Replication JSON was valid and identified SQL Server `16.0.4305.5`; HTML reports were generated.

### PostgreSQL

- `check`, `test-connection`, all implemented collectors, and slow, disk-growth, and replication permission checks returned success. Account Security was `NON_PRIVILEGED`; all three feature permission states were `READY`.
- `diagnose slow` and `diagnose disk-growth`: `HEALTHY`, complete snapshots, exit 0.
- `diagnose replication`: role `PRIMARY`, complete evidence, status `DEGRADED`, exit 0. No standby, receiver, or slot was observed, but `wal_level=replica` and WAL sender capacity are enabled, so the Toolkit correctly does not claim that replication is unconfigured. The snapshot reports `NO_CONNECTED_STANDBY` as a warning.
- Replication JSON was valid and identified PostgreSQL `16.15`. PostgreSQL has no registered `active-requests` Collector; the System runner calls only the collectors implemented for this database type.

### MySQL

- `check`, `test-connection`, all five implemented collectors, and slow, disk-growth, and replication permission checks returned success. Account Security was `NON_PRIVILEGED`; all three feature permission states were `READY`.
- `diagnose slow`: `DEGRADED`/`PARTIAL`, exit 1, because current wait detail is unavailable. Active sessions, requests, transactions, and blocking collectors themselves succeeded; no `SSCursor.close`, `MySQLResult.__del__`, or `Exception ignored` error occurred.
- `diagnose disk-growth`: `HEALTHY`, complete snapshot, exit 0.
- `diagnose replication`: `SOURCE_OR_STANDALONE` / `NOT_CONFIGURED`, healthy, partial evidence, exit 0.
- Replication JSON was valid and identified MySQL `8.4.11`. The System test recorded the explicit incomplete slow report as a gap rather than treating it as a healthy finding.

### Oracle

- Container, loopback endpoint, Thin driver, existing credential alias, and listener service `freepdb1` were confirmed. The alias was readable; no credential was created, changed, or deleted.
- `check`, `test-connection`, `permissions`, and `collect` returned `ORA-01045`: user `DBA_MONITOR` lacks `CREATE SESSION`. Feature permission is therefore `UNKNOWN`; no database query can proceed. No GRANT was generated or executed. Oracle reports and database version could not be produced.
- The System runner recorded the structured blocker and skipped dependent Oracle commands. This is an account permission gap, not a driver or network failure.

## Reports, JSON, and safety

- Nine latest HTML reports (slow, disk-growth, replication for each reachable database) exist, are non-empty UTF-8, contain no external HTTP(S) resources, and passed the password/secret-pattern check. Reports remain under ignored `reports/`.
- Replication JSON for each reachable database parsed successfully and included matching database ID, platform, status, and permission readiness. The same secret-pattern check found no credential material.
- System artifacts under ignored `.tmp/system-test/` contain command metadata only; logs/reports and local Inventory are not tracked.
- No database write, DDL/DML, GRANT, REVOKE, KILL, failover, or configuration change was issued. No credential was altered. No traceback or password was observed in CLI output.

## Test layers and quality gates

The opt-in System run selected `sqlserver2022,pg16,mysql84,oraclefree`; it recorded one MySQL incomplete diagnosis and the Oracle missing-privilege blocker without hiding either outcome.

| Layer / gate | Result |
|---|---|
| Unit | PASS — 502 passed, 1 skipped, 7 deselected |
| Integration | PASS — 6 passed, 504 deselected |
| Regression | PASS — 21 passed, 489 deselected |
| System | PASS — 1 passed, 509 deselected; Oracle dependent commands marked skipped after ORA-01045 |
| Full pytest | PASS — 509 passed, 1 skipped |
| Ruff | PASS (`ruff check .`) |
| strict mypy | PASS (`mypy dba`, 56 source files) |
| `dba validate` | PASS (5 configured entries, 83 SQL scripts) |
| `dba doctor` | `READY_WITH_WARNING` — ODBC Driver 17 selected; MongoDB/Redis drivers absent |
| Secret scanner | PASS — no tracked artifacts or likely literal secrets found |

The one skip is the Windows-inapplicable POSIX permission test. The seven deselected Unit tests are the six separately marked Integration tests and the System test.

## Findings and changes

1. PostgreSQL `replication_node` and `replication_archiver` each returned one legitimate row while `max_rows: 1` caused the bounded executor to mark the result conservatively truncated. Their limits are now 2, and a regression assertion prevents the singleton snapshot queries from reverting to 1. The real replication diagnosis then had complete data.
2. The System harness tried the unimplemented PostgreSQL `active-requests` Collector for every database. It now selects registered collectors by database type, includes `dba check`, records valid PARTIAL/INCOMPLETE diagnoses as gaps, and records ORA-01045 as an expected permission blocker while skipping only commands for that unreachable target. Tests cover those rules.
3. The local Oracle Inventory entry had service name `test`; the running listener exposed `freepdb1`, so the ignored local entry was corrected and marked `local-test`/`docker`/`test`. This changed only local Inventory, not the database.

No SQL Server 2008 R2/2012 or alternate PostgreSQL/MySQL/Oracle versions were tested. Oracle requires a DBA to provision `CREATE SESSION` for the existing dedicated monitor account before its read-only E2E can proceed. MySQL slow diagnosis still lacks current wait detail in this environment. PostgreSQL reports a no-connected-standby warning rather than asserting `NOT_CONFIGURED` while WAL sender capacity is enabled.

**Conclusion: PASS_WITH_GAPS.** Three databases completed real read-only replication E2E. Oracle's explicit least-privilege blocker remains; no Toolkit safety boundary was bypassed.
