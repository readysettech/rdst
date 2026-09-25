"""A collection cycle's work stays bounded by the tracked set, not by the engine or Library."""
import asyncio

import pytest

from features.query_registry import discovery
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry import QueryRegistry, hash_sql
from shared.query_registry.delta_processor import CounterRow, DeltaResult
from shared.query_registry.library_store import LibraryStore
from shared.query_registry.observation_store import ObservationStore
from tests.unit.test_query_discovery import (
    FakeConnection, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
)


def _snapshot_counts(store):
    with store._read() as conn:
        return dict(conn.execute(
            "SELECT captured_at, COUNT(*) FROM counter_snapshot GROUP BY captured_at").fetchall())


@pytest.mark.asyncio
async def test_wide_sweep_checkpoints_only_moved_baseline_rows(tmp_path, monkeypatch):
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    MonitorStore().set_settings('demo', {'enabled': True})
    store = ObservationStore(tmp_path / 'cache.db')
    rows = [pg_stat_row(index, calls=1, total=1) for index in range(12000)]
    connection = FakeConnection(pg_rules(2000, rows, [
        pg_text_row(index, f'SELECT c{index} FROM scale_rows') for index in range(12000)]))
    clock = [2000]
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store, local_clock=lambda: clock[0])
    try:
        for _ in range(2):
            await collector.collect_now()
            assert max(_snapshot_counts(store).values()) <= (
                discovery.SERIES_QUERIES + discovery.COLLECT_ENTRANT_STATEMENTS)
            clock[0] += 60
            connection.rules = pg_rules(clock[0], rows)
        before = _snapshot_counts(store)
        moved = sorted(collector._baseline)[0]
        index = int(moved.split(':')[2])
        rows[index] = pg_stat_row(index, calls=9, total=9)
        connection.rules = pg_rules(clock[0], rows)
        await collector.collect_now()
        latest = {at: count for at, count in _snapshot_counts(store).items() if at not in before}
        # One moved row is one checkpoint row, whatever the width of the native sweep.
        assert list(latest.values()) == [1]
        restored = {row['engine_key']: row['calls']
                    for row in store.latest_counter_snapshots('demo', collector._baseline_epoch)}
        assert restored[moved] == 9
        assert set(collector._baseline) <= set(restored)
    finally:
        await collector.close()
        store.close()


@pytest.mark.asyncio
async def test_hashing_and_diffing_a_sweep_stay_off_the_event_loop(tmp_path, monkeypatch):
    store = ObservationStore(tmp_path / 'cache.db')
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                          [pg_text_row(111, 'SELECT * FROM users')]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'), store=store)
    original_hash, original_diff = discovery.hash_sql, discovery.process_snapshot
    calls = []

    def off_loop(function):
        def run(*args, **kwargs):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                calls.append(function.__name__)
                return function(*args, **kwargs)
            pytest.fail(f'{function.__name__} over a full sweep blocks API requests')
        return run

    monkeypatch.setattr(discovery, 'hash_sql', off_loop(original_hash))
    monkeypatch.setattr(discovery, 'process_snapshot', off_loop(original_diff))
    try:
        await collector.collect_now()
        connection.rules = pg_rules(2060, [pg_stat_row(111, calls=7, total=140)])
        await collector.collect_now()
        assert {'hash_sql', 'process_snapshot'} <= set(calls)
    finally:
        await collector.close()
        store.close()


@pytest.mark.asyncio
async def test_cycle_never_decodes_the_whole_library(tmp_path, monkeypatch):
    path = str(tmp_path / 'queries.toml')
    seed = QueryRegistry(registry_path=path)
    with seed.defer_save():
        for index in range(50):
            seed.add_query(sql=f'SELECT c{index} FROM other_table', target='other')
    store = ObservationStore(tmp_path / 'cache.db')
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                          [pg_text_row(111, 'SELECT * FROM users')]))
    registry = QueryRegistry(registry_path=path)
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store, registry=registry)

    def whole_library(*args, **kwargs):
        pytest.fail('A collection cycle decoded every Library entry')

    monkeypatch.setattr(LibraryStore, 'load_all', whole_library)
    monkeypatch.setattr(LibraryStore, '_load_all', whole_library)
    try:
        event = await collector.collect_now()
        assert event.event == 'discovery_update'
        assert len(event.data['new_hashes']) == 1
    finally:
        await collector.close()
        store.close()
    monkeypatch.undo()
    entries = QueryRegistry(registry_path=path).list_queries(limit=None)
    assert len(entries) == 51
    added = [entry for entry in entries if entry.lifecycle_for('demo')]
    assert [entry.original_sql for entry in added] == ['SELECT * FROM users']


