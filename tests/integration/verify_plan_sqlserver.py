"""Explicit V0.4 disposable SQL Server plan fixture; excluded from ordinary pytest."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

import yaml
from verify_sqlserver import HOST, LOGIN, cli_run, connection_string

sys.path.insert(0, "/workspace")
from dba.environment import driver_info  # noqa: E402


def verify(config: dict[str, str]) -> dict[str, Any]:
    if sys.platform != "linux" or config.get("host") != HOST or not Path("/.dockerenv").exists():
        raise RuntimeError("Disposable Docker fixture required")
    pyodbc = importlib.import_module("pyodbc")
    pyodbc.pooling = False
    report: dict[str, Any] = {"success": False, "environment": driver_info(), "scenarios": []}
    admin = pyodbc.connect(connection_string("sa", config["administrator_password"], "plan-setup"),
                           autocommit=True, timeout=5)
    workload = None
    observer = None
    setup_done = False
    try:
        admin.timeout = 15
        cursor = admin.cursor()
        try:
            password = config["workload_password"].replace("'", "''")
            cursor.execute(f"CREATE LOGIN toolkit_workload WITH PASSWORD=N'{password}';")
            cursor.execute(Path(__file__).with_name("plan_fixture.sql").read_text(encoding="utf-8"))
            while cursor.nextset():
                pass
            setup_done = True
        finally:
            cursor.close()
        workload = pyodbc.connect(
            connection_string("toolkit_workload", config["workload_password"], "plan-fixture"),
            autocommit=True, timeout=5)
        workload.timeout = 15
        observer = pyodbc.connect(
            connection_string(LOGIN, config["monitor_password"], "plan-observer"),
            autocommit=True, timeout=5, readonly=True)
        observer.timeout = 10
        cursor = workload.cursor()
        try:
            session_id = int(cursor.execute("SELECT @@SPID;").fetchone()[0])
        finally:
            cursor.close()
        cursor = observer.cursor()
        try:
            report["version"] = cursor.execute(
                "SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(32));"
            ).fetchone()[0]
            report["monitor_is_sysadmin"] = cursor.execute(
                "SELECT IS_SRVROLEMEMBER('sysadmin');"
            ).fetchone()[0]
            assert report["monitor_is_sysadmin"] == 0
        finally:
            cursor.close()

        with tempfile.TemporaryDirectory(prefix="dba-plan-") as temp:
            root = Path(temp)
            inventory = root / "inventory.yaml"
            inventory.write_text(yaml.safe_dump({"databases": [{
                "id": "isolated-sqlserver", "name": "Plan fixture", "db_type": "sqlserver",
                "host": HOST, "port": 1433, "database": "master",
                "credential_alias": "isolated-monitor", "environment": "development",
            }]}), encoding="utf-8")
            settings = root / "runtime.yaml"
            settings.write_text(yaml.safe_dump({"sqlserver": {
                "trust_server_certificate": True, "plan_max_xml_size": 1048576,
            }}), encoding="utf-8")

            cases = {
                "scan": "SELECT SUM(value) FROM dbo.ToolkitPlanFixture WITH (INDEX(1));",
                "seek_lookup": "SELECT payload FROM dbo.ToolkitPlanFixture WITH (INDEX(IX_ToolkitPlanFixture_value)) WHERE value=3;",
                "nested_loop": "SELECT COUNT(*) FROM dbo.ToolkitPlanFixture a INNER LOOP JOIN dbo.ToolkitPlanFixture b ON a.id=b.id WHERE a.id<5;",
                "hash": "SELECT COUNT(*) FROM dbo.ToolkitPlanFixture a INNER HASH JOIN dbo.ToolkitPlanFixture b ON a.value=b.value;",
                "sort": "SELECT id FROM dbo.ToolkitPlanFixture ORDER BY payload;",
            }
            for name, sql in cases.items():
                cursor = workload.cursor()
                try:
                    cursor.execute(sql)
                    cursor.fetchone()
                finally:
                    cursor.close()
                report_path = root / f"{name}.html"
                code, payload, captured = cli_run([
                    "--inventory", str(inventory), "--settings", str(settings),
                    "--sql-root", "/workspace/sql", "diagnose", "isolated-sqlserver",
                    "plan", "--session", str(session_id), "--format", "json",
                    "--report", str(report_path),
                ], config["monitor_password"])
                assert code == 0 and payload.get("success"), "Real plan diagnosis failed"
                assert report_path.exists(), "Plan HTML report unavailable"
                html_report = report_path.read_text(encoding="utf-8")
                assert "Execution Plan XML" not in html_report and "ShowPlanXML" not in html_report
                assert report_path.with_suffix(".sqlplan").exists()
                for secret in (config["monitor_password"], config["administrator_password"],
                               config["workload_password"]):
                    assert secret not in captured + html_report, "Credential leakage detected"
                details = payload["report"]["details"]
                operators = [node["physical_op"] for node in details["top_operators"]]
                report["scenarios"].append({
                    "name": name, "success": True, "source": details["source"],
                    "operator_count": details["operator_count"], "top_operators": operators,
                    "findings": [item["title"] for item in payload["report"]["findings"]],
                    "artifact_xml_embedded": True, "artifact_html": True,
                })
            scenario_map = {entry["name"]: entry for entry in report["scenarios"]}
            assert "Clustered Index Scan" in scenario_map["scan"]["top_operators"]
            assert "Index Seek" in scenario_map["seek_lookup"]["top_operators"]
            assert "Nested Loops" in scenario_map["nested_loop"]["top_operators"]
            assert "Hash Match" in scenario_map["hash"]["top_operators"]
            assert "Sort" in scenario_map["sort"]["top_operators"]
            report["key_lookup_found"] = "KEY_LOOKUP" in scenario_map["seek_lookup"]["findings"]
            report["remaining_toolkit_sessions"] = 0
            cursor = observer.cursor()
            try:
                report["remaining_toolkit_sessions"] = cursor.execute(
                    "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE program_name='dba-toolkit';"
                ).fetchone()[0]
            finally:
                cursor.close()
            assert report["remaining_toolkit_sessions"] == 0
            report["success"] = True
    finally:
        if workload is not None:
            workload.close()
        if observer is not None:
            observer.close()
        try:
            if setup_done:
                cursor = admin.cursor()
                try:
                    cursor.execute("DROP TABLE dbo.ToolkitPlanFixture; DROP USER toolkit_workload; DROP LOGIN toolkit_workload;")
                    while cursor.nextset():
                        pass
                    report["test_objects_removed"] = True
                finally:
                    cursor.close()
        finally:
            admin.close()
    return report


def main() -> int:
    config = json.load(sys.stdin)
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
            report = verify(config)
        output = json.dumps(report, ensure_ascii=False, default=str, indent=2)
        if any(config[key] in output for key in
               ("monitor_password", "administrator_password", "workload_password")):
            raise RuntimeError("Credential leakage detected")
        print(output)
        return 0 if report["success"] else 1
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "locations": [{"function": f.name, "line": f.lineno}
                                        for f in traceback.extract_tb(exc.__traceback__)]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
