SELECT a.pid AS blocked_pid, blockers.blocker_pid,
    a.wait_event_type, a.wait_event
FROM pg_stat_activity AS a
CROSS JOIN LATERAL unnest(pg_blocking_pids(a.pid)) AS blockers(blocker_pid)
WHERE a.pid <> pg_backend_pid() AND a.backend_type = 'client backend'
ORDER BY a.pid, blockers.blocker_pid;
