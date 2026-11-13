from __future__ import annotations

from pathlib import Path

import pytest

from dba.connection import resolve_connection_settings
from dba.core import DatabaseType
from dba.inventory import ValidationError
from dba.registry import MAX_SQL_TIMEOUT_SECONDS, SqlScriptRegistry, fetch_bounded_rows
from dba.settings import (
    MySQLSettings,
    OracleSettings,
    PostgreSQLSettings,
    SQLServerSettings,
    apply_diagnostic_settings,
    apply_diagnostic_threshold,
    validate_diagnostic_settings,
)
from dba.sql_timeout import (
    DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS,
    MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS,
    SqlDeadline,
    SqlDeadlineExpired,
    effective_sql_timeout,
    validate_diagnostic_sql_timeout,
)

ROOT = Path(__file__).resolve().parents[1]
ENGINE_SETTINGS = (SQLServerSettings, PostgreSQLSettings, MySQLSettings, OracleSettings)
SECTION = {SQLServerSettings: "sqlserver", PostgreSQLSettings: "postgresql",
           MySQLSettings: "mysql", OracleSettings: "oracle"}
CAP_FIELD = {SQLServerSettings: "query_timeout", PostgreSQLSettings: "query_timeout",
             MySQLSettings: "query_timeout", OracleSettings: "call_timeout"}


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "runtime.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("script,global_,cap,expected", [
    (10, 30, None, 10),   # shorter reviewed script limit is preserved
    (5, 30, None, 5),
    (15, 30, None, 15),   # previously capped by the 10 s engine default
    (60, 30, None, 30),   # global value is the ceiling
    (10, 6, None, 6),
    (10, 30, 7, 7),       # configured legacy cap may only shorten
    (10, 30, 50, 10),
    (60, 60, 86400, 60),
])
def test_effective_timeout_is_min_of_script_global_and_optional_cap(
    script: int, global_: int, cap: int | None, expected: int,
) -> None:
    assert effective_sql_timeout(script, global_, cap) == expected


@pytest.mark.parametrize("value", [0, -1, MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS + 1, True, False, "30", 1.5, None])
def test_global_timeout_validation_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError, match="diagnostic_sql_timeout_seconds must be an integer in 1..60"):
        validate_diagnostic_sql_timeout(value)


@pytest.mark.parametrize("script,cap", [(0, None), (MAX_SQL_TIMEOUT_SECONDS + 1, None), (True, None),
                                        (10, 0), (10, True), (10, "7")])
def test_effective_timeout_rejects_invalid_script_or_cap(script: object, cap: object) -> None:
    with pytest.raises(ValidationError):
        effective_sql_timeout(script, 30, cap)  # type: ignore[arg-type]


def test_global_ceiling_never_exceeds_registry_bound() -> None:
    assert DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS == 30
    assert MAX_DIAGNOSTIC_SQL_TIMEOUT_SECONDS == MAX_SQL_TIMEOUT_SECONDS


@pytest.mark.parametrize("cls", ENGINE_SETTINGS)
def test_engine_defaults_use_global_30_seconds_without_legacy_cap(cls: type) -> None:
    settings = cls()
    assert settings.diagnostic_sql_timeout_seconds == 30
    assert getattr(settings, CAP_FIELD[cls]) is None
    assert settings.effective_sql_timeout(10) == 10
    assert settings.effective_sql_timeout(15) == 15
    assert settings.effective_sql_timeout(60) == 30


@pytest.mark.parametrize("cls", ENGINE_SETTINGS)
@pytest.mark.parametrize("field,value", [("diagnostic_sql_timeout_seconds", 0),
                                         ("diagnostic_sql_timeout_seconds", 61),
                                         ("diagnostic_sql_timeout_seconds", True),
                                         ("cap", 0), ("cap", True), ("cap", "10")])
def test_engine_settings_reject_invalid_timeouts(cls: type, field: str, value: object) -> None:
    name = CAP_FIELD[cls] if field == "cap" else field
    with pytest.raises(ValidationError):
        cls(**{name: value})


@pytest.mark.parametrize("cls", ENGINE_SETTINGS)
def test_shared_diagnostics_section_sets_global_for_every_engine(tmp_path: Path, cls: type) -> None:
    path = write(tmp_path, f"{SECTION[cls]}: {{}}\ndiagnostics:\n  diagnostic_sql_timeout_seconds: 12\n")
    settings = apply_diagnostic_settings(cls.load(path), path)
    assert isinstance(settings, cls)
    assert settings.diagnostic_sql_timeout_seconds == 12
    assert settings.effective_sql_timeout(15) == 12 and settings.effective_sql_timeout(5) == 5
    assert settings.long_running_seconds == cls().long_running_seconds
    validate_diagnostic_settings(path)


def test_diagnostics_section_combines_slow_threshold_and_timeout(tmp_path: Path) -> None:
    path = write(tmp_path, "sqlserver: {}\ndiagnostics:\n  slow:\n    threshold_seconds: 17\n"
                           "  diagnostic_sql_timeout_seconds: 20\n")
    settings = apply_diagnostic_threshold(SQLServerSettings.load(path), path)
    assert isinstance(settings, SQLServerSettings)
    assert (settings.long_running_seconds, settings.diagnostic_sql_timeout_seconds) == (17, 20)


