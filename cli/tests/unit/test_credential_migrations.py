"""Tests for cleanup of credentials retired by updated RDST clients."""

from __future__ import annotations

import os

import toml

from shared.config.credential_migrations import retire_legacy_trial_credentials
from shared.config.targets import TargetsConfig


class _FakeSecretStore:
    def __init__(self, secrets: dict[str, str]):
        self.secrets = dict(secrets)

    def clear_required(self, names: list[str]) -> dict[str, list[str]]:
        cleared: list[str] = []
        missing: list[str] = []
        for name in names:
            if self.secrets.pop(name, None) is None:
                missing.append(name)
            else:
                cleared.append(name)
        return {"cleared": cleared, "missing": missing, "errors": []}


def test_retirement_removes_only_legacy_trial_state(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """
default = "prod"

[targets.prod]
engine = "postgresql"
host = "db.example.com"

[account]
user_id = "account-1"
status = "active"

[[emails]]
email = "current@example.com"
primary = true
verified = true

[trial]
token = "old-trial-token"
email = "old@example.com"
status = "active"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RDST_TRIAL_TOKEN", "old-trial-token")
    store = _FakeSecretStore({
        "RDST_TRIAL_TOKEN": "old-trial-token",
        "ANTHROPIC_API_KEY": "sk-ant-owned",
    })

    result = retire_legacy_trial_credentials(
        secret_store=store,
        config=TargetsConfig(str(path)),
    )

    assert result == {
        "environment_removed": True,
        "keyring_removed": True,
        "config_removed": True,
        "errors": [],
    }
    assert "RDST_TRIAL_TOKEN" not in os.environ
    assert store.secrets == {"ANTHROPIC_API_KEY": "sk-ant-owned"}
    saved = toml.load(path)
    assert "trial" not in saved
    assert saved["account"] == {"user_id": "account-1", "status": "active"}
    assert saved["targets"]["prod"]["host"] == "db.example.com"
    assert saved["emails"] == [{
        "email": "current@example.com",
        "primary": True,
        "verified": True,
    }]

    second = retire_legacy_trial_credentials(
        secret_store=store,
        config=TargetsConfig(str(path)),
    )
    assert second == {
        "environment_removed": False,
        "keyring_removed": False,
        "config_removed": False,
        "errors": [],
    }


def test_retirement_does_not_create_an_empty_config(tmp_path):
    path = tmp_path / "missing.toml"

    result = retire_legacy_trial_credentials(
        secret_store=_FakeSecretStore({}),
        config=TargetsConfig(str(path)),
    )

    assert result["errors"] == []
    assert not path.exists()


def test_retirement_preserves_a_trial_only_email(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[trial]\ntoken = "old-trial-token"\nemail = "old@example.com"\n',
        encoding="utf-8",
    )

    retire_legacy_trial_credentials(
        secret_store=_FakeSecretStore({}),
        config=TargetsConfig(str(path)),
    )

    saved = toml.load(path)
    assert "trial" not in saved
    assert saved["emails"] == [{
        "email": "old@example.com",
        "primary": True,
        "verified": False,
    }]


def test_retirement_keeps_cleaning_config_when_keyring_fails(tmp_path):
    class _BrokenSecretStore:
        def clear_required(self, _names):
            raise RuntimeError("keyring unavailable")

    path = tmp_path / "config.toml"
    path.write_text('[trial]\ntoken = "old-trial-token"\n', encoding="utf-8")

    result = retire_legacy_trial_credentials(
        secret_store=_BrokenSecretStore(),
        config=TargetsConfig(str(path)),
    )

    assert result["errors"] == ["keyring"]
    assert result["config_removed"] is True
    assert "trial" not in toml.load(path)
