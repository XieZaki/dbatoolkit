SELECT status,
       receive_start_lsn::text AS receive_start_lsn,
       written_lsn::text AS written_lsn,
       flushed_lsn::text AS flushed_lsn,
       latest_end_lsn::text AS latest_end_lsn,
       latest_end_time,
       slot_name,
       sender_host,
       sender_port
FROM pg_stat_wal_receiver
