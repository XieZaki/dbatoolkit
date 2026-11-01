SELECT q.sql_id, q.child_number, q.plan_hash_value,
       RAWTOHEX(q.address) AS address_hex, q.hash_value,
       RAWTOHEX(q.child_address) AS child_address_hex,
       TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id,
       COUNT(*) OVER () AS cursor_match_count
FROM v$sql q
WHERE q.sql_id = :sql_id
  AND q.child_number = :child_number
  AND TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) = :inst_id
ORDER BY q.address, q.child_address
