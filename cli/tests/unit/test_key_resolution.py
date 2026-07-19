"""Unit tests for LLM API-key resolution precedence.

Focus: the T17 keyring-precedence fix — a present own key in the OS keyring
must beat a stale ``exhausted`` trial marker in config.toml, instead of
``resolve_api_key()`` raising ``TRIAL_EXHAUSTED`` before it ever checks the
keyring. The resolution is exercised without a real config file or keyring by
stubbing ``TargetsConfig`` and ``SecretStoreService``.
"""

from __future__ import annotations

import pytest

import shared.config.targets as targets_mod
import shared.secret_store_service as secret_mod
from shared.llm_manager import key_resolution
from shared.llm_manager.base import LLMError


class _FakeConfig:
    """Minimal stand-in for TargetsConfig used only for the trial block."""

    def __init__(self, trial: dict | None = None) -> None:
        self._data = {"trial": trial} if trial is not None else {}

    def load(self) -> None:  # noqa: D401 - no-op loader
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


def _patch_config(monkeypatch, trial: dict | None) -> None:
    monkeypatch.setattr(targets_mod, "TargetsConfig", lambda: _FakeConfig(trial))


def _patch_store(monkeypatch, secrets: dict[str, str]) -> None:
    monkeypatch.setattr(
        secret_mod, "SecretStoreService", lambda: _FakeStore(secrets)
    )


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


def test_exhausted_trial_without_own_key_still_raises(monkeypatch):
    """No saved own key -> the exhausted trial still dead-ends, as before."""
    _patch_config(monkeypatch, {"token": "trial-tok", "status": "exhausted"})
    _patch_store(monkeypatch, {})

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key()

    assert exc.value.code == "TRIAL_EXHAUSTED"


def test_env_key_wins_over_everything(monkeypatch):
    """Order 1 unchanged: an env ANTHROPIC_API_KEY is the fastest path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    _patch_config(monkeypatch, {"token": "trial-tok", "status": "exhausted"})
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-own"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-env"
    assert resolution.is_trial is False


def test_active_trial_used_when_no_own_key(monkeypatch):
    """Order 3 unchanged: an active trial token routes through the proxy."""
    _patch_config(monkeypatch, {"token": "trial-active", "status": "active"})
    _patch_store(monkeypatch, {})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "trial-active"
    assert resolution.is_trial is True
    assert resolution.proxy_url is not None


def test_active_trial_wins_over_keyring_own_key(monkeypatch):
    """Intentional precedence cell: an *active* trial beats a keyring own key.

    Only the *exhausted* status defers to the keyring (the T17 fix); while the
    trial is active it is resolved before the keyring is ever probed (order 3
    before order 4). Pinned so a future change can't silently flip it.
    """
    _patch_config(monkeypatch, {"token": "trial-active", "status": "active"})
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-own"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "trial-active"
    assert resolution.is_trial is True


def test_keyring_own_key_used_when_no_trial(monkeypatch):
    """Order 4 unchanged: with no trial at all, a keyring own key resolves."""
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-keyring"})

    resolution = key_resolution.resolve_api_key()

    assert resolution.api_key == "sk-ant-keyring"
    assert resolution.is_trial is False


def test_no_key_anywhere_raises_no_api_key(monkeypatch):
    """Nothing configured -> the NO_API_KEY error, not a crash."""
    _patch_config(monkeypatch, None)
    _patch_store(monkeypatch, {})

    with pytest.raises(LLMError) as exc:
        key_resolution.resolve_api_key()

    assert exc.value.code == "NO_API_KEY"
