SELECT (SELECT COUNT(*) FROM v$log_history
        WHERE first_time >= SYSDATE - 1) AS redo_switches_24h,
       (SELECT COUNT(*) FROM v$archived_log
        WHERE completion_time >= SYSDATE - 1) AS archived_count_24h,
       (SELECT SUM(blocks * block_size) FROM v$archived_log
        WHERE completion_time >= SYSDATE - 1) AS archived_bytes_24h
FROM dual
