SELECT m.tablespace_name, m.used_space * t.block_size AS used_bytes,
 m.tablespace_size * t.block_size AS allocated_bytes,
 m.used_percent, t.contents
FROM dba_tablespace_usage_metrics m
JOIN dba_tablespaces t ON t.tablespace_name = m.tablespace_name
ORDER BY m.used_percent DESC
