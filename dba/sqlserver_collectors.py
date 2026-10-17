import time
from dataclasses import replace
from datetime import datetime, timezone

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance
from dba.errors import ToolkitError
from dba.sqlserver import SQLServerAdapter


class SQLServerCollector(Collector):
    script_name = ""

    def __init__(self, adapter: SQLServerAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance:
            raise ToolkitError(
                "InstanceMismatch", "Collector and adapter must refer to the same instance."
            )
        result = self.adapter.execute_readonly(self.adapter.script_for(self.script_name))
        return replace(result, collector_name=self.script_name)


class ServerInfoCollector(SQLServerCollector):
    script_name = "server_info"


class DatabaseStatusCollector(SQLServerCollector):
    script_name = "database_status"


class ActiveSessionsCollector(SQLServerCollector):
    script_name = "active_sessions"


class ActiveRequestsCollector(SQLServerCollector):
    script_name = "active_requests"


class TransactionsCollector(SQLServerCollector):
    script_name = "transactions"


class ConnectionsCollector(SQLServerCollector):
    script_name = "connections"


class LongRunningRequestsCollector(ActiveRequestsCollector):
    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        started = time.monotonic()
        result = super().collect(instance)
        threshold_ms = self.adapter.settings.long_running_seconds * 1000
        rows = tuple(row for row in result.rows if row["elapsed_ms"] >= threshold_ms)
        return replace(
            result,
            collector_name="long_running_requests",
            rows=rows,
            row_count=len(rows),
            finished_at=datetime.now(timezone.utc),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )


COLLECTORS: dict[str, type[SQLServerCollector]] = {
    "server-info": ServerInfoCollector,
    "database-status": DatabaseStatusCollector,
    "active-sessions": ActiveSessionsCollector,
    "active-requests": ActiveRequestsCollector,
    "long-running-requests": LongRunningRequestsCollector,
    "long-running": LongRunningRequestsCollector,
}
