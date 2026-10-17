SELECT @@server_uuid AS server_uuid,
       @@server_id AS server_id,
       @@global.read_only AS read_only,
       @@global.gtid_mode AS gtid_mode,
       @@global.log_bin AS log_bin
