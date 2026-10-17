SELECT TOP (1) c.session_id, CAST(NULL AS int) AS request_id,
    q.last_execution_time AS start_time, q.last_elapsed_time / 1000 AS elapsed_ms,
    q.last_worker_time / 1000 AS cpu_ms, q.last_logical_reads AS logical_reads,
    q.last_physical_reads AS reads, q.last_logical_writes AS writes,
    CAST(NULL AS nvarchar(60)) AS wait_type, CAST(NULL AS int) AS wait_ms,
    DB_NAME(t.dbid) AS database_name, s.status, CAST(NULL AS nvarchar(32)) AS command,
    LEFT(t.text, 16384) AS sql_text,
    LEFT(SUBSTRING(t.text, (q.statement_start_offset / 2) + 1,
        CASE WHEN q.statement_end_offset = -1
             THEN (DATALENGTH(t.text) - q.statement_start_offset) / 2 + 1
             ELSE (q.statement_end_offset - q.statement_start_offset) / 2 + 1 END),
        16384) AS statement_text,
    CONVERT(varchar(130), q.sql_handle, 1) AS sql_handle,
    CONVERT(varchar(130), q.plan_handle, 1) AS plan_handle,
    CASE WHEN DATALENGTH(CONVERT(nvarchar(max), p.query_plan)) <= ?
         THEN CONVERT(nvarchar(max), p.query_plan) ELSE NULL END AS plan_xml,
    CASE WHEN DATALENGTH(CONVERT(nvarchar(max), p.query_plan)) > ?
         THEN 1 ELSE 0 END AS plan_oversize
FROM sys.dm_exec_connections AS c
JOIN sys.dm_exec_sessions AS s ON s.session_id = c.session_id
LEFT JOIN sys.dm_exec_query_stats AS q ON q.sql_handle = c.most_recent_sql_handle
OUTER APPLY sys.dm_exec_sql_text(q.sql_handle) AS t
OUTER APPLY sys.dm_exec_query_plan(q.plan_handle) AS p
WHERE c.session_id = ? AND c.session_id <> @@SPID AND s.is_user_process = 1
ORDER BY q.last_execution_time DESC, c.connect_time DESC;
