import argparse
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from dba import __version__
from dba.active_sql_collectors import SQLServerActiveSQLCollector
from dba.active_sql_profiles import validate_active_sql_profiles
from dba.active_sql_report import ActiveSQLDiagnosticReport, render_active_sql_human
from dba.collectors import CollectorResult
from dba.connection import public_connection_settings, resolve_connection_settings
from dba.core import DatabaseType, DiagnosticReport
from dba.credential_session import (
    CredentialSessionClient,
    parse_ttl,
)
from dba.credentials import (
    Credential,
    CredentialProvider,
    CredentialStore,
    KeyringCredentialStore,
    PromptCredentialProvider,
    confirm_action,
    prompt_credential_for_storage,
    validate_alias,
)
from dba.disk_growth import collect_disk_growth_phase
from dba.disk_growth_profiles import validate_disk_profiles
from dba.encrypted_credentials import (
    CredentialResolver,
    EncryptedFileCredentialStore,
    prompt_master_password,
    prompt_new_master_password,
)
from dba.environment import driver_info
from dba.errors import ToolkitError
from dba.execution_plan import diagnose_plan
from dba.execution_plan_profiles import validate_plan_profiles
from dba.incident import (
    build_incident_summary,
    build_performance_triage,
    build_priority_candidates,
    manual_action_decisions,
)
from dba.inventory import Inventory, ValidationError
from dba.logging import configure_logging, redact
from dba.memory_grant import (
    MemoryGrantDiagnosticReport,
    SQLServerMemoryGrantCollector,
)
from dba.memory_grant_profiles import validate_memory_grant_profiles
from dba.memory_grant_report import render_memory_grant_human
from dba.mysql import MySQLAdapter
from dba.mysql_collectors import COLLECTORS as MYSQL_COLLECTORS
from dba.mysql_collectors import collect_recent_statements as collect_mysql_recent_statements
from dba.mysql_slow import build_slow_phase_report as build_mysql_slow_phase_report
from dba.mysql_slow import collect_slow_phase as collect_mysql_slow_phase
from dba.mysql_slow_reporter import render_recent_statements as render_mysql_recent_statements
from dba.oracle import OracleAdapter
from dba.oracle_collectors import COLLECTORS as ORACLE_COLLECTORS
from dba.oracle_plan import diagnose_oracle_plan
from dba.oracle_plan_profiles import validate_oracle_plan_profiles
from dba.oracle_slow import build_slow_phase_report as build_oracle_slow_phase_report
from dba.oracle_slow import collect_slow_phase as collect_oracle_slow_phase
from dba.postgresql import PostgreSQLAdapter
from dba.postgresql_collectors import COLLECTORS as PG_COLLECTORS
from dba.postgresql_slow import build_slow_phase_report as build_pg_slow_phase_report
from dba.postgresql_slow import collect_slow_phase as collect_pg_slow_phase
from dba.presentation import (
    OutputMode,
    StatusResult,
    render_emergency_triage,
    render_human,
    render_incident_summary,
    render_json,
    render_oracle_plan_human,
)
from dba.registry import SqlScriptRegistry
from dba.replication import diagnose_replication
from dba.replication_collectors import validate_replication_profiles
from dba.reporting import HtmlReportRenderer, build_report_model, render_bounded
from dba.runtime import RuntimeStatus, database_runtime_status, doctor, runtime_info
from dba.settings import (
    MySQLSettings,
    OracleSettings,
    PostgreSQLSettings,
    SQLServerSettings,
    apply_diagnostic_threshold,
    validate_diagnostic_settings,
)
from dba.slow import build_slow_phase_report as build_sqlserver_slow_phase_report
from dba.slow import collect_slow_phase as collect_sqlserver_slow_phase
from dba.slow_profiles import (
    emergency_collectors_for_adapter,
    optional_collectors_for_adapter,
    required_collectors_for_adapter,
    validate_slow_profiles,
)
from dba.sqlserver import SQLServerAdapter
from dba.sqlserver_collectors import COLLECTORS
from dba.top_sql import TOP_LIMIT
from dba.top_sql_collectors import METRICS as SQLSERVER_TOP_SQL_METRICS
from dba.top_sql_collectors import MYSQL_METRICS as MYSQL_TOP_SQL_METRICS
from dba.top_sql_collectors import ORACLE_METRICS as ORACLE_TOP_SQL_METRICS
from dba.top_sql_collectors import POSTGRESQL_METRICS as POSTGRESQL_TOP_SQL_METRICS
from dba.top_sql_collectors import (
    MySQLTopSQLCollector,
    OracleTopSQLCollector,
    PostgreSQLTopSQLCollector,
    SQLServerTopSQLCollector,
)
from dba.top_sql_profiles import validate_top_sql_profiles
from dba.top_sql_report import TopSQLDiagnosticReport
from dba.versioning import (
    DEFAULT_VERSION_DETECTOR,
    PROFILE_RESOLVER,
    ResolvedDiagnosticProfile,
    SupportState,
    VersionStatus,
    build_version_profile,
    resolve_profile_for_adapter,
)

TOP_SQL_METRIC_CHOICES = tuple(dict.fromkeys((*SQLSERVER_TOP_SQL_METRICS,
                                             *POSTGRESQL_TOP_SQL_METRICS,
                                             *MYSQL_TOP_SQL_METRICS,
                                             *ORACLE_TOP_SQL_METRICS)))


def _triage_summary_fingerprint(
    candidates: Sequence[dict[str, Any]], triage: dict[str, Any]
) -> tuple[Any, ...]:
    assessments = tuple(sorted(
        (str(item.get("session_key") or item.get("session_id") or ""),
         tuple(item.get("classification_labels", ())), item.get("classification_status"))
        for item in candidates
    ))
    return triage.get("status"), tuple(triage.get("unknown_reasons", ())), assessments


class _CachedCredentialProvider(CredentialProvider):
    def __init__(self, credential: Credential) -> None:
        self.credential = credential

    def get_credential(self, alias: str) -> Credential:
        return self.credential


def _account_security(adapter: Any) -> dict[str, Any]:
    evidence = getattr(adapter, "privilege_classification", {})
    classification = evidence.get("classification") if isinstance(evidence, dict) else None
    if classification in {"NON_PRIVILEGED", "PRIVILEGED", "UNKNOWN"}:
        return {"classification": classification, "evidence": evidence}
    # A successful connection means the database-specific, fail-closed
    # monitoring-account preflight completed for adapters with legacy output.
    return {"classification": "NON_PRIVILEGED", "evidence_source": "monitor_preflight"}


