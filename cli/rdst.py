#!/usr/bin/env python3
"""
rdst - Readyset Data and SQL Toolkit

A command-line interface for diagnostics, query analysis, performance tuning,
and caching with Readyset.
"""

from __future__ import annotations

import json
import os
import argparse
import sys
import signal
from pathlib import Path

# Restore default SIGINT behavior so KeyboardInterrupt is raised instead of
# calling sys.exit(130).  Raising KeyboardInterrupt lets ThreadPoolExecutor
# tear down gracefully and allows `except KeyboardInterrupt` blocks (e.g. in
# report_command.py) to catch Ctrl-C correctly.  The main() try/except catches
# KeyboardInterrupt and exits with code 1.
signal.signal(signal.SIGINT, signal.default_int_handler)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, signal.default_int_handler)


# UI system
from shared.stdio import configure_utf8_stdio
from shared.ui import StyleTokens, get_console, DataTable, SectionHeader


def _resolve_embedded_web_dist_dir() -> Path | None:
    env_dir = os.environ.get("RDST_WEB_DIST_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.append(Path(__file__).resolve().parent / "web_dist")

    for candidate in candidates:
        path = candidate.resolve()
        if (path / "index.html").exists():
            return path

    return None


def _running_under_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def _open_url_in_browser(url: str) -> None:
    """Open `url` in the system default browser, best-effort.

    `webbrowser` covers macOS (`open`), native Windows (`os.startfile`), and
    desktop Linux (`xdg-open`). Under WSL no Linux-side browser may be wired
    up, so fall back to launching the WINDOWS default browser through the
    interop layer. Headless boxes end up a silent no-op."""
    import subprocess
    import webbrowser

    try:
        if webbrowser.open(url):
            return
    except Exception:
        pass
    if not _running_under_wsl():
        return
    for opener in (
        ["wslview", url],
        ["explorer.exe", url],
    ):
        try:
            subprocess.run(opener, check=True, capture_output=True, timeout=10)
            return
        except Exception:
            continue


def _open_browser_when_ready(host: str, port: int) -> None:
    """Open the default browser at the web UI once the server answers.

    Polls /health from a daemon thread so uvicorn owns the main thread; gives
    up quietly after a short deadline (headless boxes, missing browser)."""
    import threading
    import time
    import urllib.request

    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{browse_host}:{port}"

    def _wait_and_open() -> None:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{url}/health", timeout=1)
            except Exception:
                time.sleep(0.25)
                continue
            _open_url_in_browser(url)
            return

    threading.Thread(target=_wait_and_open, daemon=True).start()


def _resolve_rdst_source_dir(repo_root: Path | None = None) -> Path:
    def _looks_like_rdst_source_dir(candidate: Path) -> bool:
        return (
            (candidate / "rdst.py").exists()
            and (candidate / "features").is_dir()
            and (candidate / "shared").is_dir()
        )

    env_dir = os.environ.get("RDST_SOURCE_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser().resolve()
        if _looks_like_rdst_source_dir(candidate):
            return candidate

    cwd = Path.cwd()
    candidates = []
    if repo_root:
        candidates.append(repo_root / "rdst")
    candidates.extend(
        [
            cwd / "rdst",
            cwd,
            cwd / ".." / "rdst",
            Path(__file__).resolve().parent,
        ]
    )

    for candidate in candidates:
        path = candidate.resolve()
        if _looks_like_rdst_source_dir(path):
            return path

    return Path(__file__).resolve().parent


def _restore_web_required_env_vars() -> tuple[list[str], list[str], list[str]]:
    """Restore required env vars from secure store for `rdst web` startup."""
    try:
        service_class = _get_env_requirements_service_class()

        requirements = service_class()
        required_names = requirements.get_required_names_for_restore()
        if not required_names:
            return [], [], []

        result = requirements.secret_store.restore_required(required_names)
        restored = list(result.get("restored", []))
        errors = list(result.get("errors", []))
        missing = sorted(
            [name for name in required_names if not os.environ.get(name)]
        )
        return restored, missing, errors
    except Exception as e:
        return [], [], [f"Preflight env restore failed: {e}"]


def _clear_web_required_env_vars() -> tuple[list[str], list[str], list[str]]:
    """Clear required env vars from secure store and current process env."""
    try:
        service_class = _get_env_requirements_service_class()

        requirements = service_class()
        required_names = requirements.get_allowed_secret_names()
        if not required_names:
            return [], [], []

        result = requirements.secret_store.clear_required(required_names)
        cleared = list(result.get("cleared", []))
        missing = list(result.get("missing", []))
        errors = list(result.get("errors", []))
        return cleared, missing, errors
    except Exception as e:
        return [], [], [f"Keyring clear failed: {e}"]


def _get_env_requirements_service_class():
    from shared.env_requirements_service import EnvRequirementsService

    return EnvRequirementsService


def _get_create_app():
    from shared.api.app import create_app

    return create_app


def print_rich_help():
    """Print colorized help using Rich."""
    from shared.cli.parser_data import get_grouped_commands, get_main_examples

    console = get_console()

    # Header
    console.print()
    console.print(SectionHeader("Readyset Data and SQL Toolkit"))
    console.print(
        f"[{StyleTokens.MUTED}]Troubleshoot latency, analyze queries, and get tuning insights.[/{StyleTokens.MUTED}]"
    )
    console.print()

    for group_name, commands in get_grouped_commands():
        table = DataTable(
            columns=["Command", "Description"],
            rows=commands,
            title=group_name,
        )
        console.print(table)
        console.print()

    console.print(f"[{StyleTokens.EMPHASIS}]Examples:[/{StyleTokens.EMPHASIS}]")
    for cmd, desc in get_main_examples():
        console.print(f"  [{StyleTokens.COMMAND}]{cmd}[/{StyleTokens.COMMAND}]")
        console.print(f"    [{StyleTokens.MUTED}]{desc}[/{StyleTokens.MUTED}]")

    console.print()
    console.print(
        f"[{StyleTokens.MUTED}]Use[/{StyleTokens.MUTED}] [{StyleTokens.COMMAND}]rdst <command> --help[/{StyleTokens.COMMAND}] [{StyleTokens.MUTED}]for command-specific options[/{StyleTokens.MUTED}]"
    )
    console.print()

    return True


# Import the CLI functionality
from shared.cli import RdstCLI, RdstResult


def parse_arguments() -> argparse.Namespace:
    from shared.cli.parser_data import build_all_subparsers

    parser = argparse.ArgumentParser(
        prog="rdst",
        description="Readyset Data and SQL Toolkit - Diagnose, analyze, and optimize SQL performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  configure     Manage database targets and connection profiles
  top          Live view of top slow queries
  analyze      Analyze and explain SQL queries
  init         First-time setup wizard
  tag          Tag and store queries for later reference
  list         Show saved queries
  version      Show version information
  update       Check for and install RDST updates
  report       Submit feedback or bug reports
  help         Show detailed help

Examples:
  rdst configure add --target prod --host db.example.com --user admin
  rdst configure add --target prod --connection-string "postgresql://user@host:5432/db"
  rdst configure list
  rdst analyze "SELECT * FROM users WHERE active = true"
  rdst analyze "SELECT COUNT(*) FROM orders WHERE status = 'pending'"
  rdst top --limit 10
  rdst top --source slowlog --target mysql-db
        """,
    )

    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    try:
        from importlib.metadata import version as _get_version
        _pkg_version = _get_version("rdst")
    except Exception:
        try:
            from _version import __version__ as _pkg_version
        except Exception:
            _pkg_version = "unknown"
    parser.add_argument(
        "--version",
        action="version",
        version=f"Readyset Data and SQL Toolkit (rdst) version {_pkg_version}",
        help="Show version information and exit",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    build_all_subparsers(subparsers)

    return parser.parse_args()


def execute_command(cli: RdstCLI, args: argparse.Namespace) -> RdstResult:
    """Execute the appropriate CLI command based on parsed arguments."""

    # Convert argparse Namespace to kwargs dictionary
    kwargs = {k: v for k, v in vars(args).items() if v is not None and k != "command"}

    command = args.command

    if command == "configure":
        # configure now uses add_subparsers(); subcommand is in configure_subcommand
        configure_subcommand = getattr(args, "configure_subcommand", None)
        if configure_subcommand is not None:
            kwargs["subcommand"] = configure_subcommand
        return cli.configure(config_path=args.config, **kwargs)
    elif command == "tunnel":
        tunnel_subcommand = getattr(args, "tunnel_subcommand", None)
        if not tunnel_subcommand:
            return RdstResult(
                False,
                "Tunnel command requires a subcommand: list, close, test\n"
                "Try: rdst tunnel --help",
            )
        return cli.tunnel(
            subcommand=tunnel_subcommand,
            target=getattr(args, "target", None),
            close_all=getattr(args, "close_all", False),
        )
    elif command == "top":
        return cli.top(**kwargs)
    elif command == "analyze":
        # Create filtered kwargs for analyze (exclude analyze-specific parameters)
        analyze_exclude_keys = [
            "query",
            "hash",
            "inline_query",
            "file",
            "stdin",
            "name",
            "target",
            "save_as",
            "fast",
            "interactive",
            "review",
            "json",
            "skip_warning",
        ]
        filtered_kwargs = {
            k: v for k, v in kwargs.items() if k not in analyze_exclude_keys
        }

        return cli.analyze(
            hash=getattr(args, "hash", None),
            query=getattr(args, "inline_query", None),  # -q/--query flag
            file=getattr(args, "file", None),
            stdin=getattr(args, "stdin", False),
            name=getattr(args, "name", None),
            positional_query=getattr(args, "query", None),  # positional argument
            target=getattr(args, "target", None),
            save_as=getattr(args, "save_as", None),
            fast=getattr(args, "fast", False),
            interactive=getattr(args, "interactive", False),
            review=getattr(args, "review", False),
            output_json=getattr(args, "json", False),
            skip_warning=getattr(args, "skip_warning", False),
            **filtered_kwargs,
        )
    elif command == "init":
        return cli.init(**kwargs)
    elif command == "query":
        # Query command with subcommands
        if not hasattr(args, "query_subcommand") or not args.query_subcommand:
            return RdstResult(
                False,
                "Query command requires a subcommand: add, edit, list, show, delete, rm\nTry: rdst query --help",
            )

        query_subcommand = args.query_subcommand

        # Build kwargs for query command
        query_kwargs = {}
        if query_subcommand in ["add", "edit", "delete", "rm", "show"]:
            query_kwargs["name"] = getattr(args, "query_name", None)
        if query_subcommand in ["edit", "delete", "rm", "show"]:
            query_kwargs["hash"] = getattr(args, "hash", None)
        if query_subcommand == "add":
            query_kwargs["query"] = getattr(args, "query", None)
            query_kwargs["file"] = getattr(args, "file", None)
            query_kwargs["target"] = getattr(args, "target", None)
        if query_subcommand == "import":
            query_kwargs["file"] = getattr(args, "file", None)
            query_kwargs["update"] = getattr(args, "update", False)
            query_kwargs["target"] = getattr(args, "target", None)
        if query_subcommand in ["list"]:
            query_kwargs["limit"] = getattr(args, "limit", 10)
            query_kwargs["target"] = getattr(args, "target", None)
            query_kwargs["filter"] = getattr(args, "filter", None)
            query_kwargs["interactive"] = getattr(args, "interactive", False)
        if query_subcommand in ["delete", "rm"]:
            query_kwargs["force"] = getattr(args, "force", False)
        if query_subcommand == "run":
            query_kwargs["queries"] = getattr(args, "queries", [])
            query_kwargs["target"] = getattr(args, "target", None)
            query_kwargs["interval"] = getattr(args, "interval", None)
            query_kwargs["concurrency"] = getattr(args, "concurrency", None)
            query_kwargs["duration"] = getattr(args, "duration", None)
            query_kwargs["count"] = getattr(args, "count", None)
            query_kwargs["quiet"] = getattr(args, "quiet", False)
            query_kwargs["file"] = getattr(args, "file", None)
            query_kwargs["analyze"] = getattr(args, "analyze", False)
            query_kwargs["skip_warning"] = getattr(args, "skip_warning", False)
        if query_subcommand == "cache-compare":
            query_kwargs["queries"] = getattr(args, "queries", [])
            query_kwargs["target"] = getattr(args, "target", None)
            query_kwargs["interval"] = getattr(args, "interval", None)
            query_kwargs["concurrency"] = getattr(args, "concurrency", None)
            query_kwargs["duration"] = getattr(args, "duration", None)
            query_kwargs["count"] = getattr(args, "count", 100)
            query_kwargs["quiet"] = getattr(args, "quiet", False)
            query_kwargs["skip_warning"] = getattr(args, "skip_warning", False)

        result = cli.query(subcommand=query_subcommand, **query_kwargs)

        # If user selected a query to analyze, exec into analyze command for clean terminal
        if result.data and result.data.get("action") == "analyze":
            selected_hash = result.data.get("selected_hash")
            selected_target = result.data.get("selected_target")

            import subprocess

            from shared.child_process import rdst_child_argv, rdst_child_env

            analyze_args = ["analyze", "--hash", selected_hash]
            if selected_target:
                analyze_args.extend(["--target", selected_target])

            child = subprocess.run(
                rdst_child_argv(analyze_args), env=rdst_child_env()
            )
            if child.returncode != 0:
                return RdstResult(False, "Analyze command failed")

        return result

    # ============================================================================
    # RDST ASK & SCHEMA - Natural language to SQL and semantic layer
    # ============================================================================
    elif command == "ask":
        return cli.ask(
            question=getattr(args, "question", None),
            target=getattr(args, "target", None),
            dry_run=getattr(args, "dry_run", False),
            timeout=getattr(args, "timeout", 30),
            verbose=getattr(args, "verbose", False),
            agent_mode=getattr(args, "agent_mode", False),
            no_interactive=getattr(args, "no_interactive", False),
        )

    elif command == "schema":
        schema_subcommand = getattr(args, "schema_subcommand", None)
        schema_kwargs = {
            "subcommand": schema_subcommand,
            "target": getattr(args, "target", None),
        }

        if schema_subcommand in ["show", "edit", "annotate", "profile"]:
            schema_kwargs["table"] = getattr(args, "table", None)
        if schema_subcommand == "annotate":
            schema_kwargs["use_llm"] = getattr(args, "use_llm", False)
            schema_kwargs["auto_accept"] = getattr(args, "auto_accept", False)
            schema_kwargs["sample_rows"] = getattr(args, "sample_rows", 5)
        if schema_subcommand == "init":
            schema_kwargs["enum_threshold"] = getattr(args, "enum_threshold", 20)
            schema_kwargs["force"] = getattr(args, "force", False)
            schema_kwargs["interactive"] = getattr(args, "interactive", False)
        if schema_subcommand == "export":
            schema_kwargs["output_format"] = getattr(args, "output_format", "yaml")
        if schema_subcommand == "delete":
            schema_kwargs["force"] = getattr(args, "force", False)

        return cli.schema(**schema_kwargs)

    elif command == 'demo':
        from features.demo.cli import DemoCommand
        demo_cmd = DemoCommand()
        demo_subcommand = getattr(args, 'demo_subcommand', None)
        tour_name = getattr(args, 'tour_name', 'quickstart')
        return demo_cmd.run(demo_subcommand, tour_name=tour_name)

    elif command == 'scan':
        from features.scan.cli.command import ScanCommand
        scan_cmd = ScanCommand()
        output_format = getattr(args, 'output', 'table')
        return scan_cmd.execute(
            subcommand=getattr(args, 'subcommand', 'scan'),
            directory=getattr(args, 'directory', '.'),
            dry_run=getattr(args, 'dry_run', False),
            analyze=getattr(args, 'analyze', False),
            target=getattr(args, 'target', None),
            output_json=(output_format == 'json'),
            file_pattern=getattr(args, 'file_pattern', None),
            diff=getattr(args, 'diff', None),
            shallow=getattr(args, 'shallow', False),
            warn_threshold=getattr(args, 'warn_threshold', 50),
            fail_threshold=getattr(args, 'fail_threshold', 30),
            nosave=getattr(args, 'nosave', False),
            sequential=getattr(args, 'sequential', False),
        )

    # =========================================================================
    # Fleet — Multi-target management
    # =========================================================================
    elif command == "fleet":
        fleet_subcommand = getattr(args, "fleet_subcommand", None)
        if not fleet_subcommand:
            return RdstResult(
                False,
                "Fleet command requires a subcommand: configure, import, discover, list, status, audit, diff, snapshots\n"
                "Try: rdst fleet --help",
            )
        from features.fleet.cli.command import FleetCommand

        fleet_cmd = FleetCommand()
        return fleet_cmd.execute(subcommand=fleet_subcommand, args=args)

    # =========================================================================
    # Audit — Single-target deep health audit
    # =========================================================================
    elif command == "audit":
        audit_subcommand = getattr(args, "audit_subcommand", None)
        from features.audit.cli.command import AuditCommand

        audit_cmd = AuditCommand()
        if audit_subcommand in ("list", "show"):
            return audit_cmd.execute_subcommand(audit_subcommand, args)
        else:
            return audit_cmd.execute(args=args)

    elif command == 'version':
        return cli.version()
    elif command == "update":
        from features.update.cli import UpdateCommand

        return UpdateCommand().execute(
            check=getattr(args, "check", False),
            version=getattr(args, "version", None),
        )
    elif command == "claude":
        # Register or remove RDST from Claude Code
        import shutil
        import subprocess

        action = getattr(args, "action", "add")

        # Check if claude CLI is available
        claude_path = shutil.which("claude")
        if not claude_path:
            return RdstResult(
                False,
                "Claude Code CLI not found. Install it from: https://claude.ai/code",
            )

        if action == "add":
            # Register the MCP server
            # Determine the best way to run the MCP server:
            # 1. If rdst-mcp is in PATH (pip installed), use it
            # 2. Otherwise, use the current interpreter with mcp_server.py
            rdst_mcp_path = shutil.which("rdst-mcp")
            if getattr(sys, "frozen", False):
                mcp_command = [sys.executable, "_mcp_server"]
            elif rdst_mcp_path:
                mcp_command = [rdst_mcp_path]
            else:
                # Find mcp_server.py relative to this script
                script_dir = os.path.dirname(os.path.abspath(__file__))
                mcp_server_path = os.path.join(script_dir, "mcp_server.py")
                if not os.path.exists(mcp_server_path):
                    return RdstResult(
                        False, f"MCP server not found at {mcp_server_path}"
                    )
                # Use uv run to ensure dependencies are available.
                uv_path = shutil.which("uv")
                if uv_path:
                    mcp_command = [
                        uv_path,
                        "run",
                        "--directory",
                        script_dir,
                        "python",
                        mcp_server_path,
                    ]
                else:
                    mcp_command = [sys.executable, mcp_server_path]

            # Install the /rdst slash command globally
            slash_cmd_content = """# RDST Mode Activated

You have RDST (Readyset Data and SQL Toolkit) tools available.

**First, call the `rdst_help` tool to check the user's setup.**

Based on the result:

## If NO targets are configured (first-time user):

Present a friendly welcome:

---

**Welcome to RDST!**

Looks like this is your first time using RDST. I'll help you get set up.

To analyze your database queries, I need to connect to your database. Please provide:

1. **Database type**: PostgreSQL or MySQL?
2. **Host**: Where is your database? (e.g., localhost, db.example.com)
3. **Port**: What port? (default: 5432 for PostgreSQL, 3306 for MySQL)
4. **Username**: Database user to connect as
5. **Database name**: Which database to connect to
6. **Password env var name**: What should I call the environment variable for the password? (e.g., MY_DB_PASSWORD)

Once you give me these details, I'll configure RDST and we can start analyzing your slow queries!

---

## If targets ARE configured:

Present a status summary:

---

**RDST Ready**

[List their configured targets - show which are ready vs need passwords]

[If any need passwords, name each password environment variable and ask the user to set it in the shell that starts Claude Code.]

**What would you like to do?**
- Analyze a SQL query
- Find and fix slow queries
- Explore your database
- Add another database connection

---

Keep it conversational. The user shouldn't need to know the underlying commands - just help them with their database.
"""
            # Install slash command to ~/.claude/commands/
            claude_commands_dir = os.path.expanduser("~/.claude/commands")
            os.makedirs(claude_commands_dir, exist_ok=True)
            slash_cmd_path = os.path.join(claude_commands_dir, "rdst.md")
            try:
                with open(slash_cmd_path, "w", encoding="utf-8") as f:
                    f.write(slash_cmd_content)
            except Exception:
                # Non-fatal - continue with MCP registration
                pass

            try:
                result = subprocess.run(
                    [claude_path, "mcp", "add", "rdst", "--"] + mcp_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    return RdstResult(
                        True,
                        """RDST registered with Claude Code!

To use RDST in Claude:
  1. Start a new Claude Code session
  2. Type /rdst to activate RDST mode

Claude will now have access to all RDST tools for query analysis and optimization.""",
                    )
                else:
                    # Check if already registered
                    if "already exists" in result.stderr.lower():
                        return RdstResult(
                            True, "RDST is already registered with Claude Code."
                        )
                    return RdstResult(False, f"Failed to register: {result.stderr}")
            except Exception as e:
                return RdstResult(False, f"Error running claude command: {e}")

        elif action == "remove":
            # Remove the slash command
            slash_cmd_path = os.path.expanduser("~/.claude/commands/rdst.md")
            if os.path.exists(slash_cmd_path):
                try:
                    os.remove(slash_cmd_path)
                except Exception:
                    pass  # Non-fatal

            try:
                result = subprocess.run(
                    [claude_path, "mcp", "remove", "rdst"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    return RdstResult(True, "RDST removed from Claude Code.")
                else:
                    return RdstResult(False, f"Failed to remove: {result.stderr}")
            except Exception as e:
                return RdstResult(False, f"Error running claude command: {e}")

        return RdstResult(False, f"Unknown action: {action}")
    elif command == "report":
        from shared.cli.report_command import ReportCommand

        report_cmd = ReportCommand()
        success = report_cmd.run(
            query_hash=getattr(args, "hash", None),
            reason=getattr(args, "reason", None),
            email=getattr(args, "email", None),
            positive=getattr(args, "positive", False),
            negative=getattr(args, "negative", False),
            include_query=getattr(args, "include_query", False),
            include_plan=getattr(args, "include_plan", False),
        )
        return RdstResult(success, "")
    elif command == "web":
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8787)
        reload = getattr(args, "reload", False)
        ui_mode = getattr(args, "ui", "auto")
        clear = getattr(args, "clear", False)

        if clear:
            cleared_envs, missing_envs, clear_errors = _clear_web_required_env_vars()
            if cleared_envs:
                print(f"Cleared secure env vars: {', '.join(cleared_envs)}")
            if missing_envs:
                print(f"No persisted entries found for: {', '.join(missing_envs)}")
            if clear_errors:
                print("Keyring clear warnings:")
                for err in clear_errors:
                    print(f"  - {err}")
                return RdstResult(False, "Failed to clear one or more secure env vars.")
            return RdstResult(True, "")

        try:
            import uvicorn
        except ImportError:
            return RdstResult(
                False,
                "Server dependencies are unavailable.\n"
                "Run 'rdst update' or reinstall RDST.",
            )

        rdst_dir = _resolve_rdst_source_dir()
        dist_dir = _resolve_embedded_web_dist_dir()
        serve_static = False

        if ui_mode == "none":
            serve_static = False
        elif ui_mode == "auto":
            serve_static = dist_dir is not None
        elif ui_mode == "dist":
            if dist_dir is None:
                return RdstResult(
                    False,
                    "Embedded RDST frontend not found. Reinstall RDST with packaged "
                    "web assets, or run with --ui none.",
                )
            serve_static = True
        else:
            return RdstResult(False, f"Invalid UI mode: {ui_mode}")

        os.environ["RDST_WEB_SERVE_STATIC"] = "1" if serve_static else "0"
        # The web server has no interactive terminal to theme; suppress the
        # OSC-11 /dev/tty probe so --reload worker respawns (which inherit this
        # env) never contend for the terminal on import.
        os.environ["RDST_NO_TTY_THEME_PROBE"] = "1"
        if serve_static and dist_dir:
            os.environ["RDST_WEB_DIST_DIR"] = str(dist_dir)
        else:
            os.environ.pop("RDST_WEB_DIST_DIR", None)

        restored_envs, missing_envs, restore_errors = _restore_web_required_env_vars()

        try:
            from shared.telemetry import telemetry
            telemetry.track("web_started", {
                "host": host,
                "port": port,
                "serve_static": serve_static,
                "ui_mode": ui_mode,
            })
        except Exception:
            pass

        print(f"Starting RDST web server on http://{host}:{port}")
        if serve_static:
            print(f"Serving embedded frontend from: {dist_dir}")
        elif ui_mode == "auto":
            print("No embedded frontend found; running API-only mode")

        if restored_envs:
            print(f"Restored secure env vars: {', '.join(restored_envs)}")
        if missing_envs:
            print(f"Missing required env vars: {', '.join(missing_envs)}")
        if restore_errors:
            print("Env restore warnings:")
            for err in restore_errors:
                print(f"  - {err}")
        if restored_envs or missing_envs:
            print(
                "Note: values set in Web apply immediately for this process. "
                "Values exported in a different shell require restarting `rdst web`."
            )

        if reload:
            print("Auto-reload enabled (watching for file changes)")
        open_browser = serve_static and not getattr(args, "no_browser", False)
        if open_browser:
            print("Opening the web UI in your browser (disable with --no-browser)")
            _open_browser_when_ready(host, port)
        print("Press Ctrl+C to stop")
        print()

        if reload:
            uvicorn.run(
                "shared.api.app:create_app",
                host=host,
                port=port,
                reload=True,
                factory=True,
                reload_dirs=[str(rdst_dir)],
            )
        else:
            static_dir_arg = str(dist_dir) if serve_static and dist_dir else None
            app = _get_create_app()(static_dist_dir=static_dir_arg)
            uvicorn.run(app, host=host, port=port)
        return RdstResult(True, "")
    elif command == "help" or command is None:
        # Check if a question was provided
        question = " ".join(getattr(args, "question", []) or [])
        if question:
            # Answer the question using the help command
            from shared.cli.help_command import HelpCommand

            help_cmd = HelpCommand()
            result = help_cmd.run(question)
            if result.success:
                help_cmd.print_formatted(result.answer)
                return RdstResult(True, "")
            else:
                return RdstResult(False, result.error or "Failed to answer question")
        else:
            # Show general help
            return cli.help()
    elif command == 'slack':
        from features.slack.cli import SlackCommand
        slack_cmd = SlackCommand()
        return slack_cmd.execute(
            subcommand=getattr(args, 'subcommand', 'list'),
            agent=getattr(args, 'agent', None),
        )
    elif command == 'agent':
        from features.agent.cli import AgentCommand
        agent_cmd = AgentCommand()
        # agent now uses add_subparsers(); subcommand is in agent_subcommand
        # Support both --name and positional agent_name
        name = getattr(args, 'name', None) or getattr(args, 'agent_name', None)
        return agent_cmd.execute(
            subcommand=getattr(args, 'agent_subcommand', None),
            name=name,
            target=getattr(args, 'target', None),
            description=getattr(args, 'description', ''),
            max_rows=getattr(args, 'max_rows', 1000),
            timeout=getattr(args, 'timeout', 600),
            port=getattr(args, 'port', 8080),
            deny_columns=getattr(args, 'deny_columns', None),
            allow_tables=getattr(args, 'allow_tables', None),
            guard=getattr(args, 'guard', None),
            verbose=getattr(args, 'verbose', False),
        )
    elif command == 'guard':
        from features.guard.cli import GuardCommand
        guard_cmd = GuardCommand()
        # guard now uses add_subparsers(); subcommand is in guard_subcommand
        # Support both --name and positional guard_name
        name = getattr(args, 'name', None) or getattr(args, 'guard_name', None)
        # For 'check': positional 'sql' arg or --sql flag (stored as sql_flag)
        sql = getattr(args, 'sql', None) or getattr(args, 'sql_flag', None)
        return guard_cmd.execute(
            subcommand=getattr(args, 'guard_subcommand', None),
            name=name,
            description=getattr(args, 'description', ''),
            mask=getattr(args, 'mask', None),
            deny_columns=getattr(args, 'deny_columns', None),
            allow_tables=getattr(args, 'allow_tables', None),
            require_where=getattr(args, 'require_where', False),
            require_limit=getattr(args, 'require_limit', False),
            no_select_star=getattr(args, 'no_select_star', False),
            max_tables=getattr(args, 'max_tables', None),
            cost_limit=getattr(args, 'cost_limit', None),
            max_estimated_rows=getattr(args, 'max_estimated_rows', None),
            required_filters=getattr(args, 'required_filters', None),
            intent=getattr(args, 'intent', None),
            schema_context=getattr(args, 'schema_context', None),
            max_rows=getattr(args, 'max_rows', 1000),
            timeout=getattr(args, 'timeout', 30),
            sql=sql,
            check_guard=getattr(args, 'check_guard', None),
            target=getattr(args, 'target', None),
        )
    else:
        return RdstResult(False, f"Unknown command: {command}")


def _interactive_menu(cli: RdstCLI) -> RdstResult:
    """Interactive menu when no command is provided.

    Presents a simple numbered list of commands and prompts for minimal
    required inputs when needed. Re-prompts on invalid input.
    """
    try:
        # If stdin is not a TTY, fall back to help behavior
        if not sys.stdin.isatty():
            return cli.help()

        from shared.cli.parser_data import COMMANDS as _PARSER_COMMANDS

        # Build the commands list from parser_data so descriptions stay in sync.
        # Order matches what --help shows; 'exit' is appended as a menu-only entry.
        _menu_command_names = [
            "configure", "top", "analyze", "ask", "scan", "agent", "guard",
            "init", "query", "schema", "tunnel", "fleet", "audit", "demo",
            "version", "update", "report", "help", "claude", "slack", "web",
        ]
        commands = [
            (name, _PARSER_COMMANDS[name].short_help)
            for name in _menu_command_names
        ] + [("exit", "Exit rdst")]

        # Use UI system components
        from shared.ui import get_console, DataTable, SectionHeader

        console = get_console()

        # Header
        console.print()
        console.print(SectionHeader("Readyset Data and SQL Toolkit"))
        console.print(
            f"[{StyleTokens.MUTED}]Troubleshoot latency, analyze queries, and get tuning insights.[/{StyleTokens.MUTED}]"
        )
        console.print()

        # Commands table using DataTable component
        rows = [(cmd, desc) for cmd, desc in commands]
        table = DataTable(
            columns=["Command", "Description"],
            rows=rows,
            show_row_numbers=True,
        )
        console.print(table)

        while True:
            choice = input("Select option [1]: ").strip()
            if not choice:
                choice_idx = 1
                break
            if choice.lower() in ("q", "quit", "exit"):
                return RdstResult(True, "Goodbye!")
            try:
                choice_idx = int(choice)
            except ValueError:
                console.print(
                    f"[red]Invalid option. Please enter a number 1-{len(commands)} or 'q' to quit.[/red]"
                )
                continue
            if choice_idx < 1 or choice_idx > len(commands):
                console.print(
                    f"[red]Invalid option. Please enter a number 1-{len(commands)} or 'q' to quit.[/red]"
                )
                continue
            break

        cmd = commands[choice_idx - 1][0]

        # Prompt for required parameters for certain commands
        if cmd == "configure":
            # Let the configure flow handle interactive wizard by default
            return cli.configure()
        elif cmd == "top":
            limit_str = input("Limit [20]: ").strip()
            try:
                limit = int(limit_str) if limit_str else 20
            except ValueError:
                limit = 20
            return cli.top(limit=limit)
        elif cmd == "analyze":
            query = input("SQL query: ").strip()
            if not query:
                return RdstResult(False, "analyze requires a SQL query")
            return cli.analyze(query=query)
        elif cmd == "init":
            return cli.init()
        elif cmd == "query":
            # Query command now has subcommands
            from shared.ui import SelectPrompt, Prompt

            options = [
                "add - Add a new query",
                "list - List all queries",
                "edit - Edit existing query",
                "delete - Delete a query",
            ]
            subcmd_choice = SelectPrompt.ask(
                "Query subcommands:", options, default=1, allow_cancel=True
            )
            if subcmd_choice is None:
                return RdstResult(False, "Cancelled")

            if subcmd_choice == 1:  # add
                queryname = Prompt.ask("Query name").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required")
                # Will open $EDITOR if no query provided
                return cli.query(subcommand="add", name=queryname)
            elif subcmd_choice == 2:  # list
                return cli.query(subcommand="list")
            elif subcmd_choice == 3:  # edit
                queryname = Prompt.ask("Query name to edit").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required for edit")
                return cli.query(subcommand="edit", name=queryname)
            elif subcmd_choice == 4:  # delete
                queryname = Prompt.ask("Query name to delete").strip()
                if not queryname:
                    return RdstResult(False, "Query name is required for delete")
                return cli.query(subcommand="delete", name=queryname)
            else:
                return RdstResult(False, "Invalid query subcommand")
        elif cmd == "scan":
            directory = input("Directory to scan [.]: ").strip() or "."
            from features.scan.cli.command import ScanCommand
            scan_cmd = ScanCommand()
            return scan_cmd.execute(
                subcommand="scan",
                directory=directory,
                dry_run=False,
                analyze=False,
                target=None,
                output_json=False,
                file_pattern=None,
                diff=None,
                shallow=False,
                warn_threshold=50,
                fail_threshold=30,
                nosave=False,
                sequential=False,
            )
        elif cmd == "agent":
            from features.agent.cli import AgentCommand
            agent_cmd = AgentCommand()
            return agent_cmd.execute(subcommand="list")
        elif cmd == "guard":
            from features.guard.cli import GuardCommand
            guard_cmd = GuardCommand()
            return guard_cmd.execute(subcommand="list")
        elif cmd == "ask":
            return cli.ask()
        elif cmd == "schema":
            return cli.schema()
        elif cmd == "version":
            return cli.version()
        elif cmd == "update":
            from features.update.cli import UpdateCommand

            return UpdateCommand().execute()
        elif cmd == "report":
            from shared.cli.report_command import ReportCommand
            report_cmd = ReportCommand()
            success = report_cmd.run()
            return RdstResult(success, "")
        elif cmd == "tunnel":
            return RdstResult(True, "Run: rdst tunnel --help")
        elif cmd == "fleet":
            return RdstResult(True, "Run: rdst fleet --help")
        elif cmd == "audit":
            return RdstResult(True, "Run: rdst audit --help")
        elif cmd == "demo":
            return RdstResult(True, "Run: rdst demo --help")
        elif cmd == "claude":
            return RdstResult(True, "Run: rdst claude --help")
        elif cmd == "slack":
            return RdstResult(True, "Run: rdst slack --help")
        elif cmd == "web":
            return RdstResult(True, "Run: rdst web --help")
        elif cmd == "help":
            return cli.help()
        else:  # exit
            return RdstResult(True, "Goodbye!")
    except (EOFError, KeyboardInterrupt):
        get_console().print("\n[yellow]Cancelled[/yellow]")
        return RdstResult(True, "")


def _invoked_as_mcp_server() -> bool:
    """Whether this process was started through the rdst-mcp entrypoint."""
    invoked = sys.argv[0] if sys.argv else ""
    return os.path.basename(invoked) == "rdst-mcp"


def main():
    configure_utf8_stdio()
    if sys.argv[1:] == ["_mcp_server"]:
        from mcp_server import main as mcp_main

        mcp_main()
        return

    # A frozen build is a single executable, so the installer publishes
    # rdst-mcp as a link to it and the invoked name selects the entrypoint.
    # Source installs declare rdst-mcp as its own console script, which routes
    # to mcp_server:main directly and never reaches here.
    if _invoked_as_mcp_server():
        import mcp_server

        mcp_server.main()
        return

    try:
        if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h", "help"):
            print_rich_help()
            sys.exit(0)

        args = parse_arguments()

        # Initialize the CLI
        cli = RdstCLI()

        # If no command specified, offer interactive menu
        if not args.command:
            result = _interactive_menu(cli)
        else:
            # Execute the command
            result = execute_command(cli, args)

        # Handle the result
        if result.ok:
            if result.message:
                print(result.message)
            # Print JSON data if present (for --json flag on commands like top)
            elif result.data:
                print(json.dumps(result.data, indent=2, default=str))

            # Check for periodic NPS prompt (every ~100 commands)
            try:
                from shared.telemetry import telemetry

                if telemetry.should_show_nps_prompt():
                    telemetry.show_nps_prompt()
            except Exception:
                pass  # Don't fail if NPS prompt fails
        else:
            if result.message:
                print(f"Error: {result.message}", file=sys.stderr)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Report crash to telemetry
        try:
            from shared.telemetry import telemetry

            command = (
                args.command
                if "args" in locals() and hasattr(args, "command")
                else "unknown"
            )
            telemetry.report_crash(e, context={"command": command, "source": "main"})
        except Exception:
            pass  # Don't fail if telemetry fails

        import traceback

        traceback.print_exc()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Ensure telemetry events are flushed before exit
        try:
            from shared.telemetry import telemetry

            telemetry.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
