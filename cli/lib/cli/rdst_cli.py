"""
Readyset CLI stubs (programmatic surface)

This module defines a small, modern-feeling programmatic interface for a future
`rdst` CLI. Each method returns a structured result and serves as a stub where
integration with cloud agent modules can be added.

No side-effects: Nothing executes long-running operations or requires external
services simply by importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
import toml

# Import UI system
from rich.console import Group

from lib.ui import KeyValueTable, MessagePanel, SimpleTree, get_console

# Local cloud agent modules (will be used by future implementations)
# We import lazily inside methods to avoid side-effects and heavy imports at module load time.


@dataclass
class RdstResult:
    ok: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None

    def __bool__(self):  # allows: if result:
        return self.ok


class CloudAgentClient:
    """Lightweight accessor to cloud agent modules (lazy imports).

    This centralizes how the CLI accesses cloud agent functionality and keeps
    imports lazy to minimize side effects during CLI discovery.
    """

    def __init__(self):
        self._console = get_console()

    # Example accessors (add more as needed)
    def configuration_manager(self):  # -> ConfigurationManager
        from configuration_manager import ConfigurationManager  # local import

        return ConfigurationManager()

    def data_manager_service(self):  # -> DataManagerService
        from lib.data_manager_service.data_manager_service import (
            DataManagerService,
        )  # local import

        return DataManagerService

    def cache_manager(self):  # -> CacheManager
        # Note: CacheManager currently requires initialization context; defer wiring
        from lib.cache_manager.cache_manager import CacheManager  # local import

        return CacheManager

    def llm_manager(self):  # -> LLMManager
        from lib.llm_manager.llm_manager import LLMManager  # local import

        return LLMManager()

    def print_panel(self, title: str, message: str):
        self._console.print(MessagePanel(message, title=title))


# ---- Configure targets persistence helpers ----
PROXY_TYPES = [
    "none",
    "readyset",
    "proxysql",
    "pgbouncer",
    "tunnel",
    "custom",
]

ENGINES = ["postgresql", "mysql"]


def normalize_db_type(db: Optional[str]) -> Optional[str]:
    if db is None:
        return None
    s = db.lower()
    if s in ("postgres", "postgresql", "psql"):
        return "postgresql"
    if s in ("mysql", "mariadb"):
        return "mysql"
    return s


def default_port_for(db: Optional[str]) -> int:
    nd = normalize_db_type(db)
    return 5432 if nd == "postgresql" else 3306


def parse_connection_string(connection_string: str) -> dict:
    """
    Parse a database connection string and extract connection parameters.

    Supports PostgreSQL and MySQL connection string formats:
    - postgresql://user:password@host:port/database?param=value
    - mysql://user:password@host:port/database?param=value

    Args:
        connection_string: Database connection URL

    Returns:
        Dictionary with parsed connection parameters:
        {
            'engine': 'postgresql' or 'mysql',
            'host': hostname,
            'port': port number (int),
            'user': username,
            'password': password (if present),
            'database': database name,
            'ssl_params': dict of SSL-related query parameters
        }

    Raises:
        ValueError: If connection string format is invalid or unsupported
    """
    if not connection_string:
        raise ValueError("Connection string cannot be empty")

    try:
        parsed = urlsplit(connection_string)
    except Exception as e:
        raise ValueError(f"Invalid connection string format: {e}")

    # Validate and extract scheme (engine)
    scheme = parsed.scheme.lower()
    if scheme not in ("postgresql", "postgres", "mysql"):
        raise ValueError(
            f"Unsupported database engine '{scheme}'. "
            f"Supported: postgresql, postgres, mysql"
        )

    # Normalize engine name
    engine = "postgresql" if scheme in ("postgresql", "postgres") else "mysql"

    # Extract host
    if not parsed.hostname:
        raise ValueError("Connection string missing hostname")
    host = parsed.hostname

    # Extract port (use default if not specified)
    port = parsed.port if parsed.port else default_port_for(engine)

    # Extract username
    user = unquote(parsed.username) if parsed.username else None
    if not user:
        raise ValueError("Connection string missing username")

    # Extract password (optional - we'll prompt for env var later)
    password = unquote(parsed.password) if parsed.password else None

    # Extract database name from path
    database = parsed.path.lstrip("/") if parsed.path else None
    if not database:
        raise ValueError("Connection string missing database name")

    # Parse query parameters for SSL settings
    ssl_params = {}
    if parsed.query:
        params = parse_qs(parsed.query)

        # PostgreSQL SSL parameters
        if "sslmode" in params:
            ssl_params["sslmode"] = params["sslmode"][0]
        if "sslrootcert" in params:
            ssl_params["sslrootcert"] = params["sslrootcert"][0]
        if "sslcert" in params:
            ssl_params["sslcert"] = params["sslcert"][0]
        if "sslkey" in params:
            ssl_params["sslkey"] = params["sslkey"][0]

        # MySQL SSL parameters
        if "ssl" in params:
            ssl_params["ssl"] = params["ssl"][0]
        if "ssl-mode" in params:
            ssl_params["ssl-mode"] = params["ssl-mode"][0]
        if "ssl-ca" in params:
            ssl_params["ssl-ca"] = params["ssl-ca"][0]

    # Determine TLS setting from SSL parameters
    tls = False
    if engine == "postgresql":
        sslmode = ssl_params.get("sslmode", "")
        tls = sslmode in ("require", "verify-ca", "verify-full")
    elif engine == "mysql":
        ssl = ssl_params.get("ssl", "")
        ssl_mode = ssl_params.get("ssl-mode", "")
        tls = ssl in ("true", "1") or ssl_mode in (
            "REQUIRED",
            "VERIFY_CA",
            "VERIFY_IDENTITY",
        )

    return {
        "engine": engine,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "ssl_params": ssl_params,
        "tls": tls,
    }


class TargetsConfig:
    """Simple TOML-based targets storage under ~/.rdst/config.toml"""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else Path.home() / ".rdst" / "config.toml"
        self._data: Dict[str, Any] = {
            "targets": {},
            "default": None,
            "init": {"completed": False},
        }

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = toml.load(self.path)
            except Exception:
                self._data = {
                    "targets": {},
                    "default": None,
                    "init": {"completed": False},
                    "llm": {},
                }
        else:
            self._data = {
                "targets": {},
                "default": None,
                "init": {"completed": False},
                "llm": {},
            }

        # Ensure structural defaults
        self._data.setdefault("targets", {})
        self._data.setdefault("default", None)
        self._data.setdefault("init", {"completed": False})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            toml.dump(self._data, f)

    def list_targets(self) -> List[str]:
        return sorted(self._data.get("targets", {}).keys())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return (self._data.get("targets", {}) or {}).get(name)

    def upsert(self, name: str, entry: Dict[str, Any]) -> None:
        self._data.setdefault("targets", {})
        self._data["targets"][name] = entry

    def remove(self, name: str) -> bool:
        t = self._data.get("targets", {})
        if name in t:
            del t[name]
            if self._data.get("default") == name:
                self._data["default"] = None
            return True
        return False

    def set_default(self, name: Optional[str]) -> None:
        self._data["default"] = name

    def get_default(self) -> Optional[str]:
        return self._data.get("default")

    # Init tracking helpers
    def is_init_completed(self) -> bool:
        try:
            return bool((self._data.get("init") or {}).get("completed", False))
        except Exception:
            return False

    def mark_init_completed(self, version: Optional[str] = None) -> None:
        import datetime

        self._data.setdefault("init", {})
        self._data["init"]["completed"] = True
        self._data["init"]["completed_at"] = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if version is not None:
            self._data["init"]["version"] = version

    # LLM configuration methods
    def get_llm_config(self) -> Dict[str, Any]:
        """Get current LLM configuration."""
        return self._data.get("llm", {})

    def set_llm_config(self, config: Dict[str, Any]) -> None:
        """Set LLM configuration."""
        self._data.setdefault("llm", {})
        self._data["llm"].update(config)

    def get_llm_provider(self) -> Optional[str]:
        """Get configured LLM provider."""
        return self._data.get("llm", {}).get("provider")

    def get_llm_base_url(self) -> Optional[str]:
        """Get configured LLM base URL (for lmstudio)."""
        return self._data.get("llm", {}).get("base_url")

    def get_llm_model(self) -> Optional[str]:
        """Get configured LLM model."""
        return self._data.get("llm", {}).get("model")

    def set_llm_provider(
        self, provider: str, base_url: Optional[str] = None, model: Optional[str] = None
    ) -> None:
        """Set LLM provider configuration."""
        self._data.setdefault("llm", {})
        self._data["llm"]["provider"] = provider
        if base_url:
            self._data["llm"]["base_url"] = base_url
        if model:
            self._data["llm"]["model"] = model

    # Trial configuration methods
    def get_trial_config(self) -> Dict[str, Any]:
        """Get trial configuration."""
        return self._data.get("trial", {})

    def set_trial_config(self, trial: Dict[str, Any]) -> None:
        """Set trial configuration."""
        self._data["trial"] = trial

    def is_trial_active(self) -> bool:
        """Check if user has an active trial."""
        trial = self._data.get("trial", {})
        return bool(trial.get("token") and trial.get("status") == "active")


class RdstCLI:
    """Stubs for rdst commands. Each returns RdstResult and shows intended integrations."""

    def __init__(self, client: Optional[CloudAgentClient] = None):
        self.client = client or CloudAgentClient()

    # rdst configure
    def configure(self, config_path: Optional[str] = None, **kwargs) -> RdstResult:
        """Manages database targets and connection profiles using service + renderer pattern."""
        import asyncio
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureInput, ConfigureOptions
        from lib.cli.configure_renderer import ConfigureRenderer

        try:
            # Load agent config if provided
            if config_path:
                cm = self.client.configuration_manager()
                cm.load_from_json_config(config_path)
                self.client.print_panel(
                    "configure", f"Loaded agent config from {config_path}"
                )

            subcmd = (kwargs.get("subcommand") or "menu").lower()
            valid_subcommands = {
                "add",
                "edit",
                "list",
                "remove",
                "default",
                "menu",
                "llm",
                "test",
            }

            if subcmd not in valid_subcommands:
                return RdstResult(False, f"Unknown subcommand: {subcmd}")

            # Load configuration for fallback operations
            cfg = TargetsConfig()
            cfg.load()

            # Handle LLM configuration separately (uses wizard - independent of targets)
            if subcmd == "llm":
                from .configuration_wizard import ConfigurationWizard

                wizard = ConfigurationWizard(console=self.client._console)
                return wizard.configure_llm(cfg, kwargs)

            # Handle menu (interactive wizard) - fallback to wizard
            if subcmd == "menu":
                from .configuration_wizard import ConfigurationWizard

                wizard = ConfigurationWizard(console=self.client._console)
                return wizard.configure_targets(subcmd, cfg, **kwargs)

            # Use service + renderer pattern for other subcommands
            service = ConfigureService()
            renderer = ConfigureRenderer()

            result = None
            error = None

            async def _run():
                nonlocal result, error
                try:
                    if subcmd == "list":
                        async for event in service.list_targets(
                            ConfigureInput(), ConfigureOptions()
                        ):
                            renderer.render(event)
                            if event.type == "target_list":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "test":
                        target_name = kwargs.get("target") or kwargs.get("name")
                        if not target_name:
                            target_name = cfg.get_default()
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {
                                    "message": "No target specified and no default target configured"
                                },
                            )()
                            return
                        async for event in service.test_connection(target_name):
                            renderer.render(event)
                            if (
                                event.type == "connection_test"
                                and event.status == "success"
                            ):
                                result = event
                            elif event.type == "error":
                                error = event
                            elif (
                                event.type == "connection_test"
                                and event.status == "failed"
                            ):
                                error = event

                    elif subcmd == "add":
                        target_name = kwargs.get("name")
                        if not target_name:
                            # Fall back to wizard for interactive add
                            return
                        target_data = {
                            "engine": kwargs.get("engine", "postgresql"),
                            "host": kwargs.get("host", "localhost"),
                            "port": kwargs.get("port", 5432),
                            "database": kwargs.get("database", "postgres"),
                            "user": kwargs.get("user", "postgres"),
                            "password_env": kwargs.get("password_env", ""),
                            "tls": kwargs.get("tls", False),
                        }
                        async for event in service.add_target(
                            ConfigureInput(target_name=target_name),
                            ConfigureOptions(target_data=target_data),
                        ):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "edit":
                        target_name = kwargs.get("name")
                        if not target_name:
                            # Fall back to wizard for interactive edit
                            return
                        target_data = {}
                        for key in [
                            "engine",
                            "host",
                            "port",
                            "database",
                            "user",
                            "password_env",
                            "tls",
                        ]:
                            if kwargs.get(key) is not None:
                                target_data[key] = kwargs.get(key)
                        async for event in service.update_target(
                            target_name,
                            ConfigureInput(target_name=target_name),
                            ConfigureOptions(target_data=target_data),
                        ):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "remove":
                        target_name = kwargs.get("name")
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {"message": "Target name is required for remove"},
                            )()
                            return
                        async for event in service.remove_target(target_name):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "default":
                        target_name = kwargs.get("name")
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {"message": "Target name is required for set-default"},
                            )()
                            return
                        async for event in service.set_default(target_name):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                finally:
                    renderer.cleanup()

            asyncio.run(_run())

            # Handle fallback to wizard for interactive operations
            if result is None and error is None and subcmd in ("add", "edit"):
                from .configuration_wizard import ConfigurationWizard

                wizard = ConfigurationWizard(console=self.client._console)
                return wizard.configure_targets(subcmd, cfg, **kwargs)

            if result:
                return RdstResult(True, "Operation completed successfully")
            elif error:
                return RdstResult(False, getattr(error, "message", str(error)))
            else:
                return RdstResult(True, "Operation completed")

        except Exception as e:
            return RdstResult(False, f"configure failed: {e}")

    def _test_connection(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Test database connection for a target. Returns JSON-formatted result."""
        import json

        target_name = kwargs.get("target") or kwargs.get("name")

        # If no target specified, use default
        if not target_name:
            target_name = cfg.get_default()
            if not target_name:
                result = {
                    "success": False,
                    "error": "No target specified and no default target configured",
                }
                return RdstResult(False, json.dumps(result, indent=2))

        # Get target configuration
        target_config = cfg.get(target_name)
        if not target_config:
            result = {
                "success": False,
                "error": f"Target '{target_name}' not found in configuration",
            }
            return RdstResult(False, json.dumps(result, indent=2))

        # Extract connection parameters
        engine = target_config.get("engine", "").lower()
        host = target_config.get("host", "localhost")
        port = target_config.get("port")
        user = target_config.get("user", "postgres")
        database = target_config.get("database", "postgres")
        password_env = target_config.get("password_env", "")

        # Get password from environment
        password = os.environ.get(password_env, "") if password_env else ""
        if password_env and not password:
            result = {
                "success": False,
                "target": target_name,
                "error": f"Password environment variable '{password_env}' is not set",
            }
            return RdstResult(False, json.dumps(result, indent=2))

        # Test connection based on engine
        try:
            if engine == "postgresql":
                import psycopg2

                conn = psycopg2.connect(
                    host=host,
                    port=port or 5432,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()

                result = {
                    "success": True,
                    "target": target_name,
                    "engine": engine,
                    "host": host,
                    "port": port or 5432,
                    "database": database,
                    "server_version": version,
                }
                return RdstResult(True, json.dumps(result, indent=2))

            elif engine == "mysql":
                import pymysql

                conn = pymysql.connect(
                    host=host,
                    port=port or 3306,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()

                result = {
                    "success": True,
                    "target": target_name,
                    "engine": engine,
                    "host": host,
                    "port": port or 3306,
                    "database": database,
                    "server_version": version,
                }
                return RdstResult(True, json.dumps(result, indent=2))

            else:
                result = {"success": False, "error": f"Unsupported engine: {engine}"}
                return RdstResult(False, json.dumps(result, indent=2))

        except Exception as e:
            error_msg = str(e)
            # Provide helpful hints for common errors
            hints = []
            if (
                "authentication failed" in error_msg.lower()
                or "access denied" in error_msg.lower()
            ):
                hints.append("Check that your password is correct")
                hints.append(
                    f"Verify the password environment variable '{password_env}' is set correctly"
                )
            elif (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                hints.append(
                    f"Check that the database server is running on {host}:{port or (5432 if engine == 'postgresql' else 3306)}"
                )
                hints.append("Verify the host and port are correct")
            elif "does not exist" in error_msg.lower():
                hints.append(f"Check that the database '{database}' exists")

            result = {
                "success": False,
                "target": target_name,
                "engine": engine,
                "host": host,
                "port": port or (5432 if engine == "postgresql" else 3306),
                "error": error_msg,
                "hints": hints if hints else None,
            }
            return RdstResult(False, json.dumps(result, indent=2))

    # rdst top
    def top(
        self,
        target: str = None,
        source: str = "auto",
        limit: int = 10,
        sort: str = "total_time",
        filter: str = None,
        json: bool = False,
        watch: bool = False,
        no_color: bool = False,
        **kwargs,
    ) -> RdstResult:
        """Live view of top slow queries from database telemetry."""
        from .top import TopCommand
        import time

        start_time = time.time()
        target_engine = "unknown"
        queries_found = 0

        try:
            # Get target engine for telemetry
            if target:
                try:
                    cfg = TargetsConfig()
                    cfg.load()
                    target_config = cfg.get(target)
                    if target_config:
                        target_engine = target_config.get("engine", "unknown")
                except Exception:
                    pass

            top_command = TopCommand(client=self.client)
            result = top_command.execute(
                target, source, limit, sort, filter, json, watch, no_color, **kwargs
            )

            # Extract queries found from result
            if result.data:
                queries_found = result.data.get(
                    "queries_found", result.data.get("total_queries_tracked", 0)
                )

            # Track telemetry
            duration_seconds = int(time.time() - start_time)
            mode = "interactive" if kwargs.get("interactive") else "snapshot"

            try:
                from lib.telemetry import telemetry

                telemetry.track_top(
                    mode=mode,
                    duration_seconds=duration_seconds,
                    queries_found=queries_found,
                    target_engine=target_engine,
                )
            except Exception:
                pass

            return result

        except Exception as e:
            # Track crash
            try:
                from lib.telemetry import telemetry

                telemetry.report_crash(e, context={"command": "top", "target": target})
            except Exception:
                pass
            return RdstResult(False, f"top failed: {e}")

    # rdst analyze
    def analyze(
        self,
        hash: Optional[str] = None,
        query: Optional[str] = None,
        file: Optional[str] = None,
        stdin: bool = False,
        name: Optional[str] = None,
        positional_query: Optional[str] = None,
        target: Optional[str] = None,
        save_as: Optional[str] = None,
        readyset_cache: bool = False,
        fast: bool = False,
        interactive: bool = False,
        review: bool = False,
        large_query_bypass: Optional[str] = None,
        output_json: bool = False,
        skip_warning: bool = False,
        **kwargs,
    ) -> RdstResult:
        """
        Analyze SQL query with support for multiple input modes.

        Supports input from:
        - Registry by hash (--hash)
        - Registry by name (--name)
        - Inline query (-q/--query)
        - File (-f/--file)
        - Stdin (--stdin)
        - Interactive prompt (fallback)
        - Positional argument (backward compatibility)

        Args:
            hash: Query hash from registry
            query: SQL query string from -q flag
            file: Path to SQL file from -f flag
            stdin: Whether to read from stdin
            name: Query name for registry lookup
            positional_query: Positional query argument
            target: Target database
            save_as: Name to save query as after analysis
            readyset_cache: Whether to test Readyset caching with Docker container
            fast: Whether to skip EXPLAIN ANALYZE and use EXPLAIN only
            interactive: Whether to enter interactive mode after analysis
            review: Whether to review conversation history instead of analyzing
            **kwargs: Additional arguments

        Returns:
            RdstResult with analysis results
        """
        from .analyze_command import AnalyzeCommand, AnalyzeInputError
        import time

        # Track timing for telemetry
        start_time = time.time()
        query_hash = None
        target_engine = "unknown"
        error_type = None
        resolved_input = None

        try:
            analyze_cmd = AnalyzeCommand(client=self.client)

            # Resolve input using precedence rules
            resolved_input = analyze_cmd.resolve_input(
                hash=hash,
                inline_query=query,
                file_path=file,
                use_stdin=stdin,
                name=name,
                positional_query=positional_query,
                save_as=save_as,
                large_query_bypass=large_query_bypass,
            )

            # Use explicit --target, then registry target (from --hash/--name
            # lookup), then config default
            target_db = target
            cfg = TargetsConfig()
            cfg.load()
            if not target_db and resolved_input.registry_target:
                target_db = resolved_input.registry_target
            if not target_db:
                target_db = cfg.get_default()

            # Get target engine for telemetry
            if target_db:
                try:
                    target_config = cfg.get(target_db)
                    if target_config:
                        target_engine = target_config.get("engine", "unknown")
                except Exception:
                    pass

            # Execute analysis
            result = analyze_cmd.execute_analyze(
                resolved_input,
                target=target_db,
                readyset=readyset_cache,
                readyset_cache=readyset_cache,
                fast=fast,
                interactive=interactive,
                review=review,
                output_json=output_json,
                skip_warning=skip_warning,
            )

            # Extract query hash from result for telemetry
            if result.data:
                query_hash = result.data.get("query_hash") or result.data.get("hash")

            # Track telemetry
            duration_ms = int((time.time() - start_time) * 1000)
            mode = (
                "interactive"
                if interactive
                else (
                    "fast"
                    if fast
                    else ("readyset_cache" if readyset_cache else "standard")
                )
            )

            try:
                from lib.telemetry import telemetry

                telemetry.track_analyze(
                    query_hash=query_hash or "unknown",
                    mode=mode,
                    duration_ms=duration_ms,
                    success=result.ok,
                    target_engine=target_engine,
                )

                # First successful analyze — ask for micro-feedback
                if result.ok and telemetry.is_first_successful_analyze():
                    try:
                        telemetry.show_first_analyze_feedback()
                    except Exception:
                        pass
            except Exception:
                pass  # Don't fail analyze if telemetry fails

            return result

        except AnalyzeInputError as e:
            error_type = "input_error"
            # Track failed analysis
            try:
                from lib.telemetry import telemetry

                duration_ms = int((time.time() - start_time) * 1000)
                telemetry.track_analyze(
                    query_hash="unknown",
                    mode="standard",
                    duration_ms=duration_ms,
                    success=False,
                    error_type=error_type,
                    target_engine=target_engine,
                )
            except Exception:
                pass
            return RdstResult(False, str(e))
        except Exception as e:
            error_type = type(e).__name__
            # Track crash and report to Sentry
            try:
                from lib.telemetry import telemetry

                duration_ms = int((time.time() - start_time) * 1000)
                telemetry.track_analyze(
                    query_hash=query_hash or "unknown",
                    mode="standard",
                    duration_ms=duration_ms,
                    success=False,
                    error_type=error_type,
                    target_engine=target_engine,
                )
                telemetry.report_crash(
                    e, context={"command": "analyze", "target": target_db}
                )
            except Exception:
                pass
            return RdstResult(False, f"analyze failed: {e}")

    # rdst init
    def init(self, **kwargs) -> RdstResult:
        """First-time guided setup (init)."""
        try:
            # Determine interactivity and force flags from kwargs
            force = bool(kwargs.get("force", False))
            interactive = kwargs.get("interactive", None)
            # Run the init command
            from .init_command import InitCommand

            wizard = InitCommand(console=self.client._console, cli=self)
            return wizard.run(force=force, interactive=interactive)
        except Exception as e:
            return RdstResult(False, f"init failed: {e}")

    # rdst query - query registry management
    def query(self, subcommand: str, **kwargs) -> RdstResult:
        """
        Manage query registry: add, edit, list, show, delete queries.

        This is separate from analysis - purely for query management.

        Args:
            subcommand: One of: add, edit, list, show, delete, rm
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        import asyncio

        try:
            from lib.services.query_service import QueryService
            from lib.services.types import (
                QueryCommandInput,
                QueryCompleteEvent,
                QueryErrorEvent,
            )
            from .query_renderer import QueryRenderer

            service = QueryService()
            renderer = QueryRenderer()
            complete_event = None
            error_event = None

            async def _run() -> None:
                nonlocal complete_event, error_event
                try:
                    async for event in service.execute(
                        QueryCommandInput(subcommand=subcommand, kwargs=kwargs)
                    ):
                        renderer.render(event)
                        if isinstance(event, QueryCompleteEvent):
                            complete_event = event
                        elif isinstance(event, QueryErrorEvent):
                            error_event = event
                finally:
                    renderer.cleanup()

            asyncio.run(_run())

            if complete_event:
                payload = complete_event.result
                return RdstResult(
                    bool(payload.get("ok", complete_event.success)),
                    payload.get("message", ""),
                    payload.get("data") or {},
                )
            if error_event:
                return RdstResult(False, error_event.message)
            return RdstResult(False, "query command returned no result")
        except Exception as e:
            return RdstResult(False, f"query command failed: {e}")

    # rdst help / rdst version
    def help(self) -> RdstResult:
        """Display a friendly welcome/help page."""
        banner = (
            "\n"
            "==============================================\n"
            "  Readyset Data and SQL Toolkit (rdst)\n"
            "==============================================\n"
        )
        intro = (
            "Troubleshoot latency, analyze queries, and get tuning insights.\n"
            "\n"
            "Common commands:\n"
            "  - rdst configure        Manage database targets and profiles\n"
            "  - rdst configure llm    Configure AI analysis provider (independent of targets)\n"
            "  - rdst analyze          Explain a SQL query\n"
            "  - rdst cache            Evaluate Readyset caching benefits\n"
            "  - rdst top              Live view of top slow queries\n"
            "  - rdst init             First-time setup wizard\n"
            "  - rdst query list             Show saved queries\n"
            "  - rdst query            Manage query registry\n"
            "  - rdst version          Show version information\n"
            "  - rdst report           Submit feedback or bug reports\n"
            "\n"
            "Examples:\n"
            "  rdst configure add --target prod --host db.example.com --user admin\n"
            "  rdst configure llm\n"
            '  rdst analyze "SELECT * FROM users WHERE active = true"\n'
        )
        return RdstResult(True, f"{banner}{intro}")

    def version(self) -> RdstResult:
        """Report CLI/library version."""
        try:
            from importlib.metadata import version as get_version

            pkg_version = get_version("rdst")
        except Exception:
            # Fallback to _version.py if package metadata not available
            try:
                from _version import __version__

                pkg_version = __version__
            except Exception:
                pkg_version = "unknown"

        return RdstResult(
            True, f"Readyset Data and SQL Toolkit (rdst) version {pkg_version}"
        )

    # rdst report
    def report(self, title: str, body: str = "", **kwargs) -> RdstResult:
        """Stub: Submit feedback or bug reports from within the CLI."""
        if not title:
            return RdstResult(False, "report requires a title")
        msg = "Report stub – would submit feedback via control plane API."
        return RdstResult(True, msg, data={"title": title, "body": body})

    # ============================================================================
    # RDST ASK - Text-to-SQL with hybrid linear + agent architecture
    # NOTE: Not yet exposed in CLI - internal API only
    # ============================================================================
    def ask(
        self,
        question: Optional[str] = None,
        target: Optional[str] = None,
        dry_run: bool = False,
        timeout: int = 30,
        verbose: bool = False,
        agent_mode: bool = False,
        no_interactive: bool = False,
        **kwargs,
    ) -> RdstResult:
        """
        Generate SQL from natural language using hybrid linear + agent architecture.

        Uses a fast linear flow (schema → filter → clarify → generate → validate → execute)
        for most queries, with automatic escalation to an intelligent agent for complex cases.

        The agent can:
        - Explore the schema iteratively
        - Sample data to understand semantics
        - Ask the user clarifying questions
        - Refine its approach based on observations

        Args:
            question: Natural language question (if None, prompt user interactively)
            target: Target database name (if None, use default)
            dry_run: Generate SQL but don't execute (default: False)
            timeout: Query timeout in seconds (default: 30)
            verbose: Show detailed information
            agent_mode: Skip linear flow and go directly to agent exploration
            no_interactive: Skip clarification prompts, use first interpretation
            **kwargs: Additional parameters

        Returns:
            RdstResult with generated SQL, execution results, and metadata

        Examples:
            # Basic usage
            rdst ask "Show me the top 10 customers by revenue"

            # Dry run (generate but don't execute)
            rdst ask "Count active users" --dry-run

            # Direct agent mode for complex queries
            rdst ask "Find users who give the most downvotes" --agent

            # Verbose output
            rdst ask "Show slow queries" --verbose
        """
        import asyncio
        from ..engines.ask3.renderer import AskRenderer
        from ..engines.ask3.input_handler import (
            AskInputHandler,
            NonInteractiveInputHandler,
        )
        from ..services.ask_service import AskService
        from ..services.types import (
            AskInput,
            AskOptions,
            AskClarificationNeededEvent,
            AskResultEvent,
            AskErrorEvent,
        )

        # Interactive prompt if no question provided
        if not question:
            import sys

            if not sys.stdin.isatty():
                return RdstResult(
                    False,
                    'ask requires a question. Example: rdst ask "How many users are there?"',
                )
            try:
                question = input("Question: ").strip()
            except (EOFError, KeyboardInterrupt):
                return RdstResult(False, "Cancelled")
            if not question:
                return RdstResult(False, "ask requires a question")

        try:
            # Validate question is provided
            if not question:
                return RdstResult(
                    False,
                    'Question required. Usage: rdst ask "your question here" --target <target>',
                )

            # Create renderer and input handler
            renderer = AskRenderer(verbose=verbose)
            input_handler = (
                NonInteractiveInputHandler() if no_interactive else AskInputHandler()
            )

            # Create service (no callbacks - fully event-driven)
            service = AskService()

            # Build input
            input_data = AskInput(
                question=question,
                target=target,
                source="cli",
            )
            options_data = AskOptions(
                dry_run=dry_run,
                timeout_seconds=timeout,
                verbose=verbose,
                agent_mode=agent_mode,
                no_interactive=no_interactive,
            )

            # Run async service with sync bridge, handling events
            result_event = None
            error_event = None

            async def _run_ask():
                nonlocal result_event, error_event

                async for event in service.ask(input_data, options_data):
                    # Render the event
                    renderer.render(event)

                    # Handle clarification - collect input and resume
                    if isinstance(event, AskClarificationNeededEvent):
                        try:
                            answers = input_handler.collect_clarifications(event)
                            # Resume with answers
                            async for resume_event in service.resume(
                                event.session_id, answers
                            ):
                                renderer.render(resume_event)
                                if isinstance(resume_event, AskResultEvent):
                                    result_event = resume_event
                                elif isinstance(resume_event, AskErrorEvent):
                                    error_event = resume_event
                        except (EOFError, KeyboardInterrupt):
                            error_event = AskErrorEvent(
                                type="error",
                                message="Cancelled by user",
                                phase="clarify",
                            )
                            renderer.render(error_event)
                            return

                    elif isinstance(event, AskResultEvent):
                        result_event = event

                    elif isinstance(event, AskErrorEvent):
                        error_event = event

            asyncio.run(_run_ask())

            # Build result from final events
            if result_event:
                message = f"\nSQL: {result_event.sql}\n"
                message += f"Rows: {result_event.row_count}\n"
                message += f"Execution time: {result_event.execution_time_ms:.1f}ms\n"
                message += f"LLM calls: {result_event.llm_calls}\n"
                message += f"Total tokens: {result_event.total_tokens}\n"

                return RdstResult(
                    ok=True,
                    message=message,
                    data={
                        "sql": result_event.sql,
                        "rows": result_event.rows,
                        "columns": result_event.columns,
                        "row_count": result_event.row_count,
                        "execution_time_ms": result_event.execution_time_ms,
                        "llm_calls": result_event.llm_calls,
                        "total_tokens": result_event.total_tokens,
                        "status": "success",
                    },
                )

            elif error_event:
                if "cancelled" in error_event.message.lower():
                    return RdstResult(ok=False, message="Operation cancelled by user")
                return RdstResult(
                    ok=False,
                    message=error_event.message,
                    data={"phase": error_event.phase} if error_event.phase else {},
                )

            else:
                return RdstResult(False, "Ask command failed unexpectedly")

        except Exception as e:
            import traceback

            traceback.print_exc()
            return RdstResult(False, f"ask command failed: {e}")

    # ============================================================================
    # RDST SCHEMA - Semantic layer management
    # NOTE: Not yet exposed in CLI - internal API only
    # ============================================================================
    def schema(
        self, subcommand: str = None, target: str = None, **kwargs
    ) -> RdstResult:
        """
        Manage semantic layer for better SQL generation.

        Args:
            subcommand: One of: show, init, edit, annotate, export, delete, list, add-table, add-term
            target: Target database name
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        import asyncio

        try:
            from .schema_command import SchemaCommand
            from .schema_renderer import SchemaRenderer
            from lib.services.schema_service import SchemaService
            from lib.services.types import (
                SchemaCompleteEvent,
                SchemaErrorEvent,
                SchemaInitOptions,
            )

            schema_cmd = SchemaCommand()
            service = SchemaService()
            renderer = SchemaRenderer()

            # Interactive menu if no subcommand provided
            if not subcommand:
                import sys

                if not sys.stdin.isatty():
                    return RdstResult(
                        False,
                        "Schema command requires a subcommand: show, init, edit, annotate, export, delete, list\nTry: rdst schema --help",
                    )
                from lib.ui import SelectPrompt

                options = [
                    "show - Display semantic layer",
                    "init - Initialize from database",
                    "annotate - Add descriptions",
                    "edit - Edit in $EDITOR",
                ]
                try:
                    choice = SelectPrompt.ask(
                        "Schema subcommands:", options, default=1, allow_cancel=True
                    )
                except (EOFError, KeyboardInterrupt):
                    return RdstResult(False, "Cancelled")
                if choice is None:
                    return RdstResult(False, "Cancelled")
                subcommand = ["show", "init", "annotate", "edit"][choice - 1]

            # Keep interactive/editor-only flows on legacy command implementation.
            if subcommand in ("edit", "annotate"):
                if not target:
                    target = self._get_default_target()
                    if not target:
                        return RdstResult(
                            False,
                            "No target specified and no default target configured.",
                        )

                if subcommand == "edit":
                    result = schema_cmd.edit(target, kwargs.get("table"))
                else:
                    table = kwargs.get("table")
                    use_llm = kwargs.get("use_llm", False)
                    auto_accept = kwargs.get("auto_accept", False)
                    sample_rows = kwargs.get("sample_rows", 5)
                    target_config = self._get_target_config(target)
                    if use_llm and not target_config:
                        return RdstResult(
                            False,
                            f"Target '{target}' not found. Run 'rdst configure' first.",
                        )
                    if auto_accept and not use_llm:
                        return RdstResult(
                            False,
                            "--auto-accept requires --use-llm.",
                        )
                    result = schema_cmd.annotate(
                        target, table, use_llm=use_llm,
                        auto_accept=auto_accept,
                        sample_rows=sample_rows, target_config=target_config,
                    )
                return RdstResult(bool(result.get("ok")), result.get("message", ""))

            if subcommand != "list" and not target:
                target = self._get_default_target()
                if not target:
                    return RdstResult(
                        False,
                        "No target specified and no default target configured. Use --target or run 'rdst configure'",
                    )

            complete_event = None
            error_event = None

            async def _consume(generator):
                nonlocal complete_event, error_event
                async for event in generator:
                    renderer.render(event)
                    if isinstance(event, SchemaCompleteEvent):
                        complete_event = event
                    elif isinstance(event, SchemaErrorEvent):
                        error_event = event

            if subcommand == "show":
                asyncio.run(
                    _consume(service.get_schema_events(target, kwargs.get("table")))
                )
            elif subcommand == "init":
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure' first.",
                    )
                if kwargs.get("interactive", False):
                    # keep interactive enum flow on legacy command
                    result = schema_cmd.init(
                        target,
                        target_config,
                        kwargs.get("enum_threshold", 20),
                        kwargs.get("force", False),
                        True,
                    )
                    return RdstResult(bool(result.get("ok")), result.get("message", ""))

                options = SchemaInitOptions(
                    enum_threshold=kwargs.get("enum_threshold", 20),
                    force=kwargs.get("force", False),
                    sample_enums=True,
                )
                asyncio.run(
                    _consume(service.init_events(target, target_config, options))
                )
            elif subcommand == "export":
                asyncio.run(
                    _consume(
                        service.export_events(
                            target, kwargs.get("output_format", "yaml")
                        )
                    )
                )
            elif subcommand == "delete":
                force = kwargs.get("force", False)
                if not force:
                    try:
                        confirm = input(f"Delete semantic layer for '{target}'? [y/N] ")
                        if confirm.lower() != "y":
                            return RdstResult(False, "Cancelled")
                    except EOFError:
                        return RdstResult(
                            False,
                            "Cannot prompt for confirmation in non-interactive mode. Use --force",
                        )
                asyncio.run(_consume(service.delete_events(target)))
            elif subcommand == "list":
                asyncio.run(_consume(service.list_targets_events()))
            elif subcommand == "refresh":
                # Keep refresh on the legacy command for now (not implemented as a service event stream).
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(
                        False,
                        f"Target '{target}' not found. Run 'rdst configure' first.",
                    )

                result = schema_cmd.refresh(target, target_config)
                return RdstResult(bool(result.get("ok")), result.get("message", ""))
            elif subcommand == "add-table":
                result = service.add_table(
                    target,
                    kwargs.get("table"),
                    kwargs.get("description", ""),
                    kwargs.get("context", ""),
                )
                return RdstResult(
                    bool(result.success), result.message or result.error or ""
                )
            elif subcommand == "add-term":
                result = service.add_terminology(
                    target,
                    kwargs.get("term"),
                    kwargs.get("definition", ""),
                    kwargs.get("sql_pattern", ""),
                )
                return RdstResult(
                    bool(result.success), result.message or result.error or ""
                )
            else:
                return RdstResult(False, f"Unknown schema subcommand: {subcommand}")

            if error_event:
                return RdstResult(False, error_event.message)
            if not complete_event:
                return RdstResult(False, "schema command returned no result")

            if subcommand == "export" and complete_event.export_result:
                return RdstResult(True, complete_event.export_result.content)
            if subcommand == "list" and complete_event.target_list:
                msg = (
                    f"Found {len(complete_event.target_list.targets)} semantic layer(s)"
                )
                return RdstResult(True, msg)
            if subcommand == "show":
                return RdstResult(True, "")
            if subcommand == "init" and complete_event.init_result:
                return RdstResult(
                    bool(complete_event.init_result.success),
                    ""
                    if complete_event.init_result.success
                    else (complete_event.init_result.error or ""),
                )
            if subcommand == "delete" and complete_event.delete_result:
                return RdstResult(bool(complete_event.delete_result.success), "")
            return RdstResult(True, "")
        except Exception as e:
            return RdstResult(False, f"schema command failed: {e}")

    def _format_schema_show(self, data: dict) -> str:
        """Format schema show output for display."""
        console = get_console()
        renderables: list[Any] = []

        summary = data.get("summary", {})
        renderables.append(
            KeyValueTable(
                {
                    "Tables": summary.get("tables", 0),
                    "Columns": summary.get("columns", 0),
                    "Terminology": summary.get("terminology", 0),
                },
                title="Summary",
            )
        )

        tables = data.get("tables", {})
        if tables:
            tree = SimpleTree("Tables")
            for name, table in tables.items():
                desc = table.get("description", "No description")
                table_node = tree.add(f"{name}: {desc}")
                if table.get("columns"):
                    for col_name, col in table["columns"].items():
                        col_desc = col.get("description", "")
                        col_type = col.get("type", "")
                        if col.get("enum_values"):
                            enum_preview = list(col["enum_values"].keys())[:3]
                            col_type = f"enum({', '.join(enum_preview)}...)"
                        table_node.add(f"{col_name} ({col_type}): {col_desc}")
            renderables.append(tree)

        terminology = data.get("terminology", {})
        if terminology:
            term_tree = SimpleTree("Terminology")
            for term, info in terminology.items():
                term_tree.add(f"{term}: {info.get('definition', '')}")
            renderables.append(term_tree)

        with console.capture() as capture:
            console.print(Group(*renderables))
        return capture.get().rstrip()

    def _get_default_target(self) -> str:
        """Get the default target from config."""
        try:
            cfg = TargetsConfig()
            cfg.load()
            return cfg.get_default() or ""
        except Exception:
            return ""

    def _get_target_config(self, target: str) -> dict:
        """Get target configuration by name."""
        try:
            cfg = TargetsConfig()
            cfg.load()
            return cfg.get(target) or {}
        except Exception:
            return {}


# Ready-to-use singleton for simple imports: from lib.cli import rdst
rdst = RdstCLI()
