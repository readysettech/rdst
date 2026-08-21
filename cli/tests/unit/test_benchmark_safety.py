"""B5 / F4 — server-side benchmark safety rails.

The web benchmark path executes raw client SQL in a tight loop against the
selected target. These tests assert the rails hold *server-side*, independent
of the UI confirm dialog: writes are rejected, over-cap requests are rejected,
and rejections surface via the shared {code, message, detail} envelope without
leaking raw driver text.
"""

from __future__ import annotations

import re
import sqlite3
from unittest.mock import patch

import pytest

from features.query_registry.service import (
    MAX_BENCHMARK_CONCURRENCY,
    MAX_BENCHMARK_DURATION_SECONDS,
    MAX_BENCHMARK_MAX_COUNT,
    QueryService,
    _LoadTestEvidenceRecorder,
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

    @pytest.mark.asyncio
    async def test_over_cap_concurrency_rejected(self):
        events = await _collect(
            QueryService().stream_benchmark(
                queries=[{"sql": "SELECT 1"}],
                target="demo",
                mode="concurrency",
                interval_ms=0,
                concurrency=MAX_BENCHMARK_CONCURRENCY + 1,
                duration_seconds=5,
                max_count=10,
            )
        )

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].code == "benchmark_concurrency_capped"

    @pytest.mark.asyncio
    async def test_concurrency_mode_opens_one_read_only_connection_per_worker(self):
        connections: list[_FakeConnection] = []

        def create_connection(_config, *, lane=None):
            assert lane == "rdst/loadtest"
            connection = _FakeConnection()
            connections.append(connection)
            return connection

        with (
            patch(
                "shared.db_connection.create_direct_connection",
                side_effect=create_connection,
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
                    mode="concurrency",
                    interval_ms=0,
                    concurrency=3,
                    duration_seconds=1,
                    max_count=6,
                )
            )

        assert len(connections) == 3
        assert all(
            connection.executed[0] == "SET default_transaction_read_only = on"
            for connection in connections
        )
        assert events[-1].type == "complete"
        assert events[-1].total_executions == 6


