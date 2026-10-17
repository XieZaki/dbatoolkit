SELECT drs.database_id, DB_NAME(drs.database_id) AS database_name,
 ar.replica_server_name, drs.is_local, drs.synchronization_state_desc,
 drs.synchronization_health_desc, drs.log_send_queue_size,
 drs.redo_queue_size
FROM sys.dm_hadr_database_replica_states AS drs
LEFT JOIN sys.availability_replicas AS ar ON ar.replica_id = drs.replica_id
ORDER BY drs.database_id, ar.replica_server_name
