"""Neon project discovery through the Neon API."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import requests

from features.fleet.models import FleetMember
from .provider_common import (
    REQUEST_TIMEOUT,
    bearer_get,
    forget_secret,
    json_items,
    provider_status,
    read_secret,
    secret_store,
    slugify,
    status_cache,
    unique_name,
)

API_BASE = "https://console.neon.tech/api/v2"

API_KEY_SECRET_NAME = "NEON_API_KEY"

# The account name is stable for the life of a key; the discovery panel polls
# status every couple of seconds.
_ACCOUNT = status_cache("neon")

# Neon paginates /projects with a default limit of 10.
PAGE_LIMIT = 100
MAX_PAGES = 100

DEFAULT_PORT = 5432
DEFAULT_DATABASE = "neondb"
DEFAULT_USER = "neondb_owner"

SIGNED_OUT_DETAIL = (
    "No Neon API key on this machine. Paste an API key from the Neon console, "
    f"or set {API_KEY_SECRET_NAME} in your environment."
)


def get_api_key() -> Optional[str]:
    """Return the Neon API key from the environment or the keyring."""
    return read_secret(API_KEY_SECRET_NAME)


def _get(
    path: str,
    key: str,
    params: Optional[dict[str, Any]] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> requests.Response:
    return bearer_get(f"{API_BASE}{path}", key, params=params, timeout=timeout)


def validate_key(key: str) -> tuple[bool, str]:
    """Check an API key against the projects endpoint before storing it."""
    try:
        response = _get("/projects", key, params={"limit": 1})
    except requests.RequestException as exc:
        return False, f"Could not reach the Neon API: {exc}"
    if response.status_code == 200:
        return True, "Key accepted by the Neon API"
    if response.status_code in (401, 403):
        return False, "Neon rejected this API key"
    return False, f"Neon API returned {response.status_code}"


def _account_name(key: str) -> Optional[str]:
    """Best-effort display name for the key's account."""
    try:
        response = _get("/users/me", key)
        if response.status_code != 200:
            return None
        body = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    for field in ("email", "name", "login"):
        value = body.get(field)
        if value:
            return str(value)
    return None


def _connected_detail(key: str, method: Optional[str], status: dict[str, Any]) -> str:
    account = _ACCOUNT.get(key, lambda: _account_name(key))
    return f"Using a Neon API key ({account})" if account else "Using a Neon API key"


def get_neon_status() -> dict[str, Any]:
    """Report whether this machine can talk to the Neon API."""
    return provider_status(
        api_name="Neon",
        credential=lambda: (get_api_key(), "api_key"),
        probe=lambda key: _get("/projects", key, params={"limit": 1}),
        signed_out_detail=SIGNED_OUT_DETAIL,
        rejected_detail="Neon rejected this API key; paste a new one",
        connected_detail=_connected_detail,
        # Neon OAuth is partner-gated, so the UI only ever offers key paste,
        # and organizations stay empty.
        extra={"organizations": []},
    )


def store_api_key(key: str) -> dict[str, Any]:
    """Persist a validated API key, reporting honestly when it cannot stick."""
    result = secret_store().set_secret(API_KEY_SECRET_NAME, key)
    forget_secret(API_KEY_SECRET_NAME)
    _ACCOUNT.clear()
    stored = bool(result.get("persisted"))
    detail = str(result.get("message") or "")
    if not stored:
        detail = (
            f"{detail} Export {API_KEY_SECRET_NAME} in your environment to keep it "
            "across restarts."
        ).strip()
    from .identity import capture_provider_identity_async

    capture_provider_identity_async("neon", key)
    return {"connected": True, "method": "api_key", "stored": stored, "detail": detail}


def logout() -> dict[str, Any]:
    """Clear the stored Neon API key."""
    try:
        secret_store().clear_required([API_KEY_SECRET_NAME])
    except Exception:
        os.environ.pop(API_KEY_SECRET_NAME, None)
    forget_secret(API_KEY_SECRET_NAME)
    _ACCOUNT.clear()
    return {"signed_out": True}


def _list_projects(key: str, errors: list[str]) -> Optional[list[dict[str, Any]]]:
    """Page through every project the key can see."""
    projects: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    seen_cursors: set[str] = set()

    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        try:
            response = _get("/projects", key, params=params)
        except requests.RequestException as exc:
            errors.append(f"Could not reach the Neon API: {exc}")
            return None
        if response.status_code != 200:
            errors.append(f"Neon API returned {response.status_code} listing projects")
            return None
        try:
            body = response.json()
        except ValueError:
            errors.append("Neon project listing was not valid JSON")
            return None
        if not isinstance(body, dict):
            errors.append("Neon project listing was not an object")
            return None

        page = [item for item in (body.get("projects") or []) if isinstance(item, dict)]
        projects.extend(page)

        pagination = body.get("pagination")
        next_cursor = ""
        if isinstance(pagination, dict):
            next_cursor = str(pagination.get("cursor") or "")
        if len(page) < PAGE_LIMIT or not next_cursor or next_cursor in seen_cursors:
            return projects
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    errors.append("Neon project listing stopped after the page limit")
    return projects


