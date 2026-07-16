"""Strict, file-backed service fixtures for RDST browser integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter


_MISSING = object()


class FixtureStore:
    """Read serial, fail-closed service responses written by Playwright."""

    def __init__(self, fixture_home: str | os.PathLike[str] | None = None) -> None:
        if fixture_home is None:
            fixture_home = os.environ.get("RDST_E2E_HOME")
        if not fixture_home:
            raise RuntimeError("RDST_E2E_HOME is required by the web E2E server")
        self.path = Path(fixture_home) / "backend-fixtures.json"
        self._revision: str | None = None
        self._operations: dict[str, list[dict[str, Any]]] = {}
        self._indices: dict[str, int] = {}
        self._lock = threading.Lock()

    def _reload(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        revision = str(payload.get("revision", ""))
        if revision == self._revision:
            return
        operations = payload.get("operations")
        if not isinstance(operations, dict):
            raise RuntimeError("backend-fixtures.json must contain an operations object")
        self._revision = revision
        self._operations = operations
        self._indices = {}

    def take(self, operation: str, *, default: Any = _MISSING) -> dict[str, Any]:
        with self._lock:
            self._reload()
            responses = self._operations.get(operation)
            if not responses:
                if default is not _MISSING:
                    return default
                raise RuntimeError(
                    f"No server-side browser fixture configured for '{operation}'"
                )

            index = self._indices.get(operation, 0)
            if index >= len(responses):
                response = responses[-1]
                if response.get("repeat") is not True:
                    raise RuntimeError(
                        f"Server-side browser fixture exhausted for '{operation}' "
                        f"after {len(responses)} response(s)"
                    )
            else:
                response = responses[index]
            self._indices[operation] = index + 1

        error = response.get("error")
        if error is not None:
            raise HTTPException(
                status_code=int(error.get("status", 500)),
                detail=str(error.get("detail", "Fixture error")),
            )
        return response

    def value(self, operation: str, *, default: Any = _MISSING) -> Any:
        response = self.take(
            operation,
            default={"value": default} if default is not _MISSING else _MISSING,
        )
        if "value" not in response:
            raise RuntimeError(f"Fixture '{operation}' must contain a value")
        return response["value"]

    async def events(
        self, operation: str, adapter: TypeAdapter
    ) -> AsyncIterator[Any]:
        async for event in self.replay(self.take(operation), adapter):
            yield event

    @staticmethod
    async def replay(
        response: dict[str, Any], adapter: TypeAdapter
    ) -> AsyncIterator[Any]:
        """Yield the response's events validated against the production model."""
        events = response.get("events")
        if not isinstance(events, list):
            raise RuntimeError("Fixture response must contain an events list")
        await asyncio.sleep(float(response.get("delay_ms", 0)) / 1000)
        for raw_event in events:
            event = dict(raw_event)
            await asyncio.sleep(float(event.pop("_delay_ms", 0)) / 1000)
            yield adapter.validate_python(event)
