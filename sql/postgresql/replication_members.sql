SELECT application_name,
       client_addr::text AS client_addr,
       state,
       sync_state,
       sent_lsn::text AS sent_lsn,
       write_lsn::text AS write_lsn,
       flush_lsn::text AS flush_lsn,
       replay_lsn::text AS replay_lsn,
       CASE WHEN sent_lsn IS NOT NULL AND write_lsn IS NOT NULL
            THEN pg_wal_lsn_diff(sent_lsn, write_lsn)::bigint ELSE NULL END AS write_lag_bytes,
       CASE WHEN sent_lsn IS NOT NULL AND flush_lsn IS NOT NULL
            THEN pg_wal_lsn_diff(sent_lsn, flush_lsn)::bigint ELSE NULL END AS flush_lag_bytes,
       CASE WHEN sent_lsn IS NOT NULL AND replay_lsn IS NOT NULL
            THEN pg_wal_lsn_diff(sent_lsn, replay_lsn)::bigint ELSE NULL END AS replay_lag_bytes,
       write_lag,
       flush_lag,
       replay_lag,
       reply_time,
       backend_start
FROM pg_stat_replication
ORDER BY application_name, client_addr
