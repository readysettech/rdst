"""Tests for Supabase project discovery and its API routes (mocked HTTP)."""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import patch

import pytest
import requests

from features.providers import supabase_oauth
from features.providers.provider_common import status_cache
from features.providers.supabase import discover_supabase_projects
from tests.test_providers.conftest import (
    BrokerStub,
    make_response as _response,
    recording_set_secret,
)


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture(autouse=True)
def clean_oauth_state(monkeypatch):
    """Start every test signed out, with no dev client config in reach."""
    monkeypatch.delenv("RDST_SUPABASE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("RDST_SUPABASE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("RDST_KEYSERVICE_URL", "https://keyservice.test")
    monkeypatch.setattr(
        "shared.secret_store_service.SecretStoreService.get_secret",
        lambda self, name: None,
    )
    supabase_oauth.LOGIN_REGISTRY.clear()
    supabase_oauth._clear_tokens()
    status_cache("supabase").clear()
    yield
    supabase_oauth.LOGIN_REGISTRY.clear()
    supabase_oauth._clear_tokens()


def _sb_broker() -> BrokerStub:
    return BrokerStub(
        "supabase",
        access="sba_broker",
        refresh="sbr_broker",
        refreshed="sba_refreshed",
        next_refresh="sbr_next",
    )


def _project(ref: str, name: str, status: str = "ACTIVE_HEALTHY", **overrides) -> dict:
    project = {
        "id": ref,
        "name": name,
        "status": status,
        "region": "us-east-1",
        "organization_id": "org-1",
        "database": {"host": f"db.{ref}.supabase.co", "version": "15.1"},
    }
    project.update(overrides)
    return project


def _pooler(ref: str) -> list[dict]:
    return [
        {
            "database_type": "PRIMARY",
            "db_host": "aws-0-us-east-1.pooler.supabase.com",
            "db_name": "postgres",
            "db_user": f"postgres.{ref}",
            "db_port": 5432,
        }
    ]


def _routing_get(routes: dict):
    """Return a requests.get stub that dispatches on the URL path."""

    def _get(url, **kwargs):
        for suffix, response in routes.items():
            if url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected Supabase URL: {url}")

    return _get


class TestDiscoverSupabaseProjects:
    def _routes(self, projects: list[dict]) -> dict:
        routes = {
            "/v1/projects": _response(200, projects),
            "/v1/organizations": _response(200, [{"id": "org-1", "slug": "acme", "name": "Acme"}]),
        }
        for project in projects:
            ref = project["id"]
            routes[f"/v1/projects/{ref}/config/database/pooler"] = _response(
                200, _pooler(ref)
            )
            routes[f"/v1/projects/{ref}/billing/addons"] = _response(
                200,
                {
                    "selected_addons": [
                        {"type": "compute_instance", "variant": {"identifier": "ci_micro", "name": "Micro"}}
                    ]
                },
            )
        return routes

    def test_maps_healthy_projects_and_skips_unhealthy(self):
        projects = [
            _project("abcdefghijklmnop", "Prod App"),
            _project("qrstuvwxyz123456", "Paused App", status="INACTIVE"),
        ]
        errors: list[str] = []

        with patch("features.providers.supabase.get_access_token", return_value=("sbp_x", "oauth")):
            with patch(
                "features.providers.supabase.requests.get",
                _routing_get(self._routes(projects)),
            ):
                members = discover_supabase_projects(errors)

        assert len(members) == 1
        member = members[0]
        assert member.name == "supabase-prod-app"
        assert member.engine == "postgresql"
        # The pooler host from the API wins over the project's direct db host.
        assert member.host == "aws-0-us-east-1.pooler.supabase.com"
        assert member.port == 5432
        assert member.user == "postgres.abcdefghijklmnop"
        assert member.database == "postgres"
        assert member.password_env == "RDST_SUPABASE_ABCDEFGHIJKLMNOP_PASSWORD"
        assert member.group is None
        assert member.region == "us-east-1"
        assert member.instance_class == "supabase-micro"
        assert member.tls is True
        assert member.tags == [
            "provider:supabase",
            "supabase-ref:abcdefghijklmnop",
            "direct-host:db.abcdefghijklmnop.supabase.co",
            "supabase-org:acme",
        ]

        assert len(errors) == 1
        assert "Paused App" in errors[0]
        assert "INACTIVE" in errors[0]

    def test_name_collision_gets_ref_suffix(self):
        projects = [_project("aaaaaa111111", "My App"), _project("bbbbbb222222", "my-app")]
        errors: list[str] = []

        with patch("features.providers.supabase.get_access_token", return_value=("sbp_x", "oauth")):
            with patch(
                "features.providers.supabase.requests.get",
                _routing_get(self._routes(projects)),
            ):
                members = discover_supabase_projects(errors)

        assert [member.name for member in members] == [
            "supabase-my-app",
            "supabase-my-app-bbbbbb",
        ]
        assert errors == []

    def test_pooler_failure_falls_back_to_direct_host(self):
        projects = [_project("abcdefghijklmnop", "Prod App")]
        routes = self._routes(projects)
        routes["/config/database/pooler"] = _response(500, None)
        routes.pop("/v1/projects/abcdefghijklmnop/config/database/pooler")
        errors: list[str] = []

        with patch("features.providers.supabase.get_access_token", return_value=("sbp_x", "oauth")):
            with patch("features.providers.supabase.requests.get", _routing_get(routes)):
                members = discover_supabase_projects(errors)

        assert len(members) == 1
        assert members[0].host == "db.abcdefghijklmnop.supabase.co"
        assert members[0].user == "postgres"
        assert members[0].port == 5432
        assert len(errors) == 1
        assert "pooler config unavailable" in errors[0]


class TestSupabaseOAuthBroker:
    def _sign_in(self, broker) -> str:
        started = supabase_oauth.start_login("http://127.0.0.1:8787/")
        broker.result_status = "ready"
        assert supabase_oauth.get_login_status(started["login_id"])["state"] == "success"
        return started["login_id"]

    def test_start_sends_pickup_hash_and_optional_return_url(self):
        broker = _sb_broker()
        return_url = "http://127.0.0.1:8787/configure?connected=supabase"
        with patch("features.providers.supabase_oauth.requests.post", broker):
            started = supabase_oauth.start_login(
                "http://127.0.0.1:8787/", return_url
            )

        url, payload = broker.calls[0]
        assert url == broker.START_URL
        assert set(payload) == {"pickup_key_hash", "return_url"}
        assert payload["return_url"] == return_url
        pickup_key = supabase_oauth.LOGIN_REGISTRY[started["login_id"]]["pickup_key"]
        assert payload["pickup_key_hash"] == hashlib.sha256(
            pickup_key.encode("ascii")
        ).hexdigest()
        assert started["authorize_url"] == broker.start_response.json()["authorize_url"]

    def test_broker_failure_and_expiry_map_to_failed(self):
        broker = _sb_broker()
        broker.result_status = "failed"
        broker.result_detail = "Supabase denied the request"
        with patch("features.providers.supabase_oauth.requests.post", broker):
            started = supabase_oauth.start_login("http://127.0.0.1:8787/")
            failed = supabase_oauth.get_login_status(started["login_id"])

            broker.result_status = "expired"
            expired_start = supabase_oauth.start_login("http://127.0.0.1:8787/")
            expired = supabase_oauth.get_login_status(expired_start["login_id"])

        assert failed == {"state": "failed", "detail": "Supabase denied the request"}
        assert expired["state"] == "failed"
        assert "expired" in expired["detail"]

    def test_tokens_stay_in_memory(self):
        broker = _sb_broker()
        written: list[tuple[str, str]] = []

        with patch("features.providers.supabase_oauth.requests.post", broker):
            with patch(
                "shared.secret_store_service.SecretStoreService.set_secret",
                recording_set_secret(written),
            ):
                self._sign_in(broker)
                assert supabase_oauth.get_access_token() == ("sba_broker", "oauth")

        assert written == []
        supabase_oauth._clear_tokens()
        assert supabase_oauth.get_access_token() == (None, None)

    def test_expiring_token_refreshes_through_the_broker(self):
        broker = _sb_broker()
        supabase_oauth._store_tokens(
            {"access_token": "sba_old", "refresh_token": "sbr_broker", "expires_in": 0}
        )

        with patch("features.providers.supabase_oauth.requests.post", broker):
            token, method = supabase_oauth.get_access_token()

        assert (token, method) == ("sba_refreshed", "oauth")
        assert (broker.REFRESH_URL, {"refresh_token": "sbr_broker"}) in broker.calls

    def test_dev_fallback_refreshes_against_supabase(self, monkeypatch):
        monkeypatch.setenv("RDST_SUPABASE_OAUTH_CLIENT_ID", "dev-client")
        monkeypatch.setenv("RDST_SUPABASE_OAUTH_CLIENT_SECRET", "dev-secret")
        supabase_oauth._store_tokens(
            {"access_token": "sba_old", "refresh_token": "sbr_dev", "expires_in": 0}
        )
        posted: list[str] = []

        def fake_post(url, **kwargs):
            posted.append(url)
            assert kwargs["auth"] == ("dev-client", "dev-secret")
            return _response(200, {"access_token": "sba_direct", "expires_in": 3600})

        with patch("features.providers.supabase_oauth.requests.post", fake_post):
            token, method = supabase_oauth.get_access_token()

        assert (token, method) == ("sba_direct", "oauth")
        assert posted == [supabase_oauth.TOKEN_URL]


class TestSupabaseRoutes:
    # These cover the shared _start_provider_login / _poll_provider_login route
    # helpers (also used by the DigitalOcean login routes) plus the Supabase-only
    # direct-exchange dev fallback.
    def test_login_and_poll_run_through_the_broker(self, fleet_router_client):
        broker = _sb_broker()
        with patch("features.providers.supabase_oauth.requests.post", broker):
            response = fleet_router_client.post(
                "/api/providers/supabase-login",
                headers={"origin": "http://localhost:3001"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body == {
                "login_id": "login-1",
                "authorize_url": "https://keyservice.test/oauth/supabase/authorize?login_id=login-1",
            }

            pending = fleet_router_client.get("/api/providers/supabase-login/login-1")
            assert pending.json()["state"] == "running"

            broker.result_status = "ready"
            done = fleet_router_client.get("/api/providers/supabase-login/login-1")

        assert done.json()["state"] == "success"
        assert supabase_oauth.get_access_token() == ("sba_broker", "oauth")
        assert broker.calls[0][1]["return_url"] == (
            "http://localhost:3001/api/providers/oauth-return?provider=supabase"
        )

    def test_login_uses_desktop_return_url(
        self, fleet_router_client, monkeypatch
    ):
        monkeypatch.setenv("RDST_DESKTOP", "1")
        broker = _sb_broker()

        with patch("features.providers.supabase_oauth.requests.post", broker):
            response = fleet_router_client.post("/api/providers/supabase-login")
            assert response.status_code == 200
            # The browser comes back to this server first, then on to the deep link.
            assert broker.calls[0][1]["return_url"] == (
                "http://127.0.0.1:8787/api/providers/oauth-return?provider=supabase"
            )
            broker.result_status = "ready"
            broker.completion = "c-1"
            page = fleet_router_client.get(
                "/api/providers/oauth-return",
                params={"provider": "supabase", "login_id": "login-1", "completion": "c-1"},
            )

        assert page.status_code == 200
        assert '"rdst://oauth-complete?provider=supabase"' in page.text
        assert supabase_oauth.get_access_token() == ("sba_broker", "oauth")

    def test_login_broker_failure_returns_409(self, fleet_router_client):
        broker = _sb_broker()
        broker.start_response = _response(503, None)
        with patch("features.providers.supabase_oauth.requests.post", broker):
            response = fleet_router_client.post("/api/providers/supabase-login")

        assert response.status_code == 409
        assert response.json()["code"] == "supabase_oauth_unavailable"

    def test_offline_login_explains_the_connection_problem(self, fleet_router_client):
        offline = requests.ConnectionError(
            "HTTPSConnectionPool(host='keyservice.test', port=443): "
            "[Errno 111] Connection refused"
        )
        with patch("features.providers.supabase_oauth.requests.post", side_effect=offline):
            response = fleet_router_client.post("/api/providers/supabase-login")

        assert response.status_code == 409
        assert response.json() == {
            "code": "supabase_oauth_unavailable",
            "detail": "Couldn't reach Supabase sign-in. Check your connection and try again.",
        }

    def test_login_falls_back_to_direct_exchange_for_dev(
        self, fleet_router_client, monkeypatch
    ):
        monkeypatch.setenv("RDST_SUPABASE_OAUTH_CLIENT_ID", "dev-client")
        monkeypatch.setenv("RDST_SUPABASE_OAUTH_CLIENT_SECRET", "dev-secret")
        broker = _sb_broker()

        with patch("features.providers.supabase_oauth.requests.post", broker):
            response = fleet_router_client.post("/api/providers/supabase-login")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["authorize_url"].startswith(
            "https://api.supabase.com/v1/oauth/authorize?"
        )
        assert "client_id=dev-client" in body["authorize_url"]
        assert broker.calls == []


class TestBrowserReturnBindsThePickup:
    def test_pickup_waits_for_the_browser_to_bring_the_completion_back(
        self, fleet_router_client
    ):
        broker = _sb_broker()
        broker.completion = "c-1"
        with patch("features.providers.supabase_oauth.requests.post", broker):
            started = fleet_router_client.post("/api/providers/supabase-login").json()
            broker.result_status = "ready"
            waiting = fleet_router_client.get(f"/api/providers/supabase-login/{started['login_id']}")
            page = fleet_router_client.get(
                "/api/providers/oauth-return",
                params={"provider": "supabase", "login_id": started["login_id"],
                        "completion": "c-1"},
            )
            done = fleet_router_client.get(f"/api/providers/supabase-login/{started['login_id']}")

        assert waiting.json()["state"] == "running"
        assert "Connected to Supabase" in page.text
        assert "window.close()" in page.text
        assert done.json()["state"] == "success"
        assert broker.calls[-1][1]["completion"] == "c-1"

    # What a browser sends when the broker's page on the keyservice navigates here.
    CROSS_SITE = {
        "referer": "https://keyservice.readyset.io/",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "document",
    }

    def _start_ready(self, fleet_router_client, broker):
        broker.completion = "c-1"
        started = fleet_router_client.post("/api/providers/supabase-login").json()
        broker.result_status = "ready"
        return started["login_id"]

    def _return(self, fleet_router_client, login_id, **extra):
        return fleet_router_client.get(
            "/api/providers/oauth-return",
            params={"provider": "supabase", "login_id": login_id, **extra},
            headers=self.CROSS_SITE,
        )

    def test_cross_site_return_with_the_completion_lands_the_tokens(self, fleet_router_client):
        broker = _sb_broker()
        with patch("features.providers.supabase_oauth.requests.post", broker):
            login_id = self._start_ready(fleet_router_client, broker)
            page = self._return(fleet_router_client, login_id, completion="c-1")

        assert page.status_code == 200
        assert "Connected to Supabase" in page.text
        assert page.headers["referrer-policy"] == "no-referrer"
        assert supabase_oauth.get_access_token() == ("sba_broker", "oauth")

    def test_cross_site_return_without_the_completion_lands_nothing(self, fleet_router_client):
        broker = _sb_broker()
        with patch("features.providers.supabase_oauth.requests.post", broker):
            login_id = self._start_ready(fleet_router_client, broker)
            missing = self._return(fleet_router_client, login_id)
            wrong = self._return(fleet_router_client, login_id, completion="forged")
            # Neither attempt spoils the login for the browser that really returns.
            real = self._return(fleet_router_client, login_id, completion="c-1")

        assert (missing.status_code, wrong.status_code) == (400, 403)
        assert real.status_code == 200
        assert supabase_oauth.get_access_token() == ("sba_broker", "oauth")

    def test_return_for_a_login_started_elsewhere_lands_nothing(self, fleet_router_client):
        page = fleet_router_client.get(
            "/api/providers/oauth-return",
            params={"provider": "supabase", "login_id": "not-mine", "completion": "c-1"},
        )

        assert page.status_code == 400
        assert "sign-in not found" in page.text
        assert supabase_oauth.get_access_token() == (None, None)

    def test_return_route_refuses_unknown_providers(self, fleet_router_client):
        page = fleet_router_client.get(
            "/api/providers/oauth-return", params={"provider": "evil"})

        assert page.status_code == 404


class TestRemoteBrowserSignIn:
    """`rdst web --host 0.0.0.0`: the broker can only return to this machine's loopback."""

    @staticmethod
    def _post(path, client, headers=None):
        from httpx import ASGITransport, AsyncClient
        from tests.test_providers.conftest import make_fleet_router_client

        app = make_fleet_router_client().app

        async def send():
            transport = ASGITransport(app=app, client=client)
            async with AsyncClient(transport=transport, base_url="http://10.0.0.5:8787") as http:
                return await http.post(path, headers=headers or {})

        return asyncio.run(send())

    @pytest.mark.parametrize("provider", ["supabase", "digitalocean"])
    def test_remote_browser_is_told_where_to_connect_from(self, provider):
        broker = _sb_broker()
        with patch("features.providers.provider_oauth.requests.post", broker):
            response = self._post(
                f"/api/providers/{provider}-login", ("10.0.0.9", 50000),
                {"origin": "http://10.0.0.5:8787"})

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == f"{provider}_oauth_remote_browser"
        assert "from a browser on the machine running RDST" in body["detail"]
        assert broker.calls == []

    def test_loopback_browser_still_starts_the_broker_flow(self, fleet_router_client):
        broker = _sb_broker()
        with patch("features.providers.supabase_oauth.requests.post", broker):
            response = fleet_router_client.post("/api/providers/supabase-login")

        assert response.status_code == 200
        assert broker.calls[0][0] == broker.START_URL


class TestStatusCollectsOrphanedLogin:
    def test_status_picks_up_login_after_ui_poll_died(self, monkeypatch):
        """A browser approval that lands after the UI stopped polling must
        still sign the user in on the next status check."""
        from features.providers import supabase, supabase_oauth

        supabase_oauth._register_login(
            "orphan-login", {"mode": "broker", "pickup_key": "pk", "completion": "c-1"}
        )

        def fake_broker_post(action, payload):
            assert action == "result"
            assert payload == {
                "login_id": "orphan-login", "pickup_key": "pk", "completion": "c-1"}
            return {"status": "ready", "tokens": {
                "access_token": "at-orphan", "refresh_token": "rt",
                "expires_in": 3600,
            }}

        monkeypatch.setattr(supabase_oauth._OAUTH, "broker_post", fake_broker_post)

        calls = []

        def fake_get(path, token, timeout=10.0):
            calls.append((path, token))
            class R:
                status_code = 200
                def json(self):
                    return []
            return R()

        monkeypatch.setattr(supabase, "_get", fake_get)
        status = supabase.get_supabase_status()
        assert status["connected"] is True
        assert status["method"] == "oauth"
        assert calls and calls[0][1] == "at-orphan"

    def test_success_is_sticky_against_losing_racer(self):
        # A late "failed" racer must not overwrite a login already marked success.
        from features.providers import supabase_oauth

        supabase_oauth._register_login("race-login", {"mode": "broker", "pickup_key": "pk"})
        supabase_oauth._mark_login("race-login", "success", "Connected to Supabase")
        supabase_oauth._mark_login("race-login", "failed", "This sign-in expired.")
        entry = supabase_oauth.LOGIN_REGISTRY["race-login"]
        assert entry["status"] == "success"
