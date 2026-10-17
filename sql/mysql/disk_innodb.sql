SELECT MAX(CASE WHEN variable_name = 'innodb_redo_log_capacity' THEN variable_value END)
 AS redo_log_capacity,
 MAX(CASE WHEN variable_name = 'innodb_log_file_size' THEN variable_value END)
 AS legacy_redo_log_file_size,
 MAX(CASE WHEN variable_name = 'innodb_log_files_in_group' THEN variable_value END)
 AS redo_log_files,
 MAX(CASE WHEN variable_name = 'innodb_undo_tablespaces' THEN variable_value END)
 AS undo_tablespaces,
 MAX(CASE WHEN variable_name = 'innodb_max_undo_log_size' THEN variable_value END)
 AS max_undo_log_size
FROM performance_schema.global_variables
WHERE variable_name IN ('innodb_redo_log_capacity', 'innodb_log_file_size',
 'innodb_log_files_in_group', 'innodb_undo_tablespaces', 'innodb_max_undo_log_size')
