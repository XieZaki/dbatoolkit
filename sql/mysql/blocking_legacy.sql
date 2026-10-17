SELECT waiter_thread.THREAD_ID AS blocked_thread_id,
    waiter_trx.TRX_MYSQL_THREAD_ID AS blocked_pid,
    holder_thread.THREAD_ID AS blocker_thread_id,
    holder_trx.TRX_MYSQL_THREAD_ID AS blocker_pid,
    w.REQUESTING_ENGINE_TRANSACTION_ID AS blocked_transaction_id,
    w.BLOCKING_ENGINE_TRANSACTION_ID AS blocker_transaction_id,
    requested.LOCK_TYPE AS requested_lock_type,
    held.LOCK_TYPE AS held_lock_type
FROM information_schema.INNODB_LOCK_WAITS AS w
JOIN information_schema.INNODB_LOCKS AS requested
    ON requested.LOCK_ID = w.REQUESTED_LOCK_ID
JOIN information_schema.INNODB_LOCKS AS held
    ON held.LOCK_ID = w.BLOCKING_LOCK_ID
JOIN information_schema.INNODB_TRX AS waiter_trx
    ON waiter_trx.TRX_ID = w.REQUESTING_TRX_ID
JOIN information_schema.INNODB_TRX AS holder_trx
    ON holder_trx.TRX_ID = w.BLOCKING_TRX_ID
LEFT JOIN performance_schema.threads AS waiter_thread
    ON waiter_thread.PROCESSLIST_ID = waiter_trx.TRX_MYSQL_THREAD_ID
LEFT JOIN performance_schema.threads AS holder_thread
    ON holder_thread.PROCESSLIST_ID = holder_trx.TRX_MYSQL_THREAD_ID
ORDER BY blocked_pid, blocker_pid;
