import json
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


def _clear_session_env(_monkeypatch):
    # FakeStore writes os.environ directly, so monkeypatch would restore what it wrote.
    for name in account_session.SESSION_SECRET_NAMES:
        os.environ.pop(name, None)


def test_session_missing_its_access_token_refreshes_instead_of_signing_out(monkeypatch):
    # What an older build left on Windows: the access token didn't fit one Credential
    # Manager entry, so only the refresh token and the expiry were stored.
    _clear_session_env(monkeypatch)
    store = FakeStore({
        account_session.REFRESH_TOKEN_NAME: "kept-refresh",
        account_session.EXPIRES_AT_NAME: str(time.time() + 3000),
    })
    assert account_session.is_signed_in_locally(store) is True
    calls = []

    def post(url, json, **_kwargs):
        calls.append((url, json))
        return type("Response", (), {
            "status_code": 200,
            "json": lambda self: {
                "access_token": "a" * 1600,
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        })()

    monkeypatch.setattr(account_session.requests, "post", post)

    assert account_session.access_token(store=store) == "a" * 1600
    assert calls[0][0].endswith("/account-auth/refresh")
    assert calls[0][1] == {"refresh_token": "kept-refresh"}
    assert store.values[account_session.ACCESS_TOKEN_NAME] == "a" * 1600
    assert store.values[account_session.REFRESH_TOKEN_NAME] == "rotated-refresh"
    _clear_session_env(monkeypatch)


def test_session_survives_a_restart_with_a_long_access_token(monkeypatch):
    from tests.unit.test_secret_store_service import _windows_like_service

    _clear_session_env(monkeypatch)
    store = _windows_like_service()
    monkeypatch.setattr(store, "is_available", lambda: True)
    monkeypatch.setattr(account_session, "save_account_metadata", lambda account: None)
    token = "eyJ" + "b" * 1700
    account_session.save_session(
        {"access_token": token, "refresh_token": "refresh", "expires_in": 3600},
        store=store,
    )

    # A new process has none of the session in its environment.
    _clear_session_env(monkeypatch)
    session = account_session.load_session(store)
    assert session["access_token"] == token
    assert account_session.access_token(store=store) == token
    store.clear_required(account_session.SESSION_SECRET_NAMES)
    _clear_session_env(monkeypatch)


class UnavailableStore(FakeStore):
    def set_secret(self, name, value, **kwargs):
        os.environ[name] = value
        return {"persisted": False, "session_only": True}


@pytest.mark.parametrize("partial", [False, True])
def test_private_file_session_survives_restart_and_signout(monkeypatch, tmp_path, partial):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    store = UnavailableStore()
    if partial:
        store.values[account_session.REFRESH_TOKEN_NAME] = "stale-refresh"
    saved = account_session.save_session(
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}, store=store)
    path = tmp_path / "account-session.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert all(value["persisted"] for value in saved["persistence"].values())
    _clear_session_env(monkeypatch)
    assert account_session.load_session(store)["refresh_token"] == "refresh"
    assert account_session.access_token(store=store) == "access"
    account_session.clear_session(store)
    assert not path.exists() and account_session.load_session(store) is None


def test_keyring_remains_first_choice_and_clears_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    account_session.save_session({"access_token": "old", "refresh_token": "old"},
                                 store=UnavailableStore())
    store = FakeStore()
    account_session.save_session({"access_token": "new", "refresh_token": "new"}, store=store)
    assert not (tmp_path / "account-session.json").exists()
    _clear_session_env(monkeypatch)
    assert account_session.load_session(store)["refresh_token"] == "new"
    _clear_session_env(monkeypatch)


@pytest.mark.parametrize("contents,mode", [('{}', 0o600), ('not json', 0o600),
                                          ('valid', 0o644)])
def test_unsafe_or_malformed_fallback_is_ignored(monkeypatch, tmp_path, contents, mode):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    path = tmp_path / "account-session.json"
    if contents == "valid":
        contents = json.dumps({name: "token" for name in account_session.SESSION_SECRET_NAMES})
    path.write_text(contents)
    path.chmod(mode)
    assert account_session.load_session(FakeStore()) is None


def test_fallback_symlink_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    real = tmp_path / "other.json"
    real.write_text(json.dumps({name: "token" for name in account_session.SESSION_SECRET_NAMES}))
    real.chmod(0o600)
    (tmp_path / "account-session.json").symlink_to(real)
    assert account_session.load_session(FakeStore()) is None


def test_fallback_refresh_rotates_durable_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    store = UnavailableStore()
    account_session.save_session({"access_token": "old", "refresh_token": "old", "expires_in": -1},
                                 store=store)
    _clear_session_env(monkeypatch)
    response = MagicMock(status_code=200)
    response.json.return_value = {"access_token": "rotated", "refresh_token": "rotated"}
    monkeypatch.setattr(account_session.requests, "post", lambda *args, **kwargs: response)
    assert account_session.access_token(store=store) == "rotated"
    _clear_session_env(monkeypatch)
    assert account_session.load_session(store)["refresh_token"] == "rotated"


def test_failure_to_persist_fallback_clears_process_session(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    _clear_session_env(monkeypatch)
    def fail(values):
        raise OSError("disk full")
    monkeypatch.setattr(account_session, "_save_file_session", fail)
    with pytest.raises(account_session.AccountSessionError, match="Could not save"):
        account_session.save_session({"access_token": "access", "refresh_token": "refresh"},
                                     store=UnavailableStore())
    assert account_session.load_session(FakeStore()) is None
