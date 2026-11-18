"""Bounded diagnostic policies; connection and Registry limits remain independent."""

from dataclasses import fields, replace
from pathlib import Path
from typing import Any

# path -> engine field, default, minimum, maximum
PARAMETERS: dict[str, tuple[str, int | bool, int, int]] = {
    "diagnostic_sql_timeout_seconds": ("diagnostic_sql_timeout_seconds", 30, 1, 60),
    "slow.threshold_seconds": ("long_running_seconds", 10, 1, 86400),
    "limits.top_n": ("slow_top_n", 10, 1, 100),
    "concurrency.active_requests_threshold": ("high_concurrency_threshold", 50, 1, 86400),
    "transactions.idle_threshold_seconds": ("idle_transaction_seconds", 60, 1, 86400),
    "transactions.sqlserver_long_transaction_seconds": ("sqlserver_long_transaction_seconds", 3600, 1, 86400),
    "plans.sqlserver.max_xml_bytes": ("plan_max_xml_size", 1048576, 4096, 4194304),
    "plans.sqlserver.top_operators": ("plan_top_operators", 10, 1, 100),
    "plans.sqlserver.large_scan_rows": ("plan_large_scan_rows", 100000, 1, 1000000000),
    "plans.sqlserver.cardinality_ratio_warning": ("plan_cardinality_ratio_warning", 10, 1, 86400),
    "plans.sqlserver.cardinality_ratio_critical": ("plan_cardinality_ratio_critical", 100, 1, 86400),
}


def flatten_diagnostics(value: object, prefix: str = "") -> dict[str, int | bool]:
    from dba.inventory import ValidationError

    if not isinstance(value, dict):
        raise ValidationError("diagnostics must be a mapping")
    result: dict[str, int | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValidationError("diagnostics may contain only supported policy fields")
        path = f"{prefix}.{key}" if prefix else key
        if path in PARAMETERS:
            _, default, minimum, maximum = PARAMETERS[path]
            if type(default) is bool:
                if type(item) is not bool:
                    raise ValidationError(f"diagnostics.{path} must be boolean")
                if item:
                    raise ValidationError("Automatic plan collection is not implemented; use explicit plan diagnosis")
            elif type(item) is not int or not minimum <= item <= maximum:
                raise ValidationError(f"diagnostics.{path} must be an integer in {minimum}..{maximum}")
            result[path] = item
        elif any(name.startswith(path + ".") for name in PARAMETERS):
            result.update(flatten_diagnostics(item, path))
        else:
            raise ValidationError("diagnostics may contain only supported policy fields")
    return result


def merge_diagnostics(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in overrides.items():
        result[key] = (merge_diagnostics(result.get(key, {}), value)
                       if isinstance(value, dict) else value)
    return result


def resolve_diagnostics(
    settings: Any, instance: Any, path: Path | None, *, top: int | None = None,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    from dba.inventory import ValidationError, load_yaml

    data = load_yaml(path) if path else {}
    shared = flatten_diagnostics(data.get("diagnostics", {}))
    local = flatten_diagnostics(instance.diagnostics)
    legacy = data.get(instance.db_type.value, {})
    supported = {item.name for item in fields(settings)}
    updates: dict[str, int | bool] = {}
    evidence: dict[str, dict[str, Any]] = {}
    sources = getattr(instance, "_diagnostic_sources", {})
    defaults = getattr(instance, "_diagnostic_defaults", {})
    for name, (field, default, minimum, maximum) in PARAMETERS.items():
        if field not in supported and not name.startswith("plans."):
            continue
        if name.startswith("plans.sqlserver.") and instance.db_type.value != "sqlserver":
            continue
        history: list[dict[str, Any]] = [{"source": "DEFAULT", "value": default}]
        if field in legacy:
            history.append({"source": "SETTINGS_ENGINE", "value": legacy[field]})
        if name in shared:
            history.append({"source": "SETTINGS_GLOBAL", "value": shared[name]})
        if name in defaults and sources.get(name) == "INVENTORY_INSTANCE":
            history.append({"source": "INVENTORY_DEFAULTS", "value": defaults[name]})
        if name in local:
            history.append({"source": sources.get(name, "INVENTORY_INSTANCE"), "value": local[name]})
        if name == "limits.top_n" and top is not None:
            if type(top) is not int or not minimum <= top <= maximum:
                raise ValidationError("top must be an integer in 1..100")
            history.append({"source": "CLI", "value": top})
        chosen = history[-1]
        value = chosen["value"]
        if type(default) is int and (type(value) is not int or not minimum <= value <= maximum):
            raise ValidationError(f"Effective diagnostics.{name} exceeds its safety range")
        evidence[name] = {
            **chosen, "overridden": history[:-1], "at_hard_limit": type(default) is int and value == maximum,
        }
        if field in supported:
            updates[field] = value
    return replace(settings, **updates), evidence
