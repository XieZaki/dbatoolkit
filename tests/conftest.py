from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def credential_test_endpoint(request: pytest.FixtureRequest, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    import dba.credential_session as session
    from dba.credentials import KeyringCredentialStore

    # No suite member may discover the real user's default file/keyring/session.
    for variable in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    monkeypatch.setattr(KeyringCredentialStore, "_load_keyring", lambda self: self._keyring)

    endpoint = (rf"\\.\pipe\dba-unit-{uuid.uuid4().hex}" if os.name == "nt"
                else str(tmp_path / "unused-agent.sock"))
    monkeypatch.setattr(session, "_endpoint", lambda: endpoint)

    def no_default_agent(_self: session.CredentialSessionClient) -> None:
        pytest.fail("Unit test must inject its own IPC agent")

    monkeypatch.setattr(session.CredentialSessionClient, "ensure_agent", no_default_agent)


@pytest.fixture
def isolated_credential_ipc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            credential_test_endpoint: None) -> Iterator[str]:
    """Real IPC with a unique endpoint in both processes; never probe the user agent."""
    import dba.credential_session as session

    endpoint = (rf"\\.\pipe\dba-test-{uuid.uuid4().hex}" if os.name == "nt"
                else str(tmp_path / "agent.sock"))
    monkeypatch.setattr(session, "_endpoint", lambda: endpoint)
    bootstrap = (
        "import os,sys; from pathlib import Path; import dba.credential_session as s; "
        "endpoint=sys.argv[1]; original=s._windows_identity; "
        "s._windows_identity=lambda: (original()[0], endpoint); "
        "s._unix_socket_path=lambda: Path(endpoint); s.serve_agent()"
    )
    agent = subprocess.Popen([sys.executable, "-c", bootstrap, endpoint],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)
    client = session.CredentialSessionClient()

    def existing_agent_only(_self: session.CredentialSessionClient) -> None:
        assert client.request("STATUS") is not None, "isolated test agent stopped"

    monkeypatch.setattr(session.CredentialSessionClient, "ensure_agent", existing_agent_only)
    try:
        deadline = time.monotonic() + 8
        while client.request("STATUS") is None:
            assert agent.poll() is None, "isolated test agent exited before becoming ready"
            assert time.monotonic() < deadline, "isolated test agent startup timed out"
            time.sleep(0.05)
        yield endpoint
    finally:
        if agent.poll() is None:
            agent.terminate()
        agent.communicate(timeout=10)

_DATABASE_MARKERS = {
    "test_mysql": "mysql",
    "test_mysql_privileges": "mysql",
    "test_postgresql": "postgresql",
    "test_sqlserver": "sqlserver",
    "test_oracle": "oracle",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give existing flat tests a default layer without relocating the suite."""
    for item in items:
        path = Path(str(item.path))
        module_name = path.stem
        database_marker = _DATABASE_MARKERS.get(module_name)
        if database_marker:
            item.add_marker(getattr(pytest.mark, database_marker))

        if not any(
            list(item.iter_markers(name)) for name in ("unit", "integration", "system")
        ):
            item.add_marker(pytest.mark.unit)