class TestBenchmarkExecutionEvidence:
    """Q11 attribution: a benchmark records its exact completed executions."""

    @pytest.mark.asyncio
    async def test_successful_benchmark_records_exact_counts(self, monkeypatch):
        recorded = []

        class Writer:
            def __init__(self, target, *, lane):
                self.target = target
                self.lane = lane

            def record(self, executions, *, run_id, started_at, ended_at):
                recorded.append(
                    (self.target, self.lane, run_id, list(executions), started_at, ended_at)
                )

            def close(self):
                return None

        monkeypatch.setattr(
            "features.query_registry.service.ExecutionEvidenceWriter", Writer
        )
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

        complete = events[-1]
        assert complete.type == "complete"
        assert recorded
        assert {(target, lane) for target, lane, *_ in recorded} == {
            ("demo", "rdst/loadtest")
        }
        # Warmup executions reach the database like any other, so attribution
        # counts them even though no statistic does.
        assert (
            sum(
                execution["exec_count"]
                for _, _, _, executions, _, _ in recorded
                for execution in executions
            )
            == complete.total_successes + complete.warmup_executions
        )
        for _, _, run_id, _, started_at, ended_at in recorded:
            # Stable per-second run_id: no flush batch index in the key.
            assert re.fullmatch(r"[0-9a-f]{32}:exact:\d+", run_id)
            assert started_at <= ended_at

    def test_long_run_flushes_completion_buckets_in_their_own_windows(
        self, monkeypatch
    ):
        flushed = []

        class Writer:
            def __init__(self, _target, *, lane):
                assert lane == "rdst/loadtest"

            def record(self, executions, *, run_id, started_at, ended_at):
                flushed.append((run_id, list(executions), started_at, ended_at))

            def close(self):
                return None

        monkeypatch.setattr(
            "features.query_registry.service.ExecutionEvidenceWriter", Writer
        )
        recorder = _LoadTestEvidenceRecorder("demo")
        first = recorder.note_started("SELECT 1", 1000.1)
        recorder.note_completed(first, 1000.2)
        recorder.flush_closed(1001.0)
        second = recorder.note_started("SELECT 1", 1060.1)
        recorder.note_completed(second, 1060.2)
        recorder.flush_all()
        recorder.close()

        assert [run_id for run_id, _, _, _ in flushed] == [
            f"{recorder.run_id}:exact:1000",
            f"{recorder.run_id}:exact:1060",
        ]
        assert [rows for _, rows, _, _ in flushed] == [
            [{"sql": "SELECT 1", "exec_count": 1}],
            [{"sql": "SELECT 1", "exec_count": 1}],
        ]
        assert [(started, ended) for _, _, started, ended in flushed] == [
            (1000.2, 1000.2),
            (1060.2, 1060.2),
        ]

    def test_no_completed_or_started_execution_writes_no_evidence(
        self, monkeypatch
    ):
        flushed = []

        class Writer:
            def __init__(self, _target, *, lane):
                assert lane == "rdst/loadtest"

            def record(self, executions, *, run_id, started_at, ended_at):
                flushed.append((run_id, list(executions)))

            def close(self):
                return None

        monkeypatch.setattr(
            "features.query_registry.service.ExecutionEvidenceWriter", Writer
        )
        recorder = _LoadTestEvidenceRecorder("demo")
        recorder.flush_all()
        recorder.close()
        assert flushed == []

    @staticmethod
    def _recorder_with_store(monkeypatch, tmp_path):
        from shared.query_registry import observation_store

        cache_db = tmp_path / "cache.db"
        observation_store.ObservationStore(cache_db).close()
        monkeypatch.setattr(
            observation_store, "default_cache_db_path", lambda: cache_db
        )
        return _LoadTestEvidenceRecorder("demo"), cache_db

    @staticmethod
    def _evidence_rows(cache_db):
        with sqlite3.connect(cache_db) as db:
            return db.execute(
                "SELECT run_id, normalized_hash, exec_count FROM rdst_execution"
                " ORDER BY run_id"
            ).fetchall()

    def test_reflushed_bucket_updates_cumulative_row_in_place(
        self, monkeypatch, tmp_path
    ):
        """Flushing a bucket, completing more work in the same second, and
        flushing again must upsert ONE evidence row carrying the cumulative
        count, never a second row the overlap reader would sum twice."""
        recorder, cache_db = self._recorder_with_store(monkeypatch, tmp_path)
        sql = "SELECT * FROM orders WHERE id = 1"
        for index in range(5):
            token = recorder.note_started(sql, 1000.1 + index * 0.01)
            recorder.note_completed(token, 1000.2 + index * 0.01)
        recorder.flush_all()
        token = recorder.note_started(sql, 1000.8)
        recorder.note_completed(token, 1000.9)
        recorder.flush_all()
        recorder.close()

        rows = self._evidence_rows(cache_db)
        assert len(rows) == 1
        run_id, _, exec_count = rows[0]
        assert run_id == f"{recorder.run_id}:exact:1000"
        assert exec_count == 6

    def test_same_hash_sql_merges_into_one_row_per_second(
        self, monkeypatch, tmp_path
    ):
        """Two literals of the same normalized query completed in one second
        share a normalized_hash, so a flush must merge them into one row."""
        from shared.query_registry.query_registry import hash_sql

        first_sql = "SELECT * FROM orders WHERE id = 1"
        second_sql = "SELECT * FROM orders WHERE id = 2"
        assert hash_sql(first_sql) == hash_sql(second_sql)

        recorder, cache_db = self._recorder_with_store(monkeypatch, tmp_path)
        token = recorder.note_started(first_sql, 1000.1)
        recorder.note_completed(token, 1000.2)
        token = recorder.note_started(second_sql, 1000.3)
        recorder.note_completed(token, 1000.4)
        recorder.flush_all()
        recorder.close()

        rows = self._evidence_rows(cache_db)
        assert len(rows) == 1
        run_id, normalized_hash, exec_count = rows[0]
        assert run_id == f"{recorder.run_id}:exact:1000"
        assert normalized_hash == hash_sql(first_sql)
        assert exec_count == 2

    @pytest.mark.asyncio
    async def test_store_failure_does_not_fail_the_benchmark(
        self, monkeypatch, tmp_path
    ):
        # Drive the real best-effort helper into a broken store: cache.db
        # exists, but opening it raises. The benchmark must still complete.
        from shared.query_registry import observation_store

        cache_db = tmp_path / "cache.db"
        cache_db.touch()
        monkeypatch.setattr(
            observation_store, "default_cache_db_path", lambda: cache_db
        )

        def broken_store(*args, **kwargs):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(observation_store, "ObservationStore", broken_store)

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

        assert events[-1].type == "complete"
        assert events[-1].total_successes >= 1


