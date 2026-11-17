SELECT pg_catalog.pg_backend_pid() AS backend_pid,
    pg_catalog.current_database() AS database_name,
    pg_catalog.current_setting('server_version_num')::integer AS version_num,
    pg_catalog.current_setting('statement_timeout') AS statement_timeout,
    backend_start,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles AS r
        WHERE pg_catalog.pg_has_role(r.oid, 'MEMBER')
            AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
                OR r.rolname IN ('pg_write_all_data', 'pg_read_all_data', 'pg_signal_backend',
                    'pg_execute_server_program', 'pg_read_server_files', 'pg_write_server_files'))
    ) AS elevated_membership
FROM pg_catalog.pg_stat_activity
WHERE pid = pg_catalog.pg_backend_pid();
