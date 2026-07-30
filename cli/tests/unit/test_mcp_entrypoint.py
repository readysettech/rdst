"""The rdst-mcp entrypoint when RDST ships as a single frozen executable.

A frozen build contains one executable, so the installer publishes rdst-mcp
as a link to it and the invoked name decides which entrypoint runs.
"""

from __future__ import annotations

import sys
import types

import pytest

import rdst


@pytest.mark.parametrize(
    "invoked",
    [
        "rdst-mcp",
        "/home/user/.local/bin/rdst-mcp",
        "./rdst-mcp",
        "/opt/rdst/tools/current/rdst/rdst-mcp",
    ],
)
def test_mcp_entrypoint_recognized(monkeypatch, invoked):
    monkeypatch.setattr(sys, "argv", [invoked])
    assert rdst._invoked_as_mcp_server()


@pytest.mark.parametrize(
    "invoked",
    [
        "rdst",
        "/home/user/.local/bin/rdst",
        "rdst.py",
        # A different tool whose name merely starts with the entrypoint name
        # must still run the CLI.
        "rdst-mcp-wrapper",
        "",
    ],
)
def test_cli_entrypoint_not_mistaken_for_mcp(monkeypatch, invoked):
    monkeypatch.setattr(sys, "argv", [invoked])
    assert not rdst._invoked_as_mcp_server()


def test_missing_argv_runs_the_cli(monkeypatch):
    monkeypatch.setattr(sys, "argv", [])
    assert not rdst._invoked_as_mcp_server()


def test_main_dispatches_to_the_mcp_server(monkeypatch):
    calls = []
    stub = types.ModuleType("mcp_server")
    stub.main = lambda: calls.append("mcp")
    monkeypatch.setitem(sys.modules, "mcp_server", stub)
    monkeypatch.setattr(sys, "argv", ["/opt/rdst/bin/rdst-mcp"])

    rdst.main()

    assert calls == ["mcp"]


def test_main_does_not_dispatch_for_the_cli_name(monkeypatch):
    calls = []
    stub = types.ModuleType("mcp_server")
    stub.main = lambda: calls.append("mcp")
    monkeypatch.setitem(sys.modules, "mcp_server", stub)
    # A bare `rdst` with no subcommand would open the interactive menu, so stop
    # at argument parsing: reaching it proves the MCP branch was not taken.
    monkeypatch.setattr(sys, "argv", ["/opt/rdst/bin/rdst", "--not-a-real-flag"])

    with pytest.raises(SystemExit):
        rdst.main()

    assert calls == []
