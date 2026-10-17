SELECT t.processlist_id, t.thread_id, t.processlist_user AS user_name,
    t.processlist_host AS host_raw, t.processlist_db AS database_name,
    t.processlist_command AS command_name, t.processlist_state AS state_name,
    LEFT(t.processlist_info, 16384) AS sql_text,
    CHAR_LENGTH(t.processlist_info) > 16384 AS sql_text_truncated,
    t.processlist_time * 1000 AS duration_ms,
    NULL AS rows_examined, NULL AS rows_affected, NULL AS current_wait
FROM performance_schema.threads AS t
WHERE t.type = 'FOREGROUND' AND t.processlist_id IS NOT NULL
    AND t.processlist_id <> CONNECTION_ID()
    AND t.processlist_command <> 'Sleep' AND t.processlist_info IS NOT NULL
ORDER BY duration_ms DESC;
