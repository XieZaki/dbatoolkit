SELECT table_schema AS schema_name, SUM(data_length) AS data_bytes,
 SUM(index_length) AS index_bytes, COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
GROUP BY table_schema ORDER BY SUM(data_length + index_length) DESC
