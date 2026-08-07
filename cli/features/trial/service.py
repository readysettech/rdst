"""Trial registration and status service."""

from __future__ import annotations

import logging
import os
from typing import Any

from shared.config.targets import TargetsConfig
from shared.keyservice import keyservice_url
from shared.llm import cents_to_tokens, format_tokens
from shared.secret_store_service import SecretStoreService
from shared.telemetry import telemetry

from .models import TrialActivateResult, TrialRegisterResult, TrialStatusResult

logger = logging.getLogger(__name__)


class TrialService:
    """Stateless service for trial registration, activation, and status."""

    def __init__(self, secret_store: SecretStoreService | None = None):
        self.secret_store = secret_store or SecretStoreService()

    def _load_config(self) -> TargetsConfig:
        cfg = TargetsConfig()
        cfg.load()
        return cfg

    async def register(self, email: str, source: str = "cli") -> TrialRegisterResult:
        """Proxy registration to keyservice, return structured result."""
        import httpx

        if not email or "@" not in email:
            return TrialRegisterResult(
                success=False,
                error_code="INVALID_EMAIL",
                detail="Invalid email address.",
                status_code=400,
            )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    keyservice_url("/register"), json={"email": email}
                )
        except Exception as exc:
            logger.warning(
                "Unable to reach keyservice during trial registration: error=%s",
                type(exc).__name__,
            )
            return TrialRegisterResult(
                success=False,
                error_code="CONNECTION_ERROR",
                detail="Unable to reach RDST trial service.",
                status_code=503,
            )

        try:
            resp_data: dict[str, Any] = resp.json()
        except Exception:
            resp_data = {}

        code = resp_data.get("code", "")

        if resp.status_code >= 400:
            detail = " ".join(str(resp_data.get("detail", "")).split())[:500]
            logger.warning(
                "Keyservice trial registration rejected: status=%s code=%s detail=%s",
                resp.status_code,
                code or "unknown",
                detail or "not provided",
            )
        else:
            logger.info(
                "Keyservice trial registration accepted: status=%s tier=%s resent=%s",
                resp.status_code,
                resp_data.get("email_tier", "unknown"),
                bool(resp_data.get("token_resent")),
            )

        if resp.status_code == 503:
            return TrialRegisterResult(
                success=False,
                error_code="PROGRAM_FULL",
                detail=resp_data.get(
                    "detail", "The RDST free trial program is currently full."
                ),
                status_code=503,
            )

        if resp.status_code == 429:
            return TrialRegisterResult(
                success=False,
                error_code="RATE_LIMITED",
                detail=resp_data.get(
                    "detail",
                    "You've signed up for too many accounts recently.",
                ),
                status_code=429,
            )

        if resp.status_code == 409:
            return TrialRegisterResult(
                success=False,
                error_code="ALREADY_REGISTERED",
                detail="This email is already registered. Enter your trial token below.",
                status_code=409,
            )

        if resp.status_code == 400 and code == "DISPOSABLE_EMAIL":
            return TrialRegisterResult(
                success=False,
                error_code="DISPOSABLE_EMAIL",
                detail=(
                    "Disposable or temporary email addresses are not allowed. "
                    "Please use your real email address."
                ),
                status_code=400,
            )

        if resp.status_code == 400 and code == "INVALID_DOMAIN":
            return TrialRegisterResult(
                success=False,
                error_code="INVALID_DOMAIN",
                detail=(
                    "This email domain doesn't appear to accept mail. "
                    "Please check for typos."
                ),
                status_code=400,
            )

        if resp.status_code == 400 and code == "EMAIL_REJECTED":
            return TrialRegisterResult(
                success=False,
                error_code="EMAIL_REJECTED",
                detail=resp_data.get("detail", "This email could not be verified."),
                did_you_mean=resp_data.get("did_you_mean"),
                status_code=400,
            )

        if resp.status_code == 400 and code == "EMAIL_ALIAS":
            return TrialRegisterResult(
                success=False,
                error_code="EMAIL_ALIAS",
                detail=resp_data.get(
                    "detail",
                    "Email aliases with '+' are not supported for trial signup.",
                ),
                did_you_mean=resp_data.get("base_email"),
                status_code=400,
            )

        if resp.status_code == 422:
            email_error = resp_data.get("email_error", "")
            hint = resp_data.get("hint", "")
            detail = f"Could not send verification email. {email_error}"
            if hint:
                detail += f" {hint}"
            return TrialRegisterResult(
                success=False,
                error_code="EMAIL_SEND_FAILED",
                detail=detail,
                status_code=422,
            )

        if resp.status_code >= 400:
            return TrialRegisterResult(
                success=False,
                error_code="UNKNOWN_ERROR",
                detail=resp_data.get(
                    "detail", f"Registration failed (HTTP {resp.status_code})."
                ),
                status_code=resp.status_code,
            )

        try:
            telemetry.track(
                "trial_registration",
                {
                    "display_name": "RDST Token Requested",
                    "email": email,
                    "email_domain": email.split("@")[1] if "@" in email else "unknown",
                    "email_tier": resp_data.get("email_tier", "business"),
                    "limit_display": resp_data.get("limit_display", "$5.00"),
                    "source": source,
                },
            )
        except Exception:
            pass

        return TrialRegisterResult(
            success=True,
            limit_display=resp_data.get("limit_display", "$5.00"),
            email_tier=resp_data.get("email_tier", "business"),
            status_code=resp.status_code,
            trial_token=resp_data.get("trial_token"),
            token_resent=bool(resp_data.get("token_resent")),
            limit_cents=resp_data.get("limit_cents"),
            remaining_cents=resp_data.get("remaining_cents"),
        )

    async def activate(
        self,
        token: str,
        email: str,
        email_tier: str | None = None,
        source: str = "cli",
        limit_cents: int | None = None,
        remaining_cents: int | None = None,
    ) -> TrialActivateResult:
        """Save trial token to config.toml + keyring + env.

        When the keyservice reports the account's real balance (a resend for
        an existing account), store that. Otherwise seed from the email-tier
        default; the proxy corrects it on the first LLM call."""
        if not token or len(token.strip()) < 10:
            return TrialActivateResult(success=False, message="Invalid token.")

        token = token.strip()
        if limit_cents is None:
            limit_cents = (
                500
                if email_tier == "business"
                else 150
                if email_tier == "personal"
                else None
            )
        # A fresh activation has spent nothing; only a reported balance carries
        # real remaining.
        if remaining_cents is None:
            remaining_cents = limit_cents

        try:
            cfg = self._load_config()
            trial_config: dict[str, Any] = {
                "token": token,
                "email": email,
                "status": "active",
            }
            if limit_cents is not None:
                trial_config["limit_cents"] = limit_cents
                trial_config["remaining_cents"] = remaining_cents
            cfg.set_trial_config(trial_config)
            # Trial activation confirms a real, reachable address (the user
            # clicked the verification link). Promote it to the primary
            # [[emails]] identity so telemetry follows the real human even when
            # the gate captured a wrong or throwaway address first.
            cfg.set_email(email)
            cfg.save()
        except Exception as exc:
            return TrialActivateResult(
                success=False, message=f"Failed to save config: {exc}"
            )

        self.secret_store.set_secret(
            name="RDST_TRIAL_TOKEN",
            value=token,
            persist=True,
        )
        os.environ["RDST_TRIAL_TOKEN"] = token

        if source == "web":
            # Key resolution prefers a real Anthropic key over the trial
            # token, so activating the trial from the web is an explicit
            # request to switch sources: drop the stored key so the trial
            # takes effect immediately.
            os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                self.secret_store.clear_required(["ANTHROPIC_API_KEY"])
            except Exception:
                pass

        try:
            telemetry.track(
                "trial_activated",
                {
                    "display_name": "RDST Token Confirmed",
                    "email": email,
                    "email_domain": email.split("@")[1] if "@" in email else "unknown",
                    "email_tier": email_tier or "unknown",
                    "source": source,
                },
            )
        except Exception:
            pass

        return TrialActivateResult(
            success=True, message="Trial activated successfully."
        )

    def _build_status_result(
        self,
        trial: dict[str, Any],
        *,
        active: bool,
        status: str | None,
        remaining_cents: int | None,
        limit_cents: int | None,
    ) -> TrialStatusResult:
        """Build a TrialStatusResult with formatted display fields."""
        remaining_display = None
        limit_display = None
        percent_remaining = None

        if remaining_cents is not None and limit_cents is not None:
            remaining_display = format_tokens(cents_to_tokens(remaining_cents))
            limit_display = format_tokens(cents_to_tokens(limit_cents))
            percent_remaining = (
                int((remaining_cents / limit_cents) * 100) if limit_cents > 0 else 0
            )

        return TrialStatusResult(
            active=active,
            email=trial.get("email"),
            status=status,
            remaining_cents=remaining_cents,
            limit_cents=limit_cents,
            remaining_tokens_display=remaining_display,
            limit_tokens_display=limit_display,
            percent_remaining=percent_remaining,
        )

    def get_status(self) -> TrialStatusResult:
        """Read trial state from config.toml, format balance."""
        try:
            cfg = self._load_config()
        except Exception:
            return TrialStatusResult(active=False)

        trial = cfg.get_trial_config()
        if not trial.get("token"):
            return TrialStatusResult(active=False)

        status = trial.get("status")
        return self._build_status_result(
            trial,
            active=status == "active",
            status=status,
            remaining_cents=trial.get("remaining_cents"),
            limit_cents=trial.get("limit_cents"),
        )

    def simulate_exhausted(self) -> TrialStatusResult:
        """Force trial status to exhausted for local/dev testing."""
        try:
            cfg = self._load_config()
        except Exception:
            return TrialStatusResult(active=False)

        trial = cfg.get_trial_config()
        if not trial.get("token"):
            return TrialStatusResult(active=False)

        try:
            limit_cents = trial.get("limit_cents") or trial.get("limit") or 500
            trial["status"] = "exhausted"
            trial["remaining_cents"] = 0
            trial["limit_cents"] = int(limit_cents)
            cfg.set_trial_config(trial)
            cfg.save()
        except Exception:
            return TrialStatusResult(active=False)

        return self._build_status_result(
            trial,
            active=False,
            status="exhausted",
            remaining_cents=0,
            limit_cents=int(limit_cents),
        )
