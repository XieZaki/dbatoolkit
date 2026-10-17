SELECT d.datname AS database_name, s.numbackends AS connections,
    s.xact_commit, s.xact_rollback, s.blks_read, s.blks_hit,
    s.temp_files, s.temp_bytes, s.conflicts, s.deadlocks
FROM pg_stat_database AS s
JOIN pg_database AS d ON d.oid = s.datid
ORDER BY d.datname;
