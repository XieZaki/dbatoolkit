SELECT dest_id, status, destination
FROM v$archive_dest_status WHERE status <> 'INACTIVE'
