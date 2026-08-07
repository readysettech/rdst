"""Integration tests for the trial API endpoints.

Drives the real `TrialService` against an isolated `~/.rdst/`. The
`register` and `activate` paths make external HTTPS calls to the
key-service worker — they're better covered by a future test that
mocks `httpx`. What we cover here is everything that operates purely
on the on-disk trial config.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from features.trial.api import routes as trial_routes
from features.trial.models import TrialRegisterResult
from shared.config.targets import TargetsConfig


def _seed_active_trial() -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.set_trial_config(
        {
            "token": "trial-token-abc",
            "email": "user@example.com",
            "status": "active",
            "remaining_cents": 350,
            "limit_cents": 500,
        }
    )
    cfg.save()


async def test_status_initial_state_returns_inactive(client, tmp_rdst_home):
    """Fresh `~/.rdst/` with no trial config → `active=false`, all
    optional fields None."""
    response = await client.get("/api/trial/status")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["active"] is False
    assert body["email"] is None
    assert body["status"] is None
    assert body["remaining_cents"] is None


async def test_status_reads_seeded_trial_from_disk(client, tmp_rdst_home):
    """An on-disk trial block is reflected in `GET /api/trial/status`."""
    _seed_active_trial()

    response = await client.get("/api/trial/status")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["active"] is True
    assert body["email"] == "user@example.com"
    assert body["status"] == "active"
    assert body["remaining_cents"] == 350
    assert body["limit_cents"] == 500


async def test_activate_trial_wakes_parked_jobs(
    client, tmp_rdst_home, inmemory_keyring, monkeypatch
):
    class Registry:
        wake_calls = 0

        def wake_needs_key(self):
            self.wake_calls += 1

    registry = Registry()
    monkeypatch.setattr(trial_routes, "run_registry", registry)

    response = await client.post(
        "/api/trial/activate",
        json={"token": "trial-token-abc", "email": "user@example.com"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert registry.wake_calls == 1


async def test_simulate_exhaust_flips_status_and_zeros_remaining(client, tmp_rdst_home):
    """`/trial/simulate/exhaust` should set status=exhausted and zero
    remaining; the change must persist to disk."""
    _seed_active_trial()

    # No `Origin` header → same-host check is a no-op; loopback peer
    # check is satisfied by ASGITransport's default 127.0.0.1 client.
    sim = await client.post("/api/trial/simulate/exhaust")
    assert sim.status_code == 200, sim.text
    assert sim.json()["success"] is True

    # Re-read status from the API; both the API and the on-disk config
    # should reflect the simulated exhaustion.
    status = await client.get("/api/trial/status")
    assert status.status_code == 200
    body = status.json()
    assert body["active"] is False
    assert body["status"] == "exhausted"
    assert body["remaining_cents"] == 0
    assert body["limit_cents"] == 500

    cfg = TargetsConfig()
    cfg.load()
    persisted = cfg.get_trial_config()
    assert persisted["status"] == "exhausted"
    assert persisted["remaining_cents"] == 0


async def test_simulate_exhaust_with_no_active_trial_returns_no_op(
    client, tmp_rdst_home
):
    """No trial token on disk → simulate is a no-op with success=false."""
    response = await client.post("/api/trial/simulate/exhaust")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert "No active trial" in (body.get("message") or "")


async def test_register_accepts_remote_same_origin_browser(
    app, tmp_rdst_home, monkeypatch
):
    """A browser on the Vagrant host reaches TrialService through the VM IP."""
    calls = []

    async def register(_service, email: str, source: str):
        calls.append((email, source))
        return TrialRegisterResult(success=True, limit_display="150K tokens")

    monkeypatch.setattr(trial_routes.TrialService, "register", register)

    transport = ASGITransport(app=app, client=("192.168.56.1", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://192.168.56.10:8787"
    ) as remote:
        response = await remote.post(
            "/api/trial/register",
            headers={"origin": "http://192.168.56.10:8787"},
            json={"email": "person@hotmail.com"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert calls == [("person@hotmail.com", "web")]


async def test_register_rejects_remote_cross_origin_browser(
    app, tmp_rdst_home, monkeypatch
):
    calls = []

    async def register(_service, email: str, source: str):
        calls.append((email, source))
        return TrialRegisterResult(success=True)

    monkeypatch.setattr(trial_routes.TrialService, "register", register)

    transport = ASGITransport(app=app, client=("192.168.56.1", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://192.168.56.10:8787"
    ) as remote:
        response = await remote.post(
            "/api/trial/register",
            headers={"origin": "https://evil.example"},
            json={"email": "person@hotmail.com"},
        )

    assert response.status_code == 403
    assert calls == []
