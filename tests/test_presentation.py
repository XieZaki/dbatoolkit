import json

from dba.presentation import CheckResult, OutputMode, StatusResult, render_human, render_json


def test_check_human_and_json_share_one_domain_result() -> None:
    result = CheckResult(
        {"id": "sql", "db_type": "sqlserver"},
        {"status": "READY_WITH_WARNING", "driver": "ODBC Driver 17"},
        {"alias": "monitor", "backend": "OS Store", "status": "READY"},
        {"target": "localhost:1433", "database": "master", "status": "READY",
         "encrypt": False, "trust_server_certificate": True},
        "READY_WITH_WARNING", ("Driver 18 unavailable; using Driver 17.",),
        {"classification": "NON_PRIVILEGED"},
    )
    human = render_human("check", result)
    machine = json.loads(render_json(result))
    assert "DBA Toolkit 检查" in human and "就绪（有提示）" in human
    assert "permissions" not in machine
    assert machine["status"] == "READY_WITH_WARNING"


def test_status_human_handles_no_active_requests() -> None:
    result = StatusResult(
        {"id": "sql", "db_type": "sqlserver", "target": "localhost:1433"},
        "16.0", "READY", 2, 0, 0, {"ONLINE": 3}, "READY",
    )
    rendered = render_human("status", result)
    assert "活动请求数" in rendered and "0" in rendered
    assert "dba diagnose sql slow" in rendered


def test_human_database_access_error_preserves_vendor_error_and_guide() -> None:
    rendered = render_human("collect", {
        "error_type": "QueryDenied", "database_id": "mysql-lab",
        "error_message": "Database rejected the reviewed query.",
        "error": {"vendor_code": 1142, "message": "SELECT command denied",
                  "guidance": "Review docs/DATABASE_PERMISSIONS.md."},
    })
    assert "1142" in rendered
    assert "SELECT command denied" in rendered
    assert "docs/DATABASE_PERMISSIONS.md" in rendered
    assert "GRANT" not in rendered
    assert "password" not in rendered.lower()


def test_json_redacts_sensitive_values() -> None:
    result = json.loads(render_json({"password": 'DBA-Test-Secret-987654', "credential_alias": "monitor"}))
    assert result["password"] == "[REDACTED]"
    assert result["credential_alias"] == "monitor"
    assert OutputMode.JSON.value == "json"
