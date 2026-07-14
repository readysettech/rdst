"""Every demo container must launch with an explicit CPU + memory ceiling.

Without these, `docker run` applies no limit and Postgres under the comparison
workload will grab every core on the host. These tests pin the caps onto each
launch command so a regression can never silently ship an unbounded container.
"""

import subprocess

import features.qprouter.deploy as deploy


def _capture(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=300):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(deploy, "_run", fake_run)
    return calls


def _flags(cmd, flag):
    """Value passed to `flag` in a docker argv (e.g. --cpus 2)."""
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]


def test_every_container_launches_with_cpu_and_memory_caps(monkeypatch):
    calls = _capture(monkeypatch)

    ports = deploy.Ports(pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090)
    deploy.deploy_postgres_baked("qpdemo-pg", 5432)
    deploy.deploy_readyset("qpdemo-readyset", 5433, 6034, 5432,
                           {"user": "u", "password": "p", "database": "d"})
    deploy.deploy_sqp("qpdemo-sqp", ports, __import__("pathlib").Path("/tmp/c"),
                      __import__("pathlib").Path("/tmp/d"))
    deploy.deploy_qp_cron("qpdemo-qp-cron", __import__("pathlib").Path("/tmp/cfg"))

    assert len(calls) == 4
    for cmd in calls:
        assert _flags(cmd, "--cpus"), f"no --cpus in {cmd[:6]}"
        assert _flags(cmd, "--memory"), f"no --memory in {cmd[:6]}"


def test_caps_use_the_configured_defaults(monkeypatch):
    calls = _capture(monkeypatch)
    deploy.deploy_postgres_baked("qpdemo-pg", 5432)
    (cmd,) = calls
    assert _flags(cmd, "--cpus") == [deploy.RESOURCE_LIMITS["pg"]["cpus"]]
    assert _flags(cmd, "--memory") == [deploy.RESOURCE_LIMITS["pg"]["memory"]]


def test_limit_flags_are_env_overridable(monkeypatch):
    # RESOURCE_LIMITS is read at import; _limit_flags reads from it, so patch the
    # resolved table to prove the value flows through to the argv.
    monkeypatch.setitem(deploy.RESOURCE_LIMITS["readyset"], "cpus", "3")
    monkeypatch.setitem(deploy.RESOURCE_LIMITS["readyset"], "memory", "4g")
    assert deploy._limit_flags("readyset") == ["--cpus", "3", "--memory", "4g"]


def test_data_path_cpus_scales_4_to_8_and_never_exceeds_host():
    # 4-8 cores on a capable machine, capped at 8, and never more than the host
    # physically has (a sub-4-core machine gets what it has, not a forced 4).
    assert deploy._data_path_cpus(1) == 1
    assert deploy._data_path_cpus(2) == 2
    assert deploy._data_path_cpus(4) == 4
    assert deploy._data_path_cpus(6) == 6
    assert deploy._data_path_cpus(8) == 8
    assert deploy._data_path_cpus(16) == 8
    assert deploy._data_path_cpus(32) == 8
    for cores in range(1, 65):
        assert deploy._data_path_cpus(cores) <= cores


def test_router_cpus_is_a_small_fixed_cap():
    # SQP is a light proxy, never the bottleneck: 2 cores everywhere, or fewer on
    # a single-core host. It does not scale up with Postgres.
    assert deploy._router_cpus(1) == 1
    assert deploy._router_cpus(2) == 2
    assert deploy._router_cpus(8) == 2
    assert deploy._router_cpus(32) == 2
    for cores in range(1, 65):
        assert deploy._router_cpus(cores) <= cores
