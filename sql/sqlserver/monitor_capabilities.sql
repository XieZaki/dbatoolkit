SELECT IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin,
    IS_SRVROLEMEMBER('securityadmin') AS is_securityadmin,
    IS_SRVROLEMEMBER('serveradmin') AS is_serveradmin,
    IS_MEMBER('db_owner') AS is_db_owner,
    CASE WHEN EXISTS (
        SELECT 1 FROM sys.databases AS d
        WHERE d.database_id = DB_ID() AND d.owner_sid = SUSER_SID()
    ) THEN 1 ELSE 0 END AS is_database_owner,
    CONVERT(int, PARSENAME(CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion')), 4)) AS major_version,
    HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER STATE') AS view_server_state,
    HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER PERFORMANCE STATE') AS view_server_performance_state,
    HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW ANY DATABASE') AS view_any_database,
    HAS_PERMS_BY_NAME('msdb.dbo.backupset', 'OBJECT', 'SELECT') AS backup_history_select,
    CASE WHEN EXISTS (SELECT 1 FROM sys.all_columns
        WHERE object_id = OBJECT_ID('sys.dm_exec_sessions') AND name = 'database_id')
        THEN 1 ELSE 0 END AS sessions_database_id,
    CASE WHEN EXISTS (SELECT 1 FROM sys.all_columns
        WHERE object_id = OBJECT_ID('sys.dm_exec_sessions') AND name = 'open_transaction_count')
        THEN 1 ELSE 0 END AS sessions_open_transaction_count;
