"""Real-database integration tests for the schema introspection API.

`/api/schema` runs `information_schema` queries against the live
target. Catches PG vs MySQL information_schema dialect breakage that
mocks would silently let through.

To run locally:

    cd rdst/tests/integration
    docker compose up -d postgres   # or: mysql
    export RDST_TEST_PASSWORD=testpassword
    export RDST_TEST_ENGINE=postgresql
    pytest tests/test_realdb_schema_api.py -v -m realdb
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.realdb


TARGET_NAME = "itschema"


@pytest.fixture(autouse=True)
def _set_test_password(monkeypatch):
    if not os.environ.get("RDST_TEST_PASSWORD"):
        monkeypatch.setenv("RDST_TEST_PASSWORD", "testpassword")


async def _add_target(client, payload: dict) -> None:
    response = await client.post(
        "/api/configure/targets",
        json={"name": TARGET_NAME, "target": payload},
    )
    assert response.status_code == 200, response.text


async def test_schema_returns_seeded_tables_and_columns(
    client, db_target_payload, db_engine
):
    """`GET /api/schema` against a live container returns
    `title_basics` / `title_ratings` and their columns."""
    await _add_target(client, db_target_payload)

    response = await client.get(f"/api/schema?target={TARGET_NAME}")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body.get("error") is None, body
    assert body["dialect"] == ("mysql" if db_engine == "mysql" else "postgresql")

    # Some collectors return tables as plain names, others as
    # schema-qualified names. Tolerate both: match by suffix.
    tables = body.get("tables") or {}
    assert tables, f"Schema response had no tables: {body}"

    def _find(table_name: str) -> str | None:
        for key in tables:
            if key == table_name or key.endswith("." + table_name):
                return key
        return None

    title_basics_key = _find("title_basics")
    title_ratings_key = _find("title_ratings")
    assert title_basics_key, f"title_basics missing; got tables: {list(tables)}"
    assert title_ratings_key, f"title_ratings missing; got tables: {list(tables)}"

    basics_cols = {c.lower() for c in tables[title_basics_key]}
    assert {"tconst", "titletype", "primarytitle"}.issubset(basics_cols), (
        f"title_basics missing expected columns; got {basics_cols!r}"
    )

    ratings_cols = {c.lower() for c in tables[title_ratings_key]}
    assert {"tconst", "averagerating", "numvotes"}.issubset(ratings_cols), (
        f"title_ratings missing expected columns; got {ratings_cols!r}"
    )
