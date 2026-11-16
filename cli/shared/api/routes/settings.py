"""API routes for user-level RDST settings."""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.api.email import EMAIL_RE as _EMAIL_RE
from shared.api.email import normalized_email as _normalized_email
from shared.api.guards import require_local_request
from shared.config.targets import TargetsConfig

router = APIRouter(prefix="/settings")
logger = logging.getLogger(__name__)
_LOCAL_DATA_RESET_LOCK = threading.Lock()
_KEYRING_CLEANUP_TIMEOUT_SECONDS = 5.0
# The latest AI decision still being applied to monitored databases, which writes to the
# monitor store; a local-data reset waits for it.
_ai_consent_apply: threading.Thread | None = None

__all__ = ["router", "_EMAIL_RE", "_normalized_email"]


class EmailRequest(BaseModel):
    email: str
    # Optional lead enrichment: stored locally and attached to telemetry
    # alongside the email. Never required.
    first_name: str = ""
    last_name: str = ""


class EmailResponse(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    verified: bool = False


class EmailUpdateResponse(BaseModel):
    success: bool
    email: str
    # Whether the email is verified (possibly instantly, when the keyservice
    # already knew it from the trial or CLI report flow) and whether a
    # verification email is on its way. verified=False with
    # verification_started=False means the keyservice was unreachable; the
    # gate holds and offers a retry.
    verified: bool = False
    verification_started: bool = False


class VerifyPollResponse(BaseModel):
    verified: bool


def _make_resolver():
    try:
        import dns.resolver
    except ImportError:
        return None
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3.0
    resolver.lifetime = 3.0
    return resolver


def _domain_has_mx(domain: str, resolver=None) -> Optional[bool]:
    """Best-effort mail-domain reachability check.

    Returns True when the domain has a mail route, False when it provably has
    none (reject the address), and None when the answer can't be determined
    (resolver unavailable, timeout, or network error) so the caller lets it
    through — an offline user must never be stranded at the gate.
    """
    if resolver is None:
        resolver = _make_resolver()
    if resolver is None:
        return None
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    try:
        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0
    except dns.resolver.NXDOMAIN:
        return False
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # No MX record. Per RFC 5321 an address record acts as an implicit mail
        # exchanger, so a resolvable host is allowed; a nonexistent one is not.
        try:
            resolver.resolve(domain, "A")
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except Exception:
            return None
    except (dns.exception.Timeout, Exception):
        return None


def _fire_email_captured(email: str, domain: str) -> None:
    """Fire the PostHog `email_captured` funnel event, fire-and-forget. Any
    failure here is swallowed so telemetry never blocks or breaks the save."""
    try:
        from shared.telemetry import telemetry

        telemetry.track(
            "email_captured",
            {
                "display_name": "RDST Email Captured",
                "source": "gate",
                "email": email,
                "email_domain": domain,
            },
        )
    except Exception:
        pass


@router.get("/email")
async def get_email(request: Request) -> EmailResponse:
    require_local_request(request)
    cfg = TargetsConfig()
    cfg.load()
    identity = cfg.get_identity()
    return EmailResponse(
        email=identity["email"],
        first_name=identity["first_name"],
        last_name=identity["last_name"],
        verified=identity["verified"],
    )


@router.post("/email")
async def set_email(request: Request, body: EmailRequest) -> EmailUpdateResponse:
    require_local_request(request)
    email = _normalized_email(body.email)
    domain = email.split("@", 1)[1]
    if _domain_has_mx(domain) is False:
        raise HTTPException(
            status_code=400,
            detail=(
                "We couldn't find a mail server for that email domain. "
                "Please double-check the address."
            ),
        )
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_email(email)
    cfg.set_name(body.first_name.strip()[:100], body.last_name.strip()[:100])
    cfg.save()
    _fire_email_captured(email, domain)

    from shared.hosted_reports import hosted_reports_enabled

    if not hosted_reports_enabled():
        return EmailUpdateResponse(
            success=True,
            email=email,
            verified=False,
            verification_started=False,
        )

    # This install proves mailbox access through the keyservice, the same
    # verification a trial registration performs, minus the trial token. The
    # click on the emailed link releases the report credential to this install
    # alone, through /email/verify-poll, even for an address another flow or
    # machine already verified. Names are deliberately NOT sent: they live on
    # this machine and in telemetry only.
    from features.audit.email_service import EmailService

    result = EmailService().register_email(email)
    if result.get("verified"):
        token = result.get("report_token")
        if token:
            cfg.add_verified_email(email, token)
            cfg.save()
        return EmailUpdateResponse(
            success=True,
            email=email,
            verified=True,
            verification_started=True,
        )
    if result.get("error"):
        logger.warning("Email verification registration failed: %s", result["error"])
        raise HTTPException(
            status_code=502,
            detail=(
                "The RDST verification service is temporarily unavailable. "
                "Please try again in a moment."
            ),
        )
    return EmailUpdateResponse(
        success=True,
        email=email,
        verified=False,
        verification_started=True,
    )


Theme = Literal["dark", "light", "system"]
FontSize = Literal["default", "large"]
KeepReadingsDays = Literal[7, 30, 90]
ReportView = Literal["quick", "detailed"]


class Preferences(BaseModel):
    """App preferences kept in the RDST config file's [preferences] section."""
    theme: Theme = "dark"
    font_size: FontSize = "default"
    # How long minute-detail readings stay (the monitor store's 5-minute rollup).
    keep_readings_days: KeepReadingsDays = 7
    # The Report page's last view, for every database and day.
    report_view: ReportView = "quick"
    # The database the app has selected; None until one is picked.
    selected_target: str | None = None
    # "Push to this computer": inbox alerts as system notifications from the app. None
    # until someone chooses.
    push_notifications: bool | None = None


class PreferencesUpdate(BaseModel):
    theme: Theme | None = None
    font_size: FontSize | None = None
    keep_readings_days: KeepReadingsDays | None = None
    report_view: ReportView | None = None
    # An empty name clears the selection.
    selected_target: str | None = Field(default=None, max_length=256)
    push_notifications: bool | None = None


def _read_preferences(cfg: TargetsConfig) -> Preferences:
    section = cfg._data.get("preferences")
    try:
        return Preferences.model_validate(section if isinstance(section, dict) else {})
    except ValueError:
        return Preferences()


@router.get("/preferences")
async def get_preferences(request: Request) -> Preferences:
    require_local_request(request)
    cfg = TargetsConfig()
    cfg.load()
    preferences = _read_preferences(cfg)
    # A database removed since it was selected is no selection.
    if preferences.selected_target and cfg.get(preferences.selected_target) is None:
        preferences.selected_target = None
    return preferences


@router.put("/preferences")
async def put_preferences(request: Request, body: PreferencesUpdate) -> Preferences:
    """Change the preferences sent; the rest keep their values."""
    require_local_request(request)
    cfg = TargetsConfig()
    cfg.load()
    current = _read_preferences(cfg)
    changes = body.model_dump(exclude_none=True)
    if "selected_target" in changes:
        changes["selected_target"] = changes["selected_target"].strip() or None
    updated = current.model_copy(update=changes)
    # TOML has no null; preferences not set yet are left out of the file.
    cfg._data["preferences"] = updated.model_dump(exclude_none=True)
    cfg.save()
    from shared.monitor.monitor_store import _PREFERENCES_CACHE

    _PREFERENCES_CACHE.update(at=0.0, value={})
    return updated


class TelemetrySettingsResponse(BaseModel):
    consent: Literal["pending", "granted", "denied"]
    enabled: bool
    consent_version: int
    # Whether this distribution waits for opt-in before sending anything.
    consent_required: bool
    forced_off_reason: str | None = None


class TelemetrySettingsRequest(BaseModel):
    consent: Literal["granted", "denied"]


def _telemetry_settings_response() -> TelemetrySettingsResponse:
    from shared.telemetry import telemetry

    from shared.distribution import consent_opt_in

    return TelemetrySettingsResponse(
        consent=telemetry.telemetry_consent(),
        enabled=telemetry.is_enabled(),
        consent_version=telemetry.CONSENT_VERSION,
        consent_required=consent_opt_in(),
        forced_off_reason=telemetry.forced_off_reason(),
    )


@router.get("/telemetry")
async def get_telemetry_settings(request: Request) -> TelemetrySettingsResponse:
    require_local_request(request)
    return _telemetry_settings_response()


@router.put("/telemetry")
async def set_telemetry_settings(
    request: Request, body: TelemetrySettingsRequest
) -> TelemetrySettingsResponse:
    require_local_request(request)
    from shared.telemetry import telemetry

    telemetry.set_telemetry_consent(body.consent)
    if body.consent == "granted" and telemetry.is_enabled():
        telemetry.track("telemetry_consent_granted", {"consent_version": telemetry.CONSENT_VERSION})
    return _telemetry_settings_response()


class AIDataSettingsResponse(BaseModel):
    consent: Literal["pending", "granted", "denied"]
    enabled: bool
    consent_version: int
    consent_required: bool


class AIDataSettingsRequest(BaseModel):
    consent: Literal["granted", "denied"]


def _ai_data_settings_response() -> AIDataSettingsResponse:
    from shared.ai_data_consent import (
        AI_DATA_CONSENT_VERSION,
        ai_data_consent,
        ai_data_sharing_allowed,
        requires_ai_data_consent,
    )

    return AIDataSettingsResponse(
        consent=ai_data_consent(),
        enabled=ai_data_sharing_allowed(),
        consent_version=AI_DATA_CONSENT_VERSION,
        consent_required=requires_ai_data_consent(),
    )


@router.get("/ai-data")
async def get_ai_data_settings(request: Request) -> AIDataSettingsResponse:
    require_local_request(request)
    return _ai_data_settings_response()


@router.put("/ai-data")
async def set_ai_data_settings(
    request: Request, body: AIDataSettingsRequest
) -> AIDataSettingsResponse:
    require_local_request(request)
    from features.monitor import database_limit
    from shared.ai_data_consent import set_ai_data_consent
    from shared.monitor.monitor_store import MonitorStore

    set_ai_data_consent(body.consent)
    if body.consent == "denied":
        from features.agent_connect import discover

        # A coding agent RDST started is an AI model reading the database: it stops too.
        await discover.stop_all()
    # The choice is saved and answered now; claiming or freeing each database's slot talks
    # to the Key Service in the background.
    global _ai_consent_apply
    _ai_consent_apply = threading.Thread(
        target=database_limit.apply_ai_consent, args=(MonitorStore(),), daemon=True
    )
    _ai_consent_apply.start()
    return _ai_data_settings_response()


class HostedReportSettingsResponse(BaseModel):
    enabled: bool
    reason: str | None = None


@router.get("/hosted-reports")
async def get_hosted_report_settings(request: Request) -> HostedReportSettingsResponse:
    require_local_request(request)
    from shared.hosted_reports import (
        hosted_reports_disabled_message,
        hosted_reports_enabled,
    )

    enabled = hosted_reports_enabled()
    return HostedReportSettingsResponse(
        enabled=enabled,
        reason=None if enabled else hosted_reports_disabled_message(),
    )


class ResetLocalDataResponse(BaseModel):
    success: bool
    removed_path: str


@router.post("/reset-local-data")
async def reset_local_data(request: Request) -> ResetLocalDataResponse:
    """Delete RDST-owned local files, credentials, and managed runtimes."""
    require_local_request(request)

    from features.providers import (
        digitalocean_oauth,
        identity as provider_identity,
        provider_common,
        supabase_oauth,
    )
    from features.providers.neon import API_KEY_SECRET_NAME
    from features.qpdemo.service import DemoService
    from features.slack.config import stored_credential_secret_names
    from shared.constants import rdst_data_dir, rdst_instance_suffix
    from shared.deploy.sandbox_manager import sandbox_manager
    from shared.env_requirements_service import EnvRequirementsService
    from shared.run_registry import run_registry
    from shared.secret_store_service import (
        SecretStoreService,
        platform_secret_names,
        stored_secret_inventory,
    )
    from shared.ssh_tunnel import get_tunnel_manager
    from shared.telemetry import telemetry

    data_dir = rdst_data_dir()
    while not _LOCAL_DATA_RESET_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.05)
    applying = _ai_consent_apply
    if applying is not None and applying.is_alive():
        await asyncio.to_thread(applying.join, 60)

    try:
        try:
            cfg = TargetsConfig()
            cfg.load()
            # This instance's keyring services only: another instance's keys aren't its data.
            web_service = f"{SecretStoreService.SERVICE_NAME}{rdst_instance_suffix()}"
            slack_service = f"rdst-slack{rdst_instance_suffix()}"
            secret_names: dict[str, set[str]] = {
                web_service: set(
                    EnvRequirementsService().get_clearable_secret_names(cfg)
                )
                | {API_KEY_SECRET_NAME}
            }
            # A damaged ledger must not block the reset that recovers from it: the known
            # names and the platform listing below still cover what RDST stores.
            try:
                inventory = stored_secret_inventory()
            except Exception as exc:
                logger.warning("Ignoring unreadable RDST secret inventory: %s", exc)
                inventory = {}
            for service_name, names in inventory.items():
                secret_names.setdefault(service_name, set()).update(names)
            for service_name in (web_service, slack_service):
                names = await asyncio.wait_for(
                    asyncio.to_thread(platform_secret_names, service_name),
                    timeout=_KEYRING_CLEANUP_TIMEOUT_SECONDS,
                )
                secret_names.setdefault(service_name, set()).update(names)
            try:
                slack_names = stored_credential_secret_names()
            except Exception as exc:
                logger.warning("Ignoring unreadable Slack credential metadata: %s", exc)
                slack_names = []
            secret_names.setdefault(slack_service, set()).update(slack_names)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "local_data_reset_inventory_failed",
                    "message": (
                        "RDST could not inventory its stored credentials. "
                        "No local data was removed."
                    ),
                    "detail": str(exc),
                },
            ) from exc

        credential_errors: list[str] = []
        for service_name, names in secret_names.items():
            try:
                secret_store = SecretStoreService(service_name=service_name)
                secret_store.refresh_availability()
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        secret_store.clear_required,
                        sorted(names),
                        clear_environment="owned",
                        update_inventory=False,
                    ),
                    timeout=_KEYRING_CLEANUP_TIMEOUT_SECONDS,
                )
                credential_errors.extend(
                    f"{service_name}: {error}"
                    for error in result.get("errors", [])
                )
            except Exception as exc:
                credential_errors.append(f"{service_name}: {exc}")

        if credential_errors:
            logger.error(
                "Local RDST credential cleanup failed: %s",
                "; ".join(credential_errors),
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "local_data_reset_incomplete",
                    "message": (
                        "RDST could not remove all stored credentials. "
                        "No local files were removed."
                    ),
                    "detail": credential_errors,
                },
            )

        errors: list[str] = []
        demo_result = await asyncio.to_thread(DemoService.instance().reset_local_data)
        if not demo_result.get("success"):
            errors.append(f"Demo runtime: {demo_result.get('message')}")

        await run_registry.reset_and_wait()
        try:
            await asyncio.to_thread(get_tunnel_manager().close_all)
        except Exception as exc:
            errors.append(f"SSH tunnels: {exc}")
        try:
            await sandbox_manager.reset_local_data()
        except Exception as exc:
            errors.append(f"Readyset sandbox: {exc}")

        provider_common.reset_runtime_state()
        provider_identity.reset_runtime_state()
        digitalocean_oauth.reset_runtime_state()
        supabase_oauth.reset_runtime_state()
        telemetry.reset_runtime_state()

        try:
            if data_dir.exists():
                shutil.rmtree(data_dir)
        except Exception as exc:
            errors.append(f"Local files: {exc}")

        if errors:
            logger.error("Local RDST data reset incomplete: %s", "; ".join(errors))
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "local_data_reset_incomplete",
                    "message": "Some RDST data could not be removed. Retry the reset.",
                    "detail": errors,
                },
            )

        return ResetLocalDataResponse(success=True, removed_path=str(data_dir))
    finally:
        _LOCAL_DATA_RESET_LOCK.release()