def test_scoped_registry_merges_only_its_own_changes(tmp_path):
    path = str(tmp_path / 'queries.toml')
    seed = QueryRegistry(registry_path=path)
    first, _ = seed.add_query(sql='SELECT a FROM t', target='demo')
    second, _ = seed.add_query(sql='SELECT b FROM t', target='demo')
    scoped = QueryRegistry(registry_path=path)
    scoped.load_keys([first])
    assert set(scoped._queries) == {first}
    # Another writer changes an identity the scoped registry never loaded.
    other = QueryRegistry(registry_path=path)
    other.update_query_tag(second, 'theirs')
    with scoped.defer_save():
        scoped.add_query(sql='SELECT a FROM t', target='demo', frequency=7)
        scoped.add_query(sql='SELECT c FROM t', target='demo')
    # An exact hash outside the scope still resolves, from storage.
    assert scoped.get_query(second).tag == 'theirs'
    entries = {entry.hash: entry for entry in QueryRegistry(registry_path=path).list_queries()}
    assert len(entries) == 3
    assert entries[first].frequency == 7
    assert entries[second].tag == 'theirs'
    # Whole-catalog reads see everything, not the scope.
    assert len(scoped.list_queries()) == 3


def test_observed_hashes_reads_one_targets_lifecycle(tmp_path):
    registry = QueryRegistry(registry_path=str(tmp_path / 'queries.toml'))
    seen, _ = registry.add_query(sql='SELECT a FROM t', target='demo', observed=True)
    registry.add_query(sql='SELECT b FROM t', target='demo')
    registry.add_query(sql='SELECT c FROM t', target='other', observed=True)
    assert QueryRegistry(registry_path=str(tmp_path / 'queries.toml')).observed_hashes(
        'demo') == {seen}


def test_counter_rows_carry_no_instance_dictionary():
    row = CounterRow('k', 1, 1, 1.0, 1.0, 1.0, 1.0, 0.0, 'e')
    delta = DeltaResult('k', 0.0, 1.0, 'complete', 1, 1.0, 1.0, 1)
    assert not hasattr(row, '__dict__') and not hasattr(delta, '__dict__')


def test_recent_observation_retention_takes_few_transactions(tmp_path, monkeypatch):
    store = ObservationStore(tmp_path / 'cache.db')
    store.record_recent_observations('demo', [
        {'normalized_hash': f'h{index:05d}', 'window_start': start, 'window_end': start + 60,
         'calls_delta': 1, 'exec_time_delta': 1.0, 'approximate_qps': 1.0,
         'completeness': 'complete'}
        for index in range(3000) for start in (0, 1000)])
    original = store._write
    transactions = []

    def counted():
        transactions.append(1)
        return original()

    monkeypatch.setattr(store, '_write', counted)
    try:
        assert store.prune_observations(500, chunk=2000) == 3000
        # Proportional to deleted rows over the chunk size, never to the identity count.
        assert len(transactions) <= 3
        with store._read() as conn:
            assert conn.execute('SELECT COUNT(*) FROM recent_observation').fetchone()[0] == 3000
    finally:
        store.close()


def test_monitor_read_outlasts_a_long_writer_then_gives_up(tmp_path, monkeypatch):
    import sqlite3

    from shared.monitor import monitor_store

    store = MonitorStore(tmp_path / 'monitor.db')
    store.ensure_open()
    original = MonitorStore._read_once
    failures = [2]

    def contended(self, sql, params):
        if failures[0]:
            failures[0] -= 1
            raise sqlite3.OperationalError('database is locked')
        return original(self, sql, params)

    monkeypatch.setattr(MonitorStore, '_read_once', contended)
    assert store.read('SELECT 1 AS one') == [{'one': 1}]
    failures[0] = 10**6
    monkeypatch.setattr(monitor_store, 'READ_RETRY_S', 0.3)
    with pytest.raises(sqlite3.OperationalError):
        store.read('SELECT 1 AS one')


