SELECT a.pid, a.state, a.xact_start,
    EXTRACT(EPOCH FROM (clock_timestamp() - a.xact_start))::bigint AS transaction_age_seconds,
    a.query_start, LEFT(a.query, 16384) AS query,
    length(a.query) > 16384 AS query_truncated,
    a.usename, a.datname, a.application_name,
    a.client_addr::text AS client_addr, a.client_hostname, a.client_port
FROM pg_stat_activity AS a
WHERE a.pid <> pg_backend_pid() AND a.backend_type = 'client backend'
    AND a.xact_start IS NOT NULL
ORDER BY a.xact_start;
