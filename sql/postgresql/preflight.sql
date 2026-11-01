SELECT current_setting('server_version_num')::integer AS server_version_num,
    current_setting('default_transaction_read_only') AS default_transaction_read_only,
    current_setting('transaction_read_only') AS transaction_read_only,
    current_user AS monitor_user,
    r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolbypassrls
FROM pg_roles AS r WHERE r.rolname = current_user;
