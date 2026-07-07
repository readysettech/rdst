"""Integration tests for the query registry API — no service mocks.

The real `QueryRegistry` reads/writes `~/.rdst/queries.toml`, relocated
into a tmp dir per test by the `tmp_rdst_home` fixture in `conftest.py`.
"""

from __future__ import annotations

from pathlib import Path

from shared.query_registry import hash_sql


async def test_query_registry_post_strips_comments_and_persists_canonical_sql(client):
    """POST /api/query-registry should save comment-prefixed SQL successfully."""
    sql = "-- look up one user\nSELECT * FROM users WHERE id = 42"

    post_response = await client.post("/api/query-registry", json={"sql": sql})
    assert post_response.status_code == 200
    post_payload = post_response.json()
    assert post_payload["success"] is True
    assert post_payload["hash"] == hash_sql(sql)

    get_response = await client.get("/api/query-registry?limit=10")
    assert get_response.status_code == 200

    payload = get_response.json()
    queries = payload["queries"]
    assert len(queries) == 1
    assert payload["total"] == 1
    assert payload["limit"] == 10
    assert payload["offset"] == 0
    assert queries[0]["hash"] == post_payload["hash"]
    assert queries[0]["sql"] == "SELECT * FROM users WHERE id = :p1"


async def test_list_normalizes_legacy_list_target_entry(client, tmp_rdst_home: Path):
    """Entries written by legacy clients with last_target as a list must not
    blank the listing; the target is normalized to a string on load."""
    (tmp_rdst_home / "queries.toml").write_text(
        '[queries.aaaa1111bbbb]\n'
        'sql = "SELECT count(*) FROM users"\n'
        'hash = "aaaa1111bbbb"\n'
        'tag = "chat-how-many-users"\n'
        'first_analyzed = "2026-04-05T23:20:38Z"\n'
        'last_analyzed = "2026-07-07T14:08:24Z"\n'
        'frequency = 0\n'
        'source = "chat"\n'
        'last_target = []\n'
        '\n'
        '[queries.cccc2222dddd]\n'
        'sql = "SELECT * FROM orders WHERE id = :p1"\n'
        'hash = "cccc2222dddd"\n'
        'tag = ""\n'
        'first_analyzed = "2026-07-07T10:00:00Z"\n'
        'last_analyzed = "2026-07-07T10:00:00Z"\n'
        'frequency = 3\n'
        'source = "top-historical"\n'
        'last_target = "pgtest"\n'
    )

    listing = await client.get("/api/query-registry?limit=150")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["error"] is None
    assert payload["total"] == 2
    by_hash = {q["hash"]: q for q in payload["queries"]}
    assert by_hash["aaaa1111bbbb"]["target"] == ""
    assert by_hash["cccc2222dddd"]["target"] == "pgtest"


async def test_register_then_delete_then_list_excludes_query(client):
    sql = "SELECT * FROM accounts WHERE id = 7"

    post = await client.post("/api/query-registry", json={"sql": sql})
    assert post.status_code == 200
    query_hash = post.json()["hash"]

    delete = await client.delete(f"/api/query-registry/{query_hash}")
    assert delete.status_code == 200, delete.text
    assert delete.json()["success"] is True

    listing = await client.get("/api/query-registry")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 0
    assert payload["queries"] == []


async def test_duplicate_register_dedupes_by_hash(client):
    """Posting the same SQL twice must collapse into one entry — the
    registry is keyed on the normalized hash, not on insertion count."""
    sql = "SELECT name FROM customers WHERE id = 1"

    first = await client.post("/api/query-registry", json={"sql": sql})
    second = await client.post("/api/query-registry", json={"sql": sql})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["hash"] == second.json()["hash"]

    listing = await client.get("/api/query-registry")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 1
    assert len(payload["queries"]) == 1


async def test_list_pagination_offset_and_limit(client):
    """Register 5 distinct queries, then walk the list with `limit`/`offset`.

    Each query has a different table name so normalization (which
    parameterizes literals) doesn't collapse them onto one hash.
    """
    tables = ["products", "orders", "users", "shipments", "invoices"]
    for table in tables:
        sql = f"SELECT * FROM {table} WHERE id = 1"
        post = await client.post("/api/query-registry", json={"sql": sql})
        assert post.status_code == 200

    page1 = await client.get("/api/query-registry?limit=2&offset=0")
    page2 = await client.get("/api/query-registry?limit=2&offset=2")
    page3 = await client.get("/api/query-registry?limit=2&offset=4")

    assert page1.status_code == page2.status_code == page3.status_code == 200

    p1, p2, p3 = page1.json(), page2.json(), page3.json()
    assert p1["total"] == p2["total"] == p3["total"] == 5
    assert len(p1["queries"]) == 2
    assert len(p2["queries"]) == 2
    assert len(p3["queries"]) == 1

    # Pages must not overlap.
    seen = {q["hash"] for q in p1["queries"]} | {q["hash"] for q in p2["queries"]} | {
        q["hash"] for q in p3["queries"]
    }
    assert len(seen) == 5


async def test_update_sql_preserves_tag_and_changes_hash(client):
    post = await client.post("/api/query-registry", json={"sql": "SELECT id FROM users"})
    old_hash = post.json()["hash"]
    patch = await client.patch(
        f"/api/query-registry/{old_hash}/tag", json={"tag": "my_query"}
    )
    assert patch.json()["success"] is True

    # Structurally different SQL — literals alone normalize to the same hash.
    response = await client.patch(
        f"/api/query-registry/{old_hash}/sql",
        json={"sql": "SELECT id, email FROM users"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["hash_changed"] is True
    assert body["hash"] != old_hash

    listing = (await client.get("/api/query-registry")).json()
    assert listing["total"] == 1
    assert listing["queries"][0]["hash"] == body["hash"]
    assert listing["queries"][0]["tag"] == "my_query"


async def test_update_sql_unknown_hash(client):
    response = await client.patch(
        "/api/query-registry/nope/sql", json={"sql": "SELECT 1"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": False, "hash": None, "hash_changed": False, "error": "Query not found",
    }


async def test_import_sql_file(client, tmp_path):
    sql_file = tmp_path / "queries.sql"
    sql_file.write_text(
        "-- name: users_by_id\n"
        "SELECT * FROM users WHERE id = ?;\n"
        "\n"
        "-- name: recent_orders\n"
        "-- target: prod\n"
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT 10;\n"
    )

    response = await client.post(
        "/api/query-registry/import", json={"file": str(sql_file)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["imported"] == 2
    assert body["errors"] == []

    listing = (await client.get("/api/query-registry")).json()
    assert listing["total"] == 2
    tags = {q["tag"] for q in listing["queries"]}
    assert tags == {"users_by_id", "recent_orders"}


async def test_import_missing_file(client):
    response = await client.post(
        "/api/query-registry/import", json={"file": "/nonexistent/queries.sql"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
