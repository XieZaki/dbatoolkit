SELECT n.nspname AS schema_name, c.relname AS relation_name,
 c.relkind AS relation_kind, pg_relation_size(c.oid) AS relation_bytes,
 pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','m','p')
  AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY pg_total_relation_size(c.oid) DESC, n.nspname, c.relname
LIMIT 201
