"""
Unit tests for shared.run_registry.RunRegistry.

Detached background runs: start() returns a run_id immediately, every event
is buffered in memory, and subscribers replay missed events before continuing
live. Gates (asyncio.Event) keep the tests deterministic.
"""

import asyncio
from dataclasses import dataclass

import pytest

from shared.run_registry import RunRegistry


@dataclass
class Ev:
    type: str
    payload: str = ""


async def _collect(registry, run_id, after_seq=0):
    async def _go():
        return [e async for e in registry.events(run_id, after_seq=after_seq)]

    return await asyncio.wait_for(_go(), 2.0)


async def _wait_status(registry, run_id, status):
    async def _go():
        while registry.status(run_id) != status:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_go(), 2.0)


def _gated(gate, before, after):
    """Generator yielding `before` events, parking on `gate`, then `after`."""

    async def gen():
        for name in before:
            yield Ev(name)
        await gate.wait()
        for name in after:
            yield Ev(name)

    return gen()


def _steps(n):
    async def gen():
        for i in range(n):
            yield Ev(f"step{i}", payload=str(i))

    return gen()


class TestDetachedExecution:
    @pytest.mark.asyncio
    async def test_describe_preserves_navigation_metadata(self):
        registry = RunRegistry()
        run_id = registry.start(
            "cache_test",
            "imdb",
            _steps(1),
            metadata={"query_hash": "abc123", "label": "Top customers"},
        )

        description = registry.describe(run_id)

        assert description is not None
        assert description["metadata"] == {
            "query_hash": "abc123",
            "label": "Top customers",
        }
        await _collect(registry, run_id)

    @pytest.mark.asyncio
    async def test_start_returns_immediately_and_runs_detached(self):
        registry = RunRegistry()
        gate = asyncio.Event()
        run_id = registry.start(
            "bootstrap", "imdb", _gated(gate, ["started"], ["finished"])
        )
        assert isinstance(run_id, str) and "bootstrap" in run_id
        # The generator is parked on the gate; start() already returned.
        assert registry.status(run_id) in ("running", "needs_key")

        gate.set()
        events = await _collect(registry, run_id)
        assert [e["event"] for e in events] == ["started", "finished", "run_end"]
        assert registry.status(run_id) == "done"

    @pytest.mark.asyncio
    async def test_live_events_in_order_with_contiguous_seq(self):
        registry = RunRegistry()
        run_id = registry.start("bootstrap", "imdb", _steps(5))

        events = await _collect(registry, run_id)

        assert [e["event"] for e in events[:-1]] == [f"step{i}" for i in range(5)]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert events[-1]["event"] == "run_end"
        assert events[-1]["data"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_unknown_run_raises_keyerror(self):
        registry = RunRegistry()
        with pytest.raises(KeyError):
            await _collect(registry, "no_such_run")


class TestReplayThenLive:
    @pytest.mark.asyncio
    async def test_mid_run_subscriber_replays_then_continues(self):
        registry = RunRegistry()
        gate = asyncio.Event()
        run_id = registry.start(
            "bootstrap", "imdb", _gated(gate, ["early1", "early2"], ["late"])
        )
        # Wait until the early events are buffered, then subscribe late.
        await _wait_status(registry, run_id, "running")
        collector = asyncio.create_task(_collect(registry, run_id))
        await asyncio.sleep(0.01)
        gate.set()

        events = await collector
        assert [e["event"] for e in events] == ["early1", "early2", "late", "run_end"]
        assert [e["seq"] for e in events] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_after_seq_resume_skips_seen_events(self):
        registry = RunRegistry()
        run_id = registry.start("bootstrap", "imdb", _steps(4))
        await _collect(registry, run_id)

        resumed = await _collect(registry, run_id, after_seq=2)

        assert [e["event"] for e in resumed] == ["step2", "step3", "run_end"]
        assert resumed[0]["seq"] == 3


class TestProcessLifetime:
    @pytest.mark.asyncio
    async def test_fresh_registry_does_not_know_finished_run(self):
        registry = RunRegistry()
        run_id = registry.start("bootstrap", "imdb", _steps(2))
        await _collect(registry, run_id)

        fresh = RunRegistry()
        assert fresh.status(run_id) is None
        with pytest.raises(KeyError):
            await _collect(fresh, run_id, after_seq=1)


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_yielded_error_event_marks_run_failed(self):
        registry = RunRegistry()

        async def gen():
            yield Ev("annotate_error", payload="bad key")
            yield Ev("never")

        run_id = registry.start("schema_annotation", "imdb", gen())
        events = await _collect(registry, run_id)

        assert registry.status(run_id) == "failed"
        assert [e["event"] for e in events] == [
            "annotate_error",
            "never",
            "run_end",
        ]
        assert events[-1]["data"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_error_event_does_not_close_generator_before_sibling_finishes(self):
        registry = RunRegistry()
        sibling_finished = asyncio.Event()

        async def gen():
            yield Ev("error", payload="deploy failed")
            await asyncio.sleep(0)
            sibling_finished.set()
            yield Ev("schema_finished")

        run_id = registry.start("bootstrap", "imdb", gen())
        events = await _collect(registry, run_id)

        assert sibling_finished.is_set()
        assert [e["event"] for e in events] == [
            "error",
            "schema_finished",
            "run_end",
        ]
        assert events[-1]["data"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_generator_error_becomes_error_event_and_failed_status(self):
        registry = RunRegistry()

        async def gen():
            yield Ev("step0")
            raise RuntimeError("boom")

        run_id = registry.start("bootstrap", "imdb", gen())
        events = await _collect(registry, run_id)

        assert registry.status(run_id) == "failed"
        assert [e["event"] for e in events] == ["step0", "error", "run_end"]
        error = events[1]["data"]
        assert error["code"] == "RUN_FAILED"
        assert "boom" in error["message"]
        assert events[-1]["data"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_cancel_ends_stream_with_cancelled_status(self):
        registry = RunRegistry()
        gate = asyncio.Event()
        run_id = registry.start("bootstrap", "imdb", _gated(gate, ["step0"], ["never"]))
        await _wait_status(registry, run_id, "running")
        collector = asyncio.create_task(_collect(registry, run_id))
        await asyncio.sleep(0.01)

        assert registry.cancel(run_id) is True
        events = await collector

        assert registry.status(run_id) == "cancelled"
        assert events[-1]["event"] == "run_end"
        assert events[-1]["data"]["status"] == "cancelled"
        assert registry.cancel("no_such_run") is False

    @pytest.mark.asyncio
    async def test_needs_key_gates_status_then_returns_to_running(self):
        registry = RunRegistry()
        gate = asyncio.Event()
        run_id = registry.start(
            "bootstrap",
            "imdb",
            _gated(gate, ["step0", "needs_key"], ["step1"]),
            resume_event=gate,
        )
        await _wait_status(registry, run_id, "needs_key")

        assert registry.wake_needs_key() == 1
        events = await _collect(registry, run_id)
        assert [e["event"] for e in events] == [
            "step0", "needs_key", "step1", "run_end",
        ]
        assert registry.status(run_id) == "done"

    @pytest.mark.asyncio
    async def test_wake_needs_key_resumes_every_parked_run(self):
        registry = RunRegistry()
        first_gate = asyncio.Event()
        second_gate = asyncio.Event()
        first = registry.start(
            "bootstrap",
            "imdb",
            _gated(first_gate, ["needs_key"], ["first_resumed"]),
            resume_event=first_gate,
        )
        second = registry.start(
            "bootstrap",
            "analytics",
            _gated(second_gate, ["needs_key"], ["second_resumed"]),
            resume_event=second_gate,
        )
        await _wait_status(registry, first, "needs_key")
        await _wait_status(registry, second, "needs_key")

        assert registry.wake_needs_key() == 2

        first_events, second_events = await asyncio.gather(
            _collect(registry, first),
            _collect(registry, second),
        )
        assert [event["event"] for event in first_events] == [
            "needs_key",
            "first_resumed",
            "run_end",
        ]
        assert [event["event"] for event in second_events] == [
            "needs_key",
            "second_resumed",
            "run_end",
        ]
        assert registry.status(first) == "done"
        assert registry.status(second) == "done"


class TestEviction:
    @pytest.mark.asyncio
    async def test_finished_runs_beyond_cap_are_forgotten(self):
        registry = RunRegistry(max_finished=1)
        first = registry.start("bootstrap", "imdb", _steps(1))
        await _collect(registry, first)
        second = registry.start("bootstrap", "imdb", _steps(1))
        await _collect(registry, second)

        assert registry.status(first) is None
        with pytest.raises(KeyError):
            await _collect(registry, first)
        assert registry.status(second) == "done"


class TestMetadata:
    @pytest.mark.asyncio
    async def test_describe_and_find_active_across_kinds(self):
        registry = RunRegistry()
        gate = asyncio.Event()
        bootstrap = registry.start(
            "bootstrap", "imdb", _gated(gate, ["step0"], [])
        )
        annotation = registry.start(
            "schema_annotation", "imdb", _gated(gate, ["step0"], [])
        )
        await asyncio.sleep(0.01)

        assert registry.find_active("bootstrap", "imdb") == bootstrap
        assert (
            registry.find_active(("bootstrap", "schema_annotation"), "imdb")
            == annotation
        )
        assert registry.describe(annotation) == {
            "run_id": annotation,
            "kind": "schema_annotation",
            "target": "imdb",
            "status": "running",
            "last_seq": 1,
            "metadata": {},
        }

        gate.set()
        await _collect(registry, bootstrap)
        await _collect(registry, annotation)
        assert registry.find_active("bootstrap", "imdb") is None
