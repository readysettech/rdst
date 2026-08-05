"""Unit tests for target password lock guard."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from shared.api.target_guard import (
    TARGET_PASSWORD_REQUIRED_CODE,
    ensure_target_password,
    resolve_target_config,
)
from shared.password_resolver import PasswordResolution
from shared.ssh_tunnel import SshConnectionError


def _mock_config(
    *,
    default_target: str | None = "prod",
    targets: dict | None = None,
):
    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = default_target
    mapping = targets or {
        "prod": {"engine": "postgresql", "password_env": "PROD_DB_PASSWORD"},
        "staging": {"engine": "postgresql", "password": "direct-secret"},
    }
    cfg.get.side_effect = lambda name: mapping.get(name)
    return cfg


def test_resolve_target_config_uses_explicit_target():
    cfg = _mock_config()
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        target_name, target_config = resolve_target_config("staging")

    assert target_name == "staging"
    assert target_config["engine"] == "postgresql"


def test_resolve_target_config_uses_default_target_when_not_provided():
    cfg = _mock_config(default_target="prod")
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        target_name, _ = resolve_target_config(None)

    assert target_name == "prod"


def test_resolve_target_config_raises_400_when_no_target():
    cfg = _mock_config(default_target=None)
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        with pytest.raises(HTTPException) as exc_info:
            resolve_target_config(None)

    assert exc_info.value.status_code == 400


def test_resolve_target_config_raises_404_when_target_not_found():
    cfg = _mock_config(targets={"prod": {"password": "x"}})
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        with pytest.raises(HTTPException) as exc_info:
            resolve_target_config("staging")

    assert exc_info.value.status_code == 404


def test_ensure_target_password_allows_direct_password():
    cfg = _mock_config()
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        guard = ensure_target_password("staging")

    assert guard.target_name == "staging"
    assert guard.target_engine == "postgresql"


def test_ensure_target_password_allows_password_env(monkeypatch):
    monkeypatch.setenv("PROD_DB_PASSWORD", "from-env")
    cfg = _mock_config()
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        guard = ensure_target_password("prod")

    assert guard.target_name == "prod"
    assert guard.target_engine == "postgresql"


def test_ensure_target_password_raises_423_with_structured_detail(monkeypatch):
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)
    cfg = _mock_config()
    with patch("shared.api.target_guard.TargetsConfig", return_value=cfg):
        with pytest.raises(HTTPException) as exc_info:
            ensure_target_password("prod")

    exc = exc_info.value
    assert exc.status_code == 423
    assert exc.detail["code"] == TARGET_PASSWORD_REQUIRED_CODE
    assert exc.detail["target"] == "prod"
    assert exc.detail["password_env"] == "PROD_DB_PASSWORD"
    assert exc.detail["category"] == "target_password_required"
    assert exc.detail["message"].startswith(
        "No password is available for target 'prod'"
    )
    assert "PROD_DB_PASSWORD" not in exc.detail["message"]


def test_ensure_target_password_allows_keychain_password(monkeypatch):
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)
    cfg = _mock_config()
    with (
        patch("shared.api.target_guard.TargetsConfig", return_value=cfg),
        patch(
            "shared.api.target_guard.resolve_password",
            return_value=PasswordResolution(available=True, source="secure_store"),
        ),
    ):
        guard = ensure_target_password("prod")

    assert guard.target_name == "prod"


def test_ensure_target_password_categorizes_ssh_failure():
    target = {
        "engine": "postgresql",
        "password": "not-a-real-secret",
        "host": "db.internal",
        "port": 5432,
        "ssh": {"host": "jump.example.com", "port": 2222},
    }
    cfg = _mock_config(targets={"prod": target})
    with (
        patch("shared.api.target_guard.TargetsConfig", return_value=cfg),
        patch(
            "shared.db_connection.resolve_connection_params",
            side_effect=SshConnectionError("offline"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        ensure_target_password("prod")

    exc = exc_info.value
    assert exc.status_code == 503
    assert exc.detail["category"] == "ssh_jump_unreachable"
    assert exc.detail["target_name"] == "prod"
    assert "jump.example.com:2222 is unreachable" in exc.detail["message"]
