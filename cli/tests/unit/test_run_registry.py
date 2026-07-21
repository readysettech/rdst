"""
Unit tests for shared.run_registry.RunRegistry.

Detached background runs: start() returns a run_id immediately, every event
is persisted to a per-run JSONL, and subscribers replay missed events before
continuing live. Gates (asyncio.Event) keep the tests deterministic.
"""

import asyncio
import json
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
    async def test_start_returns_immediately_and_runs_detached(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
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
    async def test_live_events_in_order_with_contiguous_seq(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        run_id = registry.start("bootstrap", "imdb", _steps(5))

        events = await _collect(registry, run_id)

        assert [e["event"] for e in events[:-1]] == [f"step{i}" for i in range(5)]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert events[-1]["event"] == "run_end"
        assert events[-1]["data"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_unknown_run_raises_keyerror(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        with pytest.raises(KeyError):
            await _collect(registry, "no_such_run")


class TestReplayThenLive:
    @pytest.mark.asyncio
    async def test_mid_run_subscriber_replays_then_continues(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
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
    async def test_after_seq_resume_skips_seen_events(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        run_id = registry.start("bootstrap", "imdb", _steps(4))
        await _collect(registry, run_id)

        resumed = await _collect(registry, run_id, after_seq=2)

        assert [e["event"] for e in resumed] == ["step2", "step3", "run_end"]
        assert resumed[0]["seq"] == 3


class TestPersistence:
    @pytest.mark.asyncio
    async def test_events_persisted_as_jsonl(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        run_id = registry.start("bootstrap", "imdb", _steps(3))
        await _collect(registry, run_id)

        path = tmp_path / f"{run_id}.jsonl"
        assert path.exists()
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert [rec["event"] for rec in lines] == [
            "step0", "step1", "step2", "run_end",
        ]
        assert lines[0]["data"]["payload"] == "0"
        assert all("ts" in rec for rec in lines)

    @pytest.mark.asyncio
    async def test_fresh_registry_replays_finished_run_from_disk(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        run_id = registry.start("bootstrap", "imdb", _steps(2))
        await _collect(registry, run_id)

        fresh = RunRegistry(base_dir=tmp_path)
        events = await _collect(fresh, run_id, after_seq=1)
        assert [e["event"] for e in events] == ["step1", "run_end"]
        assert fresh.status(run_id) == "done"

    @pytest.mark.asyncio
    async def test_unterminated_file_reads_as_interrupted(self, tmp_path):
        # A run whose process died mid-flight leaves a JSONL with no run_end.
        path = tmp_path / "bootstrap_imdb_20260721_000000_abc123.jsonl"
        path.write_text(
            json.dumps({"seq": 1, "event": "step0", "data": {}, "ts": "t"}) + "\n"
        )

        registry = RunRegistry(base_dir=tmp_path)
        run_id = "bootstrap_imdb_20260721_000000_abc123"
        assert registry.status(run_id) == "interrupted"
        events = await _collect(registry, run_id)
        assert [e["event"] for e in events] == ["step0"]


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_generator_error_becomes_error_event_and_failed_status(
        self, tmp_path
    ):
        registry = RunRegistry(base_dir=tmp_path)

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
    async def test_cancel_ends_stream_with_cancelled_status(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
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
    async def test_needs_key_gates_status_then_returns_to_running(self, tmp_path):
        registry = RunRegistry(base_dir=tmp_path)
        gate = asyncio.Event()
        run_id = registry.start(
            "bootstrap", "imdb", _gated(gate, ["step0", "needs_key"], ["step1"])
        )
        await _wait_status(registry, run_id, "needs_key")

        gate.set()
        events = await _collect(registry, run_id)
        assert [e["event"] for e in events] == [
            "step0", "needs_key", "step1", "run_end",
        ]
        assert registry.status(run_id) == "done"


class TestEviction:
    @pytest.mark.asyncio
    async def test_finished_runs_evicted_beyond_cap_but_replayable_from_disk(
        self, tmp_path
    ):
        registry = RunRegistry(base_dir=tmp_path, max_finished=1)
        first = registry.start("bootstrap", "imdb", _steps(1))
        await _collect(registry, first)
        second = registry.start("bootstrap", "imdb", _steps(1))
        await _collect(registry, second)

        # Oldest finished run left memory but its JSONL replay still works.
        assert registry.status(first) == "done"
        events = await _collect(registry, first)
        assert [e["event"] for e in events] == ["step0", "run_end"]
