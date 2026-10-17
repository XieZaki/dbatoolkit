import re
from dataclasses import dataclass

from dba.errors import ToolkitError


@dataclass(frozen=True)
class ServerCapabilities:
    product_version: str
    sessions_database_id: bool
    sessions_open_transaction_count: bool

    def __post_init__(self) -> None:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", self.product_version)
        if match is None or (int(match[1]), int(match[2])) < (10, 50):
            raise ToolkitError("UnsupportedVersion", "SQL Server 2008 R2 or later is required.")

    @property
    def sessions_script(self) -> str:
        return (
            "active_sessions"
            if self.sessions_database_id and self.sessions_open_transaction_count
            else "active_sessions_legacy"
        )
