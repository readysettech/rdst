#!/usr/bin/env python3
"""Isolated 100k Docker soak using monitor_scale's workload and observer hooks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

import monitor_scale as scale

ROOT = scale.ROOT
WEB_APP = ROOT.parent / 'web-apps/apps/rdst'
OUTPUT = Path('/tmp/claude-1000/c40')
CAP_REPORT = Path('/tmp/claude-1000/cx/c38.report.md')


def container_names(tag):
    """Owned container names; the tag keeps concurrent runs from colliding."""
    if not tag.replace('-', '').isalnum():
        raise ValueError('Container tag must be alphanumeric with hyphens')
    return 'rdst-scale100k-' + tag + '-pg', 'rdst-scale100k-' + tag + '-mysql'
ENDPOINTS = ('health', 'queries', 'load', 'load/latency', 'settings',
             'analyze-jobs', 'judge-runs', 'index-changes')


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def connect(engine, *, admin=False):
    prefix = 'SCALE100K_' + engine.upper() + '_'
    kwargs = dict(host='127.0.0.1', port=int(os.environ[prefix + 'PORT']),
                  user='postgres' if engine == 'pg' else 'root',
                  password=os.environ[prefix + 'PASSWORD'], connect_timeout=10)
    database = ('postgres' if engine == 'pg' else 'mysql') if admin else scale.DATABASE
    if engine == 'pg':
        import psycopg2
        conn = psycopg2.connect(**kwargs, dbname=database, sslmode='disable',
                                application_name='rdst-scale100k-driver')
        conn.autocommit = True
        return conn
    import pymysql
    return pymysql.connect(**kwargs, database=database, autocommit=True,
                           read_timeout=30, write_timeout=30)


def docker(args, *, env=None):
    result = subprocess.run(['docker', *args], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Docker command failed: ' + args[0])
    return result.stdout.strip()


def browser_probe(port, output):
    javascript = r'''
const {chromium}=require('@playwright/test');
(async()=>{
 const browser=await chromium.launch({headless:true});const results=[];
 try {
  for(const target of ['scale-pg','scale-mysql']) {
   const page=await browser.newPage();let requests=0,api=0,errors=[],http_errors=[];
   page.on('request',r=>{requests++;if(r.url().includes('/api/'))api++});
   page.on('pageerror',e=>errors.push(e.message));
   page.on('response',r=>{if(r.status()>=500&&r.url().includes('/api/'))http_errors.push({status:r.status(),path:new URL(r.url()).pathname})});
   const start=Date.now();let ready_ms=null;
   const ready=page.waitForResponse(r=>r.url().includes('/api/monitor/targets/'+target+'/queries')&&r.status()===200,{timeout:30000}).then(()=>{ready_ms=Date.now()-start}).catch(()=>{});
   await page.goto('http://127.0.0.1:'+process.argv[1]+'/database?target='+target,{waitUntil:'domcontentloaded'});
   await ready;await page.waitForTimeout(5000);
   const navigation=await page.evaluate(()=>({url:location.pathname,timing:performance.getEntriesByType('navigation')[0].toJSON(),text:document.body.innerText.slice(0,500)}));
   results.push({target,ready_ms,wall_ms:Date.now()-start,requests,api,errors,http_errors,...navigation});
   await page.close();
  }
 }finally{await browser.close()}
 console.log(JSON.stringify(results));
})().catch(e=>{console.error(e.name);process.exit(1)});
'''
    try:
        result = subprocess.run(['nice', '-n', '10', 'node', '-e', javascript, str(port)],
                                cwd=WEB_APP, capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=85)
        output.write_text(result.stdout)
        return result.returncode
    except subprocess.TimeoutExpired:
        return 124


def record_browser_probe(port, output, browser_path):
    scale.emit(output / "samples.jsonl", "browser",
               returncode=browser_probe(port, browser_path), browser_file=browser_path.name)


def setup_engine(engine):
    for _ in range(180):
        try:
            conn = connect(engine, admin=True)
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError(engine + ' startup timed out')
    try:
        scale.query(conn, 'CREATE DATABASE rdst_scale')
    finally:
        conn.close()
    conn = connect(engine)
    try:
        if engine == 'pg':
            scale.query(conn, 'CREATE EXTENSION pg_stat_statements')
            settings = scale.query(conn, "SELECT name,setting,min_val,max_val FROM pg_settings WHERE name IN ('shared_preload_libraries','pg_stat_statements.max','pg_stat_statements.track') ORDER BY name")
            version = scale.query(conn, 'SELECT version()')[0][0]
        else:
            settings = scale.query(conn, "SHOW VARIABLES WHERE Variable_name IN ('performance_schema','performance_schema_digests_size','max_digest_length','performance_schema_max_digest_length')")
            version = scale.query(conn, 'SELECT version()')[0][0]
        columns = ','.join(f'c{bit} INTEGER NOT NULL' for bit in range(20))
        scale.query(conn, f'CREATE TABLE scale_rows (id INTEGER PRIMARY KEY,{columns})')
        values = ','.join('(' + ','.join([str(i), *(str((i + bit) % 100) for bit in range(20))]) + ')' for i in range(1, 65))
        scale.query(conn, 'INSERT INTO scale_rows VALUES ' + values)
        return dict(version=version, settings=settings, source=scale.source_counts(engine, conn))
    finally:
        conn.close()


def extra_counts(data):
    result = {}
    with sqlite3.connect(f'file:{data}/monitor.db?mode=ro', uri=True, timeout=20) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(tables):
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if 'target' in columns and any(word in table for word in ('identity', 'alias', 'baseline', 'text', 'tier')):
                result[table] = dict(conn.execute(f'SELECT target,count(*) FROM "{table}" GROUP BY target').fetchall())
    return result


def write_qualification(output):
    """Write the measured verdict and return a failing exit code for failed gates."""
    failures = set()
    api_times = {}
    collector_times = {}
    peak_rss = resource_count = browser_count = lock_count = slow_locks = retries = 0

    def records(name):
        path = output / name
        if not path.exists():
            failures.add('Missing ' + name)
            return
        with path.open() as stream:
            for line in stream:
                yield json.loads(line)

    for row in records('samples.jsonl'):
        kind = row['kind']
        if kind == 'api_error':
            failures.add('API request failed')
        elif kind == 'api':
            api_times.setdefault((row['target'], row['endpoint']), []).append(row['ms'])
            if row['status'] != 200:
                failures.add('API request failed')
        elif kind == 'resources':
            resource_count += 1
            peak_rss = max(peak_rss, row['rss_bytes'])
            if row.get('source_errors') or row.get('local_error'):
                failures.add('Resource or source sampling failed')
            for local in row.get('local', {}).values():
                if local['tracked'] > 10000 or local['texts'] > 10000 or local['full_detail'] > 1000:
                    failures.add('Query tier limit exceeded')
        elif kind == 'browser':
            browser_count += 1
            if row['returncode']:
                failures.add('Browser probe failed')
                continue
            path = output / row['browser_file']
            if not path.exists():
                failures.add('Missing browser artifact')
                continue
            pages = json.loads(path.read_text())
            if {page['target'] for page in pages} != set(scale.TARGETS.values()):
                failures.add('Missing browser target')
            for page in pages:
                if page['errors']:
                    failures.add('Browser script error')
                if page.get('http_errors'):
                    failures.add('Browser API request failed')
                if page['ready_ms'] is None or page['ready_ms'] > 10000:
                    failures.add('Database render exceeds 10s')
    for row in records('backend-metrics.jsonl'):
        kind = row['kind']
        if kind == 'collect':
            collector_times.setdefault(row['target'], []).append(row['duration_ms'])
            if row['event'] == 'discovery_error':
                failures.add('Collector failed')
        elif kind == 'collect_error':
            failures.add('Collector failed')
        elif kind == 'ai_attempt':
            failures.add('AI attempted while signed out')
        elif kind == 'sqlite_locked':
            retries += 1
        elif kind == 'sqlite_lock':
            lock_count += 1
            slow_locks += row['duration_ms'] > 250
    if not resource_count or not browser_count:
        failures.add('Missing resource or browser samples')
    if peak_rss > 1024**3:
        failures.add('Backend RSS exceeds 1 GiB')
    for target in scale.TARGETS.values():
        for endpoint in ENDPOINTS:
            values = api_times.get((target, endpoint), [])
            if not values or scale.percentile(values, .95) > 2000:
                failures.add('API endpoint p95 exceeds 2s or samples missing')
        values = collector_times.get(target, [])
        if not values or scale.percentile(values, .95) > 60000:
            failures.add('Collector p95 exceeds 60s or samples missing')
    if not lock_count or slow_locks > lock_count * .05 or retries > lock_count * .01:
        failures.add('SQLite p95 exceeds 250ms or retry rate exceeds 1%')
    for engine in scale.ENGINES:
        seeded = False
        for row in records(engine + '-load.jsonl'):
            seeded |= row['kind'] == 'seeded'
            if row['kind'] == 'load_error':
                failures.add(engine + ' load failure')
        if not seeded:
            failures.add(engine + ' did not seed all shapes')
    summary = {'verdict': 'FAIL' if failures else 'PASS', 'failures': sorted(failures),
               'max_rss_bytes': peak_rss, 'resource_samples': resource_count,
               'browser_probes': browser_count}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    return 2 if failures else 0


def run(args):
    output = args.output.resolve()
    root = args.root.resolve()
    if not output.is_relative_to(root) or output == root:
        raise ValueError('Use a fresh child directory of ' + str(root))
    pg_name, mysql_name = container_names(args.tag)
    global WEB_APP
    dist_dir = Path(os.environ.get('RDST_WEB_DIST_DIR') or WEB_APP / 'dist')
    WEB_APP = dist_dir.parent
    output.mkdir(parents=True, exist_ok=False)
    data = output / 'data'
    data.mkdir()
    ports = {engine: free_port() for engine in scale.ENGINES}
    port = free_port()
    if len(set([*ports.values(), port])) != 3:
        raise RuntimeError('Port reservation collision')
    environment = dict(os.environ)
    for key in list(environment):
        if any(word in key for word in ('ANTHROPIC', 'OPENAI', 'RDST_ACCOUNT', 'LT_', 'DIGITALOCEAN')):
            del environment[key]
    for engine in scale.ENGINES:
        prefix = 'SCALE100K_' + engine.upper() + '_'
        environment[prefix + 'PORT'] = str(ports[engine])
        environment[prefix + 'PASSWORD'] = os.urandom(24).hex()
    environment.update(RDST_DATA_DIR=str(data), XDG_DATA_HOME=str(output / 'xdg'),
                       PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring', RDST_TELEMETRY='0',
                       RDST_SCALE_OUTPUT=str(output), RDST_OBSERVATION_SCHEDULER='1',
                       RDST_WEB_DIST_DIR=str(dist_dir))
    os.environ.clear()
    os.environ.update(environment)
    scale.connect = connect
    stop = threading.Event()
    threads = []
    backend = None
    created = []
    setup = dict(started=time.time(), ports=ports, backend_port=port, shapes=100000,
                 qps=args.qps, engines={}, container_names=(pg_name, mysql_name))
    dist = dist_dir / 'index.html'
    setup['dist_sha256'] = hashlib.sha256(dist.read_bytes()).hexdigest()
    try:
        # Only names created successfully by this invocation enter cleanup.
        pg_env = dict(environment, POSTGRES_PASSWORD=environment['SCALE100K_PG_PASSWORD'])
        docker(['run', '-d', '--name', pg_name, '--cpus', '0.75', '--memory', '1g',
                '-p', f'127.0.0.1:{ports["pg"]}:5432', '-e', 'POSTGRES_PASSWORD',
                'postgres:16', '-c', 'shared_preload_libraries=pg_stat_statements',
                '-c', 'pg_stat_statements.max=100000', '-c', 'pg_stat_statements.track=all'], env=pg_env)
        created.append(pg_name)
        mysql_env = dict(environment, MYSQL_ROOT_PASSWORD=environment['SCALE100K_MYSQL_PASSWORD'], MYSQL_ROOT_HOST='%')
        docker(['run', '-d', '--name', mysql_name, '--cpus', '0.75', '--memory', '2g',
                '-p', f'127.0.0.1:{ports["mysql"]}:3306', '-e', 'MYSQL_ROOT_PASSWORD', '-e', 'MYSQL_ROOT_HOST',
                'mysql:8.0', '--performance-schema=ON', '--performance-schema-digests-size=110000',
                '--max-digest-length=2048', '--performance-schema-max-digest-length=2048',
                '--innodb-buffer-pool-size=128M'], env=mysql_env)
        created.append(mysql_name)
        for engine in scale.ENGINES:
            setup['engines'][engine] = setup_engine(engine)
            (output / 'setup.json').write_text(json.dumps(setup, indent=2, default=str))
            thread = threading.Thread(target=scale.workload, args=(engine, 100000, args.qps, stop, output / (engine + '-load.jsonl')),
                                      kwargs={'seed_qps': args.seed_qps})
            thread.start()
            threads.append(thread)
        (output / 'state.json').write_text(json.dumps({'phase': 'waiting_for_cap_report', 'ts': time.time()}))
        while not CAP_REPORT.exists():
            if time.time() - setup['started'] > 3 * 3600:
                raise RuntimeError('Cap report missing after three hours; backend was never started')
            stop.wait(10)
        setup['cap_report_sha256'] = hashlib.sha256(CAP_REPORT.read_bytes()).hexdigest()
        setup['cap_report_seen'] = time.time()
        sys.path.insert(0, str(ROOT))
        from shared.config.targets import TargetsConfig
        from shared.monitor.monitor_store import MonitorStore
        cfg = TargetsConfig()
        cfg.load()
        for engine in scale.ENGINES:
            cfg.upsert(scale.TARGETS[engine], dict(engine='postgresql' if engine == 'pg' else 'mysql',
                       host='127.0.0.1', port=ports[engine], user='postgres' if engine == 'pg' else 'root',
                       database=scale.DATABASE, password_env='SCALE100K_' + engine.upper() + '_PASSWORD',
                       tls=False, read_only=True))
        cfg.set_default('scale-pg')
        cfg.mark_init_completed()
        cfg.save()
        store = MonitorStore()
        for target in scale.TARGETS.values():
            store.set_settings(target, {'enabled': True, 'deep_analysis': False, 'nightly_report': False})
        extra = ''
        if args.observer:
            observer = args.observer.resolve()
            extra = (f'sys.path.insert(0,{str(observer.parent)!r});import {observer.stem};'
                     f'{observer.stem}.install();')
        entry = "import sys,runpy;sys.path.insert(0,'scripts');import scale_backend_observer;scale_backend_observer.install();" + extra + "sys.argv=['rdst.py','web','--no-browser','--ui','dist','--host','127.0.0.1','--port'," + repr(str(port)) + "];runpy.run_path('rdst.py',run_name='__main__')"
        with (output / 'backend.log').open('w') as log:
            backend = subprocess.Popen(['nice', '-n', '10', str(ROOT / '.venv/bin/python'), '-c', entry],
                                       cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        (output / 'backend.pid').write_text(str(backend.pid))
        setup['backend_started'] = time.time()
        setup['backend_pid'] = backend.pid
        (output / 'setup.json').write_text(json.dumps(setup, indent=2, default=str))
        for _ in range(120):
            if backend.poll() is not None:
                raise RuntimeError('Backend exited during startup')
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=2).close()
                break
            except Exception:
                stop.wait(1)
        else:
            raise RuntimeError('Backend startup timed out')
        started = time.time()
        last_cpu = None
        last_browser = 0
        seeded_at = {}
        fields = ['ts', 'elapsed_s', 'engine', 'cpu_pct', 'rss_bytes', 'monitor_bytes', 'source_digests',
                  'global_entries', 'dealloc', 'digest_lost', 'texts', 'tracked', 'full_detail',
                  *[endpoint.replace('/', '_') + '_ms' for endpoint in ENDPOINTS]]
        with (output / 'samples.csv').open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            while True:
                now = time.time()
                for engine in scale.ENGINES:
                    if engine not in seeded_at:
                        loads = [json.loads(line) for line in (output / (engine + '-load.jsonl')).read_text().splitlines()] if (output / (engine + '-load.jsonl')).exists() else []
                        seed = next((row for row in loads if row['kind'] == 'seeded'), None)
                        if seed:
                            seeded_at[engine] = seed['ts']
                deadline = max(started + args.hours * 3600,
                               max(seeded_at.values(), default=now) + args.hours * 3600)
                if len(seeded_at) == 2 and now >= deadline:
                    break
                if now - started >= 6 * 3600:
                    raise RuntimeError('Six-hour safety deadline reached')
                if backend.poll() is not None:
                    raise RuntimeError('Backend exited during soak')
                stat = Path(f'/proc/{backend.pid}/stat').read_text().split()
                cpu = (int(stat[13]) + int(stat[14])) / os.sysconf('SC_CLK_TCK')
                sample = dict(elapsed_s=now - started, rss_bytes=int(stat[23]) * os.sysconf('SC_PAGE_SIZE'),
                              cpu_pct=(cpu - last_cpu[1]) / (now - last_cpu[0]) * 100 if last_cpu else None,
                              files={p.name: p.stat().st_size for p in data.iterdir() if p.is_file()})
                last_cpu = (now, cpu)
                try:
                    sample['local'] = scale.local_counts(data)
                    sample['identity_tables'] = extra_counts(data)
                except Exception as exc:
                    sample['local_error'] = type(exc).__name__
                for engine in scale.ENGINES:
                    try:
                        conn = connect(engine)
                        try:
                            sample.setdefault('source', {})[engine] = scale.source_counts(engine, conn)
                        finally:
                            conn.close()
                    except Exception as exc:
                        sample.setdefault('source_errors', {})[engine] = type(exc).__name__
                    local = sample.get('local', {}).get(engine, {})
                    source = sample.get('source', {}).get(engine, {})
                    row = dict(ts=now, elapsed_s=sample['elapsed_s'], engine=engine, cpu_pct=sample['cpu_pct'],
                               rss_bytes=sample['rss_bytes'], monitor_bytes=sample['files'].get('monitor.db'),
                               source_digests=source.get('digests'), global_entries=source.get('global_entries'),
                               dealloc=source.get('dealloc'), digest_lost=source.get('digest_lost'),
                               texts=local.get('texts'), tracked=local.get('tracked'), full_detail=local.get('full_detail'))
                    for endpoint in ENDPOINTS:
                        url = f'http://127.0.0.1:{port}/api/monitor/targets/{scale.TARGETS[engine]}/{endpoint}'
                        if endpoint == 'load/latency':
                            url += '?keys=' + local.get('sample_key', 'missing')
                        begin = time.monotonic()
                        try:
                            with urllib.request.urlopen(url, timeout=30) as response:
                                body = response.read()
                                ms = (time.monotonic() - begin) * 1000
                                row[endpoint.replace('/', '_') + '_ms'] = ms
                                scale.emit(output / 'samples.jsonl', 'api', target=scale.TARGETS[engine], endpoint=endpoint,
                                           status=response.status, ms=ms, bytes=len(body))
                        except Exception as exc:
                            scale.emit(output / 'samples.jsonl', 'api_error', target=scale.TARGETS[engine],
                                       endpoint=endpoint, error_type=type(exc).__name__, ms=(time.monotonic() - begin) * 1000)
                    writer.writerow(row)
                    stream.flush()
                scale.emit(output / 'samples.jsonl', 'resources', **sample)
                (output / 'state.json').write_text(json.dumps({'phase': 'observing', 'ts': now,
                    'elapsed_s': now - started, 'seeded_at': seeded_at, 'deadline': deadline}))
                if now - last_browser >= 600:
                    browser_path = output / ('browser-' + str(int(now)) + '.json')
                    record_browser_probe(port, output, browser_path)
                    last_browser = now
                stop.wait(min(120, max(0, deadline - time.time())))
        setup['finished'] = time.time()
        setup['seeded_at'] = seeded_at
        setup['elapsed_s'] = time.time() - started
        setup['dist_end_sha256'] = hashlib.sha256(dist.read_bytes()).hexdigest()
        (output / 'setup.json').write_text(json.dumps(setup, indent=2, default=str))
        (output / 'state.json').write_text(json.dumps({'phase': 'completed', 'ts': time.time()}))
        return write_qualification(output)
    except BaseException as exc:
        (output / 'failure.json').write_text(json.dumps({'error_type': type(exc).__name__, 'message': str(exc), 'ts': time.time()}))
        raise
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=45)
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=30)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=10)
        cleanup = {name: docker(['rm', '-f', name]) for name in created}
        (output / 'cleanup.json').write_text(json.dumps(cleanup, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT / 'run')
    parser.add_argument('--root', type=Path, default=OUTPUT,
                        help='Directory that must contain --output')
    parser.add_argument('--tag', default='c40', help='Container name tag')
    parser.add_argument('--hours', type=float, default=3)
    parser.add_argument('--qps', type=float, default=40)
    parser.add_argument('--observer', type=Path, default=None,
                        help='Extra profiling module whose install() runs inside the backend')
    parser.add_argument('--seed-qps', type=float, default=None,
                        help='Admission rate for the initial 100k shapes (default: --qps)')
    args = parser.parse_args()
    if not args.run:
        print('SKIP: pass --run to create isolated Docker containers and run the soak')
        return 0
    if not 1.5 <= args.hours <= 5 or not 0 < args.qps <= 40:
        parser.error('Require 1.5..5 hours and 0 < qps <= 40')
    if args.seed_qps is not None and not 0 < args.seed_qps <= 2000:
        parser.error('Require 0 < seed-qps <= 2000')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
