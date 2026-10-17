from abc import ABC, abstractmethod

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance, DatabaseType, Finding
from dba.settings import SQLServerSettings


class Analyzer(ABC):
    @abstractmethod
    def analyze(
        self, instance: DatabaseInstance, results: tuple[CollectorResult, ...]
    ) -> tuple[Finding, ...]: ...


SCENARIOS = frozenset(
    {
        "slow",
        "blocking",
        "long-transaction",
        "disk-growth",
        "log-growth",
        "replication-lag",
        "space",
        "backup",
        "connection",
        "high-cpu",
        "io",
        "temp-growth",
    }
)


class AnalyzerRegistry:
    """Dispatch by database AND scenario/source, never a universal rule set."""

    def __init__(self) -> None:
        self._analyzers: dict[tuple[DatabaseType, str], Analyzer] = {}

    def register(self, db_type: DatabaseType, scenario: str, analyzer: Analyzer) -> None:
        key = (db_type, scenario)
        if key in self._analyzers:
            raise ValueError("Analyzer already registered")
        self._analyzers[key] = analyzer

    def get(self, db_type: DatabaseType, scenario: str) -> Analyzer:
        return self._analyzers[(db_type, scenario)]


class SlowAnalyzer(Analyzer):
    def __init__(self, settings: "SQLServerSettings | None" = None) -> None:
        self.settings = settings or SQLServerSettings()

    def analyze(
        self, instance: DatabaseInstance, results: tuple[CollectorResult, ...]
    ) -> tuple[Finding, ...]:
        from dba.slow import build_slow_report

        return build_slow_report(instance, results, self.settings).findings


class BlockingAnalyzer(Analyzer):
    pass


class LongTransactionAnalyzer(Analyzer):
    pass


class DiskGrowthAnalyzer(Analyzer):
    pass


class LogGrowthAnalyzer(Analyzer):
    pass


class ReplicationLagAnalyzer(Analyzer):
    pass


class SpaceAnalyzer(Analyzer):
    pass


class BackupAnalyzer(Analyzer):
    pass


class ConnectionAnalyzer(Analyzer):
    pass


class HighCpuAnalyzer(Analyzer):
    pass


class IoAnalyzer(Analyzer):
    pass


class TempGrowthAnalyzer(Analyzer):
    pass


class PostgreSQLWalAnalyzer(LogGrowthAnalyzer):
    pass


class SQLServerTransactionLogAnalyzer(LogGrowthAnalyzer):
    pass


class MySQLBinlogAnalyzer(LogGrowthAnalyzer):
    pass


class OracleRedoArchiveAnalyzer(LogGrowthAnalyzer):
    pass
