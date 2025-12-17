#!/usr/bin/env python3
"""
MCP Server Sync Check

Verifies that the MCP server tools match the CLI commands.
This test should run first in CI to catch MCP/CLI drift early.

Exit codes:
  0 - All tools in sync
  1 - Mismatch found (CLI command missing from MCP or vice versa)
"""

import subprocess
import re
import sys
import os

# Commands that are intentionally NOT exposed via MCP
# tune: Not implemented yet
# claude: Meta command for registering with Claude Code
# help: Handled by rdst_help tool differently
# howdoi: CLI-only docs lookup (MCP has its own tool descriptions)
# ask: Requires interactive TTY for best experience
MCP_EXCLUDED_COMMANDS = {
    "tune",      # N/A - not implemented
    "claude",    # N/A - meta command for MCP registration itself
    "help",      # N/A - rdst_help handles this differently
    "howdoi",    # N/A - CLI-only, MCP has its own tool descriptions
    "ask",       # CLI-only - requires interactive TTY for multi-step flow
    # Schema subcommands (handled via rdst_schema tool, some are CLI-only)
    "annotate",  # Schema subcommand - interactive wizard, CLI-only
    "export",    # Schema subcommand - handled by rdst_schema tool
}

# MCP-only tools that don't map directly to CLI commands
# These are helper tools for the MCP integration
MCP_ONLY_TOOLS = {
    "read_config",      # Reads ~/.rdst/config.toml directly
    "set_env",          # Sets env vars in MCP session
    "help",             # Entry point tool, different from CLI help
    "test_connection",  # Maps to 'configure test' subcommand
}

# Mapping of MCP tool suffixes to CLI subcommands
# e.g., rdst_configure_add -> configure add
SUBCOMMAND_MAPPING = {
    "configure": ["add", "list", "remove", "default", "llm"],
    "query": ["add", "list", "delete"],
}


def get_cli_commands():
    """Get CLI commands by parsing rdst.py source file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rdst_path = os.path.join(script_dir, "..", "..", "..", "rdst.py")

    try:
        with open(rdst_path, 'r') as f:
            content = f.read()

        # Find main subparser commands (exclude subcommand parsers like query_parser)
        # Only match: subparsers.add_parser('command_name', ...)
        commands = set()
        for match in re.finditer(r"subparsers\.add_parser\s*\(\s*['\"](\w+)['\"]", content):
            commands.add(match.group(1))

        # Filter out query subcommands that use a different parser variable
        # These are defined with query_subparsers.add_parser, not subparsers.add_parser
        query_subcommands = {'add', 'edit', 'delete', 'rm', 'show', 'list', 'import'}
        commands = commands - query_subcommands

        return commands
    except Exception as e:
        print(f"Error getting CLI commands: {e}", file=sys.stderr)
        return set()


def get_mcp_tools():
    """Get MCP tool names from mcp_server.py."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_path = os.path.join(script_dir, "..", "..", "..", "mcp_server.py")

    try:
        with open(mcp_path, "r") as f:
            content = f.read()

        # Find all tool names: "name": "rdst_xxx"
        tools = re.findall(r'"name":\s*"rdst_([a-z_]+)"', content)
        return set(tools)
    except Exception as e:
        print(f"Error reading MCP tools: {e}", file=sys.stderr)
        return set()


def normalize_mcp_tool(tool_name):
    """
    Convert MCP tool name to CLI command equivalent.

    rdst_analyze -> analyze
    rdst_configure_add -> configure_add (for subcommand checking)
    rdst_top -> top
    """
    return tool_name


def check_sync():
    """Check if MCP tools are in sync with CLI commands."""
    cli_commands = get_cli_commands()
    mcp_tools = get_mcp_tools()

    if not cli_commands:
        print("ERROR: Could not get CLI commands", file=sys.stderr)
        return False

    if not mcp_tools:
        print("ERROR: Could not get MCP tools", file=sys.stderr)
        return False

    print(f"CLI commands found: {sorted(cli_commands)}")
    print(f"MCP tools found: {sorted(mcp_tools)}")
    print()

    errors = []

    # Build expected MCP tools from CLI commands
    expected_mcp_tools = set()

    for cmd in cli_commands:
        if cmd in MCP_EXCLUDED_COMMANDS:
            continue

        # Check if this command has subcommands
        if cmd in SUBCOMMAND_MAPPING:
            for subcmd in SUBCOMMAND_MAPPING[cmd]:
                expected_mcp_tools.add(f"{cmd}_{subcmd}")
        else:
            expected_mcp_tools.add(cmd)

    # Add MCP-only tools to what we expect to find
    actual_mcp_tools = mcp_tools - MCP_ONLY_TOOLS

    # Check for CLI commands missing from MCP
    for expected in expected_mcp_tools:
        if expected not in mcp_tools:
            errors.append(f"CLI command '{expected}' is missing from MCP server")

    # Check for MCP tools that don't match CLI commands
    for tool in actual_mcp_tools:
        # Handle subcommand pattern
        base_cmd = tool.split('_')[0]

        if base_cmd in SUBCOMMAND_MAPPING:
            # This is a subcommand tool, check if it's expected
            if tool not in expected_mcp_tools:
                errors.append(f"MCP tool 'rdst_{tool}' doesn't match a CLI subcommand")
        elif tool not in expected_mcp_tools and tool not in MCP_ONLY_TOOLS:
            errors.append(f"MCP tool 'rdst_{tool}' doesn't match a CLI command")

    # Report results
    if errors:
        print("SYNC ERRORS FOUND:")
        for error in errors:
            print(f"  - {error}")
        print()
        print("To fix:")
        print("  - Add missing MCP tools to mcp_server.py")
        print("  - Or add command to MCP_EXCLUDED_COMMANDS if intentionally excluded")
        print("  - Or add to SUBCOMMAND_MAPPING if it's a subcommand")
        return False

    print("MCP tools are in sync with CLI commands")
    return True


def main():
    print("=" * 60)
    print("MCP Server Sync Check")
    print("=" * 60)
    print()

    success = check_sync()

    print()
    if success:
        print("PASS: MCP server is in sync with CLI")
        sys.exit(0)
    else:
        print("FAIL: MCP server is out of sync with CLI")
        sys.exit(1)


if __name__ == "__main__":
    main()
