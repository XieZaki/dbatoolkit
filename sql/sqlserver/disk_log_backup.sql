SELECT MAX(backup_finish_date) AS last_log_backup_time
FROM msdb.dbo.backupset WHERE database_name = DB_NAME() AND type = 'L'
