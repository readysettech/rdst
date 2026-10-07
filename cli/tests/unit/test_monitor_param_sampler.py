"""Frequent parameter capture for top-load queries, and planning with missing values."""

from __future__ import annotations

from features.monitor import param_sampler
from features.monitor.analyze_queue import apply_suggestions, automatic_skip_reason
from features.monitor.param_sampler import ParamSampler, usable
from shared.monitor.monitor_store import SAMPLES_PER_QUERY, MonitorStore

NOW = 1_000_800


class Cursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class Connection:
    def __init__(self, rows):
        self.cursor_ = Cursor(rows)

    def cursor(self):
        return self.cursor_

    def rollback(self):
        self.ended = "rollback"

    def commit(self):
        self.ended = "commit"


class Collector:
    _engine = "postgresql"

    def __init__(self, aliases):
        self._alias_map = aliases


def _store(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True})
    rows = [{"query_key": key, "calls": 10, "total_ms": ms}
            for key, ms in (("aaaaaaaaaaa1", 100.0), ("bbbbbbbbbbb2", 50.0),
                            ("ccccccccccc3", 30.0))]
    store.record("db", NOW - 60, rows, {"aaaaaaaaaaa1": "SELECT * FROM orders WHERE id = $1",
                                         "bbbbbbbbbbb2": "SELECT * FROM users WHERE id = $1",
                                         "ccccccccccc3": "DROP INDEX i"}, [])
    return store


def test_only_whole_literal_texts_are_kept():
    assert usable("SELECT * FROM orders WHERE id = 7")
    assert not usable("SELECT * FROM orders WHERE id = $1")
    assert not usable("SELECT " + "x, " * 400 + "1")


def test_sampler_polls_only_for_top_queries_lacking_samples(tmp_path, monkeypatch):
    store = _store(tmp_path)
    for index in range(SAMPLES_PER_QUERY):
        store.record_sample("db", "bbbbbbbbbbb2", f"SELECT * FROM users WHERE id = {index}", NOW)
    sampler = ParamSampler(store, clock=lambda: NOW, interval_s=3)
    # Full samples and DDL are not wanted.
    assert sampler.wanted_keys("db") == {"aaaaaaaaaaa1"}
    from features.query_registry import discovery

    monkeypatch.setattr(discovery.query_discovery, "collector_for", lambda target: Collector({
        "10:20:111:t": "aaaaaaaaaaa1", "10:20:222:t": "bbbbbbbbbbb2"}))
    connection = Connection([(111, "SELECT * FROM orders WHERE id = 42"),
                             (111, "SELECT * FROM orders WHERE id = $1")])
    monkeypatch.setattr(sampler, "_connection", lambda target: connection)
    assert sampler.tick() is True
    sql, params = connection.cursor_.executed[-1]
    assert "pg_stat_activity" in sql and params == ([111],)
    assert "LIMIT 200" in sql
    assert store.read("SELECT query_key,text FROM query_sample WHERE query_key='aaaaaaaaaaa1'") \
        == [{"query_key": "aaaaaaaaaaa1", "text": "SELECT * FROM orders WHERE id = 42"}]
    # The read ends with COMMIT so the sampler never shows up in the rollback rate.
    assert connection.ended == "commit"


def test_mysql_reads_statement_history_by_digest():
    cursor = Cursor([("shop", "d1", "SELECT * FROM t WHERE id = 5")])
    rows = ParamSampler._read_mysql(cursor, {"shop:d1": "k1"})
    assert rows[0] == ("k1", "SELECT * FROM t WHERE id = 5")
    assert "events_statements_history_long" in cursor.executed[0][0]
    assert "MAX_EXECUTION_TIME(500)" in cursor.executed[0][0]
    assert "LIMIT 200" in cursor.executed[0][0]
    assert len(cursor.executed) == 1


