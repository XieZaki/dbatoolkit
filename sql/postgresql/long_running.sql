SELECT a.pid, a.datname, a.usename, a.application_name,
    a.client_addr::text AS client_addr, a.client_hostname, a.client_port,
    a.query_start, a.state, a.wait_event_type, a.wait_event,
    LEFT(a.query, 16384) AS query, length(a.query) > 16384 AS query_truncated,
    (EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start)) * 1000)::bigint AS duration_ms
FROM pg_stat_activity AS a
WHERE a.pid <> pg_backend_pid() AND a.backend_type = 'client backend'
    AND a.state = 'active' AND a.query_start IS NOT NULL
ORDER BY duration_ms DESC;
