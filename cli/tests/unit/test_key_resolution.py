"""Unit tests for Readyset account and Anthropic BYOK resolution."""

from __future__ import annotations

import pytest

import shared.config.targets as targets_mod
import shared.secret_store_service as secret_mod
import shared.account_session as account_session
from shared.llm_manager import key_resolution
from shared.llm_manager.base import LLMError


class _FakeConfig:
    """Minimal stand-in for TargetsConfig used only for the trial block."""

    def __init__(self, trial: dict | None = None) -> None:
        self._data = {"trial": trial} if trial is not None else {}

    def load(self) -> None:
        return None


class _FakeStore:
    """Keyring stand-in returning secrets from an in-memory map."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, name: str) -> str | None:
        return self._secrets.get(name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient keys leaking in from the developer environment."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)
    monkeypatch.setattr(account_session, "access_token", lambda: None)


def _patch_config(monkeypatch, trial: dict | None) -> None:
    monkeypatch.setattr(targets_mod, "TargetsConfig", lambda: _FakeConfig(trial))


def _patch_store(monkeypatch, secrets: dict[str, str]) -> None:
    monkeypatch.setattr(secret_mod, "SecretStoreService", lambda: _FakeStore(secrets))


def test_exhausted_trial_yields_to_present_keyring_key(monkeypatch):
    """The bug fix: exhausted trial + a saved own key -> use the own key."""
    _patch_config(monkeypatch, {"token": "trial-tok", "status": "exhausted"})
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-own"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-own"
    assert resolution.is_trial is False
    # And the resolved own key is exported so LLMManager's own resolve sees it.
    import os

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-own"


def test_exhausted_legacy_trial_without_own_key_requires_login(monkeypatch):
    """Retired trial metadata no longer counts as AI access."""
    _patch_config(monkeypatch, {"token": "trial-tok", "status": "exhausted"})
    _patch_store(monkeypatch, {})

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key()

    assert exc.value.code == "LOGIN_REQUIRED"


def test_env_key_wins_over_everything(monkeypatch):
    """Order 1 unchanged: an env ANTHROPIC_API_KEY is the fastest path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    _patch_config(monkeypatch, {"token": "trial-tok", "status": "exhausted"})
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-own"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-env"
    assert resolution.is_trial is False


def test_active_legacy_trial_is_ignored(monkeypatch):
    monkeypatch.setenv("RDST_KEYSERVICE_URL", "https://trial.example")
    _patch_config(monkeypatch, {"token": "trial-active", "status": "active"})
    _patch_store(monkeypatch, {})

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key()

    assert exc.value.code == "LOGIN_REQUIRED"


def test_keyring_own_key_wins_over_legacy_trial_metadata(monkeypatch):
    _patch_config(monkeypatch, {"token": "trial-active", "status": "active"})
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-own"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-own"
    assert resolution.is_trial is False


def test_keyring_own_key_used_when_no_trial(monkeypatch):
    """Order 4 unchanged: with no trial at all, a keyring own key resolves."""
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-keyring"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-keyring"
    assert resolution.is_trial is False


def test_no_key_or_account_requires_login(monkeypatch):
    """A fresh install is directed to Readyset sign-in."""
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {})

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key()

    assert exc.value.code == "LOGIN_REQUIRED"


def test_account_session_selects_hosted_glm(monkeypatch):
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {})
    monkeypatch.setattr(account_session, "access_token", lambda: "supabase-access")

    resolution = key_resolution.resolve_api_key()

    assert resolution.provider == "readyset"
    assert resolution.api_key == "supabase-access"
    assert resolution.model == "z-ai/glm-5.3-flash"


def test_explicit_claude_never_uses_readyset_account(monkeypatch):
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {})
    monkeypatch.setattr(account_session, "access_token", lambda: "supabase-access")

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key("claude")

    assert exc.value.code == "NO_API_KEY"
