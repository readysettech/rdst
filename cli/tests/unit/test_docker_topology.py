"""Docker client/daemon topology tests."""

from __future__ import annotations

import pytest
from shared.deploy.docker_topology import DockerTopology, DockerTopologyError


def test_local_topology_uses_client_loopback_and_host_gateway():
    topology = DockerTopology.from_environment({})

    assert topology.remote is False
    assert topology.published_host == "127.0.0.1"
    assert topology.container_host_for("localhost") == "host.docker.internal"


def test_non_loopback_docker_host_is_detected_as_remote():
    topology = DockerTopology.from_environment(
        {"DOCKER_HOST": "tcp://docker.example.com:2375"}
    )

    assert topology.remote is True
    assert topology.published_host == "docker.example.com"


def test_active_docker_context_endpoint_is_detected(monkeypatch):
    monkeypatch.setattr(
        "shared.deploy.docker_topology._context_endpoint",
        lambda context: "ssh://docker.example.com",
    )

    topology = DockerTopology.from_environment({"DOCKER_CONTEXT": "remote"})

    assert topology.remote is True
    assert topology.published_host == "docker.example.com"


def test_tunneled_remote_daemon_uses_explicit_published_host():
    topology = DockerTopology.from_environment(
        {
            "DOCKER_HOST": "tcp://127.0.0.1:2375",
            "RDST_DOCKER_REMOTE": "1",
            "RDST_DOCKER_PUBLISHED_HOST": "192.168.122.1",
            "RDST_DOCKER_UPSTREAM_HOST": "192.168.122.222",
        }
    )

    assert topology.remote is True
    assert topology.published_host == "192.168.122.1"
    assert topology.container_host_for("localhost") == "192.168.122.222"


def test_remote_daemon_rejects_client_local_upstream_without_route():
    topology = DockerTopology.from_environment(
        {
            "DOCKER_HOST": "tcp://127.0.0.1:2375",
            "RDST_DOCKER_REMOTE": "1",
            "RDST_DOCKER_PUBLISHED_HOST": "192.168.122.1",
        }
    )

    with pytest.raises(DockerTopologyError, match="RDST_DOCKER_UPSTREAM_HOST"):
        topology.container_host_for("127.0.0.1")


def test_remote_daemon_requires_published_host_for_tunnel():
    with pytest.raises(DockerTopologyError, match="RDST_DOCKER_PUBLISHED_HOST"):
        DockerTopology.from_environment(
            {
                "DOCKER_HOST": "tcp://127.0.0.1:2375",
                "RDST_DOCKER_REMOTE": "true",
            }
        )