@router.post("/email/verify-poll")
async def verify_poll(request: Request) -> VerifyPollResponse:
    """One non-blocking check of the primary email's verification, mirroring
    the CLI report flow's /report-status poll; persists the report token the
    moment the keyservice confirms the click."""
    require_local_request(request)
    from shared.hosted_reports import hosted_reports_enabled

    if not hosted_reports_enabled():
        return VerifyPollResponse(verified=False)
    cfg = TargetsConfig()
    cfg.load()
    identity = cfg.get_identity()
    if not identity["email"]:
        return VerifyPollResponse(verified=False)
    if identity["verified"]:
        return VerifyPollResponse(verified=True)

    from features.audit.email_service import EmailService

    # The check makes blocking keyservice requests; keep them off the event loop.
    status = await asyncio.to_thread(EmailService().check_status, identity["email"])
    if status.get("error"):
        logger.warning("Email verification status check failed: %s", status["error"])
        raise HTTPException(
            status_code=502,
            detail=(
                "The RDST verification service is temporarily unavailable. "
                "Please try again in a moment."
            ),
        )
    if status.get("verified") and status.get("report_token"):
        cfg.add_verified_email(identity["email"], status["report_token"])
        cfg.save()
        return VerifyPollResponse(verified=True)
    # A report went out through another install's email: delivered, nothing to store.
    return VerifyPollResponse(verified=bool(status.get("verified")))