def _default_branch(key: str, project_id: str) -> Optional[dict[str, Any]]:
    response = _get(f"/projects/{project_id}/branches", key)
    if response.status_code != 200:
        return None
    for branch in json_items(response, "branches"):
        if branch.get("default") is True:
            return branch
    return None


def _read_write_endpoint(key: str, project_id: str, branch_id: str) -> Optional[dict[str, Any]]:
    response = _get(f"/projects/{project_id}/branches/{branch_id}/endpoints", key)
    if response.status_code != 200:
        return None
    for endpoint in json_items(response, "endpoints"):
        if endpoint.get("type") == "read_write":
            return endpoint
    return None


def _first_database(key: str, project_id: str, branch_id: str) -> Optional[dict[str, Any]]:
    response = _get(f"/projects/{project_id}/branches/{branch_id}/databases", key)
    if response.status_code != 200:
        return None
    databases = json_items(response, "databases")
    return databases[0] if databases else None


def _compute_units(value: Any) -> Optional[str]:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return None


def _instance_class(endpoint: dict[str, Any]) -> Optional[str]:
    """Autoscaling bounds as a size label; 1 CU is 1 vCPU and 4 GB."""
    minimum = _compute_units(endpoint.get("autoscaling_limit_min_cu"))
    maximum = _compute_units(endpoint.get("autoscaling_limit_max_cu"))
    if minimum is None or maximum is None:
        return None
    return f"neon-{minimum}-{maximum}cu"


def discover_neon_projects(errors: list[str]) -> list[FleetMember]:
    """List Neon projects as fleet members.

    Passwords are never fetched: members carry a per-project `password_env`
    for the credentials step to fill, the same as Supabase.
    """
    key = get_api_key()
    if key is None:
        errors.append("No Neon API key configured")
        return []

    projects = _list_projects(key, errors)
    if projects is None:
        return []

    listed = [project for project in projects if project.get("id")]

    # Each project needs three more API calls (branches, then endpoints and
    # databases); serially that dominates discovery time.
    def _project_details(project_id: str) -> dict[str, Any]:
        try:
            branch = _default_branch(key, project_id)
            if branch is None or not branch.get("id"):
                return {}
            branch_id = str(branch["id"])
            with ThreadPoolExecutor(max_workers=2) as calls:
                endpoint = calls.submit(_read_write_endpoint, key, project_id, branch_id)
                database = calls.submit(_first_database, key, project_id, branch_id)
                read_write = endpoint.result()
                if read_write is None:
                    return {"branch_id": branch_id}
                return {
                    "branch_id": branch_id,
                    "endpoint": read_write,
                    "database": database.result(),
                }
        except (requests.RequestException, ValueError) as exc:
            return {"error": str(exc)}

    project_ids = [str(project["id"]) for project in listed]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(project_ids)))) as pool:
        details = dict(zip(project_ids, pool.map(_project_details, project_ids)))

    members: list[FleetMember] = []
    used_names: set[str] = set()
    for project in listed:
        project_id = str(project["id"])
        project_name = str(project.get("name") or project_id)
        detail = details.get(project_id, {})

        if detail.get("error"):
            errors.append(f"Skipped '{project_name}': {detail['error']}")
            continue
        branch_id = detail.get("branch_id")
        if not branch_id:
            errors.append(f"Skipped '{project_name}': no default branch found")
            continue
        endpoint = detail.get("endpoint")
        if not endpoint:
            errors.append(f"Skipped '{project_name}': no read-write endpoint on the default branch")
            continue
        # Newer regions embed a cell identifier in the host, so it is used
        # verbatim rather than rebuilt from the endpoint id.
        host = str(endpoint.get("host") or "")
        if not host:
            errors.append(f"Skipped '{project_name}': read-write endpoint has no host")
            continue

        database = detail.get("database") or {}
        db_name = str(database.get("name") or "") or DEFAULT_DATABASE
        user = str(database.get("owner_name") or "") or DEFAULT_USER

        name = unique_name(
            f"neon-{slugify(project_name) or project_id}", used_names, slugify(project_id)
        )
        members.append(
            FleetMember(
                name=name,
                engine="postgresql",
                host=host,
                port=DEFAULT_PORT,
                database=db_name,
                user=user,
                # Neon projects have no cluster concept; like Supabase
                # projects they stay ungrouped.
                group=None,
                tags=[
                    "provider:neon",
                    f"neon-project:{project_id}",
                    f"neon-branch:{branch_id}",
                ],
                instance_class=_instance_class(endpoint),
                region=project.get("region_id"),
                tls=True,
            )
        )
    return members
