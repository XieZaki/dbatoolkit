SELECT TOP (?)
    CONVERT(varchar(130), qs.sql_handle, 1) AS sql_handle,
    CONVERT(varchar(130), qs.plan_handle, 1) AS plan_handle,
    CONVERT(varchar(130), qs.query_hash, 1) AS query_hash,
    CONVERT(varchar(130), qs.query_plan_hash, 1) AS query_plan_hash,
    qs.plan_generation_num,
    qs.statement_start_offset,
    qs.statement_end_offset,
    st.dbid AS database_id,
    CASE WHEN st.text IS NULL THEN 0 ELSE 1 END AS sql_text_available,
    qs.total_worker_time AS total_cpu_time,
    qs.total_logical_reads AS total_logical_reads,
    qs.total_physical_reads AS total_physical_reads,
    qs.execution_count,
    qs.total_elapsed_time AS total_elapsed_time,
    CONVERT(decimal(38, 4), qs.total_worker_time) / NULLIF(qs.execution_count, 0)
        AS average_cpu_time,
    CONVERT(decimal(38, 4), qs.total_elapsed_time) / NULLIF(qs.execution_count, 0)
        AS average_elapsed_time,
    ranking.ranking_value
FROM sys.dm_exec_query_stats AS qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS st
CROSS APPLY (SELECT CASE ?
    WHEN N'cpu_time' THEN CONVERT(decimal(38, 4), qs.total_worker_time)
    WHEN N'logical_reads' THEN CONVERT(decimal(38, 4), qs.total_logical_reads)
    WHEN N'physical_reads' THEN CONVERT(decimal(38, 4), qs.total_physical_reads)
    WHEN N'execution_count' THEN CONVERT(decimal(38, 4), qs.execution_count)
    WHEN N'elapsed_time' THEN CONVERT(decimal(38, 4), qs.total_elapsed_time)
    WHEN N'average_cpu_time' THEN
        CONVERT(decimal(38, 4), qs.total_worker_time) / NULLIF(qs.execution_count, 0)
    WHEN N'average_elapsed_time' THEN
        CONVERT(decimal(38, 4), qs.total_elapsed_time) / NULLIF(qs.execution_count, 0)
    ELSE NULL END AS ranking_value) AS ranking
WHERE qs.execution_count > 0
  AND ranking.ranking_value IS NOT NULL
ORDER BY ranking.ranking_value DESC,
    qs.sql_handle ASC,
    qs.statement_start_offset ASC,
    qs.statement_end_offset ASC,
    qs.plan_handle ASC,
    qs.plan_generation_num DESC;
