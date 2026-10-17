SELECT slot_name, slot_type, active, restart_lsn::text AS restart_lsn,
 confirmed_flush_lsn::text AS confirmed_flush_lsn,
 CASE WHEN restart_lsn IS NULL THEN NULL
 ELSE pg_wal_lsn_diff(CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn()
 ELSE pg_current_wal_lsn() END, restart_lsn)::bigint END AS retained_wal_estimate_bytes
FROM pg_replication_slots ORDER BY slot_name
