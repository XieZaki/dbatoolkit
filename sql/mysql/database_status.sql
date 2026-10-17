SELECT s.schema_name AS schema_name,
    COALESCE(SUM(CASE WHEN t.table_type = 'BASE TABLE'
                      THEN COALESCE(t.data_length, 0) + COALESCE(t.index_length, 0)
                      ELSE 0 END), 0) AS size_bytes,
    COUNT(CASE WHEN t.table_type = 'BASE TABLE' THEN 1 END) AS table_count
FROM information_schema.schemata AS s
LEFT JOIN information_schema.tables AS t ON t.table_schema = s.schema_name
GROUP BY s.schema_name
ORDER BY s.schema_name;
