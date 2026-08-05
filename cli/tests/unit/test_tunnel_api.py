"""In-process tests for the SSH tunnel API."""

from __future__ import annotations

import stat
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from features.tunnel.api import routes
from shared.ssh_tunnel import SshAuthenticationError, SshPassphraseRequired

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


class _FakeManager:
    def __init__(self):
        self.tunnels = [
            {
                "target": "prod",
                "jump_host": "jump.example.com",
                "local_port": 41001,
                "state": "active",
                "last_used": 1234.5,
                "key_path": "/should/not/leak",
            }
        ]
        self.closed: list[str] = []
        self.closed_all = False
        self.ensure_result = ("127.0.0.1", 41002)
        self.ensure_error: Exception | None = None
        self.ensure_calls: list[tuple] = []
        self.ensure_kwargs: list[dict] = []
        self.actions: list[str] = []

    def status(self):
        return list(self.tunnels)

    def close(self, target):
        self.closed.append(target)
        self.actions.append(f"close:{target}")

    def close_all(self):
        self.closed_all = True

    @contextmanager
    def target_lifecycle(self, target):
        self.actions.append(f"lifecycle:{target}")
        yield

    def ensure_tunnel(self, *args, **kwargs):
        self.ensure_calls.append(args)
        self.ensure_kwargs.append(kwargs)
        self.actions.append(f"ensure:{args[0]}")
        if self.ensure_error:
            raise self.ensure_error
        return self.ensure_result


async def _request(method: str, path: str, json=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


def _target_config():
    cfg = {
        "engine": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "app",
        "user": "app",
        "password": "not-a-real-secret",
        "ssh": {
            "host": "jump.example.com",
            "port": 22,
            "user": "deployer",
            "key_path": "~/.ssh/jump.pem",
        },
    }
    fake = type("Config", (), {"load": lambda self: None, "get": lambda self, name: cfg})
    return fake


async def test_status_returns_safe_manager_fields(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)

    response = await _request("GET", "/api/tunnel/status")

    assert response.status_code == 200
    assert response.json() == [
        {
            "target": "prod",
            "jump_host": "jump.example.com",
            "local_port": 41001,
            "state": "active",
            "last_used": 1234.5,
        }
    ]


async def test_ssh_keys_exposes_shared_discovery(monkeypatch):
    monkeypatch.setattr(
        "shared.ssh_keys.discover_ssh_auth_options",
        lambda host: [
            {
                "kind": "host",
                "label": "jump-prod (192.0.2.10)",
                "host": "jump-prod",
                "hostname": "192.0.2.10",
            },
            {
                "kind": "file",
                "label": "Key file: /keys/jump.pem",
                "key_path": "/keys/jump.pem",
            },
            {"kind": "agent", "label": "SSH agent: ssh-ed25519 abc"},
        ],
    )

    response = await _request("GET", "/api/tunnel/ssh-keys?jump_host=jump-prod")

    assert response.status_code == 200
    assert response.json()["options"][0]["host"] == "jump-prod"
    assert response.json()["options"][1]["key_path"] == "/keys/jump.pem"
    assert response.json()["options"][2]["kind"] == "agent"


async def test_file_browser_lists_directories_files_and_parent(tmp_rdst_home):
    home = tmp_rdst_home.parent
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "nested").mkdir()
    (ssh_dir / "id_ed25519").write_text("private key", encoding="utf-8")

    response = await _request("GET", "/api/tunnel/browse?path=.ssh")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(ssh_dir)
    assert body["parent"] == str(home)
    assert body["entries"] == [
        {"name": "nested", "path": str(ssh_dir / "nested"), "is_dir": True},
        {
            "name": "id_ed25519",
            "path": str(ssh_dir / "id_ed25519"),
            "is_dir": False,
        },
    ]


async def test_file_browser_rejects_relative_parent_traversal(tmp_rdst_home):
    outside = tmp_rdst_home.parent.parent / "outside"
    outside.mkdir(exist_ok=True)

    response = await _request("GET", "/api/tunnel/browse?path=../outside")

    assert response.status_code == 400
    assert "home directory" in response.json()["detail"]


async def test_file_browser_accepts_an_explicit_absolute_path_outside_home(
    tmp_rdst_home,
):
    outside = tmp_rdst_home.parent.parent / "explicit-outside"
    outside.mkdir(exist_ok=True)

    response = await _request(
        "GET", f"/api/tunnel/browse?path={outside.as_posix()}"
    )

    assert response.status_code == 200
    assert response.json()["path"] == str(outside)


async def test_file_browser_reports_a_nonexistent_path(tmp_rdst_home):
    response = await _request("GET", "/api/tunnel/browse?path=missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Directory does not exist"


def test_ssh_key_discovery_scans_downloads_shallowly(tmp_rdst_home, monkeypatch):
    from shared.ssh_keys import discover_ssh_auth_options

    home = tmp_rdst_home.parent
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir()
    downloads = home / "Downloads"
    downloads.mkdir()
    (downloads / "jump.pem").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nnot-a-real-test-key\n",
        encoding="utf-8",
    )
    (downloads / "bastion.key").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-test-key\n",
        encoding="utf-8",
    )
    nested = downloads / "nested"
    nested.mkdir()
    (nested / "ignored.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-test-key\n",
        encoding="utf-8",
    )

    class EmptyAgent:
        def get_keys(self):
            return ()

        def close(self):
            pass

    opened_downloads = []
    original_open = Path.open

    def tracked_open(path, *args, **kwargs):
        if path.parent == downloads:
            opened_downloads.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    options = discover_ssh_auth_options(
        "",
        ssh_dir=ssh_dir,
        agent_factory=EmptyAgent,
    )
    downloads_options = [
        option
        for option in options
        if option.get("label", "").startswith("Downloads:")
    ]

    assert {option["label"] for option in downloads_options} == {
        "Downloads: bastion.key",
        "Downloads: jump.pem",
    }
    assert opened_downloads.count(downloads / "bastion.key") == 1
    assert opened_downloads.count(downloads / "jump.pem") == 1
    assert not any("ignored.pem" in option["label"] for option in options)


