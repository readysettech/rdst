from __future__ import annotations

import json
from unittest.mock import Mock
from urllib.error import URLError

from features.tunnel.cli.command import TunnelCommand


class FakeTunnelManager:
    def __init__(self):
        self.tunnels = [
            {
                "target": "prod",
                "jump_host": "jump.example.com",
                "local_port": 49152,
                "state": "active",
                "last_used": 1_700_000_000.0,
            }
        ]
        self.closed = []

    def status(self):
        return list(self.tunnels)

    def close(self, target):
        self.closed.append(target)
        self.tunnels = [
            tunnel for tunnel in self.tunnels if tunnel["target"] != target
        ]

    def close_all(self):
        self.closed.extend(tunnel["target"] for tunnel in self.tunnels)
        self.tunnels = []


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeTunnelApi:
    def __init__(self, tunnels=None):
        self.requests = []
        self.urls = []
        self.tunnels = tunnels if tunnels is not None else [
            {
                "target": "prod",
                "jump_host": "jump.example.com",
                "local_port": 49152,
                "state": "active",
                "last_used": 1_700_000_000.0,
            }
        ]

    def urlopen(self, request, timeout):
        self.urls.append(request.full_url)
        path = request.full_url.split("/api/tunnel", 1)[1]
        payload = json.loads(request.data) if request.data else None
        self.requests.append((request.method, f"/api/tunnel{path}", payload))
        if request.method == "GET":
            return FakeHttpResponse(self.tunnels)
        return FakeHttpResponse({"ok": True, "message": "Closed by web server."})


def test_tunnel_list_renders_web_server_status(monkeypatch):
    console = Mock()
    server = FakeTunnelApi()
    monkeypatch.setattr("features.tunnel.cli.command.urlopen", server.urlopen)

    result = TunnelCommand(console=console, web_port=19001).execute("list")

    assert result.ok
    assert server.requests == [("GET", "/api/tunnel/status", None)]
    console.print.assert_called_once()


def test_tunnel_list_honors_web_port_environment(monkeypatch):
    console = Mock()
    server = FakeTunnelApi()
    monkeypatch.setattr("features.tunnel.cli.command.urlopen", server.urlopen)
    monkeypatch.setenv("RDST_WEB_PORT", "19002")

    result = TunnelCommand(console=console).execute("list")

    assert result.ok
    assert server.requests == [("GET", "/api/tunnel/status", None)]
    assert server.urls == ["http://127.0.0.1:19002/api/tunnel/status"]


def test_tunnel_list_uses_default_web_port(monkeypatch):
    console = Mock()
    server = FakeTunnelApi()
    monkeypatch.setattr("features.tunnel.cli.command.urlopen", server.urlopen)

    result = TunnelCommand(console=console).execute("list")

    assert result.ok
    assert server.urls == ["http://127.0.0.1:8787/api/tunnel/status"]


def test_tunnel_list_without_web_server_explains_cli_lifetime(monkeypatch):
    console = Mock()
    monkeypatch.setattr(
        "features.tunnel.cli.command.urlopen",
        Mock(side_effect=URLError("server unavailable")),
    )

    result = TunnelCommand(console=console).execute("list")

    assert result.ok
    console.print.assert_called_once_with(
        "CLI tunnels only exist during each command; run `rdst web` to hold "
        "long-lived tunnels."
    )


def test_tunnel_close_target_uses_web_server(monkeypatch):
    console = Mock()
    server = FakeTunnelApi()
    monkeypatch.setattr("features.tunnel.cli.command.urlopen", server.urlopen)

    result = TunnelCommand(console=console, web_port=19003).execute(
        "close",
        target="prod",
    )

    assert result.ok
    assert server.requests == [
        ("POST", "/api/tunnel/close", {"all": False, "target": "prod"})
    ]
    console.print.assert_called_once_with("Closed by web server.")


def test_tunnel_close_all_uses_web_server(monkeypatch):
    server = FakeTunnelApi()
    monkeypatch.setattr("features.tunnel.cli.command.urlopen", server.urlopen)

    result = TunnelCommand(console=Mock(), web_port=19004).execute(
        "close",
        close_all=True,
    )

    assert result.ok
    assert server.requests == [
        ("POST", "/api/tunnel/close", {"all": True, "target": None})
    ]


def test_tunnel_test_discards_cached_path_before_testing(monkeypatch):
    manager = FakeTunnelManager()
    observed = {}
    config = Mock()
    config.get.return_value = {
        "engine": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "app",
        "user": "app",
        "password": "not-a-real-secret",
        "ssh": {"host": "jump.example.com"},
    }
    monkeypatch.setattr(
        "shared.db_connection.resolve_connection_params",
        lambda **kwargs: {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 41001,
            "database": "app",
            "user": "app",
            "password": "not-a-real-secret",
            "tls": True,
            "tls_verify": True,
            "tls_ca": "/certs/root.pem",
        },
    )

    def probe(probe_config, connect_timeout):
        observed.update(probe_config)
        return {"success": True}

    monkeypatch.setattr("shared.db_connection.probe_target_connection", probe)

    result = TunnelCommand(
        manager=manager,
        config=config,
        console=Mock(),
    ).execute("test", target="prod")

    assert result.ok
    assert manager.closed == ["prod"]
    assert observed["tls"] is True
    assert observed["tls_verify"] is True
    assert observed["tls_ca"] == "/certs/root.pem"
