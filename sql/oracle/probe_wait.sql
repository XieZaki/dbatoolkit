SELECT sid, event, wait_class, state FROM v$session_wait WHERE ROWNUM <= 1
