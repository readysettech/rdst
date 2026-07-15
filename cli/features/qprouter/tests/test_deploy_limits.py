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


def test_caps_are_a_fixed_laptop_floor():
    # Caps are fixed (not host-scaled) so the demo behaves identically on every
    # machine; stability comes from the small offered load, not big ceilings.
    assert deploy.RESOURCE_LIMITS["pg"]["cpus"] == "2"
    assert deploy.RESOURCE_LIMITS["readyset"]["cpus"] == "2"
    assert deploy.RESOURCE_LIMITS["pg"]["memory"] == "2g"
    assert deploy.RESOURCE_LIMITS["readyset"]["memory"] == "2g"


def test_readyset_gets_a_long_shallow_cache_ttl(monkeypatch):
    # Long TTL keeps the ~20 shallow caches from refreshing in lockstep every few
    # seconds, which is what makes the ReadySet throughput line sawtooth.
    calls = _capture(monkeypatch)
    deploy.deploy_readyset("qpdemo-readyset", 5433, 6034, 5432,
                           {"user": "u", "password": "p", "database": "d"})
    (cmd,) = calls
    env = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    ttl = [e for e in env if e.startswith("DEFAULT_TTL_MS=")]
    assert ttl, "DEFAULT_TTL_MS not set on the ReadySet container"
    assert int(ttl[0].split("=", 1)[1]) >= 60_000
