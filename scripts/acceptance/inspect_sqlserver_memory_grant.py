r"""Print fixed read-only metadata checks for the user's DBeaver test session.

No credentials, connection, workload, or Memory Grant diagnosis are used here.
Run the printed SELECT statements manually on the confirmed local test server.
"""

from __future__ import annotations

import sys

METADATA_SQL = """-- Run in DBeaver on the confirmed AdventureWorks2022 test database.
-- All remaining queries return no data if the current database is different.
SELECT DB_NAME() AS current_database, DB_ID() AS database_id, @@SPID AS session_id,
       CAST(SERVERPROPERTY('ProductVersion') AS varchar(128)) AS product_version;

SELECT TOP (1) t.object_id, s.name AS schema_name, t.name AS table_name
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
WHERE DB_NAME() = N'AdventureWorks2022'
  AND s.name = N'Sales' AND t.name = N'SalesOrderDetail';

SELECT TOP (101) c.column_id, c.name AS column_name, ty.name AS type_name,
       c.max_length, c.is_nullable
FROM sys.columns AS c
JOIN sys.types AS ty ON c.user_type_id = ty.user_type_id
WHERE DB_NAME() = N'AdventureWorks2022'
  AND c.object_id = OBJECT_ID(N'Sales.SalesOrderDetail', N'U')
ORDER BY c.column_id;

SELECT TOP (101) i.index_id, i.name AS index_name, i.type_desc,
       i.is_disabled, i.is_hypothetical, i.has_filter, i.is_unique,
       i.is_primary_key, c.name AS column_name, ic.key_ordinal,
       ic.is_descending_key, ic.is_included_column
FROM sys.indexes AS i
JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE DB_NAME() = N'AdventureWorks2022'
  AND i.object_id = OBJECT_ID(N'Sales.SalesOrderDetail', N'U')
ORDER BY i.index_id, ic.index_column_id;

SELECT COUNT_BIG(*) AS partition_count, SUM(CONVERT(bigint, p.rows)) AS approximate_rows
FROM sys.partitions AS p
WHERE DB_NAME() = N'AdventureWorks2022'
  AND p.object_id = OBJECT_ID(N'Sales.SalesOrderDetail', N'U')
  AND p.index_id IN (0, 1);
"""


def main(argv: list[str]) -> int:
    if argv:
        print("No arguments are accepted.", file=sys.stderr)
        return 2
    print(METADATA_SQL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
