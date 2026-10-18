SELECT r.session_id, r.request_id, r.start_time,
    r.total_elapsed_time AS elapsed_ms, r.cpu_time AS cpu_ms,
    r.logical_reads, r.reads, r.writes, r.wait_type, r.wait_time AS wait_ms,
    r.statement_start_offset, r.statement_end_offset,
    CONVERT(varchar(130), r.query_hash, 1) AS query_hash,
    CONVERT(varchar(130), r.query_plan_hash, 1) AS query_plan_hash,
    DB_NAME(r.database_id) AS database_name, r.status, r.command,
    LEFT(t.text, 16384) AS sql_text,
    LEFT(SUBSTRING(t.text, (r.statement_start_offset / 2) + 1,
        CASE WHEN r.statement_end_offset = -1
             THEN (DATALENGTH(t.text) - r.statement_start_offset) / 2 + 1
             ELSE (r.statement_end_offset - r.statement_start_offset) / 2 + 1 END),
        16384) AS statement_text,
    CONVERT(varchar(130), r.sql_handle, 1) AS sql_handle,
    CONVERT(varchar(130), r.plan_handle, 1) AS plan_handle,
    CASE WHEN DATALENGTH(CONVERT(nvarchar(max), p.query_plan)) <= ?
         THEN CONVERT(nvarchar(max), p.query_plan) ELSE NULL END AS plan_xml,
    CASE WHEN DATALENGTH(CONVERT(nvarchar(max), p.query_plan)) > ?
         THEN 1 ELSE 0 END AS plan_oversize
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON s.session_id = r.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS t
OUTER APPLY sys.dm_exec_query_plan(r.plan_handle) AS p
WHERE r.session_id = ? AND r.session_id <> @@SPID AND s.is_user_process = 1
ORDER BY r.request_id;
