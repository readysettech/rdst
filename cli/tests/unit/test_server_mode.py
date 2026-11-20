"""Server mode: RDST running as a service in a container.

The container is reached through a published port or an ingress, so every
request must carry the access token, origin checks must follow the scheme and
host the browser used behind a TLS-terminating proxy, secrets must survive a
restart without an OS keychain, and a restarted server must reclaim the
monitoring leases its previous container left behind.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient, Response

from shared import server_mode
from shared.api.guards import require_local_request

TOKEN = "test-access-token"
BRIDGE_CLIENT = ("172.17.0.1", 40000)


@pytest.fixture(autouse=True)
def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_SERVER", "1")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RDST_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("RDST_AUTH", raising=False)
    monkeypatch.setattr(server_mode, "_token_cache", None)
    yield
    server_mode._token_cache = None


def _app() -> FastAPI:
    app = FastAPI()
    server_mode.install(app)

    @app.post("/api/guarded")
    async def guarded(request: Request) -> dict[str, bool]:
        require_local_request(request)
        return {"ok": True}

    @app.get("/api/open")
    async def open_route() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/{path:path}")
    async def spa(path: str) -> dict[str, str]:
        return {"page": path}

    return app


def _send(method: str, path: str, base_url: str = "http://localhost:8787",
          client: tuple[str, int] = BRIDGE_CLIENT, **kwargs) -> Response:
    cookies = kwargs.pop("cookies", None)

    async def run() -> Response:
        transport = ASGITransport(app=_app(), client=client)
        async with AsyncClient(transport=transport, base_url=base_url, cookies=cookies) as http:
            return await http.request(method, path, **kwargs)

    return asyncio.run(run())


def _session_cookie() -> str:
    return server_mode._session_value(TOKEN)


def test_api_without_a_session_is_unauthorized():
    response = _send("GET", "/api/open")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_page_without_a_session_redirects_to_sign_in():
    response = _send("GET", "/database?tab=queries")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fdatabase%3Ftab%3Dqueries"


def test_health_stays_open_for_probes():
    assert _send("GET", "/health").status_code == 200


def test_sign_in_with_the_token_sets_a_session_cookie():
    response = _send("POST", "/login", data={"token": TOKEN, "next": "/database"})

    assert response.status_code == 303
    assert response.headers["location"] == "/database"
    cookie = response.headers["set-cookie"]
    assert f"{server_mode.SESSION_COOKIE}={_session_cookie()}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie


def test_sign_in_behind_tls_marks_the_cookie_secure():
    response = _send("POST", "/login", data={"token": TOKEN, "next": "/"},
                     headers={"x-forwarded-proto": "https"})

    assert "Secure" in response.headers["set-cookie"]


def test_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setattr(server_mode, "_FAILED_SIGN_IN_DELAY_SECONDS", 0)
    response = _send("POST", "/login", data={"token": "nope", "next": "/"})

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("next_path", ["//evil.example", "https://evil.example", "/\\evil"])
def test_sign_in_never_redirects_off_site(next_path):
    response = _send("POST", "/login", data={"token": TOKEN, "next": next_path})

    assert response.headers["location"] == "/"


def test_a_token_in_the_url_never_signs_in():
    # Query strings land in access logs, so the token is only accepted in a form or header.
    response = _send("GET", f"/inbox?token={TOKEN}")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert "set-cookie" not in response.headers
    assert _send("GET", f"/api/open?token={TOKEN}").status_code == 401


def test_only_the_access_checks_401_is_marked_for_the_sign_in_redirect():
    assert _send("GET", "/api/open").headers[server_mode.SIGN_IN_HEADER] == "required"


def test_an_oversized_sign_in_form_is_refused():
    response = _send("POST", "/login", data={"token": "x" * 10_000})

    assert response.status_code == 413
    assert "set-cookie" not in response.headers


def test_session_cookie_admits_the_browser():
    response = _send("GET", "/api/open",
                     cookies={server_mode.SESSION_COOKIE: _session_cookie()})

    assert response.status_code == 200


def test_bearer_token_admits_scripts_without_an_origin():
    response = _send("POST", "/api/guarded", headers={"authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


def test_same_origin_browser_behind_a_tls_ingress_passes_the_guard():
    response = _send(
        "POST", "/api/guarded",
        base_url="http://rdst.home.example",
        cookies={server_mode.SESSION_COOKIE: _session_cookie()},
        headers={"origin": "https://rdst.home.example", "x-forwarded-proto": "https"},
    )

    assert response.status_code == 200


def test_cross_site_page_is_still_forbidden_with_a_session():
    response = _send(
        "POST", "/api/guarded",
        cookies={server_mode.SESSION_COOKIE: _session_cookie()},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403


def test_browser_on_a_published_localhost_port_passes_the_guard():
    """Docker publishes the port on localhost; the peer is the bridge address."""
    response = _send(
        "POST", "/api/guarded",
        cookies={server_mode.SESSION_COOKIE: _session_cookie()},
        headers={"origin": "http://localhost:8787"},
    )

    assert response.status_code == 200


def test_auth_none_leaves_sign_in_to_a_proxy(monkeypatch):
    monkeypatch.setenv("RDST_AUTH", "none")

    assert _send("GET", "/api/open").status_code == 200


def test_generated_token_is_kept_on_the_data_volume(monkeypatch, tmp_path):
    monkeypatch.delenv("RDST_ACCESS_TOKEN")
    token, source = server_mode.access_token()
    server_mode._token_cache = None
    again, again_source = server_mode.access_token()

    assert source == "generated" and again_source == "file"
    assert token == again
    path = tmp_path / "access-token"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_file_keyring_round_trips_and_is_private(tmp_path):
    ring = server_mode.FileKeyring(tmp_path / "secrets.json")
    ring.set_password("rdst-web", "RDST_SHOP_PASSWORD", "s3cret")

    assert server_mode.FileKeyring(tmp_path / "secrets.json").get_password(
        "rdst-web", "RDST_SHOP_PASSWORD") == "s3cret"
    assert json.loads((tmp_path / "secrets.json").read_text()) == {
        "rdst-web": {"RDST_SHOP_PASSWORD": "s3cret"}}
    assert stat.S_IMODE(os.stat(tmp_path / "secrets.json").st_mode) == 0o600

    ring.delete_password("rdst-web", "RDST_SHOP_PASSWORD")
    assert ring.get_password("rdst-web", "RDST_SHOP_PASSWORD") is None


def test_lease_from_an_earlier_container_is_reclaimed():
    from shared.query_registry.observation_store import _dead_collection_owner

    owner = "rdst-process:" + json.dumps(["old-container-id", 7, 1.0, "x"])

    assert _dead_collection_owner(owner) is True


def test_outside_server_mode_another_hosts_lease_is_kept(monkeypatch):
    from shared.query_registry.observation_store import _dead_collection_owner

    monkeypatch.delenv("RDST_SERVER")
    owner = "rdst-process:" + json.dumps(["another-laptop", 7, 1.0, "x"])

    assert _dead_collection_owner(owner) is False


def test_provider_oauth_returns_to_the_public_address():
    from features.providers.api.routes import _provider_return_url

    class _Req:
        headers = {"host": "rdst-0:8787", "x-forwarded-proto": "https",
                   "x-forwarded-host": "rdst.home.example", "origin": ""}

        class url:  # noqa: N801 - mirrors starlette's attribute
            scheme = "http"

        base_url = "http://rdst-0:8787/"

    assert _provider_return_url(_Req(), "supabase") == (
        "https://rdst.home.example/configure?connected=supabase")


@pytest.mark.parametrize("origin, refused", [
    ("http://localhost:8787", False), ("http://127.0.0.1:18787", False),
    ("https://rdst.home.example", True), ("http://10.0.0.5:8787", True),
])
def test_keyservice_sign_ins_start_only_from_localhost(origin, refused):
    from urllib.parse import urlsplit

    parts = urlsplit(origin)

    class _Req:
        headers = {"host": parts.netloc}

        class url:  # noqa: N801 - mirrors starlette's attribute
            scheme = parts.scheme

    refusal = server_mode.broker_sign_in_refusal(_Req())
    assert (refusal == server_mode.BROKER_SIGN_IN_LOCALHOST_ONLY) is refused
    if not refused:
        assert refusal is None


def _docker_on_path(monkeypatch):
    """Docker looks installed; any attempt to run it fails the test."""
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")

    def _no_docker(*args, **kwargs):
        raise AssertionError(f"server mode ran {args[0] if args else kwargs}")

    monkeypatch.setattr(subprocess, "run", _no_docker)


def test_docker_checks_report_containers_off(monkeypatch):
    from features.audit.readyset_benchmark import docker_available
    from features.cache.readyset_container import check_docker_available
    from features.demo.cli.command import DemoCommand
    from features.qpdemo.service import DemoService
    from shared.deploy.local_docker import docker_runtime_status

    _docker_on_path(monkeypatch)

    assert docker_runtime_status() == {"installed": False, "running": False}
    check = check_docker_available()
    assert check["success"] is False
    assert check["error"] == server_mode.CONTAINERS_UNAVAILABLE
    assert docker_available() is False
    assert DemoService.docker_installed() is False
    assert DemoCommand._docker_available(object.__new__(DemoCommand)) is False


def test_outside_server_mode_docker_checks_are_unchanged(monkeypatch):
    from features.audit.readyset_benchmark import docker_available

    monkeypatch.delenv("RDST_SERVER")
    _docker_on_path(monkeypatch)

    assert server_mode.containers_unavailable_reason() is None
    assert docker_available() is True


def _demo_app() -> FastAPI:
    from features.qpdemo.api import routes as qpdemo

    app = FastAPI()
    app.include_router(qpdemo.router, prefix="/api")
    return app


def test_demo_api_refuses_in_server_mode():
    async def run() -> Response:
        transport = ASGITransport(app=_demo_app(), client=("127.0.0.1", 5000))
        async with AsyncClient(transport=transport, base_url="http://localhost:8787") as http:
            return await http.post("/api/demo/provision")

    response = asyncio.run(run())

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "unavailable_in_container"


def test_readyset_comparisons_refuse_in_server_mode():
    from fastapi import HTTPException

    from features.cache.api.routes import _require_readyset_runtime

    with pytest.raises(HTTPException) as refused:
        asyncio.run(_require_readyset_runtime())

    assert refused.value.status_code == 409
    assert refused.value.detail["message"] == server_mode.CONTAINERS_UNAVAILABLE


def test_load_test_runs_the_origin_lane_but_not_readyset():
    from fastapi import HTTPException

    from features.query_registry.api.routes import _require_lane_runtime

    _require_lane_runtime(["origin"])
    with pytest.raises(HTTPException) as refused:
        _require_lane_runtime(["origin", "readyset"])

    assert refused.value.status_code == 409


def test_cache_deploy_to_local_docker_refuses_in_server_mode():
    from features.cache.service import CacheService

    async def run():
        from features.cache.models import CacheInput, CacheOptions

        service = CacheService()
        return [event async for event in service.deploy(
            CacheInput(target="shop"), CacheOptions(mode="docker"))]

    events = asyncio.run(run())

    assert events[-1].type == "error"
    assert events[-1].message == server_mode.CONTAINERS_UNAVAILABLE


def test_analyze_skips_the_readyset_container_run():
    from unittest.mock import AsyncMock, Mock, patch

    from features.analyze.models import AnalyzeInput, AnalyzeOptions
    from features.analyze.service import AnalyzeService

    service = AnalyzeService()
    seen = {}

    async def no_progress():
        return
        yield

    async def capture(**kwargs):
        seen["readyset"] = kwargs["readyset_result"]
        return
        yield

    async def run():
        with (
            patch.object(service, "_run_workflow_with_progress", new_callable=AsyncMock,
                         return_value=(no_progress(), {"success": True, "result": {}})),
            patch.object(service, "_run_readyset_analysis_sync") as readyset,
            patch.object(service, "_process_results", side_effect=capture),
        ):
            async for _ in service._run_parallel_analysis(
                input=AnalyzeInput(sql="select 1", normalized_sql="select 1", source="test"),
                options=AnalyzeOptions(target="shop", readyset_cache=True),
                target_name="shop", target_config={}, workflow_path=Mock(),
            ):
                pass
        return readyset

    readyset = asyncio.run(run())

    readyset.assert_not_called()
    assert seen["readyset"] == {"success": False, "error": server_mode.CONTAINERS_UNAVAILABLE}


def test_unreachable_database_advice_mentions_network_policy_in_server_mode(monkeypatch):
    from features.monitor.connection import connection_problem

    monkeypatch.delenv("RDST_SERVER", raising=False)
    assert "NetworkPolicy" not in connection_problem("connection refused")["fix"]
    monkeypatch.setenv("RDST_SERVER", "1")
    for error in ("connection refused", "connection timed out"):
        assert "networkPolicy.databases" in connection_problem(error)["fix"]
    assert "NetworkPolicy" not in connection_problem("password authentication failed")["fix"]


def test_connection_test_refused_mentions_network_policy_in_server_mode(monkeypatch):
    from features.configure.service import ConfigureService

    monkeypatch.setenv("RDST_SERVER", "1")
    config = {"engine": "postgresql", "host": "127.0.0.1", "port": 1, "user": "u",
              "database": "d", "password": "p"}
    result = ConfigureService()._connection_test(config)
    assert result["success"] is False
    assert result["message"].startswith("Connection refused: Cannot reach 127.0.0.1:1.")
    assert "networkPolicy.databases" in result["message"]


def test_sign_in_page_says_where_the_token_is_for_docker_and_kubernetes():
    page = _send("GET", "/login").text
    assert "Enter this server's access token." in page
    assert f"docker exec &lt;container&gt; cat {server_mode.access_token_path()}" in page
    assert "prefers-color-scheme: dark" in page


def test_sign_in_page_shows_the_exact_kubectl_command_under_helm(monkeypatch):
    monkeypatch.setenv("RDST_AUTH_SECRET", "team-rdst-auth")
    monkeypatch.setenv("RDST_AUTH_SECRET_KEY", "access-token")
    monkeypatch.setenv("RDST_NAMESPACE", "tools")
    page = _send("GET", "/login").text
    assert ("kubectl -n tools get secret team-rdst-auth -o jsonpath='{.data.access-token}' "
            "| base64 -d") in page
    assert "docker exec" not in page


def test_startup_lines_point_at_the_secret_when_the_token_comes_from_env(monkeypatch):
    monkeypatch.setenv("RDST_AUTH_SECRET", "rdst-auth")
    monkeypatch.setenv("RDST_NAMESPACE", "rdst")
    lines = server_mode.startup_lines("0.0.0.0", 8787)
    assert TOKEN not in "\n".join(lines)
    assert ("  kubectl -n rdst get secret rdst-auth -o jsonpath='{.data.access-token}' "
            "| base64 -d") in lines


def test_startup_lines_say_how_to_read_a_generated_token_without_printing_it(monkeypatch):
    monkeypatch.delenv("RDST_ACCESS_TOKEN")
    monkeypatch.setenv("RDST_PUBLIC_URL", "https://rdst.example.com/")
    lines = server_mode.startup_lines("0.0.0.0", 8787)
    token = server_mode.access_token()[0]
    assert token not in "\n".join(lines)
    assert f"  docker exec <container> cat {server_mode.access_token_path()}" in lines


def test_health_probes_stay_out_of_the_access_log_after_uvicorn_configures_logging():
    import logging
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    server_mode.quiet_health_checks()
    logging.config.dictConfig(LOGGING_CONFIG)
    access = logging.getLogger("uvicorn.access")

    def record(path):
        return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                 '%s - "%s %s HTTP/%s" %d', ("10.0.0.1:1", "GET", path, "1.1", 200), None)

    assert not access.filter(record("/health"))
    assert not access.filter(record("/health?probe=1"))
    assert access.filter(record("/api/monitor/targets"))


def test_routes_that_take_a_server_path_refuse_in_server_mode():
    from fastapi import HTTPException

    from features.fleet.api.routes import FleetImportRequest, import_fleet
    from features.query_registry.api.routes import import_queries
    from features.tunnel.api.routes import browse_files
    from shared.api.routes.browse import browse_directory

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "client": ("127.0.0.1", 0), "server": ("localhost", 8787)})

    async def refused(call) -> int:
        try:
            await call
        except HTTPException as exc:
            return exc.status_code
        return 200

    assert asyncio.run(refused(browse_directory(request, path="/"))) == 409
    assert asyncio.run(refused(browse_files(request, path="/"))) == 409
    assert asyncio.run(refused(import_fleet(request, FleetImportRequest(csv_file="/etc/passwd")))) == 409
    assert asyncio.run(refused(import_fleet(request, FleetImportRequest(csv_content="a,b\n")))) == 200
    from features.query_registry.api.routes import ImportQueriesRequest
    assert asyncio.run(refused(import_queries(request, ImportQueriesRequest(file="/etc/passwd")))) == 409
