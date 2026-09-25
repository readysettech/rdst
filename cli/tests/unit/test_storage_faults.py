"""Portable storage fault gates using real files, transactions and killed processes."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shared.monitor import monitor_store
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry import library_store, observation_store, cache_compaction
from shared.query_registry.observation_store import ObservationStore, StorageError
from shared.query_registry.query_registry import QueryEntry, hash_sql, _sql_digest, normalize_sql
from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

NOW = 1_790_000_000 // 3600 * 3600
WORKER = Path(__file__).with_name('storage_fault_worker.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def logical(path):
    conn = sqlite3.connect(path)
    try:
        return hashlib.sha256('\n'.join(conn.iterdump()).encode()).hexdigest()
    finally:
        conn.close()


def start(root, *args):
    env = dict(os.environ, RDST_DATA_DIR=str(root), RDST_TESTING='true')
    return subprocess.Popen([sys.executable, str(WORKER), args[0], str(root), *args[1:]],
                            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def await_file(root, name, process, seconds=15):
    deadline = time.monotonic() + seconds
    while not (root / name).exists():
        if process.poll() is not None:
            out, err = process.communicate()
            pytest.fail(f'{name}: exited {process.returncode}: {out} {err}')
        assert time.monotonic() < deadline, name
        time.sleep(.01)


def reap(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def seed_history(root, monkeypatch, keys=1000, minutes=60):
    # Process tests use the actual production runtime decision, including DELETE fallback.
    conn = sqlite3.connect(':memory:')
    actual = conn.execute('SELECT sqlite_version()').fetchone()[0]
    conn.close()
    monkeypatch.setattr(sqlite3, 'sqlite_version_info', tuple(map(int, actual.split('.'))))
    store = MonitorStore(root / 'monitor.db', clock=lambda: NOW)
    store.ensure_open()
    def seed(conn):
        conn.executemany("INSERT INTO query_text VALUES ('db',?,'SELECT 1',?,?)",
                         [(f'q{k}', NOW - 100 * 86400, NOW) for k in range(keys)])
        conn.executemany("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,?,?,?,2.0,0,0,0)",
                         ((f'q{k}', res, NOW - age - n * res, 1)
                          for res, age, count in ((60, 86400, minutes), (300, 5 * 86400, 24),
                                                  (3600, 80 * 86400, 24))
                          for k in range(keys) for n in range(count)))
        conn.executemany("INSERT INTO monitor_setting VALUES ('db',?,?)",
                         [(f'rollup_until_{res}', str(NOW // dest * dest))
                          for res, dest in ((60, 300), (300, 3600), (3600, 86400))])
        conn.executemany("INSERT INTO query_tier VALUES ('db',?,?)",
                         [(f'q{k}', k + 1) for k in range(keys)])
        conn.executemany("INSERT INTO query_load VALUES ('db',?,1,?)",
                         [(f'q{k}', NOW) for k in range(keys)])
    store._write(seed)
    return store


def run_concurrent(root, monkeypatch, cycles=12, keys=1000, minutes=60):
    store = seed_history(root, monkeypatch, keys, minutes)
    processes = []
    started = time.monotonic()
    try:
        reader = start(root, 'concurrent', 'api', str(cycles))
        processes.append(reader)
        await_file(root, 'reader-ready', reader)
        writer = start(root, 'concurrent', 'writer', str(cycles))
        judge = start(root, 'concurrent', 'judge', str(cycles))
        processes.extend((writer, judge))
        (root / 'go').touch()
        await_file(root, 'writer-attempt', writer)
        await_file(root, 'judge-attempt', judge)
        # Keep a read snapshot pinned while both writers reach BEGIN/COMMIT.
        time.sleep(.2)
        (root / 'release-reader-ready').touch()
        for role, process in (('writer', writer), ('judge', judge), ('api', reader)):
            await_file(root, role + '-first-cycle', process)
        # A separate killed writer leaves a hot rollback journal and releases its lock.
        holder = start(root, 'concurrent', 'holder', str(cycles))
        processes.append(holder)
        await_file(root, 'holder-ready', holder)
        for role, process in (('writer', writer), ('judge', judge), ('api', reader)):
            (root / ('release-' + role + '-first-cycle')).touch()
            await_file(root, role + '-second-attempt', process)
        time.sleep(.2)
        holder.kill()
        holder.communicate(timeout=5)
        for process in (writer, reader, judge):
            out, err = process.communicate(timeout=40)
            assert process.returncode == 0, err
            result = json.loads(out.strip().splitlines()[-1])
            assert result['count'] >= cycles
            assert result['max_wait'] < 15, result
            if result['role'] == 'api':
                assert result['p95'] < 5, result
        assert time.monotonic() - started < 60
        for role in ('api', 'writer', 'judge'):
            assert int((root / (role + '-progress')).read_text()) >= cycles
        assert store.read("SELECT value FROM monitor_setting WHERE key='uncommitted'") == []
        rows = store.read('SELECT bucket_start,SUM(calls) calls FROM stmt_series '
                          'WHERE res=60 AND bucket_start>=? GROUP BY bucket_start ORDER BY bucket_start',
                          (NOW,))
        assert rows == [{'bucket_start': NOW + i * 60, 'calls': 30} for i in range(cycles)]
        assert store.read("SELECT MAX(ts) last FROM monitor_pulse WHERE kind='collect'") == [
            {'last': NOW + (cycles - 1) * 60}]
        assert len(store.analyze_jobs('db', 'done')) == cycles
        assert store.read('SELECT COUNT(*) n,MAX(ts) last FROM judge_run') == [
            {'n': cycles, 'last': NOW + cycles - 1}]
        assert store.read('PRAGMA integrity_check') == [{'integrity_check': 'ok'}]
    finally:
        for process in processes:
            reap(process)


def test_three_processes_big_history_and_killed_writer(tmp_path, monkeypatch):
    run_concurrent(tmp_path, monkeypatch)


@pytest.mark.slow
@pytest.mark.skipif(os.getenv('RDST_RUN_MONITOR_SLOW') != '1', reason='extended retained history')
def test_extended_process_history(tmp_path, monkeypatch):
    run_concurrent(tmp_path, monkeypatch, cycles=30, keys=1000, minutes=1440)


def old_library(root, monkeypatch):
    text = 'SELECT * FROM orders WHERE id IN (1, 2, 3)'
    old = _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
    with monkeypatch.context() as patch:
        patch.setattr(library_store, 'SCHEMA_VERSION', 15)
        store = library_store.LibraryStore(root / 'library.db', root / 'absent.toml')
        store.ensure_open()
        entry = QueryEntry.from_dict({'hash': old, 'sql': text, 'original_sql': text,
                                      'source': 'manual', 'frequency': 7}).to_dict()
        with store._write() as conn:
            store._insert_entry(conn, old, entry)
        store.record_analysis(query_hash=old, analysis_id='precious', created_at='2026-08-01',
                              target='db', payload={'summary': 'preserved'})
    with ObservationStore(root / 'cache.db') as cache:
        cache.record_recent_observations('db', [{'normalized_hash': old,
            'window_start': NOW - 60, 'window_end': NOW, 'calls_delta': 17,
            'exec_time_delta': 34., 'approximate_qps': .2, 'completeness': 'complete'}])
    return old, hash_sql(text), text


def old_monitor(root):
    path = root / 'monitor.db'
    store = MonitorStore(path)
    store.ensure_open()
    text = 'SELECT * FROM orders WHERE id IN (1, 2, 3)'
    old = _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
    store._write(lambda conn: conn.execute("INSERT INTO query_text VALUES ('db',?,?,0,0)",
                                           (old, text)))
    store._write(lambda conn: conn.execute("INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,60,60,17,34,0,0,0)", (old,)))
    def references(conn):
        conn.execute("INSERT INTO query_sample(target,query_key,text,observed_at) VALUES ('db',?,?,60)",
                     (old, text))
        conn.execute("INSERT INTO attention_episode VALUES ('db',?,'latency_regression',2,60,1)", (old,))
        conn.execute("INSERT INTO analyze_job(target,query_key,reason,state,priority,enqueued_at,analysis_ref) "
                     "VALUES ('db',?,'user','done',1,60,?)",
                     (old, f'/api/query-registry/{old}/analysis/precious'))
        conn.execute("INSERT INTO inbox_item(target,type,subject,state,severity,title,body_json,created_at,updated_at) "
                     "VALUES ('db','latency_regression',?,'new',1,'Slower',?,60,60)",
                     (old, json.dumps({'query_key': old})))
    store._write(references)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA user_version=18')
    conn.commit()
    conn.close()
    return old, hash_sql(text), text


@pytest.mark.parametrize('store_name,phase', [
    ('library', phase) for phase in ('backup-open', 'backup-rename', 'ddl', 'rekey', 'version', 'cache', 'cache-commit')
] + [('monitor', phase) for phase in ('backup-open', 'backup-rename', 'ddl', 'rekey', 'version')])
def test_killed_migration_is_atomic_and_next_start_completes(tmp_path, monkeypatch, store_name, phase):
    if store_name == 'library':
        old, new, text = old_library(tmp_path, monkeypatch)
        current, previous = library_store.SCHEMA_VERSION, 15
    else:
        old, new, text = old_monitor(tmp_path)
        current, previous = monitor_store.SCHEMA_VERSION, 18
    path = tmp_path / (store_name + '.db')
    before = logical(path)
    process = start(tmp_path, 'migrate', store_name, phase)
    try:
        await_file(tmp_path, 'migration-ready', process)
        process.kill()
        process.communicate(timeout=5)
        conn = sqlite3.connect(path)
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        assert version in (previous, current)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.close()
        if version == previous:
            assert logical(path) == before
        if store_name == 'library':
            store = library_store.LibraryStore(path, tmp_path / 'absent.toml')
            store.ensure_open()
            assert set(store.load_all()) == {new}
            assert store.analysis_payload(new, 'precious')['summary'] == 'preserved'
            with ObservationStore(tmp_path / 'cache.db') as cache:
                with cache._read() as conn:
                    assert [tuple(row) for row in conn.execute(
                        'SELECT normalized_hash,calls_delta FROM recent_observation')] == [(new, 17)]
        else:
            monitor_store._OPENED.discard(path)
            store = MonitorStore(path)
            assert store.read('SELECT query_key,calls FROM stmt_series') == [{'query_key': new, 'calls': 17}]
            assert store.read('SELECT query_key FROM query_sample') == [{'query_key': new}]
            assert store.read('SELECT query_key FROM attention_episode') == [{'query_key': new}]
            assert store.read('SELECT query_key,analysis_ref FROM analyze_job') == [
                {'query_key': new, 'analysis_ref': f'/api/query-registry/{new}/analysis/precious'}]
            item = store.read('SELECT subject,body_json FROM inbox_item')[0]
            assert item['subject'] == new and json.loads(item['body_json'])['query_key'] == new
        after = logical(path)
        backup = path.with_name(path.name + f'.pre-v{previous}.bak')
        assert logical(backup) == before
        backup_hash = digest(backup)
        if store_name == 'library':
            library_store.LibraryStore(path, tmp_path / 'absent.toml').ensure_open()
        else:
            MonitorStore(path).ensure_open()
        assert logical(path) == after and digest(backup) == backup_hash
    finally:
        reap(process)


def cap_connections(monkeypatch, store, enabled):
    original = store._connect
    attempts = []
    def connect():
        conn = original()
        if enabled[0]:
            pages = conn.execute('PRAGMA page_count').fetchone()[0]
            conn.execute(f'PRAGMA max_page_count={pages}')
            attempts.append(pages)
        return conn
    monkeypatch.setattr(store, '_connect', connect)
    return attempts


def api_client(monkeypatch, store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from features.monitor.api import routes
    from shared.api.app import register_error_handlers
    monkeypatch.setattr(routes, '_store', lambda: store)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize('operation', ['ingest', 'claim', 'rollup'])
def test_sqlite_full_rolls_back_reports_pauses_and_recovers(tmp_path, monkeypatch, operation):
    path = tmp_path / 'monitor.db'
    store = MonitorStore(path, clock=lambda: NOW)
    store.ensure_open()
    if operation == 'claim':
        store.enqueue_analysis('db', 'q', 'user', 1, NOW)
        store._write(lambda conn: conn.execute(
            "CREATE TRIGGER claim_space AFTER UPDATE ON analyze_job BEGIN "
            "INSERT INTO monitor_setting VALUES ('db','claim_space',zeroblob(1000000)); END"))
        action = lambda: store.claim_analysis('db', NOW)
    elif operation == 'rollup':
        store._write(lambda conn: conn.executemany(
            "INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES ('db',?,60,?,1,2,0,0,0)",
            [(f'q{i}', NOW - 600) for i in range(100)]))
        store._write(lambda conn: conn.execute(
            "CREATE TRIGGER rollup_space AFTER INSERT ON stmt_series WHEN NEW.res=300 BEGIN "
            "INSERT OR REPLACE INTO monitor_setting VALUES ('db','rollup_space',zeroblob(1000000)); END"))
        action = lambda: store.rollup('db', NOW)
    else:
        action = lambda: store.record('db', NOW, [{'query_key': f'q{i}', 'calls': 1,
                                                   'total_ms': 2} for i in range(100)],
                                     {f'q{i}': "SELECT * FROM orders WHERE value='" + 'x' * 7900 + "'" for i in range(100)}, [], None)
    before, raw = logical(path), digest(path)
    enabled = [True]
    attempts = cap_connections(monkeypatch, store, enabled)
    with pytest.raises(StorageError, match='storage full.*Free disk space'):
        action()
    assert logical(path) == before and digest(path) == raw
    response = api_client(monkeypatch, store).get('/targets/db/pulse')
    assert response.status_code == 200, response.text
    assert 'Free disk space' in response.text and response.json()['pulses'][0]['ok'] is False
    count = len(attempts)
    for _ in range(3):
        with pytest.raises(StorageError, match='Free disk space'):
            action()
    assert len(attempts) == count
    enabled[0] = False
    retry_time, _ = monitor_store._STORAGE_ERRORS[path]
    monkeypatch.setattr(monitor_store.time, 'monotonic', lambda: retry_time + 1)
    action()
    assert path not in monitor_store._STORAGE_ERRORS
    assert store.read('PRAGMA integrity_check') == [{'integrity_check': 'ok'}]
    if operation == 'claim':
        assert store.analyze_jobs('db')[0]['state'] == 'running'
    else:
        res = 60 if operation == 'ingest' else 300
        assert store.read('SELECT SUM(calls) n FROM stmt_series WHERE res=?', (res,)) == [{'n': 100}]


@pytest.mark.parametrize('store_name', ['monitor', 'library'])
def test_full_migration_and_backup_preserve_precious_store(tmp_path, monkeypatch, store_name):
    if store_name == 'library':
        old_library(tmp_path, monkeypatch)
        previous = 15
        make = lambda: library_store.LibraryStore(tmp_path / 'library.db', tmp_path / 'absent.toml')
    else:
        old_monitor(tmp_path)
        previous = 18
        make = lambda: MonitorStore(tmp_path / 'monitor.db')
        monitor_store._OPENED.discard(tmp_path / 'monitor.db')
    path = tmp_path / (store_name + '.db')
    before, raw = logical(path), digest(path)
    connect = sqlite3.connect
    def limited_backup(destination, *args, **kwargs):
        conn = connect(destination, *args, **kwargs)
        if str(destination).endswith('.bak.tmp'):
            conn.execute('PRAGMA max_page_count=1')
        return conn
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', limited_backup)
        with pytest.raises(StorageError, match='storage full.*Free disk space'):
            make().ensure_open()
    assert logical(path) == before and digest(path) == raw
    backup = path.with_name(path.name + f'.pre-v{previous}.bak')
    assert not backup.exists()
    store = make()
    enabled = [True]
    cap_connections(monkeypatch, store, enabled)
    if store_name == 'library':
        migrations = library_store._MIGRATIONS[16]
        monkeypatch.setitem(library_store._MIGRATIONS, 16, lambda strict:
                            (*migrations(strict), 'CREATE TABLE fault_space (payload BLOB)',
                             'INSERT INTO fault_space VALUES (zeroblob(1000000))'))
    else:
        original = monitor_store._rekey_identities
        def needs_space(conn):
            original(conn)
            conn.execute("INSERT INTO monitor_setting VALUES ('db','space',zeroblob(1000000))")
        monkeypatch.setattr(monitor_store, '_rekey_identities', needs_space)
    with pytest.raises((StorageError, library_store.LibraryMigrationError), match='full'):
        store.ensure_open()
    assert logical(path) == before and digest(path) == raw
    assert logical(backup) == before
    backup_hash = digest(backup)
    enabled[0] = False
    store.ensure_open()
    assert digest(backup) == backup_hash
    conn = sqlite3.connect(path)
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


@pytest.mark.parametrize('store_name', ['cache', 'library', 'monitor'])
@pytest.mark.parametrize('damage', ['header', 'payload', 'truncate', 'journal', 'wal'])
def test_corrupt_files_rebuild_only_cache_and_leave_cli_usable(tmp_path, monkeypatch, store_name, damage):
    path = tmp_path / (store_name + '.db')
    monkeypatch.setenv('RDST_DATA_DIR', str(tmp_path))
    if store_name == 'cache':
        with ObservationStore(path) as store:
            store.append_event('db', 'saved', 'x' * 20000, created_at=NOW)
        conn = sqlite3.connect(path)
        table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%event%'").fetchone()[0]
        conn.close()
    elif store_name == 'library':
        old_library(tmp_path, monkeypatch)
        library_store.LibraryStore(path, tmp_path / 'absent.toml').ensure_open()
        table = 'query_identity'
        library_store.LibraryStore.invalidate_integrity(path)
    else:
        store = MonitorStore(path)
        store.ensure_open()
        store.record('db', NOW, [], {'q': 'SELECT ' + 'x' * 8000}, [], None)
        table = 'query_text'
        monitor_store._OPENED.discard(path)
    if damage == 'header':
        data = bytearray(path.read_bytes())
        data[:16] = b'broken header!!!?'
        path.write_bytes(data)
    elif damage == 'truncate':
        path.write_bytes(path.read_bytes()[:path.stat().st_size // 2])
    elif damage == 'payload':
        conn = sqlite3.connect(path)
        page = conn.execute('SELECT rootpage FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
        size = conn.execute('PRAGMA page_size').fetchone()[0]
        conn.close()
        data = bytearray(path.read_bytes())
        data[(page - 1) * size:page * size] = bytes(size)
        path.write_bytes(data)
    else:
        Path(str(path) + ('-wal' if damage == 'wal' else '-journal')).write_bytes(b'damaged!' * 100)
    raw = digest(path)
    sidecars = {suffix: Path(str(path) + suffix).read_bytes()
                for suffix in ('-wal', '-journal') if Path(str(path) + suffix).exists()}
    if store_name == 'cache':
        ObservationStore(path).close()
        (aside,) = [p for p in tmp_path.glob('cache.db.corrupt-*')
                    if not p.name.endswith(('-wal', '-shm', '-journal'))]
        assert digest(aside) == raw
        for suffix, content in sidecars.items():
            assert Path(str(aside) + suffix).read_bytes() == content
        conn = sqlite3.connect(path)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.close()
    else:
        with pytest.raises(StorageError, match=r'(?i)restore (?:this file|the file) from (?:a )?backup'):
            if store_name == 'library':
                library_store.LibraryStore(path, tmp_path / 'absent.toml').ensure_open()
            else:
                MonitorStore(path).ensure_open()
        assert digest(path) == raw
        assert not list(tmp_path.glob(path.name + '.corrupt-*'))
        for suffix, content in sidecars.items():
            assert Path(str(path) + suffix).read_bytes() == content
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.api.app import register_error_handlers
        app = FastAPI()
        register_error_handlers(app)
        @app.get('/storage')
        def read_store():
            if store_name == 'library':
                return library_store.LibraryStore(path, tmp_path / 'absent.toml').load_all()
            return MonitorStore(path).read('SELECT * FROM query_text')
        response = TestClient(app).get('/storage')
        assert response.status_code == 503
        assert response.json()['code'] == 'storage_unavailable'
        assert 'Restore this file from a backup' in response.json()['message']
    for command in (['--version'], ['configure', 'list']):
        result = subprocess.run([sys.executable, 'rdst.py', *command], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, RDST_DATA_DIR=str(tmp_path), RDST_TESTING='true'))
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != 'linux', reason='shared SQLite VFS ABI gate on Linux CI')
def test_real_sqlite_full_inside_vacuum_preserves_data_and_recovers(tmp_path, monkeypatch):
    old_library(tmp_path, monkeypatch)
    old_monitor(tmp_path)
    path = tmp_path / 'cache.db'
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.execute('PRAGMA auto_vacuum=0')
    conn.execute('VACUUM')
    conn.close()
    before = {name: digest(tmp_path / name) for name in ('library.db', 'monitor.db')}
    cache = logical(path)
    process = start(tmp_path, 'vacuum')
    try:
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, err
        assert json.loads(out)['recovered'] is True
        assert logical(path) == cache
        assert {name: digest(tmp_path / name) for name in before} == before
    finally:
        reap(process)
