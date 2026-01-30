"""Unit tests for QueryService."""

import pytest
from unittest.mock import Mock, patch

from lib.services.query_service import QueryService
from lib.services.types import (
    QueryBenchmarkProgressEvent,
    QueryCommandInput,
    QueryCompleteEvent,
    QueryErrorEvent,
    QueryStatusEvent,
)


@pytest.mark.asyncio
async def test_execute_emits_status_and_complete():
    service = QueryService()
    mock_result = Mock(ok=True, message="ok", data={"x": 1})

    with patch("lib.cli.query_command.QueryCommand.execute", return_value=mock_result):
        events = [
            event
            async for event in service.execute(
                QueryCommandInput(subcommand="list", kwargs={"limit": 1})
            )
        ]

    assert isinstance(events[0], QueryStatusEvent)
    assert isinstance(events[1], QueryCompleteEvent)
    assert events[1].success is True
    assert events[1].result["ok"] is True


@pytest.mark.asyncio
async def test_execute_emits_error_on_exception():
    service = QueryService()

    with patch(
        "lib.cli.query_command.QueryCommand.execute", side_effect=RuntimeError("boom")
    ):
        events = [
            event
            async for event in service.execute(
                QueryCommandInput(subcommand="list", kwargs={})
            )
        ]

    assert isinstance(events[-1], QueryErrorEvent)
    assert "boom" in events[-1].message


@pytest.mark.asyncio
async def test_stream_benchmark_yields_progress_then_complete():
    service = QueryService()

    def _fake_worker(**kwargs):
        queue = kwargs["progress_queue"]
        queue.put_nowait(
            QueryBenchmarkProgressEvent(
                type="progress",
                elapsed_seconds=1.0,
                total_executions=10,
                total_successes=10,
                total_failures=0,
                qps=10.0,
                queries=[],
            )
        )
        queue.put_nowait(
            QueryBenchmarkProgressEvent(
                type="complete",
                elapsed_seconds=2.0,
                total_executions=20,
                total_successes=20,
                total_failures=0,
                qps=10.0,
                queries=[],
            )
        )

    with patch.object(service, "_run_benchmark_sync", side_effect=_fake_worker):
        events = [
            event
            async for event in service.stream_benchmark(
                queries=["q1"],
                target="prod",
                mode="interval",
                interval_ms=100,
                concurrency=1,
                duration_seconds=5,
                max_count=None,
            )
        ]

    assert len(events) == 2
    assert events[0].type == "progress"
    assert events[1].type == "complete"
