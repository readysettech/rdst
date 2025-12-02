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
import sys
import os
from pathlib import Path
from getpass import getpass
from urllib.parse import urlsplit, urlunsplit, quote
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
            valid_subcommands = {"add", "edit", "list", "remove", "default", "menu", "llm"}

            if subcmd not in valid_subcommands:
                return RdstResult(False, f"Unknown subcommand: {subcmd}")

            # Load configuration
            cfg = TargetsConfig()
            cfg.load()

            # Use the modern configuration wizard
            from .configuration_wizard import ConfigurationWizard
            wizard = ConfigurationWizard(console=self.client._console if _RICH_AVAILABLE else None)

            # Handle LLM configuration separately (independent of targets)
            if subcmd == "llm":
                return wizard.configure_llm(cfg, kwargs)

            return wizard.configure_targets(subcmd, cfg, **kwargs)

        except Exception as e:
            return RdstResult(False, f"configure failed: {e}")

    # rdst top
    def top(self, target: str = None, source: str = "auto", limit: int = 10, 
            sort: str = "total_time", filter: str = None, json: bool = False, 
            watch: bool = False, no_color: bool = False, **kwargs) -> RdstResult:
        """Live view of top slow queries from database telemetry."""
        from .top import TopCommand
        top_command = TopCommand(client=self.client)
        return top_command.execute(target, source, limit, sort, filter, json, watch, no_color, **kwargs)

    # rdst analyze
    def analyze(self, hash: Optional[str] = None, query: Optional[str] = None,
                file: Optional[str] = None, stdin: bool = False, name: Optional[str] = None,
                positional_query: Optional[str] = None, target: Optional[str] = None,
                save_as: Optional[str] = None, db: Optional[str] = None, readyset: bool = False,
                fast: bool = False, interactive: bool = False, review: bool = False, **kwargs) -> RdstResult:
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
            readyset: Whether to use local Readyset Docker container
            fast: Whether to auto-skip slow EXPLAIN ANALYZE queries after 10 seconds
            interactive: Whether to enter interactive mode after analysis
            review: Whether to review conversation history instead of analyzing
            **kwargs: Additional arguments

        Returns:
            RdstResult with analysis results
        """
        from .analyze_command import AnalyzeCommand, AnalyzeInputError

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
                save_as=save_as
            )

            # Use target parameter, fallback to db for backward compatibility, then to default
            target_db = target or db
            if not target_db:
                # Get default target from configuration if none specified
                cfg = TargetsConfig()
                cfg.load()
                target_db = cfg.get_default()

            # Execute analysis
            return analyze_cmd.execute_analyze(resolved_input, target=target_db, readyset=readyset, fast=fast, interactive=interactive, review=review)

        except AnalyzeInputError as e:
            return RdstResult(False, str(e))
        except Exception as e:
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

    # rdst cache "<query>" or rdst cache "<hash_id>"
    def cache(self, query: str, strategy: str = "explicit", target: Optional[str] = None,
              tag: Optional[str] = None, json: bool = False, **kwargs) -> RdstResult:
        """
        Benchmark query performance and evaluate Readyset caching benefits.

        New workflow:
        1. Resolve query (from hash_id or direct SQL)
        2. Run query against target DB and collect metrics (NO CACHE)
        3. Cache query in Readyset container
        4. Run query against Readyset and collect metrics (WITH CACHE)
        5. Compare performance
        6. Output CREATE CACHE command for user's production Readyset
        7. Save query to registry if new

        Args:
            query: SQL query string OR hash_id from registry
            strategy: Caching strategy (explicit, always, etc.)
            target: Target database name
            tag: Optional tag to assign when saving to registry
            json: Output results in JSON format
            **kwargs: Additional arguments

        Returns:
            RdstResult with performance comparison and caching instructions
        """
        from .cache_command import CacheCommand

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
            target_config = cfg.get(target)

            if not target_config:
                available_targets = cfg.list_targets()
                targets_str = ', '.join(available_targets) if available_targets else 'none'
                return RdstResult(False, f"Target '{target}' not found. Available targets: {targets_str}")

            # Execute cache command
            cache_cmd = CacheCommand(client=self.client)
            return cache_cmd.execute_cache(query, target, target_config, strategy, tag, json)

        except Exception as e:
            return RdstResult(False, f"cache command failed: {e}")

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
        """Stub: Report CLI/library version."""
        # In a future iteration, derive from package/version file
        return RdstResult(True, "Readyset Diagnostics & SQL Tuning (rdst) version 0.0.0 (stubs)")

    # rdst report
    def report(self, title: str, body: str = "", **kwargs) -> RdstResult:
        """Stub: Submit feedback or bug reports from within the CLI."""
        if not title:
            return RdstResult(False, "report requires a title")
        msg = "Report stub – would submit feedback via control plane API."
        return RdstResult(True, msg, data={"title": title, "body": body})









# Ready-to-use singleton for simple imports: from lib.cli import rdst
rdst = RdstCLI()
