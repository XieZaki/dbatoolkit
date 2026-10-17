"""Bounded MySQL 8.0 monitoring collectors."""

import ipaddress
import time
from dataclasses import replace
from datetime import datetime, timezone

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.mysql import MySQLAdapter


def host_ip(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    candidate = raw
    if raw.startswith("[") and "]:" in raw:
        candidate = raw[1:raw.index("]")]
    elif raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit():
        candidate = raw.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


class MySQLCollector(Collector):
    script_name = ""

    def __init__(self, adapter: MySQLAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.MYSQL:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        name = self.adapter.statement_script if self.script_name == "active_requests" else self.script_name
        script = self.adapter.registry.get(name, DatabaseType.MYSQL)
        result = self.adapter.execute_readonly(script)
        if self.script_name in {"active_sessions", "active_requests", "transactions"} and result.success:
            result = replace(result, rows=tuple({**row, "host_ip": host_ip(row.get("host_raw"))}
                                                for row in result.rows))
        return replace(result, collector_name=self.script_name)


class ServerInfoCollector(MySQLCollector):
    script_name = "server_info"


class DatabaseStatusCollector(MySQLCollector):
    script_name = "database_status"


class ActiveSessionsCollector(MySQLCollector):
    script_name = "active_sessions"


class ActiveRequestsCollector(MySQLCollector):
    script_name = "active_requests"


class TransactionsCollector(MySQLCollector):
    script_name = "transactions"


class BlockingCollector(MySQLCollector):
    script_name = "blocking"


class LongRunningCollector(ActiveRequestsCollector):
    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        started = time.monotonic()
        result = super().collect(instance)
        threshold = self.adapter.settings.long_running_seconds * 1000
        rows = tuple(row for row in result.rows
                     if type(row.get("duration_ms")) is int and row["duration_ms"] >= threshold)
        return replace(result, collector_name="long_running", rows=rows,
                       row_count=len(rows), finished_at=datetime.now(timezone.utc),
                       elapsed_ms=(time.monotonic() - started) * 1000)


COLLECTORS: dict[str, type[MySQLCollector]] = {
    "server-info": ServerInfoCollector,
    "database-status": DatabaseStatusCollector,
    "active-sessions": ActiveSessionsCollector,
    "active-requests": ActiveRequestsCollector,
    "long-running": LongRunningCollector,
    "long-running-requests": LongRunningCollector,
}
