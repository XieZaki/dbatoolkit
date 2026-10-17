"""Version-aware diagnostic profile foundation.

This module owns the canonical family vocabulary, normalization, verification
state and the single atomic profile resolver. Bindings atomically route
feature SQL, permissions and parser contracts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from dba.collectors import CollectorResult
from dba.core import DatabaseType
from dba.errors import ToolkitError
from dba.permissions import VERSION_DETECTION_PRIVILEGES, requirements_for_profile

if TYPE_CHECKING:
    from dba.registry import SqlScript, SqlScriptRegistry


CANONICAL_VERSION_FAMILIES: dict[DatabaseType, tuple[str, ...]] = {
    DatabaseType.SQLSERVER: ("2008r2", "2012", "2014", "2016", "2017", "2019", "2022", "2025"),
    DatabaseType.MYSQL: ("5.7", "8.0", "8.4"),
    DatabaseType.POSTGRESQL: ("13", "14", "15", "16", "17", "18"),
    DatabaseType.ORACLE: ("11gr2", "12c", "18c", "19c", "21c", "23ai", "26ai"),
}
class VersionStatus(StrEnum):
    VERIFIED = "VERIFIED"
    AUTO_DETECTED = "AUTO_DETECTED"
    UNVERIFIED = "UNVERIFIED"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class SupportState(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class RuntimeConfigState(StrEnum):
    CONFIGURED = "CONFIGURED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNKNOWN = "UNKNOWN"


class DataVisibility(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DetectedDatabaseVersion:
    raw_version: str
    normalized_family: str | None
    variant_key: str | None = None


@dataclass(frozen=True)
class VersionDetection:
    detected: DetectedDatabaseVersion | None
    error_type: str | None = None
    error_message: str | None = None
    database_error: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProfileBinding:
    db_type: DatabaseType
    version_family: str
    feature: str
    support_state: SupportState
    sql_profile_id: str
    permission_profile_id: str
    parser_contract_id: str
    profile_family: str
    runtime_variant: str = "default"
    required_privileges: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedDiagnosticProfile:
    db_type: DatabaseType
    feature: str
    declared_version_family: str | None
    detected_raw_version: str | None
    detected_version_family: str | None
    version_status: VersionStatus
    support_state: SupportState
    profile_family: str | None
    sql_profile_id: str | None
    permission_profile_id: str | None
    parser_contract_id: str | None
    runtime_variant: str | None
    runtime_config_state: RuntimeConfigState = RuntimeConfigState.UNKNOWN
    data_visibility: DataVisibility = DataVisibility.UNKNOWN


class VersionExecutor(Protocol):
    def execute_readonly(self, script: SqlScript) -> CollectorResult: ...


class VersionDetector(Protocol):
    def detect(
        self, database_type: DatabaseType, registry: SqlScriptRegistry,
        executor: VersionExecutor,
    ) -> VersionDetection: ...


def canonical_version_families(database_type: DatabaseType) -> tuple[str, ...]:
    """Return the one canonical family list for a supported database type."""
    return CANONICAL_VERSION_FAMILIES.get(database_type, ())


def validate_version_family(database_type: DatabaseType, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("version_family must be a quoted string")
    if value not in canonical_version_families(database_type):
        supported = ", ".join(canonical_version_families(database_type)) or "none"
        raise ValueError(
            f"Unsupported non-canonical {database_type.value} version_family {value!r}; "
            f"supported: {supported}"
        )
    return value


def normalize_version(
    database_type: DatabaseType, raw_version: str, *, server_version_num: int | None = None
) -> DetectedDatabaseVersion:
    """Normalize a bounded vendor version string without a latest-version fallback."""
    raw = raw_version.strip()
    family: str | None = None
    variant: str | None = None

    if database_type == DatabaseType.SQLSERVER:
        match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", raw)
        if match:
            major, minor = int(match[1]), int(match[2])
            family = {
                (10, 50): "2008r2", (11, 0): "2012", (12, 0): "2014",
                (13, 0): "2016", (14, 0): "2017", (15, 0): "2019",
                (16, 0): "2022", (17, 0): "2025",
            }.get((major, minor))
    elif database_type == DatabaseType.MYSQL:
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw)
        if match and "mariadb" not in raw.casefold():
            major, minor, patch = (int(match[i]) for i in range(1, 4))
            if (major, minor) == (5, 7):
                family = "5.7"
            elif (major, minor) == (8, 0):
                family = "8.0"
                variant = "pre_8022" if patch < 22 else "8022_plus"
            elif (major, minor) == (8, 4):
                family = "8.4"
    elif database_type == DatabaseType.POSTGRESQL:
        if server_version_num is not None and 130000 <= server_version_num < 190000:
            family = str(server_version_num // 10000)
        else:
            match = re.match(r"^(\d+)(?:\.|\s|$)", raw)
            if match and 13 <= int(match[1]) <= 18:
                family = match[1]
    elif database_type == DatabaseType.ORACLE:
        folded = raw.casefold()
        release_match = re.search(r"\brelease\s+(\d+)\.(\d+)", folded)
        numeric_match = release_match or re.search(r"\b(\d+)\.(\d+)(?:\.\d+)*", folded)
        numeric = (int(numeric_match[1]), int(numeric_match[2])) if numeric_match else None
        if numeric and numeric[0] == 11 and numeric[1] == 2:
            family = "11gr2"
        elif "26ai" in folded or ("26ai" not in folded and numeric == (23, 26)):
            family = "26ai"
        elif "23ai" in folded or "23c" in folded or numeric == (23, 0):
            family = "23ai"
        elif numeric:
            major, minor = numeric
            if major == 12 and minor in {1, 2}:
                family = "12c"
                variant = "12_1" if minor == 1 else "12_2_plus"
            elif major in {18, 19, 21}:
                family = f"{major}c"

    if family not in canonical_version_families(database_type):
        family = None
    return DetectedDatabaseVersion(raw, family, variant)


def version_status(
    declared_family: str | None, detected: DetectedDatabaseVersion | None
) -> VersionStatus:
    detected_family = detected.normalized_family if detected else None
    if declared_family and detected_family:
        return (VersionStatus.VERIFIED if declared_family == detected_family
                else VersionStatus.MISMATCH)
    if declared_family:
        return VersionStatus.UNVERIFIED
    if detected_family:
        return VersionStatus.AUTO_DETECTED
    return VersionStatus.UNKNOWN


def detect_database_version(
    database_type: DatabaseType, registry: SqlScriptRegistry, executor: VersionExecutor
) -> VersionDetection:
    """Run the fixed, reviewed per-engine version query through the adapter."""
    from dba.registry import SqlScriptRegistry

    if not isinstance(registry, SqlScriptRegistry):
        return VersionDetection(None, "RegistryRejected", "Version SQL registry is unavailable.")
    if database_type not in CANONICAL_VERSION_FAMILIES:
        return VersionDetection(None, "NotImplemented", "Version detection is not implemented.")
    try:
        script = registry.get("version_detect", database_type)
        registry.authorize(script, database_type)
        result = executor.execute_readonly(script)
    except Exception as exc:
        if isinstance(exc, ToolkitError):
            return VersionDetection(None, exc.error_type, str(exc))
        return VersionDetection(None, "VersionDetectionFailed",
                                "Version detection query could not be completed.")
    if not result.success:
        return VersionDetection(None, result.error_type or "VersionDetectionFailed",
                                result.error_message or "Version detection query failed.",
                                result.database_error)
    if result.truncated or len(result.rows) != 1:
        return VersionDetection(None, "VersionDetectionIncomplete",
                                "Version detection did not return exactly one row.")
    row = result.rows[0]
    raw_value = row.get("raw_version")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return VersionDetection(None, "VersionDetectionIncomplete", "Vendor version value is unavailable.")
    version_num = row.get("server_version_num")
    detected = normalize_version(
        database_type, raw_value,
        server_version_num=version_num if type(version_num) is int else None,
    )
    return VersionDetection(detected)


class RegistryVersionDetector:
    """Concrete detector using only fixed scripts from the formal SQL Registry."""

    def detect(
        self, database_type: DatabaseType, registry: SqlScriptRegistry,
        executor: VersionExecutor,
    ) -> VersionDetection:
        return detect_database_version(database_type, registry, executor)


DEFAULT_VERSION_DETECTOR: VersionDetector = RegistryVersionDetector()


def build_version_profile(
    database_type: DatabaseType, declared_family: str | None,
    detected: DetectedDatabaseVersion | None, *, error_type: str | None = None,
    error_message: str | None = None,
    database_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "declared_version_family": declared_family,
        "detected_version": detected.raw_version if detected else None,
        "detected_version_family": detected.normalized_family if detected else None,
        "runtime_variant": detected.variant_key if detected else None,
        "version_status": version_status(declared_family, detected).value,
    }
    if error_type:
        result["version_detection_error"] = {
            "error_type": error_type,
            "message": error_message or "Version detection could not be completed.",
            "database_error": database_error,
        }
        requirements = VERSION_DETECTION_PRIVILEGES.get(database_type, ())
        if requirements:
            result["required_detection_privilege"] = requirements[0].replace(
                "SELECT ", "SELECT ON ", 1
            )
    return result


# Legacy component IDs exist solely to make the Phase A binding shape explicit.
# They refer to current registries and are not claimed to be version-routed.
def _legacy_binding(db_type: DatabaseType, family: str, feature: str,
                    variant: str = "default") -> ProfileBinding:
    prefix = f"legacy:{db_type.value}:{family}:{feature}:{variant}"
    return ProfileBinding(db_type, family, feature, SupportState.UNKNOWN,
                          f"{prefix}:sql", f"{prefix}:permission", f"{prefix}:parser",
                          "legacy-compatible", variant)


# These are explicit foundation routes. They are deliberately sparse until
# later phases migrate each feature's SQL, permissions and parser atomically.
PROFILE_BINDINGS: tuple[ProfileBinding, ...] = (
    ProfileBinding(DatabaseType.SQLSERVER, "2008r2", "slow", SupportState.SUPPORTED,
                   "sqlserver_2008r2_slow", "sqlserver_slow_legacy", "sqlserver_slow",
                   "sqlserver_slow_legacy", required_privileges=("VIEW SERVER STATE", "VIEW ANY DATABASE")),
    *(ProfileBinding(DatabaseType.SQLSERVER, family, "slow", SupportState.SUPPORTED,
                     "sqlserver_shared_slow", "sqlserver_slow_legacy", "sqlserver_slow",
                     "sqlserver_slow_shared", required_privileges=("VIEW SERVER STATE", "VIEW ANY DATABASE"))
      for family in ("2012", "2014", "2016", "2017", "2019")),
    ProfileBinding(DatabaseType.SQLSERVER, "2022", "slow", SupportState.SUPPORTED,
                   "sqlserver_shared_slow", "sqlserver_slow_2022", "sqlserver_slow",
                   "sqlserver_slow_shared",
                   required_privileges=("VIEW SERVER PERFORMANCE STATE", "VIEW ANY DATABASE")),
    ProfileBinding(DatabaseType.SQLSERVER, "2025", "slow", SupportState.UNKNOWN,
                   "", "", "", "unverified"),
    _legacy_binding(DatabaseType.SQLSERVER, "2022", "disk-growth"),
    _legacy_binding(DatabaseType.SQLSERVER, "2022", "plan"),
    ProfileBinding(DatabaseType.SQLSERVER, "2008r2", "replication", SupportState.UNSUPPORTED,
                   "", "", "", "unsupported", "default"),
    ProfileBinding(DatabaseType.MYSQL, "5.7", "slow", SupportState.SUPPORTED,
                   "mysql57_slow", "mysql57_slow", "mysql_slow", "mysql_slow_legacy",
                   required_privileges=("PROCESS", "SELECT performance_schema")),
    *(ProfileBinding(DatabaseType.MYSQL, "8.0", "slow", SupportState.SUPPORTED,
                     "mysql80_slow", "mysql80_slow", "mysql_slow", "mysql_slow_modern",
                     variant, ("PROCESS", "SELECT performance_schema"))
      for variant in ("pre_8022", "8022_plus")),
    ProfileBinding(DatabaseType.MYSQL, "8.4", "slow", SupportState.SUPPORTED,
                   "mysql84_slow", "mysql84_slow", "mysql_slow", "mysql_slow_modern",
                   required_privileges=("PROCESS", "SELECT performance_schema")),
    *(ProfileBinding(DatabaseType.POSTGRESQL, family, "slow", SupportState.SUPPORTED,
                     "pg13_18_slow", "pg13_18_slow", "postgresql_slow", "pg13_18_slow",
                     required_privileges=("pg_monitor", "CONNECT"))
      for family in ("13", "14", "15", "16", "17", "18")),
    ProfileBinding(DatabaseType.ORACLE, "11gr2", "slow", SupportState.SUPPORTED,
                   "oracle11gr2_slow", "oracle11gr2_slow", "oracle_slow", "oracle11gr2_slow",
                   required_privileges=("CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION",
                                        "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL",
                                        "SELECT SYS.V_$TRANSACTION")),
    ProfileBinding(DatabaseType.ORACLE, "12c", "slow", SupportState.SUPPORTED,
                   "oracle12c_121_slow", "oracle12c_121_slow", "oracle_slow", "oracle12c_121_slow",
                   "12_1", ("CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION",
                            "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL",
                            "SELECT SYS.V_$TRANSACTION")),
    ProfileBinding(DatabaseType.ORACLE, "12c", "slow", SupportState.SUPPORTED,
                   "oracle12c_122_slow", "oracle12c_122_slow", "oracle_slow", "oracle12c_122_slow",
                   "12_2_plus", ("CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION",
                                 "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL",
                                 "SELECT SYS.V_$TRANSACTION")),
    *(ProfileBinding(DatabaseType.ORACLE, family, "slow", SupportState.SUPPORTED,
                     "oracle_modern_slow", "oracle_modern_slow", "oracle_slow", "oracle_modern_slow",
                     required_privileges=("CREATE SESSION", "SELECT SYS.V_$INSTANCE", "SELECT SYS.V_$VERSION",
                                          "SELECT SYS.V_$DATABASE", "SELECT SYS.V_$SESSION", "SELECT SYS.V_$SQL",
                                          "SELECT SYS.V_$TRANSACTION"))
      for family in ("18c", "19c", "21c", "23ai", "26ai")),
) + tuple(
    ProfileBinding(DatabaseType.MYSQL, family, "replication", SupportState.SUPPORTED,
                   sql_id, permission_id, parser_id, profile_family, variant,
                   required_privileges)
    for family, variant, sql_id, permission_id, parser_id, profile_family, required_privileges in (
        ("5.7", "default", "mysql57_replication", "mysql57_replication",
         "mysql57_replication", "mysql57_replication", ("REPLICATION CLIENT",)),
        ("8.0", "pre_8022", "mysql80_pre_8022_replication", "mysql80_pre_8022_replication",
         "mysql80_pre_8022_replication", "mysql80_pre_8022_replication", ("REPLICATION CLIENT",)),
        ("8.0", "8022_plus", "mysql80_8022_plus_replication", "mysql80_8022_plus_replication",
         "mysql80_8022_plus_replication", "mysql80_8022_plus_replication", ("REPLICATION CLIENT",)),
        ("8.4", "default", "mysql84_replication", "mysql84_replication",
         "mysql84_replication", "mysql84_replication", ("REPLICATION CLIENT",)),
    )
) + tuple(
    ProfileBinding(DatabaseType.POSTGRESQL, family, "replication", SupportState.SUPPORTED,
                   "pg13_18_replication", "pg13_18_replication", "pg13_18_replication",
                   "pg13_18_replication", "default", ("pg_monitor", "CONNECT"))
    for family in ("13", "14", "15", "16", "17", "18")
) + tuple(
    ProfileBinding(DatabaseType.SQLSERVER, family, "replication", SupportState.SUPPORTED,
                   "sqlserver_ag_pre2022" if family != "2022" else "sqlserver_ag_2022",
                   "sqlserver_ag_pre2022" if family != "2022" else "sqlserver_ag_2022",
                   "sqlserver_ag_shared", "sqlserver_ag_pre2022" if family != "2022"
                   else "sqlserver_ag_2022", "default",
                   ("VIEW SERVER STATE",) if family != "2022" else
                   ("VIEW SERVER PERFORMANCE STATE",))
    for family in ("2012", "2014", "2016", "2017", "2019", "2022")
) + (
    ProfileBinding(DatabaseType.SQLSERVER, "2025", "replication", SupportState.UNKNOWN,
                   "", "", "", "unverified", "default"),
) + tuple(
    ProfileBinding(DatabaseType.ORACLE, family, "replication", SupportState.SUPPORTED,
                   sql_id, permission_id, parser_id, profile_family, variant,
                   required_privileges)
    for family, variant, sql_id, permission_id, parser_id, profile_family, required_privileges in (
        ("11gr2", "default", "oracle11gr2_replication", "oracle11gr2_replication",
         "oracle11gr2_replication", "oracle11gr2_replication",
         requirements_for_profile("oracle11gr2_replication").required),
        ("12c", "12_1", "oracle12c_121_replication", "oracle12c_121_replication",
         "oracle12c_121_replication", "oracle12c_121_replication",
         requirements_for_profile("oracle12c_121_replication").required),
        ("12c", "12_2_plus", "oracle12c_122_replication", "oracle12c_122_replication",
         "oracle12c_122_replication", "oracle12c_122_replication",
         requirements_for_profile("oracle12c_122_replication").required),
        *( (family, "default", "oracle_modern_replication", "oracle_modern_replication",
            "oracle_modern_replication", "oracle_modern_replication",
            requirements_for_profile("oracle_modern_replication").required)
           for family in ("18c", "19c", "21c", "23ai", "26ai") ),
    )
)


class ProfileResolutionError(ValueError):
    pass


class DiagnosticProfileResolver:
    """The sole public route resolver; returns one immutable atomic binding."""

    def __init__(self, bindings: tuple[ProfileBinding, ...] = PROFILE_BINDINGS) -> None:
        self.bindings = bindings

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        seen: set[tuple[DatabaseType, str, str, str]] = set()
        forbidden = {"SYSDBA", "DBA", "SELECT_CATALOG_ROLE", "SYSADMIN", "CONTROL SERVER",
                     "SUPERUSER", "ALL PRIVILEGES", "SUPER", "SYSTEM_USER"}
        for binding in self.bindings:
            key = (binding.db_type, binding.version_family, binding.feature,
                   binding.runtime_variant)
            if key in seen:
                errors.append(f"duplicate profile route: {key}")
            seen.add(key)
            if binding.version_family not in canonical_version_families(binding.db_type):
                errors.append(f"unknown version family: {binding.db_type.value}/{binding.version_family}")
            if not isinstance(binding.support_state, SupportState):
                errors.append(f"missing or invalid support state: {key}")
            has_reference = any((binding.sql_profile_id, binding.permission_profile_id,
                                 binding.parser_contract_id))
            if binding.support_state != SupportState.UNSUPPORTED and (
                binding.support_state != SupportState.UNKNOWN or has_reference
            ):
                referenced = (binding.sql_profile_id, binding.permission_profile_id,
                              binding.parser_contract_id)
                prefix = (f"legacy:{binding.db_type.value}:{binding.version_family}:"
                          f"{binding.feature}:{binding.runtime_variant}")
                expected = (f"{prefix}:sql", f"{prefix}:permission", f"{prefix}:parser")
                if any(not item for item in referenced):
                    errors.append(f"dangling profile reference: {key}")
                elif binding.feature == "replication":
                    if any(item.startswith("legacy:") for item in referenced):
                        errors.append(f"cross-route or unknown profile reference: {key}")
                elif binding.feature == "slow":
                    from dba.slow_profiles import validate_slow_binding
                    errors.extend(validate_slow_binding(binding))
                elif referenced != expected:
                    errors.append(f"cross-route or unknown profile reference: {key}")
                if binding.feature == "replication":
                    try:
                        permission = requirements_for_profile(binding.permission_profile_id)
                    except ValueError:
                        errors.append(f"missing permission profile: {key}")
                    else:
                        if tuple(permission.required) != binding.required_privileges:
                            errors.append(f"route permission contract mismatch: {key}")
                elif binding.feature == "slow":
                    try:
                        permission = requirements_for_profile(binding.permission_profile_id)
                    except ValueError:
                        errors.append(f"missing permission profile: {key}")
                    else:
                        if tuple(permission.required) != binding.required_privileges:
                            errors.append(f"route permission contract mismatch: {key}")
            risky = forbidden.intersection(p.upper() for p in binding.required_privileges)
            if risky:
                errors.append(f"forbidden privilege in profile metadata: {key}")
            if binding.db_type == DatabaseType.MYSQL and binding.version_family == "8.0":
                if binding.runtime_variant not in {"pre_8022", "8022_plus"}:
                    errors.append(f"invalid MySQL 8.0 variant: {key}")
            elif binding.db_type == DatabaseType.ORACLE and binding.version_family == "12c" \
                    and binding.feature in {"replication", "slow"}:
                if binding.runtime_variant not in {"12_1", "12_2_plus"}:
                    errors.append(f"invalid Oracle 12c variant: {key}")
            elif binding.runtime_variant != "default":
                errors.append(f"invalid variant for route: {key}")
        return tuple(errors)

    def resolve(
        self, database_type: DatabaseType, declared_version_family: str | None,
        detected_version: DetectedDatabaseVersion | None, feature: str,
    ) -> ResolvedDiagnosticProfile:
        invalid = self.validate()
        if invalid:
            raise ProfileResolutionError("Invalid profile registry: " + "; ".join(invalid))
        if not isinstance(database_type, DatabaseType) or database_type not in CANONICAL_VERSION_FAMILIES:
            raise ProfileResolutionError("Resolver requires a canonical supported database type")
        if declared_version_family is not None:
            try:
                validate_version_family(database_type, declared_version_family)
            except ValueError as exc:
                raise ProfileResolutionError(str(exc)) from None
        detected_family = detected_version.normalized_family if detected_version else None
        if detected_family and detected_family not in canonical_version_families(database_type):
            raise ProfileResolutionError("Detected version family is not canonical")
        status = version_status(declared_version_family, detected_version)
        family = detected_family if status == VersionStatus.AUTO_DETECTED else declared_version_family
        variant = detected_version.variant_key if detected_version else None
        if family is None or status in {VersionStatus.UNKNOWN, VersionStatus.MISMATCH}:
            return ResolvedDiagnosticProfile(
                database_type, feature, declared_version_family,
                detected_version.raw_version if detected_version else None, detected_family,
                status, SupportState.UNKNOWN, None, None, None, None, variant,
            )
        lookup_variant = variant or "default"
        route = next((item for item in self.bindings if
                      item.db_type == database_type and item.version_family == family
                      and item.feature == feature and item.runtime_variant == lookup_variant), None)
        family_routes = [item for item in self.bindings if
                         item.db_type == database_type and item.version_family == family
                         and item.feature == feature]
        has_variant_routes = any(item.runtime_variant != "default" for item in family_routes)
        if route is None and lookup_variant != "default" and not has_variant_routes:
            route = next((item for item in self.bindings if
                          item.db_type == database_type and item.version_family == family
                          and item.feature == feature and item.runtime_variant == "default"), None)
        if route is None:
            raise ProfileResolutionError(
                f"No explicit diagnostic profile route for {database_type.value}/{family}/{feature}"
            )
        return ResolvedDiagnosticProfile(
            database_type, feature, declared_version_family,
            detected_version.raw_version if detected_version else None, detected_family,
            status, route.support_state, route.profile_family,
            route.sql_profile_id or None, route.permission_profile_id or None,
            route.parser_contract_id or None, route.runtime_variant,
        )


PROFILE_RESOLVER = DiagnosticProfileResolver()


def resolve_profile_for_adapter(adapter: Any, feature: str) -> tuple[ResolvedDiagnosticProfile, VersionDetection]:
    """Detect the connected target version and resolve one complete static profile."""
    detection = DEFAULT_VERSION_DETECTOR.detect(adapter.instance.db_type, adapter.registry, adapter)
    profile = PROFILE_RESOLVER.resolve(
        adapter.instance.db_type, adapter.instance.version_family, detection.detected, feature,
    )
    return profile, detection
