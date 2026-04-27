"""Integration tests for Init API endpoints.

These drive the real `InitService` end-to-end against an isolated
`~/.rdst/` (per-test tmp dir via the `tmp_rdst_home` fixture). No
service-layer mocks — config is read from / written to a real on-disk
TOML file.

The validate-with-targets case requires live DB connectivity and lives
in `test_realdb_init_api.py` (slice 4).
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


async def test_init_status_endpoint_empty_state(client, tmp_rdst_home, monkeypatch):
    """GET /api/init/status on a fresh ~/.rdst/ reports an uninitialized
    workspace with no targets and no LLM."""
    # CI may have ANTHROPIC_API_KEY set (pulled from Secrets Manager); strip
    # it so this test's "no LLM" branch is exercised regardless.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = await client.get("/api/init/status")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["initialized"] is False
    assert payload["targets"] == []
    assert payload["default_target"] is None
    assert payload["llm_configured"] is False


async def test_init_status_endpoint_reports_configured_targets(client, tmp_rdst_home):
    """GET /api/init/status reflects targets persisted in the real config."""
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": "PROD_PASSWORD",
        },
    )
    cfg.set_default("prod")
    cfg.save()

    response = await client.get("/api/init/status")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["default_target"] == "prod"
    assert len(payload["targets"]) == 1
    target = payload["targets"][0]
    assert target["name"] == "prod"
    assert target["engine"] == "postgresql"
    assert target["is_default"] is True


async def test_init_complete_endpoint_marks_init_done_on_disk(client, tmp_rdst_home):
    """POST /api/init/complete flips the persisted init state."""
    response = await client.post("/api/init/complete")
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    # Reload from disk and verify the bit is set.
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.is_init_completed() is True


async def test_init_validate_rejects_invalid_payload(client, tmp_rdst_home):
    """POST /api/init/validate enforces request-shape validation."""
    response = await client.post(
        "/api/init/validate",
        json={"targets": "prod"},  # str, not list
    )
    assert response.status_code == 422
