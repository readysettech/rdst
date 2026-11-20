"""Find PostgreSQL and MySQL databases in the Kubernetes cluster a server-mode RDST runs in.

RDST reads Services, and CloudNativePG and Zalando clusters when those operators are
installed, through the pod's service account. It never reads Secrets: for an operator's
database it says which Secret holds the password and how to read it. The Helm chart grants
the read-only access (discovery.kubernetes); without it the search says what to turn on.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
REQUEST_TIMEOUT_SECONDS = (2, 5)
# Ports the databases RDST monitors listen on: PostgreSQL, PgBouncer, MySQL.
POSTGRES_PORTS = (5432, 6432)
MYSQL_PORTS = (3306,)
# MySQL's X protocol port: a Service exposing only it isn't one RDST can connect to.
MYSQL_X_PORT = 33060
DB_NAME = re.compile(r"postgres|postgis|timescale|pgbouncer|mysql|mariadb|percona|(^|[-_])pg($|[-_])",
                     re.IGNORECASE)
MYSQL_NAME = re.compile(r"mysql|mariadb|percona", re.IGNORECASE)
NOT_ALLOWED = ("RDST isn't allowed to list Services in this cluster. Turn on "
               "discovery.kubernetes in the Helm chart, or connect the database manually.")


class DiscoveryNotAllowed(RuntimeError):
    """The service account can't list Services: the chart's discovery access is off."""


def available() -> bool:
    """Whether this is a server-mode RDST in a pod that has a service account token."""
    from shared import server_mode

    return (server_mode.enabled() and bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
            and (SERVICE_ACCOUNT_DIR / "token").is_file())


def namespaces() -> list[str]:
    """The namespaces the chart limited discovery to; empty means the whole cluster."""
    raw = os.environ.get("RDST_KUBERNETES_NAMESPACES", "")
    return [name.strip() for name in raw.split(",") if name.strip()]


def _api_base() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{port}"


def _get(path: str) -> dict[str, Any] | None:
    """One GET against the API server. None when the resource type isn't installed."""
    import requests

    token = (SERVICE_ACCOUNT_DIR / "token").read_text().strip()
    ca = SERVICE_ACCOUNT_DIR / "ca.crt"
    response = requests.get(_api_base() + path, timeout=REQUEST_TIMEOUT_SECONDS,
                            headers={"Authorization": f"Bearer {token}"},
                            verify=str(ca) if ca.is_file() else True, allow_redirects=False)
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise PermissionError(response.status_code)
    response.raise_for_status()
    return response.json()


def _list(group_path: str, resource: str) -> list[dict[str, Any]]:
    """Every object of one kind, across the allowed namespaces or the whole cluster."""
    allowed = namespaces()
    paths = ([f"{group_path}/namespaces/{name}/{resource}" for name in allowed] if allowed
             else [f"{group_path}/{resource}"])
    items: list[dict[str, Any]] = []
    for path in paths:
        body = _get(path)
        if body:
            items += [item for item in body.get("items") or [] if isinstance(item, dict)]
    return items


def _optional(group_path: str, resource: str) -> list[dict[str, Any]]:
    """An operator's objects; nothing when the operator isn't installed or isn't readable."""
    try:
        return _list(group_path, resource)
    except PermissionError:
        return []
    except Exception:
        logger.debug("Listing %s failed", resource, exc_info=True)
        return []


def _host(name: str, namespace: str) -> str:
    return f"{name}.{namespace}.svc.cluster.local"


def _engine_and_port(service: dict[str, Any]) -> tuple[str, int] | None:
    """The engine and port of a Service that fronts a database, or None."""
    meta = service.get("metadata") or {}
    ports = [port for port in (service.get("spec") or {}).get("ports") or []
             if isinstance(port, dict)]
    numbers = {int(port.get("port") or 0): str(port.get("name") or "") for port in ports}
    for number in POSTGRES_PORTS:
        if number in numbers:
            return "postgresql", number
    for number in MYSQL_PORTS:
        if number in numbers:
            return "mysql", number
    named = DB_NAME.search(str(meta.get("name") or "")) or any(
        DB_NAME.search(name) for name in numbers.values())
    if not named:
        return None
    for number, port_name in numbers.items():
        if number and number != MYSQL_X_PORT:
            mysql = MYSQL_NAME.search(str(meta.get("name") or "")) or MYSQL_NAME.search(port_name)
            return ("mysql" if mysql else "postgresql"), number
    return None


