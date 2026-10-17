SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid, s.serial# AS serial_number, s.sql_id, s.sql_child_number AS child_number,
 SUBSTR(q.sql_text, 1, 1000) AS sql_text,
 CASE WHEN s.sql_exec_start IS NOT NULL
  THEN ROUND((SYSDATE - s.sql_exec_start) * 86400)
  ELSE s.last_call_et END AS duration_seconds,
 CASE WHEN s.sql_exec_start IS NOT NULL THEN 'SQL_EXEC_START'
  ELSE 'LAST_CALL_ET_ACTIVE' END AS duration_source,
 s.last_call_et, q.elapsed_time AS sql_cumulative_elapsed_us,
 q.cpu_time AS sql_cumulative_cpu_us, q.buffer_gets AS sql_cumulative_buffer_gets,
 q.disk_reads AS sql_cumulative_disk_reads, q.executions AS sql_cumulative_executions,
 q.rows_processed AS sql_cumulative_rows_processed,
 s.module, s.machine, s.program, s.username, s.event, s.wait_class, s.state,
 s.blocking_session, s.blocking_instance
FROM v$session s LEFT JOIN v$sql q
 ON q.sql_id = s.sql_id AND q.child_number = s.sql_child_number
WHERE s.type = 'USER' AND s.status = 'ACTIVE'
 AND s.sql_id IS NOT NULL
 AND s.sid <> TO_NUMBER(SYS_CONTEXT('USERENV', 'SID'))
ORDER BY duration_seconds DESC NULLS LAST
