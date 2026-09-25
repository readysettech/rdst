"""Product trigger contracts at an isolated PostHog SDK receiver."""

import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request

from features.monitor import analyze_queue, database_limit, report, telemetry as monitor_telemetry
from features.monitor.api import routes
from features.monitor.connection import ConnectionWatch
from shared import account_session, product_telemetry as product, telemetry_manager
from shared.api.routes import telemetry as client
from shared.monitor.monitor_store import MonitorStore

T0 = 1790762100
TARGET = 'canary-target'
CANARIES = ['canary.prod.internal', '/private/canary/file', 'canary@example.com',
            'secret_password_123', 'canary_sql_column']
MESSAGE = 'at canary.prod.internal /private/canary/file canary@example.com password=secret_password_123 SELECT canary_sql_column FROM orders'
BASE = dict(device_id=str, installation_id=str, rdst_version=str, os=str, os_version=str,
            python_version=str, auth_type=str, timestamp=str, internal_user=bool,
            account_kind=str)
SURFACE = dict(surface=str, is_desktop=bool, app_version=str)
DB = dict(database_id=str, engine=str)
DESKTOP = {
    'desktop_installed': dict(os='linux', os_version='6.0', arch='x64', install_kind='fresh'),
    'desktop_upgraded': dict(from_version='1.0.0', to_version='1.1.0'),
    'desktop_startup_failed': dict(stage='start', attempts=3, error_class='timeout', message=MESSAGE),
    'tray_quit': dict(uptime_hours=2.5, monitored_count=1),
    'start_at_login_changed': dict(enabled=True, by='user'),
    'os_notifications_disabled_detected': dict(reason='linux_no_daemon', platform='linux'),
    'system_notification_shown': dict(kind='test', critical=False),
    'system_notification_clicked': dict(kind='test', critical=False),
}
CONTRACTS = {
    **{name: {**BASE, **SURFACE, 'source': str, **{k: type(v) for k, v in props.items()}}
       for name, props in DESKTOP.items()},
    'monitoring_enabled': {**DB, 'display_name': str, 'first_for_account': bool,
        'ai_monitoring': str, 'own_key': bool, 'monitored_count': int},
    'monitoring_disabled': {**DB, 'display_name': str, 'monitored_count': int, 'reason': str, 'own_key': bool},
    'monitoring_limit_reached': {**DB, 'display_name': str, 'reason': str, 'feature': str,
        'monitored_count': int, 'limit': int, 'own_key': bool},
    'connection_lost': DB,
    'connection_restored': {**DB, 'downtime_s': int},
    'inbox_item_filed': {**DB, 'kind': str, 'band': str, 'change': str, 'delivery': str, 'subject_kind': str},
    'first_inbox_item': {**DB, 'display_name': str, 'kind': str, 'band': str, 'minutes_since_enable': int},
    'deep_analysis_completed': {**DB, 'ok': bool, 'duration_s': float, 'trigger': str,
        'mode': str, 'values_source': type(None), 'user_requested': bool, 'own_key': bool, 'score_bucket': type(None)},
    'analyze_run': dict(query_hash=str, mode=str, duration_ms=int, tokens_in=int, tokens_out=int,
        success=bool, target_engine=str, source=str),
    'first_analyze': dict(display_name=str, target_engine=str, duration_ms=int, source=str),
    'installation': dict(display_name=str, install_method=str, shell=str, terminal=str),
    'report_generated': {**DB, 'trigger': str, 'ok': bool, 'duration_s': float, 'error_class': str},
    'monitoring_daily_summary': {**DB, 'monitored_count': int, 'own_key': bool,
        **{k: int for k in ('queries_seen queries_known new_queries analyses_done analyses_failed analyses_skipped analyses_queued ai_checks ai_check_errors inbox_items inbox_items_new notifications inbox_open reports ai_calls collection_errors collected_minutes').split()},
        'period_hours': float, 'ai_cost_usd': float, 'uptime_pct': float, 'days_monitored': float},
    'web_started': dict(port=int, serve_static=bool, ui_mode=str, launched_by=str),
    'app_error': dict(source=str, component=str, error_class=str, message=str, handled=bool,
        suppressed_repeats=int, level=str),
}


