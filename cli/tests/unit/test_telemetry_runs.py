"""Tests for `TelemetryManager.command_run` / `command_run_sync`.

Covers the four code paths through the context manager:

1. Success — a terminal success event is observed; `success=True` finalizes.
2. Error event — a terminal error event is observed; `success=False` and
   `error_type` are captured.
3. Exception — the body raises; `success=False` and `error_type=<exc class>`
   are captured and the exception re-raises.
4. First-analyze — `analyze` runs route through `track_analyze`, which fires
   `first_analyze` on the first successful run.

Plus generic-command behavior (ask/scan/top emit `<name>_run` with stats).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_rdst_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _make_tm(temp_rdst_dir: Path, *, successful_analyzes: int = 0):
    """Build a TelemetryManager rooted at a temp dir, with telemetry enabled
    so internal stat updates run, but with PostHog disabled (no real network)."""
    from shared.telemetry_manager import TelemetryManager

    stats_file = temp_rdst_dir / "stats.json"
    stats_file.write_text(json.dumps({"successful_analyzes": successful_analyzes}))

    tm = TelemetryManager()
    tm._rdst_dir = temp_rdst_dir
    tm._enabled = True
    tm._initialized = True
    tm._device_id = "test-device-id"
    tm._stats = {"successful_analyzes": successful_analyzes}
    return tm


@dataclass
class _FakeComplete:
    """Mimics analyze CompleteEvent shape for the detector."""

    success: bool
    query_hash: Optional[str] = None


@dataclass
class _FakeError:
    """Mimics analyze ErrorEvent shape for the detector."""

    message: str = "boom"
    stage: Optional[str] = None


def _fake_analyze_detector(event: Any):
    """Standalone detector that mirrors analyze_terminal_detector's contract
    without importing feature code (keeps this test independent)."""
    from shared.telemetry_manager import TerminalState

    if isinstance(event, _FakeComplete):
        return TerminalState(
            success=event.success,
            error_type=None if event.success else "complete_unsuccessful",
            extra={"query_hash": event.query_hash} if event.query_hash else {},
        )
    if isinstance(event, _FakeError):
        return TerminalState(success=False, error_type=event.stage or "error")
    return None


# ---------------------------------------------------------------------------
# Path 1: terminal success event
# ---------------------------------------------------------------------------


class TestSuccessPath:
    def test_sync_success_routes_through_track_analyze(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                target_engine="postgresql",
                mode="standard",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeComplete(success=True, query_hash="abc123"))

        mock_track_analyze.assert_called_once()
        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is True
        assert kwargs["query_hash"] == "abc123"
        assert kwargs["target_engine"] == "postgresql"
        assert kwargs["source"] == "cli"
        assert kwargs["mode"] == "standard"
        assert kwargs["error_type"] is None
        assert isinstance(kwargs["duration_ms"], int) and kwargs["duration_ms"] >= 0

    def test_async_success(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        async def run_it():
            async with tm.command_run(
                "analyze",
                source="web",
                target_engine="mysql",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeComplete(success=True, query_hash="xyz"))

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            asyncio.run(run_it())

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is True
        assert kwargs["source"] == "web"
        assert kwargs["target_engine"] == "mysql"


# ---------------------------------------------------------------------------
# Path 2: terminal error event
# ---------------------------------------------------------------------------


class TestErrorEventPath:
    def test_error_event_marks_failure(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeError(stage="explain"))

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["error_type"] == "explain"

    def test_unsuccessful_complete_sets_error_type(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeComplete(success=False))

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["error_type"] == "complete_unsuccessful"

    def test_failure_dominates_later_success(self, temp_rdst_dir):
        """A late `success=True` event must not overwrite an earlier failure."""
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeError(stage="parse"))
                run.observe(_FakeComplete(success=True))

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["error_type"] == "parse"


# ---------------------------------------------------------------------------
# Path 3: exception in the body
# ---------------------------------------------------------------------------


class TestExceptionPath:
    def test_sync_exception_finalizes_then_reraises(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with pytest.raises(RuntimeError, match="boom"):
                with tm.command_run_sync(
                    "analyze",
                    source="cli",
                    terminal_detector=_fake_analyze_detector,
                ):
                    raise RuntimeError("boom")

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["error_type"] == "RuntimeError"

    def test_async_exception_finalizes_then_reraises(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        async def run_it():
            async with tm.command_run(
                "analyze",
                source="web",
                terminal_detector=_fake_analyze_detector,
            ):
                raise ValueError("kaboom")

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with pytest.raises(ValueError, match="kaboom"):
                asyncio.run(run_it())

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        assert kwargs["error_type"] == "ValueError"

    def test_caller_set_error_type_wins(self, temp_rdst_dir):
        """If the caller has classified the failure (e.g. `input_error`), the
        exception path must not overwrite it."""
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze", source="cli",
            ) as run:
                run.success = False
                run.error_type = "input_error"
                # No raise — caller already classified.

        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["error_type"] == "input_error"


# ---------------------------------------------------------------------------
# Path 4: first-analyze fires through `track_analyze`
# ---------------------------------------------------------------------------


class TestFirstAnalyzePath:
    def test_first_success_fires_first_analyze(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir, successful_analyzes=0)

        with patch.object(tm, "track") as mock_track:
            with tm.command_run_sync(
                "analyze",
                source="web",
                target_engine="postgresql",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeComplete(success=True, query_hash="qh"))

            first_analyze_calls = [
                c for c in mock_track.call_args_list if c.args and c.args[0] == "first_analyze"
            ]
            assert len(first_analyze_calls) == 1
            props = first_analyze_calls[0].args[1]
            assert props["display_name"] == "RDST First Analyze"
            assert props["source"] == "web"
            assert props["target_engine"] == "postgresql"

    def test_subsequent_runs_do_not_refire(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir, successful_analyzes=5)

        with patch.object(tm, "track") as mock_track:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(_FakeComplete(success=True))

            first_analyze_calls = [
                c for c in mock_track.call_args_list if c.args and c.args[0] == "first_analyze"
            ]
            assert len(first_analyze_calls) == 0


# ---------------------------------------------------------------------------
# Generic command behavior (ask/scan/top route through track_with_stats)
# ---------------------------------------------------------------------------


class TestGenericCommandFinalize:
    def test_ask_emits_ask_run_with_extras(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_with_stats") as mock_track:
            with tm.command_run_sync(
                "ask",
                source="web",
                target_engine="postgresql",
                agent_mode=True,
                dry_run=False,
            ) as run:
                run.success = True
                run.extra["query_hash"] = "qh-1"

        mock_track.assert_called_once()
        event_name, props = mock_track.call_args.args
        assert event_name == "ask_run"
        assert props["success"] is True
        assert props["source"] == "web"
        assert props["target_engine"] == "postgresql"
        assert props["agent_mode"] is True
        assert props["dry_run"] is False  # default-falsy values still surface
        assert props["query_hash"] == "qh-1"
        assert "duration_ms" in props

    def test_top_increments_total_top_runs(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)
        tm._stats = {"total_top_runs": 7}

        with patch.object(tm, "track_with_stats"):
            with tm.command_run_sync(
                "top",
                source="web",
                target_engine="mysql",
            ) as run:
                run.success = True

        assert tm._stats["total_top_runs"] == 8

    def test_top_realtime_emits_separate_event_but_shares_counter(self, temp_rdst_dir):
        """Historical `top` and `top_realtime` are distinct PostHog events
        (different success semantics) but share `total_top_runs` so NPS
        prompt pacing reflects all top-feature usage."""
        tm = _make_tm(temp_rdst_dir)
        tm._stats = {"total_top_runs": 3}

        with patch.object(tm, "track_with_stats") as mock_track:
            with tm.command_run_sync(
                "top_realtime",
                source="web",
                target_engine="postgresql",
                duration=30,
            ) as run:
                run.success = True

        event_name, props = mock_track.call_args.args
        assert event_name == "top_realtime_run"
        assert props["duration"] == 30
        assert tm._stats["total_top_runs"] == 4  # shared counter

    def test_scan_increments_total_scans(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)
        tm._stats = {}

        with patch.object(tm, "track_with_stats"):
            with tm.command_run_sync(
                "scan",
                source="web",
                target_engine="postgresql",
            ) as run:
                run.success = True

        assert tm._stats["total_scans"] == 1

    def test_unknown_command_does_not_increment_stats(self, temp_rdst_dir):
        """Generic finalize is permissive: unknown commands still emit the
        PostHog event but don't touch any stat counter."""
        tm = _make_tm(temp_rdst_dir)
        before = dict(tm._stats)

        with patch.object(tm, "track_with_stats") as mock_track:
            with tm.command_run_sync(
                "wibble",
                source="cli",
            ) as run:
                run.success = True

        event_name, _ = mock_track.call_args.args
        assert event_name == "wibble_run"
        # No new stat keys created.
        assert set(tm._stats.keys()) == set(before.keys())


