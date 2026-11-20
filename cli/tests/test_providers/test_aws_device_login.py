"""AWS SSO device sign-in without the AWS CLI (servers and containers)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest

from features.providers import aws_device_login, aws_login
from features.providers.aws_sso import load_session_token, session_token_path, stable_session_name

from .conftest import make_fleet_router_client


pytestmark = pytest.mark.usefixtures("run_blocking_inline")

START_URL = "https://example.awsapps.com/start"
REGISTRATION = {"clientId": "cid", "clientSecret": "csecret", "clientSecretExpiresAt": 4102444800}
AUTHORIZATION = {
    "deviceCode": "dev-code",
    "userCode": "ABCD-EFGH",
    "verificationUri": "https://device.sso.us-east-1.amazonaws.com/",
    "verificationUriComplete": "https://device.sso.us-east-1.amazonaws.com/?user_code=ABCD-EFGH",
    "expiresIn": 600,
    "interval": 5,
}
TOKEN = {"accessToken": "access-1", "expiresIn": 28800, "refreshToken": "refresh-1"}


class _Errors:
    class AuthorizationPendingException(Exception):
        pass

    class SlowDownException(Exception):
        pass

    class ExpiredTokenException(Exception):
        pass

    class AccessDeniedException(Exception):
        pass


class FakeOidc:
    exceptions = _Errors

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.registered = None
        self.token_calls = 0

    def register_client(self, **params):
        self.registered = params
        return dict(REGISTRATION)

    def start_device_authorization(self, **params):
        assert params == {"clientId": "cid", "clientSecret": "csecret", "startUrl": START_URL}
        return dict(AUTHORIZATION)

    def create_token(self, **params):
        assert params["grantType"] == aws_device_login.DEVICE_GRANT_TYPE
        assert params["deviceCode"] == "dev-code"
        self.token_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome()
        return outcome


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    drive, path = os.path.splitdrive(str(tmp_path))
    for name, value in {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                        "HOMEDRIVE": drive, "HOMEPATH": path}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / ".aws" / "config"))
    aws_login.LOGIN_REGISTRY.clear()
    yield tmp_path
    aws_login.LOGIN_REGISTRY.clear()


def _login(outcomes, session_name="rdst-sso-test"):
    client = FakeOidc(outcomes)
    sleeps = []
    login = aws_device_login.DeviceLogin(
        start_url=START_URL, region="us-east-1", session_name=session_name,
        client_factory=lambda region: client, sleep=sleeps.append,
    )
    login.start()
    return login, client, sleeps


def test_start_exposes_the_verification_page_with_the_code():
    login, client, _ = _login([TOKEN])
    assert login.verification_url == AUTHORIZATION["verificationUriComplete"]
    assert login.user_code == "ABCD-EFGH"
    assert client.registered == {"clientName": "rdst", "clientType": "public",
                                 "scopes": ["sso:account:access"]}


def test_success_writes_a_token_botocore_loads_and_can_refresh():
    from botocore.utils import JSONFileCache, SSOTokenLoader

    login, _, sleeps = _login([TOKEN])
    login.poll()
    assert login.snapshot() == ("success", "AWS SSO session is active")
    assert sleeps == []

    path = aws_device_login.sso_cache_dir() / f"{aws_device_login.cache_key(START_URL, 'rdst-sso-test')}.json"
    assert path == session_token_path("rdst-sso-test")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    loaded = SSOTokenLoader(cache=JSONFileCache(str(aws_device_login.sso_cache_dir())))(
        START_URL, session_name="rdst-sso-test")
    assert loaded["accessToken"] == "access-1"
    assert {"clientId", "clientSecret", "registrationExpiresAt", "refreshToken"} <= loaded.keys()
    assert loaded["registrationExpiresAt"] == "2100-01-01T00:00:00Z"
    assert datetime.strptime(loaded["expiresAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc) > datetime.now(timezone.utc)
    assert load_session_token("rdst-sso-test") == ("access-1", "us-east-1")


def test_legacy_profile_token_is_keyed_by_start_url():
    login, client, _ = _login([TOKEN], session_name=None)
    login.poll()
    assert "scopes" not in client.registered
    data = json.loads((aws_device_login.sso_cache_dir() /
                       f"{aws_device_login.cache_key(START_URL, None)}.json").read_text())
    assert data["startUrl"] == START_URL and data["accessToken"] == "access-1"
    assert "clientSecret" not in data


def test_pending_then_success_waits_the_interval():
    login, client, sleeps = _login([_Errors.AuthorizationPendingException,
                                    _Errors.AuthorizationPendingException, TOKEN])
    login.poll()
    assert login.snapshot()[0] == "success"
    assert sleeps == [5, 5] and client.token_calls == 3


def test_slow_down_backs_off():
    login, _, sleeps = _login([_Errors.SlowDownException, _Errors.AuthorizationPendingException, TOKEN])
    login.poll()
    assert login.snapshot()[0] == "success"
    assert sleeps == [10, 10]


def test_expired_code_times_out_without_a_token():
    login, _, _ = _login([_Errors.ExpiredTokenException])
    login.poll()
    assert login.snapshot()[0] == "timeout"
    assert load_session_token("rdst-sso-test") == (None, None)


def test_denied_request_fails():
    login, _, _ = _login([_Errors.AccessDeniedException])
    login.poll()
    assert login.snapshot() == ("failed", "The AWS sign-in request was denied")


def test_code_lifetime_bounds_polling():
    client = FakeOidc([_Errors.AuthorizationPendingException] * 50)
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    login = aws_device_login.DeviceLogin(
        start_url=START_URL, region="us-east-1", session_name="s",
        client_factory=lambda region: client, sleep=sleep, clock=lambda: now[0])
    login.start()
    client.outcomes = [_Errors.AuthorizationPendingException] * 500
    login.poll()
    assert login.snapshot()[0] == "timeout"
    assert client.token_calls == 120


def test_logout_without_cli_removes_cached_tokens(monkeypatch):
    revoked = []

    class Sso:
        def logout(self, accessToken):
            revoked.append(accessToken)

    class Session:
        def create_client(self, service, region_name):
            assert service == "sso"
            return Sso()

    monkeypatch.setattr("botocore.session.get_session", lambda: Session())
    aws_device_login.write_token_cache(start_url=START_URL, region="us-east-1", session_name="s1",
                                       token=TOKEN, registration=REGISTRATION)
    registration_file = aws_device_login.sso_cache_dir() / "botocore-client-id-us-east-1.json"
    registration_file.write_text(json.dumps({"clientId": "cid"}))
    monkeypatch.setattr(aws_device_login, "cli_available", lambda: False)

    assert aws_login.logout() == {"signed_out": True, "removed": 1}
    assert revoked == ["access-1"]
    assert load_session_token("s1") == (None, None)
    assert registration_file.exists()


class TestRoutesWithoutCli:
    @pytest.fixture(autouse=True)
    def no_cli(self, monkeypatch):
        monkeypatch.setattr(aws_device_login, "cli_available", lambda: False)
        self.fake = FakeOidc([_Errors.AuthorizationPendingException, TOKEN])
        monkeypatch.setattr(aws_device_login, "_oidc_client", lambda region: self.fake)
        monkeypatch.setattr(aws_device_login.DeviceLogin, "run_in_background",
                            lambda self, name: None)

    def _poll(self, client, login_id):
        return client.get(f"/api/providers/aws-login/{login_id}").json()

    def test_sso_session_login_runs_the_device_flow(self):
        client = make_fleet_router_client()
        response = client.post("/api/providers/aws-sso-login",
                               json={"start_url": START_URL, "region": "us-east-1"})
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "started"
        assert body["verification_url"] == AUTHORIZATION["verificationUriComplete"]

        status = self._poll(client, body["login_id"])
        assert status["state"] == "running"
        assert status["verification_url"] == AUTHORIZATION["verificationUriComplete"]

        entry = aws_login.LOGIN_REGISTRY[body["login_id"]]
        entry["device"]._sleep = lambda seconds: None
        entry["device"].poll()
        status = self._poll(client, body["login_id"])
        assert status["state"] == "success"
        assert load_session_token(stable_session_name(START_URL))[0] == "access-1"

    def test_profile_login_uses_the_profiles_sso_session(self, isolated_home, monkeypatch):
        config = isolated_home / ".aws" / "config"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[profile dev]\nsso_session = corp\nsso_account_id = 111111111111\n"
            "sso_role_name = Admin\nregion = us-east-1\n\n"
            f"[sso-session corp]\nsso_start_url = {START_URL}\nsso_region = us-east-1\n"
            "sso_registration_scopes = sso:account:access\n")
        verified = iter([(False, "expired"), (True, "Signed in as arn:aws:sts::1:assumed-role/Admin/me")])
        monkeypatch.setattr(aws_login, "verify_profile", lambda profile: next(verified))
        client = make_fleet_router_client()

        body = client.post("/api/providers/aws-login", json={"profile": "dev"}).json()
        assert body["state"] == "started"
        entry = aws_login.LOGIN_REGISTRY[body["login_id"]]
        entry["device"]._sleep = lambda seconds: None
        entry["device"].poll()
        status = self._poll(client, body["login_id"])
        assert status["state"] == "success"
        assert status["detail"].startswith("Signed in as")
        assert load_session_token("corp")[0] == "access-1"

    def test_profile_without_sso_settings_is_rejected(self, isolated_home, monkeypatch):
        config = isolated_home / ".aws" / "config"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("[profile keys]\nregion = us-east-1\n")
        monkeypatch.setattr(aws_login, "verify_profile", lambda profile: (False, "no creds"))
        response = make_fleet_router_client().post("/api/providers/aws-login",
                                                   json={"profile": "keys"})
        assert response.status_code == 400
        assert response.json()["code"] == "unknown_profile"

    def test_login_times_out_after_the_window(self, monkeypatch):
        client = make_fleet_router_client()
        body = client.post("/api/providers/aws-sso-login",
                           json={"start_url": START_URL, "region": "us-east-1"}).json()
        entry = aws_login.LOGIN_REGISTRY[body["login_id"]]
        entry["started_at"] = time.monotonic() - aws_login.LOGIN_TIMEOUT_SECONDS - 1
        assert self._poll(client, body["login_id"])["state"] == "timeout"
        assert entry["device"]._cancelled.is_set()
