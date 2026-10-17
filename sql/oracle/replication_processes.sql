SELECT process, status, thread#, sequence#, block#, blocks, client_process
FROM SYS.V_$MANAGED_STANDBY
WHERE process IN ('MRP0', 'MRP', 'RFS', 'ARCH', 'FAL')
ORDER BY process, thread#, sequence#
