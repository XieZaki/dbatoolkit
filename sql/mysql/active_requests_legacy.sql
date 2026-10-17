SELECT p.ID AS processlist_id, t.THREAD_ID AS thread_id,
    p.USER AS processlist_user, p.USER AS user_name,
    p.HOST AS processlist_host, p.HOST AS host_raw,
    p.DB AS processlist_db, p.DB AS database_name,
    p.COMMAND AS processlist_command, p.COMMAND AS command_name,
    p.TIME AS processlist_time, p.TIME AS time_seconds,
    p.STATE AS processlist_state, p.STATE AS state_name,
    LEFT(p.INFO, 16384) AS processlist_info,
    LEFT(p.INFO, 16384) AS sql_text,
    CHAR_LENGTH(p.INFO) > 16384 AS sql_text_truncated,
    e.EVENT_NAME AS current_event, x.TRX_STATE AS transaction_state,
    x.TRX_ID AS transaction_id,
    GREATEST(COALESCE(CAST(e.TIMER_WAIT / 1000000000 AS UNSIGNED), 0),
             COALESCE(p.TIME * 1000, 0)) AS duration_ms
FROM information_schema.PROCESSLIST AS p
LEFT JOIN performance_schema.threads AS t ON t.PROCESSLIST_ID = p.ID
LEFT JOIN performance_schema.events_statements_current AS e
    ON e.THREAD_ID = t.THREAD_ID AND e.END_EVENT_ID IS NULL
LEFT JOIN information_schema.INNODB_TRX AS x ON x.TRX_MYSQL_THREAD_ID = p.ID
WHERE p.ID IS NOT NULL AND p.ID <> CONNECTION_ID() AND p.INFO IS NOT NULL
ORDER BY duration_ms DESC, p.ID;
