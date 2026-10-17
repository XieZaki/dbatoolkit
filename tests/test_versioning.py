from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from dba.cli import main
from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.inventory import Inventory, ValidationError
from dba.permissions import VERSION_DETECTION_PRIVILEGES
from dba.registry import SqlScript, SqlScriptRegistry
from dba.versioning import (
    CANONICAL_VERSION_FAMILIES,
    DEFAULT_VERSION_DETECTOR,
    PROFILE_BINDINGS,
    PROFILE_RESOLVER,
    DataVisibility,
    DetectedDatabaseVersion,
    DiagnosticProfileResolver,
    ProfileResolutionError,
    RegistryVersionDetector,
    RuntimeConfigState,
    SupportState,
    VersionStatus,
    build_version_profile,
    detect_database_version,
    normalize_version,
    version_status,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("db_type", "raw", "family", "variant"), [
    (DatabaseType.SQLSERVER, "10.50.6000.34", "2008r2", None),
    (DatabaseType.SQLSERVER, "10.0.6000.34", None, None),
    (DatabaseType.SQLSERVER, "11.0.7001.0", "2012", None),
    (DatabaseType.SQLSERVER, "15.0.2000.5", "2019", None),
    (DatabaseType.SQLSERVER, "16.0.4305.5", "2022", None),
    (DatabaseType.SQLSERVER, "17.0.1.0", "2025", None),
    (DatabaseType.SQLSERVER, "18.0.1.0", None, None),
    (DatabaseType.MYSQL, "5.7.44-log", "5.7", None),
    (DatabaseType.MYSQL, "8.0.21", "8.0", "pre_8022"),
    (DatabaseType.MYSQL, "8.0.22", "8.0", "8022_plus"),
    (DatabaseType.MYSQL, "8.4.1", "8.4", None),
    (DatabaseType.MYSQL, "9.0.0", None, None),
    (DatabaseType.MYSQL, "10.11.2-MariaDB", None, None),
    (DatabaseType.POSTGRESQL, "13.12 (Debian)", "13", None),
    (DatabaseType.POSTGRESQL, "18.0", "18", None),
    (DatabaseType.POSTGRESQL, "19.0", None, None),
    (DatabaseType.ORACLE, "Oracle Database 12c Release 12.1.0.2.0", "12c", None),
    (DatabaseType.ORACLE, "Oracle Database 12c Release 12.2.0.1.0", "12c", None),
    (DatabaseType.ORACLE, "Oracle Database 19c Release 19.0.0.0.0", "19c", None),
    (DatabaseType.ORACLE, "Oracle Database 23ai Release 23.0.0.0.0", "23ai", None),
    (DatabaseType.ORACLE, "Oracle Database 26ai Release 23.26.0.0.0", "26ai", None),
    (DatabaseType.ORACLE, "Oracle Database 24x Release 24.0.0.0.0", None, None),
    (DatabaseType.ORACLE, "not a version banner", None, None),
])
def test_normalization_preserves_raw_and_fails_closed(
    db_type: DatabaseType, raw: str, family: str | None, variant: str | None,
) -> None:
    detected = normalize_version(db_type, raw)
    assert detected.raw_version == raw
    assert detected.normalized_family == family
    assert detected.variant_key == variant


def test_postgresql_version_number_precedes_banner_parsing() -> None:
    detected = normalize_version(DatabaseType.POSTGRESQL, "PostgreSQL 16.4", server_version_num=160004)
    assert detected.normalized_family == "16"
    assert normalize_version(DatabaseType.POSTGRESQL, "19.0", server_version_num=190000).normalized_family is None


@pytest.mark.parametrize(("declared", "detected", "expected"), [
    ("2022", DetectedDatabaseVersion("16.0.1", "2022"), VersionStatus.VERIFIED),
    (None, DetectedDatabaseVersion("16.0.1", "2022"), VersionStatus.AUTO_DETECTED),
    ("2022", None, VersionStatus.UNVERIFIED),
    ("2019", DetectedDatabaseVersion("16.0.1", "2022"), VersionStatus.MISMATCH),
    (None, None, VersionStatus.UNKNOWN),
    ("2022", DetectedDatabaseVersion("future", None), VersionStatus.UNVERIFIED),
])
def test_version_status_semantics(
    declared: str | None, detected: DetectedDatabaseVersion | None, expected: VersionStatus,
) -> None:
    assert version_status(declared, detected) == expected


class FakeExecutor:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.script: SqlScript | None = None

    def execute_readonly(self, script: SqlScript) -> CollectorResult:
        self.script = script
        now = datetime.now(timezone.utc)
        return CollectorResult("version-test", script.metadata.name, now, now, 0.1,
                               (self.row,), 1, False, True)


