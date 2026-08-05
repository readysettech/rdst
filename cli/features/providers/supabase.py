"""Supabase project discovery through the Management API."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import requests

from features.fleet.models import FleetMember
from .provider_common import (
    bearer_get,
    json_items,
    provider_status,
    slugify,
    status_cache,
    unique_name,
)
from .supabase_oauth import (
    API_BASE,
    BROKER_PROVIDER,
    REQUEST_TIMEOUT,
    SIGNED_OUT_DETAIL,
    collect_outstanding_logins,
    get_access_token,
)

# Organizations are stable for the life of a credential; the discovery panel
# polls status every couple of seconds.
_ORGANIZATIONS = status_cache(BROKER_PROVIDER)

HEALTHY_STATUS = "ACTIVE_HEALTHY"
# Supavisor session mode. The transaction pooler on 6543 drops prepared
# statements and session state that query analysis depends on.
POOLER_PORT = 5432


def _get(path: str, token: str, timeout: float = REQUEST_TIMEOUT) -> requests.Response:
    return bearer_get(f"{API_BASE}{path}", token, timeout=timeout)


def _fetch_organizations(token: str) -> list[dict[str, Any]]:
    """Raw organization listing; empty when the call fails or is not allowed."""
    try:
        response = _get("/v1/organizations", token)
        if response.status_code != 200:
            return []
        return json_items(response)
    except (requests.RequestException, ValueError):
        return []


def _organizations(token: str) -> list[dict[str, str]]:
    """List organizations as {slug, name} for the status payload."""
    listed: list[dict[str, str]] = []
    for organization in _fetch_organizations(token):
        slug = organization.get("slug") or organization.get("id")
        if slug:
            listed.append({"slug": str(slug), "name": organization.get("name") or str(slug)})
    return listed


def _connected_detail(token: str, method: Optional[str], status: dict[str, Any]) -> str:
    status["organizations"] = _ORGANIZATIONS.get(token, lambda: _organizations(token))
    return "Signed in with Supabase OAuth"


def get_supabase_status() -> dict[str, Any]:
    """Report whether this machine can talk to the Supabase Management API."""
    return provider_status(
        api_name="Supabase",
        credential=get_access_token,
        probe=lambda token: _get("/v1/projects", token),
        signed_out_detail=SIGNED_OUT_DETAIL,
        rejected_detail="Supabase rejected the session credentials; sign in again",
        connected_detail=_connected_detail,
        extra={"organizations": []},
        collect=collect_outstanding_logins,
    )


def _pooler_primary(entries: Any) -> Optional[dict[str, Any]]:
    """Pick the PRIMARY entry from a pooler config response."""
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None
    candidates = [entry for entry in entries if isinstance(entry, dict)]
    for entry in candidates:
        if str(entry.get("database_type", "")).upper() == "PRIMARY":
            return entry
    return candidates[0] if candidates else None


def _pooler_config(token: str, ref: str) -> Optional[dict[str, Any]]:
    """The PRIMARY pooler entry for a project, or None when it is unavailable."""
    try:
        response = _get(f"/v1/projects/{ref}/config/database/pooler", token)
        if response.status_code != 200:
            return None
        return _pooler_primary(response.json())
    except (requests.RequestException, ValueError):
        return None


def _instance_class(token: str, ref: str) -> Optional[str]:
    """Best-effort compute size; the addons endpoint may be PAT-only."""
    try:
        response = _get(f"/v1/projects/{ref}/billing/addons", token)
        if response.status_code != 200:
            return None
        addons = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(addons, dict):
        return None
    for addon in addons.get("selected_addons") or []:
        if not isinstance(addon, dict) or addon.get("type") != "compute_instance":
            continue
        variant = addon.get("variant")
        if not isinstance(variant, dict):
            continue
        raw = variant.get("name") or variant.get("identifier") or variant.get("id")
        slug = slugify(str(raw or ""))
        if not slug:
            continue
        slug = re.sub(r"^ci-", "", slug)
        return slug if slug.startswith("supabase-") else f"supabase-{slug}"
    return None


def discover_supabase_projects(errors: list[str]) -> list[FleetMember]:
    """List healthy Supabase projects as fleet members.

    Passwords are never fetched: Supabase does not expose them, so members
    carry a per-project `password_env` for the credentials step to fill.
    """
    token, _ = get_access_token()
    if token is None:
        errors.append("Not signed in to Supabase")
        return []

    try:
        response = _get("/v1/projects", token)
    except requests.RequestException as exc:
        errors.append(f"Could not reach the Supabase API: {exc}")
        return []
    if response.status_code in (401, 403):
        errors.append(
            "Supabase rejected the session while listing projects; "
            "sign in again and retry"
        )
        return []
    if response.status_code != 200:
        errors.append(f"Supabase API returned {response.status_code} listing projects")
        return []
    try:
        projects = json_items(response)
    except ValueError:
        errors.append("Supabase project listing was not valid JSON")
        return []

    org_slugs = {
        str(organization.get("id")): str(organization.get("slug") or organization.get("id"))
        for organization in _fetch_organizations(token)
        if organization.get("id")
    }

    healthy: list[dict[str, Any]] = []
    for project in projects:
        ref = str(project.get("id") or "")
        project_name = str(project.get("name") or ref)
        if not ref:
            continue
        status = str(project.get("status") or "")
        if status != HEALTHY_STATUS:
            errors.append(f"Skipped '{project_name}': project status is {status or 'unknown'}")
            continue
        healthy.append(project)

    # Each project needs two more API calls (pooler config, compute size);
    # serially that dominates discovery time, so fetch them concurrently and
    # run the projects themselves concurrently too.
    def _project_details(ref: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        with ThreadPoolExecutor(max_workers=2) as calls:
            primary = calls.submit(_pooler_config, token, ref)
            instance_class = calls.submit(_instance_class, token, ref)
            return primary.result(), instance_class.result()

    refs = [str(project.get("id")) for project in healthy]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(refs)))) as pool:
        details = dict(zip(refs, pool.map(_project_details, refs)))

    members: list[FleetMember] = []
    used_names: set[str] = set()
    for project in healthy:
        ref = str(project.get("id") or "")
        project_name = str(project.get("name") or ref)

        database = project.get("database") if isinstance(project.get("database"), dict) else {}
        direct_host = str(database.get("host") or "")

        primary, instance_class = details.get(ref, (None, None))
        host = ""
        user = ""
        db_name = ""
        if primary is not None:
            host = str(primary.get("db_host") or "")
            user = str(primary.get("db_user") or "")
            db_name = str(primary.get("db_name") or "")

        if not host:
            # The direct host is AAAA-only on most projects, so this fallback
            # can be unreachable from IPv4-only machines.
            if not direct_host:
                errors.append(f"Skipped '{project_name}': no pooler or direct host available")
                continue
            errors.append(
                f"'{project_name}': pooler config unavailable, using the direct host "
                f"{direct_host} (IPv6-only unless the IPv4 add-on is enabled)"
            )
            host = direct_host
            user = "postgres"
            db_name = "postgres"

        name = unique_name(f"supabase-{slugify(project_name) or ref}", used_names, ref[:6])

        tags = ["provider:supabase", f"supabase-ref:{ref}"]
        if direct_host:
            tags.append(f"direct-host:{direct_host}")

        organization_id = str(project.get("organization_id") or "")
        org_slug = org_slugs.get(organization_id, organization_id)
        if org_slug:
            tags.append(f"supabase-org:{org_slug}")
        members.append(
            FleetMember(
                name=name,
                engine="postgresql",
                host=host,
                port=POOLER_PORT,
                database=db_name or "postgres",
                user=user or f"postgres.{ref}",
                # Supabase projects have no cluster concept; like standalone
                # RDS instances they stay ungrouped. The org rides in a tag.
                group=None,
                tags=tags,
                instance_class=instance_class,
                region=project.get("region"),
                tls=True,
            )
        )
    return members
