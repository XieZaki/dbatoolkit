SELECT file_id, file_name, file_type, tablespace_name, engine,
 free_extents, total_extents, extent_size, initial_size, maximum_size,
 data_free AS engine_reported_data_free_bytes, status
FROM information_schema.files
WHERE engine = 'InnoDB'
ORDER BY initial_size DESC, file_name
LIMIT 200
