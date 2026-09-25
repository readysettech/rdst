"""Isolated SQLite processes for the storage fault unit suite."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.monitor.monitor_store import MonitorStore
from shared.query_registry import library_store


def wait(root: Path, name: str) -> None:
    deadline = time.monotonic() + 40
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(.01)


def barrier(root: Path, name: str) -> None:
    (root / name).touch()
    wait(root, 'release-' + name)


def concurrent(root: Path, role: str, cycles: int) -> None:
    now = 1_790_000_000 // 3600 * 3600
    store = MonitorStore(root / 'monitor.db', clock=lambda: now)
    store.ensure_open()
    if role == 'holder':
        conn = sqlite3.connect(store.path, isolation_level=None)
        conn.execute('BEGIN EXCLUSIVE')
        conn.execute("INSERT INTO monitor_setting VALUES ('db','uncommitted','1')")
        barrier(root, 'holder-ready')
        return
    if role == 'api':
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from features.monitor.api import routes

        routes._now = lambda: now + cycles * 60
        app = FastAPI()
        app.include_router(routes.router)
        client = TestClient(app)
        conn = sqlite3.connect(store.path, isolation_level=None)
        conn.execute('BEGIN')
        conn.execute('SELECT COUNT(*) FROM stmt_series').fetchone()
        barrier(root, 'reader-ready')
        conn.execute('COMMIT')
        conn.close()
        durations = []
        for i in range(cycles * 2):
            if i == 1:
                barrier(root, 'api-first-cycle')
                (root / 'api-second-attempt').touch()
            start = time.monotonic()
            response = client.get('/targets/db/queries?range=1h')
            assert response.status_code == 200, response.text
            assert 'database is locked' not in response.text
            durations.append(time.monotonic() - start)
            (root / 'api-progress').write_text(str(i + 1))
        print(json.dumps({'role': role, 'max_wait': max(durations), 'count': len(durations),
                          'p95': sorted(durations)[int(len(durations) * .95)]}))
        return
    wait(root, 'go')
    durations = []
    for i in range(cycles):
        if i == 1:
            barrier(root, role + '-first-cycle')
            (root / (role + '-second-attempt')).touch()
        start = time.monotonic()
        (root / (role + '-attempt')).touch()
        if role == 'writer':
            store.record('db', now + i * 60,
                         [{'query_key': f'q{k}', 'calls': 3, 'total_ms': 6.0}
                          for k in range(10)], {}, [], None)
        else:
            job = store.enqueue_analysis('db', f'job{i}', 'user', 1, now + i)
            assert job
            claimed = store.claim_analysis('db', now + i, cap=10000)
            assert claimed and claimed['id'] == job['id']
            store.finish_analysis(claimed['id'], 'done', now + i)
            store.record_judge_run('db', now + i, [], outcome='empty')
            if i == 0:
                store.maintain(pause_s=0)
        durations.append(time.monotonic() - start)
        (root / (role + '-progress')).write_text(str(i + 1))
    print(json.dumps({'role': role, 'max_wait': max(durations), 'count': cycles}))


def migrate(root: Path, store_name: str, phase: str) -> None:
    # Connection callbacks stop real SQL immediately before the next statement,
    # leaving the preceding DDL or re-key write uncommitted when the parent kills us.
    original = sqlite3.connect
    state = {'ddl': False, 'rekey': False}

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            upper = sql.strip().upper()
            if phase == 'ddl' and state['ddl']:
                barrier(root, 'migration-ready')
            if phase == 'rekey' and state['rekey']:
                barrier(root, 'migration-ready')
            if phase == 'version' and upper.startswith('PRAGMA USER_VERSION ='):
                barrier(root, 'migration-ready')
            result = super().execute(sql, *args, **kwargs)
            if self.in_transaction and upper.startswith(('CREATE ', 'ALTER ')):
                state['ddl'] = True
            if self.in_transaction and upper.startswith('DELETE FROM QUERY_IDENTITY'):
                state['rekey'] = True
            if self.in_transaction and upper.startswith('DELETE FROM QUERY_TEXT'):
                state['rekey'] = True
            return result

    def connect(path, *args, **kwargs):
        conn = original(path, *args, factory=Connection, **kwargs)
        if phase == 'backup-open' and str(path).endswith('.bak.tmp'):
            barrier(root, 'migration-ready')
        return conn

    sqlite3.connect = connect
    replace = os.replace

    def renamed(source, destination):
        replace(source, destination)
        if phase == 'backup-rename' and str(destination).endswith('.bak'):
            barrier(root, 'migration-ready')

    os.replace = renamed
    if store_name == 'library':
        if phase == 'cache':
            library_store.LibraryStore._rekey_observation_history = lambda self: barrier(root, 'migration-ready')
        if phase == 'cache-commit':
            from shared.query_registry.observation_store import ObservationStore
            rekey = ObservationStore.rekey_identities
            def committed(self, moves):
                result = rekey(self, moves)
                barrier(root, 'migration-ready')
                return result
            ObservationStore.rekey_identities = committed
        library_store.LibraryStore(root / 'library.db', root / 'absent.toml').ensure_open()
    else:
        MonitorStore(root / 'monitor.db').ensure_open()



def vacuum(root: Path) -> None:
    from storage_full_vfs import install
    from shared.query_registry import cache_compaction
    from shared.query_registry.observation_store import StorageError

    state, keep_alive = install()
    original = sqlite3.connect
    def connect(path, *args, **kwargs):
        uri = str(path)
        if not uri.startswith('file:'):
            uri = 'file:' + uri
        uri += ('&' if '?' in uri else '?') + 'vfs=rdst-fault-full'
        kwargs['uri'] = True
        return original(uri, *args, **kwargs)
    sqlite3.connect = connect
    retry = cache_compaction._retrying
    def execute(conn, sql, *args, **kwargs):
        if sql == 'VACUUM':
            state['full'] = True
        return retry(conn, sql, *args, **kwargs)
    cache_compaction._retrying = execute
    path = root / 'cache.db'
    before = path.read_bytes()
    try:
        cache_compaction.compact(path, keep_seconds=10**12)
        raise AssertionError('VACUUM must fail')
    except StorageError as exc:
        assert 'Free disk space' in str(exc), str(exc)
        assert state['failures'] > 0
    finally:
        state['full'] = False
        cache_compaction._retrying = retry
    assert path.read_bytes() == before
    result = cache_compaction.compact(path, keep_seconds=10**12)
    assert result['vacuumed'] is True
    print(json.dumps({'failures': state['failures'], 'recovered': True}))

if __name__ == '__main__':
    root = Path(sys.argv[2])
    os.environ['RDST_DATA_DIR'] = str(root)
    if sys.argv[1] == 'concurrent':
        concurrent(root, sys.argv[3], int(sys.argv[4]))
    elif sys.argv[1] == 'vacuum':
        vacuum(root)
    else:
        migrate(root, sys.argv[3], sys.argv[4])
