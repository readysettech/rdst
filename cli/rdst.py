#!/usr/bin/env python3
"""
rdst - Readyset Data and SQL Toolkit

A command-line interface for diagnostics, query analysis, performance tuning,
and caching with Readyset.
"""

import json
import os
import sys
import argparse
import shutil
import subprocess


# UI system
from lib.ui import StyleTokens, get_console, DataTable, SectionHeader


def print_rich_help():
    """Print colorized help using Rich."""
    from lib.cli.parser_data import get_commands_for_table, get_main_examples

    console = get_console()

    # Header
    console.print()
    console.print(SectionHeader("rdst", "Readyset Data and SQL Toolkit"))
    console.print(
        f"[{StyleTokens.MUTED}]Troubleshoot latency, analyze queries, and get tuning insights.[/{StyleTokens.MUTED}]"
    )
    console.print()

    table = DataTable(
        columns=["Command", "Description"],
        rows=get_commands_for_table(),
        title="Commands",
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
from lib.cli import RdstCLI, RdstResult


def parse_arguments() -> argparse.Namespace:
    from lib.cli.parser_data import build_all_subparsers

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
  report       Submit feedback or bug reports
  help         Show detailed help

Examples:
  rdst configure add --target prod --host db.example.com --user admin
  rdst configure add --target prod --connection-string "postgresql://user:pass@host:5432/db"
  rdst configure list
  rdst analyze "SELECT * FROM users WHERE active = true"
  rdst analyze "SELECT COUNT(*) FROM orders WHERE status = 'pending'" --readyset-cache
  rdst top --limit 10
        """,
    )

    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
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
        return cli.configure(config_path=args.config, **kwargs)
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
            "readyset_cache",
            "fast",
            "interactive",
            "review",
            "large_query_bypass",
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
            readyset_cache=getattr(args, "readyset_cache", False),
            fast=getattr(args, "fast", False),
            interactive=getattr(args, "interactive", False),
            review=getattr(args, "review", False),
            large_query_bypass=getattr(args, "large_query_bypass", None),
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

        result = cli.query(subcommand=query_subcommand, **query_kwargs)

        # If user selected a query to analyze, exec into analyze command for clean terminal
        if result.data and result.data.get("action") == "analyze":
            selected_hash = result.data.get("selected_hash")
            selected_target = result.data.get("selected_target")

            # Build args for analyze command - use Python interpreter since rdst.py is a script
            analyze_args = [
                sys.executable,
                sys.argv[0],
                "analyze",
                "--hash",
                selected_hash,
            ]
            if selected_target:
                analyze_args.extend(["--target", selected_target])

            # Replace this process with analyze - gives clean terminal state
            os.execv(sys.executable, analyze_args)

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

        if schema_subcommand in ["show", "edit", "annotate"]:
            schema_kwargs["table"] = getattr(args, "table", None)
        if schema_subcommand == "annotate":
            schema_kwargs["use_llm"] = getattr(args, "use_llm", False)
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

    elif command == "version":
        return cli.version()
    elif command == "claude":
        # Register or remove RDST from Claude Code
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
            # 2. Otherwise, use python3 with full path to mcp_server.py
            rdst_mcp_path = shutil.which("rdst-mcp")
            if rdst_mcp_path:
                mcp_command = ["rdst-mcp"]
            else:
                # Find mcp_server.py relative to this script
                script_dir = os.path.dirname(os.path.abspath(__file__))
                mcp_server_path = os.path.join(script_dir, "mcp_server.py")
                if not os.path.exists(mcp_server_path):
                    return RdstResult(
                        False, f"MCP server not found at {mcp_server_path}"
                    )
                # Use uv run to ensure dependencies are available, fallback to python3
                if shutil.which("uv"):
                    mcp_command = [
                        "uv",
                        "run",
                        "--directory",
                        script_dir,
                        "python",
                        mcp_server_path,
                    ]
                else:
                    mcp_command = ["python3", mcp_server_path]

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

[If any need passwords, show: "To use [target], export: `export VAR_NAME='password'`"]

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
                with open(slash_cmd_path, "w") as f:
                    f.write(slash_cmd_content)
            except Exception:
                # Non-fatal - continue with MCP registration
                pass

            try:
                result = subprocess.run(
                    ["claude", "mcp", "add", "rdst", "--"] + mcp_command,
                    capture_output=True,
                    text=True,
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
                    ["claude", "mcp", "remove", "rdst"], capture_output=True, text=True
                )
                if result.returncode == 0:
                    return RdstResult(True, "RDST removed from Claude Code.")
                else:
                    return RdstResult(False, f"Failed to remove: {result.stderr}")
            except Exception as e:
                return RdstResult(False, f"Error running claude command: {e}")

        return RdstResult(False, f"Unknown action: {action}")
    elif command == "report":
        from lib.cli.report_command import ReportCommand

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
    elif command == "help" or command is None:
        # Check if a question was provided
        question = " ".join(getattr(args, "question", []) or [])
        if question:
            # Answer the question using the help command
            from lib.cli.help_command import HelpCommand

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
    else:
        return RdstResult(False, f"Unknown command: {command}")


def _interactive_menu(cli: RdstCLI) -> RdstResult:
    """Interactive menu when no command is provided.

    Presents a simple numbered list of commands and prompts for minimal
    required inputs when needed. Falls back to help on invalid input.
    """
    try:
        # If stdin is not a TTY, fall back to help behavior
        if not sys.stdin.isatty():
            return cli.help()

        # Define commands once
        commands = [
            ("configure", "Manage database targets"),
            ("top", "Live view of slow queries"),
            ("analyze", "Analyze a SQL query"),
            ("ask", "Ask questions in natural language"),
            ("init", "First-time setup wizard"),
            ("query", "Manage query registry"),
            ("schema", "Manage semantic layer"),
            ("list", "Show saved queries"),
            ("version", "Show version information"),
            ("report", "Submit feedback or bug reports"),
            ("help", "Show help"),
            ("Exit", "Exit rdst"),
        ]

        # Use UI system components
        from lib.ui import get_console, DataTable, SectionHeader

        console = get_console()

        # Header
        console.print()
        console.print(SectionHeader("rdst", "Readyset Data and SQL Toolkit"))
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
        choice = input("Select option [1]: ").strip()
        if not choice:
            choice_idx = 1
        else:
            try:
                choice_idx = int(choice)
            except ValueError:
                return cli.help()
        if choice_idx < 1 or choice_idx > len(commands):
            return cli.help()
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
            return cli.analyze(query)
        elif cmd == "init":
            return cli.init()
        elif cmd == "query":
            # Query command now has subcommands
            from lib.ui import SelectPrompt, Prompt

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
        elif cmd == "ask":
            return cli.ask()
        elif cmd == "schema":
            return cli.schema()
        elif cmd == "list":
            return cli.query(subcommand="list")
        elif cmd == "version":
            return cli.version()
        elif cmd == "report":
            title = input("Title: ").strip()
            if not title:
                return RdstResult(False, "report requires a title")
            body = input("Body (optional): ").strip()
            return cli.report(title, body=body)
        else:  # help, Exit
            return cli.help()
    except (EOFError, KeyboardInterrupt):
        return cli.help()


def main():
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
                from lib.telemetry import telemetry

                if telemetry.should_show_nps_prompt():
                    telemetry.show_nps_prompt()
            except Exception:
                pass  # Don't fail if NPS prompt fails
        else:
            print(f"Error: {result.message}", file=sys.stderr)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Report crash to telemetry
        try:
            from lib.telemetry import telemetry

            command = (
                args.command
                if "args" in locals() and hasattr(args, "command")
                else "unknown"
            )
            telemetry.report_crash(e, context={"command": command, "source": "main"})
        except Exception:
            pass  # Don't fail if telemetry fails

        if args.verbose if "args" in locals() else False:
            import traceback

            traceback.print_exc()
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Ensure telemetry events are flushed before exit
        try:
            from lib.telemetry import telemetry

            telemetry.flush()
        except Exception:
            pass


if __name__ == "__main__":
    main()
