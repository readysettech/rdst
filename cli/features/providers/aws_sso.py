"""Read AWS SSO accounts and roles from a cached device-authorized token.

After `aws sso login` device-authorizes a session, the CLI caches an access
token under ~/.aws/sso/cache. That token alone lets the `sso` API list every
account and role the user may assume, so a profile can be built by picking from
those lists instead of hand-typing an account id and role name.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .aws_login import AwsSdkUnavailable, _load_botocore


NO_SESSION_MESSAGE = (
    "No active AWS SSO session for this start URL. Sign in first, then retry."
)

# Prefix for the deterministic sso-session that both the device login and
# every finalized profile share. Multiple profiles referencing one
# [sso-session] is exactly how AWS configs work.
SESSION_NAME_PREFIX = "rdst-sso-"


def sso_cache_dir() -> Path:
    return Path.home() / ".aws" / "sso" / "cache"


def stable_session_name(start_url: str) -> str:
    """Derive one deterministic sso-session name per (normalized) start URL.

    Login caches a token keyed to this name and every profile built from the
    same start URL references it, so botocore's credential provider finds the
    token it needs. Different start URLs hash to different sessions.
    """
    digest = hashlib.sha256(_normalize_start_url(start_url).encode("utf-8"))
    return f"{SESSION_NAME_PREFIX}{digest.hexdigest()[:16]}"


def session_token_path(session_name: str) -> Path:
    """The cache file botocore reads for an sso-session's OIDC token.

    The AWS CLI and botocore both key an sso-session token cache by
    sha1(session_name); matching that here checks the exact file the
    credential path will load.
    """
    key = hashlib.sha1(session_name.encode("utf-8")).hexdigest()
    return sso_cache_dir() / f"{key}.json"


def load_session_token(
    session_name: str,
) -> tuple[Optional[str], Optional[str]]:
    """Return the (accessToken, region) botocore would load for session_name.

    Reads only the session's own cache file and skips it when expired, so a
    token cached under a different session (a user's own login) never counts.
    """
    if not session_name:
        return None, None
    try:
        data = json.loads(session_token_path(session_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    token = data.get("accessToken")
    if not token or _is_expired(data.get("expiresAt")):
        return None, None
    return token, data.get("region")


def resolve_token(start_url: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a usable token, preferring the stable session's own cache.

    Account and role listing accept any valid token for the SSO instance, so
    this falls back to a normalized startUrl scan; credentials must instead go
    through the stable session (see load_session_token).
    """
    token, region = load_session_token(stable_session_name(start_url))
    if token:
        return token, region
    return find_cached_token(start_url)


def _normalize_start_url(url: str) -> str:
    """Reduce a start URL to a form that compares equal across CLI variants.

    Drops any `#`/`/#/` fragment, trims trailing slashes, and lowercases, so
    `https://d-x.awsapps.com/start/#/` and `https://D-X.awsapps.com/start`
    match the same cached token.
    """
    text = (url or "").strip()
    text = text.split("#", 1)[0]
    return text.rstrip("/").lower()


def _parse_expiry(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("UTC"):
        text = text[:-3] + "+00:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_expired(value: Any) -> bool:
    parsed = _parse_expiry(value)
    if parsed is None:
        # An unreadable expiry is left to the API to reject rather than hiding
        # a token that may still be valid.
        return False
    return parsed <= datetime.now(timezone.utc)


def find_cached_token(start_url: str) -> tuple[Optional[str], Optional[str]]:
    """Return the (accessToken, region) cached for start_url, or (None, None).

    Scans ~/.aws/sso/cache for a JSON file whose normalized startUrl matches
    and whose expiresAt is still in the future.
    """
    target = _normalize_start_url(start_url)
    cache_dir = sso_cache_dir()
    if not cache_dir.is_dir():
        return None, None

    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        token = data.get("accessToken")
        cached_url = data.get("startUrl")
        if not token or not cached_url:
            continue
        if _normalize_start_url(cached_url) != target:
            continue
        if _is_expired(data.get("expiresAt")):
            continue
        return token, data.get("region")
    return None, None


def _sso_client(region: Optional[str]):
    botocore_session, Config = _load_botocore()
    session = botocore_session.get_session()
    return session.create_client(
        "sso",
        region_name=region,
        config=Config(
            connect_timeout=5.0, read_timeout=10.0, retries={"max_attempts": 2}
        ),
    )


def _describe_error(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def list_accounts(start_url: str) -> tuple[list[dict[str, str]], Optional[str]]:
    """List accounts the cached token may assume, sorted by account name.

    Returns (accounts, error). On no token, expiry, or an API failure the list
    is empty and error is a human-readable string.
    """
    token, region = resolve_token(start_url)
    if not token:
        return [], NO_SESSION_MESSAGE
    try:
        client = _sso_client(region)
        result = client.list_accounts(accessToken=token)
    except AwsSdkUnavailable as exc:
        return [], str(exc)
    except Exception as exc:
        return [], _describe_error(exc)

    accounts = [
        {
            "account_id": entry.get("accountId", ""),
            "account_name": entry.get("accountName", ""),
        }
        for entry in result.get("accountList", [])
    ]
    accounts.sort(key=lambda item: (item["account_name"].lower(), item["account_id"]))
    return accounts, None


def list_account_roles(
    start_url: str, account_id: str
) -> tuple[list[str], Optional[str]]:
    """List role names available in one account, sorted case-insensitively.

    Returns (roles, error) with the same error convention as list_accounts.
    """
    token, region = resolve_token(start_url)
    if not token:
        return [], NO_SESSION_MESSAGE
    try:
        client = _sso_client(region)
        result = client.list_account_roles(accessToken=token, accountId=account_id)
    except AwsSdkUnavailable as exc:
        return [], str(exc)
    except Exception as exc:
        return [], _describe_error(exc)

    roles = [
        entry.get("roleName", "")
        for entry in result.get("roleList", [])
        if entry.get("roleName")
    ]
    roles.sort(key=str.lower)
    return roles, None
