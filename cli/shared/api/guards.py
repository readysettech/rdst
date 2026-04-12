"""Shared request guards for local-only API endpoints."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _normalize_host(host: str) -> str:
    if host.startswith("::ffff:"):
        return host.split("::ffff:", 1)[1]
    return host


def is_loopback_request(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    host = _normalize_host(client.host or "")
    return host in _LOOPBACK_HOSTS


def same_host_from_headers(request: Request) -> bool:
    host_header = request.headers.get("host")
    expected_host = None
    if host_header:
        expected_host = urlsplit(f"http://{host_header}").hostname
        if expected_host:
            expected_host = _normalize_host(expected_host)

    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed_host = urlsplit(value).hostname
        if not parsed_host:
            return False
        parsed_host = _normalize_host(parsed_host)
        if parsed_host not in _LOOPBACK_HOSTS:
            return False
        if expected_host and parsed_host != expected_host:
            return False
    return True
