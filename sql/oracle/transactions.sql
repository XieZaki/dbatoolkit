SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
 s.sid, s.serial# AS serial_number, t.start_date AS transaction_start_time,
 ROUND((SYSDATE - t.start_date) * 86400) AS transaction_age_seconds,
 t.used_ublk, t.used_urec, t.status AS transaction_status,
 s.username, s.machine, s.program, s.sql_id, s.prev_sql_id
FROM v$transaction t JOIN v$session s ON s.taddr = t.addr
WHERE s.type = 'USER'
