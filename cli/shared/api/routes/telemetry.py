"""Product events from the desktop shell, sent through this backend's telemetry gating."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from shared.api.guards import require_local_request

router = APIRouter()

# The events a client may send, and the properties each may carry. Anything else is dropped.
CLIENT_EVENTS: dict[str, frozenset[str]] = {
    "desktop_installed": frozenset({"os", "os_version", "arch", "install_kind", "delayed"}),
    "desktop_upgraded": frozenset({"from_version", "to_version", "delayed"}),
    "desktop_startup_failed": frozenset({"stage", "error_class", "attempts", "message",
                                         "delayed"}),
    "tray_quit": frozenset({"uptime_hours", "monitored_count"}),
    "start_at_login_changed": frozenset({"enabled", "by"}),
    "os_notifications_disabled_detected": frozenset({"reason", "platform"}),
    "system_notification_shown": frozenset({"kind", "critical"}),
    "system_notification_clicked": frozenset({"kind", "critical"}),
    "app_error": frozenset({"component", "error_class", "message", "handled", "delayed"}),
}

PropertyValue = Union[bool, int, float, str, None]


class ClientEvent(BaseModel):
    event: str
    properties: Dict[str, PropertyValue] = Field(default_factory=dict)


class ClientEventsRequest(BaseModel):
    source: str = "desktop"
    events: List[ClientEvent] = Field(default_factory=list, max_length=50)


class ClientEventsResponse(BaseModel):
    accepted: int
    telemetry_enabled: bool
    rejected: Optional[List[str]] = None


def _send(source: str, events: list[ClientEvent]) -> tuple[int, list[str]]:
    from shared import product_telemetry

    accepted = 0
    rejected: list[str] = []
    for item in events:
        allowed = CLIENT_EVENTS.get(item.event)
        if allowed is None:
            rejected.append(item.event[:60])
            continue
        props: dict[str, Any] = {key: value for key, value in item.properties.items()
                                 if key in allowed}
        if item.event == "app_error":
            product_telemetry.report_error(
                str(props.get("component") or "main"), error_class=str(props.get("error_class") or "Error"),
                message=str(props.get("message") or ""), source=source,
                handled=bool(props.get("handled", False)),
                extra={"delayed": props["delayed"]} if "delayed" in props else None)
        elif item.event == "desktop_installed":
            product_telemetry.desktop_installed({**props, "source": source})
        else:
            product_telemetry.capture(item.event, {**props, "source": source})
        accepted += 1
    return accepted, rejected


@router.post("/telemetry/events")
async def post_client_events(request: Request, body: ClientEventsRequest) -> ClientEventsResponse:
    """Forward allowlisted desktop events to PostHog, if this install sends telemetry."""
    require_local_request(request)
    from shared import product_telemetry

    source = "desktop" if body.source == "desktop" else "web"
    accepted, rejected = await asyncio.to_thread(_send, source, body.events)
    return ClientEventsResponse(accepted=accepted, telemetry_enabled=product_telemetry.enabled(),
                                rejected=rejected or None)


@router.get("/telemetry/identity")
async def get_telemetry_identity(request: Request) -> dict[str, Any]:
    """Local identity shared by the renderer and backend telemetry."""
    require_local_request(request)
    from shared import product_telemetry

    if not product_telemetry.enabled():
        return {"telemetry_enabled": False}

    def read() -> dict[str, Any]:
        manager = product_telemetry._telemetry()
        internal_user = getattr(manager, "_internal_user_result", None)
        if internal_user is None:
            internal_user = manager._detect_internal_user()
        return {"telemetry_enabled": True, **product_telemetry.identity(manager.device_id),
                "internal_user": internal_user}

    return await asyncio.to_thread(read)
