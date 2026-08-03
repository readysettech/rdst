"""B5 / F4 — server-side benchmark safety rails.

The web benchmark path executes raw client SQL in a tight loop against the
selected target. These tests assert the rails hold *server-side*, independent
of the UI confirm dialog: writes are rejected, over-cap requests are rejected,
and rejections surface via the shared {code, message, detail} envelope without
leaking raw driver text.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from features.query_registry.service import (
    MAX_BENCHMARK_DURATION_SECONDS,
    MAX_BENCHMARK_MAX_COUNT,
    QueryService,
    benchmark_read_only_reason,
    set_session_read_only,
)

pytestmark = pytest.mark.usefixtures("run_executor_inline")


class TestBenchmarkReadOnlyReason:
    """Unit tests for the read-only statement classifier."""

    def test_plain_select_allowed(self):
        assert benchmark_read_only_reason("SELECT * FROM orders WHERE id = 1") is None

    def test_cte_select_allowed(self):
        assert (
            benchmark_read_only_reason(
                "WITH recent AS (SELECT * FROM orders LIMIT 10) "
                "SELECT count(*) FROM recent"
            )
            is None
        )

    def test_select_with_trailing_semicolon_allowed(self):
        assert benchmark_read_only_reason("SELECT 1;") is None

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM orders WHERE 1=1",
            "UPDATE orders SET total = 0",
            "INSERT INTO orders (id) VALUES (1)",
            "DROP TABLE orders",
            "TRUNCATE orders",
            "ALTER TABLE orders ADD COLUMN x int",
            "CREATE TABLE t (id int)",
            "GRANT ALL ON orders TO public",
        ],
    )
    def test_write_statements_rejected(self, sql):
        assert benchmark_read_only_reason(sql) is not None

    def test_multi_statement_rejected(self):
        reason = benchmark_read_only_reason("SELECT 1; DELETE FROM orders")
        assert reason is not None
        assert "multiple statements" in reason.lower()

    def test_data_modifying_cte_rejected(self):
        # The exact F7-style bypass: a SELECT/WITH lead that hides a DELETE.
        reason = benchmark_read_only_reason(
            "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone"
        )
        assert reason is not None

    def test_select_into_rejected(self):
        reason = benchmark_read_only_reason("SELECT * INTO backup FROM orders")
        assert reason is not None

    def test_empty_rejected(self):
        assert benchmark_read_only_reason("   ") is not None


async def _collect(agen):
    events = []
    async for event in agen:
        events.append(event)
    return events


class _FakeCursor:
    """Cursor that emulates a database enforcing a read-only session: once the
    session is set read-only, a SELECT-invoked write function (setval) fails at
    execution — exactly what PostgreSQL does."""

    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn

    def execute(self, sql: str) -> None:
        self._conn.executed.append(sql)
        normalized = " ".join(sql.strip().lower().split())
        if normalized.startswith("set "):
            if (
                "read only" in normalized
                or "default_transaction_read_only" in normalized
            ):
                self._conn.read_only = True
            return
        if self._conn.read_only and "setval" in normalized:
            raise RuntimeError("cannot execute setval() in a read-only transaction")

    def fetchall(self):
        return []

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.read_only = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        pass


class _FakeTargetsConfig:
    def __init__(self, engine: str = "postgresql") -> None:
        self._engine = engine

    def load(self) -> None:
        pass

    def get_default(self) -> str:
        return "demo"

    def get(self, name: str) -> dict:
        return {"engine": self._engine, "host": "127.0.0.1"}


class TestSessionReadOnlyRail:
    """B5 must-fix — read-only enforced at the SESSION level, not just lexically.

    SELECT-invoked write functions (``setval()``, ``dblink_exec()``, DML-bearing
    UDFs) parse as plain reads and pass ``benchmark_read_only_reason``; the
    session rail makes them fail at execution inside the database.
    """

    def test_lexical_classifier_gap_documented(self):
        # setval() parses as a plain SELECT — the lexical rail passes it.
        # This is the documented gap the session-level rail exists to close.
        assert (
            benchmark_read_only_reason("SELECT setval('orders_id_seq', 42)") is None
        )

    def test_set_session_read_only_postgres_syntax(self):
        conn = _FakeConnection()
        set_session_read_only(conn, "postgresql")
        assert conn.executed == ["SET default_transaction_read_only = on"]

    def test_set_session_read_only_mysql_syntax(self):
        conn = _FakeConnection()
        set_session_read_only(conn, "mysql")
        assert conn.executed == ["SET SESSION TRANSACTION READ ONLY"]

    @pytest.mark.asyncio
    async def test_benchmark_sets_session_read_only_before_any_query(self):
        conn = _FakeConnection()
        with (
            patch(
                "shared.db_connection.create_direct_connection", return_value=conn
            ),
            patch(
                "shared.config.targets.create_targets_config",
                return_value=_FakeTargetsConfig(),
            ),
        ):
            events = await _collect(
                QueryService().stream_benchmark(
                    queries=[{"identifier": "q", "sql": "SELECT 1"}],
                    target="demo",
                    mode="interval",
                    interval_ms=0,
                    concurrency=1,
                    duration_seconds=1,
                    max_count=2,
                )
            )

        # The very first statement on the connection is the read-only SET.
        assert conn.executed[0] == "SET default_transaction_read_only = on"
        assert events[-1].type == "complete"
        assert events[-1].total_successes >= 1

    @pytest.mark.asyncio
    async def test_select_invoked_write_fails_at_execution(self):
        """A setval() write that passes the lexical check FAILS at execution
        under the read-only session, and the failure surfaces in the stats."""
        conn = _FakeConnection()
        with (
            patch(
                "shared.db_connection.create_direct_connection", return_value=conn
            ),
            patch(
                "shared.config.targets.create_targets_config",
                return_value=_FakeTargetsConfig(),
            ),
        ):
            events = await _collect(
                QueryService().stream_benchmark(
                    queries=[
                        {
                            "identifier": "sneaky",
                            "sql": "SELECT setval('orders_id_seq', 42)",
                        }
                    ],
                    target="demo",
                    mode="interval",
                    interval_ms=0,
                    concurrency=1,
                    duration_seconds=1,
                    max_count=2,
                )
            )

        complete = events[-1]
        assert complete.type == "complete"
        assert complete.total_successes == 0
        assert complete.total_failures >= 1
        assert "read-only" in (complete.queries[0].last_error or "")

    @pytest.mark.asyncio
    async def test_read_only_session_failure_fails_closed(self):
        """If the session cannot be made read-only, the run aborts with the
        shared envelope — it never falls back to a writable session."""

        class _NoReadOnlySupportConnection(_FakeConnection):
            def cursor(self):
                conn = self

                class _RefusingCursor(_FakeCursor):
                    def execute(self, sql: str) -> None:
                        if sql.strip().lower().startswith("set "):
                            raise RuntimeError("SET is not supported here")
                        super().execute(sql)

                return _RefusingCursor(conn)

        conn = _NoReadOnlySupportConnection()
        with (
            patch(
                "shared.db_connection.create_direct_connection", return_value=conn
            ),
            patch(
                "shared.config.targets.create_targets_config",
                return_value=_FakeTargetsConfig(),
            ),
        ):
            events = await _collect(
                QueryService().stream_benchmark(
                    queries=[{"identifier": "q", "sql": "SELECT 1"}],
                    target="demo",
                    mode="interval",
                    interval_ms=0,
                    concurrency=1,
                    duration_seconds=1,
                    max_count=2,
                )
            )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_read_only_session"
        # Humane envelope message; raw driver text stays out.
        assert "SET is not supported here" not in events[0].message


class TestStreamBenchmarkRails:
    """The rails must fire on the real streaming path, before any DB work."""

    @pytest.mark.asyncio
    async def test_write_query_rejected_before_execution(self):
        service = QueryService()
        events = await _collect(
            service.stream_benchmark(
                queries=[{"sql": "DELETE FROM orders WHERE 1=1"}],
                target="demo",
                mode="interval",
                interval_ms=0,
                concurrency=1,
                duration_seconds=5,
                max_count=None,
            )
        )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_read_only"
        # Humane, safe message — names the offending keyword, no driver text.
        assert "read-only" in events[0].message.lower()

    @pytest.mark.asyncio
    async def test_over_cap_duration_rejected(self):
        service = QueryService()
        events = await _collect(
            service.stream_benchmark(
                queries=[{"sql": "SELECT 1"}],
                target="demo",
                mode="interval",
                interval_ms=100,
                concurrency=1,
                duration_seconds=MAX_BENCHMARK_DURATION_SECONDS + 1,
                max_count=None,
            )
        )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_duration_capped"

    @pytest.mark.asyncio
    async def test_over_cap_count_rejected(self):
        service = QueryService()
        events = await _collect(
            service.stream_benchmark(
                queries=[{"sql": "SELECT 1"}],
                target="demo",
                mode="interval",
                interval_ms=100,
                concurrency=1,
                duration_seconds=5,
                max_count=MAX_BENCHMARK_MAX_COUNT + 1,
            )
        )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_count_capped"
