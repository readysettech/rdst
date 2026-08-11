"""Route contract tests for the legacy advanced load-test endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.query_registry.api.routes import BenchmarkRequest, run_benchmark
from shared.api.target_guard import TargetGuard


@pytest.mark.asyncio
async def test_zero_interval_is_forwarded_without_defaulting_to_100ms():
    captured = {}

    def generator(**kwargs):
        captured.update(kwargs)

        async def empty():
            if False:
                yield {}

        return empty()

    request = BenchmarkRequest(
        queries=[{"identifier": "q", "sql": "SELECT 1"}],
        target="demo",
        mode="interval",
        interval_ms=0,
        concurrency=1,
        duration_seconds=1,
    )
    guard = TargetGuard(
        "demo",
        {"engine": "postgresql", "host": "127.0.0.1"},
        "postgresql",
    )

    with patch(
        "features.query_registry.api.routes._benchmark_generator",
        side_effect=generator,
    ):
        response = await run_benchmark(request, guard)
        await response.body_iterator.aclose()

    assert captured["interval_ms"] == 0
