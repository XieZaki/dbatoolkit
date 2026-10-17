SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid AS blocked_sid, s.serial# AS blocked_serial_number,
 s.blocking_instance AS blocker_inst_id, s.blocking_session AS blocker_sid,
 s.blocking_session_status, s.event, s.wait_class, s.seconds_in_wait,
 (SELECT COUNT(*) FROM v$lock l WHERE l.sid = s.sid AND l.request > 0)
 AS waiting_lock_count,
 CASE WHEN s.blocking_instance = TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE'))
  THEN (SELECT COUNT(*) FROM v$locked_object o WHERE o.session_id = s.blocking_session)
 END AS blocker_locked_object_count
FROM v$session s
WHERE s.type = 'USER' AND s.blocking_session_status = 'VALID'
 AND s.blocking_session IS NOT NULL
