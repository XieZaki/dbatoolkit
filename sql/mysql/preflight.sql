SELECT VERSION() AS server_version, CURRENT_USER() AS monitor_user,
    @@version_comment AS version_comment,
    @@performance_schema AS performance_schema_enabled,
    CONNECTION_ID() AS connection_id,
    (SELECT COUNT(*) FROM performance_schema.setup_consumers
     WHERE name = 'events_statements_current' AND enabled = 'YES') AS statements_consumer,
    (SELECT COUNT(*) FROM performance_schema.setup_consumers
     WHERE name = 'events_waits_current' AND enabled = 'YES') AS waits_consumer,
    (SELECT COUNT(*) FROM performance_schema.setup_consumers
     WHERE name = 'events_statements_history' AND enabled = 'YES') AS history_consumer;
