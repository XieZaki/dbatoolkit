SELECT name AS database_name, open_mode, database_role, log_mode,
 force_logging, protection_mode, protection_level
FROM v$database
