"""Shared request guards for the RDST web API.

RDST normally runs on loopback, but ``rdst web --host 0.0.0.0`` is a supported
way to use the UI from another machine.  Loopback callers may be the CLI,
desktop shell, or a browser.  Remote callers must be a same-origin browser so a
page on another site cannot drive RDST's unauthenticated API.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urlsplit

from fastapi import HTTPException, Request

_LOOPBACK_HOSTS = {"localhost"}


def _normalize_host(host: str) -> str:
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[1]
    return host


def _is_loopback_host(host: str) -> bool:
    normalized = _normalize_host(host).lower().rstrip(".")
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _effective_port(parsed: SplitResult) -> int | None:
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError:
        return None
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _origin(value: str) -> tuple[str, str, int] | None:
    """Return a normalized HTTP origin, rejecting malformed header values."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = _effective_port(parsed)
    if port is None:
        return None
    return parsed.scheme, _normalize_host(parsed.hostname).lower().rstrip("."), port


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    host = request.headers.get("host")
    if not host:
        return None
    return _origin(f"{request.url.scheme}://{host}")


def is_loopback_request(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    return _is_loopback_host(client.host or "")


def same_host_from_headers(request: Request) -> bool:
    """Return whether browser source headers identify this RDST server.

    A loopback browser source must use the same host spelling as the request,
    while its port may differ (for example, the Vite development server and
    Python API). Headerless loopback calls remain valid for CLI and readiness
    clients.

    For a remote browser the source must match the request's scheme, host, and
    port exactly.  This supports the same-origin UI served by ``rdst web`` while
    rejecting cross-site browser requests.
    """
    expected = _request_origin(request)
    expected_host = expected[1] if expected else ""
    host_is_loopback = bool(expected_host) and _is_loopback_host(expected_host)
    if host_is_loopback and not is_loopback_request(request):
        # A remote peer cannot become local by forging loopback Host/Origin
        # headers. This matters for non-browser clients and misconfigured
        # reverse proxies.
        return False

    found_source = False
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        found_source = True
        source = _origin(value)
        if not source or not expected:
            return False
        if host_is_loopback:
            if not _is_loopback_host(source[1]) or source[1] != expected_host:
                return False
        elif source != expected:
            return False

    # Headerless calls are valid only from loopback (CLI, curl, readiness
    # probes). A remote request must prove it came from the page RDST served.
    return found_source or is_loopback_request(request)


def require_local_request(request: Request) -> None:
    """Allow a loopback caller or a same-origin remote RDST browser.

    Call this from inside the handler rather than through Depends, so
    request-body validation keeps its precedence over the guard.
    """
    if not same_host_from_headers(request):
        if is_loopback_request(request):
            detail = "Origin/Referer host mismatch"
        elif not request.headers.get("origin") and not request.headers.get("referer"):
            detail = "Remote requests require a same-origin Origin or Referer header"
        else:
            detail = "Origin/Referer does not match the RDST web address"
        raise HTTPException(status_code=403, detail=detail)
