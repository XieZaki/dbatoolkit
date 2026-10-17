"""Oracle monitoring collectors; all source SQL comes from the registry."""

import time
from dataclasses import replace
from datetime import datetime, timezone

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.oracle import OracleAdapter


class OracleCollector(Collector):
    script_name = ""

    def __init__(self, adapter: OracleAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.ORACLE:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        script = self.adapter.registry.get(self.script_name, DatabaseType.ORACLE)
        return self.adapter.execute_readonly(script)


class ServerInfoCollector(OracleCollector):
    script_name = "server_info"


class DatabaseStatusCollector(OracleCollector):
    script_name = "database_status"


class ActiveSessionsCollector(OracleCollector):
    script_name = "active_sessions"


class ActiveRequestsCollector(OracleCollector):
    script_name = "active_requests"


class TransactionsCollector(OracleCollector):
    script_name = "transactions"


class BlockingCollector(OracleCollector):
    script_name = "blocking"

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        self.script_name = self.adapter.blocking_script
        return replace(super().collect(instance), collector_name="blocking")


class PreviousSQLCollector(OracleCollector):
    script_name = "previous_sql"


class LongRunningCollector(OracleCollector):
    script_name = "long_running"

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        started = time.monotonic()
        result = super().collect(instance)
        threshold = self.adapter.settings.long_running_seconds
        rows = tuple(row for row in result.rows if
                     type(row.get("duration_seconds")) is int
                     and row["duration_seconds"] >= threshold)
        return replace(result, collector_name="long_running", rows=rows,
                       row_count=len(rows), finished_at=datetime.now(timezone.utc),
                       elapsed_ms=(time.monotonic() - started) * 1000)


COLLECTORS: dict[str, type[OracleCollector]] = {
    "server-info": ServerInfoCollector,
    "database-status": DatabaseStatusCollector,
    "active-sessions": ActiveSessionsCollector,
    "active-requests": ActiveRequestsCollector,
    "long-running": LongRunningCollector,
    "long-running-requests": LongRunningCollector,
}
