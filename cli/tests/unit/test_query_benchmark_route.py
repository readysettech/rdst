"""Route contract tests for the legacy advanced load-test endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from features.query_registry.api.routes import BenchmarkRequest, run_benchmark
from shared.api.target_guard import TargetGuard


def _http_request(**headers: str) -> Request:
    """A loopback POST, or a remote one once a source header is supplied."""
    raw = [(b"host", b"127.0.0.1:8787")]
    raw += [(name.encode(), value.encode()) for name, value in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/query-registry/benchmark",
            "scheme": "http",
            "query_string": b"",
            "headers": raw,
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8787),
        }
    )


def _request() -> BenchmarkRequest:
    return BenchmarkRequest(
        queries=[{"identifier": "q", "sql": "SELECT 1"}],
        target="demo",
        mode="interval",
        interval_ms=0,
        concurrency=1,
        duration_seconds=1,
    )


def _guard() -> TargetGuard:
    return TargetGuard(
        "demo",
        {"engine": "postgresql", "host": "127.0.0.1"},
        "postgresql",
    )


@pytest.mark.asyncio
async def test_zero_interval_is_forwarded_without_defaulting_to_100ms():
    captured = {}

    def generator(**kwargs):
        captured.update(kwargs)

        async def empty():
            if False:
                yield {}

        return empty()

    with patch(
        "features.query_registry.api.routes._benchmark_generator",
        side_effect=generator,
    ):
        response = await run_benchmark(_request(), _http_request(), _guard())
        await response.body_iterator.aclose()

    assert captured["interval_ms"] == 0


@pytest.mark.asyncio
async def test_cross_site_benchmark_is_forbidden():
    """This endpoint runs the SQL its body carries; a foreign page cannot."""
    started = []

    with patch(
        "features.query_registry.api.routes._benchmark_generator",
        side_effect=lambda **kwargs: started.append(kwargs),
    ):
        with pytest.raises(HTTPException) as raised:
            await run_benchmark(
                _request(),
                _http_request(origin="https://evil.example"),
                _guard(),
            )

    assert raised.value.status_code == 403
    assert started == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lanes", [["readyset"], ["origin", "readyset"], ["origin"]])
async def test_benchmarks_check_source_credentials_without_deployed_cache(lanes):
    import json
    from features.query_registry.api.routes import require_benchmark_target

    request = _http_request()
    request._body = json.dumps({"target": "demo", "lanes": lanes}).encode()
    with patch("shared.api.target_guard.ensure_target_password", return_value=_guard()) as password:
        guard = await require_benchmark_target(request)
    password.assert_called_once_with("demo")
    assert guard.target_name == "demo"


@pytest.mark.asyncio
async def test_readyset_only_requires_source_credentials():
    import json
    from features.query_registry.api.routes import require_benchmark_target

    request = _http_request()
    request._body = json.dumps({"target": "demo", "lanes": ["readyset"]}).encode()
    with patch(
        "shared.api.target_guard.ensure_target_password",
        side_effect=HTTPException(status_code=423, detail="Source password required"),
    ) as password:
        with pytest.raises(HTTPException) as raised:
            await require_benchmark_target(request)
    assert raised.value.status_code == 423
    password.assert_called_once_with("demo")
