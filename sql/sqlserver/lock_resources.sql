SELECT l.request_session_id AS session_id, l.request_request_id AS request_id,
    l.request_exec_context_id AS exec_context_id, l.request_owner_type,
    l.request_mode, l.request_status, l.request_type,
    l.resource_type, l.resource_subtype, l.resource_database_id,
    DB_NAME(l.resource_database_id) AS resource_database_name,
    DB_ID() AS connection_database_id,
    l.resource_associated_entity_id,
    RTRIM(l.resource_description) AS resource_description,
    l.resource_lock_partition,
    target.object_id AS resolved_object_id,
    OBJECT_SCHEMA_NAME(target.object_id, l.resource_database_id) AS schema_name,
    OBJECT_NAME(target.object_id, l.resource_database_id) AS object_name,
    o.type_desc AS object_type_desc, o.is_ms_shipped,
    target.index_id, i.name AS index_name, i.type_desc AS index_type_desc,
    target.partition_number, au.type AS allocation_unit_type
FROM sys.dm_tran_locks AS l
-- Catalog views are database-scoped: HoBT and allocation-unit IDs are resolved only
-- when the lock belongs to the connection database. Other databases are never switched to.
LEFT JOIN sys.partitions AS hp
    ON l.resource_database_id = DB_ID()
    AND l.resource_type IN ('KEY', 'PAGE', 'RID', 'HOBT', 'OIB')
    AND l.resource_associated_entity_id <> 0
    AND hp.hobt_id = l.resource_associated_entity_id
LEFT JOIN sys.allocation_units AS au
    ON l.resource_database_id = DB_ID()
    AND l.resource_type = 'ALLOCATION_UNIT'
    AND l.resource_associated_entity_id <> 0
    AND au.allocation_unit_id = l.resource_associated_entity_id
LEFT JOIN sys.partitions AS ap
    ON (au.type IN (1, 3) AND ap.hobt_id = au.container_id)
    OR (au.type = 2 AND ap.partition_id = au.container_id)
CROSS APPLY (
    SELECT CASE WHEN l.resource_type = 'OBJECT'
                THEN CONVERT(int, l.resource_associated_entity_id)
                ELSE COALESCE(hp.object_id, ap.object_id) END AS object_id,
        COALESCE(hp.index_id, ap.index_id) AS index_id,
        COALESCE(hp.partition_number, ap.partition_number) AS partition_number
) AS target
LEFT JOIN sys.objects AS o
    ON l.resource_database_id = DB_ID() AND o.object_id = target.object_id
LEFT JOIN sys.indexes AS i
    ON l.resource_database_id = DB_ID() AND i.object_id = target.object_id
    AND i.index_id = target.index_id
WHERE l.request_session_id <> @@SPID
    AND EXISTS (
        SELECT 1 FROM sys.dm_tran_locks AS pending
        WHERE pending.request_status IN
                ('WAIT', 'CONVERT', 'LOW_PRIORITY_WAIT', 'LOW_PRIORITY_CONVERT', 'ABORT_BLOCKERS')
            AND pending.resource_type = l.resource_type
            AND pending.resource_database_id = l.resource_database_id
            AND pending.resource_associated_entity_id = l.resource_associated_entity_id
            AND pending.resource_description = l.resource_description
            AND pending.resource_lock_partition = l.resource_lock_partition
    )
ORDER BY l.resource_database_id, l.resource_type, l.resource_associated_entity_id,
    l.resource_description, l.resource_lock_partition, l.request_status, l.request_session_id;
