SELECT DB_NAME() AS database_name, d.recovery_model_desc, d.log_reuse_wait_desc,
 d.is_cdc_enabled,
 u.total_log_size_in_bytes AS log_size_bytes, u.used_log_space_in_bytes AS log_used_bytes,
 u.used_log_space_in_percent AS log_used_percent
FROM sys.databases d CROSS JOIN sys.dm_db_log_space_usage u
WHERE d.database_id = DB_ID()
