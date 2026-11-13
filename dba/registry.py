from __future__ import annotations

import hashlib
import json
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from dba.core import DatabaseType
from dba.inventory import UniqueKeyLoader, ValidationError
from dba.security import check_sql

if TYPE_CHECKING:
    from dba.sql_timeout import SqlDeadline

MAX_SQL_SOURCE_BYTES = 1024 * 1024
MAX_METADATA_SOURCE_BYTES = 64 * 1024
MAX_SQL_TIMEOUT_SECONDS = 60
MAX_SQL_ROWS = 10_000
DEFAULT_MAX_RESULT_BYTES = 16 * 1024 * 1024


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
    profile_role: str | None = None
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES


@dataclass(frozen=True)
class SqlScript:
    metadata: SqlMetadata
    text: str


class FetchCursor(Protocol):
    def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...


@dataclass(frozen=True)
class BoundedFetch:
    rows: tuple[Sequence[Any], ...]
    row_limit_reached: bool
    byte_limit_reached: bool
    result_bytes: int

    @property
    def truncated(self) -> bool:
        return self.row_limit_reached or self.byte_limit_reached


def _value_payload_bytes(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_value_payload_bytes(key) + _value_payload_bytes(item)
                   for key, item in value.items())
    if isinstance(value, Sequence):
        return sum(_value_payload_bytes(item) for item in value)
    if isinstance(value, (bool, int, float, Decimal, date, datetime)):
        return len(str(value).encode("utf-8", errors="replace"))
    return len(str(value).encode("utf-8", errors="replace"))


def fetch_bounded_rows(
    cursor: FetchCursor, max_rows: int, max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    *, deadline: SqlDeadline | None = None,
    before_fetch: Callable[[float], None] | None = None,
) -> BoundedFetch:
    """Read at most one row per fetch and stop at the row or payload-byte budget.

    The byte count covers returned cell payloads after driver decoding; it does not
    claim to bound a single value already materialized internally by a driver.
    """
    if (type(max_rows) is not int or not 1 <= max_rows <= MAX_SQL_ROWS
            or type(max_result_bytes) is not int
            or not 1 <= max_result_bytes <= DEFAULT_MAX_RESULT_BYTES):
        raise ValueError("Invalid reviewed SQL result limits")

    rows: list[Sequence[Any]] = []
    result_bytes = 0
    byte_limit_reached = False
    while len(rows) < max_rows:
        if deadline is not None:
            remaining = deadline.require_remaining("Fetch")
            if before_fetch is not None:
                before_fetch(remaining)
                deadline.require_remaining("Fetch")
        batch = cursor.fetchmany(1)
        if deadline is not None:
            deadline.require_remaining("Fetch")
        if not batch:
            break
        row = batch[0]
        row_bytes = _value_payload_bytes(row)
        if row_bytes > max_result_bytes - result_bytes:
            byte_limit_reached = True
            break
        rows.append(row)
        result_bytes += row_bytes

    return BoundedFetch(
        rows=tuple(rows),
        row_limit_reached=len(rows) >= max_rows,
        byte_limit_reached=byte_limit_reached,
        result_bytes=result_bytes,
    )


