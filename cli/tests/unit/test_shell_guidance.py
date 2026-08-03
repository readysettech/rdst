"""Platform-specific command guidance tests."""

from __future__ import annotations

from unittest.mock import patch

from features.analyze.cli.output_formatter import _generate_db_test_command
from mcp_server import _installation_guidance
from shared.cli.help_command import HelpCommand
from shared.shell import adapt_shell_guidance, environment_assignment


def test_environment_assignment_uses_powershell_on_windows():
    assert (
        environment_assignment("PROD_DB_PASSWORD", "your-password", windows=True)
        == '$env:PROD_DB_PASSWORD = "your-password"'
    )


def test_environment_assignment_uses_export_on_posix():
    assert (
        environment_assignment("PROD_DB_PASSWORD", "your-password", windows=False)
        == 'export PROD_DB_PASSWORD="your-password"'
    )


def test_embedded_guidance_uses_powershell_assignments_on_windows():
    guidance = adapt_shell_guidance(
        'Set it: export API_KEY="secret" and open $EDITOR',
        windows=True,
    )

    assert guidance == 'Set it: $env:API_KEY = "secret" and open EDITOR'


def test_mcp_installation_guidance_is_windows_specific():
    guidance = _installation_guidance(windows=True)

    assert "uv sync --group dev" in guidance
    assert "install.sh" not in guidance
    assert "rdst update" not in guidance


def test_fallback_help_uses_powershell_assignments_on_windows():
    command = HelpCommand.__new__(HelpCommand)

    with patch(
        "shared.cli.help_command.adapt_shell_guidance",
        side_effect=lambda text: adapt_shell_guidance(text, windows=True),
    ):
        result = command._fallback_search("configure a target", "unavailable")

    assert '$env:ENV_VAR = "your-password"' in result.answer
    assert "export ENV_VAR" not in result.answer


def test_postgres_test_command_uses_powershell_syntax_on_windows():
    command = _generate_db_test_command(
        "SELECT * FROM users WHERE name = 'Ada'",
        {
            "host": "db.example.com",
            "port": 5432,
            "user": "app",
            "database": "prod",
            "password_env": "PROD_DB_PASSWORD",
        },
        "postgresql",
        windows=True,
    )

    assert command.startswith("$env:PGPASSWORD = $env:PROD_DB_PASSWORD; psql ")
    assert "name = ''Ada''" in command
    assert "export " not in command
