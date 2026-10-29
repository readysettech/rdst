"""The demo containers follow the Docker network RDST shares with them."""

import subprocess
from pathlib import Path

from features.qprouter import deploy


def _launches(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, timeout=300):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(deploy, "_run", fake_run)
    ports = deploy.Ports(pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090)
    deploy.deploy_postgres_baked("qpdemo-pg", 5432)
    deploy.deploy_readyset(
        "qpdemo-readyset", 5433, 6034, 5432, {"user": "u", "password": "p", "database": "d"}
    )
    deploy.deploy_sqp("qpdemo-sqp", ports, Path("/tmp/c"), Path("/tmp/d"))
    deploy.deploy_qp_cron("qpdemo-qp-cron", Path("/tmp/cfg"))
    return [cmd for cmd in calls if cmd[1] in {"run", "create"}]


def test_containers_join_the_shared_network_and_are_reached_by_name(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setenv("RDST_DOCKER_NETWORK", "ci_default")
    monkeypatch.setenv("RDST_DOCKER_PUBLISHED_HOST", "rdst-readyset-sandbox")

    launches = _launches(monkeypatch)

    assert len(launches) == 4
    for cmd in launches:
        assert cmd[cmd.index("--network") + 1] == "ci_default", cmd[:6]
    assert deploy.client_address("qpdemo-pg", 5440, 5432) == ("qpdemo-pg", 5432)


def test_without_a_shared_network_rdst_uses_the_published_ports(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.delenv("RDST_DOCKER_NETWORK", raising=False)
    monkeypatch.delenv("RDST_DOCKER_PUBLISHED_HOST", raising=False)

    launches = _launches(monkeypatch)

    assert all("--network" not in cmd for cmd in launches)
    assert deploy.client_address("qpdemo-pg", 5440, 5432) == ("127.0.0.1", 5440)
