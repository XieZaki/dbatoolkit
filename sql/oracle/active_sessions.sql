SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid, s.serial# AS serial_number, s.username, s.status, s.schemaname,
 s.osuser, s.machine, s.program, s.module, s.action, s.client_identifier,
 s.service_name, s.sql_id, s.sql_child_number, s.prev_sql_id,
 s.prev_child_number, s.event, s.wait_class, s.state, s.seconds_in_wait,
 s.logon_time, s.last_call_et, s.sql_exec_start,
 s.blocking_session, s.blocking_instance
FROM v$session s
WHERE s.type = 'USER' AND s.sid <> TO_NUMBER(SYS_CONTEXT('USERENV', 'SID'))
