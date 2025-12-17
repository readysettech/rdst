#!/usr/bin/env python3
"""
Documentation Sync Check

Verifies that the howdoi embedded docs match the CLI commands.
This test should run in CI to catch documentation drift early.

Exit codes:
  0 - All documented commands match CLI
  1 - Mismatch found (CLI command missing from docs or vice versa)
"""

import subprocess
import re
import sys
import os

# Commands that are intentionally NOT documented in howdoi
# These are either internal, meta commands, or subcommands
DOCS_EXCLUDED_COMMANDS = {
    "tune",      # Not implemented yet
    "help",      # Meta command
    "howdoi",    # Self-referential - the howdoi command itself
    "version",   # Simple utility command
    "claude",    # MCP registration - specialized
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
    """Get CLI commands by importing rdst and checking subparsers."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rdst_dir = os.path.join(script_dir, "..", "..", "..")

    # Add rdst directory to path
    sys.path.insert(0, rdst_dir)

    try:
        # Import and call parse_arguments to get the parser
        # We need to temporarily modify sys.argv to avoid parsing actual args
        old_argv = sys.argv
        sys.argv = ['rdst']

        # Import fresh to get the subparsers
        import importlib
        if 'rdst' in sys.modules:
            importlib.reload(sys.modules['rdst'])

        # Read the source file and extract commands from subparsers.add_parser calls
        rdst_path = os.path.join(rdst_dir, "rdst.py")
        with open(rdst_path, 'r') as f:
            content = f.read()

        # Find all subparsers.add_parser('command_name', ...) calls
        commands = set()
        for match in re.finditer(r"subparsers\.add_parser\s*\(\s*['\"](\w+)['\"]", content):
            commands.add(match.group(1))

        sys.argv = old_argv
        return commands

    except Exception as e:
        print(f"Error getting CLI commands: {e}", file=sys.stderr)
        return set()
    finally:
        sys.path.pop(0) if rdst_dir in sys.path else None


def get_documented_commands():
    """Get commands documented in RDST_DOCS from howdoi_command.py."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    howdoi_path = os.path.join(script_dir, "..", "..", "..", "lib", "cli", "howdoi_command.py")

    try:
        with open(howdoi_path, "r") as f:
            content = f.read()

        # Extract RDST_DOCS string
        docs_match = re.search(r'RDST_DOCS\s*=\s*"""(.+?)"""', content, re.DOTALL)
        if not docs_match:
            print("Could not find RDST_DOCS in howdoi_command.py", file=sys.stderr)
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
            errors.append(f"CLI command '{cmd}' is missing from howdoi docs (RDST_DOCS)")

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
        print("  - Add missing commands to RDST_DOCS in lib/cli/howdoi_command.py")
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
