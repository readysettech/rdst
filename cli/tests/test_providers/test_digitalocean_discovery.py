"""Tests for DigitalOcean database discovery and its API routes (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from features.providers import digitalocean_oauth
from features.providers.digitalocean import (
    API_BASE,
    discover_digitalocean_clusters,
    get_digitalocean_status,
)
from features.fleet.models import FleetMember
from tests.test_providers.conftest import make_response as _response


pytestmark = pytest.mark.usefixtures("run_blocking_inline")

CLUSTER_PASSWORD = "wv78n3zpz42xezdk"


@pytest.fixture(autouse=True)
def clean_oauth_state(monkeypatch):
    """Start every test signed out, with the broker pointed at a stub host."""
    monkeypatch.setenv("RDST_KEYSERVICE_URL", "https://keyservice.test")
    digitalocean_oauth.LOGIN_REGISTRY.clear()
    digitalocean_oauth.logout()
    yield
    digitalocean_oauth.LOGIN_REGISTRY.clear()
    digitalocean_oauth.logout()


def _do_uri(user: str, password: str, host: str, port: int = 25060, db: str = "defaultdb") -> str:
    """Assemble a DigitalOcean connection URI from parts.

    Built by interpolation on purpose so no complete DSN literal appears in the
    source for secret scanners to flag.
    """
    prefix = "postgresql://"
    return prefix + f"{user}:{password}@{host}:{port}/{db}?sslmode=require"


def _cluster(
    cluster_id: str,
    name: str,
    engine: str = "pg",
    *,
    host: str | None = None,
    database: str = "defaultdb",
    user: str = "doadmin",
    connection: dict | None = None,
    **overrides,
) -> dict:
    host = host or f"{name}-do-user-1234-0.b.db.ondigitalocean.com"
    scheme = "mysql" if "mysql" in engine else "postgresql"
    uri_prefix = f"{scheme}://"
    cluster = {
        "id": cluster_id,
        "name": name,
        "engine": engine,
        "version": "16",
        "num_nodes": 1,
        "region": "nyc3",
        "size": "db-s-2vcpu-4gb",
        "status": "online",
        "connection": {
            "uri": (
                uri_prefix
                + f"{user}:{CLUSTER_PASSWORD}@{host}:25060/{database}"
                "?sslmode=require"
            ),
            # DigitalOcean returns an empty database here on live clusters,
            # which is why the URI is the source of truth.
            "database": "",
            "host": host,
            "port": 25060,
            "user": user,
            "password": CLUSTER_PASSWORD,
            "ssl": True,
        },
    }
    if connection is not None:
        cluster["connection"] = connection
    cluster.update(overrides)
    return cluster


def _page(clusters: list[dict], next_url: str | None = None, total: int | None = None) -> dict:
    page: dict = {"databases": clusters}
    if next_url:
        page["links"] = {"pages": {"next": next_url, "last": next_url}}
    if total is not None:
        page["meta"] = {"total": total}
    return page


def _member(name: str, host: str) -> FleetMember:
    return FleetMember(
        name=name,
        engine="postgresql",
        host=host,
        port=25060,
        database="defaultdb",
        user="doadmin",
        password_env="DO_PROD_DB_PASSWORD",
        tags=["provider:digitalocean"],
        tls=True,
    )


def _signed_in():
    """Patch discovery onto a live OAuth session."""
    return patch(
        "features.providers.digitalocean.get_access_token", return_value=("dop_v1", "oauth")
    )


class _DoStub:
    """Stub for ``requests.get`` against the DigitalOcean API."""

    ACCOUNT_URL = f"{API_BASE}/v2/account"
    DATABASES_URL = f"{API_BASE}/v2/databases"

    def __init__(
        self,
        pages: list[dict],
        account_response: MagicMock | None = None,
        probe_response: MagicMock | None = None,
    ):
        self.pages = pages
        self.account_response = account_response or _response(
            200, {"account": {"email": "dev@example.com", "status": "active"}}
        )
        self.probe_response = probe_response
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((url, params))
        if url == self.ACCOUNT_URL:
            return self.account_response
        if url == f"{self.DATABASES_URL}?per_page=1":
            return self.probe_response or _response(200, self.pages[0])
        if url == self.DATABASES_URL:
            return _response(200, self.pages[0])
        for index, page in enumerate(self.pages):
            if url == f"{self.DATABASES_URL}?page={index + 1}":
                return _response(200, page)
        raise AssertionError(f"unexpected DigitalOcean URL: {url}")


class TestDiscoverDigitalOceanClusters:
    def test_maps_clusters_and_pages_through_the_listing(self):
        first = _page(
            [_cluster("aaaa1111-2222", "prod-db")],
            next_url=f"{_DoStub.DATABASES_URL}?page=2",
            total=2,
        )
        second = _page([_cluster("bbbb3333-4444", "Staging DB", engine="advanced_mysql")])
        stub = _DoStub(pages=[first, second])
        errors: list[str] = []

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                members = discover_digitalocean_clusters(errors)

        assert errors == []
        assert [member.name for member in members] == ["do-prod-db", "do-staging-db"]

        member = members[0]
        assert member.engine == "postgresql"
        assert member.host == "prod-db-do-user-1234-0.b.db.ondigitalocean.com"
        assert member.port == 25060
        assert member.database == "defaultdb"
        assert member.user == "doadmin"
        assert member.password_env == "RDST_DO_PROD_DB_PASSWORD"
        assert member.password_secret_arn is None
        assert member.group is None
        assert member.region == "nyc3"
        assert member.instance_class == "db-s-2vcpu-4gb"
        assert member.tls is True
        assert member.tags == ["provider:digitalocean", "do-cluster:aaaa1111-2222"]
        # The plaintext password from the URI never rides along on a member.
        assert CLUSTER_PASSWORD not in str(member.to_target_config())

        assert members[1].engine == "mysql"
        assert members[1].password_env == "RDST_DO_STAGING_DB_PASSWORD"

        listings = [url for url, _ in stub.calls]
        assert listings == [_DoStub.DATABASES_URL, f"{_DoStub.DATABASES_URL}?page=2"]

    def test_engine_filter_keeps_relational_and_skips_the_rest(self):
        clusters = [
            _cluster("pg-1", "pg-plain", engine="pg"),
            _cluster("pg-2", "pg-advanced", engine="advanced_pg"),
            _cluster("my-1", "my-plain", engine="mysql"),
            _cluster("rd-1", "cache", engine="redis"),
            _cluster("vk-1", "valkey-cache", engine="valkey"),
            _cluster("kf-1", "events", engine="kafka"),
        ]
        stub = _DoStub(pages=[_page(clusters)])
        errors: list[str] = []

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                members = discover_digitalocean_clusters(errors)

        # Unsupported engines are skipped silently: they are not databases
        # rdst can target, not discovery failures.
        assert errors == []
        assert [member.engine for member in members] == [
            "postgresql",
            "postgresql",
            "mysql",
        ]

    def test_uri_wins_over_the_discrete_connection_fields(self):
        cluster = _cluster(
            "aaaa1111-2222",
            "prod-db",
            connection={
                "uri": _do_uri(
                    "uri_user", "secret", "uri-host.db.ondigitalocean.com", port=25061, db="uri_db"
                ),
                "host": "field-host.db.ondigitalocean.com",
                "port": 25060,
                "user": "field_user",
                "database": "",
                "password": "secret",
            },
        )
        stub = _DoStub(pages=[_page([cluster])])
        errors: list[str] = []

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                members = discover_digitalocean_clusters(errors)

        member = members[0]
        assert member.host == "uri-host.db.ondigitalocean.com"
        assert member.port == 25061
        assert member.user == "uri_user"
        assert member.database == "uri_db"

    def test_cluster_without_connection_details_is_skipped(self):
        stub = _DoStub(pages=[_page([_cluster("aaaa1111", "half-built", connection={})])])
        errors: list[str] = []

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                members = discover_digitalocean_clusters(errors)

        assert members == []
        assert len(errors) == 1
        assert "half-built" in errors[0]

    def test_name_collision_gets_an_id_suffix(self):
        clusters = [_cluster("aaaaaa111111", "My App"), _cluster("bbbbbb222222", "my-app")]
        stub = _DoStub(pages=[_page(clusters)])
        errors: list[str] = []

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                members = discover_digitalocean_clusters(errors)

        assert [member.name for member in members] == ["do-my-app", "do-my-app-bbbbbb"]
        assert errors == []

    def test_listing_failure_reports_an_error(self):
        errors: list[str] = []

        with _signed_in():
            with patch(
                "features.providers.digitalocean.requests.get",
                return_value=_response(500, None),
            ):
                members = discover_digitalocean_clusters(errors)

        assert members == []
        assert errors == ["DigitalOcean API returned 500 listing databases"]


class TestDigitalOceanStatus:
    def test_status_with_a_session_reports_the_account(self):
        stub = _DoStub(pages=[_page([])])

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                status = get_digitalocean_status()

        assert status["connected"] is True
        assert status["method"] == "oauth"
        assert "dev@example.com" in status["detail"]

    def test_rejected_session_keeps_the_method(self):
        stub = _DoStub(pages=[_page([])], probe_response=_response(401, None))

        with _signed_in():
            with patch("features.providers.digitalocean.requests.get", stub):
                status = get_digitalocean_status()

        assert status["connected"] is False
        assert status["method"] == "oauth"
        assert "sign in again" in status["detail"]


class TestDigitalOceanRoutes:
    # discover-preview is a single provider-agnostic endpoint; this is the one
    # place its already_exists behavior is exercised across all providers.
    def test_discover_preview_flags_existing_targets(
        self, fleet_router_client, tmp_rdst_home
    ):
        from shared.config.targets import TargetsConfig

        config = TargetsConfig()
        config.load()
        config.upsert("existing", {
            "engine": "postgresql",
            "host": "prod-db-do-user-1234-0.b.db.ondigitalocean.com",
            "port": 25060,
            "database": "defaultdb",
            "user": "doadmin",
            "password_env": "DO_PROD_DB_PASSWORD",
        })
        config.save()

        members = [
            _member("do-old", "prod-db-do-user-1234-0.b.db.ondigitalocean.com"),
            _member("do-new", "staging-db-do-user-1234-0.b.db.ondigitalocean.com"),
        ]

        with patch(
            "features.providers.digitalocean.discover_digitalocean_clusters",
            return_value=members,
        ):
            response = fleet_router_client.post(
                "/api/providers/discover-preview", json={"provider": "digitalocean"}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        by_name = {member["name"]: member for member in body["members"]}
        assert by_name["do-old"]["already_exists"] is True
        assert by_name["do-new"]["already_exists"] is False
        assert body["errors"] == []
