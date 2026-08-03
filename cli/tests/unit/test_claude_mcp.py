"""
Unit tests for the claude MCP command in rdst.py.

Verifies that shutil and subprocess are properly imported and used in the
claude command handler, and that the command returns expected results.
"""

from __future__ import annotations

import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch


class TestClaudeMcpImports:
    """Tests that shutil and subprocess are available in the claude command code path."""

    def test_shutil_importable(self):
        """shutil is a stdlib module and must be importable."""
        import shutil
        assert shutil is not None

    def test_subprocess_importable(self):
        """subprocess is a stdlib module and must be importable."""
        import subprocess
        assert subprocess is not None

    def test_shutil_which_available(self):
        """shutil.which is the function used by the claude command handler."""
        import shutil
        assert callable(shutil.which)

    def test_subprocess_run_available(self):
        """subprocess.run is the function used by the claude command handler."""
        import subprocess
        assert callable(subprocess.run)


class TestClaudeCommandHandlerShutil:
    """Tests that shutil.which calls inside the claude command work correctly."""

    def _run_claude_command(self, args_override=None, which_side_effect=None):
        """
        Execute the 'claude' branch of execute_command with mocked dependencies.

        Returns the RdstResult from execute_command.
        """
        import argparse
        # Defer rdst.py import to avoid heavy side effects at collection time
        import importlib
        import sys as _sys

        # Build a minimal args namespace
        args = argparse.Namespace(command="claude", action="add", config=None)
        if args_override:
            for k, v in args_override.items():
                setattr(args, k, v)

        # shutil.which is called inside the 'claude' block - patch it
        def _default_which(name):
            return {
                "claude": "/usr/local/bin/claude",
                "rdst-mcp": None,
                "uv": None,
            }.get(name)

        which_fn = which_side_effect or _default_which

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("shutil.which", side_effect=which_fn):
            with patch("subprocess.run", return_value=mock_result):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        # Import execute_command from rdst.py
                        import rdst as rdst_module
                        from shared.cli import RdstCLI
                        cli = RdstCLI()
                        result = rdst_module.execute_command(cli, args)

        return result

    def test_claude_not_found_returns_failure(self):
        """When 'claude' is not in PATH, command returns a failure result."""
        def _which_no_claude(name):
            return None  # Nothing found

        result = self._run_claude_command(which_side_effect=_which_no_claude)
        assert result.ok is False
        assert "Claude Code CLI not found" in result.message

    def test_claude_found_add_action_succeeds(self):
        """When 'claude' is in PATH and action is 'add', command returns success."""
        result = self._run_claude_command()
        assert result.ok is True
        assert "RDST registered with Claude Code" in result.message

    def test_claude_found_uses_current_python_when_no_uv(self):
        """When uv is absent, register the current Python interpreter."""
        def _which_no_uv(name):
            return {
                "claude": "/usr/local/bin/claude",
                "rdst-mcp": None,
                "uv": None,
            }.get(name)

        called_with = {}

        def _capture_run(cmd, **kwargs):
            called_with["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        import argparse
        args = argparse.Namespace(command="claude", action="add", config=None)

        with patch("shutil.which", side_effect=_which_no_uv):
            with patch("subprocess.run", side_effect=_capture_run):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        with patch("os.path.exists", return_value=True):
                            import rdst as rdst_module
                            from shared.cli import RdstCLI
                            cli = RdstCLI()
                            result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        import sys

        separator = called_with["cmd"].index("--")
        assert called_with["cmd"][separator + 1] == sys.executable

    def test_claude_found_uses_uv_when_available(self):
        """When 'uv' is present, uses uv run for mcp_server.py."""
        def _which_with_uv(name):
            return {
                "claude": "/usr/local/bin/claude",
                "rdst-mcp": None,
                "uv": "/usr/local/bin/uv",
            }.get(name)

        called_with = {}

        def _capture_run(cmd, **kwargs):
            called_with["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        import argparse
        args = argparse.Namespace(command="claude", action="add", config=None)

        with patch("shutil.which", side_effect=_which_with_uv):
            with patch("subprocess.run", side_effect=_capture_run):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        with patch("os.path.exists", return_value=True):
                            import rdst as rdst_module
                            from shared.cli import RdstCLI
                            cli = RdstCLI()
                            result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        separator = called_with["cmd"].index("--")
        assert called_with["cmd"][separator + 1] == "/usr/local/bin/uv"

    def test_frozen_cli_registers_its_embedded_mcp_mode(self):
        called_with = {}

        def _capture_run(cmd, **kwargs):
            called_with["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        import argparse
        import rdst as rdst_module
        from shared.cli import RdstCLI

        args = argparse.Namespace(command="claude", action="add", config=None)
        with patch("shutil.which", side_effect=lambda name: f"/bin/{name}"):
            with patch("subprocess.run", side_effect=_capture_run):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        with patch.object(sys, "frozen", True, create=True):
                            result = rdst_module.execute_command(RdstCLI(), args)

        assert result.ok is True
        separator = called_with["cmd"].index("--")
        assert called_with["cmd"][separator + 1:] == [
            sys.executable,
            "_mcp_server",
        ]

    def test_claude_rdst_mcp_in_path(self):
        """When 'rdst-mcp' is installed, it is used directly."""
        def _which_with_rdst_mcp(name):
            return {
                "claude": "/usr/local/bin/claude",
                "rdst-mcp": "/usr/local/bin/rdst-mcp",
                "uv": None,
            }.get(name)

        called_with = {}

        def _capture_run(cmd, **kwargs):
            called_with["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        import argparse
        args = argparse.Namespace(command="claude", action="add", config=None)

        with patch("shutil.which", side_effect=_which_with_rdst_mcp):
            with patch("subprocess.run", side_effect=_capture_run):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        import rdst as rdst_module
                        from shared.cli import RdstCLI
                        cli = RdstCLI()
                        result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        separator = called_with["cmd"].index("--")
        assert called_with["cmd"][separator + 1] == "/usr/local/bin/rdst-mcp"

    def test_claude_remove_action(self):
        """The 'remove' action calls subprocess.run with 'mcp remove'."""
        called_with = {}

        def _capture_run(cmd, **kwargs):
            called_with["cmd"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        import argparse
        args = argparse.Namespace(command="claude", action="remove", config=None)

        with patch("shutil.which", return_value=r"C:\Users\faruk\bin\claude.cmd"):
            with patch("subprocess.run", side_effect=_capture_run):
                with patch("os.path.exists", return_value=False):
                    import rdst as rdst_module
                    from shared.cli import RdstCLI
                    cli = RdstCLI()
                    result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        assert called_with["cmd"] == [
            r"C:\Users\faruk\bin\claude.cmd",
            "mcp",
            "remove",
            "rdst",
        ]

    def test_subprocess_run_failure_returns_error(self):
        """When subprocess.run returns non-zero, the result is a failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error occurred"

        import argparse
        args = argparse.Namespace(command="claude", action="add", config=None)

        def _which_claude_only(name):
            return "/usr/local/bin/claude" if name == "claude" else None

        with patch("shutil.which", side_effect=_which_claude_only):
            with patch("subprocess.run", return_value=mock_result):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        with patch("os.path.exists", return_value=True):
                            import rdst as rdst_module
                            from shared.cli import RdstCLI
                            cli = RdstCLI()
                            result = rdst_module.execute_command(cli, args)

        assert result.ok is False
        assert "some error occurred" in result.message

    def test_already_registered_treated_as_success(self):
        """When stderr says 'already exists', the result is treated as success."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "MCP server already exists"

        import argparse
        args = argparse.Namespace(command="claude", action="add", config=None)

        def _which_claude_only(name):
            return "/usr/local/bin/claude" if name == "claude" else None

        with patch("shutil.which", side_effect=_which_claude_only):
            with patch("subprocess.run", return_value=mock_result):
                with patch("os.makedirs"):
                    with patch("builtins.open", mock.mock_open()):
                        with patch("os.path.exists", return_value=True):
                            import rdst as rdst_module
                            from shared.cli import RdstCLI
                            cli = RdstCLI()
                            result = rdst_module.execute_command(cli, args)

        assert result.ok is True
        assert "already registered" in result.message

    def test_unknown_action_returns_failure(self):
        """An unrecognized action value returns a failure result."""
        import argparse
        args = argparse.Namespace(command="claude", action="frobnicate", config=None)

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            import rdst as rdst_module
            from shared.cli import RdstCLI
            cli = RdstCLI()
            result = rdst_module.execute_command(cli, args)

        assert result.ok is False
        assert "frobnicate" in result.message