# ---------------------------------------------------------------------------
# Detector contract (strict typing — no surprises from event.type strings)
# ---------------------------------------------------------------------------


class TestFinalizerRegistry:
    def test_register_command_finalizer_overrides_generic(self, temp_rdst_dir):
        """A registered finalizer takes priority over `_generic_finalizer`."""
        tm = _make_tm(temp_rdst_dir)

        captured: list = []

        def my_finalizer(run):
            captured.append((run.name, run.success, dict(run.extra)))

        tm.register_command_finalizer("custom", my_finalizer)

        with patch.object(tm, "track_with_stats") as mock_generic:
            with tm.command_run_sync("custom", source="cli", flag=True) as run:
                run.success = True
                run.extra["k"] = "v"

        # Bespoke finalizer ran, generic did not.
        assert captured == [("custom", True, {"flag": True, "k": "v"})]
        mock_generic.assert_not_called()

    def test_finalizer_exception_is_swallowed(self, temp_rdst_dir):
        """A buggy finalizer must not break the caller."""
        tm = _make_tm(temp_rdst_dir)

        def bad_finalizer(run):
            raise RuntimeError("finalizer broke")

        tm.register_command_finalizer("buggy", bad_finalizer)

        # No exception should propagate.
        with tm.command_run_sync("buggy", source="cli") as run:
            run.success = True


