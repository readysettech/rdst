"""Integration tests for the target/password guard.

The guard refuses to serve target-bound routes when the configured
`password_env` is unset (HTTP 423). When the env var is set, the same
routes pass the guard — the underlying handler may still 4xx for unrelated
reasons (e.g. invalid SQL, missing target connectivity), but the guard
itself must not block.

These tests use the real `TargetsConfig` against the disk under
`tmp_rdst_home` — no service-layer mocks.
"""

from __future__ import annotations

import pytest

from shared.api.target_guard import TARGET_PASSWORD_REQUIRED_CODE
from shared.config.targets import TargetsConfig


def _seed_prod_target() -> None:
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "app",
            "user": "app",
            "password_env": "PROD_DB_PASSWORD",
        },
    )
    cfg.set_default("prod")
    cfg.save()


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("POST", "/api/analyze", {"query": "select 1", "target": "prod"}),
        ("POST", "/api/ask", {"question": "count users", "target": "prod"}),
        ("GET", "/api/schema?target=prod", None),
        ("GET", "/api/top?target=prod", None),
        ("POST", "/api/readyset/setup", {"target": "prod"}),
        (
            "POST",
            "/api/query-registry/benchmark",
            {"queries": ["select 1"], "target": "prod"},
        ),
    ],
)
async def test_target_bound_endpoints_return_423_when_password_missing(
    client, method, url, payload, monkeypatch
):
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)
    _seed_prod_target()

    response = await client.request(method, url, json=payload)

    assert response.status_code == 423
    detail = response.json().get("detail", {})
    assert detail.get("code") == TARGET_PASSWORD_REQUIRED_CODE
    assert detail.get("target") == "prod"
    assert detail.get("password_env") == "PROD_DB_PASSWORD"


async def test_target_bound_endpoint_returns_unlocked_guard_when_password_present(
    client, monkeypatch
):
    """Happy path: with the env var set, the guard returns a TargetGuard
    instead of raising 423. The downstream route can still fail (e.g.
    EXPLAIN against a fake host) — what matters here is that the response
    is *not* the 423 lock and the guard's detail shape is gone.
    """
    monkeypatch.setenv("PROD_DB_PASSWORD", "secret")
    _seed_prod_target()

    response = await client.get("/api/schema?target=prod")

    assert response.status_code != 423, response.text
    detail = response.json() if response.headers.get(
        "content-type", ""
    ).startswith("application/json") else {}
    # Whatever the downstream returns, it must not be the lock guard payload.
    assert (
        not isinstance(detail, dict)
        or detail.get("detail", {}).get("code") != TARGET_PASSWORD_REQUIRED_CODE
    )
