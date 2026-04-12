#!/usr/bin/env python3
"""Integration tests for query registry API behavior."""

import pytest
from httpx import ASGITransport, AsyncClient

from shared.api.app import create_app
from shared.query_registry import hash_sql


@pytest.fixture
def app():
    """Create FastAPI app for testing."""
    return create_app()


@pytest.mark.asyncio
async def test_query_registry_post_strips_comments_and_persists_canonical_sql(
    app, tmp_path, monkeypatch
):
    """POST /api/query-registry should save comment-prefixed SQL successfully."""
    monkeypatch.setattr("shared.constants.RDST_DATA_DIR", tmp_path / ".rdst")

    sql = "-- look up one user\nSELECT * FROM users WHERE id = 42"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post_response = await client.post("/api/query-registry", json={"sql": sql})
        assert post_response.status_code == 200

        post_payload = post_response.json()
        assert post_payload["success"] is True
        assert post_payload["hash"] == hash_sql(sql)

        get_response = await client.get("/api/query-registry?limit=10")
        assert get_response.status_code == 200

    payload = get_response.json()
    queries = payload["queries"]
    assert len(queries) == 1
    assert payload["total"] == 1
    assert payload["limit"] == 10
    assert payload["offset"] == 0
    assert queries[0]["hash"] == post_payload["hash"]
    assert queries[0]["sql"] == "SELECT * FROM users WHERE id = :p1"
