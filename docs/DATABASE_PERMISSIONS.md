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
