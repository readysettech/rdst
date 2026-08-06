"""Dispatch and environment guards for the MCP server.

The tool listing is the contract: anything the server answers for is something
a client can be told to call, whether or not it appears in tools/list.
"""

from __future__ import annotations

import os

import pytest

import mcp_server


@pytest.fixture
def no_subprocess(monkeypatch):
    """Fail loudly if a rejected tool call still reaches the CLI."""
    calls = []

    def spy(args, **kwargs):
        calls.append(args)
        return {"success": True, "stdout": "", "stderr": "", "returncode": 0}

    monkeypatch.setattr(mcp_server, "run_rdst_command", spy)
    return calls


class TestToolDispatch:
    def test_advertised_tools_are_the_allowed_set(self):
        names = mcp_server.advertised_tool_names()

        assert "rdst_analyze" in names
        assert not any(name.startswith("_retired_") for name in names)

    @pytest.mark.parametrize(
        "name",
        [
            "rdst_cache_add",
            "rdst_cache_drop_all",
            "rdst_cache_delete",
            "rdst_cache_deploy",
            "rdst_cache_show",
        ],
    )
    def test_retired_cache_tools_are_refused(self, name, no_subprocess):
        result = mcp_server.handle_tool_call(name, {"target": "prod", "confirm": True})

        assert result["success"] is False
        assert f"Unknown tool: {name}" in result["stderr"]
        assert no_subprocess == []

    def test_unknown_tool_is_refused(self, no_subprocess):
        result = mcp_server.handle_tool_call("rdst_not_a_tool", {})

        assert result["success"] is False
        assert no_subprocess == []

    def test_advertised_tool_still_dispatches(self, no_subprocess):
        result = mcp_server.handle_tool_call("rdst_configure_list", {})

        assert result["success"] is True
        assert no_subprocess == [["configure", "list"]]


class TestSetEnvAllowlist:
    @pytest.mark.parametrize(
        "name",
        ["LD_PRELOAD", "BASH_ENV", "PATH", "PYTHONPATH", "prod_db_password", "TOKENS"],
    )
    def test_non_credential_names_are_refused(self, name):
        before = os.environ.get(name)

        result = mcp_server.handle_tool_call(
            "rdst_set_env", {"name": name, "value": "/tmp/evil.so"}
        )

        assert result["success"] is False
        assert name in result["stderr"]
        assert os.environ.get(name) == before

    @pytest.mark.parametrize(
        "name",
        ["PROD_DB_PASSWORD", "TPCH_PASSWD", "ANTHROPIC_API_KEY", "SLACK_TOKEN", "APP_SECRET"],
    )
    def test_credential_names_are_set(self, name, monkeypatch):
        # setenv first so pytest restores the pre-test value afterwards.
        monkeypatch.setenv(name, "")

        result = mcp_server.handle_tool_call("rdst_set_env", {"name": name, "value": "s3cret"})

        assert result["success"] is True
        assert os.environ[name] == "s3cret"
