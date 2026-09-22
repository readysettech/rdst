"""Durable Jev assessment lifecycle and target-boundary tests."""

from __future__ import annotations

import pytest

from features.schema import assessment_context
from shared.query_registry.query_registry import QueryRegistry


def _registry(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    registry.load()
    return registry


def test_literal_variants_create_one_assessment_and_survive_observation(tmp_path):
    registry = _registry(tmp_path)
    first, _ = registry.add_query(
        "SELECT * FROM orders WHERE status = 'new'",
        source="top-historical",
        target="shop",
        observed=True,
    )
    second, _ = registry.add_query(
        "SELECT * FROM orders WHERE status = 'paid'",
        source="top-historical",
        target="shop",
        observed=True,
    )
    assert second == first
    store = registry.library_store
    assert store.prepare_assessments("shop", "physical-a", now=1) == 1
    assert store.prepare_assessments("shop", "physical-a", now=2) == 0

    claim = store.claim_assessment(
        "shop", "physical-a", owner="worker-a", request_id="request-a", now=3
    )
    assert claim and claim["hash"] == first
    assert store.complete_assessment(
        claim["id"],
        claim["generation"],
        "worker-a",
        result={"findings": []},
        priority_score=75,
        band="High",
        confidence=0.8,
        model="jev-1.13.0",
        rubric_version="query-quick-assessment-v1",
        input_fingerprint="input",
        schema_fingerprint="schema",
        schema_collected_at="2026-09-21T00:00:00Z",
        schema_coverage="relevant tables",
        assessed_at="2026-09-21T00:00:01Z",
        now=4,
    )

    # Ordinary registry persistence rewrites lifecycle fields but preserves
    # the completed assessment and its indexed priority projection.
    registry.add_query(
        "SELECT * FROM orders WHERE status = 'shipped'",
        source="top-historical",
        target="shop",
        observed=True,
    )
    assessment = store.assessment_for(first, "shop", "physical-a")
    assert assessment["status"] == "complete"
    assert assessment["priority_score"] == 75
    page, _, _, _ = store.query_library(
        target="shop",
        search="",
        view="all",
        source="all",
        params="all",
        activity="all",
        impact="all",
        sort="jev-priority",
        cursor_position=None,
        limit=10,
        now_ms=0,
    )
    assert page[0]["_assessment"]["priority_score"] == 75


def test_same_shape_is_separate_per_physical_target_and_no_reassessment(tmp_path):
    registry = _registry(tmp_path)
    query_hash, _ = registry.add_query(
        "SELECT customer_id FROM orders WHERE status = 'new'",
        source="web",
        target="shop",
        save_intent=True,
    )
    store = registry.library_store
    store.prepare_assessments("shop", "physical-a", now=1)
    old = store.claim_assessment(
        "shop", "physical-a", owner="old", request_id="old-request", now=2
    )
    assert old
    store.complete_assessment(
        old["id"],
        old["generation"],
        "old",
        result={"findings": []},
        priority_score=25,
        band="Low",
        confidence=0.6,
        model="jev-1.13.0",
        rubric_version="query-quick-assessment-v1",
        input_fingerprint="a",
        schema_fingerprint="a",
        schema_collected_at="a",
        schema_coverage="relevant tables",
        assessed_at="a",
        now=3,
    )
    assert store.prepare_assessments("shop", "physical-a", now=4) == 0
    assert (
        store.claim_assessment(
            "shop", "physical-a", owner="again", request_id="again", now=4
        )
        is None
    )

    # Reusing the display name for another physical database creates new work
    # and clears the current sort projection without deleting provenance.
    assert store.prepare_assessments("shop", "physical-b", now=5) == 1
    assert store.assessment_for(query_hash, "shop", "physical-a")["band"] == "Low"
    assert store.assessment_for(query_hash, "shop", "physical-b")["status"] == "pending"


def test_claim_fencing_and_expiry_prevent_stale_completion(tmp_path):
    registry = _registry(tmp_path)
    registry.add_query("SELECT * FROM products", source="web", target="shop")
    store = registry.library_store
    store.prepare_assessments("shop", "physical-a", now=1)
    first = store.claim_assessment(
        "shop",
        "physical-a",
        owner="worker-a",
        request_id="stable",
        now=2,
        lease_seconds=10,
    )
    assert first
    assert store.recover_expired_assessment_claims(now=13) == 1
    second = store.claim_assessment(
        "shop",
        "physical-a",
        owner="worker-b",
        request_id="new",
        now=14,
    )
    assert second and second["request_id"] == "stable"
    assert not store.complete_assessment(
        first["id"],
        first["generation"],
        "worker-a",
        result={"findings": []},
        priority_score=100,
        band="High",
        confidence=1,
        model="jev-1.13.0",
        rubric_version="query-quick-assessment-v1",
        input_fingerprint="old",
        schema_fingerprint="old",
        schema_collected_at="old",
        schema_coverage="old",
        assessed_at="old",
        now=15,
    )


def _complete(store, claim, score, band):
    assert store.complete_assessment(
        claim["id"],
        claim["generation"],
        "worker",
        result={"findings": []},
        priority_score=score,
        band=band,
        confidence=0.7,
        model="jev-1.13.0",
        rubric_version="query-quick-assessment-v1",
        input_fingerprint="input",
        schema_fingerprint="schema",
        schema_collected_at="2026-09-21T00:00:00Z",
        schema_coverage="relevant tables",
        assessed_at="2026-09-21T00:00:01Z",
        now=5,
    )


def test_newest_shape_is_claimed_first_and_ranks_the_whole_registry(tmp_path):
    registry = _registry(tmp_path)
    older, _ = registry.add_query(
        "SELECT id FROM customers WHERE email = 'a@example.com'",
        source="web",
        target="shop",
    )
    newer, _ = registry.add_query(
        "SELECT * FROM orders ORDER BY created_at", source="web", target="shop"
    )
    store = registry.library_store
    assert store.prepare_assessments("shop", "physical-a", now=1) == 2

    first = store.claim_assessment(
        "shop", "physical-a", owner="worker", request_id="one", now=2
    )
    assert first and first["hash"] == newer
    _complete(store, first, 80, "High")
    second = store.claim_assessment(
        "shop", "physical-a", owner="worker", request_id="two", now=3
    )
    assert second and second["hash"] == older
    _complete(store, second, 20, "Low")

    # Ranking is a whole-registry SQLite order, not a reordering of one page.
    page, _, _, _ = store.query_library(
        target="shop",
        search="",
        view="all",
        source="all",
        params="all",
        activity="all",
        impact="all",
        sort="jev-priority",
        cursor_position=None,
        limit=1,
        now_ms=0,
    )
    assert [row["hash"] for row in page] == [newer]

    # An unassessed shape ranks after every scored one rather than beside Low.
    registry.add_query("SELECT * FROM products", source="web", target="shop")
    store.prepare_assessments("shop", "physical-a", now=6)
    page, _, _, _ = store.query_library(
        target="shop",
        search="",
        view="all",
        source="all",
        params="all",
        activity="all",
        impact="all",
        sort="jev-priority",
        cursor_position=None,
        limit=10,
        now_ms=0,
    )
    assert [row["hash"] for row in page][:2] == [newer, older]
    assert page[2]["_assessment"]["status"] == "pending"


@pytest.mark.parametrize(
    "schema_info",
    [
        'Schema information: Error collecting PostgreSQL schema - connection to server at "127.0.0.1", port 15432 failed',
        "Schema information: Missing connection details",
        "Schema information: No schema found for referenced tables",
        "Schema information: Failed to collect schema - boom",
    ],
)
def test_unusable_schema_defers_instead_of_assessing_without_evidence(
    tmp_path, monkeypatch, schema_info
):
    registry = _registry(tmp_path)
    registry.add_query("SELECT * FROM orders", source="web", target="shop")
    store = registry.library_store

    # The collector reports these outcomes as a successful call with a
    # one-line message, so an assessment must not be built from them.
    monkeypatch.setattr(
        assessment_context,
        "collect_target_schema",
        lambda *args, **kwargs: {
            "success": True,
            "schema_info": schema_info,
            "engine_version": "unknown",
        },
    )
    with pytest.raises(ConnectionError):
        assessment_context.collect_assessment_context(
            "SELECT * FROM orders",
            target="shop",
            target_config={"engine": "postgresql", "host": "127.0.0.1"},
            store=store,
        )
    identity = assessment_context.target_identity(
        {"engine": "postgresql", "host": "127.0.0.1"}
    )
    assert store.load_schema_snapshot("shop", identity, "any-table-set") is None


def test_collected_structure_is_accepted_and_reused(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    registry.add_query("SELECT * FROM orders", source="web", target="shop")
    store = registry.library_store
    calls: list[int] = []

    def collector(*args, **kwargs):
        calls.append(1)
        return {
            "success": True,
            "schema_info": "Schema information:\nTable: orders\n  id integer PRIMARY KEY",
            "engine_version": "PostgreSQL 14.8",
        }

    monkeypatch.setattr(assessment_context, "collect_target_schema", collector)
    config = {"engine": "postgresql", "host": "127.0.0.1"}
    first = assessment_context.collect_assessment_context(
        "SELECT * FROM orders", target="shop", target_config=config, store=store
    )
    second = assessment_context.collect_assessment_context(
        "SELECT * FROM orders", target="shop", target_config=config, store=store
    )
    assert first.fingerprint == second.fingerprint
    assert first.coverage == "relevant tables"
    # The persisted snapshot is shared, so a second pending shape over the
    # same tables costs no additional metadata collection.
    assert len(calls) == 1


def test_rollback_gate_keeps_the_worker_from_building_library_db(
    tmp_path, monkeypatch
):
    import asyncio

    from features.query_registry.assessment import AssessmentWorker
    from shared.query_registry.library_store import library_db_path_for

    monkeypatch.setenv("RDST_REGISTRY_SQLITE", "0")
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    registry.load()
    registry.add_query("SELECT * FROM orders", source="web", target="shop")
    assert registry.library_store is None

    async def run_one_cycle():
        worker = AssessmentWorker()
        await worker.start()
        worker.select_target("shop")
        await asyncio.sleep(0.2)
        await worker.stop()
        return worker

    worker = asyncio.run(run_one_cycle())
    assert not library_db_path_for(tmp_path / "queries.toml").exists()
    assert worker._task is None


def test_findings_filter_selects_whole_registry_by_concern(tmp_path):
    from features.query_registry.assessment_rubric import finding_mask

    registry = _registry(tmp_path)
    gap, _ = registry.add_query("SELECT * FROM orders WHERE status = 'new'", source="web", target="shop")
    join, _ = registry.add_query("SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id", source="web", target="shop")
    clean, _ = registry.add_query("SELECT id FROM products", source="web", target="shop")
    store = registry.library_store
    store.prepare_assessments("shop", "physical-a", now=1)

    masks = {
        join: finding_mask([{"id": "join_growth"}, {"id": "index_coverage"}]),
        gap: finding_mask([{"id": "index_coverage"}]),
        clean: finding_mask([]),
    }
    for _ in range(3):
        claim = store.claim_assessment("shop", "physical-a", owner="w", request_id="r", now=2)
        assert claim
        store.complete_assessment(
            claim["id"], claim["generation"], "w", result={"findings": []},
            priority_score=50, band="Medium", confidence=0.5,
            finding_mask=masks[claim["hash"]], model="jev-1.13.0",
            rubric_version="query-quick-assessment-v1", input_fingerprint="i",
            schema_fingerprint="s", schema_collected_at="t", schema_coverage="relevant tables",
            assessed_at="t", now=3,
        )

    # A shape nobody has assessed yet must not count as "no concern found".
    registry.add_query("SELECT 1 FROM pending_only", source="web", target="shop")

    def hashes(finding):
        page, facets, total, _ = store.query_library(
            target="shop", search="", view="all", source="all", params="all",
            activity="all", impact="all", finding=finding, sort="jev-priority",
            cursor_position=None, limit=10, now_ms=0,
        )
        return {row["hash"] for row in page}, facets["finding"], total

    index_rows, facets, total = hashes("index_coverage")
    assert index_rows == {gap, join}
    assert total == 2
    # Facet counts describe the whole registry, not the returned page.
    assert facets["index_coverage"] == 2
    assert facets["join_growth"] == 1
    assert facets["none"] == 1
    assert facets["all"] == facets["any"] + facets["none"] + 1
    assert facets["any"] == 2

    assert hashes("join_growth")[0] == {join}
    assert hashes("none")[0] == {clean}
    assert len(hashes("all")[0]) == 4  # gap, join, clean, and the unassessed shape


def test_sorting_by_one_concern_lifts_its_queries_above_the_rest(tmp_path):
    from features.query_registry.assessment_rubric import finding_mask

    registry = _registry(tmp_path)
    top, _ = registry.add_query("SELECT * FROM orders WHERE status = 'new'", source="web", target="shop")
    other, _ = registry.add_query("SELECT * FROM products", source="web", target="shop")
    store = registry.library_store
    store.prepare_assessments("shop", "physical-a", now=1)

    # The row carrying the concern scores LOWER, so only the concern sort can
    # lift it above the higher-priority row.
    plan = {
        top: (20, finding_mask([{"id": "join_growth"}])),
        other: (90, finding_mask([{"id": "index_coverage"}])),
    }
    for _ in range(2):
        claim = store.claim_assessment("shop", "physical-a", owner="w", request_id="r", now=2)
        score, mask = plan[claim["hash"]]
        store.complete_assessment(
            claim["id"], claim["generation"], "w", result={"findings": []},
            priority_score=score, band="Medium", confidence=0.5, finding_mask=mask,
            model="jev-1.13.0", rubric_version="query-quick-assessment-v1",
            input_fingerprint="i", schema_fingerprint="s", schema_collected_at="t",
            schema_coverage="relevant tables", assessed_at="t", now=3,
        )

    def order(sort):
        page, _, _, _ = store.query_library(
            target="shop", search="", view="all", source="all", params="all",
            activity="all", impact="all", sort=sort, cursor_position=None,
            limit=10, now_ms=0,
        )
        return [row["hash"] for row in page]

    assert order("jev-priority") == [other, top]
    assert order("jev-join_growth") == [top, other]
    assert order("jev-index_coverage") == [other, top]


def test_upgrade_backfills_finding_projection_for_existing_results(tmp_path):
    """An assessment completed before the projection column existed must still
    be sortable by its concerns after the upgrade."""
    from shared.query_registry import library_store
    from features.query_registry.assessment_rubric import finding_mask

    registry = _registry(tmp_path)
    query_hash, _ = registry.add_query(
        "SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id",
        source="web",
        target="shop",
    )
    store = registry.library_store
    store.prepare_assessments("shop", "physical-a", now=1)
    claim = store.claim_assessment("shop", "physical-a", owner="w", request_id="r", now=2)
    store.complete_assessment(
        claim["id"], claim["generation"], "w",
        result={"findings": [{"id": "join_growth"}, {"id": "broad_work"}]},
        priority_score=40, band="Medium", confidence=0.5,
        finding_mask=finding_mask([{"id": "join_growth"}, {"id": "broad_work"}]),
        model="jev-1.13.0", rubric_version="query-quick-assessment-v1",
        input_fingerprint="i", schema_fingerprint="s", schema_collected_at="t",
        schema_coverage="relevant tables", assessed_at="t", now=3,
    )

    # Rewind the projection to what a pre-upgrade file holds: the result is in
    # result_json, the column defaulted to 0.
    store.ensure_open()
    with store._write() as conn:
        conn.execute("UPDATE target_query SET rm_jev_findings = 0")
        page = conn.execute(
            "SELECT rm_jev_findings FROM target_query"
        ).fetchone()
        assert page[0] == 0
        for statement in library_store._MIGRATIONS[15](library_store._strict_mode()):
            if statement.strip().startswith("UPDATE"):
                conn.execute(statement)
        restored = conn.execute("SELECT rm_jev_findings FROM target_query").fetchone()

    assert restored[0] == finding_mask(
        [{"id": "join_growth"}, {"id": "broad_work"}]
    )
    page, _, _, _ = store.query_library(
        target="shop", search="", view="all", source="all", params="all",
        activity="all", impact="all", sort="jev-join_growth",
        cursor_position=None, limit=10, now_ms=0,
    )
    assert page[0]["hash"] == query_hash


def test_importing_worker_module_starts_no_background_task():
    from features.query_registry.assessment import query_assessment_worker

    assert query_assessment_worker._task is None


def test_worker_keeps_eight_lanes_in_flight_and_isolates_failures():
    import asyncio

    from features.query_registry import assessment
    from features.query_registry.assessment import AssessmentWorker

    async def scenario():
        worker = AssessmentWorker()
        in_flight = 0
        peak = 0
        started = 0
        release = asyncio.Event()

        async def fake_process_one(target):
            nonlocal in_flight, peak, started
            started += 1
            attempt = started
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await release.wait()
                if attempt == 3:
                    raise RuntimeError("one lane failed")
                return attempt <= 16
            finally:
                in_flight -= 1

        worker._process_one = fake_process_one
        worker._wait = lambda seconds: asyncio.sleep(0)
        worker.select_target("shop")
        worker._task = asyncio.create_task(worker._run())
        await asyncio.sleep(0.05)
        assert in_flight == assessment.CONCURRENCY
        release.set()
        await asyncio.sleep(0.05)
        await worker.stop()
        return peak, started

    peak, started = asyncio.run(scenario())
    assert peak == assessment.CONCURRENCY
    # Lanes refilled after the first batch returned, and the failing lane
    # did not stop the others from continuing.
    assert started > assessment.CONCURRENCY
