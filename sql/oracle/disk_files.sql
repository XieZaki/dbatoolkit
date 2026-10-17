SELECT tablespace_name, file_name, bytes AS size_bytes,
 autoextensible, maxbytes
FROM dba_data_files ORDER BY bytes DESC
