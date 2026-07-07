"""In-process integration tests for the guard API endpoints.

Drives the real routes against `tmp_rdst_home`. Intent derivation is mocked
at the LLM boundary; everything else (YAML persistence, checker) runs for
real.
"""

from __future__ import annotations

from unittest.mock import patch

from features.guard.config import GuardConfig, GuardsConfig, MaskingConfig


def _guard_body(name: str = "pii-safe", **overrides) -> dict:
    body = {
        "name": name,
        "description": "Protect PII",
        "masking": {"*.email": "email"},
        "restrictions": {"denied_columns": ["*password*"]},
        "guards": {"require_where": True, "no_select_star": True},
        "limits": {"max_rows": 500, "timeout_seconds": 20},
    }
    body.update(overrides)
    return body


async def test_guard_crud_roundtrip(client, tmp_rdst_home):
    # Empty list initially.
    response = await client.get("/api/guards")
    assert response.status_code == 200
    assert response.json() == {"guards": [], "count": 0}

    # Create.
    response = await client.post("/api/guards", json=_guard_body())
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert (tmp_rdst_home / "guards" / "pii-safe.yaml").exists()

    # Duplicate create conflicts.
    response = await client.post("/api/guards", json=_guard_body())
    assert response.status_code == 409

    # List shows summary with rule tags.
    response = await client.get("/api/guards")
    body = response.json()
    assert body["count"] == 1
    summary = body["guards"][0]
    assert summary["name"] == "pii-safe"
    assert summary["mask_count"] == 1
    assert "where" in summary["rules"]
    assert "no_select*" in summary["rules"]
    assert summary["max_rows"] == 500

    # Detail round-trips the config.
    response = await client.get("/api/guards/pii-safe")
    detail = response.json()
    assert detail["masking"] == {"*.email": "email"}
    assert detail["restrictions"]["denied_columns"] == ["*password*"]
    assert detail["guards"]["require_where"] is True
    assert detail["limits"]["max_rows"] == 500

    # Update.
    updated = _guard_body()
    updated["limits"]["max_rows"] = 100
    response = await client.put("/api/guards/pii-safe", json=updated)
    assert response.status_code == 200
    response = await client.get("/api/guards/pii-safe")
    assert response.json()["limits"]["max_rows"] == 100

    # Name is immutable through update.
    renamed = _guard_body(name="other-name")
    response = await client.put("/api/guards/pii-safe", json=renamed)
    assert response.status_code == 400

    # Delete.
    response = await client.delete("/api/guards/pii-safe")
    assert response.status_code == 200
    response = await client.get("/api/guards/pii-safe")
    assert response.status_code == 404


async def test_guard_create_rejects_invalid_name(client, tmp_rdst_home):
    response = await client.post("/api/guards", json=_guard_body(name="../evil"))
    assert response.status_code == 422


async def test_guard_update_404_for_unknown(client, tmp_rdst_home):
    response = await client.put("/api/guards/nope", json=_guard_body(name="nope"))
    assert response.status_code == 404


async def test_guard_delete_404_for_unknown(client, tmp_rdst_home):
    response = await client.delete("/api/guards/nope")
    assert response.status_code == 404


async def test_guard_check_blocks_and_passes(client, tmp_rdst_home):
    await client.post("/api/guards", json=_guard_body(name="strict"))

    # Missing WHERE blocks.
    response = await client.post(
        "/api/guards/strict/check",
        json={"sql": "SELECT id FROM users"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    failed = [r for r in body["results"] if not r["passed"] and r["level"] == "block"]
    assert any(r["guard_name"] == "require_where" for r in failed)

    # Compliant query passes; SELECT * would only warn.
    response = await client.post(
        "/api/guards/strict/check",
        json={"sql": "SELECT id, name FROM users WHERE id = 5"},
    )
    body = response.json()
    assert body["passed"] is True

    response = await client.post(
        "/api/guards/strict/check",
        json={"sql": "SELECT * FROM users WHERE id = 5"},
    )
    body = response.json()
    assert body["passed"] is True
    warns = [r for r in body["results"] if r["level"] == "warn" and not r["passed"]]
    assert any(r["guard_name"] == "no_select_star" for r in warns)


async def test_guard_check_blocks_writes(client, tmp_rdst_home):
    await client.post("/api/guards", json=_guard_body(name="ro"))

    response = await client.post(
        "/api/guards/ro/check",
        json={"sql": "DELETE FROM users WHERE id = 5"},
    )
    body = response.json()
    assert body["passed"] is False
    assert any(r["guard_name"] == "read_only" for r in body["results"] if not r["passed"])


async def test_guard_check_404_for_unknown_guard(client, tmp_rdst_home):
    response = await client.post(
        "/api/guards/nope/check", json={"sql": "SELECT 1"}
    )
    assert response.status_code == 404


async def test_guard_check_unknown_target_404(client, tmp_rdst_home):
    await client.post("/api/guards", json=_guard_body(name="t"))
    response = await client.post(
        "/api/guards/t/check",
        json={"sql": "SELECT 1", "target": "does-not-exist"},
    )
    assert response.status_code == 404


async def test_guard_derive_returns_preview_without_saving(client, tmp_rdst_home):
    derived = GuardConfig(
        name="support-guard",
        description="Support agents look up customers",
        intent="Support can look up customers by ID",
        derived=True,
        masking=MaskingConfig(patterns={"*.email": "email"}),
        guards=GuardsConfig(require_where=True),
    )

    with patch(
        "features.guard.api.routes.derive_rules_from_intent",
        return_value=derived,
    ) as mock_derive:
        response = await client.post(
            "/api/guards/derive",
            json={"name": "support-guard", "intent": "Support can look up customers by ID"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["derived"] is True
    assert body["masking"] == {"*.email": "email"}
    assert body["guards"]["require_where"] is True
    mock_derive.assert_called_once()

    # Preview only: nothing saved.
    assert not (tmp_rdst_home / "guards" / "support-guard.yaml").exists()

    # Saving the previewed config persists intent + derived flags.
    response = await client.post("/api/guards", json=body)
    assert response.status_code == 200
    response = await client.get("/api/guards/support-guard")
    detail = response.json()
    assert detail["derived"] is True
    assert detail["intent"] == "Support can look up customers by ID"


async def test_guard_derive_llm_failure_maps_to_422(client, tmp_rdst_home):
    with patch(
        "features.guard.api.routes.derive_rules_from_intent",
        side_effect=ValueError("Could not parse LLM response as JSON"),
    ):
        response = await client.post(
            "/api/guards/derive", json={"name": "x", "intent": "whatever"}
        )
    assert response.status_code == 422
