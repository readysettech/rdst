"""Resolve the RDST keyservice URL.

The keyservice is currently not open-source but will be released under
BSL 1.1 alongside the CLI in the future.
"""

from __future__ import annotations

import os

DEFAULT_KEYSERVICE_URL = "https://rdst-keyservice.readysetio.workers.dev"
KEYSERVICE_URL_ENV_VAR = "RDST_KEYSERVICE_URL"


def keyservice_base_url() -> str:
    override = os.environ.get(KEYSERVICE_URL_ENV_VAR, "").strip()
    return (override or DEFAULT_KEYSERVICE_URL).rstrip("/")


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
