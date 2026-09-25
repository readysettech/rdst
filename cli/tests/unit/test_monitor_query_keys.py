"""Affected-query links filter the complete catalog before pagination."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor.api import routes, views
from shared.monitor.monitor_store import MonitorStore


def test_rv3_04_keys_filter_before_counting_and_pagination(tmp_path, monkeypatch):
    now = 100 * 86400
    store = MonitorStore(tmp_path / 'monitor.db', clock=lambda: now)
    statements = [{'query_key': f'{i:012x}', 'calls': 1, 'total_ms': float(700 - i)}
                  for i in range(700)]
    store.record('db', now - 60, statements,
                 {row['query_key']: f'SELECT * FROM table_{i}'
                  for i, row in enumerate(statements)}, [], workload_checked=True)
    monkeypatch.setattr(views, 'registry_entries', lambda keys=None: {})
    monkeypatch.setattr(views, 'assessments', lambda target, keys=None: {})
    monkeypatch.setattr(routes, '_store', lambda: store)
    monkeypatch.setattr(routes, '_now', lambda: now)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/monitor')
    client = TestClient(app)
    selected = [f'{i:012x}' for i in (598, 699)]
    for offset, expected in enumerate(selected):
        response = client.get('/api/monitor/targets/db/queries', params={
            'range': '1h', 'keys': ','.join(selected), 'limit': 1, 'offset': offset})
        assert response.status_code == 200
        page = response.json()
        assert page['total'] == 2
        assert [row['key'] for row in page['queries']] == [expected]
    page = client.get('/api/monitor/targets/db/queries', params={
        'range': '1h', 'keys': ','.join(selected), 'q': 'table_699'}).json()
    assert page['total'] == 1
    assert [row['key'] for row in page['queries']] == [selected[1]]
