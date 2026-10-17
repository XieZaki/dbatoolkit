SELECT VERSION() AS server_version, CURRENT_USER() AS monitor_user,
    @@performance_schema AS performance_schema_enabled,
    CONNECTION_ID() AS connection_id,
    EXISTS (SELECT 1 FROM information_schema.user_privileges
            WHERE grantee = CONCAT(QUOTE(SUBSTRING_INDEX(CURRENT_USER(), '@', 1)),
                                   '@', QUOTE(SUBSTRING_INDEX(CURRENT_USER(), '@', -1)))
              AND privilege_type = 'REPLICATION CLIENT') AS replication_client,
    (SELECT COUNT(*) FROM performance_schema.setup_consumers
     WHERE name = 'events_statements_current' AND enabled = 'YES') AS statements_consumer,
    (SELECT COUNT(*) FROM performance_schema.setup_consumers
     WHERE name = 'events_waits_current' AND enabled = 'YES') AS waits_consumer;
