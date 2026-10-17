"""Oracle monitoring collectors; all source SQL comes from the registry."""

import time
from dataclasses import replace
from datetime import datetime, timezone

from dba.collectors import Collector, CollectorResult
from dba.core import DatabaseInstance, DatabaseType
from dba.errors import ToolkitError
from dba.oracle import OracleAdapter
from dba.slow_profiles import normalize_slow_result, profile_for_adapter, script_for_slow


class OracleCollector(Collector):
    script_name = ""

    def __init__(self, adapter: OracleAdapter) -> None:
        self.adapter = adapter

    def collect(self, instance: DatabaseInstance) -> CollectorResult:
        if instance != self.adapter.instance or instance.db_type != DatabaseType.ORACLE:
            raise ToolkitError("InstanceMismatch", "Collector and adapter must refer to the same instance.")
        name = script_for_slow(self.adapter, self.script_name, self.script_name)
        script = self.adapter.registry.get(name, DatabaseType.ORACLE)
        result = self.adapter.execute_readonly(script)
        profile = profile_for_adapter(self.adapter)
        return normalize_slow_result(profile, result) if profile else result


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
