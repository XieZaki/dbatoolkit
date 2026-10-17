SELECT @@version AS version, @@version_comment AS version_comment,
    @@server_uuid AS server_uuid, @@hostname AS hostname, @@port AS port,
    @@read_only AS read_only, @@super_read_only AS super_read_only,
    NOW() AS server_time,
    (SELECT variable_value FROM performance_schema.global_status
     WHERE variable_name = 'Uptime') AS uptime_seconds;
