SELECT name AS file_name, type_desc AS file_type,
 CAST(size AS bigint) * 8192 AS size_bytes, max_size, growth, is_percent_growth
FROM tempdb.sys.database_files ORDER BY size DESC
