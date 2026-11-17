SELECT SYS_CONTEXT('USERENV', 'INSTANCE') AS instance_id,
       SYS_CONTEXT('USERENV', 'SID') AS session_id,
       SYS_CONTEXT('USERENV', 'SESSIONID') AS audit_session_id
FROM DUAL
