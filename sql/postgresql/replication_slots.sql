SELECT slot_name,
       slot_type,
       active,
       active_pid,
       restart_lsn::text AS restart_lsn,
       confirmed_flush_lsn::text AS confirmed_flush_lsn,
       wal_status,
       safe_wal_size,
       CASE WHEN NOT pg_is_in_recovery() AND restart_lsn IS NOT NULL
            THEN pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint ELSE NULL END AS retained_wal_bytes
FROM pg_replication_slots
ORDER BY slot_name
