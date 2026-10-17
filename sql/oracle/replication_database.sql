SELECT dbid,
       name,
       db_unique_name,
       database_role,
       open_mode,
       protection_mode,
       protection_level,
       switchover_status
FROM SYS.V_$DATABASE
