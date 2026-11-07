import re

from dba.core import DatabaseType
from dba.inventory import ValidationError

FORBIDDEN = frozenset(
    "INSERT UPDATE DELETE MERGE ALTER DROP TRUNCATE CREATE GRANT REVOKE "
    "KILL SHUTDOWN RESTART FAILOVER SWITCHOVER PROMOTE EXEC EXECUTE CALL "
    "COPY INTO SET VACUUM ANALYZE DBCC BACKUP RESTORE LOAD OUTFILE DUMPFILE "
    "PG_TERMINATE_BACKEND PG_CANCEL_BACKEND PG_RELOAD_CONF "
    "SET_CONFIG PG_NOTIFY "
    "PG_LOGICAL_EMIT_MESSAGE NEXTVAL SETVAL LO_IMPORT LO_EXPORT "
    "PG_WRITE_FILE PG_FILE_WRITE OPENROWSET OPENDATASOURCE".split()
)

_SQL_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")
_UNSAFE_READONLY_SEQUENCES = (
    ("FOR", "SHARE"),
    ("FOR", "KEY", "SHARE"),
    ("LOCK", "IN", "SHARE", "MODE"),
    ("NEXT", "VALUE", "FOR"),
)
_SQLSERVER_LOCK_HINTS = frozenset({
    "HOLDLOCK", "NOWAIT", "PAGLOCK", "READCOMMITTED", "READCOMMITTEDLOCK",
    "READPAST", "REPEATABLEREAD", "ROWLOCK", "SERIALIZABLE", "TABLOCK",
    "TABLOCKX", "UPDLOCK", "XLOCK",
})
_SESSION_LOCK_FUNCTIONS = frozenset({
    "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS",
    "PG_ADVISORY_LOCK", "PG_ADVISORY_LOCK_SHARED", "PG_ADVISORY_UNLOCK",
    "PG_ADVISORY_UNLOCK_SHARED", "PG_ADVISORY_UNLOCK_ALL",
    "PG_ADVISORY_XACT_LOCK", "PG_ADVISORY_XACT_LOCK_SHARED",
    "PG_TRY_ADVISORY_LOCK", "PG_TRY_ADVISORY_LOCK_SHARED",
    "PG_TRY_ADVISORY_XACT_LOCK", "PG_TRY_ADVISORY_XACT_LOCK_SHARED",
})


def check_sql(sql: str, database_type: DatabaseType | None = None) -> None:
    """Conservative extra layer, never a substitute for database permissions.

    Exact SHOW BINARY LOGS, SHOW GRANTS, SHOW REPLICA STATUS, SHOW SLAVE STATUS,
    and SQL Server DBCC SQLPERF(LOGSPACE) are the only approved non-SELECT forms.
    Each is permitted only through its individually reviewed Registry script.
    Keywords in literals/comments are also rejected intentionally. Known locking
    reads, session-lock functions, and sequence allocation are rejected as well.
    Arbitrary SQL functions can have side effects: mandatory human review and
    monitoring grants remain necessary before any future execution.
    """
    # Non-SELECT forms are a closed allowlist of fixed MySQL metadata statements.
    # Reject any suffix, comment, second statement or alternate SHOW form.
    if sql.strip().upper() in {
        "SHOW BINARY LOGS", "SHOW GRANTS", "SHOW REPLICA STATUS", "SHOW SLAVE STATUS",
        "DBCC SQLPERF(LOGSPACE)",
    }:
        return
    if _has_multiple_statements(sql, database_type):
        raise ValidationError("Only one SQL statement is accepted")
    tokens = [token.upper() for token in _SQL_TOKEN.findall(sql)]
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise ValidationError("Only SELECT/WITH scripts are accepted")
    if (FORBIDDEN.intersection(tokens) or _SQLSERVER_LOCK_HINTS.intersection(tokens)
            or _SESSION_LOCK_FUNCTIONS.intersection(tokens)
            or any(_has_keyword_sequence(sql, words, database_type)
                   for words in _UNSAFE_READONLY_SEQUENCES)):
        raise ValidationError("SQL contains a forbidden token")
    if re.search(r"\bFOR\s+(?:UPDATE|SHARE)\b", sql, re.IGNORECASE):
        raise ValidationError("Locking SELECT is prohibited")


def _has_multiple_statements(sql: str, database_type: DatabaseType | None = None) -> bool:
    """Reject content after a statement terminator, ignoring quoted text/comments."""
    index = 0
    terminated = False
    length = len(sql)
    while index < length:
        if _is_line_comment(sql, index, database_type):
            line_end = _line_comment_end(sql, index)
            if line_end < 0:
                return False
            index = line_end + 1
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


def _has_keyword_sequence(
    sql: str, words: tuple[str, ...], database_type: DatabaseType | None = None,
) -> bool:
    """Match dangerous multi-keyword syntax when comments separate its words."""
    first = re.compile(rf"\b{re.escape(words[0])}\b", re.IGNORECASE)
    for match in first.finditer(sql):
        index = match.end()
        for word in words[1:]:
            while index < len(sql):
                if sql[index].isspace():
                    index += 1
                elif _is_line_comment(sql, index, database_type):
                    line_end = _line_comment_end(sql, index)
                    if line_end < 0:
                        index = len(sql)
                        break
                    index = line_end + 1
                elif sql.startswith("/*", index):
                    depth = 1
                    index += 2
                    while index < len(sql) and depth:
                        if sql.startswith("/*", index):
                            depth += 1
                            index += 2
                        elif sql.startswith("*/", index):
                            depth -= 1
                            index += 2
                        else:
                            index += 1
                    if depth:
                        index = len(sql)
                        break
                else:
                    break
            next_word = re.match(rf"{re.escape(word)}\b", sql[index:], re.IGNORECASE)
            if next_word is None:
                break
            index += next_word.end()
        else:
            return True
    return False


def _is_line_comment(sql: str, index: int, database_type: DatabaseType | None) -> bool:
    if sql.startswith("--", index):
        if database_type != DatabaseType.MYSQL:
            return True
        following_index = index + 2
        if following_index >= len(sql):
            return False
        following = sql[following_index]
        return following.isspace() or ord(following) < 32 or ord(following) == 127
    return sql[index] == "#" and database_type in {None, DatabaseType.MYSQL}


def _line_comment_end(sql: str, index: int) -> int:
    line_ends = [position for marker in ("\n", "\r")
                 if (position := sql.find(marker, index)) >= 0]
    return min(line_ends) if line_ends else -1
