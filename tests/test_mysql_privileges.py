from __future__ import annotations

import pytest

from dba.mysql_privileges import classify_mysql_grants, parse_mysql_grant

ACCOUNT = "dba_monitor@%"


def classify(*grants: str, roles: str = "NONE") -> dict[str, object]:
    return classify_mysql_grants(grants, ACCOUNT, roles)


@pytest.mark.regression
def test_read_only_grants_are_not_misclassified_as_admin() -> None:
    result = classify(
        "GRANT SELECT ON *.* TO `dba_monitor`@`%`",
        "GRANT PROCESS ON *.* TO `dba_monitor`@`%`",
    )
    assert result["classification"] == "NON_PRIVILEGED"
    assert result["write_privileges"] == []


@pytest.mark.parametrize("grant", [
    "GRANT ALL PRIVILEGES ON *.* TO `dba_monitor`@`%`",
    "GRANT SUPER ON *.* TO `dba_monitor`@`%`",
    "GRANT SYSTEM_USER ON *.* TO `dba_monitor`@`%`",
    "GRANT CONNECTION_ADMIN ON *.* TO `dba_monitor`@`%`",
    "GRANT CREATE USER ON *.* TO `dba_monitor`@`%`",
    "GRANT SELECT ON *.* TO `dba_monitor`@`%` WITH GRANT OPTION",
])
def test_high_risk_privileges_are_explicitly_classified(grant: str) -> None:
    result = classify(grant)
    assert result["classification"] == "PRIVILEGED"
    assert result["high_risk_privileges"]


def test_write_capability_is_reported_separately_from_admin_classification() -> None:
    result = classify("GRANT INSERT ON `app`.* TO `dba_monitor`@`%`")
    assert result["classification"] == "NON_PRIVILEGED"
    writes = result["write_privileges"]
    assert isinstance(writes, list)
    assert writes[0]["privilege"] == "INSERT"
    assert writes[0]["scope"] == "`app`.*"


def test_role_grants_and_unknown_active_roles_fail_closed() -> None:
    result = classify("GRANT `monitor_role` TO `dba_monitor`@`%`", roles="monitor_role")
    assert result["classification"] == "UNKNOWN"
    assert result["role_grants_present"] is True


def test_unknown_dynamic_privilege_is_not_assumed_safe() -> None:
    result = classify("GRANT FUTURE_DYNAMIC_PRIVILEGE TO `dba_monitor`@`%`")
    assert result["classification"] == "UNKNOWN"
    assert result["unclassified_dynamic_privileges"] == ["FUTURE_DYNAMIC_PRIVILEGE"]


def test_grant_parser_retains_scope_and_grant_option_for_account_safety() -> None:
    grant = parse_mysql_grant("GRANT SELECT ON *.* TO `dba_monitor`@`%` WITH GRANT OPTION")
    assert grant.privileges == ("SELECT",)
    assert grant.scope == "*.*"
    assert grant.grant_option is True
