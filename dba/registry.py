from dataclasses import dataclass
from pathlib import Path

from dba.core import DatabaseType
from dba.inventory import ValidationError, load_yaml
from dba.security import check_sql


@dataclass(frozen=True)
class SqlMetadata:
    name: str
    database_type: DatabaseType
    risk: str
    timeout_seconds: int
    max_rows: int
    reviewed: bool
    description: str
    feature: str | None = None
    version_families: tuple[str, ...] = ()
    runtime_variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class SqlScript:
    metadata: SqlMetadata
    text: str


class SqlScriptRegistry:
    def __init__(self, root: Path, formal: bool = True) -> None:
        self.root = root.resolve()
        self.formal = formal
        self._issued: dict[int, tuple[SqlScript, Path]] = {}

    def load(self, path: Path) -> SqlScript:
        path = path.resolve()
        if not path.is_relative_to(self.root) or path.suffix != ".sql":
            raise ValidationError("SQL path must be inside registry")
        metadata_path = path.with_suffix(".yaml")
        if not metadata_path.resolve().is_relative_to(self.root):
            raise ValidationError("Metadata path must be inside registry")
        data = load_yaml(metadata_path)
        expected = {
            "name",
            "database_type",
            "risk",
            "timeout_seconds",
            "max_rows",
            "reviewed",
            "description",
        }
        optional = {"feature", "version_families", "runtime_variants"}
        if not isinstance(data, dict) or not expected <= set(data) or set(data) - expected - optional:
            raise ValidationError("SQL metadata fields are invalid")
        for key in ("name", "description", "database_type", "risk"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise ValidationError("SQL metadata strings must be nonempty")
        for key in ("timeout_seconds", "max_rows"):
            if type(data[key]) is not int or data[key] <= 0:
                raise ValidationError("SQL limits must be positive integers")
        if data["risk"] != "READ_ONLY" or type(data["reviewed"]) is not bool:
            raise ValidationError("Only READ_ONLY risk and boolean reviewed are supported")
        for key in ("feature",):
            if key in data and (not isinstance(data[key], str) or not data[key].strip()):
                raise ValidationError("SQL feature metadata must be a nonempty string")
        for key in ("version_families", "runtime_variants"):
            if key in data and (not isinstance(data[key], list)
                                or any(not isinstance(item, str) or not item for item in data[key])
                                or len(set(data[key])) != len(data[key])):
                raise ValidationError("SQL applicability metadata must be unique string lists")
        data = {**data, "version_families": tuple(data.get("version_families", ())),
                "runtime_variants": tuple(data.get("runtime_variants", ()))}
        if self.formal and not data["reviewed"]:
            raise ValidationError("Formal SQL must be reviewed")
        try:
            metadata = SqlMetadata(**{**data, "database_type": DatabaseType(data["database_type"])})
        except ValueError as exc:
            raise ValidationError("Invalid SQL database_type") from exc
        if path.parent.name != metadata.database_type.value or path.stem != metadata.name:
            raise ValidationError("SQL metadata must match directory and filename")
        text = path.read_text(encoding="utf-8")
        check_sql(text)
        script = SqlScript(metadata, text)
        self._issued[id(script)] = (script, path)
        return script

    def get(self, name: str, database_type: DatabaseType = DatabaseType.SQLSERVER) -> SqlScript:
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in name):
            raise ValidationError("Invalid script name")
        return self.load(self.root / database_type.value / f"{name}.sql")

    def authorize(self, script: SqlScript, database_type: DatabaseType) -> None:
        issued = self._issued.get(id(script))
        if not isinstance(script, SqlScript) or issued is None or issued[0] is not script:
            raise ValidationError("Only scripts issued by this registry may execute")
        if script.metadata.database_type != database_type or not script.metadata.reviewed:
            raise ValidationError("Execution requires reviewed SQL for the target database")
        current = self.load(issued[1])
        if current != script:
            raise ValidationError("SQL or metadata changed after registration")
        check_sql(script.text)

    def validate(self) -> list[SqlScript]:
        if not self.root.is_dir():
            raise ValidationError("SQL registry directory is missing")
        for metadata in self.root.rglob("*.yaml"):
            if not metadata.with_suffix(".sql").is_file():
                raise ValidationError("Orphan SQL metadata")
        return [self.load(path) for path in sorted(self.root.rglob("*.sql"))]