def test_api_reports_storage_lock_as_retryable_503():
    import sqlite3

    from fastapi import FastAPI

    from shared.api.app import register_error_handlers
    from tests.unit.conftest import _ASGITestClient

    app = FastAPI()
    register_error_handlers(app)

    @app.get('/locked')
    def locked():
        raise sqlite3.OperationalError('database is locked')

    @app.get('/broken')
    def broken():
        raise sqlite3.OperationalError('no such table: missing')

    client = _ASGITestClient(app)
    try:
        response = client.get('/locked')
        assert response.status_code == 503
        assert response.json()['code'] == 'storage_busy'
        assert response.headers['retry-after'] == '2'
        assert client.get('/broken').status_code == 500
    finally:
        client.close()


def test_web_server_caps_malloc_arenas_unless_the_operator_chose(monkeypatch):
    import sys

    from shared import runtime_memory

    monkeypatch.setenv('MALLOC_ARENA_MAX', '8')
    assert runtime_memory.limit_malloc_arenas() is False
    monkeypatch.delenv('MALLOC_ARENA_MAX')
    assert runtime_memory.limit_malloc_arenas() is sys.platform.startswith('linux')


def test_scoped_registry_looks_up_each_new_identity_once(tmp_path, monkeypatch):
    path = str(tmp_path / 'queries.toml')
    QueryRegistry(registry_path=path).add_query(sql='SELECT a FROM t', target='demo')
    scoped = QueryRegistry(registry_path=path)
    new_hash = hash_sql('SELECT z FROM t')
    scoped.load_keys([new_hash])
    lookups = []
    original = LibraryStore.load_keys

    def counted(self, keys):
        lookups.append(list(keys))
        return original(self, keys)

    monkeypatch.setattr(LibraryStore, 'load_keys', counted)
    with scoped.defer_save():
        assert scoped.add_query(sql='SELECT z FROM t', target='demo')[0] == new_hash
    assert lookups == []


def _family(count, offset=0):
    return {f'k{index:05d}': f'SELECT c{index % 7}, c{index % 11} FROM scale_rows '
            f'WHERE id = {index} ORDER BY c{index % 5}' for index in range(offset, offset + count)}


def test_naming_reparses_only_new_family_members_and_outside_the_write_lock(
        tmp_path, monkeypatch):
    from features.monitor import naming
    from shared.monitor import statements

    store = MonitorStore(tmp_path / 'monitor.db')
    store.record('db', 60, [], _family(6000), [])
    naming.target_names(store, 'db')
    store.record('db', 120, [], _family(20, offset=6000), [])
    original = naming.distinct_names
    held = []

    def watched(*args, **kwargs):
        held.append(store._lock._is_owned())
        return original(*args, **kwargs)

    monkeypatch.setattr(naming, 'distinct_names', watched)
    statements._name_details.cache_clear()
    names = naming.target_names(store, 'db')
    cold = statements._name_details.cache_info().misses
    store.record('db', 180, [], _family(20, offset=6020), [])
    before = statements._name_details.cache_info().misses
    naming.target_names(store, 'db')
    # A family larger than the old 4,096-entry cache stays cached between allocations.
    assert statements._name_details.cache_info().misses - before <= 20 < cold
    assert held == [False, False]
    assert len(set(names.values())) == len(names) == 6020


def test_collector_names_new_queries_before_pages_ask(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    MonitorStore().set_settings('demo', {'enabled': True})
    store = ObservationStore(tmp_path / 'cache.db')
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                          [pg_text_row(111, 'SELECT * FROM users')]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store, local_clock=lambda: 2000)
    try:
        await_result = asyncio.run(collector.collect_now())
        assert await_result.event == 'discovery_update'
    finally:
        asyncio.run(collector.close())
        store.close()
    saved = MonitorStore().read("SELECT value FROM monitor_setting WHERE target='demo' "
                                "AND key='query_display_names_v2'")
    assert list(json.loads(saved[0]['value']).values()) == ['SELECT on users']


