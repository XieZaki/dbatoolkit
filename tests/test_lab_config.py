from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import pickle
import subprocess
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

import pytest

from dba.lab_config import (
    LabConfigError,
    LabConnectionError,
    connect_with_lab_secret,
    read_lab_connection_profile,
    read_lab_connection_secret,
    read_lab_database_config,
)

SYNTHETIC_PASSWORD = "SYNTHETIC_PASSWORD_DO_NOT_LEAK"
SYNTHETIC_TOKEN = "SYNTHETIC_TOKEN_DO_NOT_LEAK"
DATABASES = ("mysql", "pg", "oracle", "sqlserver")


def _safe_check(condition: bool, message: str) -> None:
    """Fail without asking pytest to render values that may contain secrets."""
    if not condition:
        raise AssertionError(message)


def _fixture_source(
    *,
    password: str = SYNTHETIC_PASSWORD,
    extra_field: str | None = None,
    port_expression: str | None = None,
    tail: str = "",
) -> str:
    entries = []
    for database in DATABASES:
        fields = [
            "'host': '127.0.0.1'",
            f"'port': {port_expression if port_expression is not None else '15432'}",
            f"'database': '{database}_test'",
            "'user': 'synthetic_monitor'",
            f"'password': {password!r}",
        ]
        if extra_field is not None:
            fields.append(f"'unexpected': {extra_field!r}")
        entries.append(f"    '{database}': {{{', '.join(fields)}}}")
    return "DATABASES = {\n" + ",\n".join(entries) + "\n}\n" + tail


def _write_config(tmp_path: Path, source: str | None = None) -> Path:
    path = tmp_path / "synthetic_local_config.py"
    path.write_text(source if source is not None else _fixture_source(), encoding="utf-8")
    return path


@pytest.mark.parametrize("database", DATABASES)
def test_allowlisted_database_fields_are_available_without_password(
    tmp_path: Path, database: str
) -> None:
    path = _write_config(tmp_path)

    result = read_lab_database_config(path, database)

    assert result.as_dict() == {
        "host": "127.0.0.1",
        "port": 15432,
        "database": f"{database}_test",
        "user": "synthetic_monitor",
    }
    assert "password" not in result.as_dict()
    assert not hasattr(result, "password")


def test_allowlist_does_not_accept_caller_selected_fields(tmp_path: Path) -> None:
    path = _write_config(tmp_path)

    with pytest.raises(TypeError):
        read_lab_database_config(path, "mysql", "password")  # type: ignore[call-arg]
    with pytest.raises(LabConfigError) as exc_info:
        read_lab_database_config(path, "mysql;password")

    assert exc_info.value.code == "UNKNOWN_DATABASE"
    _safe_check(SYNTHETIC_PASSWORD not in str(exc_info.value), "configuration error exposed a synthetic credential")


def test_password_is_available_only_through_secret_connection_api(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    secret = read_lab_connection_secret(path, "mysql")
    args = object()
    received: list[tuple[object, str]] = []

    result = connect_with_lab_secret(
        secret,
        lambda arg, password: received.append((arg, password)) or "connected",
        args,
    )

    assert result == "connected"
    _safe_check(
        received == [(args, SYNTHETIC_PASSWORD)],
        "connection callback did not receive the configured test credential",
    )
    assert "password" not in read_lab_database_config(path, "mysql").as_dict()


def test_connection_profile_binds_public_values_and_secret_to_one_parse(
    tmp_path: Path,
) -> None:
    path = _write_config(tmp_path)

    config, secret = read_lab_connection_profile(path, "pg")

    assert config.database == "pg_test"
    assert config.as_dict().get("password") is None
    assert repr(secret) == "Secret(<redacted>)"
    _safe_check(
        secret.reveal_for_connection() == SYNTHETIC_PASSWORD,
        "connection profile did not retain its synthetic credential",
    )


def test_secret_string_repr_format_and_namespace_are_redacted(tmp_path: Path) -> None:
    secret = read_lab_connection_secret(_write_config(tmp_path), "mysql")
    namespace = argparse.Namespace(config_password=secret)

    assert str(secret) == "<redacted>"
    assert repr(secret) == "Secret(<redacted>)"
    assert f"{secret}" == "<redacted>"
    _safe_check(SYNTHETIC_PASSWORD not in repr(namespace), "argument namespace exposed a synthetic credential")
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        json.dumps(secret)


def test_public_config_json_and_html_do_not_contain_secret(tmp_path: Path) -> None:
    config = read_lab_database_config(_write_config(tmp_path), "oracle")
    serialized = json.dumps(asdict(config))
    rendered = html.escape(serialized)

    _safe_check(SYNTHETIC_PASSWORD not in serialized, "JSON exposed a synthetic credential")
    _safe_check(SYNTHETIC_PASSWORD not in rendered, "HTML escaped output exposed a synthetic credential")


def test_secret_representations_do_not_leak_to_streams_or_logging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    secret = read_lab_connection_secret(_write_config(tmp_path), "pg")
    caplog.set_level(logging.INFO)

    print(secret)
    print(repr(secret), file=sys.stderr)
    logging.getLogger("lab-config-test").info("credential=%s repr=%r", secret, secret)

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    _safe_check(SYNTHETIC_PASSWORD not in combined, "output/logging exposed a synthetic credential")


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (_fixture_source(extra_field=SYNTHETIC_TOKEN), "INVALID_FIELD"),
        (_fixture_source(port_expression="int('15432')"), "INVALID_STRUCTURE"),
        (_fixture_source(tail="import os\n"), "INVALID_STRUCTURE"),
        (_fixture_source().replace("'user': 'synthetic_monitor'", "'user': 12"), "INVALID_TYPE"),
        (
            _fixture_source().replace(
                ", 'password': 'SYNTHETIC_PASSWORD_DO_NOT_LEAK'", ""
            ),
            "INVALID_FIELD",
        ),
        ("DATABASES = {", "PARSE_FAILED"),
    ],
    ids=["extra-field", "dynamic-port", "import-statement", "invalid-user-type", "missing-password", "broken-python"],
)
def test_invalid_configuration_fails_closed_without_echoing_source(
    tmp_path: Path, source: str, expected_code: str
) -> None:
    path = _write_config(tmp_path, source)
    source_bytes = path.read_bytes()
    before = hashlib.sha256(source_bytes).hexdigest()
    source_secrets = (SYNTHETIC_PASSWORD, SYNTHETIC_TOKEN)

    with pytest.raises(LabConfigError) as exc_info:
        read_lab_database_config(path, "mysql")
    formatted_traceback = traceback.format_exc()

    assert exc_info.value.code == expected_code
    _safe_check(
        all(secret not in str(exc_info.value) for secret in source_secrets),
        "configuration exception exposed synthetic input",
    )
    _safe_check(
        all(secret not in formatted_traceback for secret in source_secrets),
        "configuration traceback exposed synthetic input",
    )
    _safe_check(exc_info.value.__context__ is None, "configuration error retained raw parser context")
    _safe_check(path.read_bytes() == source_bytes, "configuration reader modified its source")
    _safe_check(hashlib.sha256(path.read_bytes()).hexdigest() == before, "configuration source hash changed")
    assert list(tmp_path.iterdir()) == [path]


