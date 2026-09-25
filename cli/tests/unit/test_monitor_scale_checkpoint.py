"""Counter checkpoints keep the API event loop free and precede monitor history."""
import asyncio

import pytest

from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.observation_store import ObservationStore
from tests.unit.test_query_discovery import (
    FakeConnection, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
)


@pytest.mark.asyncio
async def test_checkpoint_can_wait_on_sqlite_without_blocking_the_api_loop(tmp_path, monkeypatch):
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    MonitorStore().set_settings('demo', {'enabled': True})
    store = ObservationStore(tmp_path / 'cache.db')
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                          [pg_text_row(111, 'SELECT * FROM users')]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store, local_clock=lambda: 2000)
    checkpoint = collector._record_counter_rows
    monitor = collector._record_monitor_cycle
    order = []

    def save_checkpoint(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail('A SQLite checkpoint can block the API event loop')
        checkpoint(*args, **kwargs)
        order.append('checkpoint')

    def record_monitor(*args, **kwargs):
        rows = store.latest_counter_snapshots('demo', collector._baseline_epoch)
        assert rows[0]['calls'] == 5
        assert order == ['checkpoint']
        order.append('monitor')
        monitor(*args, **kwargs)

    monkeypatch.setattr(collector, '_record_counter_rows', save_checkpoint)
    monkeypatch.setattr(collector, '_record_monitor_cycle', record_monitor)
    try:
        event = await collector.collect_now()
        assert event.event == 'discovery_update'
        assert order == ['checkpoint', 'monitor']
    finally:
        await collector.close()
        store.close()


@pytest.mark.asyncio
async def test_wide_sweep_keeps_retained_hot_query_deltas(tmp_path, monkeypatch):
    from shared.query_registry.delta_processor import compute_pg_epoch_id

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    monitor = MonitorStore()
    monitor.set_settings('demo', {'enabled': True})
    monitor.record('demo', 2000, [],
                   {f'h{index:05d}': 'SELECT * FROM scale_rows' for index in range(10000)}, [])
    store = ObservationStore(tmp_path / 'cache.db')
    epoch = compute_pg_epoch_id(1111.0, 555.0)
    aliases = {f'10:16384:{index}:t': f'h{index:05d}' for index in range(10001)}
    store.record_identity_aliases('demo', epoch, aliases)
    rows = [pg_stat_row(index, calls=1, total=1) for index in range(10001)]
    connection = FakeConnection(pg_rules(2000, rows))
    clock = [2000]
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store, local_clock=lambda: clock[0])
    try:
        await collector.collect_now()
        clock[0] = 2060
        rows[0] = pg_stat_row(0, calls=21, total=21)
        connection.rules = pg_rules(clock[0], rows)
        await collector.collect_now()
        # Every tracked query keeps its pair; the rest is bounded newcomer headroom.
        assert len(collector._baseline) == 10001
        assert len(monitor.read('SELECT query_key FROM query_text WHERE target=?', ('demo',))) == 10000
        recorded = monitor.read('SELECT SUM(calls) n FROM stmt_series WHERE target=? AND query_key=?',
                                ('demo', 'h00000'))
        assert recorded[0]['n'] == 20
    finally:
        await collector.close()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['_record_identity_aliases', '_record_recent_observations',
                                     '_record_collector_state'])
async def test_cache_evidence_writes_keep_the_api_loop_free(tmp_path, monkeypatch, method):
    store = ObservationStore(tmp_path / 'cache.db')
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                          [pg_text_row(111, 'SELECT * FROM users')]))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'), store=store)
    original = getattr(collector, method)
    writes = []

    def write_evidence(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            pytest.fail('Cache evidence can wait on a writer while the API loop serves requests')
        original(*args, **kwargs)
        writes.append(method)

    monkeypatch.setattr(collector, method, write_evidence)
    try:
        event = await collector.collect_now()
        assert event.event == 'discovery_update'
        assert writes == [method]
        assert store.get_collector_state('demo')['state'] == 'watching'
    finally:
        await collector.close()
        store.close()
