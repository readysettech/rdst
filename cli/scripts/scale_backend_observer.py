"""Process-local measurement hooks for the opt-in scale harness."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

_LOCK = threading.Lock()
_PATH = Path(os.environ['RDST_SCALE_OUTPUT']) / 'backend-metrics.jsonl'


def emit(kind, **values):
    with _LOCK, _PATH.open('a') as stream:
        stream.write(json.dumps({'ts': time.time(), 'kind': kind, **values}) + '\n')


def install():
    original_connect = sqlite3.connect

    class MeasuredConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            measured = sql.strip().upper() in ('BEGIN IMMEDIATE', 'COMMIT')
            start = time.monotonic()
            try:
                return super().execute(sql, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
                    emit('sqlite_locked', operation=sql.split()[0], db=getattr(self, 'db_name', ''))
                raise
            finally:
                if measured:
                    emit('sqlite_lock', operation=sql, db=getattr(self, 'db_name', ''),
                         duration_ms=(time.monotonic()-start)*1000)

    def connect(*args, **kwargs):
        kwargs.setdefault('factory', MeasuredConnection)
        conn = original_connect(*args, **kwargs)
        if isinstance(conn, MeasuredConnection):
            conn.db_name = Path(str(args[0] if args else kwargs.get('database', ''))).name
        return conn

    sqlite3.connect = connect
    from features.query_registry.discovery import QueryDiscoveryCollector
    original_collect = QueryDiscoveryCollector.collect_now

    async def collect(self, *args, **kwargs):
        start = time.monotonic()
        try:
            event = await original_collect(self, *args, **kwargs)
            emit('collect', target=self.target, duration_ms=(time.monotonic()-start)*1000,
                 event=event.event, stats=event.data.get('stats', {}))
            return event
        except Exception as exc:
            emit('collect_error', target=self.target, error_type=type(exc).__name__,
                 duration_ms=(time.monotonic()-start)*1000)
            raise

    QueryDiscoveryCollector.collect_now = collect
    from shared.monitor.monitor_store import MonitorStore
    original_record = MonitorStore.record

    def record(self, *args, **kwargs):
        start = time.monotonic()
        try:
            return original_record(self, *args, **kwargs)
        finally:
            emit('record', target=args[0] if args else kwargs.get('target'),
                 duration_ms=(time.monotonic()-start)*1000)

    MonitorStore.record = record

    # Observe real HTTP admission and guard the experiment against accidental AI billing.
    # Paths and host names are logged; headers, bodies and query strings are omitted.
    def observe(method, url):
        parts = urlsplit(str(url))
        if parts.hostname in ('localhost', '127.0.0.1'):
            return
        ai = method.upper() == 'POST' and any(word in parts.path for word in
                ('jev', 'attention', 'assess', 'messages', 'completion', 'responses', 'inference'))
        if ai:
            emit('ai_attempt', host=parts.hostname, path=parts.path)
            raise RuntimeError('Scale test detected an AI request while signed out')

    import requests
    original_send = requests.Session.send

    def send(self, request, **kwargs):
        observe(request.method, request.url)
        return original_send(self, request, **kwargs)

    requests.Session.send = send
    import httpx
    original_httpx_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def httpx_send(self, request, *args, **kwargs):
        observe(request.method, request.url)
        return original_httpx_send(self, request, *args, **kwargs)

    async def async_send(self, request, *args, **kwargs):
        observe(request.method, request.url)
        return await original_async_send(self, request, *args, **kwargs)

    httpx.Client.send = httpx_send
    httpx.AsyncClient.send = async_send
    emit('observer_ready')
