WITH ranked AS (
 SELECT s.sql_id, s.child_number, s.plan_hash_value,
        s.cpu_time, s.elapsed_time, s.buffer_gets, s.disk_reads, s.executions,
        CASE :metric
          WHEN 'cpu_time' THEN s.cpu_time
          WHEN 'elapsed_time' THEN s.elapsed_time
          WHEN 'buffer_gets' THEN s.buffer_gets
          WHEN 'disk_reads' THEN s.disk_reads
          WHEN 'executions' THEN s.executions
        END AS ranking_value
 FROM v$sql s
)
SELECT sql_id, child_number, plan_hash_value, cpu_time, elapsed_time,
       buffer_gets, disk_reads, executions, ranking_value
FROM (
 SELECT sql_id, child_number, plan_hash_value, cpu_time, elapsed_time,
        buffer_gets, disk_reads, executions, ranking_value
 FROM ranked
 ORDER BY ranking_value DESC NULLS LAST, sql_id ASC, child_number ASC
)
WHERE ROWNUM <= :top_n
ORDER BY ranking_value DESC NULLS LAST, sql_id ASC, child_number ASC
