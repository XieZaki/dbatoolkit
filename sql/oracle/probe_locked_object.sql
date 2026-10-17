SELECT session_id, object_id, locked_mode FROM v$locked_object WHERE ROWNUM <= 1
