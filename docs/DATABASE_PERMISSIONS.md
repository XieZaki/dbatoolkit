# Database Permissions

Before first use, ask a database administrator to apply the block for the target engine and version to the dedicated monitoring account. The examples use `dba_monitor`; replace that account name with the actual monitoring principal. Review target scope and local policy before execution. DBA Toolkit does not inspect feature grants, generate grant scripts, or execute `GRANT`/`REVOKE`; a denied read remains a database query error. Review this document when that happens.

These grants are based on the current reviewed read-only SQL Registry and implemented diagnostics. They do not make every optional source available, and they are not proof that a server has enabled a feature or retained data. Do not grant `sysadmin`, `CONTROL SERVER`, `SUPER`, `SUPERUSER`, `SYSDBA`, `DBA`, `SELECT ANY DICTIONARY`, `ALL PRIVILEGES`, or a write role to the monitoring account.

## SQL Server

Run the block matching the server major version. It covers current slow/active request, cached plan, Top SQL (`sys.dm_exec_query_stats`), Memory Grant (`sys.dm_exec_query_memory_grants`), disk-growth, and supported Availability Group replication reads. SQL Server 2008 R2 has no Availability Groups; the replication scenario is supported from SQL Server 2012. Do not combine both version blocks unless the account intentionally monitors different server versions.

The instance/database summary also reads catalog visibility. Apply this shared grant once:

```sql
GRANT VIEW ANY DATABASE TO [dba_monitor];
```

SQL Server 2008 R2 through 2019:

```sql
GRANT VIEW SERVER STATE TO [dba_monitor];
```

SQL Server 2022 and later:

```sql
GRANT VIEW SERVER PERFORMANCE STATE TO [dba_monitor];
```

Slow diagnosis also reads `sys.dm_tran_locks` for lock resources that have a waiting or converting request. It is covered by the server-state permission above; no additional grant is required. Table, index and partition names come from catalog metadata (`sys.partitions`, `sys.allocation_units`, `sys.objects`, `sys.indexes`, `OBJECT_NAME`/`OBJECT_SCHEMA_NAME`), and SQL Server metadata visibility applies. When the monitoring account cannot see an object, the report shows the lock object as unresolvable and keeps the raw lock resource. HoBT and allocation-unit resources are resolved only for the database named in the connection. DBA Toolkit does not switch databases. Do not grant `VIEW ANY DEFINITION`, `db_owner` or other broad permissions only to obtain object names.

Optional disk-growth backup-history evidence can be enabled by granting read access to `msdb.dbo.backupset`. The principal must already be mapped to a database user in `msdb`:

```sql
USE [msdb];
GRANT SELECT ON OBJECT::[dbo].[backupset] TO [dba_monitor];
```

This optional grant is not needed for core disk/database/log-space evidence. SQL Server 2025 routing is not currently declared supported; do not infer support from the version-specific grant syntax.

## Oracle

Use a dedicated non-`SYS`/non-`SYSTEM` account and direct object grants. The common block covers session identity, slow/request evidence, Top SQL, and the Oracle cached-plan MVP:

```sql
GRANT CREATE SESSION TO dba_monitor;
GRANT SELECT ON SYS.V_$INSTANCE TO dba_monitor;
GRANT SELECT ON SYS.V_$VERSION TO dba_monitor;
GRANT SELECT ON SYS.V_$DATABASE TO dba_monitor;
GRANT SELECT ON SYS.V_$SESSION TO dba_monitor;
GRANT SELECT ON SYS.V_$SQL TO dba_monitor;
GRANT SELECT ON SYS.V_$TRANSACTION TO dba_monitor;
GRANT SELECT ON SYS.V_$SQL_PLAN TO dba_monitor;
```

Disk-growth requires these dictionary views:

```sql
GRANT SELECT ON SYS.DBA_TABLESPACE_USAGE_METRICS TO dba_monitor;
GRANT SELECT ON SYS.DBA_TABLESPACES TO dba_monitor;
GRANT SELECT ON SYS.DBA_DATA_FILES TO dba_monitor;
GRANT SELECT ON SYS.DBA_TEMP_FREE_SPACE TO dba_monitor;
```

Optional slow/blocking evidence:

```sql
GRANT SELECT ON SYS.V_$LOCK TO dba_monitor;
GRANT SELECT ON SYS.V_$LOCKED_OBJECT TO dba_monitor;
```

Optional FRA/archive/redo evidence:

