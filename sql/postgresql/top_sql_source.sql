WITH extension AS (
    SELECT e.extversion AS extension_version, n.nspname AS schema_name
    FROM pg_catalog.pg_extension AS e
    JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'pg_stat_statements'
)
SELECT extension.extension_version,
       extension.schema_name IS NOT NULL AS extension_installed,
       extension.schema_name,
       'pg_stat_statements' = ANY (
           ARRAY(
               SELECT btrim(module_name)
               FROM unnest(string_to_array(
                   COALESCE(current_setting('shared_preload_libraries', true), ''), ','
               )) AS configured(module_name)
           )
       ) AS module_preloaded,
       current_setting('compute_query_id', true) AS compute_query_id,
       current_setting('pg_stat_statements.track', true) AS tracking_mode,
       pg_catalog.to_regclass('public.pg_stat_statements') IS NOT NULL AS statements_view_available,
       pg_catalog.to_regclass('public.pg_stat_statements_info') IS NOT NULL AS info_view_available
FROM (VALUES (1)) AS singleton(dummy)
LEFT JOIN extension ON TRUE;
