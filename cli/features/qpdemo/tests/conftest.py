import subprocess
import time

import pytest

from features.qpdemo.service import DemoService, NAMES


def _docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _docker_volumes() -> set[str]:
    r = _docker(["volume", "ls", "--format", "{{.Name}}"], timeout=10)
    assert r.returncode == 0, r.stderr
    return {line for line in r.stdout.splitlines() if line.strip()}


def _qpdemo_containers() -> dict[str, str]:
    return {
        short: DemoService.instance()._container_state(name)
        for short, name in NAMES.items()
    }


def _drop_rules(svc: DemoService) -> None:
    if not svc._qp:
        return
    for rule in svc._qp.admin.pattern_rules():
        svc._qp.admin.drop_pattern_rule(rule["fingerprint_hash"])


def _drop_all_readyset_caches(svc: DemoService) -> None:
    if not svc._qp:
        return
    conn = svc._qp.readyset._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP ALL CACHES")
    except Exception:
        # Older ReadySet builds may not support DROP ALL CACHES. The SQP rules
        # are the behaviorally important state for these tests.
        pass
    finally:
        conn.close()


def _reset_runtime_state(svc: DemoService) -> None:
    svc.stop_load()
    if svc._ports:
        svc.set_querypilot(False)
    if svc._qp:
        _drop_rules(svc)
        _drop_all_readyset_caches(svc)
        svc.set_discovery_mode("count_star")
    svc._latest = None
    svc._history = []


@pytest.fixture(scope="session")
def qpdemo_stack():
    if not DemoService.docker_ok():
        pytest.skip("docker not available")

    svc = DemoService.instance()
    svc.teardown()
    volumes_before = _docker_volumes()
    events = list(svc.provision())
    if not any(e.get("type") == "complete" for e in events):
        errs = [e for e in events if e.get("type") == "error"]
        svc.teardown()
        pytest.fail(f"qpdemo provision did not complete: {errs}")

    try:
        yield svc
    finally:
        svc.teardown()
        time.sleep(2)
        containers = _qpdemo_containers()
        assert all(v == "absent" for v in containers.values()), containers
        volumes_after = _docker_volumes()
        assert not (volumes_after - volumes_before), volumes_after - volumes_before


@pytest.fixture()
def provisioned(qpdemo_stack):
    _reset_runtime_state(qpdemo_stack)
    yield qpdemo_stack
    _reset_runtime_state(qpdemo_stack)


@pytest.fixture()
def svc(provisioned):
    return provisioned
