SELECT CONNECTION_ID() AS connection_id, VERSION() AS version,
       @@version_comment AS product, @@SESSION.max_execution_time AS session_limit_ms