class TestBenchmarkParameterResolution:
    """A `$N` identity runs on its stored values, and is refused without them."""

    PG_TEXT = "SELECT * FROM orders WHERE customer_id = $1"

    def _registry(self, monkeypatch, tmp_path, values=None):
        from shared.query_registry.query_registry import QueryRegistry

        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        query_hash, _ = registry.add_query(
            sql=self.PG_TEXT, source="top-historical", target="demo"
        )
        if values:
            registry.update_parameter_history(query_hash, values, source="user")

        import shared.query_registry as shared_registry

        monkeypatch.setattr(
            shared_registry, "QueryRegistry", lambda *a, **k: registry
        )
        return query_hash

    @pytest.mark.asyncio
    async def test_stored_values_are_substituted_before_execution(
        self, monkeypatch, tmp_path
    ):
        query_hash = self._registry(monkeypatch, tmp_path, {"p1": "42"})
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
                    queries=[query_hash],
                    target="demo",
                    mode="interval",
                    interval_ms=0,
                    concurrency=1,
                    duration_seconds=1,
                    max_count=2,
                )
            )

        assert events[-1].type == "complete"
        assert events[-1].total_successes >= 1
        executed = [sql for sql in conn.executed if "orders" in sql]
        assert executed and "$1" not in executed[0]
        assert executed[0].endswith("= 42")

    @pytest.mark.asyncio
    async def test_missing_values_still_refuse_the_run(self, monkeypatch, tmp_path):
        query_hash = self._registry(monkeypatch, tmp_path)

        events = await _collect(
            QueryService().stream_benchmark(
                queries=[query_hash],
                target="demo",
                mode="interval",
                interval_ms=0,
                concurrency=1,
                duration_seconds=1,
                max_count=2,
            )
        )

        assert events[-1].type == "error"
        assert events[-1].code == "benchmark_unresolved_params"

    @pytest.mark.asyncio
    async def test_a_value_spelling_a_slot_does_not_refuse_the_run(
        self, monkeypatch, tmp_path
    ):
        query_hash = self._registry(monkeypatch, tmp_path, {"p1": "$1"})
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
                    queries=[query_hash],
                    target="demo",
                    mode="interval",
                    interval_ms=0,
                    concurrency=1,
                    duration_seconds=1,
                    max_count=2,
                )
            )

        assert events[-1].type == "complete"
        executed = [sql for sql in conn.executed if "orders" in sql]
        assert executed and executed[0].endswith("= '$1'")


class TestBenchmarkErrorDisclosure:
    """Per-query failures reach the browser as a stable summary.

    Raw driver text names schemas, columns, hosts, and roles, and its
    wording moves with every engine release. The server keeps it in its own
    log and reports a summary the client can key on.
    """

    def test_summaries_are_stable_across_driver_wording(self):
        from features.query_registry.service import _execution_error_summary

        cases = {
            "cannot execute setval() in a read-only transaction": (
                "read-only transaction"
            ),
            "canceling statement due to statement timeout": "statement timeout",
            'permission denied for table "salaries"': "permission denied",
            'syntax error at or near "FRM"': "syntax error",
            'column "secret_col" does not exist': "missing table or column",
            "server closed the connection unexpectedly": "connection error",
            "something nobody has seen before": "execution error",
        }
        for raw, summary in cases.items():
            assert _execution_error_summary(raw) == summary

    @pytest.mark.asyncio
    async def test_driver_text_is_logged_not_returned(self, caplog):
        import logging

        conn = _FakeConnection()
        with (
            patch(
                "shared.db_connection.create_direct_connection", return_value=conn
            ),
            patch(
                "shared.config.targets.create_targets_config",
                return_value=_FakeTargetsConfig(),
            ),
            caplog.at_level(logging.WARNING),
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
        assert complete.queries[0].last_error == "read-only transaction"
        assert "setval" not in (complete.queries[0].last_error or "")
        assert "cannot execute setval()" in caplog.text
