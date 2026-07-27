"""Tests for starting audits and captures as detached background runs."""

import pytest

from features.audit.api import routes
from shared.run_registry import RunRegistry


@pytest.fixture
def registry(run_registry) -> RunRegistry:
    return run_registry


@pytest.fixture
def client(run_api_client):
    return run_api_client


class StubAuditService:
    """Yields the event shape `audit_single` produces for a healthy target."""

    def audit_single(self, target_name, *, insights=True, save=True):
        from features.audit.events import (
            AuditCompleteEvent,
            AuditSnapshotSavedEvent,
            AuditStatusEvent,
        )

        async def generator():
            yield AuditStatusEvent(
                type="status", phase="collect", message=f"Auditing {target_name}..."
            )
            yield AuditSnapshotSavedEvent(type="snapshot_saved", snapshot_id="audit_imdb_1")
            yield AuditCompleteEvent(
                type="complete", success=True, snapshot_id="audit_imdb_1"
            )

        return generator()


def test_audit_starts_background_run_and_replays_events(
    client, monkeypatch, read_run_events
):
    monkeypatch.setattr(routes, "AuditService", StubAuditService)

    response = client.post("/api/audit", json={"target": "imdb", "insights": False})

    assert response.status_code == 200
    body = response.json()
    assert body["reused"] is False
    run_id = body["run_id"]
    assert run_id.startswith("audit_imdb_")

    events = [name for name, _ in read_run_events(client, run_id)]
    assert events == ["status", "snapshot_saved", "complete", "run_end"]


def test_capture_reuses_the_run_already_in_flight(
    client, monkeypatch, blocking_capture_service
):
    monkeypatch.setattr(routes, "CaptureService", blocking_capture_service)

    first = client.post("/api/audit/capture", json={"target": "imdb", "duration": 30})
    assert first.status_code == 200
    run_id = first.json()["run_id"]
    assert run_id.startswith("audit_capture_imdb_")

    second = client.post("/api/audit/capture", json={"target": "imdb", "duration": 30})
    assert second.json() == {"run_id": run_id, "reused": True}

    # Single-audit-at-a-time is global: a quick audit on another target
    # attaches to the capture rather than opening a second connection.
    quick = client.post("/api/audit", json={"target": "imdb", "insights": False})
    assert quick.json() == {"run_id": run_id, "reused": True}


def test_capture_run_is_cancellable(
    client, monkeypatch, registry, read_run_events, blocking_capture_service
):
    monkeypatch.setattr(routes, "CaptureService", blocking_capture_service)

    run_id = client.post(
        "/api/audit/capture", json={"target": "imdb", "duration": 30}
    ).json()["run_id"]

    cancelled = client.delete(f"/api/runs/{run_id}")
    assert cancelled.json() == {"run_id": run_id, "cancelled": True}

    # Draining the stream settles once the cancellation reaches the run.
    assert read_run_events(client, run_id)[-1][0] == "run_end"
    assert registry.status(run_id) == "cancelled"

    # With the run finished, the next request starts a fresh one.
    again = client.post("/api/audit/capture", json={"target": "imdb", "duration": 30})
    assert again.json()["reused"] is False
    assert again.json()["run_id"] != run_id
