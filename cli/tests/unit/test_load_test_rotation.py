"""Load Test measures a query, not one parameter value on one target.

A run rotates each stored query through several concrete parameter sets,
warms up off the clock, bounds every statement, and reports the queries it
could not run instead of quietly dropping them.
"""

from __future__ import annotations

import pytest

from features.query_registry.service import (
    LOAD_TEST_PARAMETER_SETS,
    SKIP_TARGET_MISMATCH,
    SKIP_UNRESOLVED_PARAMETERS,
    QueryService,
    parameter_variants,
    set_session_statement_timeout,
)

pytestmark = pytest.mark.usefixtures("run_executor_inline")

PG_TEXT = "SELECT * FROM orders WHERE customer_id = $1"


async def _collect(agen):
    return [event async for event in agen]


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn

    def execute(self, sql: str) -> None:
        self._conn.executed.append(sql)
        if self._conn.slow_sql and self._conn.slow_sql in sql.lower():
            raise RuntimeError(
                "canceling statement due to statement timeout"
            )

    def fetchall(self):
        return []

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, slow_sql: str | None = None) -> None:
        self.executed: list[str] = []
        self.slow_sql = slow_sql

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        return None


class _FakeTargetsConfig:
    def load(self) -> None:
        return None

    def get_default(self) -> str:
        return "demo"

    def get(self, name: str) -> dict:
        return {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "database": "shop",
            "user": "app",
        }


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """A real registry on a temporary file, patched in for the service."""
    from shared.query_registry.query_registry import QueryRegistry
    import shared.query_registry as shared_registry

    store = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    store.load()
    monkeypatch.setattr(shared_registry, "QueryRegistry", lambda *a, **k: store)
    return store


@pytest.fixture
def suggestions(monkeypatch):
    """Serve a fixed set of suggested values for the `$1` placeholder."""

    def _install(values: list[str]):
        def _suggest(sql, target, target_config=None, query_hash=None):
            del sql, target, target_config, query_hash
            return {
                "placeholders": [
                    {
                        "placeholder": ":p1",
                        "index": 1,
                        "column": "orders.customer_id",
                        "suggestions": [
                            {"value": value, "provenance": "pg_stats"}
                            for value in values
                        ],
                    }
                ],
                "sample": None,
            }

        monkeypatch.setattr(
            "features.analyze.parameter_suggestions.suggest_parameter_values",
            _suggest,
        )

    return _install


async def _run(connection, **overrides):
    from unittest.mock import patch

    settings = {
        "target": "demo",
        "mode": "interval",
        "interval_ms": 0,
        "concurrency": 1,
        "duration_seconds": 2,
        "max_count": 10,
        "warmup_executions": 0,
    }
    settings.update(overrides)
    queries = settings.pop("queries")
    with (
        patch(
            "shared.db_connection.create_direct_connection",
            return_value=connection,
        ),
        patch(
            "shared.config.targets.create_targets_config",
            return_value=_FakeTargetsConfig(),
        ),
    ):
        return await _collect(
            QueryService().stream_benchmark(queries=queries, **settings)
        )


