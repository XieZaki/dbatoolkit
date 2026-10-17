from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from dba.collectors import CollectorResult
from dba.core import DatabaseInstance
from dba.credentials import CredentialProvider, Mode, validate_provider
from dba.registry import SqlScript

if TYPE_CHECKING:
    from dba.mysql import MySQLAdapter as MySQLAdapter
    from dba.oracle import OracleAdapter as OracleAdapter
    from dba.postgresql import PostgreSQLAdapter as PostgreSQLAdapter
    from dba.sqlserver import SQLServerAdapter as SQLServerAdapter


class Adapter(ABC):
    def __init__(
        self, instance: DatabaseInstance, provider: CredentialProvider, mode: Mode = Mode.PRODUCTION
    ) -> None:
        validate_provider(provider, mode)
        self.instance = instance
        self.provider = provider

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def execute_readonly(self, script: SqlScript) -> CollectorResult: ...

    @abstractmethod
    def get_server_version(self) -> str: ...

    @abstractmethod
    def health_check(self) -> bool: ...


# Preserve the original public import without putting driver code in this module.
def __getattr__(name: str) -> type[Adapter]:
    if name == "OracleAdapter":
        from dba.oracle import OracleAdapter

        return OracleAdapter
    if name == "MySQLAdapter":
        from dba.mysql import MySQLAdapter

        return MySQLAdapter
    if name == "PostgreSQLAdapter":
        from dba.postgresql import PostgreSQLAdapter

        return PostgreSQLAdapter
    if name == "SQLServerAdapter":
        from dba.sqlserver import SQLServerAdapter

        return SQLServerAdapter
    raise AttributeError(name)
