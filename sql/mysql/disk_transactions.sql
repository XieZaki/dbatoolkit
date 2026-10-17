SELECT x.trx_id, x.trx_state, x.trx_started, x.trx_wait_started,
    x.trx_mysql_thread_id, LEFT(x.trx_query, 16384) AS trx_query,
    CHAR_LENGTH(x.trx_query) > 16384 AS query_truncated,
    x.trx_tables_locked, x.trx_rows_locked, x.trx_rows_modified,
    TIMESTAMPDIFF(SECOND, x.trx_started, NOW()) AS transaction_age_seconds,
    t.thread_id, t.processlist_command AS command_name,
    t.processlist_user AS user_name, t.processlist_host AS host_raw,
    t.processlist_db AS database_name
FROM information_schema.innodb_trx AS x
LEFT JOIN performance_schema.threads AS t
    ON t.processlist_id = x.trx_mysql_thread_id
ORDER BY x.trx_started;