def test_mysql_sampling_reads_dict_rows_without_a_rollback(tmp_path, monkeypatch):
    """The MySQL target connection is autocommit with dict rows; a ROLLBACK per poll
    would count in Com_rollback, the rollback rate RDST charts."""
    store = _store(tmp_path)
    sampler = ParamSampler(store, clock=lambda: NOW, interval_s=3)
    from features.query_registry import discovery

    class MysqlCollector(Collector):
        _engine = "mysql"

    monkeypatch.setattr(discovery.query_discovery, "collector_for", lambda target: MysqlCollector(
        {"shop:d1": "aaaaaaaaaaa1"}))
    connection = Connection([{"CURRENT_SCHEMA": "shop", "DIGEST": "d1",
                              "SQL_TEXT": "SELECT * FROM orders WHERE id = 42"}])
    monkeypatch.setattr(sampler, "_connection", lambda target: connection)
    assert sampler.tick() is True
    assert store.read("SELECT text FROM query_sample WHERE query_key='aaaaaaaaaaa1'") == [
        {"text": "SELECT * FROM orders WHERE id = 42"}]
    assert not hasattr(connection, "ended")


def test_interval_placeholders_keep_a_placeholder_a_generic_plan_reads():
    """An unsampled `interval $2` becomes `$2::interval`: planned generically, never filled."""
    from features.analyze.parameter_suggestions import suggest_parameter_values
    from features.analyze import parameter_suggestions

    parameter_suggestions_connect = parameter_suggestions._connect
    parameter_suggestions._connect = lambda *args: None
    try:
        sql = "SELECT 1 FROM o WHERE placed_at > now() - interval $2 AND n = $1"
        planned, _ = apply_suggestions(sql, suggest_parameter_values(
            sql, "t", {"engine": "postgresql"}))
    finally:
        parameter_suggestions._connect = parameter_suggestions_connect
    assert "now() - $2::interval AND n = $1" in planned and "7 days" not in planned


def test_automatic_skips_follow_the_analyzability_rules():
    assert automatic_skip_reason('ANALYZE "s".orders') == (
        "ANALYZE is a maintenance command, so there's no query plan to analyze.")
    assert automatic_skip_reason("INSERT INTO t VALUES (1)") == \
        "INSERT changes data, so RDST doesn't run it."
    assert automatic_skip_reason("SELECT * FROM orders") is None


def test_mysql_locking_reads_and_lock_functions_are_never_run():
    """A backticked MySQL statement defeats the postgres parse; the lexical check still has
    to see MySQL's locking-read spelling, and named locks are a side effect."""
    assert automatic_skip_reason("SELECT * FROM `t` WHERE `id` = 1 LOCK IN SHARE MODE")
    assert automatic_skip_reason("SELECT * FROM `t` WHERE `id` = 1 FOR UPDATE")
    assert automatic_skip_reason("SELECT GET_LOCK('job', 10)")
    assert automatic_skip_reason("SELECT * FROM `orders` WHERE `id` = ?") is None


def test_mysql_interval_placeholder_is_never_given_a_made_up_number():
    from features.analyze import parameter_suggestions
    from features.analyze.parameter_suggestions import suggest_parameter_values

    connect = parameter_suggestions._connect
    parameter_suggestions._connect = lambda *args: None
    try:
        sql = "SELECT COUNT(*) FROM orders WHERE created_at > NOW() - INTERVAL ? DAY"
        planned, source = apply_suggestions(sql, suggest_parameter_values(
            sql, "t", {"engine": "mysql"}))
    finally:
        parameter_suggestions._connect = connect
    assert planned == sql and source == "none"


def test_selects_that_change_state_are_not_analyzed_automatically():
    from features.monitor.analyze_queue import automatic_skip_reason

    setup = ("SELECT key, pg_catalog.set_config(key, val, $1) as val "
             "FROM (VALUES ($2, ($3)::pg_catalog.text)) as v(key, val)")
    assert "set_config" in automatic_skip_reason(setup)
    assert "nextval" in automatic_skip_reason("SELECT nextval('orders_id_seq')")
    assert automatic_skip_reason("SELECT id FROM orders WHERE customer_id = $1") is None
