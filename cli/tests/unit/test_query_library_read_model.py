"""Server-side Query Library read model: filters, facets, cursor, freshness."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from features.query_registry import read_model
from shared.query_registry.query_registry import (
    QueryEntry,
    QueryRegistry,
    QueryTargetLifecycle,
)


@pytest.fixture
def app():
    query_routes._reset_read_model_store_cache()
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    yield app
    query_routes._reset_read_model_store_cache()


@pytest.fixture
def registry(monkeypatch, tmp_path) -> QueryRegistry:
    instance = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    instance.load()

    import shared.query_registry as shared_registry

    monkeypatch.setattr(shared_registry, "QueryRegistry", lambda *a, **k: instance)
    return instance


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def _get(app: FastAPI, params: dict):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/query-registry", params=params)


def _library_fixture(registry: QueryRegistry) -> dict[str, str]:
    """Six 'demo' rows exercising every view/source/params/activity/impact
    bucket. Bucket membership per row is asserted in the facet test."""
    now = datetime.now(timezone.utc)
    hashes: dict[str, str] = {}

    hashes["users"], _ = registry.add_query(
        "SELECT * FROM users WHERE id = 7",
        source="top-historical",
        target="demo",
        observed=True,
    )
    users = registry.get_query(hashes["users"])
    users.avg_duration_ms = 60000.0
    users.observation_count = 2  # 2 min of DB time: impact >= 1m, < 10m

    hashes["orders"], _ = registry.add_query(
        "SELECT * FROM orders WHERE user_id = $1",
        source="ask",
        target="demo",
        question="top spenders",
        skip_param_extraction=True,
    )

    hashes["invoices"], _ = registry.add_query(
        "SELECT * FROM invoices WHERE id = $1",
        source="web",
        target="demo",
        analyzed=True,
        skip_param_extraction=True,
    )
    invoices = registry.get_query(hashes["invoices"])
    invoices.most_recent_params = {"p1": "42"}

    hashes["payments"], _ = registry.add_query(
        "SELECT count(*) FROM payments",
        source="file",
        target="demo",
        tag="Weekly revenue",
    )
    registry.get_query(hashes["payments"]).readyset_supported = "yes"

    hashes["products"], _ = registry.add_query(
        "SELECT * FROM products",
        source="scan",
        target="demo",
    )

    hashes["sessions"], _ = registry.add_query(
        "SELECT * FROM sessions WHERE expired = true",
        source="top",
        target="demo",
        observed=True,
    )
    stale = _iso(now - timedelta(days=10))
    sessions = registry.get_query(hashes["sessions"])
    sessions.last_analyzed = stale
    sessions.avg_duration_ms = 120000.0
    sessions.observation_count = 30  # 1 hour of DB time
    lifecycle = sessions.lifecycle_for("demo")
    lifecycle.first_observed_at = stale
    lifecycle.last_observed_at = stale

    registry.save()
    registry.update_readyset_identity(hashes["products"], "q_products", "yes", "demo")
    registry.mark_reviewed(hashes["sessions"], target="demo")
    return hashes


# -- legacy limit/offset contract -------------------------------------------


@pytest.mark.asyncio
async def test_offset_limit_contract_unchanged(app, registry):
    for index in range(3):
        registry.add_query(f"SELECT {index} FROM plain_t{index}", target="demo")

    response = await _get(app, {"target": "demo", "limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"queries", "total", "limit", "offset", "error"}
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["queries"]) == 2


@pytest.mark.asyncio
async def test_negative_limit_still_returns_all(app, registry):
    for index in range(3):
        registry.add_query(f"SELECT {index} FROM plain_t{index}", target="demo")

    response = await _get(app, {"target": "demo", "limit": -1})

    body = response.json()
    assert body["total"] == 3
    assert len(body["queries"]) == 3
    assert body["limit"] is None


# -- read-model response shape and facets ------------------------------------


@pytest.mark.asyncio
async def test_read_model_response_shape(app, registry):
    _library_fixture(registry)

    response = await _get(app, {"target": "demo", "view": "all"})

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "queries", "facet_counts", "next_cursor", "total", "freshness", "error",
    }
    assert set(body["facet_counts"].keys()) == {
        "view", "source", "params", "activity", "impact",
    }


@pytest.mark.asyncio
async def test_facets_computed_over_full_set_not_page(app, registry):
    _library_fixture(registry)

    response = await _get(app, {"target": "demo", "view": "all", "limit": 2})

    body = response.json()
    assert len(body["queries"]) == 2
    assert body["total"] == 6
    facets = body["facet_counts"]
    assert facets["view"] == {
        "all": 6,
        "new": 1,
        "saved": 6,
        "high-impact": 2,
        "needs-analysis": 5,
        "ready-to-cache": 1,
        "cached": 1,
    }
    assert facets["source"] == {
        "all": 6, "observed": 2, "ask": 1, "manual": 1, "file": 1, "scan": 1,
    }
    assert facets["params"] == {
        "all": 6, "without-parameters": 4, "values-ready": 1, "values-needed": 1,
    }
    assert facets["activity"] == {
        "all": 6, "1m": 5, "1h": 5, "8h": 5, "24h": 5, "7d": 5, "30d": 6,
    }
    assert facets["impact"] == {"all": 6, "1m": 2, "10m": 1, "1h": 1}


@pytest.mark.asyncio
async def test_facet_dimension_excludes_its_own_filter(app, registry):
    hashes = _library_fixture(registry)

    response = await _get(app, {"target": "demo", "source": "observed"})

    body = response.json()
    assert body["total"] == 2
    returned = {entry["hash"] for entry in body["queries"]}
    assert returned == {hashes["users"], hashes["sessions"]}
    facets = body["facet_counts"]
    # View counts apply the selected source filter; source counts do not
    # apply their own filter, mirroring the client selector.
    assert facets["view"]["all"] == 2
    assert facets["view"]["new"] == 1
    assert facets["source"] == {
        "all": 6, "observed": 2, "ask": 1, "manual": 1, "file": 1, "scan": 1,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"view": "new"},
        {"source": "observed"},
        {"params": "values-ready"},
        {"activity": "30d"},
        {"impact": "1m"},
        {"sort": "recently-observed"},
        {"sort": "newest"},
        {"sort": "most-frequent"},
        {"sort": "slowest-average"},
        {"sort": "recently-analyzed"},
    ],
)
def test_sql_read_model_matches_python_selector(registry, overrides):
    _library_fixture(registry)
    resolved = {
        "search": "",
        "view": "all",
        "source": "all",
        "params": "all",
        "activity": "all",
        "impact": "all",
        "sort": "highest-impact",
        **overrides,
    }
    now_ms = time.time() * 1000.0
    api_entries = [
        query_routes._to_registry_entry(query, "demo")
        for query in registry.list_queries()
        if query.belongs_to_target("demo")
    ]
    expected, expected_facets = read_model.select_library(
        api_entries, now_ms=now_ms, **resolved
    )

    stored, facets, total, next_position = registry.library_store.query_library(
        target="demo",
        cursor_position=None,
        limit=100,
        now_ms=now_ms,
        **resolved,
    )

    assert [entry["hash"] for entry in stored] == [entry.hash for entry in expected]
    assert facets == expected_facets
    assert total == len(expected)
    assert next_position is None


# -- search ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_matches_name_sql_question_and_hash(app, registry):
    hashes = _library_fixture(registry)

    by_tag = await _get(app, {"target": "demo", "search": "weekly revenue"})
    assert [e["hash"] for e in by_tag.json()["queries"]] == [hashes["payments"]]

    by_sql = await _get(app, {"target": "demo", "search": "invoices"})
    assert [e["hash"] for e in by_sql.json()["queries"]] == [hashes["invoices"]]

    by_question = await _get(app, {"target": "demo", "search": "top spenders"})
    assert [e["hash"] for e in by_question.json()["queries"]] == [hashes["orders"]]

    full_hash = await _get(app, {"target": "demo", "search": hashes["users"]})
    assert [e["hash"] for e in full_hash.json()["queries"]] == [hashes["users"]]

    prefix = await _get(app, {"target": "demo", "search": hashes["users"][:6]})
    assert [e["hash"] for e in prefix.json()["queries"]] == [hashes["users"]]


# -- sorting -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sort_deterministic_with_hash_tiebreaker(app, registry):
    hashes = _library_fixture(registry)

    response = await _get(app, {"target": "demo", "sort": "highest-impact"})

    order = [entry["hash"] for entry in response.json()["queries"]]
    zero_impact = sorted(
        [hashes["orders"], hashes["invoices"], hashes["payments"], hashes["products"]]
    )
    assert order == [hashes["sessions"], hashes["users"], *zero_impact]


# -- keyset cursor ------------------------------------------------------------


def _walk_fixture(registry: QueryRegistry) -> dict[str, int]:
    frequencies = {}
    for index, frequency in enumerate((60, 50, 40, 30, 20, 10)):
        query_hash, _ = registry.add_query(
            f"SELECT {index} FROM walk_t{index}",
            target="walk",
            frequency=frequency,
        )
        frequencies[query_hash] = frequency
    return frequencies


@pytest.mark.asyncio
async def test_cursor_walk_returns_stable_rows_exactly_once(app, registry):
    frequencies = _walk_fixture(registry)

    params = {"target": "walk", "sort": "most-frequent", "limit": 2}
    seen: list[str] = []

    first = await _get(app, params)
    body = first.json()
    seen.extend(entry["hash"] for entry in body["queries"])
    assert body["next_cursor"]

    # Rows move between requests: an already-returned row's sort key jumps
    # ahead of the cursor. Keyset pagination must still return every stable
    # row exactly once (OFFSET would duplicate or skip here).
    moved = registry.get_query(seen[0])
    moved.frequency = 100
    registry.save()

    cursor = body["next_cursor"]
    while cursor:
        page = await _get(app, {**params, "cursor": cursor})
        body = page.json()
        seen.extend(entry["hash"] for entry in body["queries"])
        cursor = body["next_cursor"]

    assert sorted(seen) == sorted(frequencies)
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_cursor_spec_mismatch_rejected_with_400(app, registry):
    _walk_fixture(registry)

    first = await _get(app, {"target": "walk", "sort": "most-frequent", "limit": 2})
    cursor = first.json()["next_cursor"]

    stale = await _get(
        app,
        {"target": "walk", "sort": "highest-impact", "limit": 2, "cursor": cursor},
    )
    assert stale.status_code == 400
    assert stale.json()["detail"]["code"] == "cursor_invalid"

    garbage = await _get(
        app, {"target": "walk", "sort": "most-frequent", "cursor": "not-a-cursor"}
    )
    assert garbage.status_code == 400
    assert garbage.json()["detail"]["code"] == "cursor_invalid"


@pytest.mark.asyncio
async def test_target_page_never_loads_full_registry(app, registry, monkeypatch):
    _library_fixture(registry)

    def fail_load_all():
        raise AssertionError("target read model must not load the whole registry")

    monkeypatch.setattr(registry.library_store, "load_all", fail_load_all)
    response = await _get(
        app, {"target": "demo", "sort": "highest-impact", "limit": 2}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 6
    assert len(response.json()["queries"]) == 2


@pytest.mark.asyncio
async def test_parameter_update_materializes_new_target_ownership(app, registry):
    query_hash, _ = registry.add_query(
        "SELECT * FROM users WHERE id = 7",
        source="manual",
    )
    assert registry.update_parameter_history(
        query_hash, {"p1": 7}, target="demo"
    )
    assert registry.get_query(query_hash).belongs_to_target("demo")

    response = await _get(app, {"target": "demo", "view": "all"})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [entry["hash"] for entry in response.json()["queries"]] == [query_hash]


@pytest.mark.asyncio
async def test_facets_and_page_share_one_wal_snapshot(
    app, registry, monkeypatch
):
    query_hash, _ = registry.add_query(
        "SELECT * FROM snapshot_users",
        source="manual",
        target="snapshot",
    )
    writer = QueryRegistry(registry_path=str(registry.registry_path))
    writer.load()
    original_connect = registry.library_store._connect
    wrote_between_statements = False

    class FacetCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def fetchone(self):
            nonlocal wrote_between_statements
            row = self._cursor.fetchone()
            if not wrote_between_statements:
                wrote_between_statements = True
                assert writer.remove_query(query_hash)
            return row

    class ReadConnection:
        def __init__(self):
            self._connection = original_connect()

        def execute(self, sql, bindings=()):
            cursor = self._connection.execute(sql, bindings)
            if sql.lstrip().startswith("WITH candidate"):
                return FacetCursor(cursor)
            return cursor

        def __getattr__(self, name):
            return getattr(self._connection, name)

    monkeypatch.setattr(registry.library_store, "_connect", ReadConnection)

    response = await _get(app, {"target": "snapshot", "view": "all"})

    body = response.json()
    assert wrote_between_statements
    assert body["total"] == 1
    assert [entry["hash"] for entry in body["queries"]] == [query_hash]


@pytest.mark.asyncio
async def test_large_registry_page_and_facets_stay_store_backed(
    app, registry, monkeypatch
):
    target = "large"
    entries = {}
    for index in range(5000):
        query_hash = f"{index:012x}"
        entry = QueryEntry(
            sql=f"SELECT * FROM large_table_{index}",
            hash=query_hash,
            frequency=index,
            source="top-historical",
            last_target=target,
            target_lifecycle={
                target: QueryTargetLifecycle(
                    first_observed_at="2026-08-01T00:00:00Z",
                    last_observed_at="2026-08-01T00:00:00Z",
                    sources=["top-historical"],
                )
            },
        )
        entries[query_hash] = entry.to_dict()
    registry.library_store.apply_changes({}, entries)

    def fail_load_all():
        raise AssertionError("large target page must stay inside SQLite")

    monkeypatch.setattr(registry.library_store, "load_all", fail_load_all)
    response = await _get(
        app, {"target": target, "sort": "most-frequent", "limit": 50}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["error"] is None
    assert body["total"] == 5000
    assert body["facet_counts"]["source"]["observed"] == 5000
    assert len(body["queries"]) == 50
    assert [row["frequency"] for row in body["queries"]] == list(
        range(4999, 4949, -1)
    )


@pytest.mark.asyncio
async def test_concurrent_pages_reuse_one_store_without_sharing_registry_state(
    registry, monkeypatch
):
    _library_fixture(registry)
    import shared.query_registry as shared_registry

    factory_calls = 0

    def counting_factory(*args, **kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return registry

    monkeypatch.setattr(shared_registry, "QueryRegistry", counting_factory)

    def read_page(sort):
        return query_routes._library_read_model(
            target="demo",
            search="",
            view="all",
            source="all",
            params="all",
            activity="all",
            impact="all",
            sort=sort,
            cursor=None,
            limit=2,
        )

    pages = await asyncio.gather(
        *(
            asyncio.to_thread(
                read_page,
                "most-frequent" if index % 2 else "highest-impact",
            )
            for index in range(24)
        )
    )

    assert factory_calls == 1
    assert all(page.error is None and page.total == 6 for page in pages)
    assert all(len(page.queries) == 2 for page in pages)


def test_read_model_store_cache_invalidates_when_data_home_changes(
    monkeypatch, tmp_path
):
    import shared.query_registry as shared_registry

    constructions = 0

    def factory():
        nonlocal constructions
        constructions += 1
        return QueryRegistry()

    monkeypatch.setattr(shared_registry, "QueryRegistry", factory)
    query_routes._reset_read_model_store_cache()
    first = query_routes._cached_read_model_store()

    replacement_home = tmp_path / "replacement-home"
    monkeypatch.setenv("HOME", str(replacement_home))
    monkeypatch.setenv("USERPROFILE", str(replacement_home))
    second = query_routes._cached_read_model_store()

    assert constructions == 2
    assert first is not second
    assert first.path != second.path


def test_production_filtered_page_and_facets_use_indexed_plans(
    registry, monkeypatch
):
    frequencies = _walk_fixture(registry)
    cursor_hash = next(
        query_hash for query_hash, frequency in frequencies.items() if frequency == 50
    )
    store = registry.library_store
    statements = []
    original_connect = store._connect

    def traced_connect():
        conn = original_connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(store, "_connect", traced_connect)
    page, _, total, _ = store.query_library(
        target="walk",
        search="walk_t",
        view="all",
        source="all",
        params="all",
        activity="all",
        impact="all",
        sort="most-frequent",
        cursor_position=(50.0, cursor_hash),
        limit=2,
        now_ms=time.time() * 1000.0,
    )

    assert total == 6
    assert len(page) == 2
    facet_sql = next(
        statement
        for statement in statements
        if statement.lstrip().startswith("WITH candidate AS")
    )
    page_sql = next(
        statement
        for statement in statements
        if statement.lstrip().startswith("WITH lower_metric AS")
    )

    conn = original_connect()
    try:
        facet_plan = [
            str(row[3])
            for row in conn.execute("EXPLAIN QUERY PLAN " + facet_sql).fetchall()
        ]
        page_plan = [
            str(row[3])
            for row in conn.execute("EXPLAIN QUERY PLAN " + page_sql).fetchall()
        ]
    finally:
        conn.close()

    assert any(
        "SEARCH target_query USING INDEX tq_rm_" in detail
        for detail in facet_plan
    )
    page_seeks = [
        detail
        for detail in page_plan
        if "SEARCH tq USING INDEX tq_rm_frequency" in detail
    ]
    assert len(page_seeks) == 2
    # The final merge scans only the two page-probe CTEs (each LIMIT 3 here);
    # the production table itself must be reached by the two range seeks.
    assert not any("SCAN target_query" in detail for detail in page_plan)


@pytest.mark.parametrize(
    "sort",
    [
        "highest-impact",
        "recently-observed",
        "newest",
        "most-frequent",
        "slowest-average",
        "recently-analyzed",
    ],
)
def test_keyset_page_plan_uses_sort_index(registry, sort):
    _walk_fixture(registry)

    details = registry.library_store.explain_query_library_page(
        target="walk", sort=sort
    )

    assert any(
        "SEARCH tq USING" in detail and "tq_rm_" in detail for detail in details
    )
    range_searches = [detail for detail in details if "SEARCH tq USING" in detail]
    assert len(range_searches) == 2
    assert any("<?" in detail for detail in range_searches)
    assert any("rm_hash>?" in detail for detail in range_searches)
    assert not any("SCAN tq" in detail for detail in details)


# -- freshness ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_freshness_null_without_observation_store(app, registry):
    _library_fixture(registry)

    response = await _get(app, {"target": "demo", "view": "all"})

    assert response.status_code == 200
    assert response.json()["freshness"] is None


@pytest.mark.asyncio
async def test_freshness_from_collector_state(app, registry):
    _library_fixture(registry)
    from shared.query_registry.observation_store import ObservationStore

    with ObservationStore() as store:
        store.upsert_collector_state(
            "demo",
            state="idle",
            last_attempt_at=1755000000,
            last_success_at=1755000000,
            epoch_id="epoch-1",
        )

    response = await _get(app, {"target": "demo", "view": "all"})

    freshness = response.json()["freshness"]
    assert freshness["state"] == "idle"
    assert freshness["epoch_id"] == "epoch-1"
    assert freshness["last_success_at"] == "2025-08-12T12:00:00Z"


@pytest.mark.asyncio
async def test_repeated_freshness_pages_reuse_validated_observation_store(
    app, registry, monkeypatch
):
    _library_fixture(registry)
    from features.query_registry.discovery import query_discovery
    from shared.query_registry import observation_store

    with observation_store.ObservationStore() as seed_store:
        seed_store.upsert_collector_state(
            "demo",
            state="idle",
            last_success_at=1755000000,
        )
    await query_discovery.close()

    constructions = 0
    filesystem_probes = 0
    real_store = observation_store.ObservationStore

    def store_factory():
        nonlocal constructions
        constructions += 1
        return real_store()

    def local_filesystem(*args, **kwargs):
        nonlocal filesystem_probes
        filesystem_probes += 1
        return type("StatResult", (), {"stdout": "apfs\n"})()

    monkeypatch.setattr(query_discovery, "_store_factory", store_factory)
    monkeypatch.setattr(observation_store.sys, "platform", "darwin")
    monkeypatch.setattr(observation_store.subprocess, "run", local_filesystem)

    responses = [
        await _get(app, {"target": "demo", "view": "all"})
        for _ in range(5)
    ]

    assert constructions == 1
    assert filesystem_probes == 1
    assert all(
        response.json()["freshness"]["state"] == "idle"
        for response in responses
    )


@pytest.mark.asyncio
async def test_freshness_store_failure_never_breaks_request(app, registry):
    _library_fixture(registry)
    from shared.query_registry.observation_store import default_cache_db_path

    default_cache_db_path().write_bytes(b"not a sqlite database")

    response = await _get(app, {"target": "demo", "view": "all"})

    assert response.status_code == 200
    body = response.json()
    assert body["freshness"] is None
    assert body["total"] == 6


# -- read-model helpers -------------------------------------------------------


def test_detect_parameters_mirrors_client():
    assert read_model.detect_parameters("SELECT 1 FROM t") == []
    assert read_model.detect_parameters("SELECT * FROM t WHERE a = $1") == [
        ("$1", 1, "positional")
    ]
    assert read_model.detect_parameters("SELECT * FROM t WHERE a = ? AND b = ?") == [
        ("?", 1, "positional"),
        ("?", 2, "positional"),
    ]
    assert read_model.detect_parameters("SELECT * FROM t WHERE a = :name") == [
        (":name", 1, "named")
    ]
    # A ::type cast is not a parameter.
    assert read_model.detect_parameters("SELECT a::text FROM t") == []


def test_derive_query_name_mirrors_client():
    assert read_model.derive_query_name("SELECT * FROM public.users") == (
        "Select \u00b7 users"
    )
    assert read_model.derive_query_name("SELECT count(*) FROM orders") == (
        "COUNT on orders"
    )
    assert read_model.derive_query_name("") == "Untitled query"


def test_cursor_round_trip_and_validation():
    spec = read_model.spec_hash(
        target="demo",
        search="",
        view="all",
        source="all",
        params="all",
        activity="all",
        impact="all",
        sort="highest-impact",
    )
    cursor = read_model.encode_cursor(120000.0, "abc123def456", spec)
    assert read_model.decode_cursor(cursor, spec) == (120000.0, "abc123def456")

    with pytest.raises(read_model.CursorError):
        read_model.decode_cursor(cursor, "other-spec-hash")
    with pytest.raises(read_model.CursorError):
        read_model.decode_cursor("@@not-base64@@", spec)

    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(read_model.CursorError):
            read_model.encode_cursor(non_finite, "abc123def456", spec)
        raw = json.dumps(
            {"v": non_finite, "h": "abc123def456", "s": spec},
            separators=(",", ":"),
        ).encode("utf-8")
        cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        with pytest.raises(read_model.CursorError):
            read_model.decode_cursor(cursor, spec)
