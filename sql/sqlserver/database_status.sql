SELECT name AS database_name, state_desc AS state,
    recovery_model_desc AS recovery_model, compatibility_level,
    user_access_desc AS user_access, is_read_only
FROM sys.databases
ORDER BY database_id;
