"""Event dataclasses for the add-database bootstrap run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from shared.service_events import ErrorEvent

# Stage identifiers, in pipeline order (deploy runs parallel to the rest).
STAGE_CONNECTION_TEST = "connection_test"
STAGE_STRUCTURE = "structure"
STAGE_PROFILE = "profile"
STAGE_ANNOTATE = "annotate"
STAGE_DEPLOY = "deploy"


@dataclass
class BootstrapStageEvent:
    """Progress of one bootstrap stage.

    status is started | progress | done | failed | skipped. Child-service
    events surface as status="progress" with the child's payload in detail,
    so the stream stays one flat, typed union.
    """

    type: Literal["bootstrap_stage"]
    stage: str
    status: str
    message: str = ""
    detail: dict[str, Any] | None = None


@dataclass
class BootstrapNeedsKeyEvent:
    """The run reached the annotate gate without a usable Anthropic key.

    The event name doubles as the run registry's gating signal: the run's
    status parks on needs_key until the next event arrives.
    """

    type: Literal["needs_key"]
    message: str


BootstrapEvent = BootstrapStageEvent | BootstrapNeedsKeyEvent | ErrorEvent

__all__ = [
    "STAGE_ANNOTATE",
    "STAGE_CONNECTION_TEST",
    "STAGE_DEPLOY",
    "STAGE_PROFILE",
    "STAGE_STRUCTURE",
    "BootstrapEvent",
    "BootstrapNeedsKeyEvent",
    "BootstrapStageEvent",
]
