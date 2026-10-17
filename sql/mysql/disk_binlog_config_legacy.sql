SELECT @@log_bin AS binary_log_enabled, @@binlog_format AS binlog_format,
 @@expire_logs_days AS expiration_days, @@gtid_mode AS gtid_mode,
 @@log_error AS error_log_path, @@slow_query_log AS slow_query_log_enabled,
 @@general_log AS general_log_enabled
