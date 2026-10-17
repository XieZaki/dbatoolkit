import re
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, cast

import yaml

from dba.connection import ConnectionConfigError, validate_connection_options
from dba.core import DatabaseInstance, DatabaseType
from dba.versioning import canonical_version_families, validate_version_family


class ValidationError(ValueError):
    pass


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently overwriting them."""


def _mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    construct = cast(Callable[[yaml.Node], Any], loader.construct_object)
    for key_node, value_node in node.value:
        key = construct(key_node)
        if key in result:
            raise ValidationError("Duplicate YAML key")
        result[key] = construct(value_node)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (yaml.YAMLError, TypeError, UnicodeError) as exc:
        raise ValidationError("Invalid YAML document") from exc


def _contains_credential(value: str) -> bool:
    return bool(re.search(
        r"(?i)\b(?:password|pwd|secret|token)\s*[:=]|://[^\s/]*:[^\s/]*@", value
    ))


class Inventory:
    def __init__(
        self, instances: list[DatabaseInstance], sources: dict[str, Path] | None = None
    ) -> None:
        self.instances = tuple(instances)
        self._sources = sources or {}

    @staticmethod
    def _files(path: Path) -> list[Path]:
        # The conventional legacy path selects a populated sibling directory.
        if path.name == "inventory.yaml" and not path.is_dir():
            sibling = path.with_suffix("")
            if sibling.is_dir():
                candidates = Inventory._directory_files(sibling)
                if candidates:
                    return candidates
        if path.is_dir():
            candidates = Inventory._directory_files(path)
            if not candidates:
                raise ValidationError(f"{path}: No inventory YAML files found")
            return candidates
        return [path]

    @staticmethod
    def _directory_files(path: Path) -> list[Path]:
        return sorted(
            (item for item in path.rglob("*") if item.is_file()
             and item.suffix.lower() in {".yaml", ".yml"}),
            key=lambda item: item.as_posix().casefold(),
        )

    @staticmethod
    def _validate_values(values: dict[str, Any], source: Path, where: str) -> dict[str, Any]:
        allowed = {f.name for f in fields(DatabaseInstance)}
        unknown = set(values) - allowed
        if unknown:
            raise ValidationError(f"{source}: {where}: Unknown or forbidden inventory field")
        result = dict(values)
        for key, value in values.items():
            if key == "port":
                if type(value) is not int or not 1 <= value <= 65535:
                    raise ValidationError(f"{source}: {where}: port must be an integer in 1..65535")
            elif key == "enabled":
                if type(value) is not bool:
                    raise ValidationError(f"{source}: {where}: enabled must be a boolean")
            elif key == "tags":
                if not isinstance(value, list) or any(
                    not isinstance(tag, str) or not tag.strip() for tag in value
                ):
                    raise ValidationError(f"{source}: {where}: tags must be a string list")
                tags = list(dict.fromkeys(tag.strip() for tag in value))
                for tag in tags:
                    if _contains_credential(tag):
                        raise ValidationError(f"{source}: {where}: Embedded credentials are prohibited")
                result[key] = tags
            elif key == "connection":
                if not isinstance(value, dict):
                    raise ValidationError(f"{source}: {where}: connection must be a mapping")
                # Reject credentials before reporting option names so secret-like
                # keys never cross the validation boundary.
                try:
                    from dba.connection import _reject_nested_sensitive

                    _reject_nested_sensitive(value)
                except ConnectionConfigError as exc:
                    raise ValidationError(f"{source}: {where}: {exc}") from None
                result[key] = dict(value)
            elif key == "db_type":
                if not isinstance(value, str) or value not in {t.value for t in DatabaseType}:
                    raise ValidationError(f"{source}: {where}: Unsupported db_type")
            elif key == "version_family":
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValidationError(
                        f"{source}: {where}: version_family must be a quoted string"
                    )
                if not value.strip():
                    raise ValidationError(f"{source}: {where}: version_family must not be empty")
            elif value is None and key not in {
                "id", "name", "db_type", "host", "credential_alias", "version_family"
            }:
                continue
            elif not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{source}: {where}: {key} must be a nonempty string")
            elif _contains_credential(value):
                raise ValidationError(f"{source}: {where}: Embedded credentials are prohibited")
        if "group" in result and result["group"] is not None:
            result["group"] = result["group"].strip()
        return result

    @classmethod
    def load(cls, path: Path) -> "Inventory":
        instances: list[DatabaseInstance] = []
        sources: dict[str, Path] = {}
        required = {"id", "name", "db_type", "host", "port", "credential_alias"}
        for source in cls._files(path):
            try:
                data = load_yaml(source)
            except (OSError, ValidationError) as exc:
                reason = str(exc) if isinstance(exc, ValidationError) else "Could not read inventory file"
                raise ValidationError(f"{source}: {reason}") from None
            if not isinstance(data, dict) or not set(data) <= {"defaults", "databases"} \
                    or "databases" not in data:
                raise ValidationError(f"{source}: Inventory must contain databases and optional defaults only")
            if not isinstance(data["databases"], list):
                raise ValidationError(f"{source}: databases must be a list")
            raw_defaults = data.get("defaults", {})
            if not isinstance(raw_defaults, dict):
                raise ValidationError(f"{source}: defaults must be a mapping")
            if "id" in raw_defaults:
                raise ValidationError(f"{source}: defaults: id must be set per database")
            defaults = cls._validate_values(raw_defaults, source, "defaults")
            for index, raw_entry in enumerate(data["databases"], start=1):
                where = f"databases[{index}]"
                if not isinstance(raw_entry, dict):
                    raise ValidationError(f"{source}: {where}: database must be a mapping")
                checked_entry = cls._validate_values(raw_entry, source, where)
                entry = {**defaults, **checked_entry}
                if "connection" in defaults or "connection" in checked_entry:
                    entry["connection"] = {
                        **defaults.get("connection", {}), **checked_entry.get("connection", {})
                    }
                if not required <= set(entry):
                    raise ValidationError(f"{source}: {where}: Missing required inventory field")
                database_id = entry["id"]
                if database_id in sources:
                    raise ValidationError(
                        f"Duplicate database ID {database_id}: first source {sources[database_id]}; "
                        f"second source {source}"
                    )
                try:
                    materialized = {**entry, "db_type": DatabaseType(entry["db_type"])}
                    if materialized.get("version_family") is not None:
                        try:
                            validate_version_family(
                                materialized["db_type"], materialized["version_family"]
                            )
                        except ValueError as exc:
                            raise ValidationError(f"{source}: {where}: {exc}") from None
                    materialized["connection"] = validate_connection_options(
                        materialized["db_type"], materialized.get("connection", {})
                    )
                    if "tags" in materialized:
                        materialized["tags"] = list(materialized["tags"])
                    instance = DatabaseInstance(**materialized)
                except ConnectionConfigError as exc:
                    raise ValidationError(f"{source}: {where}: {exc}") from None
                except ValidationError:
                    raise
                except ValueError as exc:
                    raise ValidationError(f"{source}: {where}: Unsupported db_type") from exc
                sources[database_id] = source
                instances.append(instance)
        return cls(instances, sources)

    def version_family_warnings(self) -> tuple[str, ...]:
        return tuple(
            f"Version family not declared for {instance.id}. Detected version can be used "
            f"temporarily, but add version_family: \"<canonical>\". Supported values: "
            f"{', '.join(canonical_version_families(instance.db_type))}."
            for instance in self.instances
            if instance.version_family is None and canonical_version_families(instance.db_type)
        )

    def filter(
        self, db_type: str | None = None, environment: str | None = None,
        group: str | None = None, tag: str | None = None,
    ) -> list[DatabaseInstance]:
        return [
            i
            for i in self.instances
            if (db_type is None or i.db_type.value == db_type)
            and (environment is None or i.environment == environment)
            and (group is None or i.group == group)
            and (tag is None or tag in i.tags)
        ]

    def source_file(self, database_id: str) -> Path:
        self.get(database_id)
        return self._sources[database_id]

    def get(self, database_id: str) -> DatabaseInstance:
        for instance in self.instances:
            if instance.id == database_id:
                return instance
        raise ValidationError("Database ID not found")
