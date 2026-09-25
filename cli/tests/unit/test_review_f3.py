"""Regression coverage for monitoring recovery, admission, and target removal."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from features.monitor.judge import reconcile_recovery
from features.query_registry import assessment
from shared.monitor.monitor_store import MonitorStore


@pytest.mark.parametrize('observed', [False, True])
def test_grouped_stops_wait_for_resumption(tmp_path, observed):
    store = MonitorStore(tmp_path / 'monitor.db')
    members = {str(i): {'kind': 'query_stopped_running', 'filed_at': 1000,
                       'at_filing': {'qps': 0}, 'baseline_qps': 1} for i in range(6)}
    store.upsert_item('app', 'many_queries_changed', 'database', .7, 'Stopped',
                      {'query_keys': list(members), 'recovery_queries': members}, 1000)
    if observed:
        store._write(lambda conn: conn.execute(
            'INSERT INTO db_series(target,res,bucket_start,calls,total_ms) VALUES (?,?,?,?,?)',
            ('app', 60, 1240, 0, 0)))
    silent = [{'query_key': key, 'calls': 0, 'qps': 0} for key in members] if observed else []
    assert reconcile_recovery(store, 'app', 1300, silent) == set()
    assert len(json.loads(store.inbox(target='app')[0]['body_json'])['recovery_queries']) == 6
    resumed = [{'query_key': key, 'calls': 10, 'qps': 1} for key in members]
    for now in (1600, 1900, 2200):
        reconcile_recovery(store, 'app', now, resumed)
    item = store.inbox(target='app')[0]
    assert item['state'] == 'resolved'
    assert all(v['after']['qps'] == 1 for v in json.loads(item['body_json'])['recovered_queries'].values())


@pytest.mark.asyncio
async def test_background_rotates_after_staggered_idle_lanes():
    worker = assessment.AssessmentWorker()
    worker.set_background_targets(['a', 'b'])
    started = []
    releases = [asyncio.Event(), asyncio.Event()]
    both_started = asyncio.Event()
    rotated = asyncio.Event()
    async def lane(target):
        started.append(target)
        if target == 'b':
            rotated.set()
            return False
        index = len(started) - 1
        if index == 1:
            both_started.set()
        if index < 2:
            await releases[index].wait()
        else:
            await asyncio.Event().wait()
        return False
    async def wait(seconds):
        releases[1].set()
        await asyncio.sleep(0)
    worker._lane = lane
    worker._wait = wait
    worker._task = asyncio.create_task(worker._run())
    try:
        await asyncio.wait_for(both_started.wait(), 1)
        releases[0].set()
        await asyncio.wait_for(rotated.wait(), 1)
        assert started[:3] == ['a', 'a', 'b']
    finally:
        await worker.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke_during_schema', [False, True])
async def test_local_monitor_does_not_classify(tmp_path, monkeypatch, revoke_during_schema):
    from features.monitor import database_limit
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    store = MonitorStore()
    store.set_settings('app', {'enabled': True})
    database_limit.set_ai_state(store, 'app', 'on' if revoke_during_schema else 'local')
    worker = assessment.AssessmentWorker()
    worker.set_background_targets(['app'])
    worker._client.assess = Mock(return_value={})
    worker._still_active = lambda *args: True
    worker._publish = AsyncMock()
    monkeypatch.setattr(assessment, '_eligible_keys', lambda target: None)
    def collect(*args, **kwargs):
        database_limit.set_ai_state(store, 'app', 'local')
        return SimpleNamespace(payload={})
    collector = Mock(side_effect=collect)
    monkeypatch.setattr(assessment, 'collect_assessment_context', collector)
    library = SimpleNamespace(retry_assessment=Mock())
    claim = {'id': 1, 'generation': 1, 'hash': 'q', 'sql': 'select 1', 'request_id': 'r'}
    await worker._assess_claim('app', 'identity', {}, library, claim, Mock())
    assert collector.call_count == int(revoke_during_schema)
    worker._client.assess.assert_not_called()


@pytest.mark.asyncio
async def test_remove_readd_disables_monitoring_and_cancels_jobs(tmp_path, monkeypatch):
    from features.configure.service import ConfigureService
    from features.configure.events import ConfigureSuccessEvent
    from features.monitor import database_limit
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    store = MonitorStore()
    store.set_settings('app', {'enabled': True, 'execute': True})
    store.enqueue_analysis('app', 'q', 'user', 1, 1000)
    assert store.claim_analysis('app', 1001)
    store.enqueue_analysis('app', 'queued', 'user', 1, 1002)
    store.enqueue_analysis('other', 'untouched', 'user', 1, 1002)
    from shared import analysis_lane
    run = analysis_lane.begin('app')
    config = {'engine': 'postgresql', 'host': 'old', 'database': 'old'}
    targets = {'app': config}
    class Config:
        def get(self, name):
            return targets.get(name)
        def remove(self, name):
            assert not store.levers(name)['enabled']
            assert all(j['state'] == 'skipped' for j in store.analyze_jobs(name))
            assert run.stopped.is_set()
            targets.pop(name)
        def save(self):
            pass
    @asynccontextmanager
    async def retire(name):
        yield True
    manager = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), retire_target=retire)
    monkeypatch.setattr('shared.deploy.sandbox_manager.sandbox_manager', manager)
    monkeypatch.setattr('shared.ssh_tunnel.get_tunnel_manager', lambda: SimpleNamespace(close=Mock()))
    release = Mock(return_value=True)
    monkeypatch.setattr(database_limit, 'release', release)
    service = ConfigureService()
    monkeypatch.setattr(service, '_load_config', Config)
    events = [event async for event in service.remove_target('app')]
    assert any(isinstance(event, ConfigureSuccessEvent) for event in events), events
    release.assert_called_once_with(config)
    targets['app'] = {'engine': 'postgresql', 'host': 'new', 'database': 'new'}
    assert 'app' not in store.monitored_targets()
    assert not store.levers('app')['execute']
    assert store.claim_analysis('app', 2000) is None
    assert store.claim_analysis('other', 2000) is not None


def test_grouped_stops_expire_as_settled(tmp_path, monkeypatch):
    from features.monitor.presentation import recovered
    monkeypatch.setenv("RDST_MONITOR_ATTENTION_SETTLE_S", "1800")
    store = MonitorStore(tmp_path / "monitor.db")
    store.upsert_item("app", "many_queries_changed", "database", .7, "Stopped", {
        "query_keys": ["q"], "recovery_queries": {"q": {
            "kind": "query_stopped_running", "filed_at": 1000, "at_filing": {"qps": 0}}}}, 1000)
    assert not reconcile_recovery(store, "app", 2799, [])
    assert reconcile_recovery(store, "app", 2800, [])
    item = store.inbox(target="app")[0]
    body = json.loads(item["body_json"])
    assert body["settled"]
    assert body["recovered_queries"]["q"]["settled"]
    assert not recovered(item, body)


@pytest.mark.parametrize("monitored", [False, True])
def test_classification_attribution_preserves_interactive_contract(monitored):
    from shared.llm_manager.inference_attribution import monitoring_database, monitor_database_headers
    from shared.llm_manager.jev_client import JevClient
    client = JevClient()
    sent = []
    client._post = lambda path, payload: sent.append((path, payload, monitor_database_headers()))
    with monitoring_database("a" * 64 if monitored else None):
        client.assess(request_id="r", rubric_version="v", sql="select 1", dialect="postgresql",
                      evidence={}, input_fingerprint="f")
    path, payload, headers = sent[0]
    assert path == "/v1/jev/assess"
    assert payload["attribution"]["operation"] == (
        "monitor_classification" if monitored else "query_registry_analysis")
    assert headers == ({"X-RDST-Monitor-Database": "a" * 64} if monitored else {})
