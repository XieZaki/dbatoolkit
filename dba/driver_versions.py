"""Validate database-driver release versions without confusing API aliases."""

from __future__ import annotations

from dataclasses import dataclass


class DriverModuleVersionError(ValueError):
    """Raised when a driver module's release identity is inconsistent."""


@dataclass(frozen=True)
class DriverModuleVersion:
    release_version: str | None
    reported_module_version: str | None
    reported_version_role: str | None


def _final_version_from_tuple(value: object) -> str | None:
    if (not isinstance(value, tuple) or len(value) < 4
            or any(type(part) is not int for part in value[:3])
            or value[3] != "final"):
        return None
    return ".".join(str(part) for part in value[:3])


def inspect_driver_module_version(
    module: object,
    *,
    module_name: str,
    distribution_name: str,
) -> DriverModuleVersion:
    """Return a validated release identity and any documented API version alias.

    PyMySQL intentionally exposes ``__version__`` as its mysqlclient-compatible
    version for Django/mysqlclient callers. Its package release is separately
    declared by ``VERSION_STRING`` and ``VERSION``. Validate both identities so
    the compatibility value is accounted for instead of silently ignored.
    """
    canonical_distribution = distribution_name.casefold().replace("_", "-")
    if canonical_distribution == "pymysql" and module_name == "pymysql":
        release = getattr(module, "VERSION_STRING", None)
        if (not isinstance(release, str) or not release
                or _final_version_from_tuple(getattr(module, "VERSION", None)) != release):
            raise DriverModuleVersionError("PyMySQL release identity is incomplete or inconsistent.")

        reported = getattr(module, "__version__", None)
        if reported is None or str(reported) == release:
            return DriverModuleVersion(release, str(reported) if reported is not None else None, None)

        reported_version = str(reported)
        if _final_version_from_tuple(getattr(module, "version_info", None)) != reported_version:
            raise DriverModuleVersionError("PyMySQL compatibility version identity is inconsistent.")
        return DriverModuleVersion(release, reported_version, "mysqlclient compatibility")

    reported = getattr(module, "__version__", getattr(module, "version", None))
    version = str(reported) if reported is not None else None
    return DriverModuleVersion(version, version, None)


def validate_driver_module_version(
    module: object,
    *,
    module_name: str,
    distribution_name: str,
    distribution_version: str,
) -> DriverModuleVersion:
    evidence = inspect_driver_module_version(
        module,
        module_name=module_name,
        distribution_name=distribution_name,
    )
    if evidence.release_version is not None and evidence.release_version != distribution_version:
        raise DriverModuleVersionError("Driver module release version differs from distribution metadata.")
    return evidence