class TestDetectorIsStrictlyTyped:
    def test_unrelated_event_with_matching_type_string_is_ignored(self, temp_rdst_dir):
        """A duck-typed object that happens to have `type='complete'` must
        NOT be treated as terminal — the detector uses isinstance, not
        magic strings."""
        tm = _make_tm(temp_rdst_dir)

        # Object with the magic string but wrong class.
        class Imposter:
            type = "complete"
            success = True
            query_hash = "fake"

        with patch.object(tm, "track_analyze") as mock_track_analyze:
            with tm.command_run_sync(
                "analyze",
                source="cli",
                terminal_detector=_fake_analyze_detector,
            ) as run:
                run.observe(Imposter())  # detector returns None (not isinstance)

        # `success` was never set → finalize sees None → reports success=False
        # (but this proves observe ignored the imposter rather than treating
        # it as a real terminal).
        kwargs = mock_track_analyze.call_args.kwargs
        assert kwargs["success"] is False
        # And no error_type was assigned by observe.
        assert kwargs["error_type"] is None

    def test_observe_without_detector_is_noop(self, temp_rdst_dir):
        tm = _make_tm(temp_rdst_dir)

        with patch.object(tm, "track_with_stats") as mock_track:
            with tm.command_run_sync("ask", source="web") as run:
                # No detector configured — observe must not change anything.
                run.observe(_FakeComplete(success=True))
                assert run.success is None
                run.success = True  # caller assigns explicitly

        _, props = mock_track.call_args.args
        assert props["success"] is True
