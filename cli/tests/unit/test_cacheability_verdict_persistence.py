"""Readyset's own verdict on a query is stored where the Query Library reads it.

The compare experiment runs EXPLAIN CREATE CACHE to decide whether it can
cache a query at all. That decision belongs on the query's identity, in the
same vocabulary the compare-outcome endpoint writes: 'yes', 'pending', or
'unsupported: <reason>'. A check that could not complete is 'pending' — a
sandbox that is still starting says nothing about the query (P69).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from features.cache.events import CacheRunCompleteEvent
from features.cache.experiment_service import ReadysetExperimentService
from features.cache.readyset_explain_cache import (
    explain_cacheability_verdict,
    explain_query_id,
    readyset_support_text,
)
from shared.deploy.sandbox_manager import SandboxConnection
from shared.service_events import ErrorEvent


class TestExplainVerdict:
    """The classifier separates a verdict from a check that did not run."""

    @pytest.mark.parametrize(
        "output, expected",
        [
            ("q_abc\tSELECT 1\tyes", "yes"),
            ("q_abc\tSELECT 1\tcached", "yes"),
            ("q_abc\tSELECT 1\tno", "unsupported"),
            ("q_abc|SELECT 1|no", "unsupported"),
            ("unsupported: cross join", "unsupported"),
            ("supported", "yes"),
        ],
    )
    def test_definitive_replies(self, output, expected):
        assert explain_cacheability_verdict(output)[0] == expected

    @pytest.mark.parametrize(
        "output",
        [
            "q_abc\tSELECT 1\tno: db error",
            "q_abc\tSELECT 1\tpending",
            "q_abc\tSELECT 1\ttimed out",
            "connection refused",
            "Readyset is unavailable",
            "",
        ],
    )
    def test_incomplete_checks_are_pending_never_unsupported(self, output):
        verdict, _reason = explain_cacheability_verdict(output)
        assert verdict == "pending"

    def test_query_id_is_read_from_the_reply(self):
        assert explain_query_id("q_13b0714e\tSELECT 1\tyes") == "q_13b0714e"
        assert explain_query_id("supported") == ""

    def test_stored_spelling_matches_the_column_vocabulary(self):
        assert readyset_support_text("yes") == "yes"
        assert readyset_support_text("pending") == "pending"
        assert readyset_support_text("unsupported", "cross join") == (
            "unsupported: cross join"
        )
        assert readyset_support_text("no") == "unsupported"


class _Entry:
    def __init__(self) -> None:
        self.hash = "abc123def456"
        self.readyset_query_id = "q_previous"
        self.readyset_supported = ""
        self.last_cache_target = ""


class _Registry:
    """Records identity writes without touching a real store."""

    def __init__(self, entry: _Entry, fail: bool = False) -> None:
        self.entry = entry
        self.fail = fail
        self.lookups: list[str] = []
        self.writes: list[dict] = []

    def load(self) -> None:
        return None

    def get_query(self, query_hash: str):
        self.lookups.append(query_hash)
        return self.entry if query_hash == self.entry.hash else None

    def update_readyset_identity(self, **kwargs) -> bool:
        if self.fail:
            raise RuntimeError("library.db is locked")
        self.writes.append(kwargs)
        self.entry.readyset_supported = kwargs["readyset_supported"]
        self.entry.readyset_query_id = kwargs["readyset_query_id"]
        return True


class _Lease:
    def __init__(self) -> None:
        self.connection = SandboxConnection(
            engine="postgresql",
            host="127.0.0.1",
            port=5433,
            database="app",
            user="app",
            password="secret",
            cache_target="origin-sandbox",
        )

    async def mark_dirty(self, reason: str) -> None:
        return None


class _Manager:
    @asynccontextmanager
    async def lease(self, **_kwargs):
        yield _Lease()


class _Cache:
    def __init__(self, explain_output: str) -> None:
        self.explain_output = explain_output
        self.statements: list[str] = []
        self.timeouts: list[int | None] = []

    def _run_readyset_sql(self, statement: str, **kwargs):
        self.statements.append(statement)
        self.timeouts.append(kwargs.get("statement_timeout_ms"))
        if statement.startswith("EXPLAIN"):
            return {"success": True, "output": self.explain_output}
        return {"success": True}


@pytest.fixture
def experiment_stubs(monkeypatch):
    monkeypatch.setattr(
        "features.cache.experiment_service._origin_connection_config",
        lambda _target: {"engine": "postgresql"},
    )
    monkeypatch.setattr(
        "features.cache.experiment_service._readyset_query",
        lambda query, _engine: query,
    )

    def rows(_config, _query, _controller=None, on_execute=None):
        if on_execute is not None:
            on_execute()
        return [(1,)]

    monkeypatch.setattr("features.cache.experiment_service._execute_rows", rows)

    async def comparison(**_kwargs):
        return {
            "success": True,
            "iterations": 3,
            "original": {"stats": {"mean": 10.0, "median": 9.0}},
            "readyset": {"stats": {"mean": 1.0, "median": 0.9}},
            "speedup": {"mean": 10.0, "median": 10.0, "improvement_pct": 90.0},
            "winner": "readyset",
        }

    monkeypatch.setattr(
        "features.cache.experiment_service._run_comparison_cancellable", comparison
    )


@pytest.fixture
def registry(monkeypatch):
    store = _Registry(_Entry())
    import shared.query_registry as shared_registry

    monkeypatch.setattr(shared_registry, "QueryRegistry", lambda *a, **k: store)
    return store


async def _compare(cache: _Cache, query_hash: str = "abc123def456"):
    service = ReadysetExperimentService(_Manager(), cache)
    return [
        event
        async for event in service.compare(
            owner_id="speed_test_123",
            target="origin",
            query="SELECT 1",
            iterations=3,
            warmup=1,
            query_hash=query_hash,
        )
    ]


@pytest.mark.asyncio
async def test_supported_query_stores_yes_and_the_readyset_id(
    experiment_stubs, registry
):
    cache = _Cache("q_13b0714e\tSELECT 1\tyes")

    events = await _compare(cache)

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert registry.writes == [
        {
            "query_hash": "abc123def456",
            "readyset_query_id": "q_13b0714e",
            "readyset_supported": "yes",
            "cache_target": "origin-sandbox",
        }
    ]


@pytest.mark.asyncio
async def test_unsupported_query_stores_the_reason(experiment_stubs, registry):
    cache = _Cache("q_13b0714e\tSELECT 1\tno")

    events = await _compare(cache)

    assert registry.entry.readyset_supported == (
        "unsupported: Readyset does not support this query"
    )
    assert any(
        isinstance(event, ErrorEvent) and event.code == "readyset_unsupported"
        for event in events
    )


@pytest.mark.asyncio
async def test_incomplete_check_stores_pending_and_reports_not_ready(
    experiment_stubs, registry
):
    cache = _Cache("q_13b0714e\tSELECT 1\tno: db error")

    events = await _compare(cache)

    assert registry.entry.readyset_supported == "pending"
    # A sandbox that is still starting is not a verdict about the query.
    assert any(
        isinstance(event, ErrorEvent) and event.code == "readyset_not_ready"
        for event in events
    )
    assert cache.statements == ["EXPLAIN CREATE CACHE FROM SELECT 1"]


@pytest.mark.asyncio
async def test_failed_explain_stores_pending(experiment_stubs, registry):
    class _FailingCache(_Cache):
        def _run_readyset_sql(self, statement: str, **kwargs):
            self.statements.append(statement)
            if statement.startswith("EXPLAIN"):
                return {"success": False, "error": "connection refused"}
            return {"success": True}

    events = await _compare(_FailingCache(""))

    assert registry.entry.readyset_supported == "pending"
    assert any(
        isinstance(event, ErrorEvent) and event.code == "readyset_not_ready"
        for event in events
    )


@pytest.mark.asyncio
async def test_an_unwritable_store_does_not_fail_the_experiment(
    experiment_stubs, monkeypatch, caplog
):
    store = _Registry(_Entry(), fail=True)
    import shared.query_registry as shared_registry

    monkeypatch.setattr(shared_registry, "QueryRegistry", lambda *a, **k: store)

    events = await _compare(_Cache("q_13b0714e\tSELECT 1\tyes"))

    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)
    assert not any(isinstance(event, ErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_a_query_outside_the_registry_is_left_alone(
    experiment_stubs, registry
):
    events = await _compare(_Cache("q_13b0714e\tSELECT 1\tyes"), query_hash="missing")

    assert registry.lookups == ["missing"]
    assert registry.writes == []
    assert any(isinstance(event, CacheRunCompleteEvent) for event in events)


@pytest.mark.asyncio
async def test_without_a_hash_the_query_text_identifies_the_entry(
    experiment_stubs, registry
):
    from shared.query_registry import hash_sql

    await _compare(_Cache("q_13b0714e\tSELECT 1\tyes"), query_hash="")

    assert registry.lookups == [hash_sql("SELECT 1")]


@pytest.mark.asyncio
async def test_cold_cache_creation_gets_its_own_statement_timeout(
    experiment_stubs, registry
):
    from features.cache.experiment_service import COLD_CREATE_CACHE_TIMEOUT_MS

    cache = _Cache("q_13b0714e\tSELECT 1\tyes")

    await _compare(cache)

    creates = [
        timeout
        for statement, timeout in zip(cache.statements, cache.timeouts)
        if statement.startswith("CREATE CACHE")
    ]
    assert creates == [COLD_CREATE_CACHE_TIMEOUT_MS]
