SELECT table_schema AS schema_name, table_name,
 engine, data_length AS data_bytes, index_length AS index_bytes,
 data_free AS engine_reported_data_free_bytes
FROM information_schema.tables
WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys')
ORDER BY COALESCE(data_length, 0) + COALESCE(index_length, 0) DESC
LIMIT 201
