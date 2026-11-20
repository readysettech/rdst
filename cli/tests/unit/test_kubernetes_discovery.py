"""A server-mode RDST in a Kubernetes pod finds the cluster's databases: Services that front
PostgreSQL or MySQL, CloudNativePG and Zalando clusters, never their Secrets."""

from __future__ import annotations

import pytest

from features.agent_connect import discover, kubernetes, scan
from tests.unit.test_agent_connect import _request, app, data_dir, run  # noqa: F401


def service(name, namespace="database", ports=((5432, "postgres"),), cluster_ip="10.0.0.9",
            kind="ClusterIP"):
    return {"metadata": {"name": name, "namespace": namespace},
            "spec": {"clusterIP": cluster_ip, "type": kind,
                     "ports": [{"port": port, "name": port_name} for port, port_name in ports]}}


CNPG = {"metadata": {"name": "pg", "namespace": "database"},
        "spec": {"bootstrap": {"initdb": {"database": "shop", "owner": "shop",
                                          "secret": {"name": "shop-app"}}}}}


@pytest.fixture
def in_cluster(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("sa-token\n")
    monkeypatch.setattr(kubernetes, "SERVICE_ACCOUNT_DIR", tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.100.0.1")
    monkeypatch.setenv("RDST_SERVER", "1")
    monkeypatch.setenv("RDST_AUTH", "none")
    monkeypatch.delenv("RDST_KUBERNETES_NAMESPACES", raising=False)
    monkeypatch.setattr(discover, "_runs", {})
    return tmp_path


def fake_api(monkeypatch, responses):
    """Answer each API path from ``responses``; a missing path is a type not installed."""
    asked = []

    def get(path):
        asked.append(path)
        answer = responses.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(kubernetes, "_get", get)
    return asked


def test_database_services_become_findings_and_others_dont():
    found = kubernetes.from_services([
        service("orders-db"),
        service("shop-mysql", ports=((3306, "mysql"), (33060, "mysqlx"))),
        service("pooler", ports=((6432, "pgbouncer"),)),
        service("orders-db-hl", cluster_ip="None"),
        service("only-x", ports=((33060, "mysqlx"),)),
        service("web", ports=((80, "http"),)),
        service("pg-custom", ports=((15432, "sql"),)),
    ], skip=set())
    assert [(f["name"], f["engine"], f["host"], f["port"]) for f in found] == [
        ("orders-db", "postgresql", "orders-db.database.svc.cluster.local", 5432),
        ("shop-mysql", "mysql", "shop-mysql.database.svc.cluster.local", 3306),
        ("pooler", "postgresql", "pooler.database.svc.cluster.local", 6432),
        ("pg-custom", "postgresql", "pg-custom.database.svc.cluster.local", 15432),
    ]
    assert all(f["provider"] == "kubernetes" for f in found)
    assert "PgBouncer" in found[2]["note"]


def test_a_cloudnativepg_cluster_is_found_through_its_read_write_service(in_cluster,
                                                                         monkeypatch):
    fake_api(monkeypatch, {
        "/api/v1/services": {"items": [service("pg-rw"), service("pg-ro"), service("pg-r"),
                                       service("other-db", namespace="apps")]},
        "/apis/postgresql.cnpg.io/v1/clusters": {"items": [CNPG]},
    })
    found = kubernetes.scan_kubernetes(lambda where: None)
    assert [f["host"] for f in found] == ["pg-rw.database.svc.cluster.local",
                                          "other-db.apps.svc.cluster.local"]
    pg = found[0]
    assert (pg["database"], pg["user"], pg["port"]) == ("shop", "shop", 5432)
    assert pg["password_hint"] == ("kubectl -n database get secret shop-app "
                                   "-o jsonpath='{.data.password}' | base64 -d")


def test_cloudnativepg_defaults_name_the_app_database_and_its_secret():
    found, covered = kubernetes.from_cnpg([{"metadata": {"name": "main", "namespace": "db"}}])
    assert (found[0]["database"], found[0]["user"]) == ("app", "app")
    assert "get secret main-app" in found[0]["password_hint"]
    assert ("db", "main-ro") in covered


def test_a_zalando_cluster_names_its_database_owner_and_secret():
    found, _ = kubernetes.from_zalando([{
        "metadata": {"name": "acid-shop", "namespace": "db"},
        "spec": {"databases": {"shop": "shop_owner"}}}])
    assert (found[0]["host"], found[0]["database"], found[0]["user"]) == (
        "acid-shop.db.svc.cluster.local", "shop", "shop_owner")
    assert "shop-owner.acid-shop.credentials.postgresql.acid.zalan.do" in \
        found[0]["password_hint"]


def test_without_list_access_the_search_says_what_to_turn_on(in_cluster, monkeypatch):
    fake_api(monkeypatch, {"/api/v1/services": PermissionError(403)})
    with pytest.raises(kubernetes.DiscoveryNotAllowed, match="discovery.kubernetes"):
        kubernetes.scan_kubernetes(lambda where: None)
    monkeypatch.setattr(scan, "knock", lambda host, port: "answering")
    result = scan.scan()
    step = result["steps"][0]
    assert (step["method"], step["state"]) == ("kubernetes", "failed")
    assert "discovery.kubernetes" in step["message"]


def test_an_operator_that_isnt_installed_or_readable_is_skipped(in_cluster, monkeypatch):
    fake_api(monkeypatch, {"/api/v1/services": {"items": [service("orders-db")]},
                           "/apis/acid.zalan.do/v1/postgresqls": PermissionError(403)})
    assert [f["name"] for f in kubernetes.scan_kubernetes(lambda where: None)] == ["orders-db"]


def test_limited_namespaces_are_listed_one_by_one(in_cluster, monkeypatch):
    monkeypatch.setenv("RDST_KUBERNETES_NAMESPACES", "database, apps")
    asked = fake_api(monkeypatch, {
        "/api/v1/namespaces/database/services": {"items": [service("orders-db")]},
        "/api/v1/namespaces/apps/services": {"items": []}})
    looked = []
    found = kubernetes.scan_kubernetes(looked.append)
    assert [f["name"] for f in found] == ["orders-db"]
    assert "/api/v1/namespaces/apps/services" in asked and "/api/v1/services" not in asked
    assert looked[0] == "Services in database, apps"


def test_the_cluster_search_runs_only_in_a_server_mode_pod(tmp_path, monkeypatch):
    monkeypatch.setattr(kubernetes, "SERVICE_ACCOUNT_DIR", tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.100.0.1")
    monkeypatch.setenv("RDST_SERVER", "1")
    assert not kubernetes.available()  # no service account token mounted
    (tmp_path / "token").write_text("t")
    assert kubernetes.available()
    assert [m[0] for m in scan.methods()] == ["kubernetes"]
    monkeypatch.delenv("RDST_SERVER")
    assert not kubernetes.available()
    assert scan.methods() == scan.METHODS


def test_found_cluster_databases_keep_their_provider_and_password_hint():
    found = discover.sanitize_candidates([{
        "engine": "postgresql", "host": "pg-rw.database.svc.cluster.local", "port": 5432,
        "provider": "kubernetes", "password_hint": "kubectl -n database get secret pg-app",
        "found_by": ["CloudNativePG cluster database/pg"]}])
    assert found[0]["provider"] == "kubernetes"
    assert found[0]["password_hint"] == "kubectl -n database get secret pg-app"


def test_a_server_offers_no_coding_agent_and_says_it_searches_the_cluster(app, in_cluster,  # noqa: F811
                                                                          monkeypatch):
    monkeypatch.setattr(discover, "detect", lambda refresh=False: pytest.fail("no agents"))
    body = run(_request(app, "GET", "/api/agent-connect/agents")).json()
    assert body == {"agents": [], "apps": [], "kubernetes": True}


def test_the_scan_route_reports_why_the_cluster_search_found_nothing(app, in_cluster,  # noqa: F811
                                                                    monkeypatch):
    fake_api(monkeypatch, {"/api/v1/services": PermissionError(403)})
    monkeypatch.setattr(discover, "detect", lambda refresh=False: [])

    async def scan_and_wait():
        started = (await _request(app, "POST", "/api/agent-connect/scan")).json()
        assert [m["method"] for m in started["methods"]] == ["kubernetes"]
        await discover.get_run(started["id"]).task
        return (await _request(app, "GET", f"/api/agent-connect/discover/{started['id']}")
                ).json()

    body = run(scan_and_wait())
    assert body["state"] == "done" and body["candidates"] == []
    assert "discovery.kubernetes" in body["message"]
