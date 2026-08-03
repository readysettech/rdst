"""Tests for Neon project discovery and its API routes (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.providers import neon
from features.providers.neon import discover_neon_projects, get_neon_status
from features.providers.provider_common import status_cache
from tests.test_providers.conftest import make_response as _response, recording_set_secret


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture(autouse=True)
def clean_key_state(monkeypatch):
    """Start every test with no API key in the environment or the keyring."""
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.setattr(
        "shared.secret_store_service.SecretStoreService.get_secret",
        lambda self, name: None,
    )
    status_cache("neon").clear()
    yield


def _project(project_id: str, name: str, region: str = "aws-us-east-2") -> dict:
    return {
        "id": project_id,
        "name": name,
        "region_id": region,
        "org_id": "org-1",
        "pg_version": 16,
    }


def _branch(branch_id: str, default: bool = True) -> dict:
    return {"id": branch_id, "name": "main", "default": default}


def _endpoint(host: str, endpoint_type: str = "read_write", **overrides) -> dict:
    endpoint = {
        "id": "ep-cool-cell-123",
        "type": endpoint_type,
        "host": host,
        "autoscaling_limit_min_cu": 0.25,
        "autoscaling_limit_max_cu": 2,
    }
    endpoint.update(overrides)
    return endpoint


class _NeonStub:
    """Stub for ``requests.get`` against the Neon API, dispatching on path."""

    def __init__(self, pages: list[dict], per_project: dict):
        self.pages = pages
        self.per_project = per_project
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((url, params))
        path = url[len(neon.API_BASE):]

        if path == "/projects":
            cursor = params.get("cursor")
            index = 0 if cursor is None else next(
                position + 1
                for position, page in enumerate(self.pages)
                if page.get("pagination", {}).get("cursor") == cursor
            )
            return _response(200, self.pages[index])
        if path == "/users/me":
            return _response(200, {"email": "dev@example.com"})

        for suffix, response in self.per_project.items():
            if path.endswith(suffix):
                return response
        raise AssertionError(f"unexpected Neon URL: {url}")


def _page(projects: list[dict], cursor: str | None = None) -> dict:
    page: dict = {"projects": projects}
    if cursor:
        page["pagination"] = {"cursor": cursor}
    return page


def _project_routes(project_id: str, branch_id: str, **overrides) -> dict:
    routes = {
        f"/projects/{project_id}/branches": _response(200, {"branches": [_branch(branch_id)]}),
        f"/projects/{project_id}/branches/{branch_id}/endpoints": _response(
            200, {"endpoints": [_endpoint(f"ep-{project_id}.c-2.us-east-2.aws.neon.tech")]}
        ),
        f"/projects/{project_id}/branches/{branch_id}/databases": _response(
            200, {"databases": [{"name": "appdb", "owner_name": "app_owner"}]}
        ),
    }
    routes.update(overrides)
    return routes


class TestDiscoverNeonProjects:
    def test_maps_projects_and_pages_through_the_listing(self, monkeypatch):
        monkeypatch.setenv("NEON_API_KEY", "napi_key")
        monkeypatch.setattr(neon, "PAGE_LIMIT", 1)

        first = _project("wispy-frost-12345678", "Prod App")
        second = _project("bold-sun-87654321", "Staging App", region="aws-eu-west-1")
        stub = _NeonStub(
            pages=[_page([first], cursor="cursor-1"), _page([second])],
            per_project={
                **_project_routes("wispy-frost-12345678", "br-main-1"),
                **_project_routes("bold-sun-87654321", "br-main-2"),
            },
        )
        errors: list[str] = []

        with patch("features.providers.neon.requests.get", stub):
            members = discover_neon_projects(errors)

        assert errors == []
        assert [member.name for member in members] == ["neon-prod-app", "neon-staging-app"]

        member = members[0]
        assert member.engine == "postgresql"
        # The endpoint host carries Neon's compute-cell segment (c-2).
        assert member.host == "ep-wispy-frost-12345678.c-2.us-east-2.aws.neon.tech"
        assert member.port == 5432
        assert member.database == "appdb"
        assert member.user == "app_owner"
        assert member.password_env == "RDST_NEON_WISPY_FROST_12345678_PASSWORD"
        assert member.group is None
        assert member.region == "aws-us-east-2"
        assert member.instance_class == "neon-0.25-2cu"
        assert member.tls is True
        assert member.tags == [
            "provider:neon",
            "neon-project:wispy-frost-12345678",
            "neon-branch:br-main-1",
        ]

        # Both pages were requested, the second one carrying the cursor.
        listings = [params for url, params in stub.calls if url.endswith("/projects")]
        assert listings == [{"limit": 1}, {"limit": 1, "cursor": "cursor-1"}]

    def test_skips_projects_without_default_branch_or_endpoint(self, monkeypatch):
        monkeypatch.setenv("NEON_API_KEY", "napi_key")
        no_branch = _project("no-branch-11111111", "Branchless")
        no_endpoint = _project("no-endpoint-22222222", "Endpointless")
        routes = {
            "/projects/no-branch-11111111/branches": _response(
                200, {"branches": [_branch("br-x", default=False)]}
            ),
            "/projects/no-endpoint-22222222/branches": _response(
                200, {"branches": [_branch("br-y")]}
            ),
            "/projects/no-endpoint-22222222/branches/br-y/endpoints": _response(
                200, {"endpoints": [_endpoint("ep-read-only.neon.tech", endpoint_type="read_only")]}
            ),
            "/projects/no-endpoint-22222222/branches/br-y/databases": _response(
                200, {"databases": []}
            ),
        }
        stub = _NeonStub(pages=[_page([no_branch, no_endpoint])], per_project=routes)
        errors: list[str] = []

        with patch("features.providers.neon.requests.get", stub):
            members = discover_neon_projects(errors)

        assert members == []
        assert len(errors) == 2
        assert "Branchless" in errors[0] and "default branch" in errors[0]
        assert "Endpointless" in errors[1] and "read-write endpoint" in errors[1]

    def test_listing_failure_reports_an_error(self, monkeypatch):
        monkeypatch.setenv("NEON_API_KEY", "napi_key")
        errors: list[str] = []

        with patch("features.providers.neon.requests.get", return_value=_response(500, None)):
            members = discover_neon_projects(errors)

        assert members == []
        assert errors == ["Neon API returned 500 listing projects"]


class TestNeonStatus:
    def test_status_without_a_key_explains_the_paste_flow(self):
        status = get_neon_status()
        assert status["connected"] is False
        assert status["method"] is None
        assert "NEON_API_KEY" in status["detail"]

    def test_status_with_a_key_reports_the_account(self, monkeypatch):
        monkeypatch.setenv("NEON_API_KEY", "napi_key")
        stub = _NeonStub(pages=[_page([])], per_project={})

        with patch("features.providers.neon.requests.get", stub):
            status = get_neon_status()

        assert status["connected"] is True
        assert status["method"] == "api_key"
        assert "dev@example.com" in status["detail"]
        assert status["organizations"] == []


class TestNeonRoutes:
    def test_key_validation_failure_returns_400(self, fleet_router_client, tmp_rdst_home):
        with patch("features.providers.neon.requests.get", return_value=_response(401, None)):
            response = fleet_router_client.post(
                "/api/providers/neon-key", json={"token": "napi_bogus"}
            )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "invalid_token"
        assert "rejected" in body["detail"]

    def test_key_is_stored_after_validation(self, fleet_router_client, tmp_rdst_home):
        stored: list[tuple[str, str]] = []

        with patch("features.providers.neon.requests.get", return_value=_response(200, {"projects": []})):
            with patch(
                "shared.secret_store_service.SecretStoreService.set_secret",
                recording_set_secret(stored),
            ):
                response = fleet_router_client.post(
                    "/api/providers/neon-key", json={"token": "  napi_good  "}
                )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method"] == "api_key"
        assert body["stored"] is True
        assert stored == [("NEON_API_KEY", "napi_good")]

