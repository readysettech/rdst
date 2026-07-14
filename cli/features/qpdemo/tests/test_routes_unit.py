"""Unit tests for the /api/demo routes that don't need a live docker stack."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.qpdemo.api import routes


class FakeService:
    def __init__(self) -> None:
        self.start_calls: list[int | None] = []

    def start_load(self, workers: int | None = None) -> None:
        self.start_calls.append(workers)


def _client(monkeypatch) -> tuple[TestClient, FakeService]:
    fake = FakeService()
    monkeypatch.setattr(routes.DemoService, "instance", classmethod(lambda cls: fake))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    return TestClient(app), fake


def test_load_start_accepts_empty_body(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.post("/api/demo/load/start")
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "running": True}
    assert fake.start_calls == [None]


def test_load_start_accepts_worker_count(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.post("/api/demo/load/start", json={"workers": 8})
    assert r.status_code == 200, r.text
    assert fake.start_calls == [8]
