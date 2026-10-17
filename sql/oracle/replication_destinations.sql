SELECT dest_id,
       status,
       type,
       database_mode,
       recovery_mode,
       destination,
       error,
       synchronization_status,
       gap_status,
       archived_thread#,
       archived_seq#,
       applied_thread#,
       applied_seq#
FROM SYS.V_$ARCHIVE_DEST_STATUS
WHERE dest_id > 1
  AND destination IS NOT NULL
ORDER BY dest_id
