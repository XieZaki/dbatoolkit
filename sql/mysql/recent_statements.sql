SELECT t.processlist_id AS session_id, t.thread_id AS thread_id,
    h.event_id AS event_id,
    CAST(h.timer_wait / 1000000000 AS UNSIGNED) AS duration_ms,
    LEFT(h.sql_text, 1001) AS sql_text,
    CHAR_LENGTH(h.sql_text) > 1000 AS sql_text_truncated
FROM performance_schema.threads AS t
LEFT JOIN performance_schema.events_statements_history AS h
    ON h.thread_id = t.thread_id AND h.thread_id = %s
WHERE t.processlist_id = %s AND t.thread_id = %s
ORDER BY h.event_id DESC
LIMIT 5;
