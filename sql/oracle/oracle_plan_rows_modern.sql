WITH target_cursor AS (
  SELECT q.address, q.hash_value, q.child_address, q.sql_id,
         q.child_number, q.plan_hash_value, q.con_id,
         COUNT(*) OVER () AS cursor_match_count
  FROM v$sql q
  WHERE q.sql_id = :sql_id
    AND q.child_number = :child_number
    AND q.con_id = :con_id
    AND q.address = HEXTORAW(:address_hex)
    AND q.hash_value = :hash_value
    AND q.child_address = HEXTORAW(:child_address_hex)
    AND q.plan_hash_value = :plan_hash_value
    AND TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) = :inst_id
), plan_rows AS (
  SELECT p.id, p.parent_id, p.depth, p.position, p.operation, p.options,
         p.object_owner, p.object_name, p.cardinality, p.cost, p.bytes,
         p.plan_hash_value, p.con_id, c.sql_id, c.child_number,
         c.cursor_match_count, TO_NUMBER(SYS_CONTEXT('USERENV', 'INSTANCE')) AS inst_id
  FROM target_cursor c
  JOIN v$sql_plan p
    ON p.address = c.address
   AND p.hash_value = c.hash_value
   AND p.child_number = c.child_number
   AND p.child_address = c.child_address
   AND p.plan_hash_value = c.plan_hash_value
   AND p.con_id = c.con_id
)
SELECT id, parent_id, depth, position, operation, options, object_owner, object_name,
       cardinality, cost, bytes, plan_hash_value, con_id, sql_id, child_number,
       cursor_match_count, inst_id
FROM (
  SELECT * FROM plan_rows ORDER BY id, position
)
WHERE ROWNUM <= 501
ORDER BY id, position
