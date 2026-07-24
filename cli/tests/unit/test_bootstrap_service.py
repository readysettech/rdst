"""
Unit tests for TargetBootstrapService.

The orchestrator composes injected fakes; assertions cover stage ordering
within tracks, the connection-test abort, refresh-vs-init idempotency, the
needs_key gate (park, poll, and resume), and track isolation.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from features.bootstrap.events import BootstrapNeedsKeyEvent, BootstrapStageEvent
from features.bootstrap.service import BootstrapOptions, TargetBootstrapService
from shared.run_registry import RunRegistry
from shared.service_events import ErrorEvent


@dataclass
class ChildEv:
    type: str
    message: str = ""


@dataclass
class AnnotateComplete:
    type: str = "annotate_complete"
    success: bool = True
    message: str = "Annotation complete"


class FakeConfigure:
    def __init__(self, success=True, message="Connection successful"):
        self._result = {
            "success": success,
            "message": message,
            "server_version": "PostgreSQL 17",
        }

    async def perform_connection_test(self, config):
        return dict(self._result)


@dataclass
class FakeSchema:
    exists: bool = False
    profile_ok: bool = True
    calls: list = field(default_factory=list)

    def get_status(self, target):
        return SimpleNamespace(exists=self.exists)

    def init(self, target, target_config):
        self.calls.append("init")
        return SimpleNamespace(success=True, tables=3, error=None)

    def refresh(self, target, target_config):
        self.calls.append("refresh")
        return {"ok": True, "message": "refreshed"}

    def profile(self, target, target_config):
        self.calls.append("profile")
        return {"ok": self.profile_ok, "message": "profiled"}


class FakeAnnotate:
    def __init__(self, events=None):
        self.events = events or [
            ChildEv("annotate_started", "starting"),
            ChildEv("annotate_complete", "Annotated 3 table(s)"),
        ]
        self.calls = 0

    async def annotate(self, target, target_config, table_name=None, sample_rows=5):
        self.calls += 1
        for event in self.events:
            yield event


def _service(configure=None, schema=None, annotate=None, validator=None):
    return TargetBootstrapService(
        configure_service=configure or FakeConfigure(),
        schema_service=schema or FakeSchema(),
        annotate_service=annotate or FakeAnnotate(),
        key_validator=validator or (lambda: {"valid": True, "reason": "ok"}),
    )


def _opts(**kwargs):
    defaults = {"key_poll_seconds": 0.01}
    defaults.update(kwargs)
    return BootstrapOptions(**defaults)


async def _run(service, options=None):
    return [
        e
        async for e in service.run("imdb", {"engine": "postgresql"}, options or _opts())
    ]


async def _drain(events):
    return [event async for event in events]


async def _collect_run(registry, run_id):
    async def collect():
        return [event async for event in registry.events(run_id)]

    return await asyncio.wait_for(collect(), 2.0)


async def _wait_run_status(registry, run_id, expected):
    async def wait():
        while registry.status(run_id) != expected:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), 2.0)


def _stages(events, stage):
    return [
        (e.status, e.message)
        for e in events
        if isinstance(e, BootstrapStageEvent) and e.stage == stage
    ]


class TestConnectionStage:
    @pytest.mark.asyncio
    async def test_connection_failure_aborts_everything(self):
        schema = FakeSchema()
        service = _service(
            configure=FakeConfigure(success=False, message="auth failed"),
            schema=schema,
        )

        events = await _run(service)

        statuses = _stages(events, "connection_test")
        assert statuses[0][0] == "started"
        assert statuses[-1] == ("failed", "auth failed")
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "CONNECTION_FAILED"
        assert schema.calls == []

    @pytest.mark.asyncio
    async def test_happy_path_stage_order_within_tracks(self):
        events = await _run(_service())

        for stage in ("connection_test", "structure", "profile", "annotate"):
            statuses = [s for s, _m in _stages(events, stage)]
            assert statuses[0] == "started"
            assert statuses[-1] == "done"
        # Schema-track ordering: structure completes before profile starts,
        # profile before annotate.
        flat = [
            (e.stage, e.status)
            for e in events
            if isinstance(e, BootstrapStageEvent)
            and e.stage in ("structure", "profile", "annotate")
        ]
        assert flat.index(("structure", "done")) < flat.index(("profile", "started"))
        assert flat.index(("profile", "done")) < flat.index(("annotate", "started"))


class TestSchemaTrack:
    @pytest.mark.asyncio
    async def test_existing_layer_refreshes_instead_of_init(self):
        schema = FakeSchema(exists=True)
        await _run(_service(schema=schema))
        assert "refresh" in schema.calls
        assert "init" not in schema.calls

    @pytest.mark.asyncio
    async def test_missing_layer_inits(self):
        schema = FakeSchema(exists=False)
        await _run(_service(schema=schema))
        assert "init" in schema.calls
        assert "refresh" not in schema.calls

    @pytest.mark.asyncio
    async def test_annotate_error_marks_stage_failed(self):
        annotate = FakeAnnotate(events=[ChildEv("annotate_error", "no credit")])
        events = await _run(_service(annotate=annotate))
        assert _stages(events, "annotate")[-1] == ("failed", "no credit")

    @pytest.mark.asyncio
    async def test_partial_annotation_marks_stage_failed(self):
        annotate = FakeAnnotate(
            events=[
                AnnotateComplete(
                    success=False,
                    message="Annotated 2 tables; 1 AI request failed",
                )
            ]
        )

        events = await _run(_service(annotate=annotate))

        assert _stages(events, "annotate")[-1] == (
            "failed",
            "Annotated 2 tables; 1 AI request failed",
        )

    @pytest.mark.asyncio
    async def test_annotate_disabled_is_skipped(self):
        annotate = FakeAnnotate()
        events = await _run(_service(annotate=annotate), _opts(annotate=False))
        assert _stages(events, "annotate")[-1][0] == "skipped"
        assert annotate.calls == 0


class TestKeyGate:
    @pytest.mark.asyncio
    async def test_key_save_wakes_gate_before_poll_interval(self):
        results = [
            {"valid": False, "reason": "no_key"},
            {"valid": True, "reason": "ok"},
        ]
        annotate = FakeAnnotate()
        service = _service(
            annotate=annotate,
            validator=lambda: results.pop(0) if results else {"valid": True},
        )
        key_wakeup = asyncio.Event()
        events = service.run(
            "imdb",
            {"engine": "postgresql"},
            _opts(key_poll_seconds=30.0),
            key_wakeup=key_wakeup,
        )

        seen = []
        async for event in events:
            seen.append(event)
            if isinstance(event, BootstrapNeedsKeyEvent):
                key_wakeup.set()
                break

        seen.extend(await asyncio.wait_for(_drain(events), timeout=1.0))

        assert annotate.calls == 1
        assert _stages(seen, "annotate")[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_missing_key_emits_needs_key_then_resumes_when_key_lands(self):
        results = [{"valid": False, "reason": "no_key"}, {"valid": True, "reason": "ok"}]
        annotate = FakeAnnotate()
        service = _service(
            annotate=annotate, validator=lambda: results.pop(0) if results else {"valid": True}
        )

        events = await _run(service)

        assert any(isinstance(e, BootstrapNeedsKeyEvent) for e in events)
        assert annotate.calls == 1
        assert _stages(events, "annotate")[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_rejected_key_stays_parked_until_valid_replacement(self):
        results = [
            {"valid": False, "reason": "no_key"},
            {"valid": False, "reason": "rejected"},
            {"valid": True, "reason": "ok"},
        ]
        rejected_checked = threading.Event()
        validation_calls = 0

        def validate_key():
            nonlocal validation_calls
            result = results.pop(0)
            validation_calls += 1
            if result["reason"] == "rejected":
                rejected_checked.set()
            return result

        annotate = FakeAnnotate()
        service = _service(annotate=annotate, validator=validate_key)
        registry = RunRegistry()
        key_wakeup = asyncio.Event()
        run_id = registry.start(
            "bootstrap",
            "imdb",
            service.run(
                "imdb",
                {"engine": "postgresql"},
                _opts(key_poll_seconds=30.0),
                key_wakeup=key_wakeup,
            ),
            resume_event=key_wakeup,
        )
        await _wait_run_status(registry, run_id, "needs_key")

        assert registry.wake_needs_key() == 1
        assert await asyncio.to_thread(rejected_checked.wait, 1.0)
        assert registry.status(run_id) == "needs_key"
        assert annotate.calls == 0

        assert registry.wake_needs_key() == 1
        events = await _collect_run(registry, run_id)

        assert validation_calls == 3
        assert annotate.calls == 1
        assert registry.status(run_id) == "done"
        assert [event["event"] for event in events].count("needs_key") == 1

    @pytest.mark.asyncio
    async def test_key_never_lands_remains_parked(self):
        service = _service(
            validator=lambda: {"valid": False, "reason": "no_key"}
        )

        wait = asyncio.create_task(service._await_key(_opts(), asyncio.Event()))
        await asyncio.sleep(0.05)

        assert not wait.done()
        wait.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait
