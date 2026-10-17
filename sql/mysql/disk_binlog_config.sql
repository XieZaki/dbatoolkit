SELECT @@log_bin AS binary_log_enabled, @@binlog_format AS binlog_format,
 @@binlog_expire_logs_seconds AS expiration_seconds,
 @@gtid_mode AS gtid_mode, @@log_error AS error_log_path,
 @@slow_query_log AS slow_query_log_enabled, @@general_log AS general_log_enabled
