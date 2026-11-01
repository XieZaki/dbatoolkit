WITH request_metrics AS (
    SELECT
        r.session_id,
        r.request_id,
        r.start_time,
        r.cpu_time AS cpu_time_ms,
        r.logical_reads,
        r.total_elapsed_time AS elapsed_time_ms,
        r.wait_type,
        r.wait_time AS wait_time_ms,
        r.blocking_session_id,
        r.sql_handle,
        r.query_hash,
        r.plan_handle,
        r.statement_start_offset,
        r.statement_end_offset,
        r.database_id
    FROM sys.dm_exec_requests AS r
    INNER JOIN sys.dm_exec_sessions AS s
        ON s.session_id = r.session_id
    WHERE s.is_user_process = 1
      AND r.session_id <> @@SPID
      AND ISNULL(s.program_name, N'') <> N'dba-toolkit'
),
cpu_ranked AS (
    SELECT session_id, request_id, start_time,
        ROW_NUMBER() OVER (
            ORDER BY cpu_time_ms DESC, session_id ASC, request_id ASC, start_time ASC
        ) AS metric_rank
    FROM request_metrics
),
logical_reads_ranked AS (
    SELECT session_id, request_id, start_time,
        ROW_NUMBER() OVER (
            ORDER BY logical_reads DESC, session_id ASC, request_id ASC, start_time ASC
        ) AS metric_rank
    FROM request_metrics
),
elapsed_ranked AS (
    SELECT session_id, request_id, start_time,
        ROW_NUMBER() OVER (
            ORDER BY elapsed_time_ms DESC, session_id ASC, request_id ASC, start_time ASC
        ) AS metric_rank
    FROM request_metrics
),
candidate_requests AS (
    SELECT session_id, request_id, start_time
    FROM cpu_ranked
    WHERE metric_rank <= ?
    UNION
    SELECT session_id, request_id, start_time
    FROM logical_reads_ranked
    WHERE metric_rank <= ?
    UNION
    SELECT session_id, request_id, start_time
    FROM elapsed_ranked
    WHERE metric_rank <= ?
),
request_text AS (
    SELECT
        r.session_id,
        r.request_id,
        r.start_time,
        r.cpu_time_ms,
        r.logical_reads,
        r.elapsed_time_ms,
        r.wait_type,
        r.wait_time_ms,
        r.blocking_session_id,
        CONVERT(varchar(130), r.sql_handle, 1) AS sql_handle,
        CONVERT(varchar(130), r.query_hash, 1) AS query_hash,
        CONVERT(varchar(130), r.plan_handle, 1) AS plan_handle,
        r.statement_start_offset,
        r.statement_end_offset,
        r.database_id,
        CASE WHEN st.text IS NOT NULL AND tx.statement_length_chars IS NOT NULL
             THEN 1 ELSE 0 END AS sql_text_available,
        CASE WHEN tx.statement_length_chars > 2000 THEN 1 ELSE 0 END AS sql_text_truncated,
        CASE WHEN st.text IS NULL OR tx.statement_length_chars IS NULL THEN NULL
             ELSE LEFT(stmt.statement_sample, 2000) END AS statement_text
    FROM request_metrics AS r
    INNER JOIN candidate_requests AS c
        ON c.session_id = r.session_id
       AND c.request_id = r.request_id
       AND c.start_time = r.start_time
    OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS st
    CROSS APPLY (
        SELECT CASE
            WHEN st.text IS NULL OR r.statement_start_offset IS NULL
                OR r.statement_start_offset < 0 THEN NULL
            WHEN r.statement_end_offset = -1
                THEN CASE WHEN DATALENGTH(st.text) >= r.statement_start_offset
                          THEN (DATALENGTH(st.text) - r.statement_start_offset) / 2
                          ELSE NULL END
            WHEN r.statement_end_offset >= r.statement_start_offset
                THEN (r.statement_end_offset - r.statement_start_offset) / 2
            ELSE NULL
        END AS statement_length_chars
    ) AS tx
    CROSS APPLY (
        SELECT CASE WHEN tx.statement_length_chars > 2001 THEN 2001
                    ELSE tx.statement_length_chars END AS sample_length
    ) AS bounded
    OUTER APPLY (
        SELECT SUBSTRING(
            st.text,
            CASE WHEN r.statement_start_offset >= 0
                 THEN (r.statement_start_offset / 2) + 1 ELSE 1 END,
            bounded.sample_length
        ) AS statement_sample
    ) AS stmt
)
SELECT
    request_text.session_id,
    request_text.request_id,
    request_text.start_time,
    request_text.cpu_time_ms,
    request_text.logical_reads,
    request_text.elapsed_time_ms,
    request_text.wait_type,
    request_text.wait_time_ms,
    request_text.blocking_session_id,
    request_text.sql_handle,
    request_text.query_hash,
    request_text.plan_handle,
    request_text.statement_start_offset,
    request_text.statement_end_offset,
    request_text.database_id,
    request_text.sql_text_available,
    request_text.sql_text_truncated,
    request_text.statement_text
FROM request_text
ORDER BY request_text.session_id ASC,
         request_text.request_id ASC,
         request_text.start_time ASC;
