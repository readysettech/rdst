"""Secure Slack credential persistence tests."""

from __future__ import annotations

import json

import pytest
from features.slack import config as slack_config
from features.slack.config import SlackCredentials


class FakeSecretStore:
    def __init__(self, available=True):
        self.available = available
        self.values = {}

    def is_available(self):
        return self.available

    def set_secret(self, name, value, persist=True, apply_to_environment=True):
        assert persist is True
        assert apply_to_environment is False
        if not self.available:
            return {"persisted": False}
        self.values[name] = value
        return {"persisted": True}

    def get_secret(self, name):
        return self.values.get(name)


def test_slack_tokens_are_stored_in_keyring_not_json(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    store = FakeSecretStore()
    monkeypatch.setattr(slack_config, "credentials_file", lambda: credentials_path)
    monkeypatch.setattr(slack_config, "ensure_slack_dirs", lambda: None)
    monkeypatch.setattr(slack_config, "_secret_store", lambda: store)
    monkeypatch.setattr(slack_config, "_credential_version", lambda: "v1")

    slack_config.save_credentials(
        SlackCredentials(
            workspace_id="T123",
            bot_token="xoxb-secret",
            app_token="xapp-secret",
            workspace_name="Readyset",
        )
    )

    persisted = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert "bot_token" not in persisted["T123"]
    assert "app_token" not in persisted["T123"]
    assert persisted["T123"]["secret_version"] == "v1"
    assert store.values == {
        "T123:v1:bot-token": "xoxb-secret",
        "T123:v1:app-token": "xapp-secret",
    }

    loaded = slack_config.load_credentials("T123")
    assert loaded["T123"].bot_token == "xoxb-secret"
    assert loaded["T123"].app_token == "xapp-secret"


def test_failed_token_pair_does_not_publish_partial_credentials(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text(
        json.dumps({"T123": {"secret_version": "v0"}}),
        encoding="utf-8",
    )
    store = FakeSecretStore()
    store.values = {
        "T123:v0:bot-token": "old-bot",
        "T123:v0:app-token": "old-app",
    }
    original_set = store.set_secret

    def fail_app(name, value, persist=True, apply_to_environment=True):
        if name == "T123:v1:app-token":
            return {"persisted": False}
        return original_set(name, value, persist, apply_to_environment)

    store.set_secret = fail_app
    monkeypatch.setattr(slack_config, "credentials_file", lambda: credentials_path)
    monkeypatch.setattr(slack_config, "ensure_slack_dirs", lambda: None)
    monkeypatch.setattr(slack_config, "_secret_store", lambda: store)
    monkeypatch.setattr(slack_config, "_credential_version", lambda: "v1")

    with pytest.raises(RuntimeError, match="secure keyring"):
        slack_config.save_credentials(SlackCredentials("T123", "new-bot", "new-app"))

    persisted = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert persisted["T123"]["secret_version"] == "v0"
    loaded = slack_config.load_credentials("T123")["T123"]
    assert (loaded.bot_token, loaded.app_token) == ("old-bot", "old-app")


def test_slack_save_requires_secure_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(
        slack_config, "credentials_file", lambda: tmp_path / "credentials.json"
    )
    monkeypatch.setattr(slack_config, "ensure_slack_dirs", lambda: None)
    monkeypatch.setattr(
        slack_config, "_secret_store", lambda: FakeSecretStore(available=False)
    )

    with pytest.raises(RuntimeError, match="secure keyring"):
        slack_config.save_credentials(
            SlackCredentials("T123", "xoxb-secret", "xapp-secret")
        )


def test_load_migrates_plaintext_tokens_to_keyring(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text(
        json.dumps(
            {
                "T123": {
                    "workspace_name": "Readyset",
                    "bot_token": "xoxb-legacy",
                    "app_token": "xapp-legacy",
                }
            }
        ),
        encoding="utf-8",
    )
    store = FakeSecretStore()
    monkeypatch.setattr(slack_config, "credentials_file", lambda: credentials_path)
    monkeypatch.setattr(slack_config, "_secret_store", lambda: store)
    monkeypatch.setattr(slack_config, "_credential_version", lambda: "v1")

    loaded = slack_config.load_credentials("T123")

    assert loaded["T123"].bot_token == "xoxb-legacy"
    assert loaded["T123"].app_token == "xapp-legacy"
    assert store.values == {
        "T123:v1:bot-token": "xoxb-legacy",
        "T123:v1:app-token": "xapp-legacy",
    }
    persisted = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert persisted["T123"]["secret_version"] == "v1"
    assert "bot_token" not in persisted["T123"]
    assert "app_token" not in persisted["T123"]
