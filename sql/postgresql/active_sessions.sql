SELECT a.pid, a.datname, a.usename, a.application_name,
    a.client_addr::text AS client_addr, a.client_hostname, a.client_port,
    a.backend_start, a.xact_start, a.query_start, a.state_change,
    a.wait_event_type, a.wait_event, a.state,
    a.backend_xid::text AS backend_xid, a.backend_xmin::text AS backend_xmin,
    LEFT(a.query, 16384) AS query, length(a.query) > 16384 AS query_truncated,
    CASE WHEN a.state = 'active' AND a.query_start IS NOT NULL
         THEN (EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start)) * 1000)::bigint
         ELSE NULL END AS duration_ms,
    CASE WHEN a.xact_start IS NOT NULL
         THEN EXTRACT(EPOCH FROM (clock_timestamp() - a.xact_start))::bigint
         ELSE NULL END AS transaction_age_seconds
FROM pg_stat_activity AS a
WHERE a.pid <> pg_backend_pid() AND a.backend_type = 'client backend'
ORDER BY a.pid;
