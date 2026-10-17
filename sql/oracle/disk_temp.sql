SELECT tablespace_name, tablespace_size AS size_bytes,
 allocated_space AS allocated_bytes, free_space AS free_bytes
FROM dba_temp_free_space
