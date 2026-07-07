"""In-process integration tests for schema refresh/profile endpoints.

Exercises the no-semantic-layer error paths; live introspection belongs
to the realdb suite.
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


def _seed_target(name: str = "schematest") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(name, {
        "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
        "database": "appdb", "user": "appuser", "password_env": "SCHEMA_PASSWORD",
    })
    cfg.save()


async def test_refresh_without_semantic_layer(client, tmp_rdst_home, monkeypatch):
    _seed_target()
    monkeypatch.setenv("SCHEMA_PASSWORD", "irrelevant")

    response = await client.post(
        "/api/semantic-layer/refresh", json={"target": "schematest"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No semantic layer found" in body["message"]


async def test_profile_without_semantic_layer(client, tmp_rdst_home, monkeypatch):
    _seed_target()
    monkeypatch.setenv("SCHEMA_PASSWORD", "irrelevant")

    response = await client.post(
        "/api/semantic-layer/profile", json={"target": "schematest"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No semantic layer" in body["message"]


async def test_refresh_locked_when_password_missing(client, tmp_rdst_home, monkeypatch):
    _seed_target()
    monkeypatch.delenv("SCHEMA_PASSWORD", raising=False)

    response = await client.post(
        "/api/semantic-layer/refresh", json={"target": "schematest"}
    )
    assert response.status_code == 423
