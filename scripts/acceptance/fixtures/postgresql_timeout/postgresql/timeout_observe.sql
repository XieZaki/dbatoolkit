SELECT pid AS backend_pid, backend_start, state, wait_event
FROM pg_catalog.pg_stat_activity
WHERE pid = -1;
