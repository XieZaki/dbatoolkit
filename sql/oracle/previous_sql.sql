SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid, s.serial# AS serial_number, s.logon_time, s.prev_sql_id,
 SUBSTR(q.sql_text, 1, 1000) AS previous_sql_text
FROM v$session s LEFT JOIN v$sql q
 ON q.sql_id = s.prev_sql_id AND q.child_number = s.prev_child_number
WHERE s.type = 'USER' AND s.blocking_session IS NULL
 AND s.prev_sql_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM v$session w WHERE w.blocking_session = s.sid
  AND w.blocking_instance = TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')))
