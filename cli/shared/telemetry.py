"""Shared telemetry access."""

from __future__ import annotations

from shared.telemetry_manager import TelemetryManager

telemetry = TelemetryManager()

__all__ = ["telemetry", "TelemetryManager"]
