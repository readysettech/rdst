"""Turn the connection details a scan or an agent found into ones that work from here.

A provider's connection string doesn't always work from the user's network. Supabase's direct
host, db.<ref>.supabase.co, is IPv6-only on most projects, so an IPv4-only network can't
reach it; its session pooler can. RDST looks the pooler up instead of asking the user or the
agent to guess: the Management API knows it, and without a token the pooler that knows the
project is the one that doesn't say "tenant or user not found".
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPABASE_DIRECT = re.compile(r"^db\.([a-z0-9]{20})\.supabase\.co$")
# Session mode: the transaction pooler (6543) drops the prepared statements and session state
# query analysis needs.
SESSION_PORT = 5432
# Supabase runs aws-0 and aws-1 pooler clusters per region; the API names the right one.
REGIONS = ("us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "eu-west-1",
           "eu-west-2", "eu-west-3", "eu-central-1", "eu-central-2", "eu-north-1",
           "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
           "ap-northeast-2", "sa-east-1")
WRONG_CLUSTER = re.compile(r"tenant or user not found|tenant/user .* not found", re.IGNORECASE)
PROBE_TIMEOUT_SECONDS = 6


def supabase_ref(host: str | None) -> str | None:
    match = SUPABASE_DIRECT.match(str(host or "").lower())
    return match.group(1) if match else None


def _token(near: list[str]) -> str | None:
    """A Supabase access token: the project's own env files, the environment, or the user's
    Supabase sign-in in RDST."""
    from features.agent_connect.scan import parse_dotenv

    for path in near:
        try:
            values = parse_dotenv(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if values.get("SUPABASE_ACCESS_TOKEN"):
            return values["SUPABASE_ACCESS_TOKEN"]
    if os.environ.get("SUPABASE_ACCESS_TOKEN"):
        return os.environ["SUPABASE_ACCESS_TOKEN"]
    try:
        from features.providers.supabase_oauth import get_access_token

        token, _method = get_access_token()
        return token
    except Exception:
        logger.debug("No Supabase sign-in to look the pooler up with", exc_info=True)
        return None


def _probe(host: str, ref: str, fields: dict[str, Any]) -> str:
    """Whether ``host`` is the pooler that knows this project: "yes", "no" (wrong cluster)
    or "unknown"."""
    import psycopg2

    try:
        conn = psycopg2.connect(host=host, port=SESSION_PORT, user=f"postgres.{ref}",
                                password=fields.get("password") or "",
                                dbname=fields.get("database") or "postgres",
                                sslmode="require", connect_timeout=PROBE_TIMEOUT_SECONDS)
        conn.close()
        return "yes"
    except Exception as exc:
        text = str(exc)
        if WRONG_CLUSTER.search(text):
            return "no"
        # It knows the project but refused the login: still the right pooler.
        return "yes" if "password authentication failed" in text else "unknown"


def supabase_pooler(fields: dict[str, Any], near: list[str]) -> dict[str, Any] | None:
    """The same Supabase database through its session pooler, or None when RDST can't tell
    which pooler serves it."""
    ref = supabase_ref(fields.get("host"))
    if ref is None:
        return None
    token = _token(near)
    host = None
    if token:
        from features.providers.supabase import _pooler_config

        primary = _pooler_config(token, ref) or {}
        host = str(primary.get("db_host") or "") or None
    if host is None:
        candidates = [f"aws-{cluster}-{region}.pooler.supabase.com"
                      for cluster in (0, 1) for region in REGIONS]
        with ThreadPoolExecutor(max_workers=12) as pool:
            answers = dict(zip(candidates, pool.map(lambda h: _probe(h, ref, fields),
                                                    candidates)))
        host = next((h for h in candidates if answers[h] == "yes"), None)
    if host is None:
        return None
    return {**fields, "host": host, "port": SESSION_PORT, "user": f"postgres.{ref}",
            "tls": True}
