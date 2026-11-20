"""Shared request guards for the RDST web API.

RDST normally runs on loopback, but ``rdst web --host 0.0.0.0`` is a supported
way to use the UI from another machine.  Loopback callers may be the CLI,
desktop shell, or a browser.  Remote callers must be a same-origin browser so a
page on another site cannot drive RDST's unauthenticated API.

The app-wide middlewares here run before any route: the Host allowlist keeps a
DNS-rebinding page (a foreign domain resolving to this machine) out entirely, and
the optional per-launch token restricts /api to the client that started RDST.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
from collections.abc import Iterable
from urllib.parse import SplitResult, urlsplit

from fastapi import HTTPException, Request

_LOOPBACK_HOSTS = {"localhost"}
_WILDCARD_HOSTS = {"", "0.0.0.0", "::"}
# Bind address and extra comma-separated Host names, set by `rdst web`.
WEB_HOST_ENV = "RDST_WEB_HOST"
WEB_ALLOWED_HOSTS_ENV = "RDST_WEB_ALLOWED_HOSTS"
WEB_PORT_ENV = "RDST_WEB_PORT"
# Per-launch API token for embedding clients (the desktop app). When set at server start,
# every /api request must carry it in TOKEN_HEADER.
API_TOKEN_ENV = "RDST_API_TOKEN"
TOKEN_HEADER = "x-rdst-token"
# OAuth callbacks that an external browser reaches by redirect, so they can't carry the token.
# Each one is bound to its own single-use state and still sits behind the Host allowlist.
TOKEN_EXEMPT_CALLBACKS = frozenset({
    "/api/account/oauth/callback",
    "/api/providers/oauth-return",
    "/api/providers/supabase-callback",
})


def _normalize_host(host: str) -> str:
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[1]
    return host


def is_loopback_host(host: str) -> bool:
    normalized = _normalize_host(host).lower().rstrip(".")
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def host_name(value: str) -> str | None:
    """The normalized name in a Host header value, or None when it is malformed."""
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or (value[end + 1:] and not value[end + 1:].startswith(":")):
            return None
        name = value[1:end]
    else:
        name, _, port = value.partition(":")
        if ":" in port:
            return None
    name = _normalize_host(name).lower().rstrip(".")
    return name or None


def configured_allowed_hosts() -> frozenset[str]:
    """Host names `rdst web` accepts besides loopback: its bind address, --allowed-host, and
    the Codespaces forwarded name when running in a Codespace."""
    names = [os.environ.get(WEB_HOST_ENV, "127.0.0.1"), codespaces_forwarded_host() or ""]
    names += os.environ.get(WEB_ALLOWED_HOSTS_ENV, "").split(",")
    return normalize_allowed_hosts(names)


def codespaces_forwarded_host() -> str | None:
    """The name GitHub Codespaces forwards this server's port under, inside a Codespace."""
    codespace = os.environ.get("CODESPACE_NAME", "").strip()
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").strip()
    port = os.environ.get(WEB_PORT_ENV, "").strip()
    if not (codespace and domain and port.isdecimal()):
        return None
    return f"{codespace}-{port}.{domain}"


def normalize_allowed_hosts(names: Iterable[str]) -> frozenset[str]:
    """Normalize names given as ``name``, ``name:port``, an IPv6 address, or ``[v6]:port``."""
    normalized = set()
    for value in names:
        value = value.strip()
        try:
            name = str(ipaddress.ip_address(value))
        except ValueError:
            name = host_name(value)
        if name and name not in _WILDCARD_HOSTS:
            normalized.add(name)
    return frozenset(normalized)


def is_allowed_host(
    host_header: str | None, allowed: frozenset[str], server_host: str | None = None
) -> bool:
    """Whether a Host header names this server rather than a DNS-rebinding domain.

    Loopback names are always accepted, as are the configured names and the IP address the
    connection was accepted on (a browser that typed the address, or the WSL listener). A
    rebinding page sends its own domain name, which matches none of these.
    """
    if host_header is None:
        # Browsers always send Host; only bare HTTP/1.0 tools omit it.
        return True
    name = host_name(host_header)
    if name is None:
        return False
    if is_loopback_host(name) or name in allowed:
        return True
    return server_host is not None and name == _normalize_host(server_host).lower()


