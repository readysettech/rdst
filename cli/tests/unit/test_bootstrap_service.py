"""
Unit tests for TargetBootstrapService.

The orchestrator composes injected fakes; assertions cover stage ordering
within tracks, the connection-test abort, refresh-vs-init idempotency, the
needs_key gate (park, poll, resume or skip), and track isolation.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from features.bootstrap.events import BootstrapNeedsKeyEvent, BootstrapStageEvent
from features.bootstrap.service import BootstrapOptions, TargetBootstrapService
from shared.service_events import ErrorEvent


@dataclass
class ChildEv:
    type: str
    message: str = ""


@dataclass
class DeployComplete:
    type: str = "deploy_complete"
    success: bool = True
    endpoint: str = "postgresql://127.0.0.1:5433/db"
    message: str = ""


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


class FakeCache:
    def __init__(self, events=None):
        self.events = events if events is not None else [
            ChildEv("progress", "deploying"),
            DeployComplete(),
        ]
        self.inputs = []

    async def deploy(self, input_data, options):
        self.inputs.append((input_data, options))
        for event in self.events:
            yield event


def _service(configure=None, schema=None, annotate=None, cache=None, validator=None):
    return TargetBootstrapService(
        configure_service=configure or FakeConfigure(),
        schema_service=schema or FakeSchema(),
        annotate_service=annotate or FakeAnnotate(),
        cache_service=cache or FakeCache(),
        key_validator=validator or (lambda: {"valid": True, "reason": "ok"}),
    )


def _opts(**kwargs):
    defaults = {"key_wait_seconds": 0.05, "key_poll_seconds": 0.01}
    defaults.update(kwargs)
    return BootstrapOptions(**defaults)


async def _run(service, options=None):
    return [
        e
        async for e in service.run("imdb", {"engine": "postgresql"}, options or _opts())
    ]


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
        cache = FakeCache()
        service = _service(
            configure=FakeConfigure(success=False, message="auth failed"),
            schema=schema,
            cache=cache,
        )

        events = await _run(service)

        statuses = _stages(events, "connection_test")
        assert statuses[0][0] == "started"
        assert statuses[-1] == ("failed", "auth failed")
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "CONNECTION_FAILED"
        assert schema.calls == []
        assert cache.inputs == []

    @pytest.mark.asyncio
    async def test_happy_path_stage_order_within_tracks(self):
        events = await _run(_service())

        for stage in ("connection_test", "structure", "profile", "annotate"):
            statuses = [s for s, _m in _stages(events, stage)]
            assert statuses[0] == "started"
            assert statuses[-1] == "done"
        deploy_statuses = [s for s, _m in _stages(events, "deploy")]
        assert deploy_statuses[0] == "started"
        assert deploy_statuses[-1] == "done"
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
    async def test_annotate_disabled_is_skipped(self):
        annotate = FakeAnnotate()
        events = await _run(_service(annotate=annotate), _opts(annotate=False))
        assert _stages(events, "annotate")[-1][0] == "skipped"
        assert annotate.calls == 0


class TestKeyGate:
    @pytest.mark.asyncio
    async def test_missing_key_emits_needs_key_then_resumes_when_key_lands(self):
        results = [{"valid": False, "reason": "no_key"}, {"valid": True, "reason": "ok"}]
        annotate = FakeAnnotate()
        service = _service(
            annotate=annotate, validator=lambda: results.pop(0) if results else {"valid": True}
        )

        events = await _run(service, _opts(key_wait_seconds=1.0))

        assert any(isinstance(e, BootstrapNeedsKeyEvent) for e in events)
        assert annotate.calls == 1
        assert _stages(events, "annotate")[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_key_never_lands_skips_annotate_but_deploy_completes(self):
        annotate = FakeAnnotate()
        service = _service(
            annotate=annotate, validator=lambda: {"valid": False, "reason": "no_key"}
        )

        events = await _run(service)

        assert any(isinstance(e, BootstrapNeedsKeyEvent) for e in events)
        assert annotate.calls == 0
        assert _stages(events, "annotate")[-1][0] == "skipped"
        assert _stages(events, "deploy")[-1][0] == "done"


class TestDeployTrack:
    @pytest.mark.asyncio
    async def test_deploy_disabled_emits_no_deploy_stage(self):
        cache = FakeCache()
        events = await _run(_service(cache=cache), _opts(deploy=False))
        assert _stages(events, "deploy") == []
        assert cache.inputs == []

    @pytest.mark.asyncio
    async def test_deploy_options_forwarded(self):
        cache = FakeCache()
        await _run(_service(cache=cache), _opts(deploy_mode="kubernetes"))
        input_data, options = cache.inputs[0]
        assert input_data.target == "imdb"
        assert options.mode == "kubernetes"
        assert options.yes is True

    @pytest.mark.asyncio
    async def test_deploy_failure_does_not_kill_schema_track(self):
        class ExplodingCache(FakeCache):
            async def deploy(self, input_data, options):
                raise RuntimeError("docker not found")
                yield  # pragma: no cover

        events = await _run(_service(cache=ExplodingCache()))

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert any("docker not found" in e.message for e in errors)
        assert _stages(events, "annotate")[-1][0] == "done"

    @pytest.mark.asyncio
    async def test_child_events_surface_as_progress_with_detail(self):
        events = await _run(_service())
        progress = [
            e
            for e in events
            if isinstance(e, BootstrapStageEvent)
            and e.stage == "deploy"
            and e.status == "progress"
        ]
        assert progress
        assert progress[0].detail["type"] in ("progress", "deploy_complete")
