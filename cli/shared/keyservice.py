"""Resolve the RDST keyservice URL.

The keyservice is currently not open-source.

Resolution order: the RDST_KEYSERVICE_URL environment variable, then the URL a
CL build bakes into the generated _version_build module, then production.
"""

from __future__ import annotations

import os

DEFAULT_KEYSERVICE_URL = "https://keyservice.readyset.io"
KEYSERVICE_URL_ENV_VAR = "RDST_KEYSERVICE_URL"


def _build_keyservice_url() -> str:
    # Only CL builds bake a URL; release builds and source checkouts have none.
    try:
        from _version_build import KEYSERVICE_URL
    except ImportError:
        return ""
    return KEYSERVICE_URL.strip()


def keyservice_base_url() -> str:
    override = os.environ.get(KEYSERVICE_URL_ENV_VAR, "").strip()
    return (override or _build_keyservice_url() or DEFAULT_KEYSERVICE_URL).rstrip("/")


def keyservice_url(path: str = "") -> str:
    if not path:
        return keyservice_base_url()
    sep = "" if path.startswith("/") else "/"
    return f"{keyservice_base_url()}{sep}{path}"


__all__ = [
    "DEFAULT_KEYSERVICE_URL",
    "KEYSERVICE_URL_ENV_VAR",
    "keyservice_base_url",
    "keyservice_url",
]
