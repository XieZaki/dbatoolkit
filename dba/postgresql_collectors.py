"""PostgreSQL Registry-backed bounded collectors."""

import time
from dataclasses import replace
from datetime import datetime, timezone

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.postgresql import PostgreSQLAdapter


class PostgreSQLCollector(Collector):
    script_name = ""

    def __init__(self, adapter: PostgreSQLAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.POSTGRESQL:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        script = self.adapter.registry.get(self.script_name, DatabaseType.POSTGRESQL)
        return replace(self.adapter.execute_readonly(script), collector_name=self.script_name)


class ServerInfoCollector(PostgreSQLCollector):
    script_name = "server_info"


class DatabaseStatusCollector(PostgreSQLCollector):
    script_name = "database_status"


class ActiveSessionsCollector(PostgreSQLCollector):
    script_name = "active_sessions"


class TransactionsCollector(PostgreSQLCollector):
    script_name = "transactions"


class BlockingCollector(PostgreSQLCollector):
    script_name = "blocking"


class LongRunningCollector(PostgreSQLCollector):
    script_name = "long_running"

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        started = time.monotonic()
        result = super().collect(instance)
        threshold = self.adapter.settings.long_running_seconds * 1000
        rows = tuple(row for row in result.rows
                     if type(row.get("duration_ms")) is int and row["duration_ms"] >= threshold)
        return replace(result, rows=rows, row_count=len(rows),
                       finished_at=datetime.now(timezone.utc),
                       elapsed_ms=(time.monotonic() - started) * 1000)


COLLECTORS: dict[str, type[PostgreSQLCollector]] = {
    "server-info": ServerInfoCollector,
    "database-status": DatabaseStatusCollector,
    "active-sessions": ActiveSessionsCollector,
    "long-running": LongRunningCollector,
    "long-running-requests": LongRunningCollector,
}
