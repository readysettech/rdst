"""Collection resumes promptly and retains fencing across backend restarts."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from unittest.mock import AsyncMock

import psutil
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes
from features.query_registry.discovery import QueryDiscoveryCollector
from features.query_registry.scheduler import ObservationScheduler
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.observation_store import LeaseLostError, ObservationStore, collection_owner
from tests.unit.test_observation_scheduler import FakeClock, FakeCoordinator, FakeLeaseStore, run_tick


def test_dead_collector_reclaimed_with_monotonic_fence(tmp_path):
    store = ObservationStore(tmp_path / 'cache.db')
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                             stdin=subprocess.PIPE)
    try:
        owner = 'rdst-process:' + json.dumps([socket.gethostname(), child.pid,
            psutil.Process(child.pid).create_time(), 'test'])
        assert store.acquire_lease('db', owner, 360, 1000) == 1
        assert store.acquire_lease('db', collection_owner(), 360, 1001) is None
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdin.close()
    fresh = collection_owner()
    assert store.acquire_lease('db', fresh, 360, 1002) == 2
    with pytest.raises(LeaseLostError):
        with store.write_fence('db', owner_id=owner, fencing_token=1, now=1002):
            pass


@pytest.mark.parametrize('kind', ['live', 'remote', 'legacy', 'denied', 'reused'])
def test_reclaim_requires_confirmed_dead_local_process(tmp_path, monkeypatch, kind):
    store = ObservationStore(tmp_path / 'cache.db')
    identity = [socket.gethostname(), os.getpid(), psutil.Process().create_time(), 'test']
    if kind == 'remote':
        identity[0] = 'remote-host'
    if kind == 'reused':
        identity[2] -= 1
    owner = 'legacy-owner' if kind == 'legacy' else 'rdst-process:' + json.dumps(identity)
    store.acquire_lease('db', owner, 360, 1000)
    if kind == 'denied':
        def denied(pid):
            raise psutil.AccessDenied(pid)
        monkeypatch.setattr(psutil, 'Process', denied)
    result = store.acquire_lease('db', 'new-owner', 360, 1001)
    assert result == (2 if kind == 'reused' else None)


async def test_first_reading_within_one_interval_of_backend_start(tmp_path):
    clock = FakeClock()
    store = ObservationStore(tmp_path / 'cache.db')
    coordinator = FakeCoordinator(store, clock)
    monitor = MonitorStore(tmp_path / 'monitor.db')
    async def reading():
        monitor.record_pulse('db', int(clock.now), 'collect', True, 'Read 1 query')
        from types import SimpleNamespace
        return SimpleNamespace(event='discovery_update', data={'stats': {}})
    coordinator.collector_for('db').collect_now = reading
    # A dead process was collecting when the desktop restarted.
    owner = 'rdst-process:' + json.dumps([socket.gethostname(), os.getpid(),
                                        psutil.Process().create_time() - 1, 'old'])
    store.acquire_lease('db', owner, 360, int(clock.now))
    scheduler = ObservationScheduler(coordinator, clock=clock.time,
        targets_provider=lambda: ['db'], monitored_provider=lambda: {'db'})
    began = clock.now
    await scheduler.start()
    try:
        await asyncio.wait_for(run_tick(scheduler, scheduler._schedules['db'], clock), 2)
        pulses = monitor.pulses('db')
        assert pulses[0]['ts'] - began < 60
        assert clock.now - began < 60
    finally:
        await asyncio.wait_for(scheduler.stop(), 2)
        store.close()


async def test_scheduler_handoff_drains_subscriber_polling():
    collector = QueryDiscoveryCollector('db', two_phase=False)
    collector.collect_now = AsyncMock()
    try:
        await collector.ensure_started()
        await asyncio.sleep(0)
        assert collector.collect_now.await_count == 1
        collector.set_scheduler_owned(True)
        await asyncio.wait_for(collector.task, 1)
        await collector.ensure_started()
        assert collector.task.done()
        assert collector.collect_now.await_count == 1
    finally:
        await asyncio.wait_for(collector.close(), 2)


async def test_check_now_joins_inflight_and_reports_failure():
    clock = FakeClock()
    coordinator = FakeCoordinator(FakeLeaseStore(), clock)
    scheduler = ObservationScheduler(coordinator, clock=clock.time,
        targets_provider=lambda: ['db'], monitored_provider=lambda: {'db'})
    scheduler._store = coordinator.store()
    scheduler._refresh_targets(clock.now)
    collector = coordinator.collector_for('db')
    collector.gate = asyncio.Event()
    one = asyncio.create_task(scheduler.check_now('db'))
    await asyncio.sleep(0)
    two = asyncio.create_task(scheduler.check_now('db'))
    await asyncio.sleep(0)
    collector.gate.set()
    results = await asyncio.wait_for(asyncio.gather(one, two), 2)
    assert all(result['ok'] for result in results)
    assert collector.collects == 1
    collector.fail_with = RuntimeError('database unavailable')
    assert not (await asyncio.wait_for(scheduler.check_now('db'), 2))['ok']
    assert scheduler.collection_status('db')['delay_reason'] == 'Retrying after an error: RuntimeError'


def test_pulse_startup_and_collect_now_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    store = MonitorStore()
    store.set_settings('db', {'enabled': True})
    store.record_pulse('db', 1000, 'collect', True, 'ok')
    from types import SimpleNamespace
    scheduler = SimpleNamespace(next_reading_at=lambda target: 2001,
        collection_status=lambda target: {'first_reading': True,
                                         'delay_reason': 'Starting up after a restart'},
        check_now=AsyncMock(return_value={'ok': True, 'detail': 'Reading complete'}))
    monkeypatch.setattr(routes, '_now', lambda: 2000)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/monitor')
    app.state.monitor_scheduler = scheduler
    with TestClient(app, base_url='http://testserver', headers={'origin': 'http://testserver'}) as http:
        page = http.get('/api/monitor/targets/db/pulse').json()
        assert page['first_reading']
        assert page['pulses'][0]['state'] == 'live'
        assert page['delay_reason'] == 'Starting up after a restart'
        response = http.post('/api/monitor/targets/db/collect-now')
        assert response.status_code == 200
        assert response.json()['ok']
        store.set_settings('db', {'enabled': False})
        assert http.post('/api/monitor/targets/db/collect-now').status_code == 409


async def test_first_monitor_reading_bounds_identity_work_then_reconciles(tmp_path, monkeypatch):
    from features.query_registry import discovery
    from tests.unit.test_query_discovery import (
        FakeConnection, pg_rules, pg_stat_row, pg_text_row, two_phase_collector,
    )

    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    monitor = MonitorStore()
    monitor.set_settings('demo', {'enabled': True})
    rows = [pg_stat_row(index, calls=1, total=float(index)) for index in range(250)]
    texts = [pg_text_row(index, f'SELECT c{index} FROM scale_rows') for index in range(250)]
    connection = FakeConnection(pg_rules(2000, rows, texts))
    store = ObservationStore(tmp_path / 'cache.db')
    collector = two_phase_collector(tmp_path, lambda target: (connection, 'postgresql'),
                                    store=store)
    try:
        await collector.collect_now()
        assert len(collector._alias_map) == discovery.STARTUP_IDENTITY_RESOLUTION_BATCH
        assert len(monitor.pulses('demo')) == 1
        assert monitor.read('SELECT COUNT(*) n FROM stmt_series') == [{'n': 0}]
        connection.rules = pg_rules(2060, rows, texts)
        await collector.collect_now()
        assert len(collector._alias_map) == 250
    finally:
        await collector.close()
        store.close()
