"""Single-query inbox identity, occurrences and measured resume."""

import json
import sqlite3

from features.monitor.judge import reconcile_recovery
from features.monitor.presentation import band_reason, item_status
from shared.monitor.monitor_store import MonitorStore, _repair_open_items

KEY = 'aaaaaaaaaaaa'


def file_stop(store, ts, subject=KEY):
    return store.upsert_item('db', 'query_stopped_running', subject, .5, 'Stopped',
                             {'query_key': KEY, 'filed_at': ts, 'numbers': {'qps': 0}},
                             ts, notify=False)


def test_stop_aliases_and_repeated_checks_share_one_open_item(tmp_path):
    store = MonitorStore(tmp_path / 'monitor.db')
    first = file_stop(store, 100, f'{KEY}:100')
    store.action_item(first['id'], 'seen', 110)
    again = file_stop(store, 200, f'{KEY}:200')
    assert again['id'] == first['id']
    assert again['subject'] == KEY and again['state'] == 'seen'
    assert len(store.inbox()) == 1
    assert len(json.loads(again['body_json'])['occurrences']) == 1


def test_resume_closes_and_next_stop_adds_occurrence(tmp_path):
    store = MonitorStore(tmp_path / 'monitor.db')
    first = file_stop(store, 100)
    resumed = {'query_key': KEY, 'calls': 1, 'qps': .1, 'mean_ms': 2, 'db_time_share': .2}
    assert reconcile_recovery(store, 'db', 400, [resumed]) == {('query_stopped_running', KEY)}
    [closed] = store.inbox('resolved')
    assert item_status({'severity_band': 'recovered'}, json.loads(closed['body_json']), None) == (
        'back_to_normal', 400)
    assert file_stop(store, 100)['state'] == 'resolved'
    again = file_stop(store, 700, f'{KEY}:700')
    assert again['id'] == first['id'] and again['state'] == 'new'
    assert json.loads(again['body_json'])['occurrences'] == [
        {'start': 100, 'end': 400, 'keys': [KEY]},
        {'start': 700, 'end': None, 'keys': [KEY]}]
    file_stop(store, 1000)
    assert len(json.loads(store.inbox('new')[0]['body_json'])['occurrences']) == 2


def test_a_settled_stop_reports_resume_at_the_observation_time(tmp_path, monkeypatch):
    monkeypatch.setenv('RDST_MONITOR_ATTENTION_SETTLE_S', '1800')
    store = MonitorStore(tmp_path / 'monitor.db')
    file_stop(store, 100)
    reconcile_recovery(store, 'db', 1900, [])
    assert json.loads(store.inbox('resolved')[0]['body_json'])['settled']
    assert file_stop(store, 2000)['state'] == 'resolved'
    reconcile_recovery(store, 'db', 2200, [
        {'query_key': KEY, 'calls': 1, 'qps': .1, 'mean_ms': 2, 'db_time_share': .2}])
    body = json.loads(store.inbox('resolved')[0]['body_json'])
    assert body['settled'] is False
    assert body['resumed_at'] == 2200
    assert file_stop(store, 2000)['state'] == 'resolved'
    assert file_stop(store, 2500)['state'] == 'new'


def test_migration_merges_open_aliases_keeps_earliest_and_history(tmp_path):
    path = tmp_path / 'monitor.db'
    store = MonitorStore(path)
    first = file_stop(store, 100)
    second = store.upsert_item('db', 'query_stopped_running', 'bbbbbbbbbbbb', .7,
                               'Stopped', {'query_key': 'bbbbbbbbbbbb'}, 200, notify=False)
    other_target = store.upsert_item('other', 'query_stopped_running', KEY, .5,
                                     'Stopped', {'query_key': KEY}, 200, notify=False)
    other_kind = store.upsert_item('db', 'traffic_spike', KEY, .5,
                                   'Busy', {'query_key': KEY}, 200, notify=False)
    dismissed = store.upsert_item('db', 'query_stopped_running', 'cccccccccccc', .5,
                                  'Stopped', {'query_key': 'cccccccccccc'}, 300, notify=False)
    store.action_item(dismissed['id'], 'dismiss', 301)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE inbox_item SET body_json=? WHERE id IN (?,?)',
                     (json.dumps({'query_key': KEY}), second['id'], dismissed['id']))
        conn.execute('PRAGMA user_version=21')
    migrated = MonitorStore(path)
    rows = migrated.inbox()
    assert {row['id'] for row in rows} == {
        first['id'], other_target['id'], other_kind['id'], dismissed['id']}
    kept = next(row for row in rows if row['id'] == first['id'])
    assert kept['created_at'] == 100 and kept['severity'] == .7
    assert [e['start'] for e in json.loads(kept['body_json'])['occurrences']] == [100, 200]
    assert len([e for e in migrated.inbox_events() if e['item_id'] == first['id']]) == 2
    assert not migrated.read('SELECT * FROM incident_pin WHERE item_id=?', (second['id'],))
    before = migrated.inbox()
    migrated._write(_repair_open_items)
    assert migrated.inbox() == before
    assert path.with_name('monitor.db.pre-v21.bak').exists()


def test_reason_uses_watch_for_fyi_and_stopped_has_no_still_happening():
    item = {'type': 'query_stopped_running', 'severity_band': 'fyi', 'created_at': 100,
            'state': 'new', 'query_keys': [KEY]}
    assert band_reason(item, {}, 'ongoing') == 'Watch: a query stopped running'
    assert item_status(item, {'filed_at': 200}, None) == ('ongoing', 200)
