SELECT sid, type, id1, id2, lmode, request FROM v$lock WHERE ROWNUM <= 1
