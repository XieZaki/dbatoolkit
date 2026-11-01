SELECT pg_is_in_recovery() AS in_recovery,
 CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn()::text
 ELSE pg_current_wal_lsn()::text END AS current_lsn,
 current_setting('wal_keep_size') AS wal_keep_size,
 current_setting('max_slot_wal_keep_size') AS max_slot_wal_keep_size,
 current_setting('max_wal_size') AS max_wal_size
