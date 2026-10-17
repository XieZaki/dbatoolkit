SELECT d.datname AS database_name, pg_database_size(d.oid) AS size_bytes,
 s.temp_bytes, s.temp_files, s.stats_reset
FROM pg_database d JOIN pg_stat_database s ON s.datid = d.oid
WHERE d.datname = current_database()
