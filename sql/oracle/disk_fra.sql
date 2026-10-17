SELECT name AS recovery_dest, space_limit, space_used,
 space_reclaimable, number_of_files FROM v$recovery_file_dest
