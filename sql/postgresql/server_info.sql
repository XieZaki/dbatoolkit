SELECT version() AS version,
    current_setting('server_version_num')::integer AS server_version_num,
    current_database() AS current_database,
    current_user AS current_user,
    pg_postmaster_start_time() AS server_start_time,
    EXTRACT(EPOCH FROM (clock_timestamp() - pg_postmaster_start_time()))::bigint AS uptime_seconds,
    clock_timestamp() AS current_timestamp;