def test_unknown_engine_fails_without_opening_a_configuration(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.py"

    with pytest.raises(LabConfigError) as exc_info:
        read_lab_database_config(missing, "mssql")

    assert exc_info.value.code == "UNKNOWN_DATABASE"
    assert not missing.exists()


def test_dynamic_configuration_expression_is_rejected_without_execution(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "must-not-be-created.txt"
    expression = (
        "__import__('pathlib').Path(" + repr(str(sentinel)) + ").write_text('synthetic')"
    )
    path = _write_config(tmp_path, _fixture_source(port_expression=expression))

    with pytest.raises(LabConfigError) as exc_info:
        read_lab_database_config(path, "mysql")

    assert exc_info.value.code == "INVALID_STRUCTURE"
    assert not sentinel.exists()


def test_configuration_error_does_not_echo_source_path(tmp_path: Path) -> None:
    sensitive_path = tmp_path / "SYNTHETIC_PATH_DO_NOT_LEAK" / "local_config.py"
    sensitive_path.parent.mkdir()
    sensitive_path.write_text("not valid python !!!", encoding="utf-8")

    with pytest.raises(LabConfigError) as exc_info:
        read_lab_database_config(sensitive_path, "mysql")

    formatted_traceback = traceback.format_exc()
    assert str(sensitive_path) not in str(exc_info.value)
    _safe_check(
        "SYNTHETIC_PATH_DO_NOT_LEAK" not in formatted_traceback,
        "configuration traceback exposed its source path",
    )


def test_connection_override_takes_precedence_and_driver_errors_are_sanitized(
    tmp_path: Path,
) -> None:
    secret = read_lab_connection_secret(_write_config(tmp_path), "sqlserver")
    seen: list[str] = []
    override = "SYNTHETIC_PASSWORD_OVERRIDE_DO_NOT_LEAK"

    connect_with_lab_secret(override, lambda _arg, password: seen.append(password), object())

    _safe_check(seen == [override], "explicit in-memory connection override was not preserved")
    with pytest.raises(LabConnectionError) as exc_info:
        connect_with_lab_secret(
            secret,
            lambda _arg, password: (_ for _ in ()).throw(RuntimeError(password)),
            object(),
        )
    formatted_traceback = traceback.format_exc()
    _safe_check(SYNTHETIC_PASSWORD not in str(exc_info.value), "connection error exposed a synthetic credential")
    _safe_check(SYNTHETIC_PASSWORD not in formatted_traceback, "connection traceback exposed a synthetic credential")
    _safe_check(exc_info.value.__context__ is None, "connection error retained raw driver context")


def test_configuration_read_does_not_change_source_or_create_a_copy(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    source_bytes = path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()

    read_lab_database_config(path, "mysql")
    read_lab_connection_secret(path, "mysql")

    _safe_check(path.read_bytes() == source_bytes, "configuration reader modified its source")
    _safe_check(hashlib.sha256(path.read_bytes()).hexdigest() == source_hash, "configuration source hash changed")
    assert {item.name for item in tmp_path.iterdir()} == {path.name}


def test_nested_pytest_failure_output_does_not_echo_driver_secret(tmp_path: Path) -> None:
    secret = "SYNTHETIC_" + "PYTEST_FAILURE_" + "VALUE_DO_NOT_LEAK"
    child_test = tmp_path / "test_sanitized_failure.py"
    child_test.write_text(
        "from dba.lab_config import connect_with_lab_secret\n"
        "def _secret(): return 'SYNTHETIC_' + 'PYTEST_FAILURE_' + 'VALUE_DO_NOT_LEAK'\n"
        "def _driver(password): raise RuntimeError('rejected ' + password)\n"
        "def test_driver_error(): connect_with_lab_secret(_secret(), _driver)\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", str(child_test)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    _safe_check(result.returncode != 0, "nested pytest did not exercise its expected failure output")
    _safe_check(secret not in output, "pytest failure output exposed a synthetic credential")
