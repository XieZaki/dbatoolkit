SELECT application_name, state, sync_state, replay_lag,
 sent_lsn::text AS sent_lsn, replay_lsn::text AS replay_lsn
FROM pg_stat_replication
