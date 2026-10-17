SELECT DB_NAME() AS database_name, name AS file_name, physical_name AS physical_path,
 type_desc AS file_type, CAST(size AS bigint) * 8192 AS size_bytes,
 CASE WHEN type = 0 THEN CAST(FILEPROPERTY(name, 'SpaceUsed') AS bigint) * 8192 ELSE NULL END AS used_bytes,
 CASE WHEN type = 0 THEN (CAST(size AS bigint) - CAST(FILEPROPERTY(name, 'SpaceUsed') AS bigint)) * 8192 ELSE NULL END AS free_bytes,
 max_size, growth, is_percent_growth
FROM sys.database_files ORDER BY size DESC