def _database_error_payload(error: object) -> dict[str, Any] | None:
    if isinstance(error, dict):
        return error
    to_dict = getattr(error, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return payload if isinstance(payload, dict) else None
    return None


class _InjectedCredentialProvider(CredentialProvider):
    """Use only the explicitly injected store; never auto-resolve another backend."""

    def __init__(self, store: CredentialStore) -> None:
        self.store = store
        self.last_backend: str | None = None

    def get_credential(self, alias: str) -> Credential:
        credential = self.store.get(validate_alias(alias))
        if credential is None:
            raise ToolkitError("CredentialNotFound", "Credential alias was not found in the injected store.")
        self.last_backend = self.store.backend_name
        return credential


def _add_output_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--json", action="store_true", help="Write structured JSON")
    command.add_argument("--quiet", action="store_true", help="Show only the final status")


def _add_credential_backend(command: argparse.ArgumentParser) -> None:
    command.add_argument("--credential-store-path", type=Path, default=None,
                         help="Explicit Store path for the selected encrypted backend")
    command.add_argument(
        "--credential-backend", choices=("auto", "os", "encrypted", "sqlite", "interactive"),
        default="auto", help="Select credential source; encrypted sessions may avoid repeated unlock prompts",
    )


def _emit(args: Any, command: str, result: Any) -> None:
    mode = OutputMode.JSON if bool(getattr(args, "json", False)) or (
        command == "diagnose" and getattr(args, "format", "console") == "json"
    ) else OutputMode.HUMAN
    if mode == OutputMode.JSON:
        print(render_json(result))
    elif getattr(args, "quiet", False):
        status = result.get("status", "READY" if result.get("success", True) else "FAILED")
        print(f"{status}  {result.get('database_id', result.get('database', {}).get('id', ''))}".rstrip())
    else:
        print(render_human(command, result))


def _redact_diagnostic_json(result: Any) -> Any:
    """Redact credential aliases from an exported diagnostic payload copy."""
    payload = redact(result)
    if not isinstance(payload, dict):
        return payload
    report = payload.get("report")
    if not isinstance(report, dict):
        return payload
    instance = report.get("instance")
    if isinstance(instance, dict) and "credential_alias" in instance:
        instance["credential_alias"] = "[REDACTED]"
    return payload


def _runtime_defaults(database_type: DatabaseType, path: Path | None) -> object:
    if database_type == DatabaseType.SQLSERVER:
        return apply_diagnostic_threshold(SQLServerSettings.load(path) if path else SQLServerSettings(), path)
    if database_type == DatabaseType.POSTGRESQL:
        return apply_diagnostic_threshold(PostgreSQLSettings.load(path) if path else PostgreSQLSettings(), path)
    if database_type == DatabaseType.MYSQL:
        return apply_diagnostic_threshold(MySQLSettings.load(path) if path else MySQLSettings(), path)
    if database_type == DatabaseType.ORACLE:
        return apply_diagnostic_threshold(OracleSettings.load(path) if path else OracleSettings(), path)
    if database_type in {DatabaseType.MONGODB, DatabaseType.REDIS}:
        return object()
    raise ToolkitError("NotImplemented", "Connection settings are not implemented for this database type.")


def _effective_settings(instance: Any, path: Path | None) -> object:
    return resolve_connection_settings(
        instance.db_type, instance.connection, _runtime_defaults(instance.db_type, path)
    )


def _diagnostic_report_path(database_id: str, scenario: str, requested: Path | None) -> Path:
    if requested is not None:
        return requested if requested.suffix.lower() == ".html" else requested.with_suffix(".html")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", database_id).strip("._") or "database"
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    directory = Path("reports")
    base = f"{safe_id}_{scenario}_{stamp}"
    candidate = directory / f"{base}.html"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{base}_{suffix:02d}.html"
        suffix += 1
    return candidate


def _configure_windows_pipe_encoding() -> None:
    """Keep human-readable UTF-8 output when Windows redirects CLI streams."""
    if not sys.platform.startswith("win"):
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not stream.isatty() and callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: CredentialProvider | None = None,
    driver: Any | None = None,
    credential_store: CredentialStore | None = None,
) -> int:
    _configure_windows_pipe_encoding()
    parser = argparse.ArgumentParser(
        prog="dba", description=("Common DBA commands. Missing database credentials can be entered "
                                "interactively when a command first needs them."),
        epilog=("Common DBA Commands:\n"
                "  dba list\n"
                "  dba check <database_id>\n"
                "  dba status <database_id>\n"
                "  dba diagnose <database_id> slow\n"
                "  dba diagnose <database_id> disk-growth\n\n"
                "Advanced commands: dba -h"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"dba-toolkit {__version__}")
    parser.add_argument("--inventory", type=Path, default=Path("config/inventory.yaml"))
    parser.add_argument("--sql-root", type=Path, default=Path("sql"))
    parser.add_argument(
        "--settings", type=Path, help="Optional runtime YAML; safe defaults when omitted"
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("help", help="Show common DBA tasks")
    listing = commands.add_parser("list", help="List inventory")
    _add_output_options(listing)
    listing.add_argument("--type", choices=[t.value for t in DatabaseType])
    listing.add_argument("--env")
    listing.add_argument("--group")
    listing.add_argument("--tag")
    showing = commands.add_parser("show", help="Show one inventory instance")
    showing.add_argument("database_id")
    _add_output_options(showing)
    validation = commands.add_parser("validate", help="Validate inventory and all formal SQL scripts")
    _add_output_options(validation)
    driver_info_command = commands.add_parser(
        "driver-info", help="Inspect local Python/ODBC environment without connecting"
    )
    _add_output_options(driver_info_command)
    doctor_command = commands.add_parser("doctor", help="Check local Toolkit runtime without credentials or connections")
    doctor_command.add_argument("--database", action="append", choices=[t.value for t in DatabaseType],
                                help="Check a configured database runtime (repeatable)")
    _add_output_options(doctor_command)
    runtime_command = commands.add_parser("runtime-info", help="Show local runtime components without connecting")
    _add_output_options(runtime_command)
    driver_command = commands.add_parser("driver", help="Inspect and manage explicitly installed local database drivers")
    driver_commands = driver_command.add_subparsers(dest="driver_command", required=True)
    driver_commands.add_parser("list", help="List active and backup managed drivers")
    driver_check = driver_commands.add_parser("check", help="Check driver packages and native requirements")
    driver_check.add_argument("--database", choices=("mysql", "postgresql", "oracle", "sqlserver"))
    driver_install = driver_commands.add_parser("install", help="Install a trusted local driver ZIP")
    driver_install.add_argument("--file", type=Path, required=True)
    driver_rollback = driver_commands.add_parser("rollback", help="Switch to the previous driver version")
    driver_rollback.add_argument("--database", choices=("mysql", "postgresql", "oracle", "sqlserver"), required=True)
    for subcommand in driver_commands.choices.values():
        _add_output_options(subcommand)
    credentials = commands.add_parser(
        "credentials", help="Manage secure credentials; missing database aliases can be enrolled on demand",
        description=("Database commands can enroll a missing credential alias when first needed. "
                     "Use these commands for sessions and advanced credential administration."),
    )
    credential_commands = credentials.add_subparsers(dest="credential_command", required=True)
    credential_set = credential_commands.add_parser(
        "set", help="Advanced: store or update one credential explicitly"
    )
    credential_set.add_argument("credential_alias")
    credential_set.add_argument("--backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    _add_output_options(credential_set)
    credential_list = credential_commands.add_parser("list", help="List stored credential aliases")
    credential_list.add_argument("--backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    credential_show = credential_commands.add_parser("show", help="Show credential metadata only")
    credential_show.add_argument("credential_alias")
    credential_show.add_argument("--backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    _add_output_options(credential_show)
    credential_delete = credential_commands.add_parser("delete", help="Delete one stored credential")
    credential_delete.add_argument("credential_alias")
    credential_delete.add_argument("--backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    _add_output_options(credential_delete)
    credential_test = credential_commands.add_parser("test", help="Test credential retrieval only")
    credential_test.add_argument("credential_alias")
    credential_test.add_argument("--backend", choices=("auto", "os", "encrypted", "sqlite"), default="auto")
    _add_output_options(credential_test)
    _add_output_options(credential_commands.choices["list"])
    credential_init = credential_commands.add_parser("init", help="Initialize encrypted local credential storage")
    credential_init.add_argument("--backend", choices=("encrypted", "sqlite"), required=True)
    _add_output_options(credential_init)
    credential_backend = credential_commands.add_parser("backend", help="Show credential backend availability")
    _add_output_options(credential_backend)
    credential_rekey = credential_commands.add_parser("rekey", help="Change encrypted store master password")
    credential_rekey.add_argument("--backend", choices=("encrypted", "sqlite"), required=True)
    _add_output_options(credential_rekey)
    credential_unlock = credential_commands.add_parser("unlock", help="Unlock encrypted credentials for a short session")
    credential_unlock.add_argument("--backend", choices=("encrypted", "sqlite"), default="encrypted")
    credential_unlock.add_argument("--ttl", default=None, help="Absolute session lifetime, 5m to 8h (default 30m)")
    credential_unlock.add_argument("--renew", action="store_true", help="Re-authenticate and replace an active session")
    _add_output_options(credential_unlock)
    credential_status = credential_commands.add_parser("status", help="Show credential session status")
    _add_output_options(credential_status)
    credential_lock = credential_commands.add_parser("lock", help="Lock and clear the active credential session")
    _add_output_options(credential_lock)
    check = commands.add_parser("check", help="Check runtime, connection and detected database version")
    check.add_argument("database_id")
    _add_credential_backend(check)
    _add_output_options(check)
    status_command = commands.add_parser("status", help="Show lightweight current database status")
    status_command.add_argument("database_id")
    _add_credential_backend(status_command)
    _add_output_options(status_command)
    connection = commands.add_parser(
        "test-connection", help="Connect, verify basic server identity and close"
    )
    connection.add_argument("database_id")
    _add_credential_backend(connection)
    _add_output_options(connection)
    collect = commands.add_parser("collect", help="Run a reviewed database collector")
    collect.add_argument("database_id")
    _add_credential_backend(collect)
    collect.add_argument("collector", choices=tuple(dict.fromkeys((*COLLECTORS, *PG_COLLECTORS,
                                                                    *MYSQL_COLLECTORS,
                                                                    *ORACLE_COLLECTORS))))
    _add_output_options(collect)
    diagnose = commands.add_parser("diagnose", help="Read-only slow, active SQL, Top SQL, Memory Grant, disk-growth, replication or cached execution-plan diagnosis")
    diagnose.add_argument("database_id")
    _add_credential_backend(diagnose)
    diagnose.add_argument("scenario", choices=("slow", "plan", "active-sql", "top-sql", "memory-grant", "disk-growth", "replication"))
    diagnose.add_argument("--session", type=int, help="Positive session ID for plan diagnosis")
    diagnose.add_argument("--sql-id", help="Oracle cached-plan SQL_ID (requires --child-number)")
    diagnose.add_argument("--child-number", type=int, help="Oracle cached-plan child cursor number")
    diagnose.add_argument("--inst-id", type=int, help="Oracle current RAC instance ID")
    diagnose.add_argument("--con-id", type=int, help="Oracle current container ID (12c+)")
    diagnose.add_argument("--plan-hash", type=int, help="Expected Oracle PLAN_HASH_VALUE")
    diagnose.add_argument("--metric", choices=TOP_SQL_METRIC_CHOICES, default=None,
                          help="Top SQL cumulative metric (default: SQL Server/Oracle cpu_time; PostgreSQL total_exec_time; MySQL sum_timer_wait)")
    diagnose.add_argument("--top", type=int, default=10, help="Top SQL row limit (1-100)")
    diagnose.add_argument("--sort", choices=("cpu", "logical-reads", "elapsed"), default="cpu",
                          help="Active SQL primary CLI ranking (HTML always includes all three rankings)")
    diagnose.add_argument("--report", type=Path, help="Output HTML report file")
    diagnose.add_argument("--format", choices=("console", "json"), default="console",
                          help=argparse.SUPPRESS)
    diagnose.add_argument("--verbose", action="store_true",
                          help="Include secondary signals and collector quality in the incident summary")
    _add_output_options(diagnose)
    credential_migrate = credential_commands.add_parser("migrate", help="Copy and verify a legacy Store into a new SQLite Store")
    credential_migrate.add_argument("--source", type=Path, required=True)
    credential_migrate.add_argument("--destination", type=Path, required=True)
    _add_output_options(credential_migrate)
    for credential_parser in credential_commands.choices.values():
        credential_parser.add_argument("--store-path", type=Path, default=None,
                                       help="Explicit encrypted Store path; never inferred from another Store")
    args = parser.parse_args(argv)
    if args.command == "driver":
        from dba.driver_manager import DriverManager

        manager = DriverManager.from_environment()
        try:
            if args.driver_command == "list":
                driver_result = manager.list()
            elif args.driver_command == "check":
                database = DatabaseType(args.database) if args.database else None
                driver_result = manager.check(database)
            elif args.driver_command == "install":
                driver_result = manager.install(args.file)
            else:
                driver_result = manager.rollback(DatabaseType(args.database))
            _emit(args, "driver", driver_result)
            if args.driver_command == "check":
                return 0 if all(item.get("status") in {"READY", "SYSTEM_DRIVER_AVAILABLE"}
                                for item in driver_result.get("checks", [])) else 1
            return 0
        except ToolkitError as exc:
            _emit(args, "driver", {"success": False, "error_type": exc.error_type,
                                    "error_message": str(exc)})
            return 1
    from dba.sqlite_credentials import SQLiteCredentialStore
    backend = getattr(args, "backend", getattr(args, "credential_backend", "auto"))
    store_path = getattr(args, "store_path", None) or getattr(args, "credential_store_path", None)
    encrypted_store = (SQLiteCredentialStore(store_path) if backend == "sqlite" else
                       EncryptedFileCredentialStore(store_path) if store_path else EncryptedFileCredentialStore())
    if args.command is None or args.command == "help":
        parser.print_help()
        return 0
    if args.command == "driver-info":
        info = driver_info()
        _emit(args, "driver-info", info)
        return 0 if info["ready"] else 1
    if args.command == "doctor":
        if args.database:
            configured = {DatabaseType(item) for item in args.database}
        else:
            inventory = Inventory.load(args.inventory)
            configured = {instance.db_type for instance in inventory.instances}
        doctor_report = doctor(configured_databases=configured)
        _emit(args, "doctor", doctor_report)
        return 0 if doctor_report["overall"] in {
            RuntimeStatus.READY.value, RuntimeStatus.READY_WITH_WARNING.value
        } else 1
    if args.command == "runtime-info":
        _emit(args, "runtime-info", runtime_info())
        return 0
    if args.command == "credentials":
        injected_store = credential_store is not None
        os_store = credential_store if credential_store is not None else KeyringCredentialStore()
        store: CredentialStore = os_store
        try:
            alias = str(getattr(args, "credential_alias", ""))
            credential_output: dict[str, Any]
            session_credential: Credential | None = None
            selected = "injected" if injected_store else getattr(args, "backend", "auto")
            session_client = CredentialSessionClient()
            if selected == "encrypted" and args.credential_command in {"init", "set", "delete", "rekey"}:
                encrypted_store._deny_write()
            if args.credential_command == "migrate":
                from dba.sqlite_credentials import migrate_store
                count = migrate_store(EncryptedFileCredentialStore(args.source),
                                      SQLiteCredentialStore(args.destination), prompt_new_master_password())
                _emit(args, "credentials migrate", {"status": "VERIFIED_COPY", "records": count,
                                                     "provider_switched": False})
                return 0
            if args.credential_command == "status":
                credential_output = session_client.status()
                _emit(args, "credentials status", credential_output)
                return 0
            if args.credential_command == "lock":
                previous = session_client.status()
                session_client.lock()
                credential_output = {"status": "ALREADY_LOCKED" if previous.get("status") == "LOCKED" else "LOCKED",
                                     "cleared": previous.get("status") != "LOCKED"}
                _emit(args, "credentials lock", credential_output)
                return 0
            if args.credential_command == "unlock":
                if injected_store:
                    raise ToolkitError("CredentialBackendUnsupported",
                                       "Credential sessions require the encrypted credential store.")
                ttl_seconds = parse_ttl(args.ttl)
                existing = session_client.status()
                if existing.get("status") == "UNLOCKED" and not args.renew:
                    session_client.list_metadata(encrypted_store.path)
                    credential_output = {**existing, "status": "ALREADY_UNLOCKED"}
                    _emit(args, "credentials unlock", credential_output)
                    return 0
                encrypted_store._require_crypto()
                if not encrypted_store.exists:
                    raise ToolkitError("CredentialStoreNotInitialized",
                                       "Initialize the encrypted credential store before unlocking it.")
                _, decrypted_credentials = encrypted_store._read_container()
                store_key = encrypted_store.session_key()
                try:
                    if isinstance(encrypted_store, SQLiteCredentialStore):
                        credential_output = session_client.load(
                            decrypted_credentials, ttl_seconds, encrypted_store.path, store_key,
                            store_backend="sqlite", store_binding=encrypted_store.binding)
                    else:
                        credential_output = session_client.load(
                            decrypted_credentials, ttl_seconds, encrypted_store.path, store_key)
                finally:
                    encrypted_store.lock()
                    decrypted_credentials.clear()
                    store_key = b""
                credential_output = {**credential_output, "ttl_seconds": ttl_seconds,
                                     "scope": "Current OS user", "secrets_persisted": False}
                _emit(args, "credentials unlock", credential_output)
                return 0
            if args.credential_command == "backend":
                if injected_store:
                    credential_output = {
                        "backend": os_store.backend_name,
                        "available": os_store.available(),
                    }
                    _emit(args, "credentials backend", credential_output)
                    return 0
                from dba.credentials import credential_store_status

                credential_output = credential_store_status(os_store)
                credential_output["credential_session"] = session_client.status()
                _emit(args, "credentials backend", credential_output)
                return 0
            if args.credential_command == "init":
                if injected_store:
                    raise ToolkitError("CredentialBackendUnsupported",
                                       "Encrypted store initialization is unavailable with an injected credential store.")
                encrypted_store._require_crypto()
                if not encrypted_store.available():
                    raise ToolkitError("CredentialCryptoUnavailable",
                                       "Encrypted credential storage requires cryptography and argon2-cffi from the local wheelhouse.")
                if encrypted_store.exists:
                    raise ToolkitError("CredentialStoreExists",
                                       "Credential store already exists; it was not overwritten.")
                password = prompt_new_master_password()
                encrypted_store.initialize(password)
                encrypted_store.lock()
                credential_output = {"backend": encrypted_store.backend_name,
                                     "path": str(encrypted_store.path), "status": "READY",
                                     "cipher": "AES-256-GCM", "kdf": "Argon2id", "format": 1}
                _emit(args, "credentials init", credential_output)
                return 0
            if args.credential_command == "rekey":
                if injected_store:
                    raise ToolkitError("CredentialBackendUnsupported",
                                       "Encrypted store rekey is unavailable with an injected credential store.")
                encrypted_store._require_crypto()
                if not encrypted_store.exists:
                    raise ToolkitError("CredentialStoreNotInitialized",
                                       "No encrypted credential store exists to rekey.")
                old_password = prompt_master_password("Current master password: ")
                new_password = prompt_new_master_password()
                encrypted_store.rekey(old_password, new_password)
                encrypted_store.lock()
                session_client.lock()
                credential_output = {"backend": encrypted_store.backend_name, "status": "READY"}
                _emit(args, "credentials rekey", credential_output)
                return 0

            if injected_store:
                store = os_store
            elif selected in {"encrypted", "sqlite"}:
                store = encrypted_store
                encrypted_store._require_crypto()
                if selected == "sqlite" and session_client.status().get("status") == "UNLOCKED":
                    from dba.sqlite_credentials import SQLiteSessionStore
                    session_client.list_metadata(encrypted_store.path)
                    store = SQLiteSessionStore(encrypted_store.path, session_client)
                if args.credential_command in {"show", "test"}:
                    session_credential = session_client.get(alias, encrypted_store.path)
                if args.credential_command == "set" and not encrypted_store.exists:
                    raise ToolkitError("CredentialStoreNotInitialized",
                                       "Initialize encrypted credential storage before storing credentials.")
            elif selected == "os":
                store = os_store
                if not store.available():
                    raise ToolkitError("CredentialStoreUnavailable", "OS secure credential store is unavailable.")
            elif args.credential_command == "list":
                credential_rows: dict[str, dict[str, str]] = {}
                if os_store.available():
                    for item in os_store.aliases():
                        saved = os_store.get(item)
                        credential_rows[item] = {"alias": item,
                                                 "username": saved.username if saved else "",
                                                 "backend": os_store.backend_name}
                session_status = session_client.status()
                if session_status.get("status") == "UNLOCKED":
                    metadata_rows = session_client.list_metadata(encrypted_store.path)
                    if metadata_rows is None:
                        raise ToolkitError("CredentialSessionUnavailable", "Credential session metadata is unavailable.")
                    for metadata_row in metadata_rows:
                        credential_rows[metadata_row["alias"]] = {
                            "alias": metadata_row["alias"], "username": metadata_row["username"],
                            "backend": encrypted_store.backend_name,
                        }
                elif encrypted_store.exists:
                    for item in encrypted_store.aliases():
                        saved = encrypted_store.get(item)
                        credential_rows[item] = {"alias": item, "username": saved.username if saved else "",
                                                 "backend": encrypted_store.backend_name}
                credential_output = {"credentials": [credential_rows[key]
                                                       for key in sorted(credential_rows)]}
            elif not injected_store:
                os_credential = os_store.get(alias) if os_store.available() else None
                if os_credential is not None:
                    store = os_store
                elif args.credential_command != "set" and selected in {"auto", "encrypted"}:
                    session_credential = session_client.get(alias, encrypted_store.path)
                    if session_credential is not None:
                        store = encrypted_store
                    elif encrypted_store.exists:
                        store = encrypted_store
                    elif args.credential_command != "set":
                        raise ToolkitError("CredentialNotFound", "Credential alias was not found in secure stores.")
                elif encrypted_store.exists:
                    encrypted_credential = encrypted_store.get(alias)
                    if encrypted_credential is not None:
                        store = encrypted_store
                    elif args.credential_command != "set":
                        raise ToolkitError("CredentialNotFound", "Credential alias was not found in secure stores.")
                    elif os_store.available():
                        store = os_store
                    else:
                        store = encrypted_store
                elif args.credential_command == "set" and os_store.available():
                    store = os_store
                elif args.credential_command == "set":
                    raise ToolkitError(
                        "CredentialStoreUnavailable",
                        "No writable secure backend is available. The legacy JSON Store is read-only; explicitly initialize and select SQLite with `dba credentials init --backend sqlite`.",
                    )
                else:
                    raise ToolkitError("CredentialNotFound", "Credential alias was not found in secure stores.")

            if args.credential_command == "list":
                if selected == "injected":
                    credential_output = {"credentials": [
                        {"alias": item,
                         "username": (stored.username if (stored := store.get(item)) is not None else ""),
                         "backend": store.backend_name}
                        for item in store.aliases()
                    ]}
                elif selected in {"encrypted", "sqlite"}:
                    session_status = session_client.status()
                    if session_status.get("status") == "UNLOCKED":
                        metadata_rows = session_client.list_metadata(encrypted_store.path)
                        if metadata_rows is None:
                            raise ToolkitError("CredentialSessionUnavailable", "Credential session metadata is unavailable.")
                        credential_output = {"credentials": [
                            {"alias": item["alias"], "username": item["username"],
                             "backend": encrypted_store.backend_name}
                            for item in metadata_rows
                        ]}
                    else:
                        credential_output = {"credentials": [
                            {"alias": item, "username": (stored.username if (stored := store.get(item)) else ""),
                             "backend": store.backend_name}
                            for item in store.aliases()
                        ]}
                elif selected != "auto":
                    credential_output = {"credentials": [
                        {"alias": item,
                         "username": (stored.username if (stored := store.get(item)) is not None else ""),
                         "backend": store.backend_name}
                        for item in store.aliases()
                    ]}
            if args.credential_command == "list":
                pass
            elif args.credential_command == "show":
                stored = session_credential or store.get(alias)
                if stored is None:
                    raise ToolkitError("CredentialNotFound", "Credential alias was not found.")
                credential_output = {
                    "alias": alias,
                    "username": stored.username,
                    "backend": store.backend_name,
                    "status": "STORED",
                }
            elif args.credential_command == "test":
                if session_credential is None and store.get(alias) is None:
                    raise ToolkitError("CredentialNotFound", "Credential alias was not found.")
                credential_output = {"alias": alias, "backend": store.backend_name, "status": "READY"}
            elif args.credential_command == "delete":
                if store.get(alias) is None:
                    raise ToolkitError("CredentialNotFound", "Credential alias was not found.")
                if not confirm_action(f"Delete credential {alias}? [y/N] "):
                    raise ToolkitError("Cancelled", "Credential deletion cancelled.")
                store.delete(alias)
                if store is encrypted_store and selected != "sqlite":
                    session_client.lock()
                credential_output = {"alias": alias, "backend": store.backend_name, "deleted": True}
            else:
                exists = store.get(alias) is not None
                if exists and not confirm_action("Credential already exists. Update credential? [y/N] "):
                    raise ToolkitError("Cancelled", "Credential update cancelled.")
                store.set(alias, prompt_credential_for_storage())
                if store is encrypted_store and selected != "sqlite":
                    session_client.lock()
                credential_output = {
                    "alias": alias,
                    "backend": store.backend_name,
                    "stored": True,
                    "updated": exists,
                }
            _emit(args, f"credentials {args.credential_command}", credential_output)
            return 0
        except ToolkitError as exc:
            _emit(args, f"credentials {args.credential_command}",
                  {"success": False, "error_type": exc.error_type,
                   "error_message": str(exc),
                   "alias": getattr(args, "credential_alias", None),
                   "backend": getattr(store, "backend_name", None)})
            return 1
    logger = configure_logging()
    started = time.monotonic()
    replication_log_fields: dict[str, Any] = {}
    account_security_credential: Credential | None = None
    instance_for_hint = None
    adapter_instance: Any | None = None
    version_profile_for_hint: ResolvedDiagnosticProfile | None = None
    diagnostic_timing: dict[str, Any] | None = None
    triage_completed_at: str | None = None
    capture_started_at: str | None = None
    capture_finished_at: str | None = None
    try:
        inventory = Inventory.load(args.inventory)
        if args.command == "list":
            rows = [redact(asdict(instance)) for instance in
                    inventory.filter(args.type, args.env, args.group, args.tag)]
            _emit(args, "list", {"databases": rows})
        elif args.command == "show":
            shown = inventory.get(args.database_id)
            effective = public_connection_settings(
                shown.db_type, _effective_settings(shown, args.settings)
            )
            effective["host"] = shown.host
            effective["port"] = shown.port
            if shown.db_type == DatabaseType.ORACLE:
                effective["target"] = shown.service_name or shown.sid
            else:
                effective["target"] = shown.database or (
                    "master" if shown.db_type == DatabaseType.SQLSERVER else
                    "postgres" if shown.db_type == DatabaseType.POSTGRESQL else None
                )
            if shown.db_type == DatabaseType.SQLSERVER:
                effective["selected_driver"] = runtime_info()["sqlserver_odbc"]["selected"]
            _emit(args, "show", redact({**asdict(shown), "effective_connection": effective}))
        elif args.command == "validate":
            validate_diagnostic_settings(args.settings)
            registry = SqlScriptRegistry(args.sql_root)
            scripts = registry.validate()
            profile_errors = PROFILE_RESOLVER.validate()
            if profile_errors:
                raise ValidationError("Version profile registry invalid: " + "; ".join(profile_errors))
            replication_errors = validate_replication_profiles(registry, PROFILE_RESOLVER.bindings)
            if replication_errors:
                raise ValidationError("Replication profile registry invalid: "
                                       + "; ".join(replication_errors))
            slow_errors = validate_slow_profiles(registry, PROFILE_RESOLVER.bindings)
            if slow_errors:
                raise ValidationError("Slow profile registry invalid: " + "; ".join(slow_errors))
            disk_errors = validate_disk_profiles(registry, PROFILE_RESOLVER.bindings)
            if disk_errors:
                raise ValidationError("Disk-growth profile registry invalid: " + "; ".join(disk_errors))
            plan_errors = validate_plan_profiles(registry, PROFILE_RESOLVER.bindings)
            plan_errors += validate_oracle_plan_profiles(registry, PROFILE_RESOLVER.bindings)
            if plan_errors:
                raise ValidationError("Execution-plan profile registry invalid: " + "; ".join(plan_errors))
            top_sql_errors = validate_top_sql_profiles(registry, PROFILE_RESOLVER.bindings)
            if top_sql_errors:
                raise ValidationError("Top SQL profile registry invalid: " + "; ".join(top_sql_errors))
            active_sql_errors = validate_active_sql_profiles(registry, PROFILE_RESOLVER.bindings)
            if active_sql_errors:
                raise ValidationError("Active SQL profile registry invalid: " + "; ".join(active_sql_errors))
            memory_grant_errors = validate_memory_grant_profiles(registry, PROFILE_RESOLVER.bindings)
            if memory_grant_errors:
                raise ValidationError("Memory Grant profile registry invalid: " + "; ".join(memory_grant_errors))
            warnings = inventory.version_family_warnings()
            validation_result = {"success": True, "instance_count": len(inventory.instances),
                                "sql_script_count": len(scripts), "warnings": warnings}
            if args.json:
                _emit(args, "validate", validation_result)
            else:
                print(f"Validation OK: {len(inventory.instances)} instances, {len(scripts)} SQL scripts")
                for warning in warnings:
                    print(f"WARNING: {warning}")
        elif args.command in {"test-connection", "collect", "diagnose", "check", "status"}:
            instance = inventory.get(args.database_id)
            instance_for_hint = instance
            if not instance.enabled:
                raise ToolkitError("InstanceDisabled", "Inventory instance is disabled.")
            if instance.db_type not in {DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                        DatabaseType.MYSQL, DatabaseType.ORACLE}:
                raise ToolkitError("NotImplemented", "Not implemented for this database type.")
            active_provider = provider or (
                _InjectedCredentialProvider(credential_store) if credential_store is not None else
                CredentialResolver(
                    KeyringCredentialStore(), encrypted_store,
                    PromptCredentialProvider(), getattr(args, "credential_backend", "auto"),
                    database_id=instance.id,
                )
            )
            if args.command == "diagnose":
                oracle_plan_args = (args.sql_id, args.child_number, args.inst_id,
                                    args.con_id, args.plan_hash)
                if args.scenario == "plan" and instance.db_type == DatabaseType.SQLSERVER:
                    if type(args.session) is not int or not 1 <= args.session <= 32767:
                        raise ToolkitError("InvalidSession", "SQL Server Plan diagnosis requires --session with a positive session ID.")
                    if any(value is not None for value in oracle_plan_args):
                        raise ToolkitError("InvalidPlanIdentity", "Oracle Plan identity options cannot be used with SQL Server.")
                elif args.scenario == "plan" and instance.db_type == DatabaseType.ORACLE:
                    if args.session is not None:
                        raise ToolkitError("InvalidSession", "Oracle Plan uses --sql-id and --child-number, not --session.")
                    if (not isinstance(args.sql_id, str) or not re.fullmatch(r"[A-Za-z0-9]{13}", args.sql_id)
                            or type(args.child_number) is not int or args.child_number < 0):
                        raise ToolkitError("InvalidPlanIdentity", "Oracle Plan diagnosis requires a valid --sql-id and non-negative --child-number.")
                    for name, value, minimum in (("--inst-id", args.inst_id, 1),
                                                 ("--con-id", args.con_id, 0),
                                                 ("--plan-hash", args.plan_hash, 1)):
                        if value is not None and (type(value) is not int or value < minimum):
                            raise ToolkitError("InvalidPlanIdentity", f"{name} is outside the accepted range.")
                elif args.scenario == "plan":
                    raise ToolkitError("NotImplemented", "Plan diagnosis is not implemented for this database type.")
                elif args.session is not None or any(value is not None for value in oracle_plan_args):
                    raise ToolkitError("InvalidPlanIdentity", "Plan identity options are only supported for plan diagnosis.")
                if args.scenario == "top-sql" and instance.db_type not in {
                    DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL, DatabaseType.MYSQL, DatabaseType.ORACLE,
                }:
                    raise ToolkitError("NotImplemented", "Top SQL collection is not implemented for this database type.")
                if args.scenario == "active-sql" and instance.db_type != DatabaseType.SQLSERVER:
                    raise ToolkitError("NotImplemented", "Active SQL collection is not implemented for this database type.")
                if args.scenario == "memory-grant" and instance.db_type != DatabaseType.SQLSERVER:
                    raise ToolkitError("NotImplemented", "Memory Grant diagnosis is not implemented for this database type.")
                if args.scenario in {"top-sql", "active-sql"} and (
                        type(args.top) is not int or not 1 <= args.top <= TOP_LIMIT):
                    raise ToolkitError("InvalidTopN", f"Top SQL row limit must be between 1 and {TOP_LIMIT}.")
            if driver is None:
                runtime = database_runtime_status(instance.db_type)
                if runtime.get("status") not in {
                    RuntimeStatus.READY.value, RuntimeStatus.READY_WITH_WARNING.value
                }:
                    if instance.db_type == DatabaseType.SQLSERVER:
                        runtime_detail = (
                            "SQL Server runtime unavailable. "
                            f"pyodbc: {runtime.get('status', 'UNKNOWN')}; "
                            f"ODBC Driver 18: {runtime.get('driver18_status', 'UNKNOWN')}; "
                            f"ODBC Driver 17: {runtime.get('driver17_status', 'UNKNOWN')}. "
                            "Run `dba doctor` for details."
                        )
                    else:
                        runtime_detail = (
                            f"{instance.db_type.value} runtime unavailable: "
                            f"{runtime.get('package', 'Python driver')} "
                            f"is {runtime.get('status', 'UNKNOWN')}. "
                            "Run `dba doctor` for details."
                        )
                    raise ToolkitError(
                        "DriverUnavailable",
                        runtime_detail,
                    )
            if instance.db_type == DatabaseType.ORACLE:
                if args.command == "collect" and args.collector not in ORACLE_COLLECTORS:
                    raise ToolkitError("NotImplemented", "Collector not implemented for Oracle.")
                oracle_settings = _effective_settings(instance, args.settings)
                assert isinstance(oracle_settings, OracleSettings)
                adapter_instance = OracleAdapter(
                    instance, active_provider, SqlScriptRegistry(args.sql_root),
                    oracle_settings, driver=driver)
            elif instance.db_type == DatabaseType.MYSQL:
                if args.command == "collect" and args.collector not in MYSQL_COLLECTORS:
                    raise ToolkitError("NotImplemented", "Collector not implemented for MySQL.")
                mysql_settings = _effective_settings(instance, args.settings)
                assert isinstance(mysql_settings, MySQLSettings)
                adapter_instance = MySQLAdapter(
                    instance, active_provider, SqlScriptRegistry(args.sql_root),
                    mysql_settings, driver=driver)
            elif instance.db_type == DatabaseType.POSTGRESQL:
                if args.command == "collect" and args.collector not in PG_COLLECTORS:
                    raise ToolkitError("NotImplemented", "Collector not implemented for PostgreSQL.")
                pg_settings = _effective_settings(instance, args.settings)
                assert isinstance(pg_settings, PostgreSQLSettings)
                adapter_instance = PostgreSQLAdapter(
                    instance, active_provider, SqlScriptRegistry(args.sql_root),
                    pg_settings, driver=driver)
            else:
                if args.command == "collect" and args.collector not in COLLECTORS:
                    raise ToolkitError("NotImplemented", "Collector not implemented for SQL Server.")
                sql_settings = _effective_settings(instance, args.settings)
                assert isinstance(sql_settings, SQLServerSettings)
                adapter_instance = SQLServerAdapter(
                    instance, active_provider, SqlScriptRegistry(args.sql_root),
                    sql_settings, driver=driver)
            assert adapter_instance is not None
            if args.command in {"collect", "diagnose", "test-connection", "check", "status"}:
                account_security_credential = active_provider.get_credential(instance.credential_alias)
                adapter_instance.provider = _CachedCredentialProvider(account_security_credential)
            output: dict[str, Any] = {}
            with adapter_instance as adapter:
                if args.command == "check":
                    detection = DEFAULT_VERSION_DETECTOR.detect(
                        instance.db_type, adapter.registry, adapter
                    )
                    version_profile = build_version_profile(
                        instance.db_type, instance.version_family, detection.detected,
                        error_type=detection.error_type,
                        error_message=detection.error_message,
                        database_error=detection.database_error,
                    )
                    runtime = database_runtime_status(instance.db_type)
                    effective = public_connection_settings(instance.db_type, adapter.settings)
                    effective.update({
                        "host": instance.host,
                        "port": instance.port,
                        "database": instance.database,
                        "status": "CONNECTED",
                    })
                    version_status = version_profile.get("version_status", "UNKNOWN")
                    check_status = ("VERSION_MISMATCH" if version_status == VersionStatus.MISMATCH.value
                                    else "VERSION_UNAVAILABLE" if detection.error_type else "READY")
                    output = {
                        "success": check_status == "READY",
                        "status": check_status,
                        "database_id": instance.id,
                        "connection": {"status": "CONNECTED"},
                        "runtime": runtime,
                        "effective_connection": effective,
                        "version_profile": version_profile,
                        "account_security": _account_security(adapter),
                        "permissions_guide": "docs/DATABASE_PERMISSIONS.md",
                    }
                elif args.command == "status":
                    collectors = (ORACLE_COLLECTORS if isinstance(adapter, OracleAdapter)
                                  else MYSQL_COLLECTORS if isinstance(adapter, MySQLAdapter)
                                  else PG_COLLECTORS if isinstance(adapter, PostgreSQLAdapter)
                                  else COLLECTORS)
                    names = ("server-info", "database-status", "active-sessions",
                             "active-requests", "long-running")
                    collector_adapter = cast(Any, adapter)
                    results = {name: collectors[name](collector_adapter).collect(instance)
                               for name in names if name in collectors}
                    warnings = tuple(
                        f"{name}: " + (f"collection failed ({result.error_type or 'unknown'})"
                                        if not result.success else "result truncated at configured row limit")
                        for name, result in results.items()
                        if not result.success or result.truncated
                    )
                    sessions = results.get("active-sessions")
                    requests = results.get("active-requests")
                    long_running = results.get("long-running")
                    databases = results.get("database-status")
                    state_counts: dict[str, int] = {}
                    if databases and databases.success:
                        for row in databases.rows:
                            state_name = str(row.get("state", "UNKNOWN"))
                            state_counts[state_name] = state_counts.get(state_name, 0) + 1
                    server_info = results.get("server-info")
                    version_value = None
                    if server_info and server_info.rows:
                        version_value = str(server_info.rows[0].get("product_version") or
                                            server_info.rows[0].get("version") or "UNKNOWN")
                    status_result = StatusResult(
                        {"id": instance.id, "db_type": instance.db_type.value,
                         "target": f"{instance.host}:{instance.port}"}, version_value,
                        "READY", sessions.row_count if sessions else None,
                        requests.row_count if requests else None,
                        long_running.row_count if long_running else None,
                        state_counts, "PARTIAL" if warnings else "READY", warnings,
                    )
                    output = asdict(status_result) | {"success": not warnings}
                elif args.command == "test-connection":
                    output = {
                        "database_id": instance.id,
                        "success": True,
                        "version": adapter.get_server_version(),
                        "preflight": adapter.preflight_checks,
                        "privilege_classification": _account_security(adapter),
                        "account_security": _account_security(adapter),
                        "credential": {
                            "alias": instance.credential_alias,
                            "backend": getattr(active_provider, "last_backend", None)
                            or "Provided Credential Provider",
                        },
                    }
                    effective = public_connection_settings(instance.db_type, adapter.settings)
                    effective["host"] = instance.host
                    effective["port"] = instance.port
                    if isinstance(adapter, SQLServerAdapter):
                        effective["driver"] = adapter.odbc_driver
                        effective["database"] = instance.database or "master"
                    else:
                        if instance.db_type == DatabaseType.ORACLE:
                            effective["target"] = instance.service_name or instance.sid
                        else:
                            effective["target"] = instance.database or (
                                "postgres" if instance.db_type == DatabaseType.POSTGRESQL else None
                            )
                    output["connection"] = effective
                elif args.command == "diagnose":
                    report: DiagnosticReport
                    plan_xml: str | None = None
                    if args.scenario == "disk-growth":
                        profile, detection = resolve_profile_for_adapter(adapter, "disk-growth")
                        if profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Disk-growth diagnosis requires a declared version_family matching the detected server version.",
                            )
                        if profile.support_state != SupportState.SUPPORTED:
                            raise ToolkitError(
                                "FeatureUnsupported",
                                "No verified disk-growth diagnostic profile is available for this database version.",
                            )
                        cast(Any, adapter).disk_growth_profile = profile
                        stage_started = time.monotonic()
                        capture_started_at = datetime.now(timezone.utc).isoformat()
                        report = collect_disk_growth_phase(adapter, emergency=True)
                        report.details.update({"capture_started_at": capture_started_at,
                                               "snapshot_consistency": "BEST_EFFORT"})
                        supplemental_expected = bool(report.details.get("supplemental_not_collected"))
                        report.details["supplemental_pending"] = supplemental_expected
                        triage_completed_at = datetime.now(timezone.utc).isoformat()
                        first_summary_monotonic: float | None = None
                        if args.format == "console" and not args.json and not args.quiet:
                            print(render_emergency_triage(report, capture_started_at=capture_started_at), flush=True)
                            first_summary_monotonic = time.monotonic()
                        disk_results = cast(tuple[CollectorResult, ...],
                                            getattr(report, "_collector_results", ()))
                        if report.details.get("emergency_evidence_complete"):
                            report = collect_disk_growth_phase(cast(Any, adapter), emergency=False,
                                                               existing=disk_results)
                        else:
                            report.details["full_profile_evidence_complete"] = False
                            report.details["full_profile_evidence_status"] = "INCOMPLETE"
                        report.details.update({"capture_started_at": capture_started_at,
                                               "triage_completed_at": triage_completed_at,
                                               "capture_finished_at": datetime.now(timezone.utc).isoformat(),
                                               "snapshot_consistency": "BEST_EFFORT",
                                               "supplemental_pending": False})
                        if args.format == "console" and not args.json and not args.quiet:
                            completion = ("补充证据：已完成" if report.details.get(
                                "full_profile_evidence_status") == "COMPLETE" else
                                "补充证据采集结束，仍有缺失或不完整项；详见报告") if supplemental_expected \
                                else "无额外补充查询；HTML 报告已生成"
                            print(completion, flush=True)
                        capture_finished_at = report.details["capture_finished_at"]
                        diagnostic_timing = {
                            "time_to_first_summary_ms": round((first_summary_monotonic - started) * 1000)
                            if first_summary_monotonic is not None else None,
                            "collection_ms": round((time.monotonic() - stage_started) * 1000),
                            "full_collection_ms": round((time.monotonic() - started) * 1000),
                            "report_write_ms": None,
                        }
                        output = {"success": not report.details["incomplete"], "report": asdict(report)}
                        output["version_profile"] = asdict(profile)
                    elif args.scenario == "slow":
                        profile, detection = resolve_profile_for_adapter(adapter, "slow")
                        if profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Slow diagnosis requires a declared version_family matching the detected server version.",
                            )
                        if profile.support_state != SupportState.SUPPORTED:
                            raise ToolkitError(
                                "FeatureUnsupported",
                                "No verified slow diagnostic profile is available for this database version.",
                            )
                        cast(Any, adapter).slow_profile = profile
                        stage_started = time.monotonic()
                        capture_started_at = datetime.now(timezone.utc).isoformat()
                        emergency_names = emergency_collectors_for_adapter(adapter)
                        phase_collect: Any
                        phase_build: Any
                        if isinstance(adapter, OracleAdapter):
                            phase_collect = collect_oracle_slow_phase
                            phase_build = build_oracle_slow_phase_report
                        elif isinstance(adapter, MySQLAdapter):
                            phase_collect = collect_mysql_slow_phase
                            phase_build = build_mysql_slow_phase_report
                        elif isinstance(adapter, PostgreSQLAdapter):
                            phase_collect = collect_pg_slow_phase
                            phase_build = build_pg_slow_phase_report
                        else:
                            phase_collect = collect_sqlserver_slow_phase
                            phase_build = build_sqlserver_slow_phase_report
                        collected = phase_collect(cast(Any, adapter), emergency_names)
                        report = phase_build(adapter, collected, emergency=True)
                        # Oracle's previous-SQL lookup is optional evidence, but it
                        # is part of the root SQL context shown in the first CLI
                        # summary. Collect it before that summary when core evidence
                        # is complete; it remains optional for the safety gate.
                        if (isinstance(adapter, OracleAdapter)
                                and cast(Any, report).details.get("emergency_evidence_complete") is True):
                            collected = phase_collect(cast(Any, adapter), ("previous_sql",), collected)
                            report = phase_build(adapter, collected, emergency=True)
                        supplemental_expected = bool(cast(Any, report).details.get(
                            "supplemental_not_collected"))
                        cast(Any, report).details.update({"capture_started_at": capture_started_at,
                                                          "snapshot_consistency": "BEST_EFFORT",
                                                          "supplemental_pending": supplemental_expected,
                                                          "diagnostic_profile": {
                                                              "sql_profile_id": profile.sql_profile_id,
                                                              "parser_contract_id": profile.parser_contract_id,
                                                              "runtime_variant": profile.runtime_variant,
                                                          }})
                        emergency_candidates = build_priority_candidates(
                            report, include_blocking_victims=True)
                        emergency_triage = build_performance_triage(report)
                        cast(Any, report).details.update({
                            "priority_candidates": emergency_candidates,
                            "performance_triage": emergency_triage,
                            "manual_action_decisions": manual_action_decisions(report),
                        })
                        emergency_triage_fingerprint = _triage_summary_fingerprint(
                            emergency_candidates, emergency_triage)
                        triage_completed_at = datetime.now(timezone.utc).isoformat()
                        first_summary_monotonic = None
                        if args.format == "console" and not args.json and not args.quiet:
                            threshold = getattr(adapter.settings, "long_running_seconds", 10)
                            print(render_emergency_triage(
                                report, emergency_candidates, capture_started_at=capture_started_at,
                                slow_threshold=threshold), flush=True)
                            first_summary_monotonic = time.monotonic()
                        core_complete = cast(Any, report).details.get("emergency_evidence_complete") is True
                        if core_complete:
                            full_names = tuple(dict.fromkeys(
                                required_collectors_for_adapter(adapter, ())
                                + optional_collectors_for_adapter(adapter)))
                            collected = phase_collect(cast(Any, adapter), full_names, collected)
                            report = phase_build(cast(Any, adapter), collected, emergency=False)
                            if isinstance(adapter, MySQLAdapter):
                                if args.format == "console" and not args.json and not args.quiet:
                                    print("正在采集根阻塞最近 SQL...", flush=True)
                                recent_result = collect_mysql_recent_statements(adapter, report)
                                collected = (*collected, recent_result)
                                report = phase_build(cast(Any, adapter), collected, emergency=False)
                                cast(Any, report).details["recent_statements"] = list(recent_result.rows)
                                cast(Any, report).details["recent_statements_elapsed_ms"] = recent_result.elapsed_ms
                                if args.format == "console" and not args.json and not args.quiet:
                                    print(render_mysql_recent_statements(recent_result.rows,
                                                                         recent_result.elapsed_ms), flush=True)
                        else:
                            cast(Any, report).details["full_profile_evidence_complete"] = False
                            cast(Any, report).details["full_profile_evidence_status"] = "INCOMPLETE"
                            if isinstance(adapter, MySQLAdapter):
                                cast(Any, report).details["recent_statements"] = [{
                                    "evidence_status": "NOT_COLLECTED_CORE_INCOMPLETE",
                                    "reason": "Required emergency evidence was incomplete; recent SQL was not queried.",
                                    "statements": [],
                                }]
                        final_candidates = build_priority_candidates(report, include_blocking_victims=True)
                        cast(Any, report).details.update({"priority_candidates": final_candidates,
                                               "diagnostic_profile": {
                                                   "sql_profile_id": profile.sql_profile_id,
                                                   "parser_contract_id": profile.parser_contract_id,
                                                   "runtime_variant": profile.runtime_variant,
                                               },
                                               "manual_action_decisions": manual_action_decisions(report),
                                               "capture_started_at": capture_started_at,
                                               "triage_completed_at": triage_completed_at,
                                               "capture_finished_at": datetime.now(timezone.utc).isoformat(),
                                               "snapshot_consistency": "BEST_EFFORT",
                                               "supplemental_pending": False})
                        final_triage = build_performance_triage(report)
                        cast(Any, report).details["performance_triage"] = final_triage
                        if (args.format == "console" and not args.json and not args.quiet
                                and _triage_summary_fingerprint(final_candidates, final_triage)
                                != emergency_triage_fingerprint):
                            threshold = getattr(adapter.settings, "long_running_seconds", 10)
                            print("【补充证据后的分诊更新】", flush=True)
                            print(render_emergency_triage(
                                report, final_candidates, capture_started_at=capture_started_at,
                                slow_threshold=threshold), flush=True)
                        if args.format == "console" and not args.json and not args.quiet:
                            completion = ("补充证据：已完成" if cast(Any, report).details.get(
                                "full_profile_evidence_status") == "COMPLETE" else
                                "补充证据采集结束，仍有缺失或不完整项；详见报告") if supplemental_expected \
                                else "无额外补充查询；HTML 报告已生成"
                            print(completion, flush=True)
                        capture_finished_at = cast(Any, report).details["capture_finished_at"]
                        diagnostic_timing = {
                            "time_to_first_summary_ms": round((first_summary_monotonic - started) * 1000)
                            if first_summary_monotonic is not None else None,
                            "collection_ms": round((time.monotonic() - stage_started) * 1000),
                            "full_collection_ms": round((time.monotonic() - started) * 1000),
                            "report_write_ms": None,
                        }
                        output = {"success": not cast(Any, report).details["incomplete"], "report": asdict(report)}
                        output["version_profile"] = asdict(profile)
                    elif args.scenario == "replication":
                        report = diagnose_replication(adapter)
                        severities = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}
                        replication_log_fields = {
                            "replication_role": report.summary.local_role,
                            "replication_health": report.summary.overall,
                            "replication_issue_count": len(report.issues),
                            "replication_highest_severity": max(
                                (issue.severity for issue in report.issues),
                                key=lambda severity: severities.get(str(severity), -1),
                                default="INFO",
                            ),
                        }
                        output = {"success": report.summary.overall != "UNKNOWN",
                                  "report": asdict(report)}
                    elif args.scenario == "active-sql":
                        assert isinstance(adapter, SQLServerAdapter)
                        active_profile, _ = resolve_profile_for_adapter(adapter, "active-sql")
                        if active_profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError("VersionVerificationRequired",
                                "Active SQL diagnosis requires a declared version family matching the detected server version.")
                        if active_profile.support_state != SupportState.SUPPORTED:
                            raise ToolkitError("FeatureSupportUnknown",
                                "No verified SQL Server Active SQL profile is available for this version.")
                        active_result = SQLServerActiveSQLCollector(adapter, active_profile).collect(
                            instance, args.sort, args.top)
                        active_data = active_result.to_dict()
                        report = ActiveSQLDiagnosticReport(instance, active_result.collection_timestamp,
                            "active-sql", (), {"version": active_profile.detected_raw_version,
                                "active_sql": active_data,
                                "samples": [{"collector": "sqlserver_active_sql",
                                    "success": active_result.collection_status == "COLLECTED",
                                    "truncated": active_result.truncated,
                                    "row_count": active_result.candidate_count,
                                    "finished_at": active_result.collection_timestamp.isoformat()}],
                                "incomplete": active_result.completeness != "COMPLETE"})
                        report_json = asdict(report)
                        report_json["details"].pop("active_sql", None)
                        output = {"success": active_result.collection_status == "COLLECTED",
                                  "report": report_json, "active_sql": active_data,
                                  "version_profile": asdict(active_profile)}
                    elif args.scenario == "memory-grant":
                        assert isinstance(adapter, SQLServerAdapter)
                        memory_profile, _ = resolve_profile_for_adapter(adapter, "memory-grant")
                        if (memory_profile.version_status != VersionStatus.VERIFIED
                                or memory_profile.support_state != SupportState.SUPPORTED):
                            raise ToolkitError("VersionVerificationRequired",
                                "A verified supported SQL Server Memory Grant profile is required.")
                        memory_result = SQLServerMemoryGrantCollector(
                            adapter, memory_profile).collect(instance)
                        memory_data = memory_result.to_dict()
                        report = MemoryGrantDiagnosticReport(
                            instance, datetime.now(timezone.utc), "memory-grant", (),
                            {"version": memory_result.database_version,
                             "memory_grant": memory_data,
                             "samples": [
                                 {"collector": "sqlserver_memory_grant_waiting",
                                  "success": memory_result.waiting.success,
                                  "truncated": memory_result.waiting.truncated,
                                  "row_count": memory_result.waiting.row_count,
                                  "error_type": memory_result.waiting.error_type,
                                  "finished_at": memory_result.waiting.finished_at},
                                 {"collector": "sqlserver_memory_grant_granted",
                                  "success": memory_result.granted.success,
                                  "truncated": memory_result.granted.truncated,
                                  "row_count": memory_result.granted.row_count,
                                  "error_type": memory_result.granted.error_type,
                                  "finished_at": memory_result.granted.finished_at},
                             ],
                             "incomplete": memory_result.completeness != "COMPLETE",
                             "snapshot_consistency": "SEQUENTIAL_BEST_EFFORT_NOT_ATOMIC",
                             "health_assessment": "NOT_ASSESSED"})
                        report_json = asdict(report)
                        report_json["details"].pop("memory_grant", None)
                        output = {
                            "success": memory_result.success and memory_result.completeness == "COMPLETE",
                            "report": report_json,
                            "memory_grant": memory_data,
                            "version_profile": asdict(memory_profile),
                        }
                        if not memory_result.success:
                            output["error_type"] = (memory_result.waiting.error_type
                                                     or memory_result.granted.error_type or "CollectionFailed")
                            output["error_message"] = (memory_result.waiting.error_message
                                                        or memory_result.granted.error_message or "Collection failed.")
                    elif args.scenario == "top-sql":
                        assert isinstance(adapter, (SQLServerAdapter, PostgreSQLAdapter, MySQLAdapter, OracleAdapter))
                        top_profile, detection = resolve_profile_for_adapter(adapter, "top-sql")
                        if top_profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError("VersionVerificationRequired",
                                "Top SQL diagnosis requires a declared version family matching the detected server version.")
                        if top_profile.support_state != SupportState.SUPPORTED:
                            raise ToolkitError("FeatureSupportUnknown",
                                "No verified Top SQL profile is available for this database version.")
                        if (instance.db_type == DatabaseType.SQLSERVER
                                and isinstance(adapter, SQLServerAdapter)):
                            metric = args.metric or "cpu_time"
                            top_result = SQLServerTopSQLCollector(adapter, top_profile).collect(
                                instance, metric, args.top)
                        elif (instance.db_type == DatabaseType.POSTGRESQL
                              and isinstance(adapter, PostgreSQLAdapter)):
                            metric = args.metric or "total_exec_time"
                            top_result = PostgreSQLTopSQLCollector(adapter, top_profile).collect(
                                instance, metric, args.top)
                        elif instance.db_type == DatabaseType.MYSQL and isinstance(adapter, MySQLAdapter):
                            metric = args.metric or "sum_timer_wait"
                            top_result = MySQLTopSQLCollector(adapter, top_profile).collect(
                                instance, metric, args.top)
                        elif instance.db_type == DatabaseType.ORACLE and isinstance(adapter, OracleAdapter):
                            metric = args.metric or "cpu_time"
                            top_result = OracleTopSQLCollector(adapter, top_profile).collect(
                                instance, metric, args.top)
                        else:
                            raise ToolkitError("UnsupportedTopSQLAdapter",
                                               "Top SQL is available only for SQL Server, PostgreSQL, MySQL, and Oracle.")
                        collector_name = {
                            DatabaseType.SQLSERVER: "sqlserver_top_sql",
                            DatabaseType.POSTGRESQL: "postgresql_top_sql",
                            DatabaseType.MYSQL: "mysql_top_sql",
                            DatabaseType.ORACLE: "oracle_top_sql",
                        }[instance.db_type]
                        capture_started_at = top_result.collection_timestamp.isoformat()
                        capture_finished_at = capture_started_at
                        report = TopSQLDiagnosticReport(instance, top_result.collection_timestamp, "top-sql", (), {
                            "version": top_profile.detected_raw_version,
                            "top_sql": top_result.to_dict(),
                            "samples": [{"collector": collector_name, "success":
                                top_result.collection_status.value == "COLLECTED", "truncated":
                                "TRUNCATED" in {item.value for item in top_result.limitations},
                                "row_count": len(top_result.items),
                                "finished_at": top_result.collection_timestamp.isoformat()}],
                            "incomplete": top_result.completeness.value != "COMPLETE",
                        })
                        output = {"success": top_result.collection_status.value == "COLLECTED",
                                  "report": asdict(report), "top_sql": top_result.to_dict(),
                                  "version_profile": asdict(top_profile)}
                    elif isinstance(adapter, SQLServerAdapter):
                        assert isinstance(adapter, SQLServerAdapter)
                        if args.session is None:
                            raise ToolkitError("InvalidSession", "Plan diagnosis requires --session <session_id>.")
                        plan_profile, detection = resolve_profile_for_adapter(adapter, "plan")
                        version_profile_for_hint = plan_profile
                        if plan_profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Plan diagnosis requires a declared version_family matching the detected server version.",
                            )
                        if plan_profile.support_state != SupportState.SUPPORTED:
                            raise ToolkitError(
                                "FeatureSupportUnknown",
                                "No reviewed SQL Server Plan profile is available for this version.",
                            )
                        plan_report, plan_xml = diagnose_plan(adapter, args.session, plan_profile)
                        report = cast(DiagnosticReport, plan_report)
                        # A valid probe with no reusable plan is an ordinary diagnostic
                        # outcome, not a database/CLI failure. Availability is explicit
                        # in plan_status and plan_available.
                        output = {"success": True, "report": asdict(plan_report)}
                    elif isinstance(adapter, OracleAdapter):
                        plan_profile, detection = resolve_profile_for_adapter(adapter, "plan")
                        version_profile_for_hint = plan_profile
                        if (plan_profile.version_status != VersionStatus.VERIFIED
                                or plan_profile.support_state != SupportState.SUPPORTED):
                            raise ToolkitError("FeatureSupportUnknown",
                                "No verified Oracle cached-plan profile is available for this server version.")
                        assert args.sql_id is not None and args.child_number is not None
                        oracle_plan_report = diagnose_oracle_plan(
                            adapter, plan_profile, args.sql_id, args.child_number,
                            inst_id=args.inst_id, con_id=args.con_id,
                            plan_hash_value=args.plan_hash)
                        report = cast(DiagnosticReport, oracle_plan_report)
                        output = {"success": True, "report": asdict(oracle_plan_report)}
                    else:
                        raise ToolkitError("NotImplemented", "Plan diagnosis is not implemented for this adapter.")
                else:
                    result = (ORACLE_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, OracleAdapter)
                              else MYSQL_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, MySQLAdapter)
                              else PG_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, PostgreSQLAdapter)
                              else COLLECTORS[args.collector](adapter).collect(instance))
                    output = asdict(result)
            if args.command == "diagnose":
                report_path = _diagnostic_report_path(instance.id, args.scenario, args.report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                incident_summary = build_incident_summary(report, str(report_path))
                cast(Any, report).details["manual_action_decisions"] = manual_action_decisions(report)
                output["incident_summary"] = asdict(incident_summary)
                output["report_path"] = str(report_path)
                plan_artifact_path: Path | None = None
                if args.scenario == "plan" and plan_xml:
                    plan_artifact_path = report_path.with_suffix(".sqlplan")
                model = build_report_model(report, incident_summary,
                                           plan_artifact=(plan_artifact_path.name
                                                          if plan_artifact_path else None))
                html_content = render_bounded(model, HtmlReportRenderer().render)
                report_write_started = time.monotonic()
                with report_path.open("x", encoding="utf-8") as report_file:
                    report_file.write(html_content)
                report_write_duration_ms = round((time.monotonic() - report_write_started) * 1000)
                report_written_at = datetime.now(timezone.utc).isoformat()
                if diagnostic_timing is not None:
                    diagnostic_timing["report_write_duration_ms"] = report_write_duration_ms
                    diagnostic_timing["total_report_ms"] = round((time.monotonic() - started) * 1000)
                    cast(Any, report).details["report_written_at"] = report_written_at
                    cast(Any, report).details["diagnostic_timings_ms"] = diagnostic_timing
                    output["report"] = asdict(report)
                    output["capture_started_at"] = capture_started_at
                    output["triage_completed_at"] = triage_completed_at
                    output["capture_finished_at"] = capture_finished_at
                    output["report_written_at"] = report_written_at
                    output["snapshot_consistency"] = "BEST_EFFORT"
                    output["diagnostic_timings_ms"] = diagnostic_timing
                    output["triage_time_to_first_summary_ms"] = diagnostic_timing["time_to_first_summary_ms"]
                if plan_artifact_path is not None and plan_xml is not None:
                    created_plan = False
                    try:
                        with plan_artifact_path.open("x", encoding="utf-8", newline="") as plan_file:
                            created_plan = True
                            plan_file.write(plan_xml)
                        output["plan_artifact_path"] = str(plan_artifact_path)
                    except Exception:
                        if created_plan:
                            plan_artifact_path.unlink(missing_ok=True)
                        report_path.unlink(missing_ok=True)
                        raise
                if args.format == "console" and not args.json:
                    if args.scenario == "active-sql":
                        print(render_active_sql_human(output["active_sql"], str(report_path)))
                    elif args.scenario == "memory-grant":
                        print(render_memory_grant_human(output["memory_grant"], str(report_path)))
                    elif args.scenario == "top-sql":
                        print(render_human("top-sql", output["top_sql"]))
                    elif args.scenario == "replication":
                        from dba.presentation import render_replication_summary

                        print(render_replication_summary(report, report_path=str(report_path),
                                                         verbose=args.verbose))
                    elif args.scenario == "plan" and instance.db_type == DatabaseType.ORACLE:
                        print(render_oracle_plan_human(report, str(report_path)))
                    elif args.scenario in {"slow", "disk-growth"}:
                        if args.quiet:
                            print(f"{'READY' if output.get('success') else 'PARTIAL'}  {report_path}", flush=True)
                        else:
                            print(f"\n详细中文报告已写入：{report_path}", flush=True)
                    else:
                        print(render_incident_summary(incident_summary, verbose=args.verbose))
                else:
                    _emit(args, "diagnose", _redact_diagnostic_json(output))
            else:
                _emit(args, args.command, redact(output))
            if not output["success"]:
                return 1
        logger.info(
            "event",
            extra={
                "event": {
                    "module": "cli",
                    "result": "ok",
                    "elapsed_time": time.monotonic() - started,
                    "database_id": getattr(args, "database_id", None),
                    **replication_log_fields,
                }
            },
        )
        return 0
    except KeyboardInterrupt:
        interrupted = {
                    "success": False,
                    "error_type": "Interrupted",
                    "error_message": "Operation interrupted.",
                }
        _emit(args, args.command or "help", interrupted)
        return 130
    except (OSError, ValidationError, UnicodeError, ToolkitError) as exc:
        # YAML/OS exceptions may contain supplied content: emit only safe categories.
        detail = (
            str(exc)
            if isinstance(exc, (ValidationError, ToolkitError))
            else "File could not be read"
        )
        failure: dict[str, Any] = {
                    "success": False,
                    "error_type": exc.error_type
                    if isinstance(exc, ToolkitError)
                    else type(exc).__name__,
                    "error_message": detail,
                }
        if version_profile_for_hint is not None:
            failure["version_profile"] = asdict(version_profile_for_hint)
        if isinstance(exc, ToolkitError) and exc.trigger_reason is not None:
            failure["trigger_reason"] = exc.trigger_reason
        database_error = getattr(exc, "database_error", None)
        if database_error is not None:
            vendor_payload = _database_error_payload(database_error)
            if vendor_payload is not None:
                failure["error"] = vendor_payload
                failure["database_type"] = vendor_payload.get("database_type")
                failure["driver"] = vendor_payload.get("driver")
        if instance_for_hint is not None:
            failure["database_id"] = instance_for_hint.id
            failure["target"] = f"{instance_for_hint.host}:{instance_for_hint.port}"
            failure["alias"] = instance_for_hint.credential_alias
            if database_error is not None:
                safe_settings = public_connection_settings(
                    instance_for_hint.db_type,
                    _effective_settings(instance_for_hint, getattr(args, "settings", None)),
                )
                failure["effective_connection"] = {
                    "host": instance_for_hint.host,
                    "port": instance_for_hint.port,
                    "database": instance_for_hint.database or "NOT_SET",
                    "connect_timeout": safe_settings.get("connect_timeout"),
                    "ssl": ("DISABLED" if safe_settings.get("ssl_disabled") is True else
                            "ENABLED" if safe_settings.get("ssl_disabled") is False else "DEFAULT"),
                }
            try:
                selected_driver = database_runtime_status(
                    instance_for_hint.db_type
                ).get("selected_native_driver")
                if selected_driver:
                    failure.setdefault("driver", selected_driver)
                safe_settings = (public_connection_settings(
                    instance_for_hint.db_type,
                    _effective_settings(instance_for_hint, getattr(args, "settings", None)),
                ) if instance_for_hint.db_type in {DatabaseType.SQLSERVER, DatabaseType.POSTGRESQL,
                                                   DatabaseType.MYSQL, DatabaseType.ORACLE} else {})
                if "encrypt" in safe_settings:
                    failure["tls"] = (
                        f"Encrypt={'yes' if safe_settings.get('encrypt') else 'no'}; "
                        f"TrustServerCertificate={'yes' if safe_settings.get('trust_server_certificate') else 'no'}"
                    )
            except (ToolkitError, ValueError):
                pass
            if isinstance(exc, ToolkitError) and exc.error_type == "PrivilegedAccount":
                failure["account"] = (
                    account_security_credential.username if account_security_credential else None
                )
                failure["privilege_classification"] = {
                    "classification": "PRIVILEGED",
                    "trigger_reason": exc.trigger_reason,
                }
            elif isinstance(exc, ToolkitError) and exc.error_type == "PrivilegeClassificationUnknown":
                failure["privilege_classification"] = {
                    "classification": "UNKNOWN", "trigger_reason": exc.trigger_reason,
                }
            elif isinstance(exc, ToolkitError) and exc.error_type == "UnsafeMonitoringAccount":
                failure["account_security"] = {
                    "classification": "NON_PRIVILEGED",
                    "write_capability_detected": exc.trigger_reason,
                }
        _emit(args, args.command or "help", failure)
        logger.error(
            "event",
            extra={
                "event": {
                    "module": "cli",
                    "result": "failed",
                    "elapsed_time": time.monotonic() - started,
                    "error_type": getattr(exc, "error_type", type(exc).__name__),
                    "database_id": instance_for_hint.id if instance_for_hint else None,
                    "target": (f"{instance_for_hint.host}:{instance_for_hint.port}"
                               if instance_for_hint else None),
                    "database_type": getattr(database_error, "database_type", None),
                    "driver": getattr(database_error, "driver", None),
                    "vendor_code": getattr(database_error, "vendor_code", None),
                    "sqlstate": getattr(database_error, "sqlstate", None),
                    "classification": getattr(database_error, "classification", None),
                    "sanitized_message": getattr(database_error, "message", None),
                }
            },
        )
        return 1
