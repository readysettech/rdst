#!/usr/bin/env python3
"""Opt-in managed-database monitoring soak. Run with the RDST virtualenv and nice 10.

Default execution skips. Source ~/.rdst-longtest/env.sh, then pass --run. Only the
literal rdst_scale database is created and dropped. Existing databases cause refusal.
Cluster settings and the existing backend stay in place. Native digest capacity is
shared across databases, and MySQL keeps its digest rows after database teardown.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = 'rdst_scale'
ENGINES = ('pg', 'mysql')
TARGETS = {engine: 'scale-' + engine for engine in ENGINES}


def statement(index: int) -> str:
    """Column subsets survive literal normalization; filters, joins and sorts vary."""
    if not 0 <= index < 100000:
        raise ValueError('Statement index must be between 0 and 99999')
    columns = ','.join(f'a.c{bit}' for bit in range(20) if (index+1) & (1 << bit))
    join = ' JOIN scale_rows b ON b.id=a.id' if index % 3 == 0 else ''
    predicate = f'a.c{index % 20} >= 0' if index % 2 else f'a.c{index % 20} BETWEEN 0 AND 100'
    order = f' ORDER BY a.c{(index // 3) % 20}' if index % 4 else ' ORDER BY a.id DESC'
    return f'SELECT {columns} FROM scale_rows a{join} WHERE a.id<=64 AND {predicate}{order} LIMIT 8'


def connect(engine, *, admin=False):
    prefix = 'LT_' + engine.upper() + '_'
    kwargs = dict(host=os.environ[prefix+'HOST'], port=int(os.environ[prefix+'PORT']),
                  user=os.environ[prefix+'ADMIN_USER'], password=os.environ[prefix+'ADMIN_PASSWORD'],
                  connect_timeout=10)
    database = os.environ[prefix+'DB'] if admin else DATABASE
    if engine == 'pg':
        import psycopg2
        connection = psycopg2.connect(**kwargs, dbname=database, sslmode='require',
                                      application_name='rdst-scale-driver')
        connection.autocommit = True
        return connection
    import pymysql
    return pymysql.connect(**kwargs, database=database, autocommit=True, read_timeout=20,
                           write_timeout=20, ssl={'ca': str(Path.home()/'.rdst-longtest/ca-mysql.crt')})


def query(connection, sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def source_counts(engine, connection):
    if engine == 'pg':
        return dict(digests=query(connection, 'SELECT count(DISTINCT queryid) FROM pg_stat_statements '
                                 'WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())')[0][0],
                    global_entries=query(connection, 'SELECT count(*) FROM pg_stat_statements')[0][0],
                    dealloc=query(connection, 'SELECT dealloc FROM pg_stat_statements_info')[0][0])
    return dict(digests=query(connection, 'SELECT count(DISTINCT DIGEST) FROM '
                              'performance_schema.events_statements_summary_by_digest '
                              "WHERE SCHEMA_NAME='rdst_scale' AND DIGEST IS NOT NULL")[0][0],
                global_entries=query(connection, 'SELECT count(*) FROM '
                                      'performance_schema.events_statements_summary_by_digest')[0][0],
                digest_lost=int(query(connection, "SHOW GLOBAL STATUS LIKE 'Performance_schema_digest_lost'")[0][1]),
                overflow_calls=query(connection, 'SELECT COALESCE(SUM(COUNT_STAR),0) FROM '
                                     'performance_schema.events_statements_summary_by_digest WHERE DIGEST IS NULL')[0][0])


def drop_scale_database(engine):
    """Remove the owned logical database, including when client connections time out."""
    try:
        admin = connect(engine, admin=True)
        try:
            query(admin, 'DROP DATABASE rdst_scale')
        finally:
            admin.close()
        return 'dropped rdst_scale via SQL'
    except Exception:
        cluster = os.environ['LT_' + engine.upper() + '_ID']
        request = urllib.request.Request(
            'https://api.digitalocean.com/v2/databases/' + cluster + '/dbs/rdst_scale',
            method='DELETE',
            headers={'Authorization': 'Bearer ' + os.environ['DIGITALOCEAN_TOKEN']},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                raise RuntimeError('Logical database deletion did not return HTTP 204')
        return 'dropped rdst_scale via logical database API'


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values)*fraction)-1)] if values else None


def emit(path, kind, **values):
    with path.open('a') as stream:
        stream.write(json.dumps({'ts': time.time(), 'kind': kind, **values}, default=str) + '\n')


def workload(engine, count, qps, stop, path, seed_qps=None):
    rng = random.Random(8721)
    attempted = 0
    seeded = False
    while not stop.is_set():
        try:
            connection = connect(engine)
            try:
                # Admit the long tail once, then replay it with a small hot working set.
                while not stop.is_set():
                    start = time.monotonic()
                    index = attempted if not seeded else (rng.randrange(20) if rng.random()<0.7 else rng.randrange(count))
                    query(connection, statement(index))
                    attempted += 1
                    if not seeded and attempted == count:
                        seeded = True
                        emit(path, 'seeded', engine=engine, statements=count, source=source_counts(engine, connection))
                    if attempted % 1000 == 0:
                        emit(path, 'load', engine=engine, executions=attempted, seeded=seeded)
                    rate = qps if seeded or not seed_qps else seed_qps
                    stop.wait(max(0, 1/rate - (time.monotonic()-start)))
            finally:
                connection.close()
        except Exception as exc:
            # Drivers can include connection credentials in exception messages.
            emit(path, 'load_error', engine=engine, error_type=type(exc).__name__)
            stop.wait(10)


def local_counts(data):
    result = {}
    with sqlite3.connect(f'file:{data}/monitor.db?mode=ro', uri=True, timeout=10) as connection:
        for engine, target in TARGETS.items():
            result[engine] = {
                'sample_key': (connection.execute('SELECT query_key FROM query_tier WHERE target=? ORDER BY rank LIMIT 1', (target,)).fetchone() or ['missing'])[0],
                'recorded_calls': connection.execute('SELECT COALESCE(sum(calls),0) FROM db_series WHERE target=? AND res=60', (target,)).fetchone()[0],
                'texts': connection.execute('SELECT count(*) FROM query_text WHERE target=?', (target,)).fetchone()[0],
                'tracked': connection.execute('SELECT count(*) FROM query_tier WHERE target=?', (target,)).fetchone()[0],
                'full_detail': connection.execute('SELECT count(*) FROM query_tier WHERE target=? AND rank<=1000', (target,)).fetchone()[0],
                'series': connection.execute('SELECT res,count(*),count(DISTINCT query_key) FROM stmt_series '
                                             'WHERE target=? GROUP BY res', (target,)).fetchall(),
                'pulse': connection.execute('SELECT kind,ok,count(*),max(ts) FROM monitor_pulse '
                                            'WHERE target=? GROUP BY kind,ok', (target,)).fetchall(),
            }
    return result


def browser_probe(output):
    # Use the existing Playwright installation and already-built UI.
    javascript = r'''
const { chromium } = require('@playwright/test');
(async () => {
 const browser = await chromium.launch({headless:true});
 const results=[];
 try {
  for (const target of ['scale-pg','scale-mysql']) {
   for(let n=0;n<4;n++) {
    const gateOverride = n > 0;
    const page=await browser.newPage();let requests=0;let api=0;let errors=[];
    page.on('request', r=>{requests++;if(r.url().includes('/api/')) api++});
    page.on('pageerror', e=>errors.push(e.message));
    if(gateOverride) {
     await page.route('**/api/env/requirements',async route=>{
      const response=await route.fetch();const body=await response.json();
      body.requirements=body.requirements.map(r=>r.kind==='anthropic_api_key'
        ? {...r,satisfied:true,source:'readyset_account'} : r);
      await route.fulfill({response,json:body});
     });
    }
    const start=Date.now();
    let readyMs=null;
    const ready=gateOverride ? page.waitForResponse(r=>r.url().includes('/api/monitor/targets/'+target+'/queries') && r.status()===200,{timeout:30000}) : null;
    await page.goto('http://127.0.0.1:8860/database?target='+target,{waitUntil:'domcontentloaded'});
    if(ready) {await ready;await page.waitForTimeout(250);readyMs=Date.now()-start;}
    await page.waitForTimeout(5000);
    const navigation=await page.evaluate(()=>({url:location.pathname,
       timing:performance.getEntriesByType('navigation')[0].toJSON(),
       text:document.body.innerText.slice(0,500)}));
    results.push({target,gate_override:gateOverride,ready_ms:readyMs,wall_ms:Date.now()-start,requests,api,errors,...navigation});
    await page.screenshot({path:process.argv[1]+'/'+target+'-'+n+'.png'});
    await page.close();
   }
  }
 } finally {await browser.close();}
 console.log(JSON.stringify(results));
})().catch(e=>{console.error(e.name);process.exit(1)});
'''
    try:
        completed = subprocess.run(['nice','-n','10','node','-e',javascript,str(output)],
                  cwd=ROOT.parent/'web-apps/apps/rdst', capture_output=True, text=True, timeout=90)
        (output/'browser.json').write_text(completed.stdout)
        return completed.returncode
    except subprocess.TimeoutExpired:
        return 124


def run(args):
    output = args.output.resolve()
    if not output.is_relative_to(Path('/tmp')) or output == Path('/tmp'):
        raise ValueError('Scale output must be a dedicated directory under /tmp')
    data = output/'data'
    if data.exists():
        raise RuntimeError('Output data directory already exists; choose a fresh directory')
    output.mkdir(parents=True, exist_ok=True)
    data.mkdir()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1',8860))
    os.environ.update(RDST_DATA_DIR=str(data), XDG_DATA_HOME=str(output/'xdg'),
                      PYTHON_KEYRING_BACKEND='keyring.backends.null.Keyring', RDST_TELEMETRY='0')
    for key in list(os.environ):
        if any(word in key for word in ('ANTHROPIC','OPENAI','RDST_ACCOUNT')):
            del os.environ[key]
    sys.path.insert(0, str(ROOT))
    from shared.config.targets import TargetsConfig
    from shared.monitor.monitor_store import MonitorStore
    cfg = TargetsConfig()
    cfg.load()
    created = []
    backend = None
    stop = threading.Event()
    threads = []
    path = output/'samples.jsonl'
    initial = {'started': time.time(), 'requested_shapes': args.shapes, 'minutes': args.minutes,
               'qps_per_engine': args.qps, 'ceilings': {}}
    try:
        for engine in ENGINES:
            admin = connect(engine, admin=True)
            try:
                exists = query(admin, "SELECT 1 FROM pg_database WHERE datname='rdst_scale'" if engine=='pg'
                               else "SELECT 1 FROM information_schema.schemata WHERE schema_name='rdst_scale'")
                if exists:
                    raise RuntimeError('rdst_scale already exists; refusing to reuse or delete it')
                ceiling = int(query(admin, 'SHOW pg_stat_statements.max')[0][0]) if engine=='pg' else int(
                    query(admin, "SHOW VARIABLES LIKE 'performance_schema_digests_size'")[0][1])
                initial['ceilings'][engine] = ceiling
                query(admin, 'CREATE DATABASE rdst_scale')
                created.append(engine)
            finally:
                admin.close()
            connection = connect(engine)
            try:
                if engine == 'pg':
                    query(connection,'CREATE EXTENSION IF NOT EXISTS pg_stat_statements')
                    query(connection,"SET statement_timeout='3s'")
                columns = ','.join(f'c{bit} INTEGER NOT NULL' for bit in range(20))
                query(connection, f'CREATE TABLE scale_rows (id INTEGER PRIMARY KEY,{columns})')
                values = ','.join('('+','.join([str(i),*(str((i+bit)%100) for bit in range(20))])+')' for i in range(1,65))
                query(connection, 'INSERT INTO scale_rows VALUES '+values)
                initial.setdefault('source_before', {})[engine] = source_counts(engine, connection)
            finally:
                connection.close()
            prefix = 'LT_' + engine.upper() + '_'
            cfg.upsert(TARGETS[engine], dict(engine='postgresql' if engine=='pg' else 'mysql',
                       host=os.environ[prefix+'HOST'], port=int(os.environ[prefix+'PORT']),
                       user=os.environ[prefix+'ADMIN_USER'], database=DATABASE,
                       password_env=prefix+'ADMIN_PASSWORD', tls=True, tls_verify=True,
                       tls_ca=str(Path.home()/f'.rdst-longtest/ca-{engine}.crt'),read_only=True))
        cfg.set_default('scale-pg')
        cfg.mark_init_completed()
        cfg.save()
        store = MonitorStore()
        for target in TARGETS.values():
            store.set_settings(target, {'enabled':True, 'deep_analysis':False, 'nightly_report':False})
        (output/'setup.json').write_text(json.dumps(initial, indent=2, default=str))
        environment = dict(os.environ, RDST_SCALE_OUTPUT=str(output), RDST_OBSERVATION_SCHEDULER='1',
            RDST_WEB_DIST_DIR=str(ROOT.parent/'web-apps/apps/rdst/dist'))
        entry = "import sys,runpy;sys.path.insert(0,'scripts');import scale_backend_observer;scale_backend_observer.install();sys.argv=['rdst.py','web','--no-browser','--ui','dist','--host','127.0.0.1','--port','8860'];runpy.run_path('rdst.py',run_name='__main__')"
        with (output/'backend.log').open('w') as log:
            backend = subprocess.Popen(['nice','-n','10',str(ROOT/'.venv/bin/python'),'-c',entry],
                            cwd=ROOT,env=environment,stdout=log,stderr=log,stdin=subprocess.DEVNULL)
        (output/'backend.pid').write_text(str(backend.pid))
        for _ in range(120):
            if backend.poll() is not None:
                raise RuntimeError('Scale backend exited during startup; inspect private log')
            try:
                urllib.request.urlopen('http://127.0.0.1:8860/api/status', timeout=2).close()
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('Scale backend did not become ready')
        # At least 10k attempted shapes, with a second phase only if the source allows it.
        for engine in ENGINES:
            count = max(10000, min(args.shapes,initial['ceilings'][engine]))
            thread = threading.Thread(target=workload,args=(engine,count,args.qps,stop,output/(engine+'-load.jsonl')))
            thread.start()
            threads.append(thread)
        started = time.time()
        seed_seconds = max(max(10000, min(args.shapes, ceiling))
                           for ceiling in initial['ceilings'].values()) / args.qps
        duration_seconds = max(args.minutes*60, seed_seconds + 50*60)
        deadline = started + duration_seconds
        emit(path, 'soak_started', duration_seconds=duration_seconds)
        last_cpu = None
        while time.time()<deadline:
            if backend.poll() is not None:
                raise RuntimeError('Scale backend exited during soak')
            now = time.time()
            stat = Path(f'/proc/{backend.pid}/stat').read_text().split()
            cpu = (int(stat[13])+int(stat[14]))/os.sysconf('SC_CLK_TCK')
            rss = int(stat[23])*os.sysconf('SC_PAGE_SIZE')
            sample = dict(elapsed_s=now-started, rss_bytes=rss, cpu_seconds=cpu,
                cpu_pct=(cpu-last_cpu[1])/(now-last_cpu[0])*100 if last_cpu else None,
                files={p.name:p.stat().st_size for p in data.iterdir() if p.is_file()})
            last_cpu = (now,cpu)
            try:
                sample['local'] = local_counts(data)
            except Exception as exc:
                sample['local_error'] = type(exc).__name__
            for engine in ENGINES:
                try:
                    connection=connect(engine)
                    try:
                        sample.setdefault('source',{})[engine]=source_counts(engine,connection)
                    finally:
                        connection.close()
                except Exception as exc:
                    sample.setdefault('source_errors',{})[engine]=type(exc).__name__
            emit(path,'resources',**sample)
            for target in TARGETS.values():
                for endpoint in ('health','queries','load','load/latency','settings','analyze-jobs','judge-runs','index-changes'):
                    url=f'http://127.0.0.1:8860/api/monitor/targets/{target}/{endpoint}'
                    if endpoint == 'load/latency':
                        engine = 'pg' if target == 'scale-pg' else 'mysql'
                        url += '?keys=' + sample.get('local', {}).get(engine, {}).get('sample_key', 'missing')
                    request_start=time.monotonic()
                    try:
                        with urllib.request.urlopen(url,timeout=15) as response:
                            body=response.read()
                            emit(path,'api',target=target,endpoint=endpoint,status=response.status,
                                 ms=(time.monotonic()-request_start)*1000,bytes=len(body))
                    except Exception as exc:
                        emit(path,'api_error',target=target,endpoint=endpoint,error_type=type(exc).__name__,
                             ms=(time.monotonic()-request_start)*1000)
            stop.wait(min(30,max(0,deadline-time.time())))
        browser_return = browser_probe(output)
        emit(path,'browser',returncode=browser_return)
        records=[json.loads(line) for line in path.read_text().splitlines()]
        observations=[json.loads(line) for line in (output/'backend-metrics.jsonl').read_text().splitlines()]
        resources=[row for row in records if row['kind']=='resources']
        apis=[row['ms'] for row in records if row['kind']=='api']
        collects=[row['duration_ms'] for row in observations if row['kind']=='collect']
        failures=[]
        if browser_return:
            failures.append('Chromium probe failed')
        else:
            pages=json.loads((output/'browser.json').read_text())
            rendered=[page for page in pages if page['gate_override']]
            if any(page['errors'] for page in pages): failures.append('Browser script error')
            if not rendered or any(page['ready_ms'] is None or page['ready_ms']>10000 for page in rendered):
                failures.append('Database render exceeds 10s with browser-only gate override')
        if any(row['kind']=='ai_attempt' for row in observations): failures.append('AI attempted while signed out')
        if any(row['kind']=='api_error' for row in records): failures.append('API request failed')
        if percentile(apis,.95) is None or percentile(apis,.95)>2000: failures.append('API p95 exceeds 2s')
        if percentile(collects,.95) is None or percentile(collects,.95)>60000: failures.append('Collector p95 exceeds 60s')
        if max(row['rss_bytes'] for row in resources)>1024**3: failures.append('Backend RSS exceeds 1 GiB')
        if any(v['tracked']>10000 or v['full_detail']>1000 for row in resources for v in row.get('local',{}).values()):
            failures.append('Query tier limit exceeded')
        locks=[row['duration_ms'] for row in observations if row['kind']=='sqlite_lock']
        retries=sum(row['kind']=='sqlite_locked' for row in observations)
        if not locks or percentile(locks,.95)>250 or retries/max(len(locks),1)>0.01:
            failures.append('SQLite lock p95 exceeds 250ms or retry rate exceeds 1%')
        if any(row['kind']=='collect_error' or (row['kind']=='collect' and row['event']=='discovery_error')
               for row in observations): failures.append('Collector failed')
        for engine in ENGINES:
            loads=[json.loads(line) for line in (output/(engine+'-load.jsonl')).read_text().splitlines()]
            if not any(row['kind']=='seeded' for row in loads): failures.append(engine+' did not seed all shapes')
            if any(row['kind']=='load_error' for row in loads): failures.append(engine+' load failure')
            seed = next((row for row in loads if row['kind']=='seeded'), None)
            if seed and time.time()-seed['ts']<45*60: failures.append(engine+' had less than 45 minutes after seeding')
            last = resources[-1].get('local',{}).get(engine,{})
            if seed and last.get('recorded_calls',0) < args.qps*(time.time()-seed['ts'])*0.5:
                failures.append(engine+' recorded less than half of expected post-seed calls')
            if last.get('texts',0)<min(initial['ceilings'][engine]*0.8,8000): failures.append(engine+' identity coverage below 80% of ceiling')
        summary={'finished':time.time(),'elapsed_s':time.time()-started,'api_p95_ms':percentile(apis,.95),
                 'collector_p95_ms':percentile(collects,.95),'max_rss_bytes':max(row['rss_bytes'] for row in resources),
                 'failures':failures,'passed':not failures}
        (output/'summary.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary))
        return 1 if failures else 0
    finally:
        stop.set()
        for thread in threads: thread.join(timeout=45)
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try: backend.wait(timeout=30)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=10)
        cleanup={}
        for engine in created:
            try:
                cleanup[engine]=drop_scale_database(engine)
            except Exception as exc: cleanup[engine]='failed: '+type(exc).__name__
        (output/'cleanup.json').write_text(json.dumps(cleanup,indent=2))
        if any(value.startswith('failed:') for value in cleanup.values()):
            raise RuntimeError('Scale database cleanup failed; inspect cleanup.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--minutes',type=float,default=55)
    parser.add_argument('--shapes',type=int,default=100000)
    parser.add_argument('--qps',type=float,default=25)
    parser.add_argument('--output',type=Path,default=Path('/tmp/claude-1000/scale'))
    args=parser.parse_args()
    if not args.run:
        print('SKIP: pass --run with long-test credentials loaded to run the scale soak')
        return 0
    if args.minutes<45 or not 10000<=args.shapes<=100000 or not 0<args.qps<=40:
        parser.error('Require >=45 minutes, 10000..100000 shapes, and 0 < qps <= 40')
    def interrupted(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__=='__main__':
    raise SystemExit(main())