```sql
GRANT SELECT ON SYS.V_$RECOVERY_FILE_DEST TO dba_monitor;
GRANT SELECT ON SYS.V_$RECOVERY_AREA_USAGE TO dba_monitor;
GRANT SELECT ON SYS.V_$ARCHIVE_DEST_STATUS TO dba_monitor;
GRANT SELECT ON SYS.V_$ARCHIVED_LOG TO dba_monitor;
GRANT SELECT ON SYS.V_$LOG_HISTORY TO dba_monitor;
```

For Data Guard replication evidence, grant the following shared statistics view once, plus the version-appropriate process view. The optional `V_$ARCHIVE_DEST_STATUS` and common `V_$DATABASE` grants above are reused where the selected diagnostic requires them.

```sql
GRANT SELECT ON SYS.V_$DATAGUARD_STATS TO dba_monitor;
```

Oracle 11gR2 through 12.1:

```sql
GRANT SELECT ON SYS.V_$MANAGED_STANDBY TO dba_monitor;
```

Oracle 12.2 and later:

```sql
GRANT SELECT ON SYS.V_$DATAGUARD_PROCESS TO dba_monitor;
```

Do not substitute `SELECT_CATALOG_ROLE`, `DBA`, `SELECT ANY DICTIONARY`, or `SYSDBA` for these direct grants. Oracle 26ai and versions other than those exercised by the project still require target-version DBA review.

## MySQL

The account host component must match the exact Toolkit client host permitted by local policy. Replace `<toolkit_client_host>` with that approved host pattern; avoid `%` unless the DBA has explicitly approved that scope. The account name example is `dba_monitor`.

```sql
GRANT PROCESS ON *.* TO 'dba_monitor'@'<toolkit_client_host>';
GRANT SELECT ON performance_schema.* TO 'dba_monitor'@'<toolkit_client_host>';
GRANT REPLICATION CLIENT ON *.* TO 'dba_monitor'@'<toolkit_client_host>';
```

`PROCESS` supports cross-account process/slow/blocking visibility; `SELECT` on Performance Schema supports statement, wait, digest, lock, and related read paths; `REPLICATION CLIENT` supports replication status and binary-log inventory. If only a subset of features is used, the DBA may omit grants for the unused feature after reviewing its Registry SQL. `REPLICATION CLIENT` is not needed for non-replication diagnostics. No grant enables Performance Schema instruments or consumers; server configuration and runtime state are separate.

For MySQL 5.7 and 8.x, these privilege names are shared. The Registry routes the replica status statement by version (legacy `SHOW SLAVE STATUS` versus `SHOW REPLICA STATUS` from MySQL 8.0.22). Do not grant `SUPER`, `SYSTEM_USER`, `FILE`, `RELOAD`, `BINLOG_ADMIN`, or `ALL PRIVILEGES` for monitoring.

### MySQL diagnostic SQL timeout and `KILL QUERY` self-cancel (C-13)

MYSQL-005 implements the SELECT limit. Automated SHOW cancellation is withheld because MySQL provides no atomic check of both the connection ID and the identity of the thread being cancelled. Real database verification remains `NOT_EXECUTED` under `SQL-TIMEOUT-CANCEL-VERIFY-009`.

**Purpose.** PyMySQL's socket timeout can leave the server executing a query.

- Reviewed read-only SELECTs use the fixed adapter statement `SET SESSION max_execution_time = %s` on recognized MySQL 5.7.8+, 8.0 and 8.4. The positive integer is derived from the remaining client Deadline. Setting this specific session variable requires no extra grant. Older 5.7 releases and unrecognized products do not receive SET. MariaDB is outside the adapter's reviewed scope.
- The four SHOW scripts (`disk_binlogs`, `monitor_grants`, `replication_status`, `replication_status_legacy`) use the client Deadline. A timeout closes the target connection with server stop unconfirmed. MySQL resolves a KILL target by numeric ID when it processes the command. The ID may be reused after disconnect, including between a separate identity check and the KILL. Binding a prior CONNECTION_ID to the local connection object cannot close that race.

**Scope limits.**

- Only the `CONNECTION_ID()` that Toolkit read from its own target connection before running the diagnostic SQL in the same run, never an ID found by listing processes or matching user, host or SQL text.
- Only `KILL QUERY`. `KILL CONNECTION` and bare `KILL` are prohibited.
- If the target identity cannot be confirmed, no cancel is sent. If cancel fails or cannot be confirmed, the target connection is closed and the real status is reported.
- Business sessions, manual sessions and other Toolkit runs are never targeted. This is not a general KILL capability; the plain-text Slow `KILL` reference command remains a manual DBA reference that Toolkit never executes.