def from_services(services: list[dict[str, Any]], skip: set[tuple[str, str]]
                  ) -> list[dict[str, Any]]:
    """Database Services, as scan findings. ``skip`` names (namespace, service) pairs an
    operator's cluster already covers, like CloudNativePG's -ro and -r Services."""
    found = []
    for service in services:
        meta = service.get("metadata") or {}
        spec = service.get("spec") or {}
        name, namespace = str(meta.get("name") or ""), str(meta.get("namespace") or "default")
        if not name or (namespace, name) in skip:
            continue
        # A headless Service names the pods behind a normal one; the normal one is enough.
        if spec.get("clusterIP") == "None" or spec.get("type") == "ExternalName":
            continue
        engine_port = _engine_and_port(service)
        if engine_port is None:
            continue
        engine, port = engine_port
        found.append({
            "name": name, "engine": engine, "host": _host(name, namespace), "port": port,
            "provider": "kubernetes", "environment": "unknown",
            "note": f"Kubernetes Service {namespace}/{name}" +
            (" (PgBouncer)" if port == 6432 else ""),
            "found_by": f"Service {namespace}/{name} on port {port}"})
    return found


def _password_hint(namespace: str, secret: str) -> str:
    return (f"kubectl -n {namespace} get secret {secret} "
            "-o jsonpath='{.data.password}' | base64 -d")


def from_cnpg(clusters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                         set[tuple[str, str]]]:
    """CloudNativePG clusters, each through its read-write Service, plus the Services they
    cover so the plain Service list leaves them out."""
    found, covered = [], set()
    for cluster in clusters:
        meta = cluster.get("metadata") or {}
        spec = cluster.get("spec") or {}
        name, namespace = str(meta.get("name") or ""), str(meta.get("namespace") or "default")
        if not name:
            continue
        covered |= {(namespace, f"{name}-{suffix}") for suffix in ("rw", "ro", "r")}
        initdb = ((spec.get("bootstrap") or {}).get("initdb") or {})
        database = str(initdb.get("database") or "app")
        owner = str(initdb.get("owner") or database)
        secret = str((initdb.get("secret") or {}).get("name") or f"{name}-app")
        found.append({
            "name": name, "engine": "postgresql", "host": _host(f"{name}-rw", namespace),
            "port": 5432, "database": database, "user": owner, "provider": "kubernetes",
            "environment": "unknown", "note": f"CloudNativePG cluster {namespace}/{name}",
            "password_hint": _password_hint(namespace, secret),
            "found_by": f"CloudNativePG cluster {namespace}/{name}"})
    return found, covered


def from_zalando(clusters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                            set[tuple[str, str]]]:
    """Zalando postgres-operator clusters, through their primary Service."""
    found, covered = [], set()
    for cluster in clusters:
        meta = cluster.get("metadata") or {}
        spec = cluster.get("spec") or {}
        name, namespace = str(meta.get("name") or ""), str(meta.get("namespace") or "default")
        if not name:
            continue
        covered |= {(namespace, name), (namespace, f"{name}-repl"),
                    (namespace, f"{name}-config")}
        databases = spec.get("databases") or {}
        database, owner = next(iter(databases.items()), (None, None)) \
            if isinstance(databases, dict) else (None, None)
        item: dict[str, Any] = {
            "name": name, "engine": "postgresql", "host": _host(name, namespace), "port": 5432,
            "database": database, "user": owner, "provider": "kubernetes",
            "environment": "unknown", "note": f"Zalando Postgres cluster {namespace}/{name}",
            "found_by": f"Zalando Postgres cluster {namespace}/{name}"}
        if owner:
            secret = f"{str(owner).replace('_', '-')}.{name}.credentials.postgresql.acid.zalan.do"
            item["password_hint"] = _password_hint(namespace, secret)
        found.append(item)
    return found, covered


def scan_kubernetes(report: Callable[[str], None]) -> list[dict[str, Any]]:
    """Database Services and operator clusters RDST can see in this cluster."""
    allowed = namespaces()
    report(f"Services in {', '.join(allowed)}" if allowed else "Services in this cluster")
    try:
        services = _list("/api/v1", "services")
    except PermissionError as exc:
        raise DiscoveryNotAllowed(NOT_ALLOWED) from exc
    cnpg, cnpg_covers = from_cnpg(_optional("/apis/postgresql.cnpg.io/v1", "clusters"))
    if cnpg:
        report("CloudNativePG clusters")
    zalando, zalando_covers = from_zalando(_optional("/apis/acid.zalan.do/v1", "postgresqls"))
    if zalando:
        report("Zalando Postgres clusters")
    return cnpg + zalando + from_services(services, cnpg_covers | zalando_covers)
