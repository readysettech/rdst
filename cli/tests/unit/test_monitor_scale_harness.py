"""Scale workload identities and default execution safety."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from shared.query_registry.query_registry import normalize_sql

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'monitor_scale.py'
SPEC = importlib.util.spec_from_file_location('monitor_scale_harness', SCRIPT)
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_default_invocation_skips_without_credentials(monkeypatch, capsys):
    monkeypatch.setattr(harness.sys, 'argv', [str(SCRIPT)])
    run = Mock(side_effect=AssertionError('Default execution reached managed databases'))
    monkeypatch.setattr(harness, 'run', run)
    assert harness.main() == 0
    run.assert_not_called()
    assert 'SKIP' in capsys.readouterr().out


@pytest.mark.parametrize('dialect', ['postgres', 'mysql'])
def test_query_shapes_survive_normalization(dialect):
    # Power-of-two edges exercise column-set transitions through the entire range.
    indices = set(range(1024)) | {9999, 10000, 99999}
    indices |= {edge + delta for edge in (2048, 4096, 8192, 16384, 32768, 65536)
                for delta in (-1, 0, 1)}
    normalized = {normalize_sql(harness.statement(index), dialect=dialect) for index in indices}
    assert len(normalized) == len(indices)


def test_hundred_thousand_shapes_have_distinct_column_sets():
    projections = {harness.statement(index).split(' FROM ')[0] for index in range(100000)}
    assert len(projections) == 100000


@pytest.mark.parametrize('index', [-1, 100000])
def test_query_shape_bounds(index):
    with pytest.raises(ValueError):
        harness.statement(index)


@pytest.mark.parametrize('count', [999, 1000, 1001, 9999, 10000, 10001])
def test_scale_policy_boundaries(count, tmp_path):
    from shared.monitor.monitor_store import EVERYTHING_ELSE, MonitorStore

    now = 100 * 86400
    store = MonitorStore(tmp_path / 'monitor.db', clock=lambda: now)
    statements = [{'query_key': f'{index:012x}', 'calls': 1, 'total_ms': count-index}
                  for index in range(count)]
    store.record('db', now, statements, {}, [])
    assert len(store.full_detail_keys('db')) == min(count, 1000)
    assert store.read('SELECT count(*) n FROM query_tier')[0]['n'] == min(count, 10000)
    store.rollup('db', now + 300)
    extra = int(count > 10000)
    assert store.read('SELECT count(*) n FROM stmt_series WHERE res=60')[0]['n'] == min(count, 1000) + extra
    assert store.read('SELECT count(*) n FROM stmt_series WHERE res=300')[0]['n'] == min(count, 10000) + extra
    rolled = store.read('SELECT sum(calls) n FROM stmt_series WHERE res=300')[0]['n']
    assert rolled == count
    if extra:
        assert store.read('SELECT calls FROM stmt_series WHERE query_key=? AND res=300',
                          (EVERYTHING_ELSE,))[0]['calls'] == count-10000


@pytest.mark.asyncio
async def test_monitored_collection_keeps_its_cadence(monkeypatch):
    from tests.unit.test_observation_scheduler import _monitored_scheduler, run_tick

    scheduler, coordinator, clock = _monitored_scheduler({'orders'})
    collector = coordinator.collector_for('orders')
    collector.db_touch_ms = 1000
    schedule = scheduler._schedules['orders']
    await run_tick(scheduler, schedule, clock)
    assert 0 < schedule.next_due-clock.now <= 60
    assert scheduler._next_interval(300, 1) == 300
    # The customer database duty limit still stretches a slow local read.
    assert scheduler._next_interval(60, 4) == 80


@pytest.mark.asyncio
async def test_library_work_and_local_history_advance_together(tmp_path, monkeypatch):
    from shared.monitor.monitor_store import MonitorStore
    from shared.query_registry.query_registry import QueryRegistry
    from tests.unit.test_query_discovery import (
        FakeConnection, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
    )

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    now = [2000]
    monitor = MonitorStore()
    monitor.set_settings('demo', {'enabled': True})
    registry = QueryRegistry(registry_path=str(tmp_path / 'queries.toml'))
    connection = FakeConnection(pg_rules(
        now[0], [pg_stat_row(111, calls=5, total=100)],
        [pg_text_row(111, 'SELECT * FROM users')],
    ))
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    registry=registry, local_clock=lambda: now[0])
    try:
        first = await collector.collect_now()
        assert len(first.data['new_hashes']) == 1
        now[0] += 60
        connection.rules = pg_rules(
            now[0], [pg_stat_row(111, calls=10, total=200), pg_stat_row(222, calls=1, total=10)],
            [pg_text_row(222, 'SELECT * FROM orders')],
        )
        second = await collector.collect_now()
        assert len(second.data['new_hashes']) == 1
        assert second.data['stats']['refreshed_identity_count'] == 1
        assert monitor.read('SELECT sum(calls) n FROM stmt_series WHERE res=60')[0]['n'] == 5
        assert monitor.read('SELECT count(*) n FROM query_text')[0]['n'] == 2
        now[0] += 60
        connection.rules = pg_rules(
            now[0], [pg_stat_row(111, calls=15, total=300), pg_stat_row(222, calls=2, total=20)],
            [pg_text_row(222, 'SELECT * FROM orders')],
        )
        resumed = await collector.collect_now()
        assert resumed.data['new_hashes'] == []
        assert resumed.data['stats']['refreshed_identity_count'] == 2
    finally:
        await collector.close()


def test_cleanup_fallback_deletes_only_scale_logical_database(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    monkeypatch.setattr(harness, 'connect', Mock(side_effect=TimeoutError))
    monkeypatch.setenv('LT_PG_ID', 'unit-cluster')
    monkeypatch.setenv('DIGITALOCEAN_TOKEN', 'unit-token')
    response = MagicMock()
    response.__enter__.return_value = SimpleNamespace(status=204)
    send = Mock(return_value=response)
    monkeypatch.setattr(harness.urllib.request, 'urlopen', send)
    assert harness.drop_scale_database('pg') == 'dropped rdst_scale via logical database API'
    request = send.call_args.args[0]
    assert request.get_method() == 'DELETE'
    assert request.full_url == 'https://api.digitalocean.com/v2/databases/unit-cluster/dbs/rdst_scale'


def test_cleanup_prefers_literal_sql_database_drop(monkeypatch):
    connection = Mock()
    monkeypatch.setattr(harness, 'connect', Mock(return_value=connection))
    query = Mock()
    monkeypatch.setattr(harness, 'query', query)
    send = Mock(side_effect=AssertionError('SQL cleanup used the cloud API'))
    monkeypatch.setattr(harness.urllib.request, 'urlopen', send)
    assert harness.drop_scale_database('mysql') == 'dropped rdst_scale via SQL'
    query.assert_called_once_with(connection, 'DROP DATABASE rdst_scale')
    connection.close.assert_called_once()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_monitoring_off_skips_database_collection_and_on_resumes(tmp_path, monkeypatch):
    from shared.monitor.monitor_store import MonitorStore
    from tests.unit.test_query_discovery import (
        FakeConnection, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
    )
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monitor = MonitorStore()
    monitor.set_settings("demo", {"enabled": False})
    calls = []
    connection = FakeConnection(pg_rules(2000, [pg_stat_row(111, calls=5, total=100)],
                                         [pg_text_row(111, "SELECT * FROM users")]))
    def connect(target):
        calls.append(target)
        return connection, "postgresql"
    collector = two_phase_collector(tmp_path, connect, local_clock=lambda: 2000)
    try:
        await collector.collect_now()
        assert not calls
        monitor.set_settings("demo", {"enabled": True})
        await collector.collect_now()
        assert calls == ["demo"]
        monitor.set_settings("demo", {"enabled": False})
        await collector.collect_now()
        assert calls == ["demo"]
    finally:
        await collector.close()
