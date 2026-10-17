SELECT ag.name AS ag_name,
       ar.replica_server_name,
       ars.is_local,
       ars.role_desc,
       ars.operational_state_desc,
       ars.connected_state_desc,
       ars.synchronization_health_desc AS replica_synchronization_health,
       ar.availability_mode_desc,
       ar.failover_mode_desc,
       adc.database_name,
       drs.database_state_desc,
       drs.synchronization_state_desc,
       drs.synchronization_health_desc AS database_synchronization_health,
       drs.log_send_queue_size,
       drs.log_send_rate,
       drs.redo_queue_size,
       drs.redo_rate,
       drs.last_commit_time
FROM sys.availability_groups AS ag
JOIN sys.availability_replicas AS ar
  ON ar.group_id = ag.group_id
LEFT JOIN sys.dm_hadr_availability_replica_states AS ars
  ON ars.replica_id = ar.replica_id
LEFT JOIN sys.dm_hadr_database_replica_states AS drs
  ON drs.replica_id = ar.replica_id
 AND drs.group_id = ag.group_id
LEFT JOIN sys.availability_databases_cluster AS adc
  ON adc.group_database_id = drs.group_database_id
ORDER BY ag.name, adc.database_name, ar.replica_server_name
