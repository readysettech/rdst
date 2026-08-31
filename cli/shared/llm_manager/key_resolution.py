"""Resolve credentials and choose Claude BYOK or Readyset-hosted AI.

RDST routes LLM requests based on key type:
  - Own Anthropic key (env var) -> direct to api.anthropic.com
  - Readyset account session   -> route to hosted inference
    (defaults to prod; overridable via RDST_KEYSERVICE_URL — see
    shared.keyservice).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from shared.keyservice import keyservice_base_url
from shared.shell import environment_assignment

HOSTED_MODEL = "z-ai/glm-5.3-flash"

@dataclass
class KeyResolution:
    """Result of API key resolution."""

    api_key: str
    is_trial: bool
    provider: str = "claude"
    model: str | None = None
    proxy_url: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


def resolve_api_key(provider: str = "auto") -> KeyResolution:
    """Resolve credentials with Claude BYOK ahead of hosted inference.

    Resolution order:
      1. ANTHROPIC_API_KEY env var  → direct to Anthropic
      2. ANTHROPIC_API_KEY in OS keyring (set via RDST) → direct
      3. Readyset account session → hosted inference through Keyservice

    The keyring is checked after the environment and before a Readyset account
    so a user-supplied Anthropic key always wins.

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
        except Exception:  # noqa: BLE001 - optional keyring backends may fail
            return None

    requested_provider = (provider or "auto").lower()
    if requested_provider not in {"auto", "claude", "readyset"}:
        raise LLMError(
            f"Unknown provider '{requested_provider}'.",
            code="NO_SUCH_PROVIDER",
        )

    # 1. User's own Anthropic API key (env var) — fastest path
    key = os.getenv("ANTHROPIC_API_KEY")
    if key and requested_provider != "readyset":
        return KeyResolution(api_key=key, is_trial=False, provider="claude")

    # 2. OS keyring (checked after the environment because some desktop
    # backends may be slow on their first probe).
    keyring_key = (
        _keyring_secret("ANTHROPIC_API_KEY")
        if requested_provider != "readyset"
        else None
    )
    if keyring_key:
        os.environ["ANTHROPIC_API_KEY"] = keyring_key
        return KeyResolution(api_key=keyring_key, is_trial=False, provider="claude")

    # 3. Readyset account session.
    if requested_provider != "claude":
        from shared.account_session import access_token

        account_token = access_token()
        if account_token:
            return KeyResolution(
                api_key=account_token,
                is_trial=False,
                provider="readyset",
                proxy_url=keyservice_base_url(),
                model=HOSTED_MODEL,
            )

    raise LLMError(
        "Sign in to Readyset to use hosted inference, or set your own Anthropic key:\n"
        f"  {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}",
        code="LOGIN_REQUIRED" if requested_provider != "claude" else "NO_API_KEY",
    )
