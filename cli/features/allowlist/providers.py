"""Read-merge-write clients for managed-provider IP allowlists."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests

from features.providers.provider_common import REQUEST_TIMEOUT, read_secret

SUPPORTED_PROVIDERS = frozenset({"supabase", "neon", "digitalocean"})
SUPABASE_ACCESS_TOKEN = "SUPABASE_ACCESS_TOKEN"
logger = logging.getLogger(__name__)


class ProviderAllowlistError(RuntimeError):
    """A provider API read or write failed."""


@dataclass
class AllowlistState:
    provider: str
    entries: list[str]
    entry_count: int
    raw: dict[str, Any]


@dataclass
class AddIpResult:
    provider: str
    cidr: str
    wrote: bool
    previous_count: int
    verified: bool
    credential_method: Optional[str] = None


def _tag_value(target: dict[str, Any], prefix: str) -> Optional[str]:
    for tag in target.get("tags") or []:
        if isinstance(tag, str) and tag.startswith(prefix):
            value = tag[len(prefix) :]
            return value or None
    return None


def provider_for_target(target: dict[str, Any]) -> Optional[str]:
    provider = _tag_value(target, "provider:")
    return provider if provider in SUPPORTED_PROVIDERS else None


def credential_for_provider(provider: str) -> tuple[Optional[str], Optional[str]]:
    """Reuse each discovery provider's existing credential plumbing."""
    if provider == "supabase":
        from features.providers.supabase_oauth import get_access_token

        token, method = get_access_token()
        if token:
            return token, method
        fallback = read_secret(SUPABASE_ACCESS_TOKEN)
        return (fallback, "access_token") if fallback else (None, None)
    if provider == "neon":
        from features.providers.neon import get_api_key

        key = get_api_key()
        return (key, "api_key") if key else (None, None)
    if provider == "digitalocean":
        from features.providers.digitalocean_oauth import get_access_token

        return get_access_token()
    return None, None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _provider_detail(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for field in ("message", "error", "detail"):
            value = body.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return (response.text or "").strip()[:300]


def _require_success(
    response: requests.Response,
    *,
    provider: str,
    action: str,
    expected: set[int],
) -> None:
    detail = _provider_detail(response)
    logger.info(
        "%s allowlist API response while %s: status=%s detail=%s",
        provider,
        action,
        response.status_code,
        detail or "<empty>",
    )
    if response.status_code in expected:
        return
    suffix = f": {detail}" if detail else ""
    raise ProviderAllowlistError(
        f"{provider} API returned {response.status_code} while {action}{suffix}"
    )


def _response_object(response: requests.Response, provider: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderAllowlistError(
            f"{provider} API returned invalid JSON while reading the allowlist"
        ) from exc
    if not isinstance(body, dict):
        raise ProviderAllowlistError(
            f"{provider} API returned an invalid allowlist response"
        )
    return body


def _read_supabase(target: dict[str, Any], token: str) -> AllowlistState:
    from features.providers.supabase_oauth import API_BASE

    ref = _tag_value(target, "supabase-ref:")
    if not ref:
        raise ProviderAllowlistError("Target is missing its Supabase project reference")
    try:
        response = requests.get(
            f"{API_BASE}/v1/projects/{ref}/network-restrictions",
            headers=_headers(token),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(
            f"Could not read the Supabase allowlist: {exc}"
        ) from exc
    _require_success(
        response,
        provider="Supabase",
        action="reading network restrictions",
        expected={200},
    )
    body = _response_object(response, "Supabase")
    config = body.get("config")
    config = config if isinstance(config, dict) else {}
    ipv4 = [str(value) for value in config.get("dbAllowedCidrs") or []]
    ipv6 = [str(value) for value in config.get("dbAllowedCidrsV6") or []]
    return AllowlistState(
        provider="supabase",
        entries=[*ipv4, *ipv6],
        entry_count=len(ipv4) + len(ipv6),
        raw={"ref": ref, "ipv4": ipv4, "ipv6": ipv6},
    )


def _write_supabase(state: AllowlistState, token: str, cidr: str) -> None:
    from features.providers.supabase_oauth import API_BASE

    payload = {
        "dbAllowedCidrs": [*state.raw["ipv4"], cidr],
        "dbAllowedCidrsV6": state.raw["ipv6"],
    }
    try:
        response = requests.post(
            f"{API_BASE}/v1/projects/{state.raw['ref']}/network-restrictions/apply",
            headers=_headers(token),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(
            f"Could not update the Supabase allowlist: {exc}"
        ) from exc
    _require_success(
        response,
        provider="Supabase",
        action="applying network restrictions",
        expected={200, 201},
    )


def _read_neon(target: dict[str, Any], token: str) -> AllowlistState:
    from features.providers.neon import API_BASE

    project_id = _tag_value(target, "neon-project:")
    if not project_id:
        raise ProviderAllowlistError("Target is missing its Neon project ID")
    try:
        response = requests.get(
            f"{API_BASE}/projects/{project_id}",
            headers=_headers(token),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(f"Could not read the Neon allowlist: {exc}") from exc
    _require_success(
        response,
        provider="Neon",
        action="reading project settings",
        expected={200},
    )
    body = _response_object(response, "Neon")
    project = body.get("project")
    project = project if isinstance(project, dict) else body
    settings = project.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    allowed = settings.get("allowed_ips")
    allowed = allowed if isinstance(allowed, dict) else {}
    entries = [str(value) for value in allowed.get("ips") or []]
    return AllowlistState(
        provider="neon",
        entries=entries,
        entry_count=len(entries),
        raw={
            "project_id": project_id,
            "protected_branches_only": bool(
                allowed.get("protected_branches_only", False)
            ),
        },
    )


def _write_neon(state: AllowlistState, token: str, cidr: str) -> None:
    from features.providers.neon import API_BASE

    payload = {
        "project": {
            "settings": {
                "allowed_ips": {
                    "ips": [*state.entries, cidr],
                    "protected_branches_only": state.raw["protected_branches_only"],
                }
            }
        }
    }
    try:
        response = requests.patch(
            f"{API_BASE}/projects/{state.raw['project_id']}",
            headers=_headers(token),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(f"Could not update the Neon allowlist: {exc}") from exc
    _require_success(
        response,
        provider="Neon",
        action="updating project settings",
        expected={200},
    )


def _read_digitalocean(target: dict[str, Any], token: str) -> AllowlistState:
    from features.providers.digitalocean_oauth import API_BASE

    cluster_id = _tag_value(target, "do-cluster:")
    if not cluster_id:
        raise ProviderAllowlistError(
            "Target is missing its DigitalOcean database cluster ID"
        )
    try:
        response = requests.get(
            f"{API_BASE}/v2/databases/{cluster_id}/firewall",
            headers=_headers(token),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(
            f"Could not read the DigitalOcean trusted sources: {exc}"
        ) from exc
    _require_success(
        response,
        provider="DigitalOcean",
        action="reading trusted sources",
        expected={200},
    )
    body = _response_object(response, "DigitalOcean")
    rules = [rule for rule in body.get("rules") or [] if isinstance(rule, dict)]
    writable_rules = []
    entries = []
    for rule in rules:
        rule_type = rule.get("type")
        value = rule.get("value")
        if not isinstance(rule_type, str) or not isinstance(value, str):
            raise ProviderAllowlistError(
                "DigitalOcean returned a trusted-source rule without a type or value"
            )
        writable = {"type": rule_type, "value": value}
        if isinstance(rule.get("description"), str):
            writable["description"] = rule["description"]
        writable_rules.append(writable)
        if rule_type == "ip_addr":
            entries.append(value)
    return AllowlistState(
        provider="digitalocean",
        entries=entries,
        entry_count=len(rules),
        raw={"cluster_id": cluster_id, "rules": writable_rules},
    )


def _write_digitalocean(
    state: AllowlistState, token: str, cidr: str
) -> None:
    from features.providers.digitalocean_oauth import API_BASE

    payload = {
        "rules": [
            *state.raw["rules"],
            {"type": "ip_addr", "value": cidr},
        ]
    }
    url = f"{API_BASE}/v2/databases/{state.raw['cluster_id']}/firewall"

    try:
        response = requests.put(
            url,
            headers=_headers(token),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderAllowlistError(
            f"Could not update the DigitalOcean trusted sources: {exc}"
        ) from exc

    if response.status_code == 403:
        detail = _provider_detail(response)
        suffix = f": {detail}" if detail else ""
        raise ProviderAllowlistError(
            "DigitalOcean API returned 403 while updating trusted sources"
            f"{suffix}. Sign out and sign back in to DigitalOcean to grant "
            "RDST permission to update trusted sources."
        )
    _require_success(
        response,
        provider="DigitalOcean",
        action="updating trusted sources",
        expected={200, 204},
    )


def read_allowlist(target: dict[str, Any]) -> AllowlistState:
    provider = provider_for_target(target)
    if not provider:
        raise ProviderAllowlistError("Target is not tagged with a supported provider")
    token, _ = credential_for_provider(provider)
    if not token:
        raise ProviderAllowlistError(f"Not signed in to {provider}")
    if provider == "supabase":
        return _read_supabase(target, token)
    if provider == "neon":
        return _read_neon(target, token)
    return _read_digitalocean(target, token)


def ip_is_allowed(entries: list[str], ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    for raw in entries:
        value = raw.strip()
        try:
            if "-" in value:
                start, end = value.split("-", 1)
                if ipaddress.ip_address(start.strip()) <= address <= ipaddress.ip_address(
                    end.strip()
                ):
                    return True
            elif address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def add_ip(target: dict[str, Any], ip: str) -> AddIpResult:
    """Read, merge, then replace a provider allowlist. Never writes blind."""
    address = ipaddress.ip_address(ip)
    if address.version != 4:
        raise ProviderAllowlistError(
            "RDST can only add an IPv4 address to provider allowlists"
        )
    cidr = f"{address}/32"
    provider = provider_for_target(target)
    if not provider:
        raise ProviderAllowlistError("Target is not tagged with a supported provider")
    token, primary_credential_method = credential_for_provider(provider)
    if not token:
        raise ProviderAllowlistError(f"Not signed in to {provider}")

    # This successful read is mandatory. Every writer below replaces a full
    # provider list (or list-bearing setting), so no write path exists before it.
    state = read_allowlist(target)
    if ip_is_allowed(state.entries, str(address)):
        return AddIpResult(provider, cidr, False, state.entry_count, True)

    credential_method = (
        primary_credential_method if provider == "digitalocean" else None
    )
    if provider == "supabase":
        _write_supabase(state, token, cidr)
    elif provider == "neon":
        _write_neon(state, token, cidr)
    else:
        _write_digitalocean(state, token, cidr)

    # Provider writes can be asynchronous or deceptively successful. Treat the
    # write as complete only after the provider's own read API reports the IP.
    verified_state = read_allowlist(target)
    verified = ip_is_allowed(verified_state.entries, str(address))
    return AddIpResult(
        provider,
        cidr,
        True,
        state.entry_count,
        verified,
        credential_method,
    )