@pytest.mark.asyncio
async def test_a_wide_new_engine_is_resolved_heaviest_first_over_several_cycles(
        tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, 'IDENTITY_RESOLUTION_BATCH', 100)
    store = ObservationStore(tmp_path / 'cache.db')
    rows = [pg_stat_row(index, calls=1, total=float(index)) for index in range(250)]
    connection = FakeConnection(pg_rules(2000, rows, [
        pg_text_row(index, f'SELECT c{index} FROM scale_rows') for index in range(250)]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store)
    hashed = []
    original = discovery.hash_sql
    monkeypatch.setattr(discovery, 'hash_sql', lambda text: hashed.append(text) or original(text))
    try:
        await collector.collect_now()
        assert len(hashed) == 100
        assert set(hashed) == {f'SELECT c{index} FROM scale_rows' for index in range(150, 250)}
        connection.rules = pg_rules(2060, rows, [
            pg_text_row(index, f'SELECT c{index} FROM scale_rows') for index in range(250)])
        await collector.collect_now()
        await collector.collect_now()
        assert len(collector._alias_map) == 250
    finally:
        await collector.close()
        store.close()


@pytest.mark.parametrize('query', ['?limit=500&offset=0&target=demo', '?target=demo&sort=newest'])
def test_query_library_listing_decodes_off_the_event_loop(tmp_path, monkeypatch, query):
    from fastapi import FastAPI

    from features.query_registry.api import routes
    from tests.unit.conftest import _ASGITestClient

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    QueryRegistry().add_query(sql='SELECT a FROM t', target='demo', observed=True)
    original = QueryRegistry.load
    loops = []

    def load(self):
        try:
            asyncio.get_running_loop()
            loops.append(True)
        except RuntimeError:
            loops.append(False)
        return original(self)

    monkeypatch.setattr(QueryRegistry, 'load', load)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api')
    client = _ASGITestClient(app)
    try:
        response = client.get('/api/query-registry' + query)
        assert response.status_code == 200
    finally:
        client.close()
    assert True not in loops


def test_target_listing_pages_in_sqlite_with_the_full_scan_order(tmp_path, monkeypatch):
    from features.query_registry.api import routes

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    registry = QueryRegistry()
    with registry.defer_save():
        for index in range(30):
            registry.add_query(sql=f'SELECT c{index} FROM t',
                               target='demo' if index % 3 else 'other')
    registry.add_query(sql='SELECT c4 FROM t', target='other', observed=True)
    expected = [entry.hash for entry in routes._target_scoped_queries('demo')]

    def whole_library(*args, **kwargs):
        pytest.fail('A listing page decoded every Library entry')

    monkeypatch.setattr(LibraryStore, 'load_all', whole_library)
    page, total = routes._target_page('demo', 7, 3)
    assert total == len(expected)
    assert [entry.hash for entry in page] == expected[3:10]
    assert [entry.hash for entry in routes._target_page('demo', None, 0)[0]] == expected


@pytest.mark.asyncio
async def test_slow_hashing_resolves_the_heaviest_texts_within_the_cycle_budget(
        tmp_path, monkeypatch):
    # A frozen build on a slow machine hashes slowly; thousands of new texts must not hold
    # one cycle past its deadline. The heaviest resolve first, the rest on later cycles.
    store = ObservationStore(tmp_path / 'cache.db')
    rows = [pg_stat_row(index, calls=1, total=index) for index in range(1, 201)]
    connection = FakeConnection(pg_rules(2000, rows, [
        pg_text_row(index, f'SELECT c{index} FROM slow_rows') for index in range(1, 201)]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store)
    now = [0.0]
    original_hash = discovery.hash_sql

    def slow_hash(text):
        now[0] += 0.1
        return original_hash(text)

    monkeypatch.setattr(discovery.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(discovery, 'hash_sql', slow_hash)
    monkeypatch.setattr(discovery, 'IDENTITY_RESOLUTION_BUDGET_SECONDS', 1.0)
    try:
        await collector.collect_now()
        resolved = set(collector._alias_map)
        assert 0 < len(resolved) <= 11
        # Totals run 1..200, so the resolved keys are the heaviest ones.
        assert min(int(key.split(':')[2]) for key in resolved) > 180
    finally:
        await collector.close()
        store.close()
