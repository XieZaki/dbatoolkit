SELECT t.processlist_id, t.thread_id, t.processlist_user AS user_name,
    t.processlist_host AS host_raw, t.processlist_db AS database_name,
    t.processlist_command AS command_name, t.processlist_state AS state_name,
    LEFT(e.sql_text, 16384) AS sql_text,
    CHAR_LENGTH(e.sql_text) > 16384 AS sql_text_truncated,
    GREATEST(COALESCE(CAST(e.timer_wait / 1000000000 AS UNSIGNED), 0),
             COALESCE(t.processlist_time * 1000, 0)) AS duration_ms,
    e.rows_examined, e.rows_affected, NULL AS current_wait
FROM performance_schema.threads AS t
JOIN performance_schema.events_statements_current AS e
    ON e.thread_id = t.thread_id AND e.end_event_id IS NULL
WHERE t.type = 'FOREGROUND' AND t.processlist_id IS NOT NULL
    AND t.processlist_id <> CONNECTION_ID() AND e.sql_text IS NOT NULL
ORDER BY duration_ms DESC;
