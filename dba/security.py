import re

from dba.inventory import ValidationError

FORBIDDEN = frozenset(
    "INSERT UPDATE DELETE MERGE ALTER DROP TRUNCATE CREATE GRANT REVOKE "
    "KILL SHUTDOWN RESTART FAILOVER SWITCHOVER PROMOTE EXEC EXECUTE CALL "
    "COPY INTO SET VACUUM ANALYZE DBCC BACKUP RESTORE LOAD OUTFILE DUMPFILE "
    "PG_TERMINATE_BACKEND PG_CANCEL_BACKEND PG_RELOAD_CONF "
    "PG_LOGICAL_EMIT_MESSAGE NEXTVAL SETVAL LO_IMPORT LO_EXPORT "
    "PG_WRITE_FILE PG_FILE_WRITE OPENROWSET OPENDATASOURCE".split()
)


def check_sql(sql: str) -> None:
    """Conservative extra layer, never a substitute for database permissions.

    Exact SHOW BINARY LOGS, SHOW GRANTS, SHOW REPLICA STATUS, SHOW SLAVE STATUS,
    and SQL Server DBCC SQLPERF(LOGSPACE) are the only approved non-SELECT forms.
    Each is permitted only through its individually reviewed Registry script.
    Keywords in literals/comments are also rejected intentionally. Arbitrary SQL
    functions can have side effects: mandatory human review and monitoring grants
    remain necessary before any future execution.
    """
    # Non-SELECT forms are a closed allowlist of fixed MySQL metadata statements.
    # Reject any suffix, comment, second statement or alternate SHOW form.
    if sql.strip().upper() in {
        "SHOW BINARY LOGS", "SHOW GRANTS", "SHOW REPLICA STATUS", "SHOW SLAVE STATUS",
        "DBCC SQLPERF(LOGSPACE)",
    }:
        return
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9$]*", sql.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise ValidationError("Only SELECT/WITH scripts are accepted")
    if FORBIDDEN.intersection(tokens):
        raise ValidationError("SQL contains a forbidden token")
    if re.search(r"\bFOR\s+(?:UPDATE|SHARE)\b", sql, re.IGNORECASE):
        raise ValidationError("Locking SELECT is prohibited")
