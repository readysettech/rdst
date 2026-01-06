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

try:
    # Rich is optional; if unavailable, we still work without pretty output
    from rich.console import Console
    from rich.panel import Panel
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover - we keep imports soft
    _RICH_AVAILABLE = False
    Console = None  # type: ignore
    Panel = None  # type: ignore

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
        self._console = Console() if _RICH_AVAILABLE else None

    # Example accessors (add more as needed)
    def configuration_manager(self):  # -> ConfigurationManager
        from configuration_manager import ConfigurationManager  # local import
        return ConfigurationManager()

    def data_manager_service(self):  # -> DataManagerService
        from lib.data_manager_service.data_manager_service import DataManagerService  # local import
        return DataManagerService

    def cache_manager(self):  # -> CacheManager
        # Note: CacheManager currently requires initialization context; defer wiring
        from lib.cache_manager.cache_manager import CacheManager  # local import
        return CacheManager

    def llm_manager(self):  # -> LLMManager
        from lib.llm_manager.llm_manager import LLMManager  # local import
        return LLMManager()

    def print_panel(self, title: str, message: str):
        if self._console and _RICH_AVAILABLE:
            self._console.print(Panel.fit(message, title=title))


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
    if scheme not in ('postgresql', 'postgres', 'mysql'):
        raise ValueError(
            f"Unsupported database engine '{scheme}'. "
            f"Supported: postgresql, postgres, mysql"
        )

    # Normalize engine name
    engine = 'postgresql' if scheme in ('postgresql', 'postgres') else 'mysql'

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
    database = parsed.path.lstrip('/') if parsed.path else None
    if not database:
        raise ValueError("Connection string missing database name")

    # Parse query parameters for SSL settings
    ssl_params = {}
    if parsed.query:
        params = parse_qs(parsed.query)

        # PostgreSQL SSL parameters
        if 'sslmode' in params:
            ssl_params['sslmode'] = params['sslmode'][0]
        if 'sslrootcert' in params:
            ssl_params['sslrootcert'] = params['sslrootcert'][0]
        if 'sslcert' in params:
            ssl_params['sslcert'] = params['sslcert'][0]
        if 'sslkey' in params:
            ssl_params['sslkey'] = params['sslkey'][0]

        # MySQL SSL parameters
        if 'ssl' in params:
            ssl_params['ssl'] = params['ssl'][0]
        if 'ssl-mode' in params:
            ssl_params['ssl-mode'] = params['ssl-mode'][0]
        if 'ssl-ca' in params:
            ssl_params['ssl-ca'] = params['ssl-ca'][0]

    # Determine TLS setting from SSL parameters
    tls = False
    if engine == 'postgresql':
        sslmode = ssl_params.get('sslmode', '')
        tls = sslmode in ('require', 'verify-ca', 'verify-full')
    elif engine == 'mysql':
        ssl = ssl_params.get('ssl', '')
        ssl_mode = ssl_params.get('ssl-mode', '')
        tls = ssl in ('true', '1') or ssl_mode in ('REQUIRED', 'VERIFY_CA', 'VERIFY_IDENTITY')

    return {
        'engine': engine,
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'database': database,
        'ssl_params': ssl_params,
        'tls': tls
    }


