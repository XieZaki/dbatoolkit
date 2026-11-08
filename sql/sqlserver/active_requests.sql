SELECT r.session_id, r.status, r.command, r.start_time,
    r.database_id,
    CONVERT(varchar(130), r.sql_handle, 1) AS sql_handle,
    r.statement_start_offset, r.statement_end_offset,
    CONVERT(varchar(130), r.plan_handle, 1) AS plan_handle,
    CONVERT(varchar(130), r.query_hash, 1) AS query_hash,
    CONVERT(varchar(130), r.query_plan_hash, 1) AS query_plan_hash,
    identity_stats.plan_generation_num,
    identity_stats.plan_generation_match_count,
    r.total_elapsed_time AS elapsed_ms, r.cpu_time AS cpu_ms,
    r.logical_reads, r.reads, r.writes, r.wait_type, r.wait_time AS wait_ms,
    r.blocking_session_id, r.wait_resource, DB_NAME(r.database_id) AS database_name,
    LEFT(t.text, 16384) AS sql_text,
    CASE WHEN DATALENGTH(t.text) > 32768 THEN 1 ELSE 0 END AS sql_text_truncated
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS t
LEFT JOIN (
    SELECT active.session_id, active.request_id,
        CASE WHEN COUNT(qs.plan_generation_num) = 1
            THEN MIN(qs.plan_generation_num) ELSE NULL END AS plan_generation_num,
        COUNT(qs.plan_generation_num) AS plan_generation_match_count
    FROM sys.dm_exec_requests AS active
    LEFT JOIN sys.dm_exec_query_stats AS qs
        ON qs.sql_handle = active.sql_handle
        AND qs.plan_handle = active.plan_handle
        AND qs.query_hash = active.query_hash
        AND qs.query_plan_hash = active.query_plan_hash
        AND qs.statement_start_offset = active.statement_start_offset
        AND qs.statement_end_offset = active.statement_end_offset
    GROUP BY active.session_id, active.request_id
) AS identity_stats
    ON identity_stats.session_id = r.session_id
    AND identity_stats.request_id = r.request_id
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
ORDER BY r.total_elapsed_time DESC, r.session_id;
