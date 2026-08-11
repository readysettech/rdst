"""Automatic Query Library discovery stays web-owned and lifecycle-correct."""

from __future__ import annotations

import asyncio

import pytest

from features.query_registry.discovery import (
    QueryDiscoveryCollector,
    QueryDiscoveryCoordinator,
)
from features.top.events import TopConnectedEvent, TopQueriesEvent
from features.top.models import TopQueryData
from shared.query_registry import QueryRegistry


def top_query(sql: str = "SELECT * FROM users") -> TopQueryData:
    return TopQueryData(
        query_hash="upstream-hash",
        query_text=sql,
        normalized_query=sql,
        freq=42,
        total_time="1.250s",
        avg_time="0.025s",
        pct_load="12.0%",
    )


class FakeTopService:
    def __init__(self, queries: list[TopQueryData]):
        self.queries = queries
        self.calls = 0

    async def get_top_queries(self, input_data, options):
        self.calls += 1
        assert input_data.target == "demo"
        assert input_data.source == "auto"
        assert options.auto_save_registry is False
        yield TopConnectedEvent(
            type="connected",
            target_name="demo",
            db_engine="postgresql",
            source="pg_stat",
        )
        yield TopQueriesEvent(
            type="queries",
            queries=self.queries,
            source="pg_stat",
            target_name="demo",
            db_engine="postgresql",
        )


@pytest.mark.asyncio
async def test_discovery_observes_without_creating_saved_intent(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
    )

    first = await collector.collect_now()
    second = await collector.collect_now()

    registry.load()
    entry = registry.list_queries()[0]
    lifecycle = entry.lifecycle_for("demo")
    assert lifecycle is not None
    assert lifecycle.first_observed_at
    assert lifecycle.saved_at == ""
    assert lifecycle.sources == ["top-historical"]
    assert entry.frequency == 42
    assert entry.avg_duration_ms == 25.0
    assert first.event == "discovery_update"
    assert first.data["new_hashes"] == [entry.hash]
    assert second.data["new_hashes"] == []
    assert service.calls == 2


@pytest.mark.asyncio
async def test_reconnect_replays_retained_events_or_latest_snapshot(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        service_factory=lambda: service,
        registry_factory=lambda: registry,
        clock=lambda: "2026-08-10T10:00:00Z",
        history_size=2,
    )

    first = await collector.collect_now()
    second = await collector.collect_now()

    assert collector.replay_after(first.cursor) == [second]
    assert collector.replay_after(-100)[0].event == "discovery_snapshot"
    assert collector.replay_after(second.cursor) == []


def test_coordinator_reuses_one_collector_per_target():
    created: list[str] = []

    def factory(target: str):
        created.append(target)
        return QueryDiscoveryCollector(target)

    coordinator = QueryDiscoveryCoordinator(collector_factory=factory)

    first = coordinator.collector_for("demo")
    assert coordinator.collector_for("demo") is first
    assert coordinator.collector_for("analytics") is not first
    assert created == ["demo", "analytics"]


@pytest.mark.asyncio
async def test_subscription_deduplicates_event_published_during_replay(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: service,
        registry_factory=lambda: registry,
    )

    subscription = collector.subscribe()
    initial = await anext(subscription)
    update = await asyncio.wait_for(anext(subscription), timeout=1)

    assert initial.event == "discovery_snapshot"
    assert update.event == "discovery_update"
    assert update.cursor > initial.cursor

    await subscription.aclose()
    assert collector.task is None


@pytest.mark.asyncio
async def test_last_subscriber_stops_target_collector(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    service = FakeTopService([top_query()])
    collector = QueryDiscoveryCollector(
        "demo",
        interval_seconds=3600,
        service_factory=lambda: service,
        registry_factory=lambda: registry,
    )

    subscription = collector.subscribe()
    await anext(subscription)
    await asyncio.wait_for(anext(subscription), timeout=1)
    assert collector.task is not None

    await subscription.aclose()

    assert collector.task is None
