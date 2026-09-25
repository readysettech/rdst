"""Local calendar contracts, independently checked with the IANA zone database."""

import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from features.monitor import analyze_queue, database_limit, report, snapshot
from shared.monitor.monitor_store import MonitorStore, local_day_bounds

ZONES = ['America/Denver', 'America/New_York', 'Europe/London', 'UTC', 'Asia/Kathmandu']
DAYS = ['2026-03-08', '2026-11-01', '2026-03-29', '2026-10-25']


@pytest.fixture(params=ZONES)
def zone(request, monkeypatch):
    if not hasattr(time, 'tzset'):
        pytest.skip('Process timezone switching requires tzset')
    original = os.environ.get('TZ')
    monkeypatch.setenv('TZ', request.param)
    time.tzset()
    yield ZoneInfo(request.param)
    if original is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = original
    time.tzset()


def bounds(day, zone):
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return tuple(int(datetime.combine(d, datetime.min.time(), zone).timestamp())
                 for d in (day, day + timedelta(days=1)))


@pytest.mark.parametrize('day', DAYS)
def test_local_day_report_totals_chart_and_cap_reset(zone, day, tmp_path):
    start, end = bounds(day, zone)
    noon = int(datetime.combine(date.fromisoformat(day), datetime.min.time(), zone)
               .replace(hour=12).timestamp())
    assert local_day_bounds(noon) == (start, end)
    expected_hours = 24
    if zone.key in ZONES[:2] and day in DAYS[:2]:
        expected_hours = 23 if day == DAYS[0] else 25
    if zone.key == 'Europe/London' and day in DAYS[2:]:
        expected_hours = 23 if day == DAYS[2] else 25
    assert end - start == expected_hours * 3600
    assert snapshot.report_period('nightly', end + 3600) == (start, end, False)
    assert snapshot.report_period('user', noon) == (start, noon, True)
    assert snapshot.prior_period(start, end) == (bounds(date.fromisoformat(day) - timedelta(days=1), zone)[0], start)
    store = MonitorStore(tmp_path / 'monitor.db')
    store.set_settings('db', {'enabled': True, 'analysis_daily_cap': 1})
    key = 'aaaaaaaaaaaa'
    for ts in (start - 60, start, end - 60, end):
        store.record('db', ts, [{'query_key': key, 'calls': 7, 'total_ms': 14}],
                     {key: 'SELECT id FROM orders'}, [], {'calls': 7, 'total_ms': 14})
    assert snapshot.day_totals(store, 'db', start, end)[key]['calls'] == 14
    chart = snapshot.day_chart(store, 'db', start, end, end + 60, [], {})
    assert chart['start'] == start and chart['end'] == end
    assert all(start <= ts < end for ts in chart['xs'])
    job = store.enqueue_analysis('db', key, 'top15_stale', 1, start, cap=None)
    assert store.claim_analysis('db', start)['id'] == job['id']
    store.finish_analysis(job['id'], 'done', start + 60, analysis_ref='saved')
    assert analyze_queue.budget(store, 'db', end - 1)['left'] == 0
    assert analyze_queue.budget(store, 'db', noon)['reset_at'] == end
    assert analyze_queue.started_today(store, 'db', start - 1) == 0
    assert analyze_queue.started_today(store, 'db', end) == 0
    assert analyze_queue.budget(store, 'db', end)['left'] == 1


@pytest.mark.parametrize('day,hour', [(DAYS[0], 2), (DAYS[1], 1), (DAYS[2], 1), (DAYS[3], 1)])
def test_nightly_missing_or_repeated_hour_runs_once(zone, day, hour, tmp_path, monkeypatch):
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    start, end = bounds(day, zone)
    previous, _ = bounds(date.fromisoformat(day) - timedelta(days=1), zone)
    store = MonitorStore(tmp_path / 'monitor.db')
    store.set_settings('db', {'enabled': True, 'nightly_report': True, 'nightly_hour': hour})
    store.record('db', start - 120, [{'query_key': 'aaaaaaaaaaaa', 'calls': 1, 'total_ms': 2}],
                 {'aaaaaaaaaaaa': 'SELECT id FROM orders'}, [])
    store.record('db', start - 60, [], {'aaaaaaaaaaaa': 'SELECT id FROM orders'}, [])
    scheduler = report.ReportScheduler(store)
    plans = []
    for now in range(start, min(end, start + 6 * 3600), 60):
        plan = scheduler._plan(now)
        plans.extend(plan)
        for kind, (_, run) in plan:
            assert kind == 'nightly'
            assert datetime.fromtimestamp(now, zone).hour >= hour
            store.update_report(run['id'], state='done', finished_at=now,
                                period=(previous, start), report={'summary': 'Done'})
    assert len(plans) == 1
    first = plans[0][1][1]['ts']
    valid = [ts for ts in range(start, start + 6 * 3600, 60)
             if datetime.fromtimestamp(ts, zone).hour >= hour]
    assert first == valid[0]
