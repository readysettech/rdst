"""Docker client/daemon topology tests."""

from __future__ import annotations

import pytest
from shared.deploy.docker_topology import DockerTopology, DockerTopologyError


def test_local_topology_uses_client_loopback_and_host_gateway():
    topology = DockerTopology.from_environment({})

    assert topology.remote is False
    assert topology.published_host == "127.0.0.1"
    assert topology.container_host_for("localhost") == "host.docker.internal"


def test_local_linux_loopback_upstream_uses_host_network():
    topology = DockerTopology.from_environment({})

    network = topology.container_network_for(
        "127.0.0.1",
        platform_name="linux",
    )

    assert network.host_network is True
    assert network.upstream_host == "localhost"
    assert network.listen_host == "127.0.0.1"


@pytest.mark.parametrize(
    "host",
    ["LOCALHOST", "localhost.", "127.0.0.2", "0:0:0:0:0:0:0:1", "[::1]"],
)
def test_local_linux_recognizes_equivalent_loopback_spellings(host):
    topology = DockerTopology.from_environment({})

    network = topology.container_network_for(host, platform_name="linux")

    assert network.host_network is True
    assert network.upstream_host == "localhost"


def test_bridge_fallback_reaches_loopback_through_the_host_gateway():
    topology = DockerTopology.from_environment({})

    network = topology.bridge_network_for("127.0.0.1")

    assert network.host_network is False
    assert network.upstream_host == "host.docker.internal"
    assert network.listen_host == "0.0.0.0"


def test_local_linux_remote_upstream_keeps_bridge_network():
    topology = DockerTopology.from_environment({})

    network = topology.container_network_for(
        "database.example.com",
        platform_name="linux",
    )

    assert network.host_network is False
    assert network.upstream_host == "database.example.com"
    assert network.listen_host == "0.0.0.0"


def test_explicit_docker_network_overrides_linux_host_network():
    topology = DockerTopology.from_environment(
        {"RDST_DOCKER_NETWORK": "rdst-e2e_default"}
    )

    network = topology.container_network_for(
        "localhost",
        platform_name="linux",
    )

    assert network.host_network is False
    assert network.docker_network == "rdst-e2e_default"
    assert network.upstream_host == "host.docker.internal"
    assert network.listen_host == "0.0.0.0"


def test_local_macos_loopback_upstream_keeps_host_gateway():
    topology = DockerTopology.from_environment({})

    network = topology.container_network_for(
        "localhost",
        platform_name="darwin",
    )

    assert network.host_network is False
    assert network.upstream_host == "host.docker.internal"


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


def test_selected_remote_context_is_detected_without_environment_override(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text(
        '{"currentContext":"remote"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    monkeypatch.setattr(
        "shared.deploy.docker_topology._context_endpoint",
        lambda context: "ssh://docker.example.com",
    )

    topology = DockerTopology.from_environment()

    assert topology.remote is True
    assert topology.published_host == "docker.example.com"


def test_docker_desktop_for_linux_keeps_host_gateway(monkeypatch):
    monkeypatch.setattr(
        "shared.deploy.docker_topology._context_endpoint",
        lambda context: "unix:///home/user/.docker/desktop/docker.sock",
    )
    topology = DockerTopology.from_environment(
        {"DOCKER_CONTEXT": "desktop-linux"}
    )

    network = topology.container_network_for(
        "localhost",
        platform_name="linux",
    )

    assert topology.remote is False
    assert topology.desktop is True
    assert network.host_network is False
    assert network.upstream_host == "host.docker.internal"


def test_remote_context_named_desktop_is_still_remote(monkeypatch):
    monkeypatch.setattr(
        "shared.deploy.docker_topology._context_endpoint",
        lambda context: "ssh://docker.example.com",
    )
    topology = DockerTopology.from_environment(
        {
            "DOCKER_CONTEXT": "desktop-linux",
            "RDST_DOCKER_UPSTREAM_HOST": "database.client.example.com",
        }
    )

    network = topology.container_network_for(
        "localhost",
        platform_name="linux",
    )

    assert topology.remote is True
    assert topology.desktop is False
    assert network.host_network is False
    assert network.upstream_host == "database.client.example.com"


def test_rootless_linux_daemon_keeps_host_gateway(monkeypatch):
    monkeypatch.setattr(
        "shared.deploy.docker_topology._context_endpoint",
        lambda context: "unix:///run/user/1000/docker.sock",
    )
    topology = DockerTopology.from_environment(
        {"DOCKER_CONTEXT": "rootless"}
    )

    network = topology.container_network_for(
        "localhost",
        platform_name="linux",
    )

    assert topology.remote is False
    assert topology.rootless is True
    assert network.host_network is False
    assert network.upstream_host == "host.docker.internal"


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

    network = topology.container_network_for(
        "localhost",
        platform_name="linux",
    )
    assert network.host_network is False
    assert network.upstream_host == "192.168.122.222"


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


@pytest.mark.parametrize("engine,tls,expected", [
    ("postgresql", True, {"sslmode": ["require"]}),
    ("postgresql", False, {}),
    ("mysql", True, {"require_ssl": ["true"]}),
    ("mysql", False, {}),
])
def test_sandbox_passes_target_encryption(engine, tls, expected):
    from urllib.parse import parse_qs, urlsplit
    from shared.deploy.local_docker import _managed_create_command

    command = _managed_create_command(
        {"db_user": "app", "db_name": "db", "db_port": "5432",
         "db_engine": engine, "db_tls": tls, "readyset_port": "5433",
         "container_name": "test", "readyset_image": "readyset:test"},
        "secret", DockerTopology.from_environment({}).bridge_network_for("db.example.com"),
        extra_args=None, restart_policy=False,
    )
    url = next(arg.split("=", 1)[1] for arg in command if arg.startswith("UPSTREAM_DB_URL="))
    assert parse_qs(urlsplit(url).query) == expected
    assert not any("DISABLE_UPSTREAM_SSL_VERIFICATION" in arg for arg in command)


def test_sandbox_mounts_target_ca_read_only(tmp_path, monkeypatch):
    from shared.deploy.local_docker import _managed_create_command

    topology = DockerTopology.from_environment({})
    monkeypatch.setattr(DockerTopology, "from_environment", lambda: topology)
    ca = tmp_path / "ca.pem"
    ca.write_text("test certificate")
    command = _managed_create_command(
        {"db_user": "app", "db_name": "db", "db_port": "5432",
         "db_engine": "postgresql", "db_tls": True, "db_tls_ca": str(ca),
         "readyset_port": "5433", "container_name": "test",
         "readyset_image": "readyset:test"},
        "secret", topology.bridge_network_for("db.example.com"),
        extra_args=None, restart_policy=False,
    )
    assert f"type=bind,src={ca},dst=/rdst-upstream-ca.pem,readonly" in command
    assert "SSL_ROOT_CERT=/rdst-upstream-ca.pem" in command
