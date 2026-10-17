SELECT c.session_id, c.connection_id, c.client_net_address, c.connect_time,
    c.protocol_type, c.net_transport, LEFT(t.text, 16384) AS last_sql,
    CASE WHEN DATALENGTH(t.text) > 32768 THEN 1 ELSE 0 END AS last_sql_truncated
FROM sys.dm_exec_connections AS c
JOIN sys.dm_exec_sessions AS s ON s.session_id = c.session_id
OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) AS t
WHERE s.is_user_process = 1 AND c.session_id <> @@SPID
ORDER BY c.session_id, c.connect_time DESC;
