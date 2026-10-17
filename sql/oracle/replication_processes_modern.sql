SELECT name, role, action, thread#, sequence#, block#, block_count, client_pid
FROM SYS.V_$DATAGUARD_PROCESS
WHERE name IN ('MRP0', 'MRP', 'RFS', 'ARCH', 'FAL')
ORDER BY name, thread#, sequence#
