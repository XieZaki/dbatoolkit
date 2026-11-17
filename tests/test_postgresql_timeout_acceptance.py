"""Offline checks of the PostgreSQL acceptance harness. No PostgreSQL server is used."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_postgresql import PREFLIGHT, FakeConnection, FakeCursor

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance
from dba.credentials import Credential
from dba.errors import ToolkitError
from dba.inventory import ValidationError
from dba.postgresql import PostgreSQLAdapter
from dba.registry import SqlScriptRegistry
from dba.settings import PostgreSQLSettings
from dba.sql_timeout import SqlDeadlineExpired
from scripts.acceptance import verify_postgresql_timeout as harness

SECRET = ("acceptance-secret-password", "acceptance-secret-user", "acceptance-secret-alias")
SET_SQL = "SELECT pg_catalog.set_config('statement_timeout', %s, false)"


class DriverError(Exception):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = state
        self.diag = SimpleNamespace(message_primary=message)


class Cursor(FakeCursor):
    def execute(self, sql: str, params: Any = None, **kwargs: Any) -> Any:
        self._execute_send(sql)
        super().execute(sql, params)
        self.pgresult = SimpleNamespace(get_value=lambda *_: params[0].encode("ascii"))
        return self

    def _execute_send(self, sql: str) -> None:
        pass


class Connection(FakeConnection):
    def __init__(self, registry: SqlScriptRegistry, pid: int) -> None:
        super().__init__({**{script.text: script.metadata.name for script in registry.validate()},
                          SET_SQL: "set_config"})
        self.cursor_factory: Any = Cursor
        self.results["preflight"] = [{**PREFLIGHT, "server_version_num": 160015}]
        self.results["timeout_identity"] = [{
            "backend_pid": pid, "database_name": "lab_database", "version_num": 160015,
            "statement_timeout": "2s", "backend_start": "2026-11-15T00:00:00+00:00",
            "elevated_membership": False,
        }]
        self.results["timeout_normal"] = [{"probe_value": 1}]

    def cursor(self) -> Cursor:
        cursor: Cursor = self.cursor_factory(self)
        self.cursors.append(cursor)
        return cursor

    def execute(self, sql: str, params: Any = None) -> Cursor:
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor


class Driver:
    def __init__(self, *connections: Connection) -> None:
        self.connections = iter(connections)
        self.credentials: list[tuple[str, str]] = []

    def connect(self, **kwargs: Any) -> Connection:
        self.credentials.append((kwargs["user"], kwargs["password"]))
        assert kwargs["dbname"] == "lab_database"
        return next(self.connections)


def asset() -> DatabaseInstance:
    return DatabaseInstance("pg16", "Lab", harness.ENGINE, "127.0.0.1", 5432,
                            SECRET[2], database="lab_database", environment="test")


def adapt(registry: SqlScriptRegistry, driver: Driver) -> PostgreSQLAdapter:
    return PostgreSQLAdapter(
        asset(), harness._CachedCredentialProvider(Credential(SECRET[1], SECRET[0])),
        registry, PostgreSQLSettings(diagnostic_sql_timeout_seconds=2), driver=driver,
    )


def test_fixture_safety_and_digest(tmp_path: Path) -> None:
    root = tmp_path / "sql"
    registry = harness.stage_registry(root, 321)
    assert len(registry.validate()) == len(PostgreSQLAdapter.REQUIRED) + 4
    script = registry.get("timeout_observe", harness.ENGINE)
    assert "WHERE pid = 321" in script.text
    assert "query" not in script.text
    registry.authorize(script, harness.ENGINE)
    (root / "postgresql/timeout_observe.sql").write_text(script.text + "\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        registry.authorize(script, harness.ENGINE)
    with pytest.raises(ToolkitError) as exc:
        harness.stage_registry(tmp_path / "bad", -123)
    assert exc.value.error_type == "IdentityRejected"


def test_credential_auto_uses_os_without_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dba import encrypted_credentials

    credential = Credential(SECRET[1], SECRET[0])
    hits = []

    class OsStore:
        backend_name = "Windows Credential Manager"

        def available(self) -> bool:
            return True

        def get(self, alias: str) -> Credential:
            hits.append(alias)
            return credential

    def forbidden_session() -> None:
        pytest.fail("An OS credential must not require an encrypted Session")

    monkeypatch.setattr(harness, "KeyringCredentialStore", OsStore)
    monkeypatch.setattr(encrypted_credentials, "CredentialSessionClient", forbidden_session)
    provider, source = harness.resolve_provider(asset(), "auto", tmp_path / "unused")
    assert source == "Windows Credential Manager"
    assert provider.get_credential(SECRET[2]) is provider.get_credential(SECRET[2]) is credential
    assert hits == [SECRET[2]]


@pytest.mark.parametrize(("failure", "outcome", "cancellation", "state"), [
    (DriverError("57014", "canceling statement due to statement timeout"),
     "SERVER_STATEMENT_TIMEOUT", "CANCELLED_CONFIRMED", "57014"),
    (DriverError("57014", "query cancelled"), "SERVER_CANCEL_OTHER_OR_UNKNOWN",
     "CANCELLED_CONFIRMED", "57014"),
    (SqlDeadlineExpired("Execute"), "CLIENT_DEADLINE", "CANCEL_STATUS_UNKNOWN", None),
    (TimeoutError("timed out"), "CLIENT_SOCKET_TIMEOUT", "CANCEL_STATUS_UNKNOWN", None),
    (DriverError("08006", "connection lost"), "CONNECTION_LOST", "CANCEL_STATUS_UNKNOWN", "08006"),
])
def test_execute_trace_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    sql_deadline_clock: Any,
                                    failure: Exception, outcome: str,
                                    cancellation: str, state: str | None) -> None:
    registry = harness.stage_registry(tmp_path / "sql")
    target_old, fresh, watcher = (Connection(registry, pid) for pid in (111, 222, 333))
    target_old.failures["timeout_execute"] = failure
    driver = Driver(target_old, watcher, fresh)
    target = adapt(registry, driver)
    observer = PostgreSQLAdapter(target.instance, target.provider, registry, target.settings, driver=driver)

    def observed(*args: Any) -> dict[str, Any]:
        return {"status": "ABSENT", "active_observed": True, "query_stop_confirmed": False}

    monkeypatch.setattr(harness, "observe", observed)
    with harness.capture_audit(SECRET) as audit, target, observer:
        before = harness.identity(target)
        report = harness.exercise(target, observer, tmp_path, audit, SECRET, before)

    assert report["outcome"] == outcome
    assert report["select_attempts"] == 1
    assert target_old.executed.count("timeout_execute") == 1
    assert report["driver_closed"] and report["adapter_detached"] and report["preflight_required"]
    assert report["partial_result_discarded"] and report["baseline_retained"]
    assert report["recovery"] == {
        "status": "PASS", "preflight": True, "new_backend_pid": 222, "independent_select": True,
    }
    assert driver.credentials == [(SECRET[1], SECRET[0])] * 3
    assert report["server_statement_timeout"] == "500ms"
    assert report["server_statement_timeout_ms"] == 500
    assert report["trace"][0]["operation"] == "set_config"
    assert (report["trace"][0]["start"]["deadline_start"]
            == report["trace"][1]["libpq_dispatch"]["deadline_start"])
    assert report["normal_trace"][1]["operation"] == "fetch"
    error = report["timeout_result"]["database_error"]
    assert error["sqlstate"] == state
    assert error["timeout"]["cancellation_status"] == cancellation
    assert error["timeout"]["connection_status"] == "DISCARDED"
    assert error["timeout"]["execution_phase"] == "Execute"
    assert report["fetch_network_timeout"]["status"] == "BLOCKED"
    assert all(report["output_parity"].values())
    assert report["status"] == ("PASS" if outcome == "SERVER_STATEMENT_TIMEOUT" else "PARTIAL")
    for artifact in tmp_path.glob("timeout_*.*"):
        assert all(secret not in artifact.read_text(encoding="utf-8") for secret in SECRET)


def test_observation_pid_reuse_and_permissions(tmp_path: Path) -> None:
    registry = harness.stage_registry(tmp_path / "sql", 111)
    connection = Connection(registry, 333)
    connection.failures["timeout_observe"] = DriverError(
        "42501", f"permission denied for view pg_stat_activity; password={SECRET[0]}",
    )
    observer = adapt(registry, Driver(connection))
    expected = {"backend_pid": 111, "backend_start": "original"}
    with harness.capture_audit(SECRET), observer:
        denied = harness.query(observer, "timeout_observe")
        evidence = harness.observe(observer, expected, Future(), denied)
    assert evidence["status"] == "UNKNOWN"
    assert evidence["error"]["sqlstate"] == "42501"
    assert evidence["query_stop_confirmed"] is False
    assert SECRET[0] not in str(evidence)
    template = replace(denied, success=True, row_count=1)
    row = {**expected, "state": "active", "wait_event": "PgSleep"}
    assert harness.visibility(replace(template, rows=(row,)), expected) == "ACTIVE_OBSERVED"
    assert harness.visibility(replace(template, rows=({**row, "backend_start": "new"},)), expected) == (
        "CONNECTION_ID_REBOUND"
    )
    assert harness.visibility(replace(template, rows=({**row, "state": "idle"},)), expected) == "PRESENT"
    assert harness.visibility(replace(template, rows=(), row_count=0), expected) == "ABSENT"


def test_observation_keeps_activity_separate_from_final_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = harness.stage_registry(tmp_path / "sql", 111)
    observer_connection = Connection(registry, 333)
    observer = adapt(registry, Driver(observer_connection))
    expected = {"backend_pid": 111, "backend_start": "original"}
    pending: Future[CollectorResult] = Future()
    watcher_row = {**expected, "state": "idle", "wait_event": None}
    observer_connection.results["timeout_observe"] = [watcher_row]
    with harness.capture_audit(SECRET), observer:
        initial = harness.query(observer, "timeout_observe")

        def finish(_seconds: float) -> None:
            observer_connection.results["timeout_observe"] = []
            pending.set_result(initial)

        observer_connection.results["timeout_observe"] = [
            {**watcher_row, "state": "active", "wait_event": "PgSleep"},
        ]
        monkeypatch.setattr(harness.time, "sleep", finish)
        evidence = harness.observe(observer, expected, pending, initial)
    assert evidence["status"] == "ABSENT"
    assert evidence["samples"][0]["status"] == "ACTIVE_OBSERVED"
    assert evidence["active_observed"] is True
    assert evidence["query_stop_confirmed"] is False


def test_output_redacts_credential_material_in_all_formats(tmp_path: Path) -> None:
    registry = harness.stage_registry(tmp_path / "sql")
    old = Connection(registry, 111)
    old.failures["timeout_execute"] = DriverError(
        "57014", f"canceling statement due to statement timeout, user={SECRET[1]} "
        f"password={SECRET[0]} alias={SECRET[2]} postgresql://{SECRET[1]}:{SECRET[0]}@localhost/lab",
    )
    target = adapt(registry, Driver(old))
    with harness.capture_audit(SECRET) as audit, target:
        normal = harness.query(target, "timeout_normal")
        failed = harness.query(target, "timeout_execute")
        assert all(harness.render_evidence(tmp_path, target, normal, failed, audit.events, SECRET).values())
    for artifact in tmp_path.glob("timeout_execute.*"):
        text = artifact.read_text(encoding="utf-8")
        assert "57014" in text
        assert all(secret not in text for secret in SECRET)
        assert "postgresql://" not in text
