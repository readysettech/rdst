"""
RDST Telemetry Module

Provides anonymous usage tracking via PostHog and crash reporting via Sentry.
All telemetry is pseudonymous (device_id) with optional email for feedback.

Usage:
    from lib.telemetry import telemetry

    # Track an event
    telemetry.track("analyze_run", {"query_hash": "abc123", "mode": "interactive"})

    # Track with device stats
    telemetry.track_with_stats("analyze_run", {"query_hash": "abc123"})

    # Report a crash (goes to Sentry)
    telemetry.report_crash(exception, {"command": "analyze", "query_hash": "abc123"})

    # Submit user feedback
    telemetry.submit_feedback(query_hash="abc123", reason="LLM suggestion was wrong", email="optional@email.com")
"""

from .telemetry_manager import TelemetryManager

# Global singleton
telemetry = TelemetryManager()

__all__ = ['telemetry', 'TelemetryManager']