class TestParameterRotation:
    """Five value sets per query, rotated in a fixed order."""

    @pytest.mark.asyncio
    async def test_stored_values_lead_and_suggestions_follow(
        self, registry, suggestions
    ):
        query_hash, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        registry.update_parameter_history(query_hash, {"p1": "42"}, source="user")
        suggestions(["7", "8", "9", "10", "11"])
        conn = _FakeConnection()

        events = await _run(conn, queries=[query_hash], max_count=5)

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.queries[0].variant_count == LOAD_TEST_PARAMETER_SETS
        executed = [sql for sql in conn.executed if "orders" in sql]
        assert [sql.rsplit("= ", 1)[1] for sql in executed] == [
            "42",
            "7",
            "8",
            "9",
            "10",
        ]

    @pytest.mark.asyncio
    async def test_round_robin_visits_every_query_before_repeating_a_value(
        self, registry, suggestions
    ):
        first, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        registry.update_parameter_history(first, {"p1": "1"}, source="user")
        second, _ = registry.add_query(
            sql="SELECT * FROM orders WHERE region_id = $1",
            source="top-historical",
            target="demo",
        )
        registry.update_parameter_history(second, {"p1": "2"}, source="user")
        suggestions(["90"])
        conn = _FakeConnection()

        events = await _run(conn, queries=[first, second], max_count=4)

        assert events[-1].type == "complete"
        executed = [sql for sql in conn.executed if "orders" in sql]
        assert [sql.rsplit("= ", 1)[1] for sql in executed] == [
            "1",
            "2",
            "90",
            "90",
        ]

    @pytest.mark.asyncio
    async def test_a_query_without_suggestions_runs_one_variant(
        self, registry, monkeypatch
    ):
        query_hash, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        registry.update_parameter_history(query_hash, {"p1": "42"}, source="user")

        def _unavailable(*_args, **_kwargs):
            raise RuntimeError("the database refused the sample")

        monkeypatch.setattr(
            "features.analyze.parameter_suggestions.suggest_parameter_values",
            _unavailable,
        )
        conn = _FakeConnection()

        events = await _run(conn, queries=[query_hash], max_count=3)

        assert events[-1].queries[0].variant_count == 1
        executed = [sql for sql in conn.executed if "orders" in sql]
        assert {sql.rsplit("= ", 1)[1] for sql in executed} == {"42"}

    @pytest.mark.asyncio
    async def test_rotation_can_be_turned_off(self, registry, suggestions):
        query_hash, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        registry.update_parameter_history(query_hash, {"p1": "42"}, source="user")
        suggestions(["7", "8"])
        conn = _FakeConnection()

        events = await _run(
            conn, queries=[query_hash], max_count=3, parameter_sets=1
        )

        assert events[-1].queries[0].variant_count == 1

    @pytest.mark.asyncio
    async def test_request_supplied_sql_is_run_exactly_as_sent(
        self, registry, suggestions
    ):
        suggestions(["7", "8"])
        conn = _FakeConnection()

        events = await _run(
            conn,
            queries=[{"identifier": "custom", "sql": "SELECT 1"}],
            max_count=3,
        )

        assert events[-1].queries[0].variant_count == 1
        assert [sql for sql in conn.executed if sql == "SELECT 1"]

    def test_a_target_without_a_database_is_not_sampled(self, registry):
        query_hash, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        entry = registry.get_query(query_hash)

        variants = parameter_variants(
            entry, "SELECT 1", "demo", {"engine": "postgresql"}, 5
        )

        assert variants == ["SELECT 1"]


class TestTargetScoping:
    """A run covers the selected target's workload, not the whole library."""

    @pytest.mark.asyncio
    async def test_a_query_from_another_target_is_skipped_and_reported(
        self, registry
    ):
        mine, _ = registry.add_query(
            sql="SELECT * FROM orders", source="top-historical", target="demo"
        )
        theirs, _ = registry.add_query(
            sql="SELECT * FROM customers", source="top-historical", target="other"
        )
        conn = _FakeConnection()

        events = await _run(conn, queries=[mine, theirs], max_count=2)

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.skipped_count == 1
        assert [
            (skip.query_hash, skip.reason) for skip in complete.skipped_queries
        ] == [(theirs, SKIP_TARGET_MISMATCH)]
        assert [stats.query_hash for stats in complete.queries] == [mine]

    @pytest.mark.asyncio
    async def test_every_query_from_another_target_refuses_the_run(
        self, registry
    ):
        theirs, _ = registry.add_query(
            sql="SELECT * FROM customers", source="top-historical", target="other"
        )

        events = await _run(_FakeConnection(), queries=[theirs])

        assert events[-1].type == "error"
        assert events[-1].code == "benchmark_all_queries_skipped"


