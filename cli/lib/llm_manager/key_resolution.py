"""Resolve API key and determine routing (direct vs trial proxy).

RDST routes LLM requests based on key type:
  - Own Anthropic key (env var) -> direct to api.anthropic.com
  - Trial token (config.toml)  -> route to rdst-keyservice.readysetio.workers.dev proxy

Trial requests include HMAC attestation headers to prevent
trial tokens from being used outside RDST.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import os
import time


# Hardcoded proxy endpoint - only changes on redeploy
TRIAL_PROXY_URL = "https://rdst-keyservice.readysetio.workers.dev/v1/messages"
# SDK version (without /v1/messages path, used by Anthropic SDK)
TRIAL_PROXY_BASE = "https://rdst-keyservice.readysetio.workers.dev"
# Client attestation value for HMAC signing — the proxy checks that requests
# come from the RDST CLI, not arbitrary HTTP clients reusing a trial token.
# This is defense-in-depth, not cryptographic security — the $5 per-user cap
# is the real protection. The proxy-side value lives in Wrangler secrets.
CLIENT_ATTESTATION = "rdst-trial-v1-e913cc8943ce5eca323eb31e6c109b65bf0f39b136f03f566e214269d147f363"


@dataclass
class KeyResolution:
    """Result of API key resolution."""

    api_key: str
    is_trial: bool
    proxy_url: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


def _make_attestation_headers(trial_token: str) -> dict[str, str]:
    """Generate HMAC attestation headers for trial proxy requests.

    The proxy validates these to ensure requests come from RDST,
    not from arbitrary HTTP clients reusing a trial token.
    """
    timestamp = str(int(time.time()))
    message = f"{timestamp}.{trial_token}"
    sig = hmac.new(
        CLIENT_ATTESTATION.encode(), message.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return {
        "X-RDST-Client": "rdst",
        "X-RDST-Signature": f"{timestamp}.{sig}",
    }


def resolve_api_key() -> KeyResolution:
    """Resolve API key with priority: env vars > trial token.

    Returns:
        KeyResolution with routing info and attestation headers.

    Raises:
        LLMError: If no key is found anywhere.
    """
    from .base import LLMError

    # 1. User's own Anthropic API key
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return KeyResolution(api_key=key, is_trial=False)

    # 2. Trial token (env var or config) — check exhausted status first
    trial_env = os.getenv("RDST_TRIAL_TOKEN")
    trial_config_token = None
    trial_status = None

    try:
        from ..cli.rdst_cli import TargetsConfig

        config = TargetsConfig()
        config.load()
        trial = config._data.get("trial", {})
        trial_config_token = trial.get("token")
        trial_status = trial.get("status")
    except Exception:
        pass

    if trial_status == "exhausted":
        raise LLMError(
            "Trial credits exhausted.\n\n"
            "To continue using RDST:\n"
            "  1. Get your own key: https://console.anthropic.com/\n"
            '  2. Set it: export ANTHROPIC_API_KEY="sk-ant-..."\n\n'
            "Want more trial credits? Email hello@readyset.io",
            code="TRIAL_EXHAUSTED",
        )

    # Env var takes priority over config
    token = trial_env or (trial_config_token if trial_status == "active" else None)
    if token:
        return KeyResolution(
            api_key=token,
            is_trial=True,
            proxy_url=TRIAL_PROXY_URL,
            extra_headers=_make_attestation_headers(token),
        )

    raise LLMError(
        "No LLM API key configured.\n\n"
        "Options:\n"
        '  1. Run \'rdst init\' to sign up for a free trial ($5 credit)\n'
        '  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."\n'
        "     Get one at: https://console.anthropic.com/",
        code="NO_API_KEY",
    )
