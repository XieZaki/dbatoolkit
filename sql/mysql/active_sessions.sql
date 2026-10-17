SELECT t.processlist_id, t.thread_id, t.processlist_user AS user_name,
    t.processlist_host AS host_raw, t.processlist_db AS database_name,
    t.processlist_command AS command_name, t.processlist_time AS time_seconds,
    t.processlist_state AS state_name,
    LEFT(t.processlist_info, 16384) AS sql_text,
    CHAR_LENGTH(t.processlist_info) > 16384 AS sql_text_truncated,
    e.event_name AS current_event, x.trx_state AS transaction_state,
    x.trx_id AS transaction_id
FROM performance_schema.threads AS t
LEFT JOIN performance_schema.events_statements_current AS e
    ON e.thread_id = t.thread_id AND e.end_event_id IS NULL
LEFT JOIN information_schema.innodb_trx AS x
    ON x.trx_mysql_thread_id = t.processlist_id
WHERE t.type = 'FOREGROUND' AND t.processlist_id IS NOT NULL
    AND t.processlist_id <> CONNECTION_ID()
ORDER BY t.processlist_id;
