SELECT r.session_id, r.request_id, r.status, r.command, r.start_time,
    r.database_id,
    CONVERT(varchar(130), r.sql_handle, 1) AS sql_handle,
    r.statement_start_offset, r.statement_end_offset,
    CONVERT(varchar(130), r.plan_handle, 1) AS plan_handle,
    CONVERT(varchar(130), r.query_hash, 1) AS query_hash,
    CONVERT(varchar(130), r.query_plan_hash, 1) AS query_plan_hash,
    r.total_elapsed_time AS elapsed_ms, r.cpu_time AS cpu_ms,
    r.logical_reads, r.reads, r.writes, r.wait_type, r.wait_time AS wait_ms,
    r.blocking_session_id, r.wait_resource, DB_NAME(r.database_id) AS database_name,
    CASE WHEN DB_NAME(r.database_id) IS NULL
        THEN 'Database name unavailable: metadata visibility or request changed'
        ELSE NULL END AS database_name_unavailable_reason,
    LEFT(t.text, 16384) AS sql_text,
    CASE WHEN DATALENGTH(t.text) > 32768 THEN 1 ELSE 0 END AS sql_text_truncated,
    CASE
        WHEN t.text IS NULL OR r.statement_start_offset IS NULL
            OR r.statement_end_offset IS NULL OR r.statement_start_offset < 0
            OR (r.statement_start_offset % 2) <> 0
            OR r.statement_start_offset > DATALENGTH(t.text)
            OR (r.statement_end_offset <> -1 AND
                (r.statement_end_offset < r.statement_start_offset
                    OR (r.statement_end_offset % 2) <> 0
                    OR r.statement_end_offset > DATALENGTH(t.text)))
            OR (r.statement_end_offset = -1 AND
                DATALENGTH(t.text) < r.statement_start_offset)
        THEN NULL
        ELSE SUBSTRING(
            t.text,
            (r.statement_start_offset / 2) + 1,
            CASE WHEN r.statement_end_offset = -1
                THEN 16384
                ELSE (r.statement_end_offset - r.statement_start_offset) / 2
            END
        )
    END AS statement_sql_text,
    CASE
        WHEN t.text IS NULL THEN 'SQL text unavailable from sys.dm_exec_sql_text'
        WHEN r.statement_start_offset IS NULL OR r.statement_end_offset IS NULL
            OR r.statement_start_offset < 0 OR (r.statement_start_offset % 2) <> 0
            OR r.statement_start_offset > DATALENGTH(t.text)
            OR (r.statement_end_offset <> -1 AND
                (r.statement_end_offset < r.statement_start_offset
                    OR (r.statement_end_offset % 2) <> 0
                    OR r.statement_end_offset > DATALENGTH(t.text)))
            OR (r.statement_end_offset = -1 AND
                DATALENGTH(t.text) < r.statement_start_offset)
            THEN 'Current statement offsets are unavailable or invalid'
        WHEN r.statement_end_offset = -1
            AND (DATALENGTH(t.text) - r.statement_start_offset) = 0
            THEN 'Current statement contains no text at the sampled offsets'
        WHEN r.statement_end_offset <> -1
            AND r.statement_end_offset = r.statement_start_offset
            THEN 'Current statement contains no text at the sampled offsets'
        ELSE NULL
    END AS statement_sql_text_unavailable_reason,
    CASE
        WHEN t.text IS NULL OR r.statement_start_offset IS NULL
            OR r.statement_end_offset IS NULL OR r.statement_start_offset < 0
            OR (r.statement_start_offset % 2) <> 0
            OR r.statement_start_offset > DATALENGTH(t.text)
            OR (r.statement_end_offset <> -1 AND
                (r.statement_end_offset < r.statement_start_offset
                    OR (r.statement_end_offset % 2) <> 0
                    OR r.statement_end_offset > DATALENGTH(t.text)))
            OR (r.statement_end_offset = -1 AND
                DATALENGTH(t.text) < r.statement_start_offset)
        THEN 0
        WHEN r.statement_end_offset = -1
            THEN CASE WHEN DATALENGTH(t.text) - r.statement_start_offset > 32768
                THEN 1 ELSE 0 END
        ELSE CASE WHEN r.statement_end_offset - r.statement_start_offset > 32768
            THEN 1 ELSE 0 END
    END AS statement_sql_text_truncated
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS t
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
ORDER BY r.total_elapsed_time DESC, r.session_id;
