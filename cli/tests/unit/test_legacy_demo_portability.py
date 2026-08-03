"""Portability regressions for the legacy ``rdst demo`` command."""

from unittest.mock import MagicMock, patch

from features.demo.cli.command import (
    DEMO_DATABASE,
    DEMO_PASSWORD,
    DEMO_PORT,
    DEMO_USER,
    DemoCommand,
)


def test_demo_uses_remote_docker_published_host(monkeypatch):
    monkeypatch.setenv("RDST_DOCKER_REMOTE", "1")
    monkeypatch.setenv("RDST_DOCKER_PUBLISHED_HOST", "192.168.122.1")

    command = DemoCommand()

    assert command._database_url() == (
        f"postgresql://{DEMO_USER}:{DEMO_PASSWORD}"
        f"@192.168.122.1:{DEMO_PORT}/{DEMO_DATABASE}"
    )

    config = MagicMock()
    with patch("features.demo.cli.command.TargetsConfig", return_value=config):
        command._configure_target()

    assert config.upsert.call_args.args[1]["host"] == "192.168.122.1"


def test_demo_defaults_to_local_docker_host(monkeypatch):
    monkeypatch.delenv("RDST_DOCKER_REMOTE", raising=False)
    monkeypatch.delenv("RDST_DOCKER_PUBLISHED_HOST", raising=False)

    command = DemoCommand()

    assert command._connection_host() == "127.0.0.1"

