# Replication and high availability diagnosis

V0.9.0 adds `dba diagnose <database_id> replication` for the SQL Server, PostgreSQL, MySQL, and Oracle adapters. The command connects only to the selected Inventory target and takes a bounded, sequential read-only snapshot. It never connects to another member discovered in database metadata.

## Coverage

- **MySQL 5.7, 8.0 and 8.4:** profile routing selects fixed `SHOW SLAVE STATUS` for 5.7 and MySQL 8.0 before 8.0.22; it selects fixed `SHOW REPLICA STATUS` for 8.0.22+ and 8.4. The report includes receiver/applier states, source host/port, source-reported seconds behind, configured delay, relay log space, GTID sets, and vendor I/O or SQL errors. Legacy and modern field aliases are selected from the same resolved profile. Zero returned channels with a complete query is `NOT_CONFIGURED` and complete evidence.
- **PostgreSQL 13–18:** one reviewed profile is explicitly bound to each family. `pg_is_in_recovery()` identifies primary or standby. A primary reports all rows in `pg_stat_replication` and `pg_replication_slots`; a standby reports `pg_stat_wal_receiver`, receive/replay LSN distance, and replay timestamp when available. Slot WAL retention is shown as observed bytes. An inactive physical slot is a risk candidate, not a command to drop it. A primary with no peers/slots is `NOT_CONFIGURED` and healthy when all evidence queries complete; `max_wal_senders` alone does not imply an expected standby.
- **SQL Server 2012+:** Availability Group, replica, and database replica DMV evidence, including role, connection and synchronization state, log-send/redo queues and rates, and last commit time when returned. Queue sizes stay in KB. Catch-up time is an estimate only when a positive rate is returned. SQL Server 2008 R2 has no Availability Groups and reports this feature as unsupported. Instances without an AG report `NOT_CONFIGURED` when the reviewed AG query succeeds and returns no rows.
- **Oracle 11gR2 and later:** Oracle 11gR2 (`11.2.x`) and 12.1 use `V$MANAGED_STANDBY`; 12.2+ uses `V$DATAGUARD_PROCESS`. The selected profile binds exact SQL objects, complete object-level grants, and parser aliases. The 11gR2 route does not use `V$DATAGUARD_PROCESS`. Primary databases with no configured remote archive destination report `NOT_CONFIGURED`. Oracle errors from archive destinations remain vendor evidence. Oracle 11gR2 has unit/Fake Adapter coverage only and is not E2E verified.

## Status and lag

Overall health is `HEALTHY`, `DEGRADED`, `CRITICAL`, or `UNKNOWN`. A confirmed absence of replication configuration is presented as `NOT_CONFIGURED` with informational severity, not as a failure. Query failure, missing required evidence, or an unsupported server version is never interpreted as “no replication.” Each route's complete SQL bundle is required evidence; a failed or truncated query yields `INCOMPLETE` or `UNKNOWN`. Permission readiness uses the same selected profile. A PostgreSQL standalone primary with no slots/peers, or MySQL returning no channels, is `NOT_CONFIGURED` with `COMPLETE` evidence when all applicable queries succeed.

Time lag uses a centralized conservative policy: under 30 seconds is informational, 30 seconds to under 5 minutes is a warning, 5 to under 30 minutes is high, and 30 minutes or more is critical. This is only one signal alongside connection state, receiver/applier state, synchronization health, and backlog. Thresholds are policy defaults, not universal health guarantees.

Byte quantities remain bytes or KB as reported. They are not converted into time without an explicit reliable rate. SQL Server catch-up estimates use its reported positive KB/s queue rate and are marked `ESTIMATED`. PostgreSQL replay timestamp age is contextual only because an idle primary can make the timestamp appear old. A single snapshot reports observed backlog and cannot determine whether it is growing or shrinking.

## Permissions

Review current status with:

```powershell
dba permissions <database_id> --feature replication
```

Minimum required monitoring permissions are:

| Database | Required | Optional evidence |
|---|---|---|
| SQL Server 2012–2019 | `VIEW SERVER STATE` | None defined |
| SQL Server 2022 | `VIEW SERVER PERFORMANCE STATE` | None defined |
| PostgreSQL | `pg_monitor`, `CONNECT` | None defined |
| MySQL | `REPLICATION CLIENT` | None defined |
| Oracle 11gR2/12.1 | `CREATE SESSION`, `V_$VERSION`, `V_$DATABASE`, `V_$DATAGUARD_STATS`, `V_$MANAGED_STANDBY`, `V_$ARCHIVE_DEST_STATUS` | None defined |
| Oracle 12.2+ | `CREATE SESSION`, `V_$VERSION`, `V_$DATABASE`, `V_$DATAGUARD_STATS`, `V_$DATAGUARD_PROCESS`, `V_$ARCHIVE_DEST_STATUS` | None defined |

The permission command uses the shared Registry. It only generates minimum grant/revoke text for DBA review; Toolkit never executes GRANT or REVOKE. No database needs a sysadmin, SUPERUSER, SUPER, SYSDBA, or catalog-wide role for this feature.

## Reports and safe operation

The default terminal output is a compact incident panel. The offline HTML report has bounded tables for summary, topology, lag/backlog, issues, permission readiness, recommendations, and collection quality. JSON contains the structured summary and bounded vendor evidence. All displayed database-derived strings are escaped/redacted.

Replication diagnosis is observational only. Toolkit never automatically fails over, switches over, promotes, starts or stops replication, resets metadata, changes a source, reconfigures or resynchronizes replication, or repairs it. This command produces no administrative reference commands. `Toolkit Executed: NO` is included in the report.

## Manual environment verification

After configuring the relevant test Inventory IDs and monitor credentials, run each target explicitly:

```powershell
dba diagnose sqlserver2022 replication
dba diagnose pg16 replication
dba diagnose mysql84 replication
dba diagnose oraclefree replication
```

Replace these example IDs with the actual IDs in the local Inventory. Ordinary unit tests use Fake Drivers and do not connect to a database.
