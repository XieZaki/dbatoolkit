from __future__ import annotations

import pytest

from dba.core import DatabaseType
from dba.mysql_privileges import (
    classify_mysql_grants,
    grant_satisfies_requirement,
    parse_mysql_grant,
    scope_covers,
)
from dba.permissions import PermissionStatus, assess_permissions, requirements_for

ACCOUNT = "dba_monitor@%"


def classify(*grants: str, roles: str = "NONE"):
    return classify_mysql_grants(grants, ACCOUNT, roles)


def test_global_select_is_non_privileged_and_maps_only_read_capability() -> None:
    security, observed = classify("GRANT SELECT ON *.* TO `dba_monitor`@`%`")
    assert security["classification"] == "NON_PRIVILEGED"
    assert security["write_privileges"] == []
    assert observed == {"GRANT SELECT ON *.* TO `dba_monitor`@`%`"}
    assessment = assess_permissions(requirements_for(DatabaseType.MYSQL, "slow"), observed)
    assert assessment["status"] == PermissionStatus.MISSING
    assert assessment["missing_required"] == ("PROCESS",)
    assert assessment["available"] == ("SELECT performance_schema",)


def test_registry_scope_is_evaluated_from_grant_evidence_not_capability_label() -> None:
    assessment = assess_permissions(
        requirements_for(DatabaseType.MYSQL, "slow"),
        {"PROCESS", "SELECT performance_schema"},
    )
    assert assessment["status"] == PermissionStatus.MISSING
    assert assessment["missing_required"] == ("PROCESS", "SELECT performance_schema")


@pytest.mark.parametrize("grant", [
    "GRANT PROCESS ON *.* TO `dba_monitor`@`%`",
    "GRANT REPLICATION CLIENT ON *.* TO `dba_monitor`@`%`",
])
def test_monitoring_dynamic_privileges_are_not_privileged(grant: str) -> None:
    security, _ = classify(grant)
    assert security["classification"] == "NON_PRIVILEGED"


@pytest.mark.parametrize("grant", [
    "GRANT ALL PRIVILEGES ON *.* TO `dba_monitor`@`%`",
    "GRANT SUPER ON *.* TO `dba_monitor`@`%`",
    "GRANT SYSTEM_USER ON *.* TO `dba_monitor`@`%`",
    "GRANT CONNECTION_ADMIN ON *.* TO `dba_monitor`@`%`",
    "GRANT CREATE USER ON *.* TO `dba_monitor`@`%`",
    "GRANT SELECT ON *.* TO `dba_monitor`@`%` WITH GRANT OPTION",
])
def test_high_risk_privileges_are_explicitly_classified(grant: str) -> None:
    security, _ = classify(grant)
    assert security["classification"] == "PRIVILEGED"
    assert security["high_risk_privileges"]


def test_all_privileges_still_satisfies_scoped_readiness_but_is_privileged() -> None:
    grant = "GRANT ALL PRIVILEGES ON *.* TO `dba_monitor`@`%`"
    security, observed = classify(grant)
    assert security["classification"] == "PRIVILEGED"
    assessment = assess_permissions(requirements_for(DatabaseType.MYSQL, "slow"), observed)
    assert assessment["status"] == PermissionStatus.READY


def test_grant_option_satisfies_its_read_scope_but_remains_privileged() -> None:
    grant = "GRANT SELECT ON *.* TO `dba_monitor`@`%` WITH GRANT OPTION"
    security, observed = classify(grant)
    assert security["classification"] == "PRIVILEGED"
    assessment = assess_permissions(requirements_for(DatabaseType.MYSQL, "slow"), observed)
    assert assessment["status"] == PermissionStatus.MISSING
    assert assessment["missing_required"] == ("PROCESS",)
    assert assessment["available"] == ("SELECT performance_schema",)


def test_write_capability_is_separate_from_privileged_classification() -> None:
    security, _ = classify("GRANT INSERT ON `app`.* TO `dba_monitor`@`%`")
    assert security["classification"] == "NON_PRIVILEGED"
    assert security["write_privileges"][0]["privilege"] == "INSERT"
    assert security["write_privileges"][0]["scope"] == "`app`.*"


def test_role_grants_are_unknown_until_effective_privileges_can_be_verified() -> None:
    security, _ = classify("GRANT `monitor_role`@`%` TO `dba_monitor`@`%`", roles="monitor_role")
    assert security["classification"] == "UNKNOWN"
    assert security["role_grants_present"]


def test_unknown_dynamic_privilege_is_unknown_not_assumed_safe() -> None:
    security, _ = classify("GRANT FUTURE_DYNAMIC_PRIVILEGE TO `dba_monitor`@`%`")
    assert security["classification"] == "UNKNOWN"
    assert security["unclassified_dynamic_privileges"] == ["FUTURE_DYNAMIC_PRIVILEGE"]


def test_grant_parser_retains_scope_and_grant_option() -> None:
    grant = parse_mysql_grant("GRANT SELECT ON *.* TO `dba_monitor`@`%` WITH GRANT OPTION")
    assert grant.privileges == ("SELECT",)
    assert grant.scope == "*.*"
    assert grant.grant_option


@pytest.mark.parametrize("required", [
    "performance_schema.*", "appdb.*", "appdb.orders",
])
def test_global_scope_covers_database_and_table_scopes(required: str) -> None:
    assert scope_covers("*.*", required)


def test_database_scope_covers_table_but_not_other_database_or_global() -> None:
    assert scope_covers("performance_schema.*", "performance_schema.threads")
    assert not scope_covers("performance_schema.*", "appdb.orders")
    assert not scope_covers("performance_schema.*", "*.*")


def test_table_scope_does_not_cover_database_scope() -> None:
    assert scope_covers("performance_schema.threads", "performance_schema.threads")
    assert not scope_covers("performance_schema.threads", "performance_schema.*")


def test_quoted_identifiers_and_system_schema_case_normalization() -> None:
    assert grant_satisfies_requirement(
        "GRANT SELECT ON `performance_schema`.* TO `dba_monitor`@`%`",
        "SELECT", "PERFORMANCE_SCHEMA.threads",
    )
    assert scope_covers("`app-db`.*", "`app-db`.table")
    assert not scope_covers("`app-db`.*", "appdb.table")


def test_scope_implication_requires_same_privilege_and_does_not_union_databases() -> None:
    assert not grant_satisfies_requirement("GRANT SELECT ON *.* TO u@h", "PROCESS", "*.*")
    grants = {
        "GRANT SELECT ON appdb.* TO u@h",
        "GRANT SELECT ON performance_schema.* TO u@h",
    }
    requirement = requirements_for(DatabaseType.MYSQL, "slow")
    assessment = assess_permissions(requirement, grants)
    assert "SELECT performance_schema" in assessment["available"]
    assert "PROCESS" in assessment["missing_required"]


def test_database_scope_does_not_satisfy_global_requirement() -> None:
    from dba.permissions import FeaturePermissions

    requirement = FeaturePermissions(
        ("SELECT globally",),
        scope_requirements=(("SELECT globally", "SELECT", "*.*"),),
    )
    result = assess_permissions(requirement, {"GRANT SELECT ON performance_schema.* TO u@h"})
    assert result["missing_required"] == ("SELECT globally",)
