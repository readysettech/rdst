"""
Unit tests for CLI commands.

Tests QueryCommand and TopCommand initialization.

Note: TopCommand functionality has been refactored to use the event-driven
service architecture. See test_top_service.py and test_top_renderer.py for
comprehensive tests of the service and renderer.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_lib_path = Path(__file__).parent.parent.parent / "lib"

# Import query_command module
query_command = _import_module_directly(
    "query_command", _lib_path / "cli" / "query_command.py"
)


# For top.py, we need to handle its relative imports specially
def _import_top_module():
    """Import top.py with mocked relative imports."""
    # Read the source
    top_path = _lib_path / "cli" / "top.py"
    with open(top_path, "r") as f:
        source = f.read()

    # Replace the relative import with a local function
    source = source.replace(
        "from ..query_registry import hash_sql",
        "def hash_sql(sql): return sql[:12] if len(sql) >= 12 else sql.ljust(12, '0')",
    )

    # Create module
    module = types.ModuleType("top")
    module.__file__ = str(top_path)
    sys.modules["top"] = module

    # Execute in module namespace
    exec(compile(source, str(top_path), "exec"), module.__dict__)

    return module


top = _import_top_module()

QueryCommand = query_command.QueryCommand
TopCommand = top.TopCommand


class TestQueryCommand:
    """Tests for QueryCommand class."""

    def test_initialization(self):
        """Test QueryCommand initialization."""
        with patch.object(query_command, "QueryRegistry") as mock_registry_class:
            mock_registry = MagicMock()
            mock_registry_class.return_value = mock_registry

            cmd = QueryCommand()

            assert cmd is not None
            assert cmd.registry == mock_registry


class TestTopCommand:
    """Tests for TopCommand class.

    Note: TopCommand has been refactored to use the event-driven service
    architecture. Core functionality is tested in:
    - test_top_service.py (TopService)
    - test_top_renderer.py (TopRenderer)
    """

    def test_initialization(self):
        """Test TopCommand initialization."""
        cmd = TopCommand()
        assert cmd is not None
        assert cmd.client is None

    def test_initialization_with_client(self):
        """Test TopCommand initialization with client."""
        mock_client = MagicMock()
        cmd = TopCommand(client=mock_client)
        assert cmd.client == mock_client


class TestSubcommandHelpDescriptions:
    """Tests that subcommand --help pages include description text (rdst-2vr.4)."""

    def test_subcommands_have_description(self):
        """Every subcommand's parser should have a description for --help."""
        import argparse
        from lib.cli.parser_data import COMMANDS, build_all_subparsers

        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers()
        parsers = build_all_subparsers(subparsers)

        missing = []
        for cmd_name, cmd_def in COMMANDS.items():
            if not cmd_def.subcommand_defs:
                continue
            for subcmd in cmd_def.subcommand_defs:
                # Get the subcommand's parser via argparse internals
                parent_parser = parsers[cmd_name]
                # Find the subparsers action
                for action in parent_parser._subparsers._actions:
                    if isinstance(action, argparse._SubParsersAction):
                        sub_parser = action.choices.get(subcmd.name)
                        if sub_parser and not sub_parser.description:
                            missing.append(f"{cmd_name} {subcmd.name}")

        assert missing == [], (
            f"Subcommands missing description in --help: {missing}"
        )


class TestInteractiveMenu:
    """Tests for _interactive_menu commands list (rdst-2vr.9)."""

    def test_no_bare_list_in_menu(self):
        """Interactive menu should not have a bare 'list' entry.

        'list' is actually 'query list' — having it standalone in the menu
        is confusing. The 'query' entry already has a submenu with list.
        """
        from rdst import _interactive_menu
        import inspect

        source = inspect.getsource(_interactive_menu)

        # The commands list should not contain a bare ("list", ...) entry
        assert '("list",' not in source, (
            "Interactive menu has bare 'list' entry — should be removed; "
            "'query' submenu already includes list"
        )
