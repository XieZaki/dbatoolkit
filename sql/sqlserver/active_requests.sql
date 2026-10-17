SELECT r.session_id, r.status, r.command, r.start_time,
    r.total_elapsed_time AS elapsed_ms, r.cpu_time AS cpu_ms,
    r.logical_reads, r.reads, r.writes, r.wait_type, r.wait_time AS wait_ms,
    r.blocking_session_id, DB_NAME(r.database_id) AS database_name,
    LEFT(t.text, 16384) AS sql_text,
    CASE WHEN DATALENGTH(t.text) > 32768 THEN 1 ELSE 0 END AS sql_text_truncated
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS t
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
ORDER BY r.total_elapsed_time DESC, r.session_id;
