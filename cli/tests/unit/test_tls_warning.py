"""Connections that don't encrypt or don't check the server say so, once, to a person."""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest

from shared import db_connection
from shared.db_connection import resolve_connection_params


def _params(**config):
    return resolve_connection_params(target_config={
        "engine": "postgresql", "host": "db.example.com", "port": 5432, "user": "reader",
        "database": "shop", **config})


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch, tmp_rdst_home):
    """Installs a terminal as stderr; called in the test body, where pytest's capture is done
    swapping stderr."""
    stderr = _Tty()
    monkeypatch.setattr(sys, "argv", ["rdst", "top"])
    monkeypatch.setattr(db_connection, "_tls_warned", set())

    def install():
        monkeypatch.setattr(sys, "stderr", stderr)
        return stderr

    return install


@pytest.mark.parametrize("config, says", [
    ({"tls": False}, "without requiring TLS"),
    ({"engine": "mysql", "port": 3306}, "without requiring TLS"),
    ({"tls": True}, "over TLS without checking"),
])
def test_unencrypted_or_unverified_connections_warn_once_per_target(terminal, config, says):
    terminal = terminal()
    for _ in range(2):
        resolve_connection_params(target="shop", target_config={
            "engine": "postgresql", "host": "db.example.com", "port": 5432, "user": "r",
            "database": "d", **config})
    resolve_connection_params(target="billing", target_config={
        "engine": "postgresql", "host": "db.example.com", "port": 5432, "user": "r",
        "database": "d", **config})

    output = terminal.getvalue()
    assert output.count("Warning: ") == 2
    assert f"shop {says}" in output and f"billing {says}" in output
    assert "tls_verify = true" in output and "tls_ca" in output


@pytest.mark.parametrize("config", [
    {"tls": True, "tls_verify": True}, {"tls_verify": True}, {"host": "localhost"},
    {"host": "127.0.0.1"}, {"host": "::1"},
])
def test_verified_or_local_connections_do_not_warn(terminal, config):
    terminal = terminal()
    _params(**config)
    assert terminal.getvalue() == ""


def test_a_tunnelled_connection_does_not_warn(terminal):
    terminal = terminal()
    with patch("shared.ssh_tunnel.get_tunnel_manager") as manager:
        manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        _params(ssh={"host": "jump.example.com"})
    assert terminal.getvalue() == ""


def test_the_app_wording_names_its_settings():
    from shared.db_connection import unverified_tls_message

    config = {"host": "db.example.com"}
    assert '"Verify TLS certificate"' in unverified_tls_message(config, labels=True, name="shop")
    assert "tls_verify = true" in unverified_tls_message(config, name="shop")
    assert unverified_tls_message({**config, "tls_verify": True}) is None


@pytest.mark.parametrize("argv", [["rdst", "top", "--json"], ["rdst", "_mcp_server"],
                                  ["rdst", "audit", "--output", "json"]])
def test_machine_readable_output_gets_no_warning(terminal, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    terminal = terminal()
    _params(tls=True)
    assert terminal.getvalue() == ""


def test_a_pipe_gets_no_warning(monkeypatch, tmp_rdst_home):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(db_connection, "_tls_warned", set())
    _params(tls=True)
    assert stderr.getvalue() == ""
