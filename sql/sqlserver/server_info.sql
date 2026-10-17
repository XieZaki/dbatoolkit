SELECT
    CONVERT(nvarchar(4000), @@VERSION) AS version,
    CONVERT(nvarchar(128), SERVERPROPERTY('Edition')) AS edition,
    CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion')) AS product_version,
    CONVERT(nvarchar(128), SERVERPROPERTY('ProductLevel')) AS product_level,
    CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')) AS server_name,
    CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')) AS machine_name,
    CONVERT(nvarchar(128), SERVERPROPERTY('InstanceName')) AS instance_name,
    CONVERT(nvarchar(40), SYSDATETIMEOFFSET(), 127) AS [current_time];
