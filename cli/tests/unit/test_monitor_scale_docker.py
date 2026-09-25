"""Safety and native connection isolation for the Docker scale harness."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'monitor_scale_docker.py'
SPEC = importlib.util.spec_from_file_location('monitor_scale_docker_harness', SCRIPT)
harness = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT.parent))
SPEC.loader.exec_module(harness)


def test_default_skips_without_starting_docker(monkeypatch, capsys):
    monkeypatch.setattr(harness.sys, 'argv', [str(SCRIPT)])
    run = Mock(side_effect=AssertionError('Default invocation created containers'))
    monkeypatch.setattr(harness, 'run', run)
    assert harness.main() == 0
    run.assert_not_called()
    assert 'SKIP' in capsys.readouterr().out


def test_postgres_connection_uses_loopback_and_disables_tls(monkeypatch):
    import psycopg2
    monkeypatch.setenv('SCALE100K_PG_PORT', '19991')
    monkeypatch.setenv('SCALE100K_PG_PASSWORD', 'test-password')
    connect = Mock()
    monkeypatch.setattr(psycopg2, 'connect', connect)
    harness.connect('pg')
    assert connect.call_args.kwargs['host'] == '127.0.0.1'
    assert connect.call_args.kwargs['port'] == 19991
    assert connect.call_args.kwargs['dbname'] == 'rdst_scale'
    assert connect.call_args.kwargs['sslmode'] == 'disable'
    assert connect.return_value.autocommit is True


def test_mysql_connection_uses_owned_database(monkeypatch):
    import pymysql
    monkeypatch.setenv('SCALE100K_MYSQL_PORT', '19992')
    monkeypatch.setenv('SCALE100K_MYSQL_PASSWORD', 'test-password')
    connect = Mock()
    monkeypatch.setattr(pymysql, 'connect', connect)
    harness.connect('mysql')
    assert connect.call_args.kwargs['host'] == '127.0.0.1'
    assert connect.call_args.kwargs['port'] == 19992
    assert connect.call_args.kwargs['database'] == 'rdst_scale'
    assert connect.call_args.kwargs['autocommit'] is True
    assert 'ssl' not in connect.call_args.kwargs


def test_browser_measurement_records_artifact_without_logger_argument_collision(tmp_path, monkeypatch):
    import json
    probe = Mock(return_value=0)
    monkeypatch.setattr(harness, 'browser_probe', probe)
    path = tmp_path / 'browser.json'
    harness.record_browser_probe(19993, tmp_path, path)
    probe.assert_called_once_with(19993, path)
    row = json.loads((tmp_path / 'samples.jsonl').read_text())
    assert row['kind'] == 'browser'
    assert row['returncode'] == 0
    assert row['browser_file'] == 'browser.json'


def _qualification_evidence(path):
    import json
    rows = [{'kind': 'resources', 'rss_bytes': 500 * 1024**2,
             'local': {engine: {'texts': 10000, 'tracked': 10000, 'full_detail': 1000}
                       for engine in harness.scale.ENGINES}}]
    for target in harness.scale.TARGETS.values():
        rows.extend({'kind': 'api', 'target': target, 'endpoint': endpoint,
                     'status': 200, 'ms': 20} for endpoint in harness.ENDPOINTS)
    rows.append({'kind': 'browser', 'returncode': 0, 'browser_file': 'browser.json'})
    (path / 'samples.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    pages = [{'target': target, 'errors': [], 'ready_ms': 1000}
             for target in harness.scale.TARGETS.values()]
    (path / 'browser.json').write_text(json.dumps(pages))
    metrics = [{'kind': 'collect', 'target': target, 'duration_ms': 1000,
                'event': 'discovery_update'} for target in harness.scale.TARGETS.values()]
    metrics.append({'kind': 'sqlite_lock', 'duration_ms': 1})
    (path / 'backend-metrics.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in metrics))
    for engine in harness.scale.ENGINES:
        (path / (engine + '-load.jsonl')).write_text('{"kind":"seeded"}\n')


def test_completed_healthy_evidence_passes(tmp_path):
    _qualification_evidence(tmp_path)
    assert harness.write_qualification(tmp_path) == 0


def test_completed_run_with_timeout_and_high_rss_fails(tmp_path):
    import json
    _qualification_evidence(tmp_path)
    with (tmp_path / 'samples.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind': 'api_error', 'error_type': 'TimeoutError'}) + '\n')
        stream.write(json.dumps({'kind': 'resources', 'rss_bytes': 1100 * 1024**2}) + '\n')
    assert harness.write_qualification(tmp_path) == 2
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary['verdict'] == 'FAIL'
    assert 'API request failed' in summary['failures']
    assert 'Backend RSS exceeds 1 GiB' in summary['failures']


def test_sparse_evidence_cannot_pass(tmp_path):
    assert harness.write_qualification(tmp_path) == 2


def test_one_slow_endpoint_is_not_hidden_by_fast_endpoints(tmp_path):
    import json
    _qualification_evidence(tmp_path)
    with (tmp_path / 'samples.jsonl').open('a') as stream:
        stream.write(json.dumps({'kind': 'api', 'target': 'scale-pg',
                                'endpoint': 'queries', 'status': 200, 'ms': 3000}) + '\n')
    assert harness.write_qualification(tmp_path) == 2
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert 'API endpoint p95 exceeds 2s or samples missing' in summary['failures']


def test_browser_http_failure_fails_even_with_successful_ready_response(tmp_path):
    import json
    _qualification_evidence(tmp_path)
    path = tmp_path / "browser.json"
    pages = json.loads(path.read_text())
    pages[0]["http_errors"] = [{"status": 500, "path": "/api/monitor/inbox"}]
    path.write_text(json.dumps(pages))
    assert harness.write_qualification(tmp_path) == 2
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert "Browser API request failed" in summary["failures"]


def test_container_names_are_tagged_and_validated():
    import pytest
    assert harness.container_names('opus') == ('rdst-scale100k-opus-pg',
                                               'rdst-scale100k-opus-mysql')
    with pytest.raises(ValueError):
        harness.container_names('bad;name')


def test_short_requalification_and_seed_rate_are_bounded(monkeypatch, tmp_path):
    import pytest
    run = Mock(return_value=0)
    monkeypatch.setattr(harness, 'run', run)
    monkeypatch.setattr(harness.signal, 'signal', Mock())
    monkeypatch.setattr(harness.sys, 'argv', [str(SCRIPT), '--run', '--hours', '1.5',
                                              '--seed-qps', '2000', '--tag', 'opus'])
    assert harness.main() == 0
    assert run.call_args.args[0].seed_qps == 2000
    monkeypatch.setattr(harness.sys, 'argv', [str(SCRIPT), '--run', '--hours', '1'])
    with pytest.raises(SystemExit):
        harness.main()


def test_workload_admits_the_tail_at_the_seed_rate(monkeypatch, tmp_path):
    import threading
    scale = harness.scale
    stop = threading.Event()
    waits = []
    executed = []

    class Connection:
        def close(self):
            pass

    def query(connection, sql):
        executed.append(sql)
        if len(executed) == 4:
            stop.set()
        return []

    monkeypatch.setattr(scale, 'connect', lambda engine: Connection())
    monkeypatch.setattr(scale, 'query', query)
    monkeypatch.setattr(scale, 'source_counts', lambda engine, connection: {})
    monkeypatch.setattr(stop, 'wait', lambda timeout=None: waits.append(timeout))
    scale.workload('pg', 2, 40, stop, tmp_path / 'load.jsonl', seed_qps=1000)
    # The tail is admitted at 1000/s; once every shape ran, replay paces at 40/s.
    assert waits[0] <= 1 / 1000
    assert all(wait > 1 / 100 for wait in waits[1:])
