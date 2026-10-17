SELECT DISTINCT waiter.processlist_id AS blocked_pid,
    holder.processlist_id AS blocker_pid,
    w.requesting_thread_id AS blocked_thread_id,
    w.blocking_thread_id AS blocker_thread_id,
    w.requesting_engine_transaction_id AS blocked_transaction_id,
    w.blocking_engine_transaction_id AS blocker_transaction_id,
    requested.lock_type AS requested_lock_type,
    held.lock_type AS held_lock_type
FROM performance_schema.data_lock_waits AS w
JOIN performance_schema.data_locks AS requested
    ON requested.engine_lock_id = w.requesting_engine_lock_id
JOIN performance_schema.data_locks AS held
    ON held.engine_lock_id = w.blocking_engine_lock_id
LEFT JOIN performance_schema.threads AS waiter
    ON waiter.thread_id = w.requesting_thread_id
LEFT JOIN performance_schema.threads AS holder
    ON holder.thread_id = w.blocking_thread_id
ORDER BY blocked_pid, blocker_pid;