async def test_import_ssh_key_copies_and_sets_permissions(
    tmp_rdst_home, monkeypatch
):
    source = tmp_rdst_home.parent / "Downloads" / "jump.pem"
    source.parent.mkdir()
    original = b"-----BEGIN RSA PRIVATE KEY-----\nnot-a-real-test-key\n"
    source.write_bytes(original)
    source.chmod(0o644)

    response = await _request(
        "POST",
        "/api/tunnel/ssh-keys/import",
        json={"source_path": str(source)},
    )

    assert response.status_code == 200
    destination = tmp_rdst_home.parent / ".ssh" / "jump.pem"
    assert response.json() == {"key_path": str(destination)}
    assert destination.read_bytes() == original
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert source.read_bytes() == original
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


async def test_close_target_and_all(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)

    target_response = await _request(
        "POST",
        "/api/tunnel/close",
        json={"target": "prod"},
    )
    all_response = await _request("POST", "/api/tunnel/close", json={"all": True})

    assert target_response.json()["ok"] is True
    assert manager.closed == ["prod"]
    assert all_response.json() == {"ok": True, "message": "Closed 1 SSH tunnel(s)."}
    assert manager.closed_all is True
    assert (await _request("POST", "/api/tunnel/close", json={})).status_code == 422
    assert (
        await _request(
            "POST",
            "/api/tunnel/close",
            json={"target": "prod", "all": True},
        )
    ).status_code == 422


async def test_test_opens_tunnel_and_probes_local_endpoint(monkeypatch):
    manager = _FakeManager()
    observed = {}

    def probe(config, connect_timeout):
        observed.update(config)
        observed["connect_timeout"] = connect_timeout
        return {"success": True}

    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", _target_config())
    monkeypatch.setattr("shared.db_connection.probe_target_connection", probe)

    response = await _request("POST", "/api/tunnel/test", json={"target": "prod"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["stage"] == "database"
    assert manager.actions[:2] == ["lifecycle:prod", "ensure:prod"]
    assert manager.ensure_kwargs[0] == {"force_fresh": True}
    assert manager.ensure_calls[0][:4] == (
        "prod",
        _target_config()().get("prod")["ssh"],
        "db.internal",
        5432,
    )
    assert observed["host"] == "127.0.0.1"
    assert observed["port"] == 41002
    assert "ssh" not in observed
    assert observed["connect_timeout"] == 5

    # The successful fresh tunnel remains visible to the Settings chip's data
    # source instead of being torn down immediately after validation.
    status = await _request("GET", "/api/tunnel/status")
    assert status.json()[0]["state"] == "active"


async def test_test_categorizes_typed_tunnel_errors(monkeypatch):
    manager = _FakeManager()
    manager.ensure_error = SshAuthenticationError("rejected")
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", _target_config())

    response = await _request("POST", "/api/tunnel/test", json={"target": "prod"})

    assert response.json()["ok"] is False
    assert response.json()["stage"] == "tunnel"
    assert response.json()["category"] == "ssh_auth_failed"
    assert "jump.example.com" in response.json()["message"]


async def test_test_explains_passphrase_api_behavior(monkeypatch):
    manager = _FakeManager()
    manager.ensure_error = SshPassphraseRequired("locked")
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", _target_config())

    response = await _request("POST", "/api/tunnel/test", json={"target": "prod"})

    body = response.json()
    assert body["category"] == "ssh_passphrase_required"
    assert "needs a passphrase" in body["message"]
    assert "ssh-add" in body["message"]
    assert "rdst tunnel test" not in body["message"]


async def test_test_reports_database_stage_failure(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", _target_config())
    monkeypatch.setattr(
        "shared.db_connection.probe_target_connection",
        lambda *args, **kwargs: {"success": False, "error": "connection refused"},
    )

    response = await _request("POST", "/api/tunnel/test", json={"target": "prod"})

    assert response.json()["ok"] is False
    assert response.json()["stage"] == "database"
    assert response.json()["category"] == "database_connection_failed"


@pytest.mark.parametrize(
    "transient_error",
    [
        "connection reset by peer (errno 104)",
        "software caused connection abort (errno 103)",
    ],
)
async def test_test_retries_one_reset_from_the_fresh_tunnel(
    monkeypatch, transient_error
):
    manager = _FakeManager()
    probe = Mock(
        side_effect=[
            {
                "success": False,
                "error": transient_error,
            },
            {"success": True},
        ]
    )
    monkeypatch.setattr(routes, "_get_tunnel_manager", lambda: manager)
    monkeypatch.setattr("shared.config.targets.TargetsConfig", _target_config())
    monkeypatch.setattr("shared.db_connection.probe_target_connection", probe)
    sleep = Mock()
    monkeypatch.setattr(routes.time, "sleep", sleep)

    response = await _request("POST", "/api/tunnel/test", json={"target": "prod"})

    assert response.json()["ok"] is True
    assert probe.call_count == 2
    sleep.assert_called_once_with(0.25)
