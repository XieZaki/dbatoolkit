SELECT 'ROLE' AS kind, granted_role AS privilege_name FROM user_role_privs
UNION ALL
SELECT 'SYSTEM' AS kind, privilege AS privilege_name FROM session_privs
