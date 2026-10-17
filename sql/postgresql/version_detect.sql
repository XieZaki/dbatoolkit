SELECT current_setting('server_version') AS raw_version,
       CAST(current_setting('server_version_num') AS integer) AS server_version_num;
