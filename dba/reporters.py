import json
from typing import TextIO

from dba.core import DiagnosticReport
from dba.logging import redact


def _safe(value: object) -> str:
    text = str(redact(value))
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32).replace("\x7f", "")


class ConsoleReporter:
    def render(self, report: DiagnosticReport) -> str:
        lines = [
            f"Instance: {report.instance.id} ({report.instance.name})",
            f"Database type: {report.instance.db_type.value}",
            f"Diagnostic time: {report.diagnosed_at.isoformat()}",
            f"Scenario: {report.scenario}",
        ]
        for finding in report.findings:
            lines.extend(
                [
                    f"[{finding.severity.value}] {finding.title}",
                    f"Evidence: {json.dumps(redact(finding.evidence), ensure_ascii=False)}",
                ]
            )
            for label in ("session", "login", "host", "ip", "sql"):
                lines.append(f"{label}: {getattr(finding, label) or '-'}")
            lines.append(f"Recommendation: {finding.recommendation}")
            for command in finding.reference_commands:
                lines.extend(
                    [
                        "ReferenceCommand (DBA manual execution only):",
                        command.description,
                        command.text,
                    ]
                )
        return _safe("\n".join(lines)) + "\n"

    def write(self, report: DiagnosticReport, stream: TextIO) -> None:
        stream.write(self.render(report))


class MarkdownReporter:
    def render(self, report: DiagnosticReport) -> str:
        # Indented code preserves arbitrary SQL/backticks without breaking fences.
        body = ConsoleReporter().render(report)
        return (
            "# DBA diagnostic report\n\n"
            + "\n".join("    " + line for line in body.splitlines())
            + "\n"
        )

    def write(self, report: DiagnosticReport, stream: TextIO) -> None:
        stream.write(self.render(report))
