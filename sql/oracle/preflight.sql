SELECT i.version AS server_version, i.instance_number AS inst_id,
 i.parallel AS rac_mode,
 SYS_CONTEXT('USERENV', 'SESSION_USER') AS monitor_user
FROM v$instance i
