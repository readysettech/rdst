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

HOSTED_MODEL = "anthropic/claude-haiku-5.5"


@dataclass
class KeyResolution:
    """Result of API key resolution."""

    api_key: str
    is_trial: bool
    provider: str = "claude"
    model: str | None = None
    proxy_url: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


def require_monitoring_session() -> None:
    """Automatic monitoring requires a current session, including own-key inference.

    An anonymous session counts; one is started when there's no session at all.
    """
    from .base import LLMError
    from shared import account_session

    try:
        token = account_session.ensure_access_token()
    except account_session.SignInRequiredError as exc:
        raise LLMError(f"{exc} ({exc.reason})", code="LOGIN_REQUIRED") from exc
    if not token:
        raise LLMError("Sign in to turn on AI monitoring (signed out)", code="LOGIN_REQUIRED")


def own_key_configured() -> bool:
    """Whether the user's own Anthropic key pays for AI, without starting any session."""
    try:
        resolution = resolve_api_key("claude")
    except Exception:
        return False
    if resolution.provider != "claude":
        return False
    try:
        from shared.config.targets import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        return (cfg.get_llm_provider() or "").lower() != "readyset"
    except Exception:
        return True


def hosted_ai_ready() -> bool:
    """Whether a session can carry Readyset-hosted work now (an anonymous one counts).

    Starts an anonymous session when there's none. A sign-in requirement only blocks
    hosted inference, so an own-key user's monitoring keeps running.
    """
    from shared import account_session

    try:
        token = account_session.ensure_access_token()
    except Exception:
        return False
    if not token:
        return False
    if account_session.signin_required() and account_session.is_anonymous_session():
        return own_key_configured()
    return True


def resolve_api_key(provider: str = "auto") -> KeyResolution:
    """Resolve credentials using the saved provider preference when present.

    A saved ``llm.provider`` selection restricts resolution to that provider.
    Otherwise, resolution order is:
      1. ANTHROPIC_API_KEY env var → direct to Anthropic
      2. ANTHROPIC_API_KEY in OS keyring (set via RDST) → direct
      3. Readyset account session -> hosted inference through Keyservice (an
         anonymous session is started when there's none)

    Without a saved preference, the keyring is checked after the environment
    and before a Readyset account for backward compatibility.

    Returns:
        KeyResolution with routing info and attestation headers.

    Raises:
        LLMError: If no key is found anywhere.
    """
    from .base import LLMError
    from .inference_attribution import attribution_for

    if attribution_for(None)["feature"] == "monitor":
        require_monitoring_session()

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

    if requested_provider == "auto":
        try:
            from shared.config.targets import TargetsConfig

            cfg = TargetsConfig()
            cfg.load()
            configured_provider = (cfg.get_llm_provider() or "").lower()
        except RuntimeError:
            # A missing or unreadable config preserves the legacy auto order.
            configured_provider = ""
        if configured_provider in {"anthropic", "claude"}:
            requested_provider = "claude"
        elif configured_provider == "readyset":
            requested_provider = "readyset"

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
        import requests

        from shared import account_session
        from shared.account_session import AccountSessionError, SignInRequiredError

        from .base import provider_unreachable

        try:
            # Without a session, an anonymous one is started: AI works before sign-in.
            account_token = account_session.ensure_access_token()
            reason = account_token and account_session.signin_required()
            if reason and account_session.is_anonymous_session():
                raise SignInRequiredError(reason)
        except SignInRequiredError as exc:
            raise LLMError(
                f"{exc}. Run 'rdst account login' or set your own Anthropic key:\n"
                f"  {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}",
                code="LOGIN_REQUIRED",
            ) from exc
        except AccountSessionError as exc:
            # Offline, the session can't refresh: a network problem, not a signed-out one.
            if isinstance(exc.__cause__, requests.RequestException):
                raise provider_unreachable(
                    "RDST's AI service", "HOSTED_INFERENCE_UNAVAILABLE", exc
                ) from exc
            raise
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
