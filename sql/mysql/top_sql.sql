SELECT ranked.schema_name, ranked.digest, ranked.ranking_value
FROM (
    SELECT SCHEMA_NAME AS schema_name,
           DIGEST AS digest,
           CASE %s
               WHEN 'sum_timer_wait' THEN SUM_TIMER_WAIT
               WHEN 'avg_timer_wait' THEN AVG_TIMER_WAIT
               WHEN 'count_star' THEN COUNT_STAR
               WHEN 'sum_rows_examined' THEN SUM_ROWS_EXAMINED
               WHEN 'sum_rows_sent' THEN SUM_ROWS_SENT
               WHEN 'sum_created_tmp_disk_tables' THEN SUM_CREATED_TMP_DISK_TABLES
               WHEN 'sum_no_index_used' THEN SUM_NO_INDEX_USED
           END AS ranking_value
    FROM performance_schema.events_statements_summary_by_digest
    WHERE DIGEST IS NOT NULL
) AS ranked
WHERE ranked.ranking_value IS NOT NULL
ORDER BY ranked.ranking_value DESC,
         ranked.schema_name ASC,
         ranked.digest ASC
LIMIT %s;
