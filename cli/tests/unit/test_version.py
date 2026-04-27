"""
Unit tests for version reporting in rdst.

Verifies both the `version` subcommand and the `--version` flag.
"""

from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

import pytest


class TestVersionSubcommand:
    """Tests for `rdst version` subcommand via RdstCLI.version()."""

    def test_version_returns_ok(self):
        """version() returns a successful RdstResult."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        assert result.ok is True

    def test_version_message_contains_rdst(self):
        """version() message mentions rdst."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        assert "rdst" in result.message.lower()

    def test_version_message_contains_readyset(self):
        """version() message mentions Readyset."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        assert "Readyset" in result.message

    def test_version_message_format(self):
        """version() message matches expected prefix format."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        assert result.message.startswith(
            "Readyset Data and SQL Toolkit (rdst) version "
        )

    def test_version_message_has_version_string(self):
        """version() message ends with a non-empty version string."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        prefix = "Readyset Data and SQL Toolkit (rdst) version "
        version_part = result.message[len(prefix):]
        assert len(version_part) > 0

    def test_version_subcommand_via_execute_command(self):
        """execute_command routes 'version' to cli.version()."""
        import rdst as rdst_module
        from shared.cli import RdstCLI

        args = argparse.Namespace(command="version", config=None)
        cli = RdstCLI()
        result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        assert "Readyset Data and SQL Toolkit (rdst) version" in result.message

    def test_version_fallback_when_metadata_unavailable(self):
        """version() gracefully falls back to 'unknown' when metadata missing."""
        from shared.cli.rdst_cli import RdstCLI

        def _raise(pkg):
            raise Exception("no metadata")

        with patch(
            "shared.cli.rdst_cli.get_version",
            side_effect=_raise,
            create=True,
        ):
            # Patch importlib.metadata.version inside rdst_cli's local scope
            with patch("importlib.metadata.version", side_effect=_raise):
                # Also block _version fallback
                with patch.dict(sys.modules, {"_version": None}):
                    cli = RdstCLI()
                    result = cli.version()

        assert result.ok is True
        # Message should still be well-formed
        assert "Readyset Data and SQL Toolkit (rdst) version" in result.message


class TestVersionFlag:
    """Tests for `rdst --version` top-level flag."""

    def test_parse_arguments_has_version_action(self):
        """The top-level parser must have a --version argument registered."""
        import rdst as rdst_module

        # Intercept parse_args to avoid reading sys.argv
        with patch.object(argparse.ArgumentParser, "parse_args", return_value=argparse.Namespace(command=None)):
            # We can't easily inspect without triggering, so rebuild the parser manually
            pass

        with patch("sys.argv", ["rdst"]):
            captured_parser = {}

            def _intercept_parse_args(self, *_a, **_kw):
                captured_parser["parser"] = self
                return argparse.Namespace(command=None)

            with patch.object(argparse.ArgumentParser, "parse_args", _intercept_parse_args):
                rdst_module.parse_arguments()

            parser = captured_parser["parser"]
            option_strings = {
                opt
                for action in parser._actions
                for opt in action.option_strings
            }
            assert "--version" in option_strings

    def test_version_flag_outputs_version_string(self, capsys):
        """--version prints the version string and exits with code 0."""
        import rdst as rdst_module

        with patch("sys.argv", ["rdst", "--version"]):
            try:
                rdst_module.parse_arguments()
            except SystemExit as exc:
                assert exc.code == 0

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "Readyset Data and SQL Toolkit (rdst) version" in output

    def test_version_flag_exits(self):
        """--version causes argparse to call sys.exit(0)."""
        import rdst as rdst_module

        with patch("sys.argv", ["rdst", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                rdst_module.parse_arguments()

        assert exc_info.value.code == 0

    def test_version_flag_version_matches_subcommand(self):
        """--version and `version` subcommand report the same version string."""
        import rdst as rdst_module
        from shared.cli import RdstCLI

        # Get version from subcommand
        cli = RdstCLI()
        args = argparse.Namespace(command="version", config=None)
        subcommand_result = rdst_module.execute_command(cli, args)
        subcommand_version = subcommand_result.message

        import io

        with patch("sys.argv", ["rdst", "--version"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                    try:
                        rdst_module.parse_arguments()
                    except SystemExit:
                        pass
                    flag_output = mock_stdout.getvalue() + mock_stderr.getvalue()

        # Both should contain the same version string
        assert "Readyset Data and SQL Toolkit (rdst) version" in flag_output
        assert "Readyset Data and SQL Toolkit (rdst) version" in subcommand_version

        # Extract version numbers and compare
        prefix = "Readyset Data and SQL Toolkit (rdst) version "
        flag_version = flag_output.strip().replace(prefix, "")
        sub_version = subcommand_version.replace(prefix, "")
        assert flag_version == sub_version


class TestVersionConsistency:
    """Tests that version is reported consistently across code paths."""

    def test_version_string_is_non_empty(self):
        """Version string is always non-empty (never blank)."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        prefix = "Readyset Data and SQL Toolkit (rdst) version "
        version_str = result.message[len(prefix):]
        assert version_str.strip() != ""

    def test_version_string_not_none_literal(self):
        """Version string should not be the literal string 'None'."""
        from shared.cli import RdstCLI
        cli = RdstCLI()
        result = cli.version()
        assert "None" not in result.message

    def test_version_subcommand_in_parser(self):
        """The 'version' subcommand is registered in the argument parser."""
        import rdst as rdst_module

        captured_parser = {}

        def _intercept_parse_args(self, *_a, **_kw):
            captured_parser["parser"] = self
            return argparse.Namespace(command=None)

        with patch("sys.argv", ["rdst"]):
            with patch.object(argparse.ArgumentParser, "parse_args", _intercept_parse_args):
                rdst_module.parse_arguments()

        parser = captured_parser["parser"]
        subcommand_names = set()
        for action in parser._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                subcommand_names.update(action.choices.keys())

        assert "version" in subcommand_names