class TestWarmup:
    """Warmup executes but is measured by nothing."""

    @pytest.mark.asyncio
    async def test_warmup_runs_are_excluded_from_the_statistics(self, registry):
        query_hash, _ = registry.add_query(
            sql="SELECT 1", source="top-historical", target="demo"
        )
        conn = _FakeConnection()

        events = await _run(
            conn, queries=[query_hash], max_count=2, warmup_executions=3
        )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.warmup_executions == 3
        assert complete.total_executions == 2
        assert complete.queries[0].executions == 2
        assert conn.executed.count("SELECT 1") == 5

    @pytest.mark.asyncio
    async def test_warmup_precedes_every_measured_execution(self, registry):
        first, _ = registry.add_query(
            sql="SELECT * FROM orders", source="top-historical", target="demo"
        )
        second, _ = registry.add_query(
            sql="SELECT * FROM customers", source="top-historical", target="demo"
        )
        conn = _FakeConnection()

        await _run(
            conn, queries=[first, second], max_count=2, warmup_executions=1
        )

        statements = [sql for sql in conn.executed if sql.startswith("SELECT")]
        assert statements[:2] == ["SELECT * FROM orders", "SELECT * FROM customers"]
        assert len(statements) == 4


class TestStatementTimeout:
    """One pathological query cannot hold the run."""

    def test_postgres_and_mysql_spellings(self):
        conn = _FakeConnection()
        set_session_statement_timeout(conn, "postgresql", 15000)
        assert conn.executed == ["SET statement_timeout = 15000"]

        conn = _FakeConnection()
        set_session_statement_timeout(conn, "mysql", 15000)
        assert conn.executed == ["SET SESSION MAX_EXECUTION_TIME = 15000"]

    @pytest.mark.asyncio
    async def test_the_session_is_bounded_before_any_query(self, registry):
        query_hash, _ = registry.add_query(
            sql="SELECT 1", source="top-historical", target="demo"
        )
        conn = _FakeConnection()

        await _run(
            conn,
            queries=[query_hash],
            max_count=1,
            statement_timeout_ms=12000,
        )

        assert conn.executed[:2] == [
            "SET default_transaction_read_only = on",
            "SET statement_timeout = 12000",
        ]

    @pytest.mark.asyncio
    async def test_a_timed_out_statement_is_recorded_and_the_run_continues(
        self, registry
    ):
        slow, _ = registry.add_query(
            sql="SELECT pg_sleep(60)", source="top-historical", target="demo"
        )
        quick, _ = registry.add_query(
            sql="SELECT * FROM orders", source="top-historical", target="demo"
        )
        conn = _FakeConnection(slow_sql="pg_sleep")

        events = await _run(conn, queries=[slow, quick], max_count=4)

        complete = events[-1]
        assert complete.type == "complete"
        by_hash = {stats.query_hash: stats for stats in complete.queries}
        assert by_hash[slow].timeouts == 2
        assert by_hash[slow].last_error == "statement timeout"
        assert by_hash[quick].successes == 2
        assert by_hash[quick].timeouts == 0


class TestSkipSurfacing:
    """A query left out of the run is visible in the run's own payload."""

    @pytest.mark.asyncio
    async def test_unresolved_parameters_are_reported_beside_the_results(
        self, registry
    ):
        runnable, _ = registry.add_query(
            sql="SELECT * FROM orders", source="top-historical", target="demo"
        )
        unresolved, _ = registry.add_query(
            sql=PG_TEXT, source="top-historical", target="demo"
        )
        conn = _FakeConnection()

        events = await _run(conn, queries=[runnable, unresolved], max_count=2)

        complete = events[-1]
        assert complete.skipped_count == 1
        skip = complete.skipped_queries[0]
        assert (skip.query_hash, skip.reason) == (
            unresolved,
            SKIP_UNRESOLVED_PARAMETERS,
        )

    @pytest.mark.asyncio
    async def test_a_query_that_never_ran_is_listed_with_zeros(self, registry):
        first, _ = registry.add_query(
            sql="SELECT * FROM orders", source="top-historical", target="demo"
        )
        second, _ = registry.add_query(
            sql="SELECT * FROM customers", source="top-historical", target="demo"
        )
        conn = _FakeConnection()

        events = await _run(conn, queries=[first, second], max_count=1)

        complete = events[-1]
        by_hash = {stats.query_hash: stats for stats in complete.queries}
        assert set(by_hash) == {first, second}
        assert by_hash[second].executions == 0