class SqlScriptRegistry:
    def __init__(self, root: Path, formal: bool = True) -> None:
        self.root = root.resolve()
        self.formal = formal
        self._issued: dict[
            int, tuple[weakref.ReferenceType[SqlScript], Path, str]
        ] = {}

    @staticmethod
    def _bounded_read(path: Path, limit: int, label: str) -> bytes:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValidationError(f"SQL Registry {label} exceeds the supported size limit")
        return raw

    @staticmethod
    def _digest(script: SqlScript) -> str:
        metadata = script.metadata
        contract = {
            "name": metadata.name,
            "database_type": metadata.database_type.value,
            "risk": metadata.risk,
            "timeout_seconds": metadata.timeout_seconds,
            "max_rows": metadata.max_rows,
            "max_result_bytes": metadata.max_result_bytes,
            "reviewed": metadata.reviewed,
            "description": metadata.description,
            "feature": metadata.feature,
            "version_families": metadata.version_families,
            "runtime_variants": metadata.runtime_variants,
            "profile_role": metadata.profile_role,
        }
        digest = hashlib.sha256()
        digest.update(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(script.text.encode("utf-8"))
        return digest.hexdigest()

    def _read(self, path: Path) -> tuple[SqlScript, Path, str]:
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise ValidationError("SQL Registry path cannot be resolved") from exc
        if not path.is_relative_to(self.root) or path.suffix != ".sql":
            raise ValidationError("SQL path must be inside registry")

        metadata_path = path.with_suffix(".yaml")
        try:
            metadata_path = metadata_path.resolve(strict=True)
        except OSError as exc:
            raise ValidationError("SQL metadata path cannot be resolved") from exc
        if not metadata_path.is_relative_to(self.root):
            raise ValidationError("Metadata path must be inside registry")

        metadata_raw = self._bounded_read(metadata_path, MAX_METADATA_SOURCE_BYTES, "metadata")
        sql_raw = self._bounded_read(path, MAX_SQL_SOURCE_BYTES, "source")
        try:
            data = yaml.load(metadata_raw.decode("utf-8"), Loader=UniqueKeyLoader)
        except (yaml.YAMLError, TypeError, UnicodeError, RecursionError) as exc:
            raise ValidationError("Invalid SQL metadata document") from exc

        expected = {
            "name",
            "database_type",
            "risk",
            "timeout_seconds",
            "max_rows",
            "reviewed",
            "description",
        }
        optional = {
            "feature", "version_families", "runtime_variants", "profile_role", "max_result_bytes",
        }
        if not isinstance(data, dict) or not expected <= set(data) or set(data) - expected - optional:
            raise ValidationError("SQL metadata fields are invalid")
        for key in ("name", "description", "database_type", "risk"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise ValidationError("SQL metadata strings must be nonempty")
        if (type(data["timeout_seconds"]) is not int
                or not 1 <= data["timeout_seconds"] <= MAX_SQL_TIMEOUT_SECONDS):
            raise ValidationError("SQL timeout must be an integer in 1..60")
        if type(data["max_rows"]) is not int or not 1 <= data["max_rows"] <= MAX_SQL_ROWS:
            raise ValidationError("SQL row limit must be an integer in 1..10000")
        if "max_result_bytes" in data and (
            type(data["max_result_bytes"]) is not int
            or not 1 <= data["max_result_bytes"] <= DEFAULT_MAX_RESULT_BYTES
        ):
            raise ValidationError("SQL result byte limit is outside the supported range")
        data.setdefault("max_result_bytes", DEFAULT_MAX_RESULT_BYTES)
        if data["risk"] != "READ_ONLY" or type(data["reviewed"]) is not bool:
            raise ValidationError("Only READ_ONLY risk and boolean reviewed are supported")
        if "feature" in data and (not isinstance(data["feature"], str) or not data["feature"].strip()):
            raise ValidationError("SQL feature metadata must be a nonempty string")
        for key in ("version_families", "runtime_variants"):
            if key in data and (not isinstance(data[key], list)
                                or any(not isinstance(item, str) or not item for item in data[key])
                                or len(set(data[key])) != len(data[key])):
                raise ValidationError("SQL applicability metadata must be unique string lists")
        if "profile_role" in data and data["profile_role"] not in {"required", "optional"}:
            raise ValidationError("SQL profile_role must be required or optional")
        if data.get("profile_role") and data.get("feature") != "disk-growth":
            raise ValidationError("SQL profile_role is currently supported only for disk-growth SQL")
        try:
            text = sql_raw.decode("utf-8")
        except UnicodeError as exc:
            raise ValidationError("SQL source must use UTF-8 encoding") from exc
        data = {
            **data,
            "version_families": tuple(data.get("version_families", ())),
            "runtime_variants": tuple(data.get("runtime_variants", ())),
        }
        if self.formal and not data["reviewed"]:
            raise ValidationError("Formal SQL must be reviewed")
        try:
            metadata = SqlMetadata(**{**data, "database_type": DatabaseType(data["database_type"])})
        except ValueError as exc:
            raise ValidationError("Invalid SQL database_type") from exc
        if path.parent.name != metadata.database_type.value or path.stem != metadata.name:
            raise ValidationError("SQL metadata must match directory and filename")
        check_sql(text, metadata.database_type)
        script = SqlScript(metadata, text)
        return script, path, self._digest(script)

    def _issue(self, script: SqlScript, path: Path, digest: str) -> None:
        key = id(script)
        registry_ref = weakref.ref(self)

        def discard(reference: weakref.ReferenceType[SqlScript]) -> None:
            registry = registry_ref()
            if registry is None:
                return
            current = registry._issued.get(key)
            if current is not None and current[0] is reference:
                registry._issued.pop(key, None)

        self._issued[key] = (weakref.ref(script, discard), path, digest)

    def load(self, path: Path) -> SqlScript:
        script, resolved_path, digest = self._read(path)
        self._issue(script, resolved_path, digest)
        return script

    def get(self, name: str, database_type: DatabaseType = DatabaseType.SQLSERVER) -> SqlScript:
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in name):
            raise ValidationError("Invalid script name")
        return self.load(self.root / database_type.value / f"{name}.sql")

    def authorize(self, script: SqlScript, database_type: DatabaseType) -> None:
        if not isinstance(script, SqlScript):
            raise ValidationError("Only scripts issued by this registry may execute")
        issued = self._issued.get(id(script))
        if issued is None or issued[0]() is not script:
            raise ValidationError("Only scripts issued by this registry may execute")
        if script.metadata.database_type != database_type or not script.metadata.reviewed:
            raise ValidationError("Execution requires reviewed SQL for the target database")
        current, _, current_digest = self._read(issued[1])
        if current_digest != issued[2] or current != script:
            raise ValidationError("SQL or metadata changed after registration")
        check_sql(script.text, database_type)

    def validate(self) -> list[SqlScript]:
        if not self.root.is_dir():
            raise ValidationError("SQL registry directory is missing")
        for metadata in self.root.rglob("*.yaml"):
            if not metadata.with_suffix(".sql").is_file():
                raise ValidationError("Orphan SQL metadata")
        return [self.load(path) for path in sorted(self.root.rglob("*.sql"))]
