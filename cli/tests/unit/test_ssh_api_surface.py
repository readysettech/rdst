"""Configure API SSH round-trip, validation, and SSE error tests."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.configure.api import routes
from shared.config.targets import TargetsConfig
from shared.ssh_tunnel import SshPassphraseRequired

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


async def _request(method: str, path: str, json_body=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json_body)


def _target_payload(ssh=None):
    target = {
        "engine": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "app",
        "user": "app",
        "password_env": "APP_PASSWORD",
    }
    if ssh is not None:
        target["ssh"] = ssh
    return {"name": "prod", "target": target}


async def test_configure_add_get_and_list_round_trip_ssh(tmp_rdst_home):
    ssh = {
        "host": "jump.example.com",
        "port": 2222,
        "user": "deployer",
        "key_path": "~/.ssh/jump.pem",
    }

    add = await _request(
        "POST",
        "/api/configure/targets",
        _target_payload(ssh),
    )
    cfg = TargetsConfig()
    cfg.load()
    saved = cfg.get("prod")
    saved["publicly_accessible"] = False
    cfg.upsert("prod", saved)
    cfg.save()
    detail = await _request("GET", "/api/configure/targets/prod")
    listing = await _request("GET", "/api/configure/targets")

    assert add.status_code == 200
    assert add.json()["success"] is True
    assert saved["ssh"] == ssh
    assert detail.json()["ssh"] == {**ssh, "profile": None}
    assert detail.json()["publicly_accessible"] is False
    assert listing.json()["targets"][0]["ssh"] == {**ssh, "profile": None}
    assert listing.json()["targets"][0]["publicly_accessible"] is False


async def test_configure_password_is_stored_under_an_automatic_hidden_name(
    tmp_rdst_home,
    monkeypatch,
):
    stored = []
    monkeypatch.setattr(
        routes.SecretStoreService,
        "set_secret",
        lambda self, name, value, persist=True: stored.append(
            (name, value, persist)
        ),
    )
    payload = _target_payload()
    payload["target"].pop("password_env")
    payload["target"]["password"] = "form-password"

    response = await _request("POST", "/api/configure/targets", payload)

    cfg = TargetsConfig()
    cfg.load()
    assert response.json()["success"] is True
    assert cfg.get("prod")["password_env"] == "RDST_PROD_PASSWORD"
    assert "password" not in cfg.get("prod")
    assert stored == [("RDST_PROD_PASSWORD", "form-password", True)]


async def test_configure_password_update_preserves_legacy_pointer(
    tmp_rdst_home,
    monkeypatch,
):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("prod", _target_payload()["target"])
    cfg.save()
    stored = []
    monkeypatch.setattr(
        routes.SecretStoreService,
        "set_secret",
        lambda self, name, value, persist=True: stored.append(
            (name, value, persist)
        ),
    )
    target = _target_payload()["target"]
    target.pop("password_env")
    target["password"] = "replacement"

    response = await _request(
        "PUT", "/api/configure/targets/prod", {"target": target}
    )

    cfg.load()
    assert response.json()["success"] is True
    assert cfg.get("prod")["password_env"] == "APP_PASSWORD"
    assert stored == [("APP_PASSWORD", "replacement", True)]


async def test_configure_ssh_defaults_port_and_rejects_invalid_shape(tmp_rdst_home):
    defaulted = await _request(
        "POST",
        "/api/configure/targets",
        _target_payload({"host": "jump.example.com"}),
    )
    cfg = TargetsConfig()
    cfg.load()

    assert defaulted.status_code == 200
    assert cfg.get("prod")["ssh"] == {
        "host": "jump.example.com",
        "port": 22,
    }

    invalid_ssh_values = [
        {"host": "jump.example.com", "port": 0},
        {"host": "jump.example.com", "port": 65536},
        {"port": 22},
        {"host": ""},
        {"host": "jump.example.com", "password": "must-not-be-accepted"},
    ]
    for ssh in invalid_ssh_values:
        payload = _target_payload(ssh)
        payload["name"] = "invalid"
        response = await _request("POST", "/api/configure/targets", payload)
        assert response.status_code == 422, ssh


async def test_configure_round_trips_reusable_ssh_profile(tmp_rdst_home):
    response = await _request(
        "POST",
        "/api/configure/targets",
        _target_payload({"profile": "shared-prod"}),
    )
    detail = await _request("GET", "/api/configure/targets/prod")

    assert response.status_code == 200
    config = TargetsConfig()
    config.load()
    assert config.get("prod")["ssh"] == {"profile": "shared-prod"}
    assert detail.json()["ssh"] == {
        "profile": "shared-prod",
        "host": None,
        "port": 22,
        "user": None,
        "key_path": None,
    }


async def test_configure_update_absent_or_empty_ssh_removes_section(
    tmp_rdst_home,
    monkeypatch,
):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            **_target_payload()["target"],
            "ssh": {"host": "jump.example.com", "port": 22},
        },
    )
    cfg.save()
    manager = Mock()
    monkeypatch.setattr("shared.ssh_tunnel.get_tunnel_manager", lambda: manager)

    request = _target_payload()["target"]
    response = await _request(
        "PUT",
        "/api/configure/targets/prod",
        {"target": request},
    )

    cfg.load()
    assert response.json()["success"] is True
    assert "ssh" not in cfg.get("prod")
    manager.close.assert_called_once_with("prod")

    cfg.get("prod")["ssh"] = {"host": "jump.example.com", "port": 22}
    cfg.save()
    manager.reset_mock()
    request["ssh"] = {}
    response = await _request(
        "PUT",
        "/api/configure/targets/prod",
        {"target": request},
    )

    cfg.load()
    assert response.json()["success"] is True
    assert "ssh" not in cfg.get("prod")
    manager.close.assert_called_once_with("prod")


async def test_configure_delete_closes_target_tunnel(tmp_rdst_home, monkeypatch):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            **_target_payload()["target"],
            "ssh": {"host": "jump.example.com", "port": 22},
        },
    )
    cfg.save()
    manager = Mock()
    monkeypatch.setattr("shared.ssh_tunnel.get_tunnel_manager", lambda: manager)

    response = await _request("DELETE", "/api/configure/targets/prod")

    assert response.json()["success"] is True
    manager.close.assert_called_once_with("prod")


async def test_connection_test_sse_emits_categorized_ssh_failure(monkeypatch):
    config = {
        **_target_payload()["target"],
        "password": "not-a-real-secret",
        "ssh": {
            "host": "jump.example.com",
            "key_path": "~/.ssh/jump.pem",
        },
    }
    cfg = Mock()
    cfg.get.return_value = config
    monkeypatch.setattr(
        "features.configure.service.ConfigureService._load_config",
        lambda self: cfg,
    )
    monkeypatch.setattr(
        "features.configure.service.resolve_connection_params",
        Mock(side_effect=SshPassphraseRequired("locked")),
    )

    response = await _request("POST", "/api/configure/targets/prod/test")
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    failed = next(frame for frame in frames if frame.get("status") == "failed")

    assert failed["target_name"] == "prod"
    assert failed["category"] == "ssh_passphrase_required"
    assert "needs a passphrase" in failed["message"]
    assert "ssh-add" in failed["message"]
    assert "Traceback" not in response.text


async def test_form_connection_test_uses_unsaved_ssh_and_keeps_successful_tunnel(
    monkeypatch,
):
    observed = {}
    manager = Mock()

    async def perform_connection_test(
        self, config, *, force_fresh_tunnel=False
    ):
        observed.update(config)
        observed["force_fresh_tunnel"] = force_fresh_tunnel
        return {"success": True, "message": "Connected"}

    monkeypatch.setattr(
        "features.configure.service.ConfigureService.perform_connection_test",
        perform_connection_test,
    )
    monkeypatch.setattr(
        "shared.ssh_tunnel.get_tunnel_manager",
        lambda: manager,
    )

    response = await _request(
        "POST",
        "/api/configure/targets/form-target/test",
        {
            "target": {
                **_target_payload()["target"],
                "password": "form-only-password",
                "ssh": {
                    "host": "new-jump.example.com",
                    "port": 2222,
                    "user": "ec2-user",
                    "key_path": "~/.ssh/new.pem",
                },
            }
        },
    )

    assert response.status_code == 200
    assert observed["name"] == "form-target"
    assert observed["force_fresh_tunnel"] is True
    assert observed["password"] == "form-only-password"
    assert observed["ssh"]["host"] == "new-jump.example.com"
    manager.close.assert_not_called()


async def test_get_payload_can_be_put_back_without_losing_metadata(tmp_rdst_home):
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            **_target_payload()["target"],
            "tags": ["environment=prod"],
            "region": "us-east-2",
            "instance_class": "db.t4g.micro",
            "group": "payments",
        },
    )
    cfg.save()

    detail = await _request("GET", "/api/configure/targets/prod")
    update = await _request(
        "PUT",
        "/api/configure/targets/prod",
        detail.json(),
    )

    cfg.load()
    saved = cfg.get("prod")
    assert update.status_code == 200
    assert update.json()["success"] is True
    assert saved["tags"] == ["environment=prod"]
    assert saved["region"] == "us-east-2"
    assert saved["instance_class"] == "db.t4g.micro"
    assert saved["group"] == "payments"
