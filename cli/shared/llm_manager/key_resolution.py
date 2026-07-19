"""Resolve API key and determine routing (direct vs trial proxy).

RDST routes LLM requests based on key type:
  - Own Anthropic key (env var) -> direct to api.anthropic.com
  - Trial token (config.toml)  -> route to the keyservice proxy
    (defaults to prod; overridable via RDST_KEYSERVICE_URL — see
    shared.keyservice).

Trial requests include HMAC attestation headers to prevent
trial tokens from being used outside RDST.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import os
import time

from shared.keyservice import keyservice_base_url, keyservice_url


# Backwards-compatible module-level constants — evaluated at import time,
# so they reflect the env var at process start. Prefer keyservice_url() /
# keyservice_base_url() directly when you need runtime-fresh values.
TRIAL_PROXY_URL = keyservice_url("/v1/messages")
TRIAL_PROXY_BASE = keyservice_base_url()
# Client attestation value for HMAC signing — the proxy checks that requests
# come from the RDST CLI, not arbitrary HTTP clients reusing a trial token.
# This is defense-in-depth, not cryptographic security — the $5 per-user cap
# is the real protection. The proxy-side value lives in Wrangler secrets
# (`ATTESTATION_SECRET` per env). Prod's secret MUST equal the constant
# below so shipped CLIs in the wild keep validating.
#
# To test against a non-prod env (staging, per-CL preview, personal dev
# env), set RDST_ATTESTATION_SECRET to that env's value, paired with
# RDST_KEYSERVICE_URL pointing at the matching Worker.
_DEFAULT_CLIENT_ATTESTATION = (
    "rdst-trial-v1-e913cc8943ce5eca323eb31e6c109b65bf0f39b136f03f566e214269d147f363"
)


def _client_attestation() -> str:
    """Return the HMAC secret, honoring RDST_ATTESTATION_SECRET override."""
    return os.getenv("RDST_ATTESTATION_SECRET") or _DEFAULT_CLIENT_ATTESTATION


CLIENT_ATTESTATION = _client_attestation()


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
        _client_attestation().encode(), message.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return {
        "X-RDST-Client": "rdst",
        "X-RDST-Signature": f"{timestamp}.{sig}",
    }


def resolve_api_key() -> KeyResolution:
    """Resolve API key with priority: env > active trial > keyring.

    Resolution order:
      1. ANTHROPIC_API_KEY env var  → direct to Anthropic
      2. RDST_TRIAL_TOKEN env var   → trial proxy
      3. Trial token in config.toml → trial proxy (only when *active*)
      4. ANTHROPIC_API_KEY in OS keyring (set via rdst web) → direct
      5. RDST_TRIAL_TOKEN in OS keyring → trial proxy

    Keyring is checked last because it may be slow on systems
    without a keyring daemon. The backend type is checked first
    (instant) so dead backends never cause a delay.

    Keyring-precedence rule (T17 / configure-and-identity open-dependency #2):
    a *present own key in the keyring beats an exhausted trial*. When the
    config marks the trial ``exhausted`` we probe the keyring for a real
    ``ANTHROPIC_API_KEY`` **before** raising ``TRIAL_EXHAUSTED`` — a user who
    saved their own key via the web UI must never be blocked by a stale
    exhausted marker in ``config.toml``. The common active-trial / no-trial
    paths still skip the keyring probe, preserving the slow-keyring
    optimization.

    Returns:
        KeyResolution with routing info and attestation headers.

    Raises:
        LLMError: If no key is found anywhere.
    """
    from .base import LLMError

    def _keyring_secret(name: str) -> str | None:
        """Read one secret from the OS keyring, swallowing backend errors."""
        try:
            from shared.secret_store_service import SecretStoreService

            return SecretStoreService().get_secret(name)
        except Exception:
            return None

    # 1. User's own Anthropic API key (env var) — fastest path
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return KeyResolution(api_key=key, is_trial=False)

    # 2. Trial token (env var) — no config read needed
    trial_env = os.getenv("RDST_TRIAL_TOKEN")
    if trial_env:
        return KeyResolution(
            api_key=trial_env,
            is_trial=True,
            proxy_url=keyservice_url("/v1/messages"),
            extra_headers=_make_attestation_headers(trial_env),
        )

    # 3. Trial token (config.toml)
    trial_config_token = None
    trial_status = None

    try:
        from shared.config.targets import TargetsConfig

        config = TargetsConfig()
        config.load()
        trial = config._data.get("trial", {})
        trial_config_token = trial.get("token")
        trial_status = trial.get("status")
    except Exception:
        pass

    if trial_status == "exhausted":
        # A present own key wins over an exhausted trial — probe the keyring
        # before honoring the exhausted status so a valid saved key is never
        # blocked (T17 keyring-precedence fix).
        own_key = _keyring_secret("ANTHROPIC_API_KEY")
        if own_key:
            os.environ["ANTHROPIC_API_KEY"] = own_key
            return KeyResolution(api_key=own_key, is_trial=False)

        raise LLMError(
            "Trial credits exhausted.\n\n"
            "To continue using RDST:\n"
            "  1. Get your own key: https://console.anthropic.com/\n"
            '  2. Set it: export ANTHROPIC_API_KEY="sk-ant-..."\n\n'
            "Want more trial credits? Email hello@readyset.io",
            code="TRIAL_EXHAUSTED",
        )

    if trial_config_token and trial_status == "active":
        return KeyResolution(
            api_key=trial_config_token,
            is_trial=True,
            proxy_url=keyservice_url("/v1/messages"),
            extra_headers=_make_attestation_headers(trial_config_token),
        )

    # 4. OS keyring (checked last — may be slow on first probe)
    keyring_key = _keyring_secret("ANTHROPIC_API_KEY")
    if keyring_key:
        os.environ["ANTHROPIC_API_KEY"] = keyring_key
        return KeyResolution(api_key=keyring_key, is_trial=False)

    # 5. Trial token in keyring (for future web UI support)
    keyring_trial = _keyring_secret("RDST_TRIAL_TOKEN")
    if keyring_trial:
        return KeyResolution(
            api_key=keyring_trial,
            is_trial=True,
            proxy_url=keyservice_url("/v1/messages"),
            extra_headers=_make_attestation_headers(keyring_trial),
        )

    raise LLMError(
        "No LLM API key configured.\n\n"
        "Options:\n"
        "  1. Run 'rdst init' to sign up for a free trial (up to 925K tokens)\n"
        '  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."\n'
        "     Get one at: https://console.anthropic.com/",
        code="NO_API_KEY",
    )
