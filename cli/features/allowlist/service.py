"""Provider allowlist context, diagnosis, and explicit write-back."""

from __future__ import annotations

import ipaddress
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from shared.config.targets import TargetsConfig

from .providers import (
    ProviderAllowlistError,
    add_ip,
    credential_for_provider,
    ip_is_allowed,
    provider_for_target,
    read_allowlist,
)

PROVIDER_IP_BLOCKED_MAYBE = "provider_ip_blocked_maybe"
PUBLIC_IP_ENDPOINTS = (
    "https://checkip.amazonaws.com",
    "https://api.ipify.org",
)
PUBLIC_IP_TIMEOUT = 3.0

GUIDANCE = {
    "supabase": (
        "Supabase network restrictions may be blocking this machine's direct "
        "Postgres or pooler connection."
    ),
    "neon": (
        "Neon IP Allow may be blocking this machine from the project's database branches."
    ),
    "digitalocean": (
        "DigitalOcean trusted sources may be blocking this machine from the database cluster."
    ),
}

_NETWORK_FAILURE = re.compile(
    r"could not connect|connection (?:failed|refused|timed out)|timeout|timed out|"
    r"no route to host|network is unreachable|network unreachable|"
    r"not allowed to connect|ip address is not allowed|host .+ is not allowed",
    re.IGNORECASE,
)
_PROVIDER_REFUSAL = {
    "supabase": re.compile(
        r"address\s+not\s+allowed|address\s+is\s+not\s+in\s+the\s+allowed\s+list|"
        r"not\s+in\s+the\s+allowed\s+list|supavisor|"
        r"eaddrnotallowed|not\s+in\s+tenant\s+allow_list|allow_list",
        re.IGNORECASE,
    ),
    "neon": re.compile(
        r"ip\s+address\s+is\s+not\s+allowed|address\s+not\s+allowed|ip\s+allow",
        re.IGNORECASE,
    ),
    "digitalocean": re.compile(
        r"connection refused|connection timed out|timeout|timed out",
        re.IGNORECASE,
    ),
}
_AUTH_FAILURE = re.compile(
    r"authentication failed|password authentication failed|access denied|"
    r"invalid password|unknown user",
    re.IGNORECASE,
)
_WRITE_LOCK = threading.Lock()


class AllowlistError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        status_code: int,
        *,
        verified: Optional[bool] = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.verified = verified


def get_public_ip() -> str:
    """Fetch this request's public IPv4 address, with one fallback."""
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            response = requests.get(url, timeout=PUBLIC_IP_TIMEOUT)
            response.raise_for_status()
            address = ipaddress.ip_address(response.text.strip())
            if address.version != 4:
                raise ValueError("the service returned an IPv6 address")
            return str(address)
        except (requests.RequestException, ValueError):
            continue
    raise AllowlistError(
        "public_ip_unavailable",
        "Could not determine this machine's public IPv4 address. "
        "Check the internet connection and try again.",
        502,
    )


def connection_failure_category(
    target: dict[str, Any], error: object
) -> Optional[str]:
    """Map provider connectivity refusals to one shared backend category."""
    # A tunneled connection originates at the jump host, so adding the local
    # machine's public IP would not repair that path.
    provider = provider_for_target(target)
    if target.get("ssh") or not provider:
        return None
    message = str(error)
    if _AUTH_FAILURE.search(message):
        return None
    if _NETWORK_FAILURE.search(message) or _PROVIDER_REFUSAL[provider].search(message):
        return PROVIDER_IP_BLOCKED_MAYBE
    return None


def is_provider_network_failure(target: dict[str, Any], error: object) -> bool:
    """Compatibility predicate backed by the centralized category mapper."""
    return connection_failure_category(target, error) is not None


def provider_network_hint(target: dict[str, Any]) -> str:
    provider = provider_for_target(target)
    if not provider:
        return ""
    return (
        f"{GUIDANCE[provider]} Open the IP allowlist helper to add this "
        "machine's public IP."
    )


def _target(config: TargetsConfig, target_name: str) -> dict[str, Any]:
    target = config.get(target_name)
    if target is None:
        raise AllowlistError(
            "target_not_found", f"Target '{target_name}' was not found.", 404
        )
    if not provider_for_target(target):
        raise AllowlistError(
            "provider_not_supported",
            f"Target '{target_name}' is not tagged as Supabase, Neon, or DigitalOcean.",
            400,
        )
    return target


def allowlist_context(config: TargetsConfig, target_name: str) -> dict[str, Any]:
    target = _target(config, target_name)
    provider = provider_for_target(target) or ""
    current_ip = get_public_ip()
    token, _ = credential_for_provider(provider)
    signed_in = token is not None
    already_allowed: Optional[bool] = None
    entry_count: Optional[int] = None
    error: Optional[str] = None

    if signed_in:
        try:
            state = read_allowlist(target)
            already_allowed = ip_is_allowed(state.entries, current_ip)
            entry_count = state.entry_count
        except ProviderAllowlistError as exc:
            error = str(exc)

    return {
        "provider": provider,
        "signed_in": signed_in,
        "current_ip": current_ip,
        "already_allowed": already_allowed,
        "entry_count": entry_count,
        "guidance": GUIDANCE[provider],
        "error": error,
    }


def add_current_ip(
    config: TargetsConfig,
    target_name: str,
    expected_ip: Optional[str] = None,
) -> dict[str, Any]:
    target = _target(config, target_name)
    provider = provider_for_target(target) or ""
    token, _ = credential_for_provider(provider)
    if not token:
        raise AllowlistError(
            "provider_sign_in_required",
            f"Connect {provider} from Settings, then try adding this IP again.",
            401,
        )

    with _WRITE_LOCK:
        current_ip = get_public_ip()
        if expected_ip and current_ip != expected_ip:
            raise AllowlistError(
                "public_ip_changed",
                f"This machine's public IP changed from {expected_ip} to "
                f"{current_ip}. Review and confirm the new address before writing.",
                409,
            )
        try:
            result = add_ip(target, current_ip)
        except ProviderAllowlistError as exc:
            raise AllowlistError("provider_api_failure", str(exc), 502) from exc

        if not result.verified:
            raise AllowlistError(
                "allowlist_verification_failed",
                f"{provider} accepted the allowlist update, but a follow-up read "
                f"did not contain {result.cidr}. No change was recorded by RDST.",
                502,
                verified=False,
            )

        if result.wrote:
            target.setdefault("rdst_added_allowlist", []).append(
                {
                    "ip": result.cidr,
                    "provider": provider,
                    "added_at": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
            )
            config.upsert(target_name, target)
            try:
                config.save()
            except Exception as exc:
                raise AllowlistError(
                    "allowlist_record_failed",
                    f"{result.cidr} was added to {provider}, but RDST could not "
                    "record the change in the target config.",
                    500,
                ) from exc
            message = (
                f"Added {result.cidr} to the {provider} allowlist. "
                f"Kept {result.previous_count} existing entries."
            )
            category = "allowlist_updated"
        else:
            message = f"{current_ip} is already on the {provider} allowlist."
            category = "already_allowed"

    return {
        "ok": True,
        "added_ip": result.cidr,
        "message": message,
        "category": category,
        "verified": result.verified,
        "credential_method": result.credential_method,
    }
