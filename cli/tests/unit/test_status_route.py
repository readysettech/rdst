"""GET /api/status names the installation by a hash of its data directory's device id."""

from __future__ import annotations

import asyncio

from starlette.requests import Request

from shared.api.routes import status


def _local_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/status", "headers": [],
                    "client": ("127.0.0.1", 50000), "query_string": b""})


def test_installation_id_follows_the_device_id(tmp_path, monkeypatch):
    from shared.telemetry import telemetry

    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(telemetry, "_device_id", "device-one")
    first = asyncio.run(status.get_status(_local_request())).installation_id
    assert first and len(first) == 16 and "device-one" not in first
    assert asyncio.run(status.get_status(_local_request())).installation_id == first
    monkeypatch.setattr(telemetry, "_device_id", "device-two")
    assert asyncio.run(status.get_status(_local_request())).installation_id != first