@pytest.fixture
def receiver(monkeypatch, tmp_path):
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('RDST_TESTING', raising=False)
    monkeypatch.delenv('RDST_TELEMETRY', raising=False)
    monkeypatch.delenv('RDST_DESKTOP', raising=False)
    monkeypatch.delenv('RDST_DESKTOP_VERSION', raising=False)
    for name in ('RDST_STARTUP_MARKERS', 'RDST_NO_TTY_THEME_PROBE', 'RDST_WEB_DIST_DIR'):
        monkeypatch.setenv(name, '0')
    monkeypatch.setattr(account_session, 'is_signed_in_locally', lambda: False)
    monkeypatch.setattr(account_session, 'account_metadata', lambda: {})
    events = []
    monkeypatch.setattr(telemetry_manager, '_get_posthog', lambda: SimpleNamespace(capture=lambda **payload: events.append(json.loads(json.dumps(payload)))))
    manager = telemetry_manager.TelemetryManager()
    manager.POSTHOG_API_KEY = 'isolated-test-key'
    manager._initialized = True
    manager._device_id = 'test-install'
    manager._internal_user_result = False
    monkeypatch.setattr(product, '_telemetry', lambda: manager)
    monkeypatch.setattr(product, 'own_key', lambda: False)
    monkeypatch.setattr(product, '_gate', product._ErrorGate())
    product._ids.clear()
    (tmp_path / 'config.toml').write_text(
        '[targets.canary-target]\nengine = "postgresql"\nhost = "canary.prod.internal"\n'
        'port = 5432\ndatabase = "canary-database"\nuser = "canary-user"\n'
        'password = "secret_password_123"\n')
    store = MonitorStore(tmp_path / 'monitor.db')
    yield manager, events, store
    manager._wait_for_background_threads()


