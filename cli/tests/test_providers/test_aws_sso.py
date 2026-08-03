"""Unit tests for reading AWS SSO accounts and roles from a cached token."""

from __future__ import annotations

import os

import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from features.providers import aws_login, aws_profiles, aws_sso


START_URL = "https://example.awsapps.com/start"


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def home(monkeypatch, tmp_path):
    home_path = str(tmp_path)
    drive, path = os.path.splitdrive(home_path)
    monkeypatch.setenv("HOME", home_path)
    monkeypatch.setenv("USERPROFILE", home_path)
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", path)
    return tmp_path


@pytest.fixture(autouse=True)
def clear_login_registry():
    aws_login.LOGIN_REGISTRY.clear()
    yield
    aws_login.LOGIN_REGISTRY.clear()


def _token_doc(start_url: str, expires: str, token: str, region: str) -> str:
    return json.dumps(
        {"accessToken": token, "startUrl": start_url, "region": region, "expiresAt": expires}
    )


def _write_session_token(
    session_name: str,
    *,
    expires: str,
    token: str = "cached-token",
    region: str = "us-east-1",
    start_url: str = START_URL,
) -> None:
    """Write the cache file botocore keys by sha1(session_name)."""
    path = aws_sso.session_token_path(session_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_token_doc(start_url, expires, token, region), encoding="utf-8")


def _write_token(
    home,
    start_url: str,
    *,
    expires: str,
    token: str = "cached-token",
    region: str = "us-east-1",
    name: str = "session.json",
) -> None:
    cache_dir = home / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / name).write_text(_token_doc(start_url, expires, token, region), encoding="utf-8")


class TestFindCachedToken:
    def test_matches_by_normalized_start_url(self, home):
        # Stored with mixed case; queried with a trailing "/#/" fragment. Both
        # normalize to the same start URL, so the cached token still matches.
        _write_token(
            home, "https://EXAMPLE.awsapps.com/start/", expires=_iso(timedelta(hours=1))
        )

        token, region = aws_sso.find_cached_token(
            "https://example.awsapps.com/start/#/"
        )

        assert token == "cached-token"
        assert region == "us-east-1"

    def test_skips_expired_token(self, home):
        _write_token(
            home,
            "https://example.awsapps.com/start",
            expires=_iso(timedelta(hours=-1)),
        )

        token, region = aws_sso.find_cached_token("https://example.awsapps.com/start")

        assert token is None
        assert region is None


class TestListAccounts:
    def test_shapes_and_sorts_accounts(self, home, monkeypatch):
        _write_token(
            home, "https://example.awsapps.com/start", expires=_iso(timedelta(hours=1))
        )
        client = MagicMock()
        client.list_accounts.return_value = {
            "accountList": [
                {"accountId": "111", "accountName": "Prod"},
                {"accountId": "222", "accountName": "dev"},
            ]
        }
        monkeypatch.setattr(aws_sso, "_sso_client", lambda region: client)

        accounts, error = aws_sso.list_accounts(
            "https://example.awsapps.com/start"
        )

        assert error is None
        assert accounts == [
            {"account_id": "222", "account_name": "dev"},
            {"account_id": "111", "account_name": "Prod"},
        ]
        client.list_accounts.assert_called_once_with(accessToken="cached-token")

    def test_no_token_returns_actionable_error(self, home):
        accounts, error = aws_sso.list_accounts(
            "https://example.awsapps.com/start"
        )

        assert accounts == []
        assert error == aws_sso.NO_SESSION_MESSAGE


class TestListAccountRoles:
    def test_shapes_and_sorts_roles(self, home, monkeypatch):
        _write_token(
            home, "https://example.awsapps.com/start", expires=_iso(timedelta(hours=1))
        )
        client = MagicMock()
        client.list_account_roles.return_value = {
            "roleList": [
                {"roleName": "ReadOnly", "accountId": "111"},
                {"roleName": "Admin", "accountId": "111"},
            ]
        }
        monkeypatch.setattr(aws_sso, "_sso_client", lambda region: client)

        roles, error = aws_sso.list_account_roles(
            "https://example.awsapps.com/start", "111"
        )

        assert error is None
        assert roles == ["Admin", "ReadOnly"]
        client.list_account_roles.assert_called_once_with(
            accessToken="cached-token", accountId="111"
        )


class TestStableSessionName:
    def test_deterministic_across_normalized_variants(self):
        first = aws_sso.stable_session_name("https://example.awsapps.com/start")
        second = aws_sso.stable_session_name("https://EXAMPLE.awsapps.com/start/#/")

        assert first == second
        assert first.startswith("rdst-sso-")
        assert aws_profiles.PROFILE_NAME_RE.fullmatch(first) is not None
        # A different start URL produces a different (still valid) session name.
        assert aws_sso.stable_session_name("https://other.awsapps.com/start") != first


class TestLoadSessionToken:
    def test_loads_token_by_session_cache_key(self, home):
        _write_session_token("rdst-sso-abc", expires=_iso(timedelta(hours=1)))

        token, region = aws_sso.load_session_token("rdst-sso-abc")

        assert token == "cached-token"
        assert region == "us-east-1"


def _process(output: str = "") -> MagicMock:
    process = MagicMock()
    process.poll.return_value = None
    process.stdout = io.StringIO(output)
    return process


class TestStartSsoSessionLogin:
    def test_already_signed_in_only_for_stable_session_token(self, home, monkeypatch):
        monkeypatch.setattr(
            aws_login,
            "_spawn_login_process",
            lambda cmd: pytest.fail("must not spawn when the stable token is live"),
        )
        session = aws_sso.stable_session_name(START_URL)
        _write_session_token(session, expires=_iso(timedelta(hours=1)))

        result = aws_login.start_sso_session_login(START_URL, "us-east-1")

        assert result["state"] == "already_signed_in"
        assert result["login_id"] is None

    def test_token_for_other_session_does_not_short_circuit(self, home, monkeypatch):
        # A user's own session ("mike") cached the same start URL, but its
        # token is keyed by a different session name, so credentials for the
        # finalized profile would not find it. Login must still run.
        _write_session_token("mike", expires=_iso(timedelta(hours=1)))
        spawned: dict[str, list[str]] = {}

        def fake_spawn(cmd):
            spawned["cmd"] = cmd
            return _process()

        monkeypatch.setattr(aws_login, "_spawn_login_process", fake_spawn)

        result = aws_login.start_sso_session_login(START_URL, "us-east-1")

        assert result["state"] == "started"
        assert spawned["cmd"] == [
            "aws",
            "sso",
            "login",
            "--sso-session",
            aws_sso.stable_session_name(START_URL),
        ]
