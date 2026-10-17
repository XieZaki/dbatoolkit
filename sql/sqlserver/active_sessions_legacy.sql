SELECT s.session_id, s.login_name, s.host_name, s.program_name,
    c.client_net_address, DB_NAME(p.dbid) AS database_name,
    s.status, s.login_time, s.last_request_start_time, s.last_request_end_time,
    p.open_tran AS open_transaction_count,
    'sys.sysprocesses.dbid' AS database_name_source,
    'sys.sysprocesses.open_tran' AS open_transaction_count_source,
    CASE WHEN p.spid IS NULL THEN 'Process metadata absent at snapshot; transaction count unavailable'
        ELSE NULL END AS open_transaction_count_unavailable_reason,
    CASE WHEN c.client_net_address IS NULL
        THEN 'Address unavailable: local transport, missing connection metadata or session changed'
        ELSE NULL END AS client_net_address_unavailable_reason,
    CASE WHEN DB_NAME(p.dbid) IS NULL
        THEN 'Database name unavailable: process metadata absent, visibility or session changed'
        ELSE NULL END AS database_name_unavailable_reason
FROM sys.dm_exec_sessions AS s
LEFT JOIN sys.dm_exec_connections AS c ON s.session_id = c.session_id
    AND c.parent_connection_id IS NULL
LEFT JOIN sys.sysprocesses AS p ON s.session_id = p.spid AND p.ecid = 0
WHERE s.is_user_process = 1 AND s.session_id <> @@SPID
ORDER BY s.session_id;
