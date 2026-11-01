SELECT TOP (101)
    mg.session_id,
    mg.request_id,
    mg.request_time,
    mg.grant_time,
    mg.requested_memory_kb,
    mg.required_memory_kb,
    mg.granted_memory_kb,
    mg.used_memory_kb,
    mg.max_used_memory_kb,
    mg.wait_time_ms,
    mg.queue_id,
    mg.wait_order,
    mg.resource_semaphore_id,
    CONVERT(varchar(130), mg.sql_handle, 1) AS grant_sql_handle,
    CONVERT(varchar(130), mg.plan_handle, 1) AS grant_plan_handle,
    CASE WHEN r.session_id IS NULL THEN 0 ELSE 1 END AS request_found,
    r.session_id AS request_session_id,
    r.request_id AS request_request_id,
    r.start_time AS request_start_time,
    r.status AS request_status,
    r.wait_type,
    r.database_id,
    CONVERT(varchar(130), r.sql_handle, 1) AS request_sql_handle,
    CONVERT(varchar(130), r.plan_handle, 1) AS request_plan_handle
FROM sys.dm_exec_query_memory_grants AS mg
LEFT JOIN sys.dm_exec_requests AS r
    ON r.session_id = mg.session_id
   AND r.request_id = mg.request_id
WHERE mg.grant_time IS NULL
  AND mg.session_id <> @@SPID;
