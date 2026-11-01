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
    if _has_multiple_statements(sql):
        raise ValidationError("Only one SQL statement is accepted")
    tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9$]*", sql.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise ValidationError("Only SELECT/WITH scripts are accepted")
    if FORBIDDEN.intersection(tokens):
        raise ValidationError("SQL contains a forbidden token")
    if re.search(r"\bFOR\s+(?:UPDATE|SHARE)\b", sql, re.IGNORECASE):
        raise ValidationError("Locking SELECT is prohibited")


def _has_multiple_statements(sql: str) -> bool:
    """Reject content after a statement terminator, ignoring quoted text/comments."""
    index = 0
    terminated = False
    length = len(sql)
    while index < length:
        if sql.startswith("--", index) or sql[index] == "#":
            newline = sql.find("\n", index)
            if newline < 0:
                return False
            index = newline + 1
            continue
        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue

        # PostgreSQL dollar-quoted strings can contain semicolons.
        if sql[index] == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if match:
                delimiter = match.group(0)
                closing = sql.find(delimiter, index + len(delimiter))
                if closing < 0:
                    return False
                index = closing + len(delimiter)
                if terminated:
                    return True
                continue

        # Oracle alternative quoting (q'[...]', q'{...}', etc.).
        if sql[index:index + 2].lower() == "q'" and index + 2 < length:
            opener = sql[index + 2]
            closer = {"[": "]", "{": "}", "(": ")", "<": ">"}.get(opener, opener)
            closing = sql.find(closer + "'", index + 3)
            if closing < 0:
                return False
            index = closing + 2
            if terminated:
                return True
            continue

        character = sql[index]
        if character in {"'", '"', "`", "["}:
            closer = "]" if character == "[" else character
            index += 1
            while index < length:
                if sql[index] == closer:
                    # SQL-standard doubled quote escapes, and SQL Server ]] identifiers.
                    if index + 1 < length and sql[index + 1] == closer:
                        index += 2
                    else:
                        index += 1
                        break
                else:
                    index += 1
            if terminated:
                return True
            continue

        if character == ";":
            if terminated:
                return True
            terminated = True
            index += 1
            continue
        if terminated and not character.isspace():
            return True
        index += 1
    return False
