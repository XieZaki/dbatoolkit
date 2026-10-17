"""Interpret the current MySQL account grants without treating broad SELECT as admin."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

HIGH_RISK_PRIVILEGES = frozenset({
    "ALL PRIVILEGES", "SUPER", "SYSTEM_USER", "CONNECTION_ADMIN", "CREATE USER",
    "GRANT OPTION", "SHUTDOWN", "FILE", "RELOAD", "SYSTEM_VARIABLES_ADMIN",
    "SESSION_VARIABLES_ADMIN", "ROLE_ADMIN", "BACKUP_ADMIN", "BINLOG_ADMIN",
    "CLONE_ADMIN", "ENCRYPTION_KEY_ADMIN", "PERSIST_RO_VARIABLES_ADMIN",
    "REPLICATION_SLAVE_ADMIN", "RESOURCE_GROUP_ADMIN", "SET_USER_ID",
    "XA_RECOVER_ADMIN", "APPLICATION_PASSWORD_ADMIN", "AUTHENTICATION_POLICY_ADMIN",
    "GROUP_REPLICATION_ADMIN", "TABLE_ENCRYPTION_ADMIN", "TELEMETRY_LOG_ADMIN",
})
WRITE_PRIVILEGES = frozenset({
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "EXECUTE", "TRIGGER",
    "INDEX", "CREATE ROUTINE", "ALTER ROUTINE", "EVENT", "CREATE TEMPORARY TABLES",
})


@dataclass(frozen=True)
class MySQLGrant:
    privileges: tuple[str, ...]
    scope: str
    grant_option: bool
    role_grant: bool
    dynamic_privilege: bool = False
    scope_type: str = "UNKNOWN"
    database: str | None = None
    object: str | None = None


def _scope_parts(scope: str) -> tuple[str, str | None, str | None]:
    """Parse GLOBAL, DATABASE, or TABLE scope, respecting quoted identifiers."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(scope):
        char = scope[index]
        if char == "`":
            if quoted and index + 1 < len(scope) and scope[index + 1] == "`":
                current.append("`")
                index += 2
                continue
            quoted = not quoted
        elif char == "." and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if quoted:
        return "UNKNOWN", None, None
    parts.append("".join(current))
    if len(parts) != 2 or not all(parts):
        return "UNKNOWN", None, None
    database, object_name = parts
    if database == "*" and object_name == "*":
        return "GLOBAL", "*", "*"
    if object_name == "*":
        return "DATABASE", database, "*"
    if database == "*":
        return "UNKNOWN", None, None
    return "TABLE", database, object_name


def parse_mysql_grant(statement: str) -> MySQLGrant:
    """Parse a single fixed-server SHOW GRANTS line into its privilege and scope."""
    normalized = " ".join(statement.strip().rstrip(";").split())
    if not normalized.upper().startswith("GRANT "):
        return MySQLGrant((), "unknown", False, False)
    role_match = re.match(r"(?is)^GRANT\s+(['`].+?['`])\s+TO\s+", normalized)
    if role_match and " ON " not in normalized.upper():
        return MySQLGrant((), "role", False, True)
    body = re.sub(r"(?is)^GRANT\s+", "", normalized, count=1)
    grant_option = bool(re.search(r"(?i)\bWITH\s+GRANT\s+OPTION\b", body))
    body = re.sub(r"(?i)\s+WITH\s+GRANT\s+OPTION\s*$", "", body)
    match = re.match(r"(?is)^(.*?)\s+ON\s+(.+?)\s+TO\s+", body)
    if match:
        raw_privileges, scope = match.group(1), match.group(2)
    else:
        # MySQL dynamic privileges may be granted without an ON scope.
        raw_privileges = re.split(r"(?i)\s+TO\s+", body, maxsplit=1)[0]
        scope = "global"
    privileges = tuple(dict.fromkeys(
        " ".join(part.strip(" `'\t").upper().split())
        for part in raw_privileges.split(",") if part.strip()
    ))
    raw_scope = scope.strip()
    scope_type, database, object_name = _scope_parts(raw_scope)
    return MySQLGrant(privileges, raw_scope, grant_option, False, match is None,
                      scope_type, database, object_name)


