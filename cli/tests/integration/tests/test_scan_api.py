"""Integration tests for the scan API endpoint.

Drives the real `ScanService` against the existing
`tests/integration/fixtures/scan/` directory. We use `dry_run=true` so
the LLM-conversion phase is skipped — extraction is 100% deterministic
(AST for Python, regex for JS/TS), so this still exercises the full
discovery → extraction pipeline end-to-end without an Anthropic key
or a real DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.config.targets import TargetsConfig

FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "fixtures" / "scan"
)


def _seed_target_and_schema(rdst_data_dir: Path, monkeypatch) -> None:
    """Configure a target plus a stub semantic-layer file.

    The scan service requires both a target *and* a semantic-layer YAML
    keyed by target name. We don't care about its contents here — only
    that the file exists, so the config-validation phase passes.
    """
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "scantest",
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": "SCAN_PASSWORD",
        },
    )
    cfg.set_default("scantest")
    cfg.save()
    monkeypatch.setenv("SCAN_PASSWORD", "irrelevant")

    semantic_dir = rdst_data_dir / "semantic-layer"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "scantest.yaml").write_text(
        "tables: {}\n",
        encoding="utf-8",
    )

    # `features.scan.service` captured RDST_SEMANTIC_LAYER_DIR at import
    # time, so monkeypatching `shared.constants` isn't enough.
    monkeypatch.setattr(
        "features.scan.service.RDST_SEMANTIC_LAYER_DIR", semantic_dir
    )


async def test_scan_json_endpoint_returns_extracted_queries(
    client, tmp_rdst_home, monkeypatch
):
    """POST /api/scan/json against the Python ORM fixtures returns
    success=True with queries extracted from sqlalchemy_app/django_app.
    Uses dry_run=true so no LLM is invoked.
    """
    _seed_target_and_schema(tmp_rdst_home, monkeypatch)

    response = await client.post(
        "/api/scan/json",
        json={
            "target": "scantest",
            "directory": str(FIXTURES_DIR),
            "dry_run": True,
            "nosave": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True, body

    # The fixtures contain ORM patterns across at least Python files.
    assert isinstance(body["files"], list)
    assert isinstance(body["queries"], list)
    assert len(body["files"]) > 0, "no files matched ORM patterns"
    assert len(body["queries"]) > 0, "no queries extracted from fixtures"

    # In dry_run mode, queries are tagged "pending" (LLM step skipped).
    statuses = {q.get("status") for q in body["queries"]}
    assert "pending" in statuses, statuses


async def test_scan_endpoint_with_nonexistent_path_streams_error(
    client, tmp_rdst_home, monkeypatch
):
    """A directory that doesn't exist must surface as an `error` SSE
    event from the config-validation phase."""
    _seed_target_and_schema(tmp_rdst_home, monkeypatch)

    response = await client.post(
        "/api/scan/json",
        json={
            "target": "scantest",
            "directory": "/this/path/definitely/does/not/exist",
            "dry_run": True,
            "nosave": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert "not found" in body["error"].lower() or "not exist" in body["error"].lower()


async def test_scan_endpoint_locked_when_target_password_missing(
    client, tmp_rdst_home, monkeypatch
):
    """The `Depends(require_target_body)` guard must reject before the
    service runs when the target's `password_env` isn't set."""
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(
        "scantest",
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "appdb",
            "user": "appuser",
            "password_env": "SCAN_PASSWORD_NOT_SET",
        },
    )
    cfg.save()
    monkeypatch.delenv("SCAN_PASSWORD_NOT_SET", raising=False)

    response = await client.post(
        "/api/scan/json",
        json={
            "target": "scantest",
            "directory": str(FIXTURES_DIR),
            "dry_run": True,
        },
    )
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "TARGET_PASSWORD_REQUIRED"
