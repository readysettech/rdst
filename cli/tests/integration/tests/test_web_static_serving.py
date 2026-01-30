#!/usr/bin/env python3
"""Integration tests for RDST static web serving."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lib.api.app import create_app


def _write_dist_fixture(dist_dir: Path) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "assets").mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text(
        """<!doctype html><html><body><div id='root'>RDST</div></body></html>""",
        encoding="utf-8",
    )
    (dist_dir / "assets" / "app.js").write_text(
        "console.log('rdst');",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_serves_index_at_root(tmp_path):
    dist_dir = tmp_path / "dist"
    _write_dist_fixture(dist_dir)

    app = create_app(static_dist_dir=str(dist_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "RDST" in response.text


@pytest.mark.asyncio
async def test_serves_asset_file(tmp_path):
    dist_dir = tmp_path / "dist"
    _write_dist_fixture(dist_dir)

    app = create_app(static_dist_dir=str(dist_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log('rdst');" in response.text


@pytest.mark.asyncio
async def test_spa_fallback_for_client_routes(tmp_path):
    dist_dir = tmp_path / "dist"
    _write_dist_fixture(dist_dir)

    app = create_app(static_dist_dir=str(dist_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/query-registry")

    assert response.status_code == 200
    assert "RDST" in response.text


@pytest.mark.asyncio
async def test_api_routes_remain_available_with_static_mode(tmp_path):
    dist_dir = tmp_path / "dist"
    _write_dist_fixture(dist_dir)

    app = create_app(static_dist_dir=str(dist_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_unknown_api_path_not_handled_by_spa(tmp_path):
    dist_dir = tmp_path / "dist"
    _write_dist_fixture(dist_dir)

    app = create_app(static_dist_dir=str(dist_dir))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/does-not-exist")

    assert response.status_code == 404
