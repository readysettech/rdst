"""DigitalOcean managed database discovery through the DigitalOcean API."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests

from .digitalocean_oauth import (
    API_BASE,
    BROKER_PROVIDER,
    SIGNED_OUT_DETAIL,
    collect_outstanding_logins,
    get_access_token,
)
from features.fleet.models import FleetMember
from .provider_common import (
    bearer_get,
    password_env_for,
    provider_status,
    slugify,
    status_cache,
    unique_name,
)

# The account email is stable for the life of a session; the discovery panel
# polls status every couple of seconds.
_ACCOUNT = status_cache(BROKER_PROVIDER)

ACCOUNT_PATH = "/v2/account"
DATABASES_PATH = "/v2/databases"

# DigitalOcean paginates with ?page=N&per_page=N and caps per_page at 200.
PER_PAGE = 100
MAX_PAGES = 100

DEFAULT_PORT = 25060

# Only the relational engines map onto rdst targets; the rest of the managed
# database catalog (redis, valkey, mongodb, kafka, opensearch) is skipped.
ENGINES = {
    "pg": "postgresql",
    "advanced_pg": "postgresql",
    "mysql": "mysql",
    "advanced_mysql": "mysql",
}


def _get(url: str, token: str, params: Optional[dict[str, Any]] = None) -> requests.Response:
    return bearer_get(url, token, params=params)


def _account_email(token: str) -> Optional[str]:
    """Best-effort account email; the account endpoint is outside OAuth scope."""
    try:
        response = _get(f"{API_BASE}{ACCOUNT_PATH}", token)
        if response.status_code != 200:
            return None
        body = response.json()
    except (requests.RequestException, ValueError):
        return None
    account = body.get("account") if isinstance(body, dict) else None
    if isinstance(account, dict) and account.get("email"):
        return str(account["email"])
    return None


def _connected_detail(token: str, method: Optional[str], status: dict[str, Any]) -> str:
    email = _ACCOUNT.get(token, lambda: _account_email(token))
    return (
        f"Signed in with DigitalOcean OAuth ({email})"
        if email
        else "Signed in with DigitalOcean OAuth"
    )


def get_digitalocean_status() -> dict[str, Any]:
    """Report whether this machine can talk to the DigitalOcean API."""
    return provider_status(
        api_name="DigitalOcean",
        credential=get_access_token,
        # Probe with the databases endpoint: OAuth tokens carry database:*
        # scopes only, and /v2/account is outside them (it 403s a perfectly
        # good token).
        probe=lambda token: _get(f"{API_BASE}{DATABASES_PATH}?per_page=1", token),
        signed_out_detail=SIGNED_OUT_DETAIL,
        rejected_detail="DigitalOcean rejected the session credentials; sign in again",
        connected_detail=_connected_detail,
        collect=collect_outstanding_logins,
    )


def _next_page(body: dict[str, Any]) -> str:
    links = body.get("links")
    pages = links.get("pages") if isinstance(links, dict) else None
    if isinstance(pages, dict):
        return str(pages.get("next") or "")
    return ""


def _reported_total(body: dict[str, Any]) -> Optional[int]:
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return None
    try:
        return int(meta["total"])
    except (KeyError, TypeError, ValueError):
        return None


def _list_databases(token: str, errors: list[str]) -> Optional[list[dict[str, Any]]]:
    """Page through every database cluster the token can see."""
    clusters: list[dict[str, Any]] = []
    url = f"{API_BASE}{DATABASES_PATH}"
    params: Optional[dict[str, Any]] = {"per_page": PER_PAGE}
    seen_pages: set[str] = set()

    for _ in range(MAX_PAGES):
        try:
            response = _get(url, token, params=params)
        except requests.RequestException as exc:
            errors.append(f"Could not reach the DigitalOcean API: {exc}")
            return None
        if response.status_code != 200:
            errors.append(
                f"DigitalOcean API returned {response.status_code} listing databases"
            )
            return None
        try:
            body = response.json()
        except ValueError:
            errors.append("DigitalOcean database listing was not valid JSON")
            return None
        if not isinstance(body, dict):
            errors.append("DigitalOcean database listing was not an object")
            return None

        clusters.extend(
            item for item in (body.get("databases") or []) if isinstance(item, dict)
        )

        total = _reported_total(body)
        next_page = _next_page(body)
        if not next_page or next_page in seen_pages:
            return clusters
        if total is not None and len(clusters) >= total:
            return clusters
        seen_pages.add(next_page)
        # The next link carries its own page and per_page query.
        url = next_page
        params = None

    errors.append("DigitalOcean database listing stopped after the page limit")
    return clusters


def _connection_fields(cluster: dict[str, Any]) -> dict[str, Any]:
    """Host, port, user and database for a cluster.

    The connection URI is the source of truth: `connection.database` is empty
    on live clusters even when the URI names the database. Discrete fields
    only fill gaps the URI leaves. The password DigitalOcean returns alongside
    them is deliberately ignored; the credentials step stays user-driven.
    """
    connection = cluster.get("connection")
    connection = connection if isinstance(connection, dict) else {}

    host = ""
    port: Optional[int] = None
    user = ""
    database = ""

    uri = str(connection.get("uri") or "")
    if uri:
        parsed = urlparse(uri)
        host = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError:
            port = None
        user = unquote(parsed.username or "")
        database = unquote((parsed.path or "").lstrip("/"))

    if not host:
        host = str(connection.get("host") or "")
    if port is None:
        try:
            port = int(connection.get("port"))
        except (TypeError, ValueError):
            port = None
    if not user:
        user = str(connection.get("user") or "")
    if not database:
        database = str(connection.get("database") or "")

    return {
        "host": host,
        "port": port or DEFAULT_PORT,
        "user": user,
        "database": database,
    }


def discover_digitalocean_clusters(errors: list[str]) -> list[FleetMember]:
    """List DigitalOcean managed database clusters as fleet members.

    Passwords are never carried over: DigitalOcean returns one in the cluster
    payload, but members only get a per-cluster `password_env` for the
    credentials step to fill, the same as the other providers.
    """
    token, _ = get_access_token()
    if token is None:
        errors.append("Not signed in to DigitalOcean")
        return []

    clusters = _list_databases(token, errors)
    if clusters is None:
        return []

    members: list[FleetMember] = []
    used_names: set[str] = set()
    for cluster in clusters:
        cluster_id = str(cluster.get("id") or "")
        cluster_name = str(cluster.get("name") or cluster_id)
        engine = ENGINES.get(str(cluster.get("engine") or "").lower())
        if engine is None:
            continue
        if not cluster_id:
            errors.append(f"Skipped '{cluster_name}': cluster has no id")
            continue

        fields = _connection_fields(cluster)
        if not fields["host"]:
            errors.append(f"Skipped '{cluster_name}': no connection details available")
            continue

        name = unique_name(
            f"do-{slugify(cluster_name) or slugify(cluster_id)}", used_names, cluster_id[:6]
        )
        members.append(
            FleetMember(
                name=name,
                engine=engine,
                host=fields["host"],
                port=fields["port"],
                database=fields["database"],
                user=fields["user"],
                password_env=password_env_for(f"do-{cluster_name or cluster_id}"),
                # DigitalOcean clusters carry their replicas internally, so
                # like Supabase and Neon projects they stay ungrouped.
                group=None,
                tags=["provider:digitalocean", f"do-cluster:{cluster_id}"],
                instance_class=str(cluster["size"]) if cluster.get("size") else None,
                region=str(cluster["region"]) if cluster.get("region") else None,
                tls=True,
            )
        )
    return members
