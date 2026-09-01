import os
import time
from unittest.mock import MagicMock

import pytest
import requests

from shared import account_session


class FakeStore:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_secret(self, name):
        return self.values.get(name)

    def set_secret(self, name, value, **_kwargs):
        self.values[name] = value
        os.environ[name] = value
        return {"persisted": True}

    def clear_required(self, names):
        for name in names:
            self.values.pop(name, None)
            os.environ.pop(name, None)
        return {"cleared": names, "missing": [], "errors": []}


def test_load_prefers_process_session(monkeypatch):
    monkeypatch.setenv(account_session.ACCESS_TOKEN_NAME, "env-access")
    monkeypatch.setenv(account_session.REFRESH_TOKEN_NAME, "env-refresh")
    monkeypatch.setenv(account_session.EXPIRES_AT_NAME, str(time.time() + 300))

    session = account_session.load_session(FakeStore({
        account_session.ACCESS_TOKEN_NAME: "stored-access",
    }))

    assert session["access_token"] == "env-access"
    assert session["refresh_token"] == "env-refresh"


def test_expired_session_refreshes_and_rotates_token(monkeypatch):
    store = FakeStore({
        account_session.ACCESS_TOKEN_NAME: "old-access",
        account_session.REFRESH_TOKEN_NAME: "old-refresh",
        account_session.EXPIRES_AT_NAME: "1",
    })
    response = type("Response", (), {
        "status_code": 200,
        "json": lambda self: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    })()
    post = lambda *args, **kwargs: response
    monkeypatch.setattr(account_session.requests, "post", post)
    monkeypatch.setattr(account_session.TargetsConfig, "load", lambda self: None)
    monkeypatch.setattr(account_session.TargetsConfig, "save", lambda self: None)

    assert account_session.access_token(store=store) == "new-access"
    assert store.values[account_session.REFRESH_TOKEN_NAME] == "new-refresh"
    store.clear_required(account_session.SESSION_SECRET_NAMES)


def test_partial_session_is_not_treated_as_signed_in(monkeypatch):
    store = FakeStore({account_session.ACCESS_TOKEN_NAME: "partial-access"})

    assert account_session.load_session(store) is None
    assert account_session.is_signed_in_locally(store) is False


def test_transient_refresh_failure_preserves_session(monkeypatch):
    store = FakeStore({
        account_session.ACCESS_TOKEN_NAME: "old-access",
        account_session.REFRESH_TOKEN_NAME: "old-refresh",
        account_session.EXPIRES_AT_NAME: "1",
    })

    def fail(*_args, **_kwargs):
        raise requests.ConnectionError("temporary outage")

    monkeypatch.setattr(account_session.requests, "post", fail)

    with pytest.raises(account_session.AccountSessionError, match="Could not refresh"):
        account_session.access_token(store=store)

    assert store.values[account_session.REFRESH_TOKEN_NAME] == "old-refresh"


def test_response_metadata_is_saved_only_for_the_current_access_token(monkeypatch):
    for name in account_session.SESSION_SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    store = FakeStore({
        account_session.ACCESS_TOKEN_NAME: "current-access",
        account_session.REFRESH_TOKEN_NAME: "current-refresh",
        account_session.EXPIRES_AT_NAME: str(time.time() + 300),
    })
    save = MagicMock()
    monkeypatch.setattr(account_session, "_save_account_metadata_unlocked", save)

    assert account_session.save_account_metadata_for_access_token(
        {"analytics_account_id": "account-1"}, "current-access", store
    ) is True
    save.assert_called_once_with({"analytics_account_id": "account-1"})

    save.reset_mock()
    assert account_session.save_account_metadata_for_access_token(
        {"analytics_account_id": "stale-account"}, "old-access", store
    ) is False
    account_session.clear_session(store)
    assert account_session.save_account_metadata_for_access_token(
        {"analytics_account_id": "logged-out-account"}, "current-access", store
    ) is False
    save.assert_not_called()