@pytest.mark.integration
@pytest.mark.parametrize(("db_type", "row", "family"), [
    (DatabaseType.SQLSERVER, {"raw_version": "16.0.4305.5"}, "2022"),
    (DatabaseType.MYSQL, {"raw_version": "8.0.21"}, "8.0"),
    (DatabaseType.POSTGRESQL, {"raw_version": "16.4", "server_version_num": 160004}, "16"),
    (DatabaseType.ORACLE,
     {"raw_version": "Oracle Database 23ai Enterprise Edition Release 23.0.0.0.0"}, "23ai"),
])
def test_registry_detector_normalizer_status_resolver_integration(
    db_type: DatabaseType, row: dict[str, Any], family: str,
) -> None:
    registry = SqlScriptRegistry(ROOT / "sql")
    executor = FakeExecutor(row)
    result = DEFAULT_VERSION_DETECTOR.detect(db_type, registry, executor)
    assert result.detected is not None
    assert result.detected.normalized_family == family
    assert executor.script is not None and executor.script.metadata.reviewed
    profile = PROFILE_RESOLVER.resolve(db_type, family, result.detected, "slow")
    assert profile.version_status == VersionStatus.VERIFIED
    assert profile.support_state == SupportState.UNKNOWN
    route_prefix = (
        f"legacy:{db_type.value}:{family}:slow:{result.detected.variant_key or 'default'}"
    )
    assert profile.sql_profile_id == f"{route_prefix}:sql"
    assert profile.permission_profile_id == f"{route_prefix}:permission"
    assert profile.parser_contract_id == f"{route_prefix}:parser"
    assert profile.runtime_config_state == RuntimeConfigState.UNKNOWN
    assert profile.data_visibility == DataVisibility.UNKNOWN
    assert isinstance(DEFAULT_VERSION_DETECTOR, RegistryVersionDetector)


def test_mysql_minor_variant_selects_one_atomic_binding() -> None:
    pre = PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, "8.0", normalize_version(DatabaseType.MYSQL, "8.0.21"), "slow"
    )
    current = PROFILE_RESOLVER.resolve(
        DatabaseType.MYSQL, "8.0", normalize_version(DatabaseType.MYSQL, "8.0.22"), "slow"
    )
    assert pre.runtime_variant == "pre_8022"
    assert current.runtime_variant == "8022_plus"
    assert pre.sql_profile_id == "legacy:mysql:8.0:slow:pre_8022:sql"
    assert current.sql_profile_id == "legacy:mysql:8.0:slow:8022_plus:sql"
    assert pre.permission_profile_id == "legacy:mysql:8.0:slow:pre_8022:permission"
    assert current.permission_profile_id == "legacy:mysql:8.0:slow:8022_plus:permission"
    assert pre.parser_contract_id == "legacy:mysql:8.0:slow:pre_8022:parser"
    assert current.parser_contract_id == "legacy:mysql:8.0:slow:8022_plus:parser"


def test_profile_mismatch_unknown_and_unsupported_never_fallback() -> None:
    mismatch = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2019", DetectedDatabaseVersion("16.0.1", "2022"), "slow"
    )
    assert mismatch.version_status == VersionStatus.MISMATCH
    assert mismatch.support_state == SupportState.UNKNOWN
    assert mismatch.sql_profile_id is None
    with pytest.raises(ProfileResolutionError, match="canonical"):
        PROFILE_RESOLVER.resolve(
            DatabaseType.SQLSERVER, "unknown", None, "slow"
        )
    old_ag = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2008r2", DetectedDatabaseVersion("10.50.1", "2008r2"),
        "replication",
    )
    assert old_ag.support_state == SupportState.UNSUPPORTED
    assert old_ag.sql_profile_id is None
    with pytest.raises(ProfileResolutionError, match="No explicit"):
        PROFILE_RESOLVER.resolve(
            DatabaseType.SQLSERVER, "2022", DetectedDatabaseVersion("16.0.1", "2022"),
            "not-registered",
        )


def test_resolved_profile_is_immutable_and_binding_registry_is_valid() -> None:
    profile = PROFILE_RESOLVER.resolve(
        DatabaseType.SQLSERVER, "2022", DetectedDatabaseVersion("16.0.1", "2022"), "slow"
    )
    with pytest.raises(FrozenInstanceError):
        profile.feature = "disk-growth"  # type: ignore[misc]
    assert PROFILE_RESOLVER.validate() == ()
    assert CANONICAL_VERSION_FAMILIES[DatabaseType.ORACLE] == (
        "12c", "18c", "19c", "21c", "23ai", "26ai"
    )


