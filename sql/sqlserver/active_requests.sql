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
    CONVERT(nvarchar(max), SUBSTRING(source.batch_bytes, 1, limits.batch_length)) AS sql_text,
    CASE WHEN source.batch_length > 32768 THEN 1 ELSE 0 END AS sql_text_truncated,
    CASE WHEN span.statement_length > 0 THEN
        CONVERT(nvarchar(max), SUBSTRING(source.batch_bytes, source.start_offset + 1, limits.statement_length))
    ELSE NULL END AS statement_sql_text,
    CASE
        WHEN t.text IS NULL THEN 'SQL text unavailable from sys.dm_exec_sql_text'
        WHEN span.statement_length IS NULL
            THEN 'Current statement offsets are unavailable or invalid'
        WHEN span.statement_length = 0
            THEN 'Current statement contains no text at the sampled offsets'
        ELSE NULL
    END AS statement_sql_text_unavailable_reason,
    CASE WHEN span.statement_length > 32768 THEN 1 ELSE 0 END AS statement_sql_text_truncated
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS t
CROSS APPLY (
    SELECT CONVERT(varbinary(max), t.text) AS batch_bytes, DATALENGTH(t.text) AS batch_length,
        CONVERT(bigint, r.statement_start_offset) AS start_offset,
        CONVERT(bigint, r.statement_end_offset) AS end_offset
) AS source
CROSS APPLY (
    SELECT CASE
        WHEN source.batch_length IS NULL OR source.start_offset IS NULL OR source.end_offset IS NULL
            OR source.start_offset < 0 OR source.start_offset % 2 <> 0
            OR source.start_offset > source.batch_length
            OR SUBSTRING(source.batch_bytes, source.start_offset + 2, 1) BETWEEN 0xDC AND 0xDF
            OR (source.end_offset <> -1 AND
                (source.end_offset < source.start_offset OR source.end_offset % 2 <> 0
                    OR source.end_offset > source.batch_length))
            THEN NULL
        WHEN source.end_offset IN (-1, source.batch_length)
            THEN source.batch_length - source.start_offset
        -- The DMV end position is inclusive. Complete a UTF-16 pair if the end points at its first unit.
        WHEN SUBSTRING(source.batch_bytes, source.end_offset + 2, 1) BETWEEN 0xD8 AND 0xDB
            AND SUBSTRING(source.batch_bytes, source.end_offset + 4, 1) BETWEEN 0xDC AND 0xDF
            THEN source.end_offset + 4 - source.start_offset
        ELSE source.end_offset + 2 - source.start_offset
    END AS statement_length
) AS span
-- Slice UTF-16 bytes independently of collation. Keep the cap on a complete code point.
CROSS APPLY (
    SELECT CASE WHEN source.batch_length <= 32768 THEN source.batch_length
                WHEN SUBSTRING(source.batch_bytes, 32768, 1) BETWEEN 0xD8 AND 0xDB THEN 32766
                ELSE 32768 END AS batch_length,
        CASE WHEN span.statement_length IS NULL THEN 0
             WHEN span.statement_length <= 32768 THEN span.statement_length
             WHEN SUBSTRING(source.batch_bytes, source.start_offset + 32768, 1) BETWEEN 0xD8 AND 0xDB THEN 32766
             ELSE 32768 END AS statement_length
) AS limits
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
ORDER BY r.total_elapsed_time DESC, r.session_id;