**Required privilege.** No additional grant for the implemented SESSION variable. For any future lab self-cancel test, MySQL 5.7 permits users without SUPER to kill their own threads. MySQL 8.0 and 8.4 permit this without CONNECTION_ADMIN or SUPER. This permission can also cover other sessions using the same account, so it does not establish this Toolkit run's ownership. Actual behavior remains to be verified per version.

**Grant SQL.** None. Do not grant `CONNECTION_ADMIN`, `SUPER` or `SYSTEM_USER` to enable cancellation. Toolkit's MySQL account safety check already rejects accounts holding them.

**Verification SQL** (run by a DBA, or by the lab test harness with the monitoring account; never against business sessions):

```sql
-- Identity of the monitoring session; the ID is what Toolkit records before a diagnostic query.
SELECT CURRENT_USER(), CONNECTION_ID();
-- Must not list CONNECTION_ADMIN, SUPER, SYSTEM_USER or ALL PRIVILEGES.
SHOW GRANTS FOR CURRENT_USER();
-- 5.7.8+ / 8.x: session variable exists and is settable without a grant.
SELECT @@SESSION.max_execution_time;
-- Disposable lab session only. Verify the positive value and then disconnect.
SET SESSION max_execution_time = 250;
SELECT @@SESSION.max_execution_time;
```

In the lab only, with two monitoring-account connections A and B: start a bounded test query on A, run `KILL QUERY <A's CONNECTION_ID()>` on B, and expect A to fail with error 1317 (query interrupted) while A's connection stays open. Running `KILL QUERY` on B against a thread of a different account is expected to fail with error 1095 (not owner of thread); use only a lab session created for this test.

**Revoke SQL.** Nothing needs revoking for this capability. If a management privilege was ever granted to the monitoring account, remove it:

```sql
REVOKE CONNECTION_ADMIN ON *.* FROM 'dba_monitor'@'<toolkit_client_host>';  -- MySQL 8.x
REVOKE SUPER ON *.* FROM 'dba_monitor'@'<toolkit_client_host>';
```

## PostgreSQL

For current slow, disk-growth/WAL, and replication diagnostics, grant connection access in each target database and the built-in monitoring role:

```sql
GRANT CONNECT ON DATABASE <database_name> TO dba_monitor;
GRANT pg_monitor TO dba_monitor;
```

Run the `CONNECT` statement for each database the account must inspect. `pg_monitor` includes visibility roles used by the implemented cross-session/statistics and settings reads. It also includes `pg_stat_scan_tables`; review that built-in role's documented capabilities for your policy. PostgreSQL Top SQL additionally requires the `pg_stat_statements` extension to be installed and collecting; this is a database/configuration prerequisite, not a grant, and Toolkit does not install or enable it.

PostgreSQL 13 uses the historical term “default roles”; later documentation calls them “predefined roles”. `pg_monitor` is available across the supported PostgreSQL 13–18 profile family. Do not grant `SUPERUSER`, `pg_read_all_data`, `pg_write_all_data`, or `pg_signal_backend` to the monitoring account.

## Operational notes

- Apply grants as a DBA through the database's normal change process; Toolkit never applies them.
- These statements cover the current supported read-only collectors. Optional evidence can remain unavailable without implying the database is healthy or unhealthy.
- A successful connection does not prove every collector can read every source. If a diagnostic query returns a vendor access-denied error, retain that error and compare the denied object with the appropriate section above.
- This document does not authorize grants in production. Confirm the target, account, scope, and version before applying any statement.

## References

- [Microsoft: `sys.dm_exec_requests` permissions](https://learn.microsoft.com/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-requests-transact-sql)
- [Microsoft: `sys.dm_exec_query_stats` permissions](https://learn.microsoft.com/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql)
- [Oracle: `GRANT`](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/GRANT.html)
- [MySQL 8.4: Privileges Provided by MySQL](https://dev.mysql.com/doc/refman/8.4/en/privileges-provided.html)
- [MySQL 8.4: Performance Schema table privileges](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-table-characteristics.html)
- [PostgreSQL 16: Predefined Roles](https://www.postgresql.org/docs/16/predefined-roles.html)
- [PostgreSQL 13: Default Roles](https://www.postgresql.org/docs/13/default-roles.html)
