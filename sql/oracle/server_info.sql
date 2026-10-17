SELECT d.name AS database_name, i.instance_name, i.instance_number AS inst_id,
 i.host_name, i.version AS server_version, i.startup_time, i.status,
 d.database_role, d.open_mode, CURRENT_TIMESTAMP AS current_timestamp
FROM v$instance i CROSS JOIN v$database d