async def fire(event, store, monkeypatch):
    if event in DESKTOP or event == 'installation':
        monkeypatch.setenv('RDST_DESKTOP', '1')
        monkeypatch.setenv('RDST_DESKTOP_VERSION', '1.2.3')
        name = 'desktop_installed' if event == 'installation' else event
        body = client.ClientEventsRequest(events=[{'event': name, 'properties': {**DESKTOP[name], 'sql': MESSAGE, 'host': CANARIES[0]}}])
        request = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})
        await client.post_client_events(request, body)
    elif event in ('monitoring_enabled', 'monitoring_disabled'):
        monkeypatch.setattr(routes, '_store', lambda: store)
        monkeypatch.setattr(database_limit, 'claim_target', lambda *args: 'on')
        monkeypatch.setattr(routes, '_release_monitoring_slot', lambda target: None)
        monkeypatch.setattr(routes, '_start_first_run', lambda *args: None)
        app = FastAPI()
        app.state.analyze_queue = SimpleNamespace(wake=lambda: None)
        request = Request({'type': 'http', 'app': app})
        if event == 'monitoring_disabled':
            store.set_settings(TARGET, {'enabled': True})
            app.state.analyze_queue = None
        await routes.put_settings(TARGET, routes.MonitorSettingsUpdate(enabled=event == 'monitoring_enabled'), request)
    elif event == 'monitoring_limit_reached':
        database_limit.limit_reached(TARGET, where='enable', active=3, limit=3)
        database_limit.limit_reached(TARGET, where='enable', active=3, limit=3)
    elif event.startswith('connection_'):
        store.set_settings(TARGET, {'enabled': True})
        watch = ConnectionWatch(store, clock=lambda: T0)
        for ts in (T0, T0 + 60, T0 + 120):
            watch.collection(TARGET, False, ts, MESSAGE)
        watch.collection(TARGET, True, T0 + 600)
    elif event in ('inbox_item_filed', 'first_inbox_item'):
        store.start_first_hour(TARGET, T0 - 600)
        for ts in (T0, T0 + 60):
            store.upsert_item(TARGET, 'query_slower', 'aaaaaaaaaaaa', .9, MESSAGE, {'sql': MESSAGE}, ts)
    elif event in ('deep_analysis_completed', 'analyze_run', 'first_analyze'):
        class Service:
            async def suggest_parameter_values(self, *args):
                return {'placeholders': []}

            async def analyze(self, *args):
                yield SimpleNamespace(type='complete', query_hash='aaaaaaaaaaaa',
                    stored_analysis_id='analysis-1', llm_analysis={'success': True},
                    explain_results={'success': True})

        monkeypatch.setattr(database_limit, '_signed_in', lambda: True)
        store.record(TARGET, T0, [{'query_key': 'aaaaaaaaaaaa', 'calls': 1, 'total_ms': 2}],
                     {'aaaaaaaaaaaa': 'SELECT canary_sql_column FROM orders'}, [])
        job = store.enqueue_analysis(TARGET, 'aaaaaaaaaaaa', 'new', 1, T0, cap=None)
        job = store.claim_analysis(TARGET, T0)
        queue = analyze_queue.AnalyzeQueue(store, service=Service(), clock=lambda: T0)
        await queue._process_recorded(job)
        assert store.read('SELECT state FROM analyze_job')[0]['state'] == 'done'
    elif event == 'report_generated':
        # A real report failure before external database/AI work begins.
        monkeypatch.setattr('features.monitor.delivery.query_totals', lambda *args: (_ for _ in ()).throw(ValueError(MESSAGE)))
        with pytest.raises(ValueError):
            await report.run_report(TARGET, 'user', store=store, clock=lambda: T0)
        await report.stop_report_threads()
        for thread in list(__import__('threading').enumerate()):
            if thread.name == 'report-telemetry':
                thread.join(2)
    elif event == 'monitoring_daily_summary':
        store.set_settings(TARGET, {'enabled': True})
        store.start_first_hour(TARGET, T0)
        monkeypatch.setattr(monitor_telemetry, '_ai_usage', lambda *args: (0, 0.0))
        assert monitor_telemetry.send_due(store, now=T0) == 0
        assert monitor_telemetry.send_due(store, now=T0 + 86400) == int(product.enabled())
        assert monitor_telemetry.send_due(store, now=T0 + 86460) == 0
    elif event == 'web_started':
        import rdst
        import uvicorn
        from shared import telemetry

        monkeypatch.setattr(telemetry, 'telemetry', product._telemetry())
        monkeypatch.setattr(rdst, '_restore_web_required_env_vars', lambda: ([], [], []))
        monkeypatch.setattr(rdst, '_resolve_embedded_web_dist_dir', lambda: None)
        monkeypatch.setattr(rdst, '_get_create_app', lambda: lambda **kwargs: FastAPI())
        monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: None)
        monkeypatch.setenv('RDST_WEB_SERVE_STATIC', '0')
        assert rdst.execute_command(None, SimpleNamespace(command='web', ui='none', no_browser=True)).ok
    elif event == 'app_error':
        handler = product.AppErrorLogHandler()
        log = logging.getLogger('features.monitor.report')
        log.addHandler(handler)
        try:
            for _ in range(2):
                log.error('Failure at %s', MESSAGE)
        finally:
            log.removeHandler(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize('event', CONTRACTS)
async def test_real_trigger_event_contract(event, receiver, monkeypatch):
    manager, events, store = receiver
    await fire(event, store, monkeypatch)
    await asyncio.to_thread(manager._wait_for_background_threads)
    matches = [payload for payload in events if payload['event'] == event]
    assert len(matches) == 1
    props = dict(matches[0]['properties'])
    expected = {**BASE, **SURFACE, **CONTRACTS[event]}
    if event in DESKTOP:
        expected['desktop_version'] = str
        assert props['surface'] == 'desktop' and props['is_desktop'] is True
    if event in ('analyze_run', 'first_analyze', 'installation'):
        expected = {**BASE, **CONTRACTS[event]}
    if '$set_once' in props:
        assert props.pop('$set_once') == {'account_kind': 'device_only'}
    assert props.keys() == expected.keys()
    for key, kind in expected.items():
        assert type(props[key]) is kind, (event, key, props[key])
    for payload in events:
        for canary in CANARIES + [TARGET]:
            assert canary not in json.dumps(payload)


@pytest.mark.parametrize('event', CONTRACTS)
@pytest.mark.parametrize('gate', ['0', 'off', 'false', 'testing', 'config', 'empty_key'])
@pytest.mark.asyncio
async def test_gates_real_trigger(event, gate, receiver, monkeypatch, tmp_path):
    manager, events, store = receiver
    if gate == 'testing':
        monkeypatch.setenv('RDST_TESTING', 'true')
    elif gate == 'config':
        (tmp_path / 'config.toml').write_text('telemetry_enabled = false\n')
    elif gate == 'empty_key':
        manager.POSTHOG_API_KEY = ''
    else:
        monkeypatch.setenv('RDST_TELEMETRY', gate)
    await fire(event, store, monkeypatch)
    await asyncio.to_thread(manager._wait_for_background_threads)
    assert events == []


def test_contract_enumerates_documented_events():
    path = Path(__file__).parents[2] / 'docs/plans/continuous_analysis/TELEMETRY.md'
    if not path.is_file():
        pytest.skip('telemetry planning document is available in development checkouts')
    document = path.read_text()
    events = set()
    for line in document.splitlines():
        if line.startswith('| `'):
            events.update(re.findall(r'`([a-z_]+)`', line.split('|')[1]))
    assert events == set(CONTRACTS) | {'inbox_item_opened'}


@pytest.mark.parametrize('internal', [False, True])
def test_internal_user_flag_and_email_profile_exception(receiver, monkeypatch, internal):
    manager, events, _ = receiver
    monkeypatch.setattr(account_session, 'is_signed_in_locally', lambda: True)
    monkeypatch.setattr(account_session, 'account_metadata', lambda: {
        'user_id': 'usr_test', 'email': 'canary@readyset.io', 'analytics_account_id': 'account-test'})
    manager._internal_user_result = internal
    product.connection_lost(TARGET)
    manager._wait_for_background_threads()
    profile, event = events
    assert profile['event'] == '$identify'
    assert profile['properties']['$set'] == {'account_kind': 'signed_in', 'email': 'canary@readyset.io'}
    assert event['properties']['internal_user'] is internal
    assert event['properties']['analytics_account_id'] == 'account-test'
    assert 'canary@readyset.io' not in json.dumps(event)


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['desktop', 'process', 'thread', 'async_task', 'api'])
async def test_error_sources_from_installed_hooks(source, receiver, monkeypatch):
    import sys
    import threading
    from httpx import ASGITransport, AsyncClient
    from shared.api.app import register_error_handlers

    manager, events, _ = receiver
    error = RuntimeError(MESSAGE)
    if source == 'desktop':
        request = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})
        body = client.ClientEventsRequest(events=[{'event': 'app_error', 'properties': {
            'component': 'main', 'error_class': 'RuntimeError', 'message': MESSAGE, 'handled': False}}])
        await client.post_client_events(request, body)
    elif source == 'async_task':
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda *args: None)
        try:
            product.install_asyncio_capture(loop)
            loop.call_exception_handler({'exception': error})
        finally:
            loop.set_exception_handler(previous)
    elif source == 'api':
        from shared import telemetry

        monkeypatch.setattr(telemetry, 'telemetry', manager)
        app = FastAPI()
        register_error_handlers(app)

        @app.get('/targets/{target}/broken')
        def broken(target: str):
            raise error

        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url='http://test.invalid') as http:
            assert (await http.get('/targets/canary-target/broken')).status_code == 500
    else:
        monkeypatch.setattr(product, '_installed', False)
        monkeypatch.setattr(product, '_handler', None)
        monkeypatch.setattr(sys, 'excepthook', lambda *args: None)
        monkeypatch.setattr(threading, 'excepthook', lambda *args: None)
        product.install_error_capture()
        try:
            if source == 'process':
                sys.excepthook(RuntimeError, error, None)
            else:
                threading.excepthook(SimpleNamespace(exc_type=RuntimeError, exc_value=error))
        finally:
            for prefix, _ in product.LOG_COMPONENTS:
                logging.getLogger(prefix).removeHandler(product._handler)
    await asyncio.to_thread(manager._wait_for_background_threads)
    matches = [payload for payload in events if payload['event'] == 'app_error']
    assert len(matches) == 1
    props = dict(matches[0]['properties'])
    props.pop('$set_once', None)
    expected = {**BASE, **SURFACE, **{k: v for k, v in CONTRACTS['app_error'].items() if k != 'level'}}
    if source == 'api':
        expected.update(route=str, method=str, location=str)
    assert props.keys() == expected.keys()
    for key, kind in expected.items():
        assert type(props[key]) is kind, (source, key, props[key])
    assert props['source'] == ('desktop' if source == 'desktop' else 'backend')
    assert props['component'] == ('main' if source == 'desktop' else source)
    assert props['handled'] is False
    assert props['suppressed_repeats'] == 0
    if source == 'api':
        assert props['route'] == '/targets/{target}/broken'
        assert props['method'] == 'GET'
    for canary in CANARIES + [TARGET]:
        assert canary not in json.dumps(matches)


def test_error_dedupe_rate_limit_expiry_and_process_restart(receiver, monkeypatch):
    manager, events, _ = receiver
    now = [10000.0]
    monkeypatch.setattr(product.time, 'monotonic', lambda: now[0])
    for _ in range(3):
        product.report_error('collector', RuntimeError(MESSAGE))
    for index in range(10):
        product.report_error('collector', ValueError(f'failure {index}'))
    manager._wait_for_background_threads()
    assert len(events) == 5
    now[0] += 3601
    product.report_error('collector', RuntimeError(MESSAGE))
    manager._wait_for_background_threads()
    assert events[-1]['properties']['suppressed_repeats'] == 2
    monkeypatch.setattr(product, '_gate', product._ErrorGate())
    product.report_error('collector', RuntimeError(MESSAGE))
    manager._wait_for_background_threads()
    assert len(events) == 7
    assert events[-1]['properties']['suppressed_repeats'] == 0
