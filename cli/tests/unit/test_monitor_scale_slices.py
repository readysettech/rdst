"""The collector reads a bounded slice of the engine's statements; history stays exact."""
import pytest

from features.query_registry import discovery, statement_stats
from shared.monitor.monitor_store import EVERYTHING_ELSE, MonitorStore
from shared.query_registry.observation_store import ObservationStore
from tests.unit.test_query_discovery import (
    FakeConnection, FakeCursor, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
)
from tests.unit.test_statement_stats import FakeExecutor, mysql_digest_row

HEAVY, ENTRANT = 3, 2


class EngineCursor(FakeCursor):
    """Answers the collector's slice and totals queries the way Postgres would."""

    def execute(self, sql, params=None):
        engine = self._connection
        engine.executed.append((sql, params))
        self.description = ('fake',)
        stats = engine.stats
        if 'WITH s AS MATERIALIZED' in sql:
            held = set(params[0])
            heavy = sorted(stats, key=lambda q: -stats[q][1])[:HEAVY]
            busy = sorted(stats, key=lambda q: -stats[q][0])[:ENTRANT]
            keep = held | set(heavy) | set(busy)
            engine.fetched.append(sorted(keep & set(stats)))
            self._rows = [pg_stat_row(q, calls=c, total=t) for q, (c, t) in stats.items()
                          if q in keep]
        elif 'pg_stat_statements(showtext := false)' in sql:
            self._rows = [(len(stats), sum(c for c, _ in stats.values()),
                           sum(t for _, t in stats.values()))]
        elif 'queryid = ANY(%s) AND dbid' in sql:
            self._rows = [pg_text_row(q, f'SELECT c{q} FROM scale_rows') for q in params[0]
                          if q in stats]
        elif 'pg_stat_database' in sql:
            self._rows = [(engine.now, 0, 0, 0, 0, 1, 100, 1.0)]
        else:
            self._rows = engine.rows_for(sql)


class Engine(FakeConnection):
    def __init__(self, stats):
        super().__init__(pg_rules(2000, []))
        self.stats = stats
        self.fetched = []
        self.now = 2000

    def cursor(self):
        return EngineCursor(self)

    def advance(self, now, **calls):
        self.now = now
        self.rules = pg_rules(now, [])
        for name, added in calls.items():
            query = int(name[1:])
            count, total = self.stats.get(query, (0, 0.0))
            self.stats[query] = (count + added, total + added * (100.0 if query < 4 else 0.1))


def _series(monitor, bucket):
    from features.query_registry.discovery import hash_sql
    keys = {hash_sql(f'SELECT c{q} FROM scale_rows'): q for q in range(1, 20)}
    rows = monitor.read('SELECT query_key,calls FROM stmt_series WHERE target=? AND res=60 '
                        'AND bucket_start=?', ('demo', bucket))
    return {keys.get(row['query_key'], row['query_key']): row['calls'] for row in rows}


@pytest.mark.asyncio
async def test_slice_keeps_held_queries_admits_hot_entrants_and_exact_totals(
        tmp_path, monkeypatch):
    monkeypatch.setattr(statement_stats, 'COLLECT_HEAVY_STATEMENTS', HEAVY)
    monkeypatch.setattr(statement_stats, 'COLLECT_ENTRANT_STATEMENTS', ENTRANT)
    monkeypatch.setattr(discovery, 'COLLECT_ENTRANT_STATEMENTS', ENTRANT)
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    monitor = MonitorStore()
    monitor.set_settings('demo', {'enabled': True})
    store = ObservationStore(tmp_path / 'cache.db')
    # Three heavy statements, two busy ones and a tail the slices never select.
    engine = Engine({q: (1000 if q in (4, 5) else 1, 1000.0 if q < 4 else 1.0)
                     for q in range(1, 11)})
    clock = [2000]
    collector = two_phase_collector(tmp_path, lambda target: (engine, 'postgresql'),
                                    store=store, local_clock=lambda: clock[0])
    try:
        await collector.collect_now()
        assert engine.fetched[0] == [1, 2, 3, 4, 5]

        # Every statement runs; the tail is never fetched but still counts in the totals.
        clock[0] = 2060
        engine.advance(2060, **{f'q{q}': q for q in range(1, 11)})
        await collector.collect_now()
        minute = _series(monitor, 2040)
        assert {q: minute[q] for q in range(1, 6)} == {q: q for q in range(1, 6)}
        assert minute[EVERYTHING_ELSE] == sum(range(6, 11))
        db = monitor.read('SELECT calls FROM db_series WHERE target=? AND res=60 '
                          'AND bucket_start=2040', ('demo',))
        assert db[0]['calls'] == sum(range(1, 11))

        # q1 falls out of the heavy and busy slices but stays held; q11 turns hot.
        clock[0] = 2120
        engine.stats.update({6: (5000, 9e6), 7: (5000, 9e6), 8: (5000, 9e6)})
        engine.advance(2120, q1=7, q11=50000)
        await collector.collect_now()
        assert 1 in engine.fetched[-1] and 11 in engine.fetched[-1]
        minute = _series(monitor, 2100)
        assert minute[1] == 7
        assert 11 not in minute
        db = monitor.read('SELECT calls FROM db_series WHERE target=? AND res=60 '
                          'AND bucket_start=2100', ('demo',))
        # q6..q8 jumped to 5,000 calls, so the engine total is their jump plus q1 and q11.
        assert db[0]['calls'] == 7 + 50000 + sum(5000 - (1 + q) for q in (6, 7, 8))

        # The entrant now has a pair and records its own minute.
        clock[0] = 2180
        engine.advance(2180, q1=1, q11=900)
        await collector.collect_now()
        minute = _series(monitor, 2160)
        assert minute[11] == 900 and minute[1] == 1
    finally:
        await collector.close()
        store.close()


