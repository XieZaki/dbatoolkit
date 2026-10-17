"""Explicit real-driver CLI check; receives a temporary monitor secret on stdin."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, "/workspace")

from dba.cli import main  # noqa: E402
from dba.core import DatabaseInstance, DatabaseType  # noqa: E402
from dba.credentials import Credential, CredentialProvider  # noqa: E402


class MonitorProvider(CredentialProvider):
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def get_credential(self, alias: str) -> Credential:
        if alias != "v08-monitor":
            raise ValueError("Unexpected credential alias")
        return Credential(self.username, self.password)


def main_verify() -> dict[str, Any]:
    config = json.load(sys.stdin)
    db_type = DatabaseType(config["db_type"])
    instance = DatabaseInstance("v08-docker", "V0.8 Docker", db_type, "127.0.0.1",
                                config["port"], "v08-monitor",
                                database=config.get("database"),
                                service_name=config.get("service_name"))
    provider = MonitorProvider(config["username"], config["password"])
    settings: dict[str, Any] = {"sqlserver": {"trust_server_certificate": True},
                                "postgresql": {"sslmode": "disable"},
                                "mysql": {"ssl_enabled": False}, "oracle": {}}
    with tempfile.TemporaryDirectory(prefix="dba-v08-") as folder:
        root = Path(folder)
        inventory = root / "inventory.yaml"
        inventory.write_text(yaml.safe_dump({"databases": [
            {**asdict(instance), "db_type": db_type.value}]}), encoding="utf-8")
        runtime = root / "runtime.yaml"
        runtime.write_text(yaml.safe_dump(settings), encoding="utf-8")
        report_path = root / "disk.html"
        prefix = ["--inventory", str(inventory), "--sql-root", "/workspace/sql",
                  "--settings", str(runtime)]

        def invoke(command: list[str]) -> tuple[int, dict[str, Any]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([*prefix, *command], provider=provider)
            rendered = output.getvalue()
            if config["password"] in rendered:
                raise RuntimeError("Credential appeared in CLI output")
            return code, json.loads(rendered)

        connected_code, connected = invoke(["test-connection", instance.id])
        if connected_code != 0:
            return {"db_type": db_type.value, "success": False,
                    "phase": "test-connection", "error_type": connected.get("error_type")}
        code, output = invoke(["diagnose", instance.id, "disk-growth", "--format", "json",
                               "--report", str(report_path)])
        if not report_path.exists():
            raise RuntimeError("HTML report missing")
        html_report = report_path.read_text(encoding="utf-8")
        if config["password"] in html_report or "password=" in html_report.lower():
            raise RuntimeError("Credential appeared in HTML report")
        details = output["report"]["details"]
        return {"db_type": db_type.value, "success": code == 0,
                "version": connected["version"], "preflight": connected["preflight"],
                "sources": details["samples"], "incomplete": details["incomplete"],
                "space_categories": sorted({row["category"] for row in details["space"]}),
                "findings": [item["title"] for item in output["report"]["findings"]],
                "next_step": details["next_diagnostic_step"],
                "report_sections": html_report.count("<section")}


if __name__ == "__main__":
    try:
        print(json.dumps(main_verify(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "phase": "verification"}))
        raise SystemExit(1) from None