def _identifier_equal(left: str, right: str) -> bool:
    system_schemas = {"performance_schema", "information_schema", "mysql", "sys"}
    if left.casefold() in system_schemas and right.casefold() in system_schemas:
        return left.casefold() == right.casefold()
    return left == right


def scope_covers(granted_scope: str, required_scope: str) -> bool:
    """Whether a MySQL privilege scope covers a narrower requirement scope."""
    grant_type, grant_database, grant_object = _scope_parts(granted_scope)
    required_type, required_database, required_object = _scope_parts(required_scope)
    if "UNKNOWN" in {grant_type, required_type}:
        return False
    if grant_type == "GLOBAL":
        return True
    if required_type == "GLOBAL":
        return False
    if grant_database is None or required_database is None:
        return False
    if not _identifier_equal(grant_database, required_database):
        return False
    if grant_type == "DATABASE":
        return True
    return (required_type == "TABLE" and grant_object is not None
            and required_object is not None and _identifier_equal(grant_object, required_object))


def grant_satisfies_requirement(
    grant_statement: str, privilege: str, required_scope: str,
) -> bool:
    grant = parse_mysql_grant(grant_statement)
    if grant.role_grant or not any(
        granted == privilege or granted == "ALL PRIVILEGES" for granted in grant.privileges
    ):
        return False
    return scope_covers(grant.scope, required_scope)


def classify_mysql_grants(
    statements: Iterable[str], current_user: str, active_roles: str | None,
) -> tuple[dict[str, object], frozenset[str]]:
    """Return security evidence and Registry capability labels from explicit grants."""
    high_risk: list[dict[str, str]] = []
    write_grants: list[dict[str, str]] = []
    grant_evidence: set[str] = set()
    has_role = False
    unknown_dynamic: list[str] = []
    for statement in statements:
        grant = parse_mysql_grant(statement)
        if not grant.role_grant and grant.privileges:
            grant_evidence.add(statement)
        if grant.role_grant:
            has_role = True
            continue
        if grant.dynamic_privilege:
            for privilege in grant.privileges:
                if privilege not in HIGH_RISK_PRIVILEGES:
                    unknown_dynamic.append(privilege)
        for privilege in grant.privileges:
            if privilege in HIGH_RISK_PRIVILEGES:
                high_risk.append({"privilege": privilege, "scope": grant.scope,
                                  "source": "explicit", "principal": current_user})
            if privilege in WRITE_PRIVILEGES:
                write_grants.append({"privilege": privilege, "scope": grant.scope,
                                     "source": "explicit", "principal": current_user})

        if grant.grant_option:
            high_risk.append({"privilege": "GRANT OPTION", "scope": grant.scope,
                              "source": "explicit", "principal": current_user})

    active = (active_roles or "NONE").strip().upper()
    unknown_roles = has_role or active not in {"", "NONE"}
    classification = ("PRIVILEGED" if high_risk else "UNKNOWN"
                      if unknown_roles or unknown_dynamic else "NON_PRIVILEGED")
    return ({"classification": classification,
             "high_risk_privileges": high_risk,
             "write_privileges": write_grants,
             "active_roles": active_roles or "NONE",
             "role_grants_present": has_role,
             "unclassified_dynamic_privileges": unknown_dynamic,
             "current_user": current_user}, frozenset(grant_evidence))


def mysql_grant_risk(grant: MySQLGrant) -> str | None:
    if grant.grant_option or any(privilege in HIGH_RISK_PRIVILEGES for privilege in grant.privileges):
        return "PRIVILEGED"
    if any(privilege in WRITE_PRIVILEGES for privilege in grant.privileges):
        return "WRITE_CAPABILITY"
    return None


def split_current_user(current_user: str) -> tuple[str, str] | None:
    """Split the canonical CURRENT_USER() user@host identity."""
    if "@" not in current_user:
        return None
    username, host = current_user.split("@", 1)
    username = username.strip(" `'")
    host = host.strip(" `'")
    if not username or not host or "\x00" in username + host:
        return None
    return username, host
