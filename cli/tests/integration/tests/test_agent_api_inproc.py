"""In-process integration tests for the agent API endpoints.

Drives the real routes against `tmp_rdst_home`. The ask pipeline is mocked
at the `AgentRuntime.ask` boundary — it needs a live database and LLM;
everything above it (config CRUD, validation, response mapping) runs for
real.
"""

from __future__ import annotations

from unittest.mock import patch

from features.agent.runtime import AgentResponse
from shared.config.targets import TargetsConfig


def _seed_target(name: str = "agenttest", env: str = "AGENT_PASSWORD") -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        name,
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": env,
        },
    )
    cfg.save()


def _agent_body(name: str = "sales-agent", **overrides) -> dict:
    body = {
        "name": name,
        "target": "agenttest",
        "description": "Sales data agent",
        "max_rows": 200,
        "timeout_seconds": 15,
        "denied_columns": ["*password*"],
    }
    body.update(overrides)
    return body


async def test_agent_crud_roundtrip(client, tmp_rdst_home):
    _seed_target()

    response = await client.get("/api/agents")
    assert response.status_code == 200
    assert response.json() == {"agents": [], "count": 0}

    response = await client.post("/api/agents", json=_agent_body())
    assert response.status_code == 200
    assert (tmp_rdst_home / "agents" / "sales-agent.yaml").exists()

    # Duplicate conflicts.
    response = await client.post("/api/agents", json=_agent_body())
    assert response.status_code == 409

    response = await client.get("/api/agents")
    body = response.json()
    assert body["count"] == 1
    assert body["agents"][0]["name"] == "sales-agent"
    assert body["agents"][0]["target"] == "agenttest"
    assert body["agents"][0]["max_rows"] == 200

    response = await client.get("/api/agents/sales-agent")
    detail = response.json()
    assert detail["safety"]["max_rows"] == 200
    assert detail["safety"]["timeout_seconds"] == 15
    assert detail["restrictions"]["denied_columns"] == ["*password*"]
    assert detail["guard"] is None

    response = await client.delete("/api/agents/sales-agent")
    assert response.status_code == 200
    response = await client.get("/api/agents/sales-agent")
    assert response.status_code == 404


async def test_agent_create_validates_target_and_guard(client, tmp_rdst_home):
    _seed_target()

    response = await client.post(
        "/api/agents", json=_agent_body(target="no-such-target")
    )
    assert response.status_code == 422
    assert "no-such-target" in response.json()["detail"]

    response = await client.post(
        "/api/agents", json=_agent_body(guard="no-such-guard")
    )
    assert response.status_code == 422
    assert "no-such-guard" in response.json()["detail"]

    response = await client.post("/api/agents", json=_agent_body(name="../evil"))
    assert response.status_code == 422


async def test_agent_create_with_guard_reference(client, tmp_rdst_home):
    _seed_target()
    response = await client.post(
        "/api/guards",
        json={"name": "pii-safe", "guards": {"require_where": True}},
    )
    assert response.status_code == 200

    response = await client.post(
        "/api/agents", json=_agent_body(name="guarded", guard="pii-safe")
    )
    assert response.status_code == 200

    response = await client.get("/api/agents/guarded")
    assert response.json()["guard"] == "pii-safe"


async def test_agent_delete_404_for_unknown(client, tmp_rdst_home):
    response = await client.delete("/api/agents/nope")
    assert response.status_code == 404


async def test_agent_ask_returns_result(client, tmp_rdst_home, monkeypatch):
    _seed_target()
    monkeypatch.setenv("AGENT_PASSWORD", "irrelevant")
    await client.post("/api/agents", json=_agent_body())

    fake = AgentResponse(
        success=True,
        sql="SELECT name FROM customers WHERE id = 1 LIMIT 200",
        columns=["name"],
        rows=[["Acme"]],
        row_count=1,
        execution_time_ms=12.5,
    )
    with patch(
        "features.agent.runtime.AgentRuntime.ask", return_value=fake
    ) as mock_ask:
        response = await client.post(
            "/api/agents/sales-agent/ask",
            json={"question": "What is customer 1 called?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["rows"] == [["Acme"]]
    assert body["row_count"] == 1
    mock_ask.assert_called_once_with("What is customer 1 called?")


async def test_agent_ask_locked_when_password_missing(client, tmp_rdst_home, monkeypatch):
    _seed_target(env="MISSING_AGENT_PASSWORD")
    monkeypatch.delenv("MISSING_AGENT_PASSWORD", raising=False)
    await client.post("/api/agents", json=_agent_body())

    response = await client.post(
        "/api/agents/sales-agent/ask", json={"question": "anything"}
    )
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"


async def test_agent_ask_404_for_unknown_agent(client, tmp_rdst_home):
    response = await client.post(
        "/api/agents/nope/ask", json={"question": "anything"}
    )
    assert response.status_code == 404


async def test_agent_schema_summary(client, tmp_rdst_home, monkeypatch):
    _seed_target()
    monkeypatch.setenv("AGENT_PASSWORD", "irrelevant")
    await client.post("/api/agents", json=_agent_body())

    with patch(
        "features.agent.runtime.AgentRuntime.get_schema_summary",
        return_value={"tables": [{"name": "customers", "columns": ["id", "name"]}], "source": "database"},
    ):
        response = await client.get("/api/agents/sales-agent/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "database"
    assert body["tables"][0]["name"] == "customers"
