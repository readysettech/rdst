"""Tests for the two-phase statement-statistics sources (research Q1/Q2/Q3, Q9 rules 5-6)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.query_registry import hash_sql

from features.query_registry.statement_stats import (
    ACTIVITY_SAMPLE_LIMIT,
    ACTIVITY_SAMPLE_TIMEOUT_MS,
    MYSQL_ACTIVITY_SAMPLE_SQL,
    MYSQL_SERVER_START_TOLERANCE_S,
    PG_ACTIVITY_SAMPLE_SQL,
    PG_TEXT_FETCH_BATCH,
    RDST_OBSERVE_MARKER,
    MysqlDigestSource,
    PgStatStatementsSource,
    mysql_engine_key,
    parse_pg_engine_key,
    pg_engine_key,
    pg_phase_a_sql,
)


class FakeExecutor:
    """Substring-matched canned responses; records every (sql, params) call in order."""

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        for matcher, rows in self.rules:
            if matcher in sql:
                return rows
        return []

    def sql_containing(self, fragment):
        matches = [sql for sql, _ in self.calls if fragment in sql]
        assert matches, f"no executed SQL contains {fragment!r}"
        return matches[0]

    def params_for(self, fragment):
        for sql, params in self.calls:
            if fragment in sql:
                return params
        raise AssertionError(f"no executed SQL contains {fragment!r}")


def pg_stat_row(version, userid=10, dbid=16384, queryid=111, toplevel=True,
                calls=5, rows=50, total=100.0, mean=20.0, minimum=1.0, maximum=40.0,
                stddev=2.5, stats_since=None, minmax_stats_since=None):
    row = [userid, dbid, queryid]
    if version >= 140000:
        row.append(toplevel)
    row += [calls, rows, total, mean, minimum, maximum, stddev]
    if version >= 170000:
        row += [stats_since, minmax_stats_since]
    return tuple(row)


def pg_executor(version, stat_rows=(), *, now=2000.0, postmaster_start=1111.0,
                stats_reset=555.0, dealloc=7, settings=None):
    if settings is None:
        settings = [("pg_stat_statements.max", "5000"), ("pg_stat_statements.track", "top")]
    rules = [
        ("server_version_num", [(version,)]),
        ("pg_stat_statements_info", [(now, postmaster_start, stats_reset, dealloc)]),
        ("pg_postmaster_start_time", [(now, postmaster_start)]),
        ("pg_stat_statements(false)", list(stat_rows)),
        ("pg_settings", settings),
        ("SET LOCAL", []),
    ]
    return FakeExecutor(rules)


class TestPgSqlVariants:
    def test_pg13_variant(self):
        source = PgStatStatementsSource()
        executor = pg_executor(130005, [pg_stat_row(130005)])
        result = source.collect(executor)
        sql = executor.sql_containing("pg_stat_statements(false)")
        lowered = sql.lower()
        assert "toplevel" not in lowered
        assert "stats_since" not in lowered
        assert "sum(" not in lowered
        assert "order by" not in lowered
        assert "limit" not in lowered
        assert "not like" not in lowered
        assert "where dbid = (select oid from pg_database" in lowered
        assert "queryid is not null" in lowered
        # Info view is PG 14+; below it the epoch is postmaster start only.
        assert not any("pg_stat_statements_info" in sql for sql, _ in executor.calls)
        assert result.rows[0].engine_key.endswith(":t")
        assert result.capabilities["toplevel_ambiguous"] is True
        assert result.capabilities["dealloc"] is None

    def test_pg15_variant(self):
        source = PgStatStatementsSource()
        executor = pg_executor(150002, [pg_stat_row(150002, toplevel=False)])
        result = source.collect(executor)
        sql = executor.sql_containing("pg_stat_statements(false)")
        assert "toplevel" in sql
        assert "stats_since" not in sql
        assert any("pg_stat_statements_info" in sql for sql, _ in executor.calls)
        assert result.rows[0].engine_key.endswith(":f")
        assert result.rows[0].stats_since is None
        assert result.rows[0].minmax_stats_since is None
        assert result.capabilities["toplevel_ambiguous"] is False
        assert result.capabilities["dealloc"] == 7

    def test_pg17_variant(self):
        source = PgStatStatementsSource()
        executor = pg_executor(170000, [pg_stat_row(170000, stats_since=1234.5,
                                                    minmax_stats_since=1300.0)])
        result = source.collect(executor)
        sql = executor.sql_containing("pg_stat_statements(false)")
        assert "stats_since" in sql
        assert "minmax_stats_since" in sql
        assert result.rows[0].stats_since == 1234.5
        assert result.rows[0].minmax_stats_since == 1300.0

    def test_below_pg13_rejected(self):
        with pytest.raises(ValueError):
            pg_phase_a_sql(120004)
        with pytest.raises(ValueError):
            PgStatStatementsSource().collect(pg_executor(120004))

    def test_full_sweep_is_not_incremental(self):
        result = PgStatStatementsSource().collect(pg_executor(150002))
        assert result.incremental is False
        assert result.next_cursor_state is None

    def test_setup_statements_run_first(self):
        source = PgStatStatementsSource()
        executor = pg_executor(150002)
        source.collect(executor)
        first_two = [sql for sql, _ in executor.calls[:2]]
        assert first_two == list(source.session_setup_statements)
        assert any("work_mem" in sql for sql in first_two)
        assert any("statement_timeout" in sql for sql in first_two)


class TestEngineKeys:
    def test_pg_key_preserves_signed_queryid(self):
        key = pg_engine_key(10, 16384, -8949261120434384446, "t")
        assert key == "10:16384:-8949261120434384446:t"
        assert parse_pg_engine_key(key) == (10, 16384, -8949261120434384446, "t")

    def test_pg_key_from_collect_with_negative_queryid(self):
        executor = pg_executor(150002, [pg_stat_row(150002, queryid=-42, toplevel=True)])
        result = PgStatStatementsSource().collect(executor)
        assert result.rows[0].engine_key == "10:16384:-42:t"

    def test_mysql_key_with_null_schema(self):
        assert mysql_engine_key(None, "abc123") == ":abc123"
        assert mysql_engine_key("app", "abc123") == "app:abc123"


class TestPgEpochIdentity:
    def collect_epoch(self, **kwargs):
        executor = pg_executor(150002, [pg_stat_row(150002)], **kwargs)
        return PgStatStatementsSource().collect(executor).epoch_id

    def test_stable_when_inputs_stable(self):
        assert self.collect_epoch() == self.collect_epoch()

    def test_changes_when_stats_reset_changes(self):
        assert self.collect_epoch(stats_reset=555.0) != self.collect_epoch(stats_reset=999.0)

    def test_changes_when_postmaster_start_changes(self):
        assert (self.collect_epoch(postmaster_start=1111.0)
                != self.collect_epoch(postmaster_start=3333.0))

    def test_dealloc_does_not_change_epoch(self):
        assert self.collect_epoch(dealloc=0) == self.collect_epoch(dealloc=5000)


class TestPgCapabilities:
    def test_populated(self):
        executor = pg_executor(150002, [pg_stat_row(150002), pg_stat_row(150002, queryid=222)])
        caps = PgStatStatementsSource().collect(executor).capabilities
        assert caps["statement_count"] == 2
        assert caps["pgss_max"] == 5000
        assert caps["track"] == "top"
        assert caps["dealloc"] == 7

    def test_settings_failure_is_non_fatal(self):
        executor = pg_executor(150002, [pg_stat_row(150002)])

        def failing_executor(sql, params=None):
            if "pg_settings" in sql:
                raise RuntimeError("permission denied")
            return executor(sql, params)

        caps = PgStatStatementsSource().collect(failing_executor).capabilities
        assert caps["pgss_max"] is None
        assert caps["track"] is None
        assert caps["statement_count"] == 1


class TestPgPhaseB:
    def test_needs_text_excludes_known_keys(self):
        executor = pg_executor(150002, [pg_stat_row(150002, queryid=1),
                                        pg_stat_row(150002, queryid=2)])
        result = PgStatStatementsSource().collect(
            executor, known_text_keys={"10:16384:1:t"}
        )
        assert result.needs_text == frozenset({"10:16384:2:t"})
        assert result.texts == {}

    def test_text_fetch_sql_shape(self):
        source = PgStatStatementsSource()
        keys = ["10:16384:-42:t", "10:16384:7:f", "11:16384:7:t"]
        batches = source.text_fetch_sql(keys, 150002)
        assert len(batches) == 1
        sql, params = batches[0]
        assert "queryid = ANY(%s)" in sql
        assert "dbid = %s" in sql
        assert "toplevel" in sql
        assert "query" in sql
        queryids, dbid = params
        assert queryids == [-42, 7]  # signed queryid preserved, deduped, sorted
        assert dbid == 16384

    def test_text_fetch_sql_pg13_omits_toplevel(self):
        batches = PgStatStatementsSource().text_fetch_sql(["10:16384:7:t"], 130005)
        assert "toplevel" not in batches[0][0]

    def test_text_fetch_batches(self):
        keys = [f"10:16384:{i}:t" for i in range(PG_TEXT_FETCH_BATCH + 1)]
        batches = PgStatStatementsSource().text_fetch_sql(keys, 150002)
        assert len(batches) == 2
        assert len(batches[0][1][0]) == PG_TEXT_FETCH_BATCH
        assert len(batches[1][1][0]) == 1

    def test_text_fetch_empty(self):
        assert PgStatStatementsSource().text_fetch_sql([], 150002) == []

    def test_text_timeout_budget(self):
        setup = PgStatStatementsSource().text_fetch_setup_statements
        assert any("120000" in stmt for stmt in setup)


NOW6 = "2026-08-16 10:00:00.123456"
FIRST_SEEN = datetime(2026, 8, 16, 9, 0, 0, 1, tzinfo=timezone.utc)


_NO_SAMPLE = object()


def mysql_digest_row(schema="app", digest="d1", text="SELECT ?", count=5,
                     sum_wait=5_000_000_000, min_wait=1_000_000_000,
                     avg_wait=1_000_000_000, max_wait=2_000_000_000,
                     rows_sent=50, first_seen=FIRST_SEEN, last_seen=NOW6,
                     sample=_NO_SAMPLE):
    row = (schema, digest, text, count, sum_wait, min_wait, avg_wait, max_wait,
           rows_sent, 60, 0, first_seen, last_seen)
    if sample is not _NO_SAMPLE:
        row += (sample,)
    return row


def mysql_executor(digest_rows=(), *, server_start=1000.0, null_count=17,
                   digest_lost=3, digests_size=10000, row_count=None,
                   sample_capable=False):
    if row_count is None:
        row_count = len(digest_rows)
    rules = [
        ("information_schema.columns", [(1,)] if sample_capable else []),
        ("UNIX_TIMESTAMP", [(server_start,)]),
        ("SELECT NOW(6)", [(NOW6,)]),
        ("COUNT(*)", [(row_count,)]),
        ("DIGEST IS NULL", [(null_count,)]),
        ("Performance_schema_digest_lost", [("Performance_schema_digest_lost",
                                             str(digest_lost))]),
        ("performance_schema_digests_size", [("performance_schema_digests_size",
                                              str(digests_size))]),
        ("FROM performance_schema.events_statements_summary_by_digest", list(digest_rows)),
        ("SET ", []),
    ]
    return FakeExecutor(rules)


class TestMysqlCursor:
    def test_first_run_full_sweep(self):
        executor = mysql_executor([mysql_digest_row()])
        result = MysqlDigestSource().collect(executor, cursor_state=None)
        sweep = executor.sql_containing("events_statements_summary_by_digest WHERE")
        assert "LAST_SEEN >=" not in sweep
        assert "DIGEST IS NOT NULL" in sweep
        assert result.incremental is True

    def test_cursor_comes_from_server_clock(self):
        executor = mysql_executor([mysql_digest_row()])
        result = MysqlDigestSource().collect(executor)
        assert result.next_cursor_state["last_seen"] == NOW6
        assert result.next_cursor_state["server_start"] == 1000.0

    def test_incremental_uses_gte_boundary(self):
        executor = mysql_executor([mysql_digest_row()])
        cursor = {"last_seen": "2026-08-16 09:59:00.000000", "server_start": 1000.0}
        MysqlDigestSource().collect(executor, cursor_state=cursor)
        sweep = executor.sql_containing("LAST_SEEN")
        assert "LAST_SEEN >= %s" in sweep
        assert "LAST_SEEN > %s" not in sweep
        assert executor.params_for("LAST_SEEN") == ("2026-08-16 09:59:00.000000",)

    def test_restart_discards_cursor(self):
        executor = mysql_executor([mysql_digest_row()],
                                  server_start=1000.0 + MYSQL_SERVER_START_TOLERANCE_S + 1)
        cursor = {"last_seen": "2026-08-16 09:59:00.000000", "server_start": 1000.0}
        result = MysqlDigestSource().collect(executor, cursor_state=cursor)
        sweep = executor.sql_containing("events_statements_summary_by_digest WHERE")
        assert "LAST_SEEN >=" not in sweep
        assert result.next_cursor_state["server_start"] == pytest.approx(
            1000.0 + MYSQL_SERVER_START_TOLERANCE_S + 1
        )

    def test_setup_pins_utc(self):
        executor = mysql_executor()
        MysqlDigestSource().collect(executor)
        assert executor.calls[0][0] == "SET time_zone = '+00:00'"


class TestMysqlEpochIdentity:
    def test_stable_within_uptime_jitter(self):
        first = MysqlDigestSource().collect(mysql_executor(server_start=1000.0))
        second = MysqlDigestSource().collect(
            mysql_executor(server_start=1000.9),
            cursor_state=first.next_cursor_state,
        )
        assert first.epoch_id == second.epoch_id
        # The stabilized estimate carries forward unchanged.
        assert second.next_cursor_state["server_start"] == 1000.0

    def test_changes_on_restart(self):
        first = MysqlDigestSource().collect(mysql_executor(server_start=1000.0))
        second = MysqlDigestSource().collect(
            mysql_executor(server_start=9000.0),
            cursor_state=first.next_cursor_state,
        )
        assert first.epoch_id != second.epoch_id


class TestMysqlRows:
    def test_row_conversion_and_first_seen(self):
        result = MysqlDigestSource().collect(mysql_executor([mysql_digest_row()]))
        row = result.rows[0]
        assert row.engine_key == "app:d1"
        assert row.calls == 5
        assert row.rows == 50
        # Picoseconds to milliseconds.
        assert row.total_exec_time == pytest.approx(5.0)
        assert row.mean_exec_time == pytest.approx(1.0)
        assert row.min_exec_time == pytest.approx(1.0)
        assert row.max_exec_time == pytest.approx(2.0)
        assert row.first_seen == pytest.approx(FIRST_SEEN.timestamp())
        assert row.stats_since is None

    def test_null_schema_row(self):
        result = MysqlDigestSource().collect(
            mysql_executor([mysql_digest_row(schema=None, digest="d9")])
        )
        assert result.rows[0].engine_key == ":d9"

    def test_texts_carried_no_phase_b(self):
        result = MysqlDigestSource().collect(mysql_executor([mysql_digest_row()]))
        assert result.needs_text == frozenset()
        assert result.texts == {"app:d1": "SELECT ?"}

    def test_null_digest_bucket_excluded_but_reported(self):
        result = MysqlDigestSource().collect(
            mysql_executor([mysql_digest_row()], null_count=42)
        )
        assert all(not row.engine_key.endswith(":None") for row in result.rows)
        assert result.capabilities["unattributed_executions"] == 42


DIGEST_TEXT = "SELECT * FROM `users` WHERE `id` = ?"
SAMPLE_TEXT = "SELECT * FROM `users` WHERE `id` = 42"


class TestMysqlSampleText:
    def sample_row(self, sample=SAMPLE_TEXT):
        return mysql_digest_row(text=DIGEST_TEXT, sample=sample)

    def test_sample_column_selected_when_capable(self):
        executor = mysql_executor([self.sample_row()], sample_capable=True)
        result = MysqlDigestSource().collect(executor)
        sweep = executor.sql_containing("events_statements_summary_by_digest WHERE")
        assert "QUERY_SAMPLE_TEXT" in sweep
        # Identity resolution stays on DIGEST_TEXT; the sample rides alongside.
        assert result.texts == {"app:d1": DIGEST_TEXT}
        assert result.sample_texts == {"app:d1": SAMPLE_TEXT}
        assert result.capabilities["query_sample_text"] is True

    def test_sample_column_omitted_without_capability(self):
        executor = mysql_executor([mysql_digest_row(text=DIGEST_TEXT)])
        result = MysqlDigestSource().collect(executor)
        sweep = executor.sql_containing("events_statements_summary_by_digest WHERE")
        assert "QUERY_SAMPLE_TEXT" not in sweep
        assert result.texts == {"app:d1": DIGEST_TEXT}
        assert result.sample_texts == {}
        assert result.capabilities["query_sample_text"] is False

    def test_incremental_fetch_also_selects_sample(self):
        executor = mysql_executor([self.sample_row()], sample_capable=True)
        cursor = {"last_seen": "2026-08-16 09:59:00.000000", "server_start": 1000.0}
        result = MysqlDigestSource().collect(executor, cursor_state=cursor)
        sweep = executor.sql_containing("LAST_SEEN >=")
        assert "QUERY_SAMPLE_TEXT" in sweep
        assert result.sample_texts == {"app:d1": SAMPLE_TEXT}

    def test_null_sample_is_omitted(self):
        executor = mysql_executor([self.sample_row(sample=None)], sample_capable=True)
        result = MysqlDigestSource().collect(executor)
        assert result.texts == {"app:d1": DIGEST_TEXT}
        assert result.sample_texts == {}

    def test_probe_runs_once_per_source(self):
        source = MysqlDigestSource()
        first = mysql_executor([self.sample_row()], sample_capable=True)
        source.collect(first)
        second = mysql_executor([self.sample_row()], sample_capable=True)
        source.collect(second)
        probes = [sql for sql, _ in second.calls if "information_schema.columns" in sql]
        assert probes == []

    def test_probe_failure_degrades_silently_and_is_retried(self):
        source = MysqlDigestSource()
        inner = mysql_executor([mysql_digest_row(text=DIGEST_TEXT)])

        def failing_executor(sql, params=None):
            if "information_schema.columns" in sql:
                raise RuntimeError("connection reset")
            return inner(sql, params)

        result = source.collect(failing_executor)
        sweep = inner.sql_containing("events_statements_summary_by_digest WHERE")
        assert "QUERY_SAMPLE_TEXT" not in sweep
        assert result.sample_texts == {}
        # The failed probe is not cached: a later cycle probes again and
        # picks the sample column up.
        retry = mysql_executor([self.sample_row()], sample_capable=True)
        second = source.collect(retry)
        assert second.sample_texts == {"app:d1": SAMPLE_TEXT}

    def test_identity_hashes_key_on_digest_text_not_the_sample(self):
        # Placeholder canonicalization makes the digest text and its own
        # literal-bearing sample one identity by design; identity still
        # derives from DIGEST_TEXT, so a sample of a different statement
        # never steers it.
        assert hash_sql(DIGEST_TEXT) == hash_sql(SAMPLE_TEXT)
        assert hash_sql(SAMPLE_TEXT) == hash_sql(
            "SELECT * FROM `users` WHERE `id` = 7"
        )
        assert hash_sql(DIGEST_TEXT) != hash_sql(
            "SELECT * FROM `users` WHERE `id` = ? AND `active` = ?"
        )


class TestActivitySample:
    """One bounded activity snapshot per cycle recovers parameter values
    (research Q7): row-capped, self-excluding, on its own short budget."""

    def test_pg_activity_sql_shape(self):
        assert PG_ACTIVITY_SAMPLE_SQL.startswith(RDST_OBSERVE_MARKER)
        assert "FROM pg_stat_activity" in PG_ACTIVITY_SAMPLE_SQL
        assert "state = 'active'" in PG_ACTIVITY_SAMPLE_SQL
        assert "pid != pg_backend_pid()" in PG_ACTIVITY_SAMPLE_SQL
        assert (
            "COALESCE(application_name, '') NOT LIKE 'rdst/%'"
            in PG_ACTIVITY_SAMPLE_SQL
        )
        assert "datname = current_database()" in PG_ACTIVITY_SAMPLE_SQL
        assert PG_ACTIVITY_SAMPLE_SQL.endswith(f"LIMIT {ACTIVITY_SAMPLE_LIMIT}")

    def test_pg_sample_runs_its_own_timeout_first_and_drops_null_rows(self):
        executor = FakeExecutor(
            [
                ("pg_stat_activity", [("SELECT * FROM t WHERE a = 1",), (None,)]),
                ("SET LOCAL", []),
            ]
        )
        texts = PgStatStatementsSource().activity_sample(executor)
        assert texts == ["SELECT * FROM t WHERE a = 1"]
        first_sql = executor.calls[0][0]
        assert first_sql == (
            f"SET LOCAL statement_timeout = {ACTIVITY_SAMPLE_TIMEOUT_MS}"
        )

    def test_mysql_activity_sql_shape(self):
        assert MYSQL_ACTIVITY_SAMPLE_SQL.startswith(RDST_OBSERVE_MARKER)
        assert "FROM information_schema.PROCESSLIST" in MYSQL_ACTIVITY_SAMPLE_SQL
        assert "INFO IS NOT NULL" in MYSQL_ACTIVITY_SAMPLE_SQL
        assert "COMMAND != 'Sleep'" in MYSQL_ACTIVITY_SAMPLE_SQL
        assert "ID != CONNECTION_ID()" in MYSQL_ACTIVITY_SAMPLE_SQL
        # The live top lane's RDST self-session exclusion, mirrored.
        assert (
            "performance_schema.session_account_connect_attrs"
            in MYSQL_ACTIVITY_SAMPLE_SQL
        )
        assert "ATTR_NAME = 'program_name'" in MYSQL_ACTIVITY_SAMPLE_SQL
        assert "ATTR_VALUE LIKE 'rdst/%'" in MYSQL_ACTIVITY_SAMPLE_SQL
        assert (
            f"MAX_EXECUTION_TIME({ACTIVITY_SAMPLE_TIMEOUT_MS})"
            in MYSQL_ACTIVITY_SAMPLE_SQL
        )
        assert MYSQL_ACTIVITY_SAMPLE_SQL.endswith(f"LIMIT {ACTIVITY_SAMPLE_LIMIT}")

    def test_mysql_sample_returns_info_texts(self):
        executor = FakeExecutor(
            [("PROCESSLIST", [("SELECT * FROM t WHERE a = 2",), ("",)])]
        )
        texts = MysqlDigestSource().activity_sample(executor)
        assert texts == ["SELECT * FROM t WHERE a = 2"]


class TestMysqlCapabilities:
    def test_populated(self):
        caps = MysqlDigestSource().collect(
            mysql_executor([mysql_digest_row()], digest_lost=3, digests_size=10000)
        ).capabilities
        assert caps["unattributed_executions"] == 17
        assert caps["digest_row_count"] == 1
        assert caps["digest_lost"] == 3
        assert caps["digests_size"] == 10000

    def test_status_failure_is_non_fatal(self):
        inner = mysql_executor([mysql_digest_row()])

        def failing_executor(sql, params=None):
            if "SHOW" in sql:
                raise RuntimeError("access denied")
            return inner(sql, params)

        caps = MysqlDigestSource().collect(failing_executor).capabilities
        assert caps["digest_lost"] is None
        assert caps["digests_size"] is None
        assert caps["unattributed_executions"] == 17