async def _reject(scope, send, status: int, code: str, message: str) -> None:
    """Answer with the shared {code, message, detail} error envelope."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"code": code, "message": message, "detail": message}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value.decode("latin-1")
    return None


# Distinct rejected host names logged per server, so a flood of names can't grow the log set.
_MAX_LOGGED_HOSTS = 64


def _printable_host(host_header: str | None) -> str | None:
    """The rejected Host name for messages and logs, or None unless it is a plain host name."""
    name = host_name(host_header) if host_header else None
    if not name or len(name) > 253 or not re.fullmatch(r"[a-z0-9._:%-]+", name):
        return None
    return name


def host_rejection_text(name: str | None) -> str:
    """Plain-text 421 body telling an operator how to allow the name."""
    shown = name or "NAME"
    subject = f'the host name "{name}"' if name else "a host name"
    return (
        f"RDST rejected this request because it was addressed to {subject}, which this\n"
        "server doesn't answer to. RDST answers only to names it was started with, so other\n"
        "websites can't reach it through DNS rebinding.\n\n"
        "If you reach RDST through a reverse proxy, tunnel, or DNS name, restart it with:\n\n"
        f"    rdst web --allowed-host {shown}\n\n"
        f"or set {WEB_ALLOWED_HOSTS_ENV}={shown} (comma-separated for several names).\n"
    )


class HostAllowlistMiddleware:
    """Reject requests whose Host header doesn't name this server (DNS rebinding)."""

    def __init__(self, app, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self._logged: set[str] = set()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            server = scope.get("server")
            server_host = server[0] if server else None
            host = _header(scope, b"host")
            if not is_allowed_host(host, self.allowed_hosts, server_host):
                await self._reject(scope, send, _printable_host(host))
                return
        await self.app(scope, receive, send)

    async def _reject(self, scope, send, name: str | None) -> None:
        key = name or ""
        if key not in self._logged and len(self._logged) < _MAX_LOGGED_HOSTS:
            self._logged.add(key)
            # uvicorn's logger reaches the console (and journald under systemd).
            logging.getLogger("uvicorn.error").warning(
                "Rejected a request for host %r; to serve it, start rdst web with "
                "--allowed-host %s or set %s", name, name or "NAME", WEB_ALLOWED_HOSTS_ENV)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = host_rejection_text(name).encode()
        await send({"type": "http.response.start", "status": 421,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"x-content-type-options", b"nosniff"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class ApiTokenMiddleware:
    """Require the per-launch token on /api requests.

    Contract for embedding clients: start the server with RDST_API_TOKEN=<random secret> in
    its environment and send ``X-RDST-Token: <secret>`` on every /api request, including
    SSE streams. /health, /mcp, static UI files, and GET on TOKEN_EXEMPT_CALLBACKS stay
    token-free. CORS preflights are answered before this check.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            callback = scope.get("method") in ("GET", "HEAD") and path in TOKEN_EXEMPT_CALLBACKS
            if (path == "/api" or path.startswith("/api/")) and not callback:
                provided = (_header(scope, TOKEN_HEADER.encode()) or "").encode("latin-1")
                if not hmac.compare_digest(provided, self.token):
                    await _reject(scope, send, 401, "unauthorized",
                                  "Missing or invalid RDST API token")
                    return
        await self.app(scope, receive, send)


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
    from shared.server_mode import forwarded_scheme_and_host

    scheme, host = forwarded_scheme_and_host(request)
    if not host:
        return None
    return _origin(f"{scheme}://{host}")


def is_loopback_request(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    return is_loopback_host(client.host or "")


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
    from shared import server_mode

    if server_mode.bearer_authenticated(request):
        return True

    expected = _request_origin(request)
    expected_host = expected[1] if expected else ""
    host_is_loopback = bool(expected_host) and is_loopback_host(expected_host)
    if host_is_loopback and not is_loopback_request(request) and not server_mode.enabled():
        # A remote peer cannot become local by forging loopback Host/Origin
        # headers. This matters for non-browser clients and misconfigured
        # reverse proxies. A server in a container is reached on localhost
        # through a published port, and its access token, not the peer
        # address, is what admits a caller.
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
            if not is_loopback_host(source[1]) or source[1] != expected_host:
                return False
        elif source != expected:
            return False

    if not found_source and is_cross_origin_fetch(request):
        return False
    # Headerless calls are valid only from loopback (CLI, curl, readiness
    # probes). A remote request must prove it came from the page RDST served.
    return found_source or is_loopback_request(request)


def is_cross_origin_fetch(request: Request) -> bool:
    """Whether browser fetch metadata marks the request as coming from another page.

    A page can suppress Origin and Referer, but the browser still labels the request with
    Sec-Fetch-Site. Top-level GET navigations stay allowed: that is how an OAuth provider
    redirects the browser back to RDST, and the page that started it can't read the result.
    """
    site = request.headers.get("sec-fetch-site", "").lower()
    if site not in {"cross-site", "same-site"}:
        return False
    return not (
        request.method in {"GET", "HEAD"}
        and request.headers.get("sec-fetch-mode", "").lower() == "navigate"
        and request.headers.get("sec-fetch-dest", "").lower() == "document"
    )


def require_local_request(request: Request) -> None:
    """Allow a loopback caller or a same-origin remote RDST browser.

    Call this from inside the handler rather than through Depends, so
    request-body validation keeps its precedence over the guard.
    """
    if not same_host_from_headers(request):
        has_source = request.headers.get("origin") or request.headers.get("referer")
        if not has_source and is_cross_origin_fetch(request):
            detail = "Cross-site requests are not allowed"
        elif is_loopback_request(request):
            detail = "Origin/Referer host mismatch"
        elif not has_source:
            detail = "Remote requests require a same-origin Origin or Referer header"
        else:
            detail = "Origin/Referer does not match the RDST web address"
        raise HTTPException(status_code=403, detail=detail)
