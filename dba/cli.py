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
from dba.incident import build_incident_summary, build_priority_candidates, manual_action_decisions
from dba.inventory import Inventory, ValidationError
from dba.logging import configure_logging, redact
from dba.mysql import MySQLAdapter
from dba.mysql_collectors import COLLECTORS as MYSQL_COLLECTORS
from dba.mysql_collectors import collect_recent_statements as collect_mysql_recent_statements
from dba.mysql_slow import build_slow_phase_report as build_mysql_slow_phase_report
from dba.mysql_slow import collect_slow_phase as collect_mysql_slow_phase
from dba.mysql_slow_reporter import render_recent_statements as render_mysql_recent_statements
from dba.oracle import OracleAdapter
from dba.oracle_collectors import COLLECTORS as ORACLE_COLLECTORS
from dba.oracle_slow import build_slow_phase_report as build_oracle_slow_phase_report
from dba.oracle_slow import collect_slow_phase as collect_oracle_slow_phase
from dba.permissions import (
    ALL_FEATURES,
    OraclePrincipal,
    PermissionStatus,
    assess_permissions,
    feature_supported,
    privilege_reasons,
    render_privileges_sql,
    render_profile_sql,
    render_sql,
    requirements_for,
    requirements_for_profile,
)
from dba.permissions import REGISTRY as PERMISSION_REGISTRY
from dba.postgresql import PostgreSQLAdapter
from dba.postgresql_collectors import COLLECTORS as PG_COLLECTORS
from dba.postgresql_slow import build_slow_phase_report as build_pg_slow_phase_report
from dba.postgresql_slow import collect_slow_phase as collect_pg_slow_phase
from dba.presentation import (
    CheckResult,
    OutputMode,
    StatusResult,
    render_emergency_triage,
    render_human,
    render_incident_summary,
    render_json,
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
from dba.versioning import (
    DEFAULT_VERSION_DETECTOR,
    PROFILE_RESOLVER,
    ProfileResolutionError,
    SupportState,
    VersionStatus,
    build_version_profile,
    resolve_profile_for_adapter,
    version_status,
)


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


def _disk_growth_permission_impact(status: object) -> str:
    value = getattr(status, "value", status)
    if value == PermissionStatus.MISSING.value:
        return "Required disk-growth permissions are missing; core storage evidence cannot be collected."
    if value == PermissionStatus.PARTIAL.value:
        return "Required disk-growth permissions are ready; optional FRA, archive, or redo context may be unavailable."
    if value == PermissionStatus.READY.value:
        return "Required and configured optional disk-growth permissions are available."
    return "Disk-growth permission impact could not be determined from the current assessment."


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
    command.add_argument(
        "--credential-backend", choices=("auto", "os", "encrypted", "interactive"),
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


def _feature_for_command(args: Any) -> str | None:
    if args.command == "diagnose":
        return args.scenario if args.scenario in ALL_FEATURES else "slow"
    if args.command == "collect":
        if args.collector in {"disk_growth", "disk-growth", "log_growth"}:
            return "disk-growth"
        return "slow"
    return None


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


def _permission_matches_evidence(permission: str, evidence: str) -> bool:
    candidate = permission.upper()
    if candidate in evidence:
        return True
    if candidate == "VIEW SERVER STATE":
        return "VIEW SERVER PERFORMANCE STATE" in evidence
    return False


def _grant_account(instance: Any, adapter: Any, credential: Credential | None) -> str | OraclePrincipal:
    if instance.db_type == DatabaseType.ORACLE:
        principal = getattr(adapter, "principal", None)
        if isinstance(principal, OraclePrincipal) and principal.verified:
            return principal
        return OraclePrincipal.unverified(credential.username if credential else None)
    current_user = getattr(adapter, "current_user", None)
    if isinstance(current_user, str) and current_user:
        return current_user
    return credential.username if credential else "monitor"


def _oracle_principal_output(adapter: Any, credential: Credential | None) -> dict[str, Any]:
    principal = getattr(adapter, "principal", None)
    if not isinstance(principal, OraclePrincipal) or not principal.verified:
        principal = OraclePrincipal.unverified(credential.username if credential else None)
    result = principal.to_dict()
    result["credential_username"] = credential.username if credential else None
    return result


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
    credential_set.add_argument("--backend", choices=("auto", "os", "encrypted"), default="auto")
    _add_output_options(credential_set)
    credential_list = credential_commands.add_parser("list", help="List stored credential aliases")
    credential_list.add_argument("--backend", choices=("auto", "os", "encrypted"), default="auto")
    credential_show = credential_commands.add_parser("show", help="Show credential metadata only")
    credential_show.add_argument("credential_alias")
    credential_show.add_argument("--backend", choices=("auto", "os", "encrypted"), default="auto")
    _add_output_options(credential_show)
    credential_delete = credential_commands.add_parser("delete", help="Delete one stored credential")
    credential_delete.add_argument("credential_alias")
    credential_delete.add_argument("--backend", choices=("auto", "os", "encrypted"), default="auto")
    _add_output_options(credential_delete)
    credential_test = credential_commands.add_parser("test", help="Test credential retrieval only")
    credential_test.add_argument("credential_alias")
    credential_test.add_argument("--backend", choices=("auto", "os", "encrypted"), default="auto")
    _add_output_options(credential_test)
    _add_output_options(credential_commands.choices["list"])
    credential_init = credential_commands.add_parser("init", help="Initialize encrypted local credential storage")
    credential_init.add_argument("--backend", choices=("encrypted",), required=True)
    _add_output_options(credential_init)
    credential_backend = credential_commands.add_parser("backend", help="Show credential backend availability")
    _add_output_options(credential_backend)
    credential_rekey = credential_commands.add_parser("rekey", help="Change encrypted store master password")
    credential_rekey.add_argument("--backend", choices=("encrypted",), required=True)
    _add_output_options(credential_rekey)
    credential_unlock = credential_commands.add_parser("unlock", help="Unlock encrypted credentials for a short session")
    credential_unlock.add_argument("--backend", choices=("encrypted",), default="encrypted")
    credential_unlock.add_argument("--ttl", default=None, help="Absolute session lifetime, 5m to 8h (default 30m)")
    credential_unlock.add_argument("--renew", action="store_true", help="Re-authenticate and replace an active session")
    _add_output_options(credential_unlock)
    credential_status = credential_commands.add_parser("status", help="Show credential session status")
    _add_output_options(credential_status)
    credential_lock = credential_commands.add_parser("lock", help="Lock and clear the active credential session")
    _add_output_options(credential_lock)
    check = commands.add_parser("check", help="Check runtime, credentials, connection and feature readiness")
    check.add_argument("database_id")
    _add_credential_backend(check)
    _add_output_options(check)
    status_command = commands.add_parser("status", help="Show lightweight current database status")
    status_command.add_argument("database_id")
    _add_credential_backend(status_command)
    _add_output_options(status_command)
    connection = commands.add_parser(
        "test-connection", help="Connect, verify monitoring permissions and close"
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
    diagnose = commands.add_parser("diagnose", help="Read-only slow, disk-growth, replication or SQL Server plan")
    diagnose.add_argument("database_id")
    _add_credential_backend(diagnose)
    diagnose.add_argument("scenario", choices=("slow", "plan", "disk-growth", "replication"))
    diagnose.add_argument("--session", type=int, help="Positive session ID for plan diagnosis")
    diagnose.add_argument("--report", type=Path, help="Output HTML report file")
    diagnose.add_argument("--format", choices=("console", "json"), default="console",
                          help=argparse.SUPPRESS)
    diagnose.add_argument("--verbose", action="store_true",
                          help="Include secondary signals and collector quality in the incident summary")
    _add_output_options(diagnose)
    permissions = commands.add_parser("permissions", help="Inspect monitor permissions or generate review scripts")
    permissions.add_argument("database_id")
    _add_credential_backend(permissions)
    _add_output_options(permissions)
    permission_scope = permissions.add_mutually_exclusive_group()
    permission_scope.add_argument("--feature", choices=ALL_FEATURES)
    permission_scope.add_argument("--all-features", action="store_true")
    permission_action = permissions.add_mutually_exclusive_group()
    permission_action.add_argument("--generate-grant", action="store_true")
    permission_action.add_argument("--generate-revoke", action="store_true")
    args = parser.parse_args(argv)
    encrypted_store = EncryptedFileCredentialStore()
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
                    credential_output = session_client.load(
                        decrypted_credentials, ttl_seconds, encrypted_store.path, store_key
                    )
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
            elif selected == "encrypted":
                store = encrypted_store
                encrypted_store._require_crypto()
                if args.credential_command in {"show", "test"}:
                    session_credential = session_client.get(alias)
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
                    metadata_rows = session_client.list_metadata()
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
                    session_credential = session_client.get(alias)
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
                        "No persistent secure backend is available. Initialize with `dba credentials init --backend encrypted`.",
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
                elif selected == "encrypted":
                    session_status = session_client.status()
                    if session_status.get("status") == "UNLOCKED":
                        metadata_rows = session_client.list_metadata()
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
                if store is encrypted_store:
                    session_client.lock()
                credential_output = {"alias": alias, "backend": store.backend_name, "deleted": True}
            else:
                exists = store.get(alias) is not None
                if exists and not confirm_action("Credential already exists. Update credential? [y/N] "):
                    raise ToolkitError("Cancelled", "Credential update cancelled.")
                store.set(alias, prompt_credential_for_storage())
                if store is encrypted_store:
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
    permission_credential: Credential | None = None
    instance_for_hint = None
    adapter_instance: Any | None = None
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
                effective["selected_driver"] = database_runtime_status(
                    DatabaseType.SQLSERVER
                ).get("selected_native_driver")
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
            if plan_errors:
                raise ValidationError("Execution-plan profile registry invalid: " + "; ".join(plan_errors))
            warnings = inventory.version_family_warnings()
            validation_result = {"success": True, "instance_count": len(inventory.instances),
                                "sql_script_count": len(scripts), "warnings": warnings}
            if args.json:
                _emit(args, "validate", validation_result)
            else:
                print(f"Validation OK: {len(inventory.instances)} instances, {len(scripts)} SQL scripts")
                for warning in warnings:
                    print(f"WARNING: {warning}")
        elif args.command in {"test-connection", "collect", "diagnose", "permissions", "check", "status"}:
            instance = inventory.get(args.database_id)
            instance_for_hint = instance
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
            if (args.command == "permissions" and (args.generate_grant or args.generate_revoke)
                    and args.feature is not None and args.feature != "replication"):
                permission_credential = active_provider.get_credential(instance.credential_alias)
                definitions = PERMISSION_REGISTRY.get(instance.db_type, {})
                requested = [args.feature] if args.feature else list(ALL_FEATURES)
                supported = [feature for feature in requested if feature in definitions]
                rows = [{"feature": feature,
                         "status": PermissionStatus.UNKNOWN.value if feature in definitions else PermissionStatus.UNSUPPORTED.value,
                         "missing_required": (),
                         "impact": definitions[feature].impact if feature in definitions else
                                  "Feature is not implemented for this database type."}
                         for feature in requested]
                generated_sql = render_sql(instance.db_type, supported, permission_credential.username,
                                           instance.database, revoke=args.generate_revoke)
                offline_permission_output = {"database_id": instance.id,
                                             "account": permission_credential.username,
                                             "features": rows, "generated_sql": generated_sql,
                                             "toolkit_executes_grants": False}
                if instance.db_type == DatabaseType.ORACLE:
                    offline_permission_output["monitoring_principal"] = OraclePrincipal.unverified(
                        permission_credential.username
                    ).to_dict()
                    offline_permission_output["guidance"] = (
                        "Oracle session identity was not verified for this offline grant. "
                        "Replace <ORACLE_MONITOR_USER> with the exact database principal before execution."
                    )
                _emit(args, "permissions", offline_permission_output)
                return 0
            if args.command == "diagnose":
                if args.scenario == "plan" and (type(args.session) is not int or not 1 <= args.session <= 32767):
                    raise ToolkitError("InvalidSession", "Plan diagnosis requires --session with a positive session ID.")
                if args.scenario != "plan" and args.session is not None:
                    raise ToolkitError("InvalidSession", "--session is only supported for plan diagnosis.")
                if args.scenario == "plan" and instance.db_type != DatabaseType.SQLSERVER:
                    raise ToolkitError("NotImplemented", "Plan diagnosis is not implemented for this database type.")
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
            if args.command in {"permissions", "collect", "diagnose", "test-connection", "check", "status"}:
                permission_credential = active_provider.get_credential(instance.credential_alias)
                adapter_instance.provider = _CachedCredentialProvider(permission_credential)
            output: dict[str, Any] = {}
            with adapter_instance as adapter:
                if args.command == "permissions":
                    definitions = PERMISSION_REGISTRY.get(instance.db_type, {})
                    requested = ([args.feature] if args.feature else list(ALL_FEATURES))
                    server_version = adapter.get_server_version()
                    replication_profile = None
                    replication_detection = None
                    if "replication" in requested:
                        try:
                            replication_profile, replication_detection = resolve_profile_for_adapter(
                                adapter, "replication"
                            )
                        except ProfileResolutionError:
                            replication_profile = None
                    slow_profile = None
                    slow_detection = None
                    if "slow" in requested:
                        try:
                            slow_profile, slow_detection = resolve_profile_for_adapter(adapter, "slow")
                        except ProfileResolutionError:
                            slow_profile = None
                    disk_profile = None
                    disk_detection = None
                    if "disk-growth" in requested:
                        try:
                            disk_profile, disk_detection = resolve_profile_for_adapter(adapter, "disk-growth")
                        except ProfileResolutionError:
                            disk_profile = None
                    plan_profile = None
                    plan_detection = None
                    if "plan" in requested:
                        try:
                            plan_profile, plan_detection = resolve_profile_for_adapter(adapter, "plan")
                        except ProfileResolutionError:
                            plan_profile = None
                    supported = [feature for feature in requested if feature in definitions
                                 and feature_supported(instance.db_type, feature, server_version)]
                    if not requested:
                        raise ToolkitError("NotImplemented", "No permission features are implemented for this database type.")
                    observed = set(getattr(adapter, "permission_observations", set()))
                    rows = []
                    for feature in requested:
                        if feature in {"replication", "slow", "disk-growth", "plan"} \
                                and feature in definitions:
                            profile = (replication_profile if feature == "replication" else
                                       slow_profile if feature == "slow" else disk_profile)
                            detection = (replication_detection if feature == "replication" else
                                         slow_detection if feature == "slow" else
                                         disk_detection if feature == "disk-growth" else plan_detection)
                            if feature == "plan":
                                profile = plan_profile
                            if profile is None or profile.version_status == VersionStatus.MISMATCH:
                                unknown_row: dict[str, Any] = {
                                    "feature": feature, "status": PermissionStatus.UNKNOWN.value,
                                    "support_state": SupportState.UNKNOWN.value,
                                    "impact": f"No unambiguous verified {feature} permission profile is available.",
                                }
                                if detection and detection.error_type:
                                    unknown_row["version_detection_error"] = {
                                        "error_type": detection.error_type,
                                        "message": detection.error_message,
                                        "database_error": detection.database_error,
                                    }
                                rows.append(unknown_row)
                                continue
                            if profile.support_state == SupportState.UNSUPPORTED:
                                rows.append({"feature": feature, "status": PermissionStatus.UNSUPPORTED.value,
                                             "support_state": profile.support_state.value,
                                             "version_profile": asdict(profile)})
                                continue
                            if profile.support_state != SupportState.SUPPORTED or not profile.permission_profile_id:
                                rows.append({"feature": feature, "status": PermissionStatus.UNKNOWN.value,
                                             "support_state": profile.support_state.value,
                                             "version_profile": asdict(profile),
                                             "impact": "No reviewed permission profile is available for this route."})
                                continue
                            requirement = requirements_for_profile(profile.permission_profile_id)
                            if profile.version_status in {VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN,
                                                          VersionStatus.AUTO_DETECTED, VersionStatus.MISMATCH}:
                                replication_assessment: Any = {
                                    "status": PermissionStatus.UNKNOWN,
                                    "missing_required": (), "missing_optional": (),
                                    "available": (),
                                }
                            else:
                                replication_assessment = assess_permissions(requirement, observed)
                            row = {"feature": feature, **replication_assessment,
                                   "support_state": profile.support_state.value,
                                   "version_profile": asdict(profile),
                                   "required_privileges": requirement.required,
                                   "missing_reasons": privilege_reasons(
                                       requirement, replication_assessment["missing_required"]
                                   )}
                            if (profile.version_status in {VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN}
                                    and detection and detection.error_type):
                                row["version_detection_error"] = {
                                    "error_type": detection.error_type,
                                    "message": detection.error_message,
                                    "database_error": detection.database_error,
                                }
                            if (replication_assessment["status"] != PermissionStatus.READY
                                    and profile.version_status in {VersionStatus.VERIFIED,
                                                                   VersionStatus.UNVERIFIED}):
                                row["impact"] = (_disk_growth_permission_impact(
                                    replication_assessment["status"]
                                ) if feature == "disk-growth" else requirement.impact)
                                row["reference_grant"] = render_profile_sql(
                                    profile.permission_profile_id, instance.db_type,
                                    _grant_account(instance, adapter, permission_credential),
                                    instance.database, server_version,
                                )
                            rows.append(row)
                            continue
                        if feature not in supported:
                            rows.append({"feature": feature, "status": PermissionStatus.UNSUPPORTED.value,
                                         "missing_required": (), "impact": "Feature is not implemented for this database type."})
                            continue
                        evidence = observed
                        requirement = requirements_for(instance.db_type, feature, server_version)
                        assessment = assess_permissions(requirement, evidence)
                        rows.append({"feature": feature, **assessment,
                                     "missing_reasons": privilege_reasons(
                                         requirement, assessment["missing_required"]
                                     ),
                                     **({"impact": _disk_growth_permission_impact(assessment["status"])
                                         if feature == "disk-growth" else definitions[feature].impact}
                                        if assessment["status"] != PermissionStatus.READY else {})})
                    generated: str | None = None
                    if args.generate_grant or args.generate_revoke:
                        assert permission_credential is not None
                        selected_profile = (replication_profile if args.feature == "replication"
                                            else slow_profile if args.feature == "slow"
                                            else disk_profile if args.feature == "disk-growth"
                                            else plan_profile if args.feature == "plan" else None)
                        if args.feature in {"replication", "slow", "disk-growth", "plan"} and selected_profile is not None \
                                and selected_profile.version_status in {VersionStatus.VERIFIED,
                                                                        VersionStatus.UNVERIFIED} \
                                and selected_profile.permission_profile_id:
                            generated = render_profile_sql(
                                selected_profile.permission_profile_id, instance.db_type,
                                _grant_account(instance, adapter, permission_credential),
                                instance.database, server_version,
                                revoke=args.generate_revoke,
                            )
                        elif args.feature is None:
                            if (replication_profile is None
                                    or replication_profile.version_status not in {VersionStatus.VERIFIED,
                                                                                 VersionStatus.UNVERIFIED}
                                    or not replication_profile.permission_profile_id
                                    or slow_profile is None
                                    or slow_profile.version_status not in {VersionStatus.VERIFIED,
                                                                           VersionStatus.UNVERIFIED}
                                    or not slow_profile.permission_profile_id
                                    or disk_profile is None
                                    or disk_profile.version_status not in {VersionStatus.VERIFIED,
                                                                           VersionStatus.UNVERIFIED}
                                    or not disk_profile.permission_profile_id
                                    or ("plan" in supported and (
                                        plan_profile is None
                                        or plan_profile.version_status not in {VersionStatus.VERIFIED,
                                                                               VersionStatus.UNVERIFIED}
                                        or not plan_profile.permission_profile_id))):
                                generated = None
                            else:
                                privileges: list[str] = []
                                for feature in supported:
                                    requirement = (
                                        requirements_for_profile(replication_profile.permission_profile_id)
                                        if feature == "replication" else
                                        requirements_for_profile(slow_profile.permission_profile_id)
                                        if feature == "slow" and slow_profile is not None
                                        and slow_profile.permission_profile_id else
                                        requirements_for_profile(disk_profile.permission_profile_id)
                                        if feature == "disk-growth" and disk_profile is not None
                                        and disk_profile.permission_profile_id else
                                        requirements_for_profile(plan_profile.permission_profile_id)
                                        if feature == "plan" and plan_profile is not None
                                        and plan_profile.permission_profile_id else
                                        requirements_for(instance.db_type, feature, server_version)
                                    )
                                    for privilege in requirement.required:
                                        if privilege not in privileges:
                                            privileges.append(privilege)
                                generated = render_privileges_sql(
                                    instance.db_type, privileges,
                                    _grant_account(instance, adapter, permission_credential),
                                    instance.database, server_version, revoke=args.generate_revoke,
                                )
                        else:
                            generated = render_sql(instance.db_type, supported,
                                                   _grant_account(instance, adapter, permission_credential),
                                                   instance.database, server_version,
                                                   revoke=args.generate_revoke)
                    output = {"database_id": instance.id,
                              "success": True,
                              "account": getattr(adapter, "current_user", None)
                              or (permission_credential.username if permission_credential else None),
                              "features": rows, "generated_sql": generated,
                              "toolkit_executes_grants": False}
                    if instance.db_type == DatabaseType.ORACLE:
                        output["monitoring_principal"] = _oracle_principal_output(
                            adapter, permission_credential
                        )
                elif args.command == "check":
                    detection = DEFAULT_VERSION_DETECTOR.detect(
                        instance.db_type, adapter.registry, adapter
                    )
                    detected = detection.detected
                    version_verification = version_status(instance.version_family, detected)
                    slow_profile = PROFILE_RESOLVER.resolve(
                        instance.db_type, instance.version_family, detected, "slow"
                    ) if instance.db_type in {
                        DatabaseType.SQLSERVER, DatabaseType.MYSQL,
                        DatabaseType.POSTGRESQL, DatabaseType.ORACLE,
                    } else None
                    disk_profile = PROFILE_RESOLVER.resolve(
                        instance.db_type, instance.version_family, detected, "disk-growth"
                    ) if instance.db_type in {
                        DatabaseType.SQLSERVER, DatabaseType.MYSQL,
                        DatabaseType.POSTGRESQL, DatabaseType.ORACLE,
                    } else None
                    try:
                        plan_profile = PROFILE_RESOLVER.resolve(
                            instance.db_type, instance.version_family, detected, "plan"
                        )
                    except ProfileResolutionError:
                        plan_profile = None
                    version_profile = build_version_profile(
                        instance.db_type, instance.version_family, detected,
                        error_type=detection.error_type,
                        error_message=detection.error_message,
                        database_error=detection.database_error,
                    )
                    version_warnings: list[str] = []
                    if instance.version_family is None and detected and detected.normalized_family:
                        version_warnings.append(
                            f"Version family not declared for {instance.id}; add "
                            f'version_family: "{detected.normalized_family}" to Inventory.'
                        )
                    if version_verification == VersionStatus.MISMATCH:
                        version_warnings.append(
                            "Declared and detected database version families differ. "
                            "Permission readiness is UNKNOWN; correct Inventory before diagnosis."
                        )
                    elif version_verification in {VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN}:
                        version_warnings.append(
                            "Database version could not be verified; permission readiness is UNKNOWN."
                        )
                    runtime = database_runtime_status(instance.db_type)
                    runtime_packages = runtime_info()
                    python_component = runtime_packages["python"]
                    driver_component = runtime_packages["drivers"].get(instance.db_type.value, {})
                    observed = set(getattr(adapter, "permission_observations", set()))
                    definitions = PERMISSION_REGISTRY.get(instance.db_type, {})
                    permission_rows: list[dict[str, Any]] = []
                    for feature in ALL_FEATURES:
                        if feature not in definitions:
                            permission_rows.append({"feature": feature, "status": "UNSUPPORTED"})
                            continue
                        if feature == "slow" and slow_profile is not None \
                                and slow_profile.version_status != VersionStatus.VERIFIED:
                            permission_rows.append({
                                "feature": feature, "status": PermissionStatus.UNKNOWN.value,
                                "support_state": slow_profile.support_state.value,
                                "version_profile": asdict(slow_profile),
                                "missing_required": (), "missing_optional": (),
                                "impact": "Version verification is required before assessing slow permissions.",
                            })
                            continue
                        if feature == "disk-growth" and disk_profile is not None \
                                and disk_profile.version_status != VersionStatus.VERIFIED:
                            permission_rows.append({
                                "feature": feature, "status": PermissionStatus.UNKNOWN.value,
                                "support_state": disk_profile.support_state.value,
                                "version_profile": asdict(disk_profile),
                                "missing_required": (), "missing_optional": (),
                                "impact": "Version verification is required before assessing disk-growth permissions.",
                            })
                            continue
                        if feature == "plan" and plan_profile is not None \
                                and plan_profile.version_status != VersionStatus.VERIFIED:
                            permission_rows.append({
                                "feature": feature, "status": PermissionStatus.UNKNOWN.value,
                                "support_state": plan_profile.support_state.value,
                                "version_profile": asdict(plan_profile),
                                "missing_required": (), "missing_optional": (),
                                "impact": "Version verification is required before assessing Plan permissions.",
                            })
                            continue
                        if version_verification in {VersionStatus.MISMATCH, VersionStatus.UNVERIFIED,
                                                    VersionStatus.UNKNOWN}:
                            permission_rows.append({
                                "feature": feature, "status": "UNKNOWN",
                                "missing_required": (), "missing_optional": (),
                                "impact": "Version verification is required before assessing permissions.",
                            })
                            continue
                        if feature == "slow" and slow_profile is not None:
                            if slow_profile.support_state != SupportState.SUPPORTED \
                                    or not slow_profile.permission_profile_id:
                                permission_rows.append({
                                    "feature": "slow", "status": PermissionStatus.UNKNOWN.value,
                                    "support_state": slow_profile.support_state.value,
                                    "version_profile": asdict(slow_profile),
                                    "missing_required": (), "missing_optional": (),
                                    "impact": "No reviewed slow permission contract is available for this route.",
                                })
                                continue
                            requirement = requirements_for_profile(slow_profile.permission_profile_id)
                            assessment = assess_permissions(requirement, observed)
                            permission_rows.append({"feature": feature, **assessment,
                                                    "support_state": slow_profile.support_state.value,
                                                    "version_profile": asdict(slow_profile),
                                                    "required_privileges": requirement.required,
                                                    "missing_reasons": privilege_reasons(
                                                        requirement, assessment["missing_required"]
                                                    ),
                                                    **({"impact": requirement.impact}
                                                       if assessment["status"] != PermissionStatus.READY else {})})
                            continue
                        if feature == "disk-growth" and disk_profile is not None:
                            if disk_profile.support_state != SupportState.SUPPORTED \
                                    or not disk_profile.permission_profile_id:
                                permission_rows.append({
                                    "feature": feature, "status": PermissionStatus.UNSUPPORTED.value,
                                    "support_state": disk_profile.support_state.value,
                                    "version_profile": asdict(disk_profile),
                                    "missing_required": (), "missing_optional": (),
                                    "impact": "No reviewed disk-growth permission contract is available for this route.",
                                })
                                continue
                            requirement = requirements_for_profile(disk_profile.permission_profile_id)
                            assessment = assess_permissions(requirement, observed)
                            permission_rows.append({"feature": feature, **assessment,
                                                    "support_state": disk_profile.support_state.value,
                                                    "version_profile": asdict(disk_profile),
                                                    "required_privileges": requirement.required,
                                                    "missing_reasons": privilege_reasons(
                                                        requirement, assessment["missing_required"]
                                                    ),
                                                    **({"impact": _disk_growth_permission_impact(assessment["status"])}
                                                       if assessment["status"] != PermissionStatus.READY else {})})
                            continue
                        if feature == "plan" and plan_profile is not None:
                            if plan_profile.support_state != SupportState.SUPPORTED \
                                    or not plan_profile.permission_profile_id:
                                permission_rows.append({
                                    "feature": "plan",
                                    "status": (PermissionStatus.UNSUPPORTED.value
                                               if plan_profile.support_state == SupportState.UNSUPPORTED
                                               else PermissionStatus.UNKNOWN.value),
                                    "support_state": plan_profile.support_state.value,
                                    "version_profile": asdict(plan_profile),
                                    "missing_required": (), "missing_optional": (),
                                    "impact": "No reviewed Plan permission contract is available for this route.",
                                })
                                continue
                            requirement = requirements_for_profile(plan_profile.permission_profile_id)
                            assessment = assess_permissions(requirement, observed)
                            plan_permission_row: dict[str, Any] = {
                                "feature": feature, **assessment,
                                "support_state": plan_profile.support_state.value,
                                "version_profile": asdict(plan_profile),
                                "required_privileges": requirement.required,
                                "missing_reasons": privilege_reasons(
                                    requirement, assessment["missing_required"]
                                ),
                            }
                            if assessment["status"] != PermissionStatus.READY:
                                plan_permission_row["impact"] = requirement.impact
                                plan_permission_row["reference_grant"] = render_profile_sql(
                                    plan_profile.permission_profile_id, instance.db_type,
                                    _grant_account(instance, adapter, permission_credential),
                                    instance.database, adapter.get_server_version(),
                                )
                            permission_rows.append(plan_permission_row)
                            continue
                        requirement = requirements_for(
                            instance.db_type, feature, adapter.get_server_version()
                        )
                        assessment = assess_permissions(requirement, observed)
                        permission_rows.append({"feature": feature, **assessment,
                                                "missing_reasons": privilege_reasons(
                                                    requirement, assessment["missing_required"]
                                                ),
                                                **({"impact": definitions[feature].impact}
                                                   if assessment["status"] != PermissionStatus.READY else {})})
                    effective = public_connection_settings(instance.db_type, adapter.settings)
                    effective.update({"host": instance.host, "port": instance.port,
                                      "database": instance.database or (
                                          "master" if instance.db_type == DatabaseType.SQLSERVER else None
                                      ), "target": f"{instance.host}:{instance.port}",
                                      "status": "READY"})
                    if isinstance(adapter, SQLServerAdapter):
                        effective["driver"] = adapter.odbc_driver
                        effective["tls"] = (
                            f"Encrypt={'yes' if effective.get('encrypt') else 'no'}; "
                            f"TrustServerCertificate={'yes' if effective.get('trust_server_certificate') else 'no'}"
                        )
                    states = {row["status"] for row in permission_rows
                              if row["status"] != "UNSUPPORTED"}
                    if version_verification == VersionStatus.MISMATCH:
                        overall = "VERSION_MISMATCH"
                    elif version_verification in {VersionStatus.UNVERIFIED, VersionStatus.UNKNOWN}:
                        overall = "VERSION_UNVERIFIED"
                    elif "UNKNOWN" in states:
                        overall = "PARTIAL"
                    elif "MISSING" in states:
                        overall = "PARTIAL" if "READY" in states else "MISSING"
                    elif "PARTIAL" in states:
                        overall = "PARTIAL"
                    elif runtime.get("status") == RuntimeStatus.READY_WITH_WARNING.value:
                        overall = RuntimeStatus.READY_WITH_WARNING.value
                    else:
                        overall = RuntimeStatus.READY.value
                    warnings = tuple(
                        ([str(runtime["warning"])] if runtime.get("warning") else [])
                        + version_warnings
                    )
                    check_result = CheckResult(
                        {"id": instance.id, "db_type": instance.db_type.value,
                         "environment": instance.environment},
                        {"status": runtime.get("status"),
                         "python": python_component.get("version"),
                         "driver": runtime.get("selected_native_driver") or driver_component.get("package"),
                         "driver_version": driver_component.get("version")},
                        {"alias": instance.credential_alias,
                         "backend": getattr(active_provider, "last_backend", None)
                         or "Provided Credential Provider", "status": "READY"},
                        effective, tuple(permission_rows), overall, warnings,
                        _account_security(adapter), version_profile,
                    )
                    output = asdict(check_result) | {"success": overall in {
                        "READY", "READY_WITH_WARNING", "PARTIAL"
                    }}
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
                    if args.scenario == "disk-growth":
                        profile, detection = resolve_profile_for_adapter(adapter, "disk-growth")
                        if profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Disk-growth diagnosis requires a declared version_family matching the detected server version.",
                                permission_readiness={
                                    "feature": "disk-growth", "status": PermissionStatus.UNKNOWN.value,
                                    "version_detection": asdict(detection),
                                    "version_profile": asdict(profile),
                                },
                            )
                        if profile.support_state != SupportState.SUPPORTED \
                                or not profile.permission_profile_id:
                            raise ToolkitError(
                                "FeatureUnsupported",
                                "No verified disk-growth diagnostic profile is available for this database version.",
                                permission_readiness={
                                    "feature": "disk-growth", "status": PermissionStatus.UNSUPPORTED.value,
                                    "version_profile": asdict(profile),
                                },
                            )
                        requirement = requirements_for_profile(profile.permission_profile_id)
                        assessment = assess_permissions(requirement, adapter.permission_observations)
                        if assessment["status"] == PermissionStatus.MISSING:
                            missing = assessment["missing_required"]
                            grant = render_profile_sql(
                                profile.permission_profile_id, instance.db_type,
                                _grant_account(instance, adapter, permission_credential),
                                instance.database, adapter.get_server_version(),
                            )
                            raise ToolkitError(
                                "MissingPermission",
                                "Required monitoring permissions are missing for disk-growth diagnosis.",
                                permission_readiness={
                                    "feature": "disk-growth", **assessment,
                                    "version_profile": asdict(profile),
                                    "missing_reasons": privilege_reasons(requirement, missing),
                                    "impact": _disk_growth_permission_impact(assessment["status"]),
                                    "suggested_grant": grant,
                                    "toolkit_executed": False,
                                    "dba_approval_required": True,
                                },
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
                        output["permission_readiness"] = {
                            "status": assessment["status"].value,
                            "missing_optional": assessment["missing_optional"],
                            "data_visibility": cast(Any, report).details.get("data_visibility", "UNKNOWN"),
                        }
                    elif args.scenario == "slow":
                        profile, detection = resolve_profile_for_adapter(adapter, "slow")
                        if profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Slow diagnosis requires a declared version_family matching the detected server version.",
                                permission_readiness={
                                    "feature": "slow", "status": PermissionStatus.UNKNOWN.value,
                                    "version_detection": asdict(detection),
                                    "version_profile": asdict(profile),
                                },
                            )
                        if profile.support_state != SupportState.SUPPORTED \
                                or not profile.permission_profile_id:
                            raise ToolkitError(
                                "FeatureUnsupported",
                                "No verified slow diagnostic profile is available for this database version.",
                                permission_readiness={
                                    "feature": "slow", "status": PermissionStatus.UNSUPPORTED.value,
                                    "version_profile": asdict(profile),
                                },
                            )
                        cast(Any, adapter).slow_profile = profile
                        requirement = requirements_for_profile(profile.permission_profile_id)
                        assessment = assess_permissions(requirement, adapter.permission_observations)
                        if assessment["status"] == PermissionStatus.MISSING:
                            missing = assessment["missing_required"]
                            grant = render_privileges_sql(
                                instance.db_type, missing,
                                _grant_account(instance, adapter, permission_credential),
                                instance.database, adapter.get_server_version(),
                            )
                            raise ToolkitError(
                                "MissingPermission",
                                "Required monitoring permissions are missing for slow diagnosis.",
                                permission_readiness={
                                    "feature": "slow", **assessment,
                                    "version_profile": asdict(profile),
                                    "missing_reasons": privilege_reasons(requirement, missing),
                                    "impact": requirement.impact,
                                    "account_security": getattr(adapter, "privilege_classification", None),
                                    "suggested_grant": grant,
                                    "toolkit_executed": False,
                                    "dba_approval_required": True,
                                },
                            )
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
                                                          "supplemental_pending": supplemental_expected})
                        emergency_candidates = build_priority_candidates(report)
                        cast(Any, report).details.update({
                            "priority_candidates": emergency_candidates,
                            "manual_action_decisions": manual_action_decisions(report),
                        })
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
                        cast(Any, report).details.update({"priority_candidates": build_priority_candidates(report),
                                               "manual_action_decisions": manual_action_decisions(report),
                                               "capture_started_at": capture_started_at,
                                               "triage_completed_at": triage_completed_at,
                                               "capture_finished_at": datetime.now(timezone.utc).isoformat(),
                                               "snapshot_consistency": "BEST_EFFORT",
                                               "supplemental_pending": False})
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
                        output["permission_readiness"] = {
                            "status": assessment["status"].value,
                            "missing_optional": assessment["missing_optional"],
                            "data_visibility": cast(Any, report).details.get("data_visibility", "UNKNOWN"),
                        }
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
                    else:
                        assert isinstance(adapter, SQLServerAdapter)
                        if args.session is None:
                            raise ToolkitError("InvalidSession", "Plan diagnosis requires --session <session_id>.")
                        plan_profile, detection = resolve_profile_for_adapter(adapter, "plan")
                        if plan_profile.version_status != VersionStatus.VERIFIED:
                            raise ToolkitError(
                                "VersionVerificationRequired",
                                "Plan diagnosis requires a declared version_family matching the detected server version.",
                                permission_readiness={
                                    "feature": "plan", "status": PermissionStatus.UNKNOWN.value,
                                    "version_detection": asdict(detection),
                                    "version_profile": asdict(plan_profile),
                                    "toolkit_executed_grant": False,
                                },
                            )
                        if plan_profile.support_state != SupportState.SUPPORTED \
                                or not plan_profile.permission_profile_id:
                            raise ToolkitError(
                                "FeatureSupportUnknown",
                                "No reviewed SQL Server Plan profile is available for this version.",
                                permission_readiness={
                                    "feature": "plan", "status": (
                                        PermissionStatus.UNSUPPORTED.value
                                        if plan_profile.support_state == SupportState.UNSUPPORTED
                                        else PermissionStatus.UNKNOWN.value
                                    ),
                                    "version_profile": asdict(plan_profile),
                                    "toolkit_executed_grant": False,
                                },
                            )
                        plan_requirements = requirements_for_profile(plan_profile.permission_profile_id)
                        plan_assessment = assess_permissions(
                            plan_requirements, getattr(adapter, "permission_observations", set())
                        )
                        if plan_assessment["status"] == PermissionStatus.MISSING:
                            missing = plan_assessment["missing_required"]
                            raise ToolkitError(
                                "MissingPermission",
                                "Required monitoring permissions are missing for Plan diagnosis.",
                                permission_readiness={
                                    "feature": "plan", **plan_assessment,
                                    "required_privileges": plan_requirements.required,
                                    "missing_reasons": privilege_reasons(plan_requirements, missing),
                                    "impact": plan_requirements.impact,
                                    "version_profile": asdict(plan_profile),
                                    "suggested_grant": render_profile_sql(
                                        plan_profile.permission_profile_id, instance.db_type,
                                        _grant_account(instance, adapter, permission_credential),
                                        instance.database, adapter.get_server_version(),
                                    ),
                                    "toolkit_executed_grant": False,
                                    "dba_approval_required": True,
                                },
                            )
                        plan_report, plan_xml = diagnose_plan(adapter, args.session, plan_profile)
                        report = plan_report
                        # A valid probe with no reusable plan is an ordinary diagnostic
                        # outcome, not a database/CLI failure. Availability is explicit
                        # in plan_status and plan_available.
                        output = {"success": True, "report": asdict(plan_report)}
                else:
                    result = (ORACLE_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, OracleAdapter)
                              else MYSQL_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, MySQLAdapter)
                              else PG_COLLECTORS[args.collector](adapter).collect(instance)
                              if isinstance(adapter, PostgreSQLAdapter)
                              else COLLECTORS[args.collector](adapter).collect(instance))
                    output = asdict(result)
            if (not output.get("success", True)
                    and output.get("error_type") in {"MissingPermission", "PreflightDenied",
                                                       "QueryDenied", "CapabilityUnavailable"}):
                feature = _feature_for_command(args)
                if feature and instance.db_type in PERMISSION_REGISTRY:
                    definition = PERMISSION_REGISTRY[instance.db_type].get(feature)
                    if definition:
                        evidence_text = str(output.get("error_message", ""))
                        evidence_upper = evidence_text.upper()
                        missing = tuple(item for item in definition.required
                                        if item.upper() in evidence_upper
                                        or (item.upper().startswith("SELECT ")
                                            and item.upper()[7:] in evidence_upper))
                        output["permission_missing"] = {
                            "feature": feature,
                            "missing_privileges": missing,
                            "required_privileges": definition.required,
                            "impact": definition.impact,
                            "suggested_grant": render_sql(instance.db_type, (feature,),
                                                           _grant_account(instance, adapter,
                                                                          permission_credential),
                                                           instance.database) if permission_credential else None,
                            "toolkit_executed_grant": False,
                            "evidence": output.get("error_message"),
                        }
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
                    if args.scenario == "replication":
                        from dba.presentation import render_replication_summary

                        print(render_replication_summary(report, report_path=str(report_path),
                                                         verbose=args.verbose))
                    elif args.scenario in {"slow", "disk-growth"}:
                        if args.quiet:
                            print(f"{'READY' if output.get('success') else 'PARTIAL'}  {report_path}", flush=True)
                        else:
                            print(f"\n详细中文报告已写入：{report_path}", flush=True)
                    else:
                        print(render_incident_summary(incident_summary, verbose=args.verbose))
                else:
                    _emit(args, "diagnose", redact(output))
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
        if (args.command == "permissions" and args.feature == "replication"
                and instance_for_hint is not None
                and instance_for_hint.db_type == DatabaseType.ORACLE
                and isinstance(exc, ToolkitError)
                and exc.error_type in {"DriverUnavailable", "PreflightFailed", "PreflightDenied", "ConnectionFailed",
                                      "ConnectionTimeout", "QueryFailed", "QueryTimeout",
                                      "CapabilityUnavailable"}):
            profile = PROFILE_RESOLVER.resolve(
                DatabaseType.ORACLE, instance_for_hint.version_family, None, "replication"
            )
            error_payload = _database_error_payload(exc.database_error)
            feature_row: dict[str, Any] = {
                "feature": "replication",
                "status": PermissionStatus.UNKNOWN.value,
                "support_state": profile.support_state.value,
                "version_profile": asdict(profile),
                "missing_required": (),
                "missing_optional": (),
                "runtime_verification": "FAILED",
            }
            if profile.support_state == SupportState.SUPPORTED and profile.permission_profile_id:
                requirement = requirements_for_profile(profile.permission_profile_id)
                feature_row.update({
                    "required_privileges": requirement.required,
                    "impact": requirement.impact,
                    "reference_grant": render_profile_sql(
                        profile.permission_profile_id, DatabaseType.ORACLE,
                        _grant_account(instance_for_hint, adapter_instance,
                                       permission_credential),
                        instance_for_hint.database,
                    ),
                })
                principal = _oracle_principal_output(adapter_instance, permission_credential)
                feature_row["monitoring_principal"] = principal
                if principal["verified"]:
                    guidance = (
                        "Runtime version verification failed; permissions are UNKNOWN. "
                        "Review this declared-family reference grant for the verified database principal."
                    )
                else:
                    guidance = (
                        "Oracle session identity could not be verified. Replace "
                        "<ORACLE_MONITOR_USER> with the exact database principal before execution."
                    )
            else:
                feature_row["impact"] = (
                    "Permission guidance is unavailable without a declared canonical Oracle version_family."
                )
                guidance = "Add a canonical version_family to Inventory or restore Oracle connectivity; no profile was guessed."
            offline_output: dict[str, Any] = {
                "success": True,
                "database_id": instance_for_hint.id,
                "account": permission_credential.username if permission_credential else None,
                "monitoring_principal": _oracle_principal_output(
                    adapter_instance, permission_credential
                ),
                "features": [feature_row],
                "version_profile": asdict(profile),
                "runtime_verification": {
                    "status": "FAILED",
                    "classification": "VERSION_OR_IDENTITY_UNVERIFIED",
                    "error_type": exc.error_type,
                    "message": str(exc),
                    "vendor_error": error_payload,
                },
                "guidance": guidance,
                "toolkit_executes_grants": False,
            }
            _emit(args, "permissions", redact(offline_output))
            logger.warning("Oracle permission profile rendered without verified runtime identity/version.",
                           extra={"event": {"module": "permissions", "database_id": instance_for_hint.id,
                                            "version_status": profile.version_status.value,
                                            "error_type": exc.error_type,
                                            "vendor_code": error_payload.get("vendor_code")
                                            if error_payload else None}})
            return 0
        if (args.command == "permissions" and args.feature == "plan"
                and instance_for_hint is not None
                and instance_for_hint.db_type == DatabaseType.SQLSERVER
                and isinstance(exc, ToolkitError)
                and exc.error_type in {"DriverUnavailable", "PreflightFailed", "PreflightDenied",
                                       "ConnectionFailed", "ConnectionTimeout", "QueryFailed",
                                       "QueryTimeout", "CapabilityUnavailable", "MissingPermission"}):
            profile = PROFILE_RESOLVER.resolve(
                DatabaseType.SQLSERVER, instance_for_hint.version_family, None, "plan"
            )
            plan_feature_row: dict[str, Any] = {
                "feature": "plan", "status": PermissionStatus.UNKNOWN.value,
                "support_state": profile.support_state.value,
                "version_profile": asdict(profile), "missing_required": (),
                "missing_optional": (), "runtime_verification": "FAILED",
                "evidence": str(exc),
            }
            if profile.support_state == SupportState.SUPPORTED and profile.permission_profile_id:
                requirement = requirements_for_profile(profile.permission_profile_id)
                observed = set(getattr(adapter_instance, "permission_observations", set()))
                assessment = assess_permissions(requirement, observed)
                missing = tuple(assessment["missing_required"])
                if not missing:
                    detail_upper = str(exc).upper()
                    missing = tuple(item for item in requirement.required if item.upper() in detail_upper)
                status = (PermissionStatus.MISSING if missing else PermissionStatus.UNKNOWN)
                plan_feature_row.update({
                    "status": status.value,
                    "required_privileges": requirement.required,
                    "missing_required": missing,
                    "impact": requirement.impact,
                    "reference_grant": render_profile_sql(
                        profile.permission_profile_id, instance_for_hint.db_type,
                        _grant_account(instance_for_hint, adapter_instance, permission_credential),
                        instance_for_hint.database,
                    ),
                    "toolkit_executed_grant": False,
                    "dba_approval_required": True,
                })
            else:
                plan_feature_row["impact"] = "No reviewed Plan permission contract is available for the declared SQL Server family."
            offline_output = {
                "success": True, "database_id": instance_for_hint.id,
                "features": [plan_feature_row],
                "runtime_verification": {"status": "FAILED", "error_type": exc.error_type,
                                          "message": str(exc)},
                "toolkit_executes_grants": False,
            }
            _emit(args, "permissions", redact(offline_output))
            logger.warning("SQL Server Plan permission profile rendered without runtime version verification.",
                           extra={"event": {"module": "permissions", "database_id": instance_for_hint.id,
                                            "version_status": profile.version_status.value,
                                            "error_type": exc.error_type}})
            return 0
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
        detail_upper = detail.upper()
        if isinstance(exc, ToolkitError) and exc.trigger_reason is not None:
            failure["trigger_reason"] = exc.trigger_reason
        if isinstance(exc, ToolkitError) and exc.permission_readiness is not None:
            failure["permission_readiness"] = exc.permission_readiness
            failure["privilege_classification"] = exc.permission_readiness.get("account_security")
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
                failure["account"] = permission_credential.username if permission_credential else None
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
        if args.command == "permissions" and instance_for_hint is not None:
            definitions = PERMISSION_REGISTRY.get(instance_for_hint.db_type, {})
            requested = [args.feature] if args.feature else list(ALL_FEATURES)
            rows = []
            for feature in requested:
                definition = definitions.get(feature)
                if definition is None:
                    rows.append({"feature": feature, "status": PermissionStatus.UNSUPPORTED.value,
                                 "missing_required": ()})
                    continue
                missing = tuple(item for item in definition.required
                                if item.upper() in detail_upper
                                or (item.upper().startswith("SELECT ")
                                    and item.upper()[7:] in detail_upper))
                status = PermissionStatus.MISSING if missing else PermissionStatus.UNKNOWN
                rows.append({"feature": feature, "status": status.value,
                             "missing_required": missing, "impact": definition.impact})
            failure["features"] = rows
            failure["toolkit_executes_grants"] = False
        if isinstance(exc, ToolkitError) and exc.error_type in {
            "MissingPermission", "PreflightDenied", "QueryDenied", "CapabilityUnavailable"
        }:
            feature = _feature_for_command(args)
            if args.command == "check" and instance_for_hint is not None:
                definitions = PERMISSION_REGISTRY.get(instance_for_hint.db_type, {})
                detail_upper = detail.upper()
                affected = [name for name, definition in definitions.items()
                            if any(_permission_matches_evidence(item, detail_upper)
                                   or (item.upper().startswith("SELECT ")
                                       and _permission_matches_evidence(item[7:], detail_upper))
                                   for item in definition.required)]
                if affected and permission_credential is not None:
                    missing_items = sorted({item for name in affected
                                            for item in requirements_for(
                                                instance_for_hint.db_type, name
                                            ).required
                                            if _permission_matches_evidence(item, detail_upper)
                                            or (item.upper().startswith("SELECT ")
                                                and _permission_matches_evidence(item[7:], detail_upper))})
                    failure["features"] = [
                        {"feature": name, "status": "MISSING",
                         "missing_required": [item for item in requirements_for(
                             instance_for_hint.db_type, name
                         ).required if item in missing_items],
                         "impact": definitions[name].impact}
                        for name in affected
                    ]
                    failure["permission_missing"] = {
                        "feature": ", ".join(affected),
                        "missing_privileges": missing_items,
                        "required_privileges": missing_items,
                        "impact": "; ".join(definitions[name].impact for name in affected),
                        "suggested_grant": render_sql(
                            instance_for_hint.db_type, affected,
                            _grant_account(instance_for_hint, adapter_instance, permission_credential),
                            instance_for_hint.database,
                        ),
                        "toolkit_executed_grant": False,
                        "evidence": detail,
                    }
        if (isinstance(exc, ToolkitError) and exc.database_error is not None
                and exc.error_type == "PermissionInspectionFailed"):
            vendor_detail = getattr(exc.database_error, "to_dict", None)
            if callable(vendor_detail):
                failure["error"] = vendor_detail()
            failure["privilege_classification"] = {"classification": "UNKNOWN"}
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
