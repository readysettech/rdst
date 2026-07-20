"""Route tests for the schema-grounded examples + question-history endpoints.

Neither endpoint touches the network or the real ~/.rdst: the semantic-layer
manager and the query registry are stubbed / temp-backed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import features.ask.api.routes as ask_routes
import features.ask.example_questions as eq
from shared.query_registry.query_registry import QueryRegistry


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ask_routes.router, prefix="/api")
    return TestClient(app)


class _NoLayerManager:
    def exists(self, target: str) -> bool:
        return False

    def load(self, target: str):  # pragma: no cover - not reached
        raise AssertionError("load() must not run when exists() is False")


def test_examples_falls_back_to_introspection(client, monkeypatch, tmp_path):
    import features.schema.semantic_layer.manager as mgr

    monkeypatch.setattr(mgr, "SemanticLayerManager", _NoLayerManager)
    monkeypatch.setattr(eq, "rdst_data_dir", lambda: tmp_path)

    resp = client.get("/api/ask/examples", params={"target": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "introspection"
    assert len(body["examples"]) == 3
    # No hardcoded e-commerce prompt leaks through.
    joined = " ".join(body["examples"]).lower()
    assert "top 10 customers by revenue" not in joined


def test_history_returns_only_ask_entries_with_questions(client, monkeypatch, tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    registry.add_query(
        "SELECT votetypeid, count(*) FROM votes GROUP BY 1",
        source="ask",
        tag="votes_by_type",
        target="demo",
        question="Which vote type is most common?",
    )
    # A non-ask entry and an ask entry without a question must be excluded.
    registry.add_query("SELECT 1", source="top", target="demo")

    import shared.query_registry as sqr

    monkeypatch.setattr(sqr, "QueryRegistry", lambda *a, **k: registry)

    resp = client.get("/api/ask/history", params={"target": "demo"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["question"] == "Which vote type is most common?"
    assert item["target"] == "demo"
    # The original submitted SQL is returned (positional GROUP BY preserved).
    assert "GROUP BY 1" in item["sql"]


def test_history_filters_by_target(client, monkeypatch, tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    registry.add_query(
        "SELECT 1 FROM a", source="ask", tag="a", target="demo", question="qa"
    )
    registry.add_query(
        "SELECT 1 FROM b", source="ask", tag="b", target="other", question="qb"
    )

    import shared.query_registry as sqr

    monkeypatch.setattr(sqr, "QueryRegistry", lambda *a, **k: registry)

    resp = client.get("/api/ask/history", params={"target": "other"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["question"] for i in items] == ["qb"]