def test_postgres_slice_asks_the_engine_for_held_queryids():
    executor = FakeExecutor([('server_version_num', [(150002,)]),
                             ('pg_stat_statements_info', [(2000.0, 1111.0, 555.0, 0)]),
                             ('WITH s AS MATERIALIZED', []), ('SET LOCAL', [])])
    statement_stats.PgStatStatementsSource().collect(
        executor, tracked_keys=['10:16384:-42:t', '10:16384:7:f', '11:16384:7:t'])
    assert executor.params_for('WITH s AS MATERIALIZED') == ([-42, 7],)


def test_mysql_slice_reads_the_heaviest_and_most_recent_changed_digests():
    rule = 'FROM performance_schema.events_statements_summary_by_digest'
    executor = FakeExecutor([('SELECT NOW(6)', [('2026-08-16 10:00:00.000000',)]),
                             ('UNIX_TIMESTAMP', [(1000.0,)]),
                             ('SUM(COUNT_STAR)', [(9, 120, 4e12)]),
                             ('DIGEST IS NULL', [(0,)]), ('COUNT(*)', [(9,)]),
                             (rule, [mysql_digest_row()])])
    cursor = {'last_seen': '2026-08-16 09:59:00.000000', 'server_start': 1000.0}
    result = statement_stats.MysqlDigestSource().collect(
        executor, cursor_state=cursor, tracked_keys=['app:d1', 'app:d2'])
    sql = executor.sql_containing('UNION')
    assert sql.startswith(statement_stats.RDST_OBSERVE_MARKER)
    assert sql.count('LAST_SEEN >= %s') == 2
    assert 'DIGEST IN' not in sql
    assert f'ORDER BY SUM_TIMER_WAIT DESC LIMIT {statement_stats.COLLECT_HEAVY_STATEMENTS}' in sql
    assert f'ORDER BY LAST_SEEN DESC LIMIT {statement_stats.COLLECT_ENTRANT_STATEMENTS}' in sql
    assert executor.params_for('UNION') == (cursor['last_seen'],) * 2
    # Totals cover the whole table, summed in the engine.
    assert result.totals == (9, 120, pytest.approx(4000.0))


def test_engine_totals_are_not_differenced_across_evictions(tmp_path):
    collector = two_phase_collector(tmp_path, lambda target: (None, 'postgresql'))

    def result(totals, dealloc, epoch='e'):
        return statement_stats.CollectResult(
            rows=[], epoch_id=epoch, capabilities={'dealloc': dealloc}, next_cursor_state=None,
            needs_text=frozenset(), texts={}, incremental=False, totals=totals)

    assert collector._engine_total_delta(result((5, 100, 10.0), 0)) is None
    assert collector._engine_total_delta(result((5, 130, 12.5), 0)) == (30, 2.5)
    # An eviction drops counts from the view, so the difference would undercount.
    assert collector._engine_total_delta(result((4, 131, 12.6), 1)) is None
    assert collector._engine_total_delta(result((4, 140, 13.6), 1)) == (9, pytest.approx(1.0))
    assert collector._engine_total_delta(result((4, 150, 14.0), 1, epoch='f')) is None
