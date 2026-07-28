"""Tests for POST /api/fleet/audit (one background run) and /api/providers/aws-status."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.audit.api import routes as audit_routes
from features.audit.events import AuditCompleteEvent
from features.fleet.api import routes as fleet_routes
from features.fleet.api.routes import MAX_FLEET_AUDIT_TARGETS
from shared.config.targets import TargetsConfig
from shared.run_registry import RunRegistry


@pytest.fixture
def run_api_target() -> str:
    return "db1"


@pytest.fixture
def registry(run_registry) -> RunRegistry:
    return run_registry


@pytest.fixture
def client(run_api_client):
    return run_api_client


@pytest.fixture
def fleet_targets(monkeypatch) -> list[str]:
    """Configured fleet target names, mutable by each test."""
    names: list[str] = []

    monkeypatch.setattr(TargetsConfig, "load", lambda self: None)
    monkeypatch.setattr(
        TargetsConfig,
        "list_fleet_targets",
        lambda self, group=None, tag=None: list(names),
    )
    return names


@pytest.fixture
def audit_calls(monkeypatch) -> list[dict]:
    """Record what the route asks AuditService.audit_fleet to do."""
    calls: list[dict] = []

    async def fake_audit_fleet(self, targets, **kwargs):
        calls.append({"targets": targets, **kwargs})
        yield AuditCompleteEvent(type="complete", success=True)

    monkeypatch.setattr(
        "features.audit.service.AuditService.audit_fleet", fake_audit_fleet
    )
    return calls


@pytest.fixture
def blocking_audit_fleet(monkeypatch) -> None:
    """A fleet audit that never finishes, so its run stays active."""

    async def never_finishes(self, targets, **kwargs):
        import asyncio

        yield AuditCompleteEvent(type="complete", success=True)
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "features.audit.service.AuditService.audit_fleet", never_finishes
    )


@pytest.fixture
def run_audit(client, read_run_events):
    """Start a fleet audit and return its drained event stream as text."""

    def _run(body: dict) -> str:
        response = client.post("/api/fleet/audit", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["reused"] is False
        run_id = response.json()["run_id"]
        return "\n".join(
            f"{name} {data}" for name, data in read_run_events(client, run_id)
        )

    return _run


class TestFleetAuditRun:
    def test_start_returns_a_fleet_run_id(self, client, fleet_targets, audit_calls):
        fleet_targets.append("db1")

        response = client.post("/api/fleet/audit", json={"save": False})

        assert response.status_code == 200
        body = response.json()
        assert body["reused"] is False
        assert body["run_id"].startswith("fleet_audit_fleet_")

    def test_run_id_carries_the_audited_group(self, client, fleet_targets, audit_calls):
        fleet_targets.append("db1")

        response = client.post("/api/fleet/audit", json={"group": "prod"})

        assert response.json()["run_id"].startswith("fleet_audit_prod_")

    def test_events_replay_from_the_background_run_stream(
        self, client, fleet_targets, audit_calls, run_audit
    ):
        fleet_targets.append("db1")

        text = run_audit({"save": False})

        assert "complete" in text
        assert text.splitlines()[-1].startswith("run_end")
        assert '"status": "done"' in text.splitlines()[-1]

    def test_run_is_cancellable(
        self, client, fleet_targets, blocking_audit_fleet, registry, read_run_events
    ):
        fleet_targets.append("db1")
        run_id = client.post("/api/fleet/audit", json={"save": False}).json()["run_id"]

        cancelled = client.delete(f"/api/runs/{run_id}")

        assert cancelled.json() == {"run_id": run_id, "cancelled": True}
        assert read_run_events(client, run_id)[-1][0] == "run_end"
        assert registry.status(run_id) == "cancelled"


class TestSingleAuditAtATime:
    def test_a_running_fleet_audit_is_handed_back(
        self, client, fleet_targets, blocking_audit_fleet
    ):
        fleet_targets.append("db1")
        run_id = client.post("/api/fleet/audit", json={"save": False}).json()["run_id"]

        again = client.post("/api/fleet/audit", json={"save": False})

        assert again.json() == {"run_id": run_id, "reused": True}

    def test_a_running_fleet_audit_blocks_a_single_audit(
        self, client, fleet_targets, blocking_audit_fleet
    ):
        fleet_targets.append("db1")
        run_id = client.post("/api/fleet/audit", json={"save": False}).json()["run_id"]

        quick = client.post("/api/audit", json={"target": "db1", "insights": False})

        assert quick.json() == {"run_id": run_id, "reused": True}

    def test_a_running_capture_blocks_a_fleet_audit(
        self, client, fleet_targets, monkeypatch, blocking_capture_service
    ):
        monkeypatch.setattr(audit_routes, "CaptureService", blocking_capture_service)
        fleet_targets.append("db1")
        run_id = client.post(
            "/api/audit/capture", json={"target": "db1", "duration": 30}
        ).json()["run_id"]

        fleet = client.post("/api/fleet/audit", json={"save": False})

        assert fleet.json() == {"run_id": run_id, "reused": True}


class TestFleetAuditCap:
    def test_over_cap_is_refused(self, client, fleet_targets, audit_calls, run_audit):
        fleet_targets.extend(f"db{i}" for i in range(MAX_FLEET_AUDIT_TARGETS + 1))

        text = run_audit({})

        assert "too_many_targets" in text
        assert "filter by group or tag" in text
        assert audit_calls == []

    def test_at_cap_runs(self, client, fleet_targets, audit_calls, run_audit):
        fleet_targets.extend(f"db{i}" for i in range(MAX_FLEET_AUDIT_TARGETS))

        text = run_audit({})

        assert "too_many_targets" not in text
        assert len(audit_calls) == 1

    def test_cap_applies_to_group_filtered_set(
        self, client, fleet_targets, audit_calls, run_audit
    ):
        fleet_targets.extend(f"db{i}" for i in range(MAX_FLEET_AUDIT_TARGETS + 4))

        text = run_audit({"group": "prod"})

        assert "too_many_targets" in text
        assert audit_calls == []

    def test_cap_applies_to_explicit_targets(
        self, client, fleet_targets, audit_calls, run_audit
    ):
        names = [f"db{i}" for i in range(MAX_FLEET_AUDIT_TARGETS + 1)]
        fleet_targets.extend(names)

        text = run_audit({"targets": names})

        assert "too_many_targets" in text
        assert audit_calls == []


class TestFleetAuditExplicitTargets:
    @pytest.fixture(autouse=True)
    def _three_targets(self, fleet_targets):
        fleet_targets.extend(["db1", "db2", "db3"])

    def test_explicit_list_audits_those_targets(self, client, audit_calls, run_audit):
        text = run_audit({"targets": ["db2", "db1"]})

        assert "too_many_targets" not in text
        assert audit_calls[0]["targets"] == ["db2", "db1"]

    def test_explicit_list_dedupes(self, client, audit_calls, run_audit):
        run_audit({"targets": ["db1", "db1", "db2"]})

        assert audit_calls[0]["targets"] == ["db1", "db2"]

    def test_targets_with_group_is_rejected(self, client, audit_calls):
        response = client.post(
            "/api/fleet/audit", json={"targets": ["db1"], "group": "prod"}
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_request"
        assert "'targets' cannot be combined" in detail["message"]
        assert audit_calls == []

    def test_targets_with_tag_is_rejected(self, client, audit_calls):
        response = client.post(
            "/api/fleet/audit", json={"targets": ["db1"], "tag": "primary"}
        )

        assert response.status_code == 400
        assert audit_calls == []

    def test_unknown_targets_are_rejected(self, client, audit_calls):
        response = client.post(
            "/api/fleet/audit", json={"targets": ["db1", "nope1", "nope2"]}
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "unknown_targets"
        assert "nope1" in detail["message"] and "nope2" in detail["message"]
        assert detail["targets"] == ["nope1", "nope2"]
        assert audit_calls == []


class TestFleetAuditDuration:
    @pytest.fixture(autouse=True)
    def _two_targets(self, fleet_targets):
        fleet_targets.extend(["db1", "db2"])

    def test_absent_duration_is_metrics_only(self, client, audit_calls, run_audit):
        run_audit({})
        assert audit_calls[0]["duration_seconds"] is None

    def test_duration_passes_through(self, client, audit_calls, run_audit):
        run_audit({"duration": 120})
        assert audit_calls[0]["duration_seconds"] == 120

    def test_duration_clamped_low(self, client, audit_calls, run_audit):
        run_audit({"duration": 3})
        assert audit_calls[0]["duration_seconds"] == 10

    def test_duration_clamped_high(self, client, audit_calls, run_audit):
        run_audit({"duration": 99999})
        assert audit_calls[0]["duration_seconds"] == 3600


class TestAwsStatusRoute:
    def test_returns_status_payload(self, client):
        payload = {
            "has_credentials": True,
            "method": "profile",
            "identity_arn": "arn:aws:iam::123456789012:user/mike",
            "account": "123456789012",
            "active_profile": None,
            "available_profiles": ["default", "dev"],
            "region": "us-east-1",
        }
        with patch("features.providers.auth.get_aws_status", return_value=payload):
            response = client.get("/api/providers/aws-status")

        assert response.status_code == 200
        assert response.json() == payload
