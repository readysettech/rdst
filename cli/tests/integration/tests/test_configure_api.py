"""Integration tests for the Configure API — no service-layer mocks.

The real `ConfigureService` runs end-to-end, reading and writing the actual
TOML file under `~/.rdst/config.toml`. The `tmp_rdst_home` fixture (in
`conftest.py`) relocates HOME and `shared.constants.RDST_DATA_DIR` to a
fresh tmp dir per test, so each test starts with an empty config.

Connection-test cases live in `test_realdb_configure_api.py` — without a
live DB they just re-test mock plumbing.
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_target(name: str, data: dict, default: bool = False) -> None:
    """Persist a target straight to disk via TargetsConfig.

    Bypasses the API so we can pre-seed state for tests that exercise
    list/get/update/remove/set-default flows without re-testing add.
    """
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert(name, data)
    if default:
        cfg.set_default(name)
    cfg.save()


def _sample_target(**overrides) -> dict:
    base = {
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "myapp",
        "user": "admin",
        "password_env": "PROD_DB_PASSWORD",
        "tls": False,
        "read_only": False,
    }
    base.update(overrides)
    return base


# ============================================================================
# GET /api/configure/targets — list
# ============================================================================


async def test_list_targets_empty(client):
    response = await client.get("/api/configure/targets")
    assert response.status_code == 200
    data = response.json()
    assert data["targets"] == []
    assert data["default_target"] is None


async def test_list_targets_with_targets(client):
    _write_target("prod", _sample_target(), default=True)
    _write_target("staging", _sample_target(engine="mysql", port=3306))

    response = await client.get("/api/configure/targets")
    assert response.status_code == 200, response.text

    data = response.json()
    assert len(data["targets"]) == 2
    by_name = {t["name"]: t for t in data["targets"]}
    assert by_name["prod"]["is_default"] is True
    assert by_name["prod"]["engine"] == "postgresql"
    assert by_name["staging"]["is_default"] is False
    assert by_name["staging"]["engine"] == "mysql"
    assert data["default_target"] == "prod"


# ============================================================================
# GET /api/configure/targets/{name} — get
# ============================================================================


async def test_get_target_exists(client, monkeypatch):
    # Set the password env so `has_password` reflects a realistic happy path.
    monkeypatch.setenv("PROD_DB_PASSWORD", "secret")
    _write_target("prod", _sample_target(tls=True), default=True)

    response = await client.get("/api/configure/targets/prod")
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["target_name"] == "prod"
    assert data["engine"] == "postgresql"
    assert data["host"] == "db.example.com"
    assert data["port"] == 5432
    assert data["database"] == "myapp"
    assert data["user"] == "admin"
    assert data["password_env"] == "PROD_DB_PASSWORD"
    assert data["has_password"] is True
    assert data["is_default"] is True
    assert data["tls"] is True


async def test_get_target_not_found(client):
    response = await client.get("/api/configure/targets/nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "not found" in data["message"]


# ============================================================================
# POST /api/configure/targets — add
# ============================================================================


async def test_add_target_success(client):
    response = await client.post(
        "/api/configure/targets",
        json={
            "name": "new_db",
            "target": {
                "engine": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
                "password_env": "TEST_DB_PASSWORD",
                "tls": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["target_name"] == "new_db"

    # Round-trip: the target survives reload from disk.
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("new_db") is not None
    assert cfg.get("new_db")["host"] == "localhost"


async def test_add_target_duplicate(client):
    _write_target("existing", _sample_target())

    response = await client.post(
        "/api/configure/targets",
        json={
            "name": "existing",
            "target": {
                "engine": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "already exists" in data["message"]


async def test_add_target_validation_error(client):
    # Missing required `host` field — pydantic should 422 before ever
    # reaching the service.
    response = await client.post(
        "/api/configure/targets",
        json={
            "name": "bad_target",
            "target": {
                "engine": "postgresql",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
            },
        },
    )
    assert response.status_code == 422


async def test_add_target_with_mysql_engine(client):
    response = await client.post(
        "/api/configure/targets",
        json={
            "name": "mysql_db",
            "target": {
                "engine": "mysql",
                "host": "mysql.example.com",
                "port": 3306,
                "database": "myapp",
                "user": "root",
                "password_env": "MYSQL_PASSWORD",
                "tls": True,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    cfg = TargetsConfig()
    cfg.load()
    saved = cfg.get("mysql_db")
    assert saved["engine"] == "mysql"
    assert saved["port"] == 3306
    assert saved["tls"] is True


async def test_add_target_minimal_fields(client):
    """Only required fields supplied — defaults from TargetData should apply."""
    response = await client.post(
        "/api/configure/targets",
        json={
            "name": "minimal",
            "target": {
                "host": "localhost",
                "database": "testdb",
                "user": "testuser",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    cfg = TargetsConfig()
    cfg.load()
    saved = cfg.get("minimal")
    # Pydantic defaults fill in engine/port.
    assert saved["engine"] == "postgresql"
    assert saved["port"] == 5432


# ============================================================================
# PUT /api/configure/targets/{name} — update
# ============================================================================


async def test_update_target_success(client):
    _write_target("prod", _sample_target())

    response = await client.put(
        "/api/configure/targets/prod",
        json={
            "target": {
                "engine": "postgresql",
                "host": "new-host.example.com",
                "port": 5433,
                "database": "myapp",
                "user": "admin",
                "tls": True,
            },
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["target_name"] == "prod"

    cfg = TargetsConfig()
    cfg.load()
    saved = cfg.get("prod")
    assert saved["host"] == "new-host.example.com"
    assert saved["port"] == 5433
    assert saved["tls"] is True


async def test_update_target_not_found(client):
    response = await client.put(
        "/api/configure/targets/nonexistent",
        json={
            "target": {
                "engine": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "not found" in data["message"]


# ============================================================================
# DELETE /api/configure/targets/{name} — remove
# ============================================================================


async def test_remove_target_success(client):
    _write_target("old_db", _sample_target())

    response = await client.delete("/api/configure/targets/old_db")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["target_name"] == "old_db"

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("old_db") is None


async def test_remove_target_not_found(client):
    response = await client.delete("/api/configure/targets/nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "not found" in data["message"]


# ============================================================================
# PUT /api/configure/default — set default
# ============================================================================


async def test_set_default_success(client):
    _write_target("prod", _sample_target())
    _write_target("staging", _sample_target(host="staging.example.com"))

    response = await client.put("/api/configure/default", json={"name": "prod"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["target_name"] == "prod"

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get_default() == "prod"


async def test_set_default_not_found(client):
    response = await client.put(
        "/api/configure/default", json={"name": "nonexistent"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "not found" in data["message"]
