SELECT @@performance_schema = 1 AS performance_schema_enabled,
       (SELECT ENABLED = 'YES'
          FROM performance_schema.setup_consumers
         WHERE NAME = 'statements_digest') AS statements_digest_enabled,
       (SELECT COUNT(*)
          FROM performance_schema.setup_instruments
         WHERE NAME LIKE 'statement/%') AS statement_instrument_count,
       (SELECT COALESCE(SUM(CASE WHEN ENABLED <> 'YES' THEN 1 ELSE 0 END), 0)
          FROM performance_schema.setup_instruments
         WHERE NAME LIKE 'statement/%') AS statement_instrument_disabled_count,
       (SELECT COALESCE(SUM(CASE WHEN TIMED <> 'YES' THEN 1 ELSE 0 END), 0)
          FROM performance_schema.setup_instruments
         WHERE NAME LIKE 'statement/%') AS statement_instrument_untimed_count,
       EXISTS (SELECT 1
                 FROM information_schema.tables
                WHERE TABLE_SCHEMA = 'performance_schema'
                  AND TABLE_NAME = 'events_statements_summary_by_digest') AS digest_table_available;
