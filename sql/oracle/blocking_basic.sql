SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid AS blocked_sid, s.serial# AS blocked_serial_number,
 s.blocking_instance AS blocker_inst_id, s.blocking_session AS blocker_sid,
 b.serial# AS blocker_serial_number,
 s.blocking_session_status, s.event, s.wait_class, s.seconds_in_wait
FROM v$session s
LEFT JOIN v$session b ON b.sid = s.blocking_session
 AND s.blocking_instance = TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE'))
WHERE s.type = 'USER' AND s.blocking_session_status = 'VALID'
 AND s.blocking_session IS NOT NULL
