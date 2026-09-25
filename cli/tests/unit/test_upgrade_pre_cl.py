"""Upgrade the actual merge-base CLI's data through app startup and first UI reads."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import time
from contextlib import closing
from pathlib import Path

import pytest
import toml

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/upgrade/pre_cl"
MANIFEST = json.loads((FIXTURE / "manifest.json").read_text())
STARTUP_TIMEOUT = 30


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return list(conn.iterdump())


def version(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


@pytest.fixture
def old_install(tmp_path, monkeypatch):
    data = tmp_path / "upgrade data" / ".rdst"
    data.mkdir(parents=True)
    assert digest(FIXTURE / "data.tar.gz") == MANIFEST["archive_sha256"]
    with tarfile.open(FIXTURE / "data.tar.gz") as archive:
        for member in archive.getmembers():
            assert member.isfile() and member.name in MANIFEST["files"]
            destination = data / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
    assert {name: digest(data / name) for name in MANIFEST["files"]} == MANIFEST["files"]
    assert version(data / "library.db") == 15
    assert version(data / "cache.db") == 2
    monkeypatch.setenv("RDST_DATA_DIR", str(data))
    monkeypatch.setenv("RDST_TESTING", "true")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    return data


def run_process(*args):
    env = {key: value for key, value in os.environ.items()
           if not any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY"))}
    env["PYTHONPATH"], env["COLUMNS"] = str(ROOT), "200"
    for target in MANIFEST["config"]["targets"].values():
        env[target["password_env"]] = "offline-fixture-password"
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, text=True,
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=STARTUP_TIMEOUT)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def start_current():
    started = time.monotonic()
    result = run_process(str(Path(__file__).resolve()), "--startup")
    assert time.monotonic() - started < STARTUP_TIMEOUT
    return json.loads(result.stdout)


def assert_preserved(data, api):
    from shared.query_registry.query_registry import QueryRegistry, hash_sql

    assert toml.load(data / "config.toml") == MANIFEST["config"]
    for name, expected_digest in MANIFEST["files"].items():
        if not name.endswith(".db"):
            assert digest(data / name) == expected_digest
    registry = QueryRegistry()
    registry.load()
    mapping = {entry["hash"]: hash_sql(entry["original_sql"] or entry["sql"])
               for entry in MANIFEST["queries"]}
    assert set(mapping.values()) == {entry.hash for entry in registry.list_queries()}
    assert any(old != new for old, new in mapping.items())
    assert len(set(mapping.values())) < len(mapping)
    for old in MANIFEST["queries"]:
        entry = registry.get_query(mapping[old["hash"]])
        assert entry is not None
        if old["tag"]:
            assert registry.get_query_by_tag(old["tag"]).hash == entry.hash
        assert set(old["target_lifecycle"]) <= set(entry.target_lifecycle)
        for target, lifecycle in old["target_lifecycle"].items():
            new = entry.target_lifecycle[target]
            assert set(lifecycle["sources"]) <= set(new.sources)
            for name in ("saved_at", "reviewed_at", "last_analyzed_at"):
                if lifecycle[name]:
                    assert getattr(new, name) == lifecycle[name]
            assert new.analysis_count >= lifecycle["analysis_count"]
    for old_hash, analyses in MANIFEST["analyses"].items():
        for analysis in analyses:
            assert registry.library_store.analysis_payload(
                mapping[old_hash], analysis["analysis_id"]) == analysis
            response = api["analyses"][analysis["analysis_id"]]
            assert response["hash"] == mapping[old_hash]
            assert response["analysis"] == analysis
    assert {entry["hash"] for entry in api["queries"]} == set(mapping.values())
    for target, text in MANIFEST["semantic_layers"].items():
        assert (data / "semantic-layer" / f"{target}.yaml").read_text() == text
        assert api["semantic"][target]["tables"] > 0
        assert api["semantic"][target]["terminology"] > 0
    with closing(sqlite3.connect(data / "cache.db")) as conn:
        conn.row_factory = sqlite3.Row
        aliases = [dict(row) for row in conn.execute(
            "SELECT * FROM identity_alias ORDER BY target_id,engine_key")]
        assert aliases == [{**row, "normalized_hash": mapping[row["normalized_hash"]]}
                           for row in MANIFEST["aliases"]]
        actual = {(row["target_id"], row["normalized_hash"], row["window_end"]): row
                  for row in conn.execute("SELECT * FROM recent_observation")}
    expected = {}
    for row in MANIFEST["observations"]:
        key = (row["target_id"], mapping[row["normalized_hash"]], row["window_end"])
        totals = expected.setdefault(key, {"calls_delta": 0, "exec_time_delta": 0.0})
        for metric in totals:
            totals[metric] += row[metric]
    assert actual.keys() == expected.keys()
    for key, totals in expected.items():
        for metric, total in totals.items():
            assert actual[key][metric] == pytest.approx(total)


def test_real_pre_cl_startup_preserves_data_and_second_start_is_noop(old_install):
    from shared.monitor.monitor_store import SCHEMA_VERSION as MONITOR_VERSION
    from shared.query_registry.library_store import SCHEMA_VERSION as LIBRARY_VERSION

    data = old_install
    library_before = dump(data / "library.db")
    api = start_current()
    assert version(data / "library.db") == LIBRARY_VERSION
    assert version(data / "monitor.db") == MONITOR_VERSION
    assert dump(data / "library.db.pre-v15.bak") == library_before
    assert digest(data / "queries.toml") == MANIFEST["files"]["queries.toml"]
    assert_preserved(data, api)
    precious = ("library.db", "cache.db", "monitor.db")
    snapshots = {name: dump(data / name) for name in precious}
    files = sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file())
    config_before = (data / "config.toml").read_bytes()
    assert_preserved(data, start_current())
    assert {name: dump(data / name) for name in precious} == snapshots
    assert sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()) == files
    assert (data / "config.toml").read_bytes() == config_before
    assert dump(data / "library.db.pre-v15.bak") == library_before
    listed = run_process("rdst.py", "query", "list").stdout
    for entry in MANIFEST["queries"]:
        if entry["tag"]:
            assert entry["tag"] in listed
    reviewed = run_process("rdst.py", "analyze", "--name", "pg-order-count", "--review").stdout
    assert "Conversation history" in reviewed
    assert "Show the captured execution plan" in reviewed
    assert "not found in registry" not in reviewed
    assert {name: dump(data / name) for name in precious} == snapshots


def test_pre_cl_session_reads_and_refreshes_as_windows_chunks(old_install, monkeypatch):
    from shared import account_session as session
    from shared.secret_store_service import SecretStoreService

    class WindowsKeyring:
        def __init__(self):
            self.entries = {}

        def get_password(self, service, name):
            return self.entries.get((service, name))

        def set_password(self, service, name, value):
            if len(value) > 1280:
                raise ValueError("Windows credential entry is too large")
            self.entries[(service, name)] = value

        def delete_password(self, service, name):
            self.entries.pop((service, name), None)

    store = SecretStoreService(service_name="pre-cl-upgrade-test")
    store._keyring = WindowsKeyring()
    monkeypatch.setattr(store, "is_available", lambda: True)
    values = {session.ACCESS_TOKEN_NAME: "old-access", session.REFRESH_TOKEN_NAME: "old-refresh",
              session.EXPIRES_AT_NAME: str(time.time() + 3600)}
    for name, value in values.items():
        monkeypatch.delenv(name, raising=False)
        store._keyring.set_password(store.service_name, name, value)
    assert session.is_signed_in_locally(store)
    assert session.access_token(store=store) == "old-access"
    token = "eyJ" + "a" * 3000

    def refresh(url, **kwargs):
        assert kwargs["json"] == {"refresh_token": "old-refresh"}
        return type("Response", (), {"status_code": 200, "json": lambda self: {
            "access_token": token, "refresh_token": "new-refresh", "expires_in": 3600}})()

    monkeypatch.setattr(session.requests, "post", refresh)
    assert session.access_token(force_refresh=True, store=store) == token
    for name in values:
        monkeypatch.delenv(name, raising=False)
    assert all(len(value) <= 1280 for value in store._keyring.entries.values())
    assert len(store._keyring.entries) > 3
    assert session.is_signed_in_locally(store)
    assert session.access_token(store=store) == token


async def startup_probe():
    """Run lifespan and first UI reads, isolating external maintenance services."""
    from unittest.mock import AsyncMock, patch

    import httpx
    from shared.api.app import create_app, lifespan
    from shared.query_registry.query_registry import hash_sql

    app = create_app()
    output = {"analyses": {}, "semantic": {}}
    with patch("shared.deploy.sandbox_manager.sandbox_manager.start", new=AsyncMock()), \
         patch("shared.deploy.sandbox_manager.sandbox_manager.stop", new=AsyncMock()), \
         patch("features.query_registry.assessment.query_assessment_worker.start", new=AsyncMock()), \
         patch("features.query_registry.assessment.query_assessment_worker.stop", new=AsyncMock()):
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://127.0.0.1") as client:
                response = await client.get("/api/query-registry")
                assert response.status_code == 200, response.text
                output["queries"] = response.json()["queries"]
                for target in MANIFEST["config"]["targets"]:
                    response = await client.get(f"/api/monitor/targets/{target}/health")
                    assert response.status_code == 200, response.text
                    response = await client.get("/api/semantic-layer/status", params={"target": target})
                    assert response.status_code == 200, response.text
                    output["semantic"][target] = response.json()
                for entry in MANIFEST["queries"]:
                    identity = hash_sql(entry["original_sql"] or entry["sql"])
                    for analysis in MANIFEST["analyses"][entry["hash"]]:
                        response = await client.get(
                            f"/api/query-registry/{identity}/analysis/{analysis['analysis_id']}")
                        assert response.status_code == 200, response.text
                        output["analyses"][analysis["analysis_id"]] = response.json()
    print(json.dumps(output))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(startup_probe())


def test_upgraded_library_analysis_and_benchmarks_follow_monitored_identity(old_install):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from features.monitor.api import routes as monitor_routes, views
    from features.query_registry.api import routes as registry_routes
    from shared.agent_activity.store import AgentActivityStore
    from shared.monitor.monitor_store import MonitorStore
    from shared.query_registry.query_registry import hash_sql

    start_current()
    views.forget_registry()
    now = int(time.time())
    store = MonitorStore()
    app = FastAPI()
    app.include_router(monitor_routes.router, prefix="/api/monitor")
    app.include_router(registry_routes.router, prefix="/api")
    with TestClient(app) as client:
        for entry in MANIFEST["queries"]:
            identity = hash_sql(entry["original_sql"] or entry["sql"])
            for analysis in MANIFEST["analyses"][entry["hash"]]:
                target = analysis["target"]
                store.record(target, now - 60, [{"query_key": identity, "calls": 1,
                                               "total_ms": 2}], {identity: entry["sql"]}, [])
                response = client.get(f"/api/monitor/targets/{target}/queries?range=1h")
                assert response.status_code == 200, response.text
                row = next(row for row in response.json()["queries"] if row["key"] == identity)
                ref = row["last_analysis"]["analysis_ref"]
                assert row["last_analysis"]["state"] == "done"
                assert ref.startswith(f"/api/query-registry/{identity}/analysis/")
                assert client.get(ref).json()["analysis"]["target"] == target
                digest = client.get(f"/api/monitor/targets/{target}/queries/{identity}/analysis")
                assert digest.status_code == 200, digest.text
                assert digest.json()["analysis_ref"] == ref
        entry = MANIFEST["queries"][0]
        identity = hash_sql(entry["original_sql"] or entry["sql"])
        target = next(iter(entry["target_lifecycle"]))
        # The fixture predates benchmark history. Persist a run on the upgraded identity
        # and verify a later launch reads it through the monitored query's history route.
        activity = AgentActivityStore()
        activity.append({"ts": now, "target": target, "action": "benchmark", "outcome": "done",
                         "details": {"lanes": ["origin"], "queries": [
                             {"hash": identity, "name": "Saved query", "p50_ms": 12.5}]}})
        start_current()
        response = client.get(f"/api/query-registry/queries/{identity}/benchmarks",
                              params={"target": target})
        assert response.status_code == 200, response.text
        assert response.json()["runs"][0]["query"]["p50_ms"] == 12.5
        other = client.get(f"/api/query-registry/queries/{identity}/benchmarks",
                           params={"target": "another-database"})
        assert other.json()["runs"] == []
