"""In-process integration tests for schema refresh/profile endpoints.

Exercises the no-semantic-layer error paths; live introspection belongs
to the realdb suite.
"""

from __future__ import annotations

import pytest



@pytest.fixture
def seeded_target_defaults() -> dict:
    return {"name": "schematest", "env": "SCHEMA_PASSWORD"}


async def test_refresh_without_semantic_layer(client, tmp_rdst_home, monkeypatch, seed_target):
    seed_target()
    monkeypatch.setenv("SCHEMA_PASSWORD", "irrelevant")

    response = await client.post(
        "/api/semantic-layer/refresh", json={"target": "schematest"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No semantic layer found" in body["message"]


async def test_profile_without_semantic_layer(client, tmp_rdst_home, monkeypatch, seed_target):
    seed_target()
    monkeypatch.setenv("SCHEMA_PASSWORD", "irrelevant")

    response = await client.post(
        "/api/semantic-layer/profile", json={"target": "schematest"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No semantic layer" in body["message"]


async def test_refresh_locked_when_password_missing(client, tmp_rdst_home, monkeypatch, seed_target):
    seed_target()
    monkeypatch.delenv("SCHEMA_PASSWORD", raising=False)

    response = await client.post(
        "/api/semantic-layer/refresh", json={"target": "schematest"}
    )
    assert response.status_code == 423
