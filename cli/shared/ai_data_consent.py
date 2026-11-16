"""Consent state for sharing database and application content with AI providers."""

from __future__ import annotations

import os

from shared.config.targets import TargetsConfig
from shared.distribution import consent_opt_in

AI_DATA_CONSENT_VERSION = 1
# Every AI gate asks, so the answer is kept until config.toml changes: (file stamp, consent),
# replaced whole so a reader never sees half of it.
_cached: tuple[object, str] | None = None


def ai_data_consent() -> str:
    """Return pending, granted, or denied for the current disclosure version."""
    global _cached
    try:
        cfg = TargetsConfig()
        try:
            stat = cfg.path.stat()
            key = (str(cfg.path), stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            key = (str(cfg.path), None, None)
        cached = _cached
        if cached is not None and cached[0] == key:
            return cached[1]
        consent = _read_consent(cfg)
        _cached = (key, consent)
        return consent
    except Exception:
        return "pending"


def _read_consent(cfg: TargetsConfig) -> str:
    # A failed read raises, so it reads as pending without being kept.
    cfg.load()
    consent = cfg.get_ai_data_consent()
    if consent == "granted" and cfg.get_ai_data_consent_version() != AI_DATA_CONSENT_VERSION:
        return "pending"
    return consent


def set_ai_data_consent(consent: str) -> None:
    """Persist an explicit decision for the current disclosure version."""
    global _cached
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_ai_data_consent(consent, AI_DATA_CONSENT_VERSION)
    cfg.save()
    _cached = None


def requires_ai_data_consent() -> bool:
    """Require the gate where the distribution asks for opt-in (the Microsoft Store build) and
    in explicit compliance testing."""
    required = os.environ.get("RDST_REQUIRE_AI_DATA_CONSENT", "").strip().lower()
    return consent_opt_in() or required in {"1", "true", "yes", "on"}


def ai_data_sharing_allowed() -> bool:
    return not requires_ai_data_consent() or ai_data_consent() == "granted"
