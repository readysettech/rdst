"""Stored analyses: the v13 migration, the bounded history, and its routes.

Covers the one-time analysis_results.toml import (backup, cap, idempotence,
the safety net a failed step falls back on), append-and-prune on store, and
the two read routes the results viewer reopens an analysis through.
"""

from __future__ import annotations

import sqlite3

import pytest
import toml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import features.query_registry.api.routes as query_routes
from shared.query_registry import library_store
from shared.query_registry.analysis_results import (
    AnalysisResultsRegistry,
    create_analysis_result,
)
from shared.query_registry.library_store import (
    ANALYSIS_HISTORY_LIMIT,
    SCHEMA_VERSION,
    library_db_path_for,
)


def _user_version(db_path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _analysis_rows(db_path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT query_hash, analysis_id, created_at, target, payload"
            " FROM query_analysis ORDER BY query_hash, analysis_id"
        ).fetchall()
    finally:
        conn.close()


def _legacy_analysis(analysis_id: str, minute: int, score: float) -> dict:
    return {
        "query_hash": "aaaaaaaaaaaa",
        "analysis_id": analysis_id,
        "target": "demo",
        "timestamp": f"2026-08-18T19:{minute:02d}:00Z",
        "performance_metrics": {"execution_time_ms": 12.5},
        "llm_analysis": {
            "performance_assessment": {
                "overall_rating": "fair",
                "efficiency_score": score,
            }
        },
        "explain_plan": {},
        "query_metrics": {},
        "rewrite_suggestions": [],
        "index_suggestions": [],
        "caching_recommendations": {},
        "database_engine": "postgresql",
        "analysis_duration_ms": 0.0,
        "llm_model_used": "claude-sonnet-4-5",
        "tokens_used": 7479,
    }


def _write_legacy_analyses(tmp_path, count: int = 3):
    path = tmp_path / "analysis_results.toml"
    results = {
        "aaaaaaaaaaaa": {
            f"20260818_1955{index:02d}_000": _legacy_analysis(
                f"20260818_1955{index:02d}_000", index, 60.0 + index
            )
            for index in range(count)
        }
    }
    with open(path, "w", encoding="utf-8") as handle:
        toml.dump({"results": results}, handle)
    return path


def _seed_v12(tmp_path):
    """Build a store as a v12 build left it: no analyses table, no version 13."""
    from shared.query_registry.query_registry import hash_sql

    sql = "SELECT title FROM posts WHERE score > $1"
    query_hash = hash_sql(sql)
    toml_path = tmp_path / "queries.toml"
    db_path = library_db_path_for(toml_path)
    library_store.LibraryStore(db_path, toml_path).apply_changes(
        {},
        {
            query_hash: {
                "hash": query_hash,
                "sql": sql,
                "original_sql": sql,
                "source": "manual",
                "target_lifecycle": {"demo": {"saved_at": "2026-08-01T10:00:00Z"}},
            }
        },
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE query_analysis")
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
    finally:
        conn.close()
    return db_path, toml_path, query_hash


class TestLegacyImport:
    """analysis_results.toml is read once and left where it was."""

    def test_upgrade_imports_the_stored_analyses(self, tmp_path):
        db_path, toml_path, query_hash = _seed_v12(tmp_path)
        analysis_path = _write_legacy_analyses(tmp_path)

        library_store.LibraryStore(db_path, toml_path).load_all()

        assert _user_version(db_path) == SCHEMA_VERSION
        rows = _analysis_rows(db_path)
        assert [(row[0], row[1], row[3]) for row in rows] == [
            ("aaaaaaaaaaaa", "20260818_195500_000", "demo"),
            ("aaaaaaaaaaaa", "20260818_195501_000", "demo"),
            ("aaaaaaaaaaaa", "20260818_195502_000", "demo"),
        ]
        # The queries survive their own migration step untouched.
        assert query_hash in library_store.LibraryStore(db_path, toml_path).load_all()
        # The file stays readable for the previous release, beside its backup.
        assert analysis_path.exists()
        assert (
            tmp_path / f"analysis_results.toml.pre-sqlite-{SCHEMA_VERSION}.bak"
        ).exists()

    def test_imported_bodies_round_trip(self, tmp_path):
        db_path, toml_path, _ = _seed_v12(tmp_path)
        _write_legacy_analyses(tmp_path, count=1)

        library_store.LibraryStore(db_path, toml_path).load_all()

        registry = AnalysisResultsRegistry(
            registry_path=str(tmp_path / "analysis_results.toml")
        )
        stored = registry.get_stored_payload("aaaaaaaaaaaa", "20260818_195500_000")
        # An imported body reads the shape a body written today reads, so
        # the viewer needs no special case for a pre-v13 analysis.
        assert stored == {
            **_legacy_analysis("20260818_195500_000", 0, 60.0),
            "rewrite_test_results": None,
            "display_payload": {},
        }

    def test_import_keeps_only_the_newest_ten(self, tmp_path):
        db_path, toml_path, _ = _seed_v12(tmp_path)
        _write_legacy_analyses(tmp_path, count=ANALYSIS_HISTORY_LIMIT + 2)

        library_store.LibraryStore(db_path, toml_path).load_all()

        rows = _analysis_rows(db_path)
        assert len(rows) == ANALYSIS_HISTORY_LIMIT
        kept = {row[1] for row in rows}
        assert "20260818_195500_000" not in kept
        assert "20260818_195501_000" not in kept
        assert "20260818_195511_000" in kept

    def test_a_second_pass_changes_nothing(self, tmp_path):
        db_path, toml_path, _ = _seed_v12(tmp_path)
        _write_legacy_analyses(tmp_path)

        library_store.LibraryStore(db_path, toml_path).load_all()
        first = _analysis_rows(db_path)
        backup = tmp_path / f"analysis_results.toml.pre-sqlite-{SCHEMA_VERSION}.bak"
        backup_bytes = backup.read_bytes()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 12")
            conn.commit()
        finally:
            conn.close()
        library_store.LibraryStore(db_path, toml_path).load_all()

        assert _analysis_rows(db_path) == first
        assert backup.read_bytes() == backup_bytes

    def test_a_data_dir_without_the_file_imports_nothing(self, tmp_path):
        db_path, toml_path, _ = _seed_v12(tmp_path)

        library_store.LibraryStore(db_path, toml_path).load_all()

        assert _analysis_rows(db_path) == []
        assert list(tmp_path.glob("analysis_results.toml*")) == []

    def test_an_unreadable_file_does_not_hold_the_library_back(self, tmp_path):
        db_path, toml_path, query_hash = _seed_v12(tmp_path)
        (tmp_path / "analysis_results.toml").write_text("this is not toml [[[")

        entries = library_store.LibraryStore(db_path, toml_path).load_all()

        assert _user_version(db_path) == SCHEMA_VERSION
        assert query_hash in entries
        assert _analysis_rows(db_path) == []

    def test_a_failed_import_keeps_the_prior_version(self, tmp_path, monkeypatch):
        db_path, toml_path, query_hash = _seed_v12(tmp_path)
        _write_legacy_analyses(tmp_path)

        def explode(self, conn):
            raise sqlite3.OperationalError("no such column: nope")

        monkeypatch.setattr(
            library_store.LibraryStore, "_import_analysis_toml", explode
        )
        with pytest.raises(library_store.LibraryMigrationError) as raised:
            library_store.LibraryStore(db_path, toml_path).load_all()

        message = str(raised.value)
        assert "schema 13" in message
        assert "no such column: nope" in message
        assert f"{db_path.name}.pre-v12.bak" in message
        assert _user_version(db_path) == 12
        backup = db_path.with_name(f"{db_path.name}.pre-v12.bak")
        conn = sqlite3.connect(backup)
        try:
            assert conn.execute("SELECT hash FROM query_identity").fetchall() == [
                (query_hash,)
            ]
        finally:
            conn.close()


class TestBoundedHistory:
    """A re-run appends; the oldest beyond the cap goes."""

    def _registry(self, tmp_path) -> AnalysisResultsRegistry:
        return AnalysisResultsRegistry(
            registry_path=str(tmp_path / "analysis_results.toml")
        )

    def _store(self, registry, index: int, score: float = 50.0) -> str:
        result = create_analysis_result(
            query_hash="hash-capped",
            target="demo",
            performance_metrics={},
            llm_analysis={
                "performance_assessment": {
                    "overall_rating": "fair",
                    "efficiency_score": score,
                }
            },
            explain_plan={},
            query_metrics={},
        )
        result.analysis_id = f"20260818_1955{index:02d}_000"
        result.timestamp = f"2026-08-18T19:{index:02d}:00Z"
        return registry.store_analysis_result("hash-capped", result)

    def test_a_rerun_appends(self, tmp_path):
        registry = self._registry(tmp_path)
        self._store(registry, 0, score=40.0)
        self._store(registry, 1, score=80.0)

        stored = registry.get_all_analyses_for_query("hash-capped")
        assert [item.analysis_id for item in stored] == [
            "20260818_195501_000",
            "20260818_195500_000",
        ]
        assert registry.get_latest_analysis("hash-capped").analysis_id == (
            "20260818_195501_000"
        )

    def test_the_eleventh_evicts_the_first(self, tmp_path):
        registry = self._registry(tmp_path)
        for index in range(ANALYSIS_HISTORY_LIMIT + 1):
            self._store(registry, index)

        stored = registry.get_all_analyses_for_query("hash-capped")
        assert len(stored) == ANALYSIS_HISTORY_LIMIT
        assert registry.get_analysis_by_id("hash-capped", "20260818_195500_000") is None
        assert stored[0].analysis_id == "20260818_195510_000"

    def test_a_minted_id_stays_distinct_within_one_second(self, tmp_path):
        registry = self._registry(tmp_path)
        first = registry.store_analysis_result(
            "hash-minted",
            create_analysis_result(
                query_hash="hash-minted",
                target="demo",
                performance_metrics={},
                llm_analysis={},
                explain_plan={},
                query_metrics={},
            ),
        )
        second = registry.store_analysis_result(
            "hash-minted",
            create_analysis_result(
                query_hash="hash-minted",
                target="demo",
                performance_metrics={},
                llm_analysis={},
                explain_plan={},
                query_metrics={},
            ),
        )

        assert first != second
        assert len(registry.get_all_analyses_for_query("hash-minted")) == 2

    def test_removing_a_query_drops_its_history(self, tmp_path):
        registry = self._registry(tmp_path)
        self._store(registry, 0)
        self._store(registry, 1)

        assert registry.remove_analyses_for_query("hash-capped") == 2
        assert registry.get_all_analyses_for_query("hash-capped") == []

    def test_cleanup_tightens_the_bound(self, tmp_path):
        registry = self._registry(tmp_path)
        for index in range(5):
            self._store(registry, index)

        assert registry.cleanup_old_analyses(keep_per_query=2) == 3
        assert [
            item.analysis_id
            for item in registry.get_all_analyses_for_query("hash-capped")
        ] == ["20260818_195504_000", "20260818_195503_000"]

    def test_analyzed_queries_list_newest_first(self, tmp_path):
        registry = self._registry(tmp_path)
        self._store(registry, 1)
        older = create_analysis_result(
            query_hash="hash-older",
            target="demo",
            performance_metrics={},
            llm_analysis={},
            explain_plan={},
            query_metrics={},
        )
        older.analysis_id = "20260101_000000_000"
        older.timestamp = "2026-01-01T00:00:00Z"
        registry.store_analysis_result("hash-older", older)

        assert registry.list_analyzed_queries() == ["hash-capped", "hash-older"]


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api")
    return app


@pytest.fixture
def analysis_registry(monkeypatch, tmp_path) -> AnalysisResultsRegistry:
    instance = AnalysisResultsRegistry(
        registry_path=str(tmp_path / "analysis_results.toml")
    )

    import shared.query_registry as shared_registry

    monkeypatch.setattr(
        shared_registry, "AnalysisResultsRegistry", lambda *a, **k: instance
    )
    return instance


def _stored_record(analysis_id: str, timestamp: str, score: float):
    result = create_analysis_result(
        query_hash="hash-viewer",
        target="demo",
        performance_metrics={"execution_time_ms": 12.5},
        llm_analysis={
            "performance_assessment": {
                "overall_rating": "good",
                "efficiency_score": score,
            }
        },
        explain_plan={"Node Type": "Seq Scan"},
        query_metrics={"calls": 42},
        rewrite_suggestions=[{"title": "add a filter"}],
        index_suggestions=[{"table": "posts"}],
        caching_recommendations={"cacheable": True},
        rewrite_test_results={"tested": True},
        database_engine="postgresql",
        llm_model_used="claude-sonnet-4-5",
        tokens_used=1234,
        display_payload={
            "explain_results": {"database_engine": "postgresql"},
            "llm_analysis": {"performance_assessment": {"overall_rating": "good"}},
            "rewrite_testing": {"tested": True},
            "index_testing": {"tested": False},
            "readyset_cacheability": {"checked": True, "cacheable": True},
            "formatted": {"metadata": {"database_engine": "postgresql"}},
        },
    )
    result.analysis_id = analysis_id
    result.timestamp = timestamp
    return result


@pytest.mark.asyncio
async def test_analyses_list_is_newest_first(app, analysis_registry):
    analysis_registry.store_analysis_result(
        "hash-viewer", _stored_record("20260818_195500_000", "2026-08-18T19:55:00Z", 60.0)
    )
    analysis_registry.store_analysis_result(
        "hash-viewer", _stored_record("20260818_195900_000", "2026-08-18T19:59:00Z", 82.0)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/query-registry/hash-viewer/analyses")

    assert response.status_code == 200
    body = response.json()
    assert body["hash"] == "hash-viewer"
    assert body["analyses"] == [
        {
            "analysis_id": "20260818_195900_000",
            "created_at": "2026-08-18T19:59:00Z",
            "target": "demo",
            "overall_rating": "good",
            "efficiency_score": 82.0,
        },
        {
            "analysis_id": "20260818_195500_000",
            "created_at": "2026-08-18T19:55:00Z",
            "target": "demo",
            "overall_rating": "good",
            "efficiency_score": 60.0,
        },
    ]


@pytest.mark.asyncio
async def test_analyses_list_is_empty_for_an_unanalyzed_query(app, analysis_registry):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/query-registry/hash-unknown/analyses")

    assert response.status_code == 200
    assert response.json() == {"hash": "hash-unknown", "analyses": []}


@pytest.mark.asyncio
async def test_stored_analysis_serves_the_whole_body(app, analysis_registry):
    record = _stored_record("20260818_195900_000", "2026-08-18T19:59:00Z", 82.0)
    analysis_registry.store_analysis_result("hash-viewer", record)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/query-registry/hash-viewer/analysis/20260818_195900_000"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["hash"] == "hash-viewer"
    assert body["analysis_id"] == "20260818_195900_000"
    assert body["created_at"] == "2026-08-18T19:59:00Z"
    assert body["target"] == "demo"
    assert body["overall_rating"] == "good"
    assert body["efficiency_score"] == 82.0
    # Everything the results view renders comes back as it went in.
    assert body["analysis"] == record.to_dict()
    assert set(body["analysis"]["display_payload"]) == {
        "explain_results",
        "llm_analysis",
        "rewrite_testing",
        "index_testing",
        "readyset_cacheability",
        "formatted",
    }


@pytest.mark.asyncio
async def test_stored_analysis_keeps_fields_a_newer_build_wrote(
    app, analysis_registry
):
    record = _stored_record("20260818_195900_000", "2026-08-18T19:59:00Z", 82.0)
    analysis_registry.store_analysis_result("hash-viewer", record)
    stored = analysis_registry.get_stored_payload(
        "hash-viewer", "20260818_195900_000"
    )
    stored["future_field"] = "written by a newer build"
    analysis_registry._store.update_analysis_payload(
        "hash-viewer", "20260818_195900_000", stored
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/query-registry/hash-viewer/analysis/20260818_195900_000"
        )

    assert response.json()["analysis"]["future_field"] == "written by a newer build"


@pytest.mark.asyncio
async def test_stored_analysis_404s_when_it_was_never_stored(app, analysis_registry):
    analysis_registry.store_analysis_result(
        "hash-viewer", _stored_record("20260818_195900_000", "2026-08-18T19:59:00Z", 82.0)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unknown_id = await client.get(
            "/api/query-registry/hash-viewer/analysis/20260101_000000_000"
        )
        unknown_hash = await client.get(
            "/api/query-registry/hash-nothing/analysis/20260818_195900_000"
        )

    assert unknown_id.status_code == 404
    assert unknown_hash.status_code == 404


@pytest.mark.asyncio
async def test_latest_still_answers_the_compact_summary(app, analysis_registry):
    analysis_registry.store_analysis_result(
        "hash-viewer", _stored_record("20260818_195900_000", "2026-08-18T19:59:00Z", 82.0)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/query-registry/hash-viewer/analysis/latest"
        )

    assert response.json() == {
        "found": True,
        "analysis": {
            "analysis_id": "20260818_195900_000",
            "analyzed_at": "2026-08-18T19:59:00Z",
            "target": "demo",
            "overall_rating": "good",
            "efficiency_score": 82.0,
        },
        "error": None,
    }
