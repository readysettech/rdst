"""Required-email behavior for the ``rdst_report`` MCP tool."""

from unittest.mock import patch

from mcp_server import get_tools, handle_tool_call


def _report_tool():
    return next(tool for tool in get_tools() if tool["name"] == "rdst_report")


def test_report_tool_requires_email():
    schema = _report_tool()["inputSchema"]

    assert "email" in schema["required"]


def test_report_tool_forwards_email_to_cli():
    with patch("mcp_server.run_rdst_command") as run_rdst_command:
        handle_tool_call(
            "rdst_report",
            {
                "reason": "Great tool",
                "email": "feedback@example.com",
                "positive": True,
            },
        )

    run_rdst_command.assert_called_once_with(
        [
            "report",
            "--reason",
            "Great tool",
            "--email",
            "feedback@example.com",
            "--positive",
        ]
    )
