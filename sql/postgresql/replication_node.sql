SELECT current_setting('server_version_num')::integer AS server_version_num,
       pg_is_in_recovery() AS in_recovery,
       current_setting('wal_level') AS wal_level,
       current_setting('max_wal_senders') AS max_wal_senders,
       current_setting('max_replication_slots') AS max_replication_slots,
       CASE WHEN pg_is_in_recovery() THEN NULL ELSE pg_current_wal_lsn()::text END AS current_wal_lsn,
       CASE WHEN pg_is_in_recovery() THEN pg_last_wal_receive_lsn()::text ELSE NULL END AS receive_lsn,
       CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()::text ELSE NULL END AS replay_lsn,
       CASE WHEN pg_is_in_recovery() THEN pg_last_xact_replay_timestamp() ELSE NULL END AS last_replay_timestamp,
       CASE WHEN pg_is_in_recovery() AND pg_last_wal_receive_lsn() IS NOT NULL
                  AND pg_last_wal_replay_lsn() IS NOT NULL
            THEN pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn())::bigint
            ELSE NULL END AS receive_replay_bytes