class TargetsConfig:
    """Simple TOML-based targets storage under ~/.rdst/config.toml"""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else Path.home() / ".rdst" / "config.toml"
        self._data: Dict[str, Any] = {"targets": {}, "default": None, "init": {"completed": False}}

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = toml.load(self.path)
            except Exception:
                self._data = {"targets": {}, "default": None, "init": {"completed": False}, "llm": {}}
        else:
            self._data = {"targets": {}, "default": None, "init": {"completed": False}, "llm": {}}

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
        self._data["init"]["completed_at"] = datetime.datetime.utcnow().isoformat() + "Z"
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

    def set_llm_provider(self, provider: str, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        """Set LLM provider configuration."""
        self._data.setdefault("llm", {})
        self._data["llm"]["provider"] = provider
        if base_url:
            self._data["llm"]["base_url"] = base_url
        if model:
            self._data["llm"]["model"] = model


class RdstCLI:
    """Stubs for rdst commands. Each returns RdstResult and shows intended integrations."""

    def __init__(self, client: Optional[CloudAgentClient] = None):
        self.client = client or CloudAgentClient()

    # rdst configure
    def configure(self, config_path: Optional[str] = None, **kwargs) -> RdstResult:
        """Manages database targets and connection profiles using modern wizard."""
        try:
            # Load agent config if provided
            if config_path:
                cm = self.client.configuration_manager()
                cm.load_from_json_config(config_path)
                self.client.print_panel("configure", f"Loaded agent config from {config_path}")

            subcmd = (kwargs.get("subcommand") or "menu").lower()
            valid_subcommands = {"add", "edit", "list", "remove", "default", "menu", "llm", "test"}

            if subcmd not in valid_subcommands:
                return RdstResult(False, f"Unknown subcommand: {subcmd}")

            # Load configuration
            cfg = TargetsConfig()
            cfg.load()

            # Handle test subcommand directly (standalone connection test)
            if subcmd == "test":
                return self._test_connection(cfg, kwargs)

            # Use the modern configuration wizard
            from .configuration_wizard import ConfigurationWizard
            wizard = ConfigurationWizard(console=self.client._console if _RICH_AVAILABLE else None)

            # Handle LLM configuration separately (independent of targets)
            if subcmd == "llm":
                return wizard.configure_llm(cfg, kwargs)

            return wizard.configure_targets(subcmd, cfg, **kwargs)

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
                result = {"success": False, "error": "No target specified and no default target configured"}
                return RdstResult(False, json.dumps(result, indent=2))

        # Get target configuration
        target_config = cfg.get(target_name)
        if not target_config:
            result = {"success": False, "error": f"Target '{target_name}' not found in configuration"}
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
                "error": f"Password environment variable '{password_env}' is not set"
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
                    connect_timeout=10
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
                    "server_version": version
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
                    connect_timeout=10
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
                    "server_version": version
                }
                return RdstResult(True, json.dumps(result, indent=2))

            else:
                result = {"success": False, "error": f"Unsupported engine: {engine}"}
                return RdstResult(False, json.dumps(result, indent=2))

        except Exception as e:
            error_msg = str(e)
            # Provide helpful hints for common errors
            hints = []
            if "authentication failed" in error_msg.lower() or "access denied" in error_msg.lower():
                hints.append("Check that your password is correct")
                hints.append(f"Verify the password environment variable '{password_env}' is set correctly")
            elif "could not connect" in error_msg.lower() or "connection refused" in error_msg.lower():
                hints.append(f"Check that the database server is running on {host}:{port or (5432 if engine == 'postgresql' else 3306)}")
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
                "hints": hints if hints else None
            }
            return RdstResult(False, json.dumps(result, indent=2))

    # rdst top
    def top(self, target: str = None, source: str = "auto", limit: int = 10,
            sort: str = "total_time", filter: str = None, json: bool = False,
            watch: bool = False, no_color: bool = False, **kwargs) -> RdstResult:
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
            result = top_command.execute(target, source, limit, sort, filter, json, watch, no_color, **kwargs)

            # Extract queries found from result
            if result.data:
                queries_found = result.data.get("queries_found", result.data.get("total_queries_tracked", 0))

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
    def analyze(self, hash: Optional[str] = None, query: Optional[str] = None,
                file: Optional[str] = None, stdin: bool = False, name: Optional[str] = None,
                positional_query: Optional[str] = None, target: Optional[str] = None,
                save_as: Optional[str] = None, db: Optional[str] = None,
                readyset_cache: bool = False, fast: bool = False, interactive: bool = False, review: bool = False,
                large_query_bypass: Optional[str] = None, **kwargs) -> RdstResult:
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
            db: Legacy parameter for target database
            readyset_cache: Whether to test ReadySet caching with Docker container
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
                large_query_bypass=large_query_bypass
            )

            # Use target parameter, fallback to db for backward compatibility, then to default
            target_db = target or db
            cfg = TargetsConfig()
            cfg.load()
            if not target_db:
                # Get default target from configuration if none specified
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
            result = analyze_cmd.execute_analyze(resolved_input, target=target_db, readyset=readyset_cache, readyset_cache=readyset_cache, fast=fast, interactive=interactive, review=review)

            # Extract query hash from result for telemetry
            if result.data:
                query_hash = result.data.get("query_hash") or result.data.get("hash")

            # Track telemetry
            duration_ms = int((time.time() - start_time) * 1000)
            mode = "interactive" if interactive else ("fast" if fast else ("readyset_cache" if readyset_cache else "standard"))

            try:
                from lib.telemetry import telemetry
                telemetry.track_analyze(
                    query_hash=query_hash or "unknown",
                    mode=mode,
                    duration_ms=duration_ms,
                    success=result.ok,
                    target_engine=target_engine,
                )
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
                telemetry.report_crash(e, context={"command": "analyze", "target": target_db})
            except Exception:
                pass
            return RdstResult(False, f"analyze failed: {e}")

    # rdst tune "<query>"
    def tune(self, query: str, **kwargs) -> RdstResult:
        """Stub: Suggest rewrites, indexes, caching.

        Intended integration:
        - Static analysis + DataManagerService schema info
        - Recommend indexes or Readyset caching
        """
        if not query:
            return RdstResult(False, "tune requires a SQL query")
        msg = "Tune stub – would suggest rewrites/indexes/caching."
        return RdstResult(True, msg, data={"query": query})

    # rdst init
    def init(self, **kwargs) -> RdstResult:
        """First-time guided setup (init)."""
        try:
            # Determine interactivity and force flags from kwargs
            force = bool(kwargs.get('force', False))
            interactive = kwargs.get('interactive', None)
            # Run the init command
            from .init_command import InitCommand
            wizard = InitCommand(console=self.client._console if _RICH_AVAILABLE else None, cli=self)
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
        try:
            from .query_command import QueryCommand
            query_cmd = QueryCommand()
            return query_cmd.execute(subcommand, **kwargs)
        except Exception as e:
            return RdstResult(False, f"query command failed: {e}")

    # rdst help / rdst version
    def help(self) -> RdstResult:
        """Display a friendly welcome/help page."""
        banner = (
            "\n"
            "==============================================\n"
            "  Readyset Diagnostics & SQL Tuning (rdst)\n"
            "==============================================\n"
        )
        intro = (
            "Troubleshoot latency, analyze queries, and get tuning insights.\n"
            "\n"
            "Common commands:\n"
            "  - rdst configure        Manage database targets and profiles\n"
            "  - rdst configure llm    Configure AI analysis provider (independent of targets)\n"
            "  - rdst analyze          Explain a SQL query\n"
            "  - rdst tune             Get optimization suggestions\n"
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
            "  rdst analyze \"SELECT * FROM users WHERE active = true\"\n"
            "  rdst tune \"SELECT u.name, p.title FROM users u JOIN posts p ON u.id=p.user_id\"\n"
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

        return RdstResult(True, f"Readyset Diagnostics & SQL Tuning (rdst) version {pkg_version}")

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
        **kwargs
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
        from ..engines.ask3 import Ask3Engine, Ask3Presenter, Status

        # Interactive prompt if no question provided
        if not question:
            import sys
            if not sys.stdin.isatty():
                return RdstResult(False, "ask requires a question. Example: rdst ask \"How many users are there?\"")
            try:
                question = input("Question: ").strip()
            except (EOFError, KeyboardInterrupt):
                return RdstResult(False, "Cancelled")
            if not question:
                return RdstResult(False, "ask requires a question")

        try:
            # Load target configuration
            if not target:
                cfg = TargetsConfig()
                cfg.load()
                target = cfg.get_default()

            if not target:
                return RdstResult(False, "No target specified and no default configured. Run 'rdst configure' first.")

            cfg = TargetsConfig()
            cfg.load()
            target_config = cfg._data.get("targets", {}).get(target)

            if not target_config:
                return RdstResult(False, f"Target '{target}' not found in configuration")

            # Determine database type
            engine_type = target_config.get('engine', 'postgresql').lower()
            if 'mysql' in engine_type:
                db_type = 'mysql'
            else:
                db_type = 'postgresql'

            # Create presenter and engine
            presenter = Ask3Presenter(verbose=verbose, use_rich=_RICH_AVAILABLE)
            engine = Ask3Engine(presenter=presenter)

            # Run the engine
            ctx = engine.run(
                question=question,
                target=target,
                target_config=target_config,
                db_type=db_type,
                timeout_seconds=timeout,
                verbose=verbose,
                no_interactive=kwargs.get('no_interactive', False),
                agent_mode=agent_mode
            )

            # Build result
            if ctx.status == Status.SUCCESS:
                row_count = ctx.execution_result.row_count if ctx.execution_result else 0
                exec_time = ctx.execution_result.execution_time_ms if ctx.execution_result else 0

                message = f"\nSQL: {ctx.sql}\n"
                message += f"Rows: {row_count}\n"
                message += f"Execution time: {exec_time:.1f}ms\n"
                message += f"LLM calls: {len(ctx.llm_calls)}\n"
                message += f"Total tokens: {ctx.total_tokens}\n"

                return RdstResult(
                    ok=True,
                    message=message,
                    data={
                        'sql': ctx.sql,
                        'rows': ctx.execution_result.rows if ctx.execution_result else [],
                        'columns': ctx.execution_result.columns if ctx.execution_result else [],
                        'row_count': row_count,
                        'execution_time_ms': exec_time,
                        'llm_calls': len(ctx.llm_calls),
                        'total_tokens': ctx.total_tokens,
                        'status': ctx.status,
                    }
                )

            elif ctx.status == Status.CANCELLED:
                return RdstResult(ok=False, message="Operation cancelled by user")

            else:
                return RdstResult(
                    ok=False,
                    message=f"Error: {ctx.error_message}",
                    data={'status': ctx.status, 'phase': ctx.phase}
                )

        except Exception as e:
            import traceback
            traceback.print_exc()
            return RdstResult(False, f"ask command failed: {e}")

    # ============================================================================
    # RDST SCHEMA - Semantic layer management
    # NOTE: Not yet exposed in CLI - internal API only
    # ============================================================================
    def schema(self, subcommand: str = None, target: str = None, **kwargs) -> RdstResult:
        """
        Manage semantic layer for better SQL generation.

        Args:
            subcommand: One of: show, init, edit, annotate, export, delete, list, add-table, add-term
            target: Target database name
            **kwargs: Subcommand-specific arguments

        Returns:
            RdstResult with operation outcome
        """
        try:
            from .schema_command import SchemaCommand
            schema_cmd = SchemaCommand()

            # Interactive menu if no subcommand provided
            if not subcommand:
                import sys
                if not sys.stdin.isatty():
                    return RdstResult(False, "Schema command requires a subcommand: show, init, edit, annotate, export, delete, list\nTry: rdst schema --help")
                print("Schema subcommands:")
                print("  [1] show - Display semantic layer")
                print("  [2] init - Initialize from database")
                print("  [3] annotate - Add descriptions")
                print("  [4] edit - Edit in $EDITOR")
                try:
                    choice = input("Select subcommand [1]: ").strip() or "1"
                except (EOFError, KeyboardInterrupt):
                    return RdstResult(False, "Cancelled")
                subcommand = {"1": "show", "2": "init", "3": "annotate", "4": "edit"}.get(choice)
                if not subcommand:
                    return RdstResult(False, "Invalid schema subcommand")

            if subcommand == 'show':
                table = kwargs.get('table')
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target
                result = schema_cmd.show(target, table)

            elif subcommand == 'init':
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target

                # Get target config
                target_config = self._get_target_config(target)
                if not target_config:
                    return RdstResult(False, f"Target '{target}' not found. Run 'rdst configure' first.")

                enum_threshold = kwargs.get('enum_threshold', 20)
                force = kwargs.get('force', False)
                interactive = kwargs.get('interactive', False)
                result = schema_cmd.init(target, target_config, enum_threshold, force, interactive)

            elif subcommand == 'edit':
                table = kwargs.get('table')
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured.")
                    target = default_target
                result = schema_cmd.edit(target, table)

            elif subcommand == 'annotate':
                table = kwargs.get('table')
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured.")
                    target = default_target
                use_llm = kwargs.get('use_llm', False)
                sample_rows = kwargs.get('sample_rows', 5)

                # Always try to get target config (needed for AI suggestions in wizard)
                target_config = self._get_target_config(target)

                # Only error if --use-llm specified but config missing
                if use_llm and not target_config:
                    return RdstResult(False, f"Target '{target}' not found. Run 'rdst configure' first.")

                result = schema_cmd.annotate(target, table, use_llm, sample_rows, target_config)

            elif subcommand == 'export':
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target
                output_format = kwargs.get('output_format', 'yaml')
                result = schema_cmd.export(target, output_format)

            elif subcommand == 'delete':
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target

                force = kwargs.get('force', False)
                if not force:
                    # Prompt for confirmation
                    try:
                        confirm = input(f"Delete semantic layer for '{target}'? [y/N] ")
                        if confirm.lower() != 'y':
                            return RdstResult(False, "Cancelled")
                    except EOFError:
                        return RdstResult(False, "Cannot prompt for confirmation in non-interactive mode. Use --force")

                result = schema_cmd.delete(target)

            elif subcommand == 'list':
                result = schema_cmd.list_targets()

            elif subcommand == 'add-table':
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target
                table = kwargs.get('table')
                description = kwargs.get('description', '')
                context = kwargs.get('context', '')
                result = schema_cmd.add_table(target, table, description, context)

            elif subcommand == 'add-term':
                if not target:
                    default_target = self._get_default_target()
                    if not default_target:
                        return RdstResult(False, "No target specified and no default target configured. Use --target or run 'rdst configure'")
                    target = default_target
                term = kwargs.get('term')
                definition = kwargs.get('definition', '')
                sql_pattern = kwargs.get('sql_pattern', '')
                result = schema_cmd.add_terminology(target, term, definition, sql_pattern)

            else:
                return RdstResult(False, f"Unknown schema subcommand: {subcommand}")

            # Format result for display
            if result['ok']:
                message = result['message']
                if result.get('data'):
                    # Format data for display
                    data = result['data']
                    if subcommand == 'show' and 'tables' in data:
                        message += self._format_schema_show(data)
                    elif subcommand == 'init':
                        message += "\n\nDiscovered:"
                        message += f"\n  Tables: {data.get('tables', 0)}"
                        message += f"\n  Columns: {data.get('columns', 0)}"
                        message += f"\n  Relationships: {data.get('relationships', 0)}"
                        if data.get('enum_columns'):
                            message += f"\n  Potential enums: {len(data['enum_columns'])}"
                        # Print message now, then add breadcrumb with colors
                        print(message)
                        message = ""  # Clear message since we printed it
                        if data.get('next_steps'):
                            # Colors: rdst=white, subcommand=green, values/quoted=blue, descriptions=dim
                            try:
                                from rich.console import Console
                                console = Console()
                                console.print()
                                console.print("[cyan]Next Steps:[/cyan]")
                                target_name = data.get('target', target)
                                console.print(f"  rdst [green]schema annotate[/green] --target [blue]{target_name}[/blue] --use-llm   [dim]AI-generate descriptions[/dim]")
                                console.print(f"  rdst [green]schema edit[/green] --target [blue]{target_name}[/blue]                 [dim]Manual editing in $EDITOR[/dim]")
                                console.print(f"  rdst [green]ask[/green] [blue]\"How many rows in each table?\"[/blue] --target [blue]{target_name}[/blue]   [dim]Try natural language queries[/dim]")
                            except ImportError:
                                print("\nNext steps:\n" + "\n".join(data['next_steps']))
                    elif subcommand == 'list':
                        targets = data.get('targets', [])
                        if targets:
                            message += "\n"
                            for t in targets:
                                message += f"\n  {t['name']}: {t['tables']} tables, {t['terminology']} terms"
                    elif subcommand == 'export':
                        message = result['data'].get('content', '')

                return RdstResult(True, message)
            else:
                return RdstResult(False, result['message'])

        except Exception as e:
            return RdstResult(False, f"schema command failed: {e}")

    def _format_schema_show(self, data: dict) -> str:
        """Format schema show output for display."""
        lines = []

        # Summary
        summary = data.get('summary', {})
        lines.append(f"\n\nSummary: {summary.get('tables', 0)} tables, {summary.get('columns', 0)} columns, {summary.get('terminology', 0)} terms")

        # Tables
        tables = data.get('tables', {})
        if tables:
            lines.append("\n\nTables:")
            for name, table in tables.items():
                desc = table.get('description', 'No description')
                lines.append(f"  {name}: {desc}")
                if table.get('columns'):
                    for col_name, col in table['columns'].items():
                        col_desc = col.get('description', '')
                        col_type = col.get('type', '')
                        if col.get('enum_values'):
                            enum_preview = list(col['enum_values'].keys())[:3]
                            col_type = f"enum({', '.join(enum_preview)}...)"
                        lines.append(f"    - {col_name} ({col_type}): {col_desc}")

        # Terminology
        terminology = data.get('terminology', {})
        if terminology:
            lines.append("\n\nTerminology:")
            for term, info in terminology.items():
                lines.append(f"  {term}: {info.get('definition', '')}")

        return "\n".join(lines)

    def _get_default_target(self) -> str:
        """Get the default target from config."""
        try:
            cfg = TargetsConfig()
            cfg.load()
            return cfg.get_default() or ''
        except Exception:
            return ''

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
