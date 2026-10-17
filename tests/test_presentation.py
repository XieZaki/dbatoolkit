import json

from dba.presentation import CheckResult, OutputMode, StatusResult, render_human, render_json


def test_check_human_and_json_share_one_domain_result() -> None:
    result = CheckResult(
        {"id": "sql", "db_type": "sqlserver"},
        {"status": "READY_WITH_WARNING", "driver": "ODBC Driver 17"},
        {"alias": "monitor", "backend": "OS Store", "status": "READY"},
        {"target": "localhost:1433", "database": "master", "status": "READY",
         "encrypt": False, "trust_server_certificate": True},
        ({"feature": "slow", "status": "READY"},),
        "READY_WITH_WARNING", ("Driver 18 unavailable; using Driver 17.",),
    )
    human = render_human("check", result)
    machine = json.loads(render_json(result))
    assert "DBA Toolkit Check" in human and "READY_WITH_WARNING" in human
    assert machine["permissions"][0]["feature"] == "slow"
    assert machine["status"] == "READY_WITH_WARNING"


def test_status_human_handles_no_active_requests() -> None:
    result = StatusResult(
        {"id": "sql", "db_type": "sqlserver", "target": "localhost:1433"},
        "16.0", "READY", 2, 0, 0, {"ONLINE": 3}, "READY",
    )
    rendered = render_human("status", result)
    assert "Active Requests" in rendered and "0" in rendered
    assert "dba diagnose sql slow" in rendered


def test_human_permission_error_includes_safe_grant_and_safety_notice() -> None:
    rendered = render_human("collect", {
        "error_type": "MissingPermission", "database_id": "mysql-prod", "feature": "disk-growth",
        "permission_missing": {
            "missing_privileges": ["REPLICATION CLIENT"],
            "impact": "Binary log collection unavailable.",
            "suggested_grant": "GRANT REPLICATION CLIENT ON *.* TO 'monitor'@'host';",
        },
    })
    assert "REPLICATION CLIENT" in rendered
    assert "Toolkit did NOT execute" in rendered
    assert "password" not in rendered.lower()


def test_json_redacts_sensitive_values() -> None:
    result = json.loads(render_json({"password": 'DBA-Test-Secret-987654', "credential_alias": "monitor"}))
    assert result["password"] == "[REDACTED]"
    assert result["credential_alias"] == "monitor"
    assert OutputMode.JSON.value == "json"