@pytest.mark.parametrize("diagnostics,error", [
    ("diagnostics: {}\n", "diagnostics may contain only"),
    ("diagnostics:\n  sql_timeout: 10\n", "diagnostics may contain only"),
    ("diagnostics:\n  diagnostic_sql_timeout_seconds: 0\n", "integer in 1..60"),
    ("diagnostics:\n  diagnostic_sql_timeout_seconds: 61\n", "integer in 1..60"),
    ("diagnostics:\n  diagnostic_sql_timeout_seconds: '30'\n", "integer in 1..60"),
    ("diagnostics:\n  diagnostic_sql_timeout_seconds: true\n", "integer in 1..60"),
    ("diagnostics:\n  diagnostic_sql_timeout_seconds: null\n", "integer in 1..60"),
])
def test_invalid_diagnostics_section_is_rejected(tmp_path: Path, diagnostics: str, error: str) -> None:
    path = write(tmp_path, "sqlserver: {}\n" + diagnostics)
    with pytest.raises(ValidationError, match=error):
        validate_diagnostic_settings(path)
    with pytest.raises(ValidationError, match=error):
        apply_diagnostic_settings(SQLServerSettings(), path)


@pytest.mark.parametrize("cls", ENGINE_SETTINGS)
def test_global_timeout_cannot_be_redefined_per_engine(tmp_path: Path, cls: type) -> None:
    path = write(tmp_path, f"{SECTION[cls]}:\n  diagnostic_sql_timeout_seconds: 45\n")
    with pytest.raises(ValidationError, match="set it only under diagnostics"):
        cls.load(path)


@pytest.mark.parametrize("db_type,cls", [
    (DatabaseType.SQLSERVER, SQLServerSettings), (DatabaseType.POSTGRESQL, PostgreSQLSettings),
    (DatabaseType.MYSQL, MySQLSettings), (DatabaseType.ORACLE, OracleSettings),
])
def test_inventory_query_timeout_remains_a_stricter_cap_only(db_type: DatabaseType, cls: type) -> None:
    settings = resolve_connection_settings(db_type, {"query_timeout": 15}, cls())
    assert getattr(settings, CAP_FIELD[cls]) == 15
    assert settings.effective_sql_timeout(10) == 10
    assert settings.effective_sql_timeout(60) == 15
    with pytest.raises(Exception, match="Unknown"):
        resolve_connection_settings(db_type, {"diagnostic_sql_timeout_seconds": 45}, cls())


def test_default_settings_preserve_every_reviewed_script_limit() -> None:
    scripts = SqlScriptRegistry(ROOT / "sql").validate()
    assert scripts
    for cls in ENGINE_SETTINGS:
        settings = apply_diagnostic_settings(cls(), None)
        for script in scripts:
            limit = script.metadata.timeout_seconds
            assert settings.effective_sql_timeout(limit) == min(limit, 30) <= limit


def test_deadline_is_absolute_and_shared_across_execute_and_fetch() -> None:
    clock = FakeClock()
    deadline = SqlDeadline.start(10, clock=clock)
    assert deadline.expires_at == 110.0 and deadline.remaining() == 10.0
    clock.now = 106.0                       # execute consumed 6 s
    assert deadline.remaining() == 4.0 and deadline.elapsed() == 6.0 and not deadline.expired()
    clock.now = 109.5                       # later fetch calls do not regain the full timeout
    assert deadline.remaining() == pytest.approx(0.5) and not deadline.expired()
    clock.now = 110.0
    assert deadline.expired() and deadline.remaining() == 0.0
    clock.now = 125.0
    assert deadline.expired() and deadline.remaining() == 0.0 and deadline.elapsed() == 25.0
    assert deadline.expires_at == 110.0


def test_deadline_validates_timeout() -> None:
    clock = FakeClock()
    for value in (0, 61, True, "5"):
        with pytest.raises(ValidationError):
            SqlDeadline.start(value, clock=clock)  # type: ignore[arg-type]


class TimedCursor:
    def __init__(self, clock: FakeClock, rows: list[tuple[int, ...]], costs: list[float]) -> None:
        self.clock = clock
        self.rows = rows
        self.costs = costs
        self.calls = 0

    def fetchmany(self, size: int) -> list[tuple[int, ...]]:
        self.calls += 1
        self.clock.now += self.costs[self.calls - 1]
        row = self.rows[self.calls - 1:self.calls]
        return row


def test_execute_consumption_and_repeated_fetches_share_one_budget() -> None:
    clock = FakeClock()
    deadline = SqlDeadline.start(8, clock=clock)
    clock.now += 3  # Execute consumed part of the same SQL budget.
    cursor = TimedCursor(clock, [(1,), (2,), (3,)], [1, 1, 1])
    remaining_before_fetch: list[float] = []

    result = fetch_bounded_rows(
        cursor, 3, deadline=deadline, before_fetch=remaining_before_fetch.append,
    )

    assert result.rows == ((1,), (2,), (3,))
    assert remaining_before_fetch == [5, 4, 3]
    assert deadline.remaining() == 2
    assert cursor.calls == 3


def test_execute_exhausting_deadline_stops_before_first_fetch() -> None:
    clock = FakeClock()
    deadline = SqlDeadline.start(5, clock=clock)
    clock.now += 5  # Execute used the whole budget.
    cursor = TimedCursor(clock, [(1,)], [0])

    with pytest.raises(SqlDeadlineExpired, match="Fetch"):
        fetch_bounded_rows(cursor, 1, deadline=deadline)

    assert cursor.calls == 0


def test_accumulated_fetch_batches_stop_when_the_original_deadline_expires() -> None:
    clock = FakeClock()
    deadline = SqlDeadline.start(5, clock=clock)
    cursor = TimedCursor(clock, [(1,), (2,), (3,), (4,)], [1.25, 1.25, 2.5, 0])
    remaining_before_fetch: list[float] = []

    with pytest.raises(SqlDeadlineExpired, match="Fetch"):
        fetch_bounded_rows(
            cursor, 4, deadline=deadline, before_fetch=remaining_before_fetch.append,
        )

    assert remaining_before_fetch == [5, 3.75, 2.5]
    assert cursor.calls == 3
    assert deadline.expired()
