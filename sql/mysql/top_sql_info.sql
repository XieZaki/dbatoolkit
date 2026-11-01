SELECT CAST((SELECT VARIABLE_VALUE
               FROM performance_schema.global_status
              WHERE VARIABLE_NAME = 'Performance_schema_digest_lost') AS UNSIGNED) AS digest_lost,
       EXISTS (SELECT 1
                 FROM performance_schema.events_statements_summary_by_digest
                WHERE DIGEST IS NULL) AS digest_null_bucket_present;
