#!/usr/bin/env python3
"""
Documentation Sync Check

Verifies that the embedded help docs match the CLI commands.
This test should run in CI to catch documentation drift early.

Exit codes:
  0 - All documented commands match CLI
  1 - Mismatch found (CLI command missing from docs or vice versa)
"""

import subprocess
import re
import sys
import os

# Commands that are intentionally NOT documented in the help docs
# These are either internal, meta commands, or subcommands
DOCS_EXCLUDED_COMMANDS = {
    "help",      # Meta command (also handles questions via rdst help "question")
    "version",   # Simple utility command
    "claude",    # MCP registration - specialized
    "run",       # Internal command for running arbitrary SQL
    "guard",     # CLI-only - interactive guard management
    "slack",     # CLI-only - requires running bot process
    "web",       # CLI-only - starts local web server
    "agent",     # Documented via MCP, not end-user help docs
    # Query subcommands (documented under "rdst query" section)
    "add",
    "edit",
    "delete",
    "rm",
    "show",
    "list",
    "import",
    # Schema subcommands (documented under "rdst schema" section)
    "annotate",  # Schema subcommand
    "export",    # Schema subcommand
    "init",      # Schema subcommand (also exists as standalone but documented separately)
}

# Commands documented under different names or as part of other commands
DOCS_ALIASES = {}


def get_cli_commands():
    """Get CLI commands from parser_data.py COMMAND_ORDER."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser_data_path = os.path.join(
        script_dir, "..", "..", "..", "lib", "cli", "parser_data.py"
    )

    try:
        with open(parser_data_path, "r") as f:
            content = f.read()

        # Find COMMAND_ORDER list which defines all top-level commands
        match = re.search(r"COMMAND_ORDER\s*[=:]\s*\[([^\]]+)\]", content)
        if not match:
            print("Could not find COMMAND_ORDER in parser_data.py", file=sys.stderr)
            return set()

        # Extract command names from the list
        commands = set()
        for cmd_match in re.finditer(r'["\'](\w+)["\']', match.group(1)):
            commands.add(cmd_match.group(1))

        return commands
    except Exception as e:
        print(f"Error getting CLI commands: {e}", file=sys.stderr)
        return set()


def get_documented_commands():
    """Get commands documented in RDST_DOCS from the help command module."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    help_docs_path = os.path.join(script_dir, "..", "..", "..", "lib", "cli", "help_command.py")

    try:
        with open(help_docs_path, "r") as f:
            content = f.read()

        # Extract RDST_DOCS string
        docs_match = re.search(r'RDST_DOCS\s*=\s*"""(.+?)"""', content, re.DOTALL)
        if not docs_match:
            print("Could not find RDST_DOCS in help_command.py", file=sys.stderr)
            return set()

        docs = docs_match.group(1)

        # Find all documented commands (### rdst <command>)
        commands = set()
        for match in re.finditer(r'###\s+rdst\s+(\w+)', docs):
            commands.add(match.group(1))

        # Also look for commands mentioned in code blocks (rdst <command>)
        for match in re.finditer(r'^rdst\s+(\w+)', docs, re.MULTILINE):
            cmd = match.group(1)
            # Skip if it's a variable placeholder
            if not cmd.startswith('$') and cmd not in ('--', '-'):
                commands.add(cmd)

        return commands
    except Exception as e:
        print(f"Error reading documented commands: {e}", file=sys.stderr)
        return set()


def check_sync():
    """Check if docs are in sync with CLI commands."""
    cli_commands = get_cli_commands()
    doc_commands = get_documented_commands()

    if not cli_commands:
        print("ERROR: Could not get CLI commands", file=sys.stderr)
        return False

    if not doc_commands:
        print("ERROR: Could not get documented commands", file=sys.stderr)
        return False

    print(f"CLI commands found: {sorted(cli_commands)}")
    print(f"Documented commands found: {sorted(doc_commands)}")
    print()

    errors = []

    # Check for CLI commands missing from docs
    for cmd in cli_commands:
        if cmd in DOCS_EXCLUDED_COMMANDS:
            continue
        if cmd not in doc_commands and cmd not in DOCS_ALIASES:
            errors.append(f"CLI command '{cmd}' is missing from help docs (RDST_DOCS)")

    # Check for documented commands that don't exist in CLI
    # (This catches stale documentation)
    for cmd in doc_commands:
        if cmd not in cli_commands and cmd not in DOCS_EXCLUDED_COMMANDS:
            # Check if it might be a subcommand reference (like "configure add")
            base_cmd = cmd.split()[0] if ' ' in cmd else cmd
            if base_cmd not in cli_commands:
                errors.append(f"Documented command 'rdst {cmd}' doesn't exist in CLI (stale docs?)")

    # Report results
    if errors:
        print("DOCUMENTATION SYNC ERRORS FOUND:")
        for error in errors:
            print(f"  - {error}")
        print()
        print("To fix:")
        print("  - Add missing commands to RDST_DOCS in lib/cli/help_command.py")
        print("  - Or add command to DOCS_EXCLUDED_COMMANDS if intentionally excluded")
        print("  - Or remove stale documentation for removed commands")
        return False

    print("Documentation is in sync with CLI commands")
    return True


def main():
    print("=" * 60)
    print("Documentation Sync Check")
    print("=" * 60)
    print()

    success = check_sync()

    print()
    if success:
        print("PASS: Documentation is in sync with CLI")
        sys.exit(0)
    else:
        print("FAIL: Documentation is out of sync with CLI")
        sys.exit(1)


if __name__ == "__main__":
    main()