def test_static_registry_rejects_duplicate_dangling_and_forbidden_metadata() -> None:
    valid = next(item for item in PROFILE_BINDINGS if item.db_type == DatabaseType.SQLSERVER
                 and item.version_family == "2022" and item.feature == "slow")
    duplicate = DiagnosticProfileResolver((valid, valid))
    assert any("duplicate" in error for error in duplicate.validate())
    dangling = DiagnosticProfileResolver((replace(valid, sql_profile_id="missing"),))
    assert any("cross-route or unknown profile reference" in error for error in dangling.validate())
    forbidden = DiagnosticProfileResolver((replace(valid, required_privileges=("sysadmin",)),))
    assert any("forbidden privilege" in error for error in forbidden.validate())
    invalid_variant = DiagnosticProfileResolver((replace(
        valid, db_type=DatabaseType.MYSQL, version_family="8.0", runtime_variant="latest"
    ),))
    assert any("invalid MySQL 8.0 variant" in error for error in invalid_variant.validate())


@pytest.mark.parametrize(("yaml_value", "valid"), [
    ('version_family: "2022"', True),
    ("version_family: 2022", False),
    ("version_family: 8.0", False),
    ('version_family: "unknown"', False),
    ('version_family: "2018"', False),
])
def test_inventory_version_family_is_strict_and_optional(
    tmp_path: Path, yaml_value: str, valid: bool,
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "databases:\n  - id: sample\n    name: Sample\n    db_type: sqlserver\n"
        "    host: 192.0.2.10\n    port: 1433\n    credential_alias: sample-monitor\n"
        f"    {yaml_value}\n",
        encoding="utf-8",
    )
    if valid:
        instance = Inventory.load(path).get("sample")
        assert instance.version_family == "2022"
    else:
        with pytest.raises(ValidationError, match="version_family"):
            Inventory.load(path)


def test_missing_inventory_family_is_a_migration_warning(tmp_path: Path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "databases:\n  - id: old\n    name: Old\n    db_type: mysql\n"
        "    host: 192.0.2.20\n    port: 3306\n    credential_alias: old-monitor\n",
        encoding="utf-8",
    )
    inventory = Inventory.load(path)
    assert inventory.get("old").version_family is None
    assert "version_family: \"<canonical>\"" in inventory.version_family_warnings()[0]


def test_validate_is_offline_and_reports_missing_family_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "databases:\n  - id: old\n    name: Old\n    db_type: mysql\n"
        "    host: 192.0.2.20\n    port: 3306\n    credential_alias: old-monitor\n",
        encoding="utf-8",
    )

    def no_connection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validate must not connect")

    monkeypatch.setattr("dba.cli.MySQLAdapter", no_connection)
    assert main(["--inventory", str(path), "--sql-root", str(ROOT / "sql"),
                 "validate", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert 'version_family: "<canonical>"' in result["warnings"][0]


def test_show_exposes_declared_family_without_connecting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        'databases:\n  - id: sample\n    name: Sample\n    db_type: sqlserver\n'
        '    version_family: "2022"\n    host: 192.0.2.10\n    port: 1433\n'
        '    credential_alias: sample-monitor\n',
        encoding="utf-8",
    )

    def no_connection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("show must not connect")

    monkeypatch.setattr("dba.cli.SQLServerAdapter", no_connection)
    assert main(["--inventory", str(path), "show", "sample", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["version_family"] == "2022"


def test_version_profile_exposes_oracle_direct_detection_grant() -> None:
    profile = build_version_profile(
        DatabaseType.ORACLE, "19c", None, error_type="CapabilityUnavailable",
        error_message="version view unavailable",
        database_error={"vendor_code": "ORA-00942"},
    )
    assert profile["required_detection_privilege"] == "SELECT ON SYS.V_$VERSION"
    assert profile["version_detection_error"]["database_error"]["vendor_code"] == "ORA-00942"
    assert VERSION_DETECTION_PRIVILEGES[DatabaseType.ORACLE] == ("SELECT SYS.V_$VERSION",)


def test_oracle_detection_failure_keeps_sanitized_vendor_evidence() -> None:
    class DeniedExecutor:
        def execute_readonly(self, script: SqlScript) -> CollectorResult:
            now = datetime.now(timezone.utc)
            return CollectorResult(
                "oracle-test", script.metadata.name, now, now, 1.0, (), 0, False, False,
                "CapabilityUnavailable", "Oracle monitoring view is unavailable.",
                {"vendor_code": "ORA-00942", "message": "table or view does not exist"},
            )

    detected = detect_database_version(
        DatabaseType.ORACLE, SqlScriptRegistry(ROOT / "sql"), DeniedExecutor()
    )
    profile = build_version_profile(
        DatabaseType.ORACLE, "19c", detected.detected,
        error_type=detected.error_type, error_message=detected.error_message,
        database_error=detected.database_error,
    )
    assert profile["version_status"] == "UNVERIFIED"
    assert profile["required_detection_privilege"] == "SELECT ON SYS.V_$VERSION"
    assert profile["version_detection_error"]["database_error"]["vendor_code"] == "ORA-00942"
