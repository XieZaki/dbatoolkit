SELECT DISTINCT 'SERVER' AS scope, permission_name COLLATE DATABASE_DEFAULT AS permission_name,
    'EFFECTIVE' AS source_type, 'effective server permissions; source not exposed by SQL Server' AS source_name,
    CAST(NULL AS nvarchar(517)) AS object_name, CAST(NULL AS nvarchar(60)) AS object_type,
    CAST(NULL AS int) AS execute_as_principal_id
FROM sys.fn_my_permissions(NULL, 'SERVER')
UNION
SELECT DISTINCT 'DATABASE' AS scope, permission_name COLLATE DATABASE_DEFAULT AS permission_name,
    'EFFECTIVE' AS source_type, 'effective current-database permissions; source not exposed by SQL Server' AS source_name,
    CAST(NULL AS nvarchar(517)) AS object_name, CAST(NULL AS nvarchar(60)) AS object_type,
    CAST(NULL AS int) AS execute_as_principal_id
FROM sys.fn_my_permissions(NULL, 'DATABASE')
UNION
SELECT DISTINCT 'SCHEMA' AS scope, p.permission_name COLLATE DATABASE_DEFAULT AS permission_name,
    'EFFECTIVE' AS source_type, 'schema effective permissions; source not exposed by SQL Server' AS source_name,
    CAST(NULL AS nvarchar(517)) AS object_name, CAST(NULL AS nvarchar(60)) AS object_type,
    CAST(NULL AS int) AS execute_as_principal_id
FROM sys.schemas AS s
CROSS APPLY sys.fn_my_permissions(QUOTENAME(s.name), 'SCHEMA') AS p
WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
UNION
SELECT DISTINCT 'OBJECT' AS scope, p.permission_name COLLATE DATABASE_DEFAULT AS permission_name,
    'EFFECTIVE' AS source_type, 'object effective permissions; source not exposed by SQL Server' AS source_name,
    CAST(NULL AS nvarchar(517)) AS object_name, CAST(NULL AS nvarchar(60)) AS object_type,
    CAST(NULL AS int) AS execute_as_principal_id
FROM sys.objects AS o
JOIN sys.schemas AS s ON o.schema_id = s.schema_id
CROSS APPLY sys.fn_my_permissions(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT') AS p
WHERE o.is_ms_shipped = 0
UNION
SELECT DISTINCT 'SERVER' AS scope,
    CASE WHEN p.state = 'W' THEN 'DELEGATION' ELSE p.permission_name END
        COLLATE DATABASE_DEFAULT AS permission_name,
    CASE WHEN p.grantee_principal_id = SUSER_ID() THEN 'EXPLICIT' ELSE 'INHERITED' END AS source_type,
    sp.name COLLATE DATABASE_DEFAULT AS source_name,
    CAST(NULL AS nvarchar(517)) AS object_name, CAST(NULL AS nvarchar(60)) AS object_type,
    CAST(NULL AS int) AS execute_as_principal_id
FROM sys.server_permissions AS p
JOIN sys.server_principals AS sp ON p.grantee_principal_id = sp.principal_id
JOIN sys.login_token AS t ON p.grantee_principal_id = t.principal_id
WHERE p.state IN ('G', 'W')
UNION
SELECT DISTINCT CASE WHEN p.class = 0 THEN 'DATABASE' WHEN p.class = 1 THEN 'OBJECT'
    ELSE p.class_desc END AS scope,
    CASE WHEN p.state = 'W' THEN 'DELEGATION' ELSE p.permission_name END
        COLLATE DATABASE_DEFAULT AS permission_name,
    CASE WHEN p.grantee_principal_id = USER_ID() THEN 'EXPLICIT' ELSE 'INHERITED' END AS source_type,
    dp.name COLLATE DATABASE_DEFAULT AS source_name,
    CASE WHEN p.class = 1 THEN
        (QUOTENAME(OBJECT_SCHEMA_NAME(p.major_id)) + '.' + QUOTENAME(OBJECT_NAME(p.major_id)))
        WHEN p.class = 3 THEN QUOTENAME(SCHEMA_NAME(p.major_id)) ELSE NULL END
        COLLATE DATABASE_DEFAULT AS object_name,
    COALESCE(o.type_desc, p.class_desc) COLLATE DATABASE_DEFAULT AS object_type,
    sm.execute_as_principal_id
FROM sys.database_permissions AS p
JOIN sys.database_principals AS dp ON p.grantee_principal_id = dp.principal_id
JOIN sys.user_token AS t ON p.grantee_principal_id = t.principal_id
LEFT JOIN sys.objects AS o ON p.class = 1 AND p.major_id = o.object_id
LEFT JOIN sys.sql_modules AS sm ON sm.object_id = o.object_id
WHERE p.state IN ('G', 'W');
