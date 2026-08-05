from __future__ import annotations

import select
import socket
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from shared.db_connection import (
    cancel_mysql_by_thread_id,
    cancel_query,
    resolve_connection_params,
)
from shared.ssh_tunnel import (
    SshKeyError,
    SshPassphraseRequired,
    TunnelManager,
)


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class _EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _DirectTcpipServer(paramiko.ServerInterface):
    def __init__(self):
        self.destinations = {}
        self.lock = threading.Lock()

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return "publickey"

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        with self.lock:
            self.destinations[chanid] = destination
        return paramiko.OPEN_SUCCEEDED

    def destination_for(self, channel):
        with self.lock:
            return self.destinations.pop(channel.get_id())


class _SshServer:
    def __init__(self):
        self.host_key = paramiko.RSAKey.generate(1024)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop_event = threading.Event()
        self.transports = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._accept, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.listener.close()
        with self.lock:
            transports = list(self.transports)
        for transport in transports:
            transport.close()
        self.thread.join(timeout=2)

    def _accept(self):
        while not self.stop_event.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                daemon=True,
            ).start()

    def _serve_connection(self, connection):
        transport = paramiko.Transport(connection)
        with self.lock:
            self.transports.append(transport)
        server = _DirectTcpipServer()
        try:
            transport.add_server_key(self.host_key)
            transport.start_server(server=server)
            while transport.is_active() and not self.stop_event.is_set():
                channel = transport.accept(0.2)
                if channel is None:
                    continue
                destination = server.destination_for(channel)
                threading.Thread(
                    target=self._forward,
                    args=(channel, destination),
                    daemon=True,
                ).start()
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()
            with self.lock:
                if transport in self.transports:
                    self.transports.remove(transport)

    @staticmethod
    def _forward(channel, destination):
        remote = None
        try:
            remote = socket.create_connection(destination, timeout=2)
            while True:
                readable, _, _ = select.select([channel, remote], [], [], 0.2)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        return
                    remote.sendall(data)
                if remote in readable:
                    data = remote.recv(65536)
                    if not data:
                        return
                    channel.sendall(data)
        except (EOFError, OSError):
            pass
        finally:
            channel.close()
            if remote is not None:
                remote.close()


class _FakeTransport:
    def __init__(self):
        self.active = True

    def is_active(self):
        return self.active

    def close(self):
        self.active = False


class _RegistryManager(TunnelManager):
    def __init__(self):
        super().__init__()
        self.next_port = 40000

    def _open_tunnel(self, target_name, ssh_cfg, dest_host, dest_port):
        self.next_port += 1
        return SimpleNamespace(
            target=target_name,
            jump_host=ssh_cfg["host"],
            local_port=self.next_port,
            transport=_FakeTransport(),
            last_used=time.time(),
            stop_event=threading.Event(),
        )

    def _resolve_ssh_config(self, target_name, ssh_cfg):
        return {
            "host": ssh_cfg["host"],
            "port": int(ssh_cfg.get("port", 22)),
            "user": ssh_cfg.get("user", "test-user"),
            "key_path": ssh_cfg.get("key_path"),
        }

    @staticmethod
    def _close_tunnel(tunnel):
        tunnel.stop_event.set()
        tunnel.transport.close()


class _PassphraseRegistryManager(_RegistryManager):
    def __init__(self):
        super().__init__()
        self.passphrase = None

    def _open_tunnel(
        self,
        target_name,
        ssh_cfg,
        dest_host,
        dest_port,
        passphrase=None,
    ):
        self.passphrase = passphrase
        return super()._open_tunnel(target_name, ssh_cfg, dest_host, dest_port)


@pytest.fixture(autouse=True)
def isolated_ssh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def tunnel_environment(tmp_path):
    try:
        echo = _EchoServer(("127.0.0.1", 0), _EchoHandler)
    except PermissionError:
        pytest.skip("local TCP sockets are unavailable in this sandbox")
    echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
    echo_thread.start()

    ssh = _SshServer()
    ssh.start()
    key_path = tmp_path / "client-key"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(key_path))

    yield {
        "ssh": ssh,
        "key_path": str(key_path),
        "echo_port": echo.server_address[1],
    }

    ssh.close()
    echo.shutdown()
    echo.server_close()
    echo_thread.join(timeout=2)


def _ssh_config(environment):
    return {
        "host": "127.0.0.1",
        "port": environment["ssh"].port,
        "user": "test-user",
        "key_path": environment["key_path"],
    }


def _open(manager, environment, target):
    return manager.ensure_tunnel(
        target,
        _ssh_config(environment),
        "127.0.0.1",
        environment["echo_port"],
    )


def _open_registry(manager, target):
    return manager.ensure_tunnel(
        target,
        {
            "host": "jump.example.com",
            "port": 22,
            "user": "test-user",
            "key_path": "/keys/test-key",
        },
        "database.internal",
        5432,
    )


def test_full_direct_tcpip_tunnel_path(tunnel_environment):
    manager = TunnelManager()
    try:
        endpoint = _open(manager, tunnel_environment, "echo-db")

        with socket.create_connection(endpoint, timeout=2) as client:
            client.sendall(b"through the ssh tunnel")
            assert client.recv(65536) == b"through the ssh tunnel"

        status = manager.status()
        assert status[0]["target"] == "echo-db"
        assert status[0]["jump_host"] == "127.0.0.1"
        assert status[0]["local_port"] == endpoint[1]
        assert status[0]["state"] == "active"
    finally:
        manager.close_all()


def test_fresh_tunnel_handles_immediate_sequential_and_concurrent_connections(
    tunnel_environment,
):
    """The endpoint returned for a fresh tunnel is ready for real traffic."""
    manager = TunnelManager()

    def round_trip(endpoint, payload):
        with socket.create_connection(endpoint, timeout=3) as client:
            client.sendall(payload)
            assert client.recv(65536) == payload

    try:
        endpoint = _open(manager, tunnel_environment, "stress-db")
        for index in range(20):
            round_trip(endpoint, f"sequential-{index}".encode())

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(round_trip, endpoint, f"concurrent-{index}".encode())
                for index in range(5)
            ]
            for future in futures:
                future.result(timeout=5)
    finally:
        manager.close_all()


def test_ensure_waits_for_accept_loop_readiness(tunnel_environment):
    manager = TunnelManager()
    original_accept = manager._accept_connections

    def delayed_accept(tunnel, dest_host, dest_port):
        time.sleep(0.1)
        original_accept(tunnel, dest_host, dest_port)

    started = time.monotonic()
    try:
        with patch.object(manager, "_accept_connections", delayed_accept):
            _open(manager, tunnel_environment, "readiness-db")
        assert time.monotonic() - started >= 0.09
    finally:
        manager.close_all()


def test_forwarder_retries_a_transient_channel_open_failure():
    transport = MagicMock()
    transport.open_channel.side_effect = [
        paramiko.SSHException("transport not ready"),
        None,
    ]
    stop_event = MagicMock()
    stop_event.is_set.return_value = False
    stop_event.wait.return_value = False
    client = MagicMock()
    tunnel = SimpleNamespace(
        target="db",
        transport=transport,
        stop_event=stop_event,
        clients={client},
        clients_lock=threading.Lock(),
    )

    TunnelManager._forward_connection(
        tunnel,
        client,
        ("127.0.0.1", 50000),
        "database.internal",
        5432,
    )

    assert transport.open_channel.call_count == 2
    stop_event.wait.assert_called_once_with(0.25)
    client.close.assert_called_once()


@pytest.mark.parametrize("error", [OSError("closed"), ValueError("closed")])
def test_relay_shutdown_socket_errors_are_silent(monkeypatch, error):
    stop_event = threading.Event()
    source = SimpleNamespace(closed=False, fileno=lambda: 1)
    destination = MagicMock()

    def close_during_select(*_args, **_kwargs):
        stop_event.set()
        raise error

    warning = MagicMock()
    monkeypatch.setattr("shared.ssh_tunnel.select.select", close_during_select)
    monkeypatch.setattr("shared.ssh_tunnel.logger.warning", warning)

    TunnelManager._pump(source, destination, stop_event)

    warning.assert_not_called()


def test_relay_errors_from_an_already_closed_socket_are_silent(monkeypatch):
    source = SimpleNamespace(closed=True, fileno=lambda: -1)
    destination = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr(
        "shared.ssh_tunnel.select.select",
        MagicMock(
            side_effect=ValueError(
                "file descriptor cannot be a negative integer"
            )
        ),
    )
    monkeypatch.setattr("shared.ssh_tunnel.logger.warning", warning)

    TunnelManager._pump(source, destination, threading.Event())

    warning.assert_not_called()


def test_relay_logs_genuine_midstream_socket_errors(monkeypatch):
    source = SimpleNamespace(closed=False, fileno=lambda: 1)
    destination = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr(
        "shared.ssh_tunnel.select.select",
        MagicMock(side_effect=OSError("network failed")),
    )
    monkeypatch.setattr("shared.ssh_tunnel.logger.warning", warning)

    TunnelManager._pump(source, destination, threading.Event())

    warning.assert_called_once()
    assert "network failed" in str(warning.call_args)


def test_last_used_updates_and_close():
    manager = _RegistryManager()
    try:
        endpoint = _open_registry(manager, "db")
        first_used = manager.status()[0]["last_used"]
        time.sleep(0.01)
        assert _open_registry(manager, "db") == endpoint

        assert manager.status()[0]["last_used"] > first_used
        manager.close("db")
        assert manager.status() == []
    finally:
        manager.close_all()


def test_identical_raw_config_reuses_without_resolving_again():
    manager = _RegistryManager()
    try:
        endpoint = _open_registry(manager, "db")

        with patch.object(
            manager,
            "_resolve_ssh_config",
            wraps=manager._resolve_ssh_config,
        ) as resolve:
            assert _open_registry(manager, "db") == endpoint

        resolve.assert_not_called()
    finally:
        manager.close_all()


@pytest.mark.parametrize(
    "ssh_config",
    [
        {"host": "jump-alias"},
        {"host": "jump-alias", "user": "ops"},
        {"host": "ignored.example.com", "profile": "prod-jump"},
    ],
)
def test_profile_or_ssh_config_defaults_are_resolved_before_reuse(ssh_config):
    manager = _RegistryManager()
    first_resolved = {
        "host": "old.example.com",
        "port": 22,
        "user": "ops",
        "key_path": "/keys/old",
    }
    second_resolved = {
        "host": "new.example.com",
        "port": 2222,
        "user": "ops",
        "key_path": "/keys/new",
    }
    try:
        with patch.object(
            manager,
            "_resolve_ssh_config",
            side_effect=[first_resolved, second_resolved],
        ) as resolve:
            first_endpoint = manager.ensure_tunnel(
                "db", ssh_config, "database.internal", 5432
            )
            second_endpoint = manager.ensure_tunnel(
                "db", ssh_config, "database.internal", 5432
            )

        assert resolve.call_count == 2
        assert second_endpoint != first_endpoint
    finally:
        manager.close_all()


def test_lru_eviction_at_tunnel_cap():
    manager = _RegistryManager()
    try:
        for index in range(10):
            _open_registry(manager, f"db-{index}")
        _open_registry(manager, "db-0")

        _open_registry(manager, "db-10")

        targets = {item["target"] for item in manager.status()}
        assert len(targets) == 10
        assert "db-0" in targets
        assert "db-1" not in targets
        assert "db-10" in targets
    finally:
        manager.close_all()


def test_dead_tunnel_reopens_with_new_port():
    manager = _RegistryManager()
    try:
        first_endpoint = _open_registry(manager, "db")
        manager._tunnels["db"].transport.close()

        second_endpoint = _open_registry(manager, "db")

        assert second_endpoint[0] == "127.0.0.1"
        assert second_endpoint[1] != first_endpoint[1]
    finally:
        manager.close_all()


def test_force_fresh_closes_and_reopens_under_the_target_lifecycle():
    manager = _RegistryManager()
    try:
        first_endpoint = _open_registry(manager, "db")
        first_tunnel = manager._tunnels["db"]

        second_endpoint = manager.ensure_tunnel(
            "db",
            {"host": "jump.example.com"},
            "database.internal",
            5432,
            force_fresh=True,
        )

        assert second_endpoint != first_endpoint
        assert first_tunnel.stop_event.is_set()
        assert not first_tunnel.transport.is_active()
    finally:
        manager.close_all()


def test_target_lifecycle_blocks_a_concurrent_close_until_probe_finishes():
    manager = _RegistryManager()
    entered = threading.Event()
    release = threading.Event()
    close_finished = threading.Event()

    def hold_lifecycle():
        with manager.target_lifecycle("db"):
            entered.set()
            release.wait(2)

    def close_target():
        manager.close("db")
        close_finished.set()

    holder = threading.Thread(target=hold_lifecycle)
    closer = threading.Thread(target=close_target)
    holder.start()
    assert entered.wait(1)
    closer.start()
    assert not close_finished.wait(0.05)

    release.set()
    holder.join(timeout=1)
    closer.join(timeout=1)
    assert close_finished.is_set()


def test_changed_resolved_ssh_config_closes_and_reopens_tunnel():
    manager = _RegistryManager()
    try:
        first_endpoint = manager.ensure_tunnel(
            "db",
            {
                "host": "jump.example.com",
                "port": 22,
                "user": "ops",
                "key_path": "/keys/right",
            },
            "database.internal",
            5432,
        )
        first_tunnel = manager._tunnels["db"]

        second_endpoint = manager.ensure_tunnel(
            "db",
            {
                "host": "jump.example.com",
                "port": 22,
                "user": "ops",
                "key_path": "/keys/wrong",
            },
            "database.internal",
            5432,
        )

        assert second_endpoint != first_endpoint
        assert first_tunnel.stop_event.is_set()
        assert not first_tunnel.transport.is_active()
        assert manager._tunnels["db"].config_fingerprint == (
            "jump.example.com",
            22,
            "ops",
            "/keys/wrong",
            "",
        )
    finally:
        manager.close_all()


def test_changed_destination_closes_and_reopens_tunnel():
    manager = _RegistryManager()
    try:
        first_endpoint = _open_registry(manager, "db")
        first_tunnel = manager._tunnels["db"]

        second_endpoint = manager.ensure_tunnel(
            "db",
            {"host": "jump.example.com"},
            "other-database.internal",
            5432,
        )

        assert second_endpoint != first_endpoint
        assert first_tunnel.stop_event.is_set()
    finally:
        manager.close_all()


def test_close_all_closes_every_tunnel():
    manager = _RegistryManager()
    _open_registry(manager, "db-1")
    _open_registry(manager, "db-2")

    manager.close_all()

    assert manager.status() == []


def test_passphrase_is_threaded_to_tunnel_open():
    manager = _PassphraseRegistryManager()
    try:
        manager.ensure_tunnel(
            "db",
            {"host": "jump.example.com"},
            "database.internal",
            5432,
            passphrase="in-memory-only",
        )

        assert manager.passphrase == "in-memory-only"
    finally:
        manager.close_all()


def test_missing_key_file_is_typed_and_actionable(tmp_path):
    manager = TunnelManager()
    missing = tmp_path / "missing-key"

    with pytest.raises(SshKeyError) as exc_info:
        manager.ensure_tunnel(
            "private-db",
            {
                "host": "jump.example.com",
                "user": "db-user",
                "key_path": str(missing),
            },
            "database.internal",
            5432,
        )

    message = str(exc_info.value)
    assert "private-db" in message
    assert "jump.example.com" in message
    assert str(missing) in message


def test_missing_key_api_copy_uses_expanded_checked_path(tmp_path):
    from shared.api.ssh_errors import ssh_error_payload

    configured = "~/.ssh/missing.pem"
    checked = str(tmp_path / ".ssh" / "missing.pem")
    error = SshKeyError("missing", key_path=checked)

    payload = ssh_error_payload(
        error,
        "private-db",
        {"host": "jump.example.com", "key_path": configured},
    )

    assert checked in payload["message"]
    assert configured not in payload["message"]
    assert "Choose an existing private key" in payload["message"]
    assert "jump.example.com" in payload["message"]


def test_passphrase_required_is_typed_and_actionable(tmp_path, monkeypatch):
    key_path = Path(tmp_path) / "encrypted-key"
    paramiko.RSAKey.generate(1024).write_private_key_file(
        str(key_path),
        password="secret",
    )

    class EmptyAgent:
        def get_keys(self):
            return ()

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "Agent", EmptyAgent)
    manager = TunnelManager()

    with pytest.raises(SshPassphraseRequired) as exc_info:
        manager.ensure_tunnel(
            "private-db",
            {
                "host": "jump.example.com",
                "user": "db-user",
                "key_path": str(key_path),
            },
            "database.internal",
            5432,
        )

    message = str(exc_info.value)
    assert "private-db" in message
    assert "jump.example.com" in message
    assert str(key_path) in message


def test_garbage_key_file_is_typed_and_names_path(tmp_path):
    key_path = Path(tmp_path) / "garbage-key"
    key_path.write_text("this is not a private key\n", encoding="utf-8")
    manager = TunnelManager()

    with pytest.raises(SshKeyError) as exc_info:
        manager.ensure_tunnel(
            "private-db",
            {
                "host": "jump.example.com",
                "user": "db-user",
                "key_path": str(key_path),
            },
            "database.internal",
            5432,
        )

    assert str(key_path) in str(exc_info.value)
    assert "Could not parse SSH private key" in str(exc_info.value)


def test_successful_tunnel_remembers_host_without_key(
    tmp_rdst_home, monkeypatch
):
    from shared.config.targets import TargetsConfig

    class FakeTransport:
        def __init__(self):
            self.active = True

        def is_active(self):
            return self.active

        def set_keepalive(self, seconds):
            self.keepalive = seconds

        def close(self):
            self.active = False

    class FakeClient:
        def __init__(self):
            self.transport = FakeTransport()

        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            self.connect_kwargs = kwargs

        def get_transport(self):
            return self.transport

        def close(self):
            self.transport.close()

    class FakeListener:
        def __init__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            pass

        def listen(self):
            pass

        def settimeout(self, timeout):
            pass

        def getsockname(self):
            return ("127.0.0.1", 41000)

        def accept(self):
            raise OSError("closed")

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "SSHClient", FakeClient)
    monkeypatch.setattr("shared.ssh_tunnel.socket.socket", FakeListener)
    save_calls = []
    original_save = TargetsConfig.save

    def tracked_save(config):
        save_calls.append(config.path)
        original_save(config)

    monkeypatch.setattr(TargetsConfig, "save", tracked_save)
    manager = TunnelManager()
    key_path = tmp_rdst_home.parent / "jump.pem"
    paramiko.RSAKey.generate(1024).write_private_key_file(str(key_path))
    try:
        manager.ensure_tunnel(
            "orders",
            {
                "host": "jump.example.com",
                "port": 2222,
                "user": "ops",
                "key_path": str(key_path),
            },
            "orders.internal",
            5432,
        )
        manager.ensure_tunnel(
            "users",
            {
                "host": "jump.example.com",
                "port": 2222,
                "user": "ops",
                "key_path": str(key_path),
            },
            "users.internal",
            5432,
        )
        config = TargetsConfig()
        config.load()
        names = config.list_ssh_hosts()
        assert len(names) == 1
        assert config.get_ssh_host(names[0]) == {
            "host": "jump.example.com",
            "port": 2222,
            "user": "ops",
        }
        assert len(save_calls) == 1
    finally:
        manager.close_all()


def test_remember_ssh_host_reports_only_real_changes(tmp_path):
    from shared.config.targets import TargetsConfig

    config = TargetsConfig(str(tmp_path / "config.toml"))
    config.load()

    assert config.remember_ssh_host("jump.example.com", 22, "ops") is True
    assert config.remember_ssh_host("jump.example.com", 22, "ops") is False
    assert config.remember_ssh_host("jump.example.com", 2222, "ops") is True
    assert len(config.list_ssh_hosts()) == 2
    assert {
        config.get_ssh_host(name)["port"] for name in config.list_ssh_hosts()
    } == {22, 2222}


def test_ssh_config_supplies_host_user_port_and_identity(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "Host jump-alias\n"
        "    HostName 192.0.2.10\n"
        "    User config-user\n"
        "    Port 2222\n"
        "    IdentityFile ~/config-key\n",
        encoding="utf-8",
    )

    resolved = TunnelManager()._resolve_ssh_config(
        "private-db",
        {"host": "jump-alias"},
    )

    assert resolved == {
        "host": "192.0.2.10",
        "port": 2222,
        "user": "config-user",
        "key_path": str(tmp_path / "config-key"),
    }


def test_resolve_connection_params_uses_tunnel_and_preserves_real_endpoint():
    target_config = {
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5433,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "ssh": {"host": "jump.example.com"},
    }

    with patch("shared.ssh_tunnel.get_tunnel_manager") as get_manager:
        get_manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        result = resolve_connection_params("private-db", target_config)

    get_manager.return_value.ensure_tunnel.assert_called_once_with(
        "private-db",
        target_config["ssh"],
        "database.internal",
        5433,
    )
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 49152
    assert result["real_host"] == "database.internal"
    assert result["real_port"] == 5433


def test_resolve_connection_params_uses_form_name_instead_of_database_host():
    target_config = {
        "name": "unsaved-form-target",
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5432,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "ssh": {"host": "jump.example.com"},
    }

    with patch("shared.ssh_tunnel.get_tunnel_manager") as get_manager:
        get_manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        resolve_connection_params(target_config=target_config)

    get_manager.return_value.ensure_tunnel.assert_called_once_with(
        "unsaved-form-target",
        target_config["ssh"],
        "database.internal",
        5432,
    )


def test_resolve_connection_params_without_ssh_is_unchanged():
    target_config = {
        "engine": "postgres",
        "host": "db.example.com",
        "port": 5434,
        "username": "app",
        "password": "password",
        "dbname": "appdb",
        "tls": True,
        "read_only": True,
        "password_env": "APP_DB_PASSWORD",
    }

    assert resolve_connection_params(target_config=target_config) == {
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5434,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "tls": True,
        "sslmode": "require",
        "tls_verify": False,
        "tls_ca": None,
        "read_only": True,
        "password_env": "APP_DB_PASSWORD",
    }


def test_resolve_connection_params_enables_verified_tls_with_ca():
    result = resolve_connection_params(
        target_config={
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "user": "app",
            "password": "password",
            "database": "appdb",
            "tls": True,
            "tls_verify": True,
            "tls_ca": "/certs/root.pem",
        }
    )

    assert result["sslmode"] == "verify-full"
    assert result["tls_verify"] is True
    assert result["tls_ca"] == "/certs/root.pem"


def test_resolve_connection_params_splits_verified_tls_identity_from_tunnel():
    target_config = {
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5432,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "tls_verify": True,
        "ssh": {"host": "jump.example.com"},
    }

    with patch("shared.ssh_tunnel.get_tunnel_manager") as get_manager:
        get_manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        result = resolve_connection_params("private-db", target_config)

    assert result["host"] == "database.internal"
    assert result["hostaddr"] == "127.0.0.1"
    assert result["port"] == 49152
    assert result["real_host"] == "database.internal"


def test_cancel_query_uses_resolved_mysql_tunnel_endpoint():
    target_config = {
        "engine": "mysql",
        "host": "database.internal",
        "port": 3306,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "ssh": {"host": "jump.example.com"},
    }
    connection = MagicMock()
    connection.thread_id.return_value = 42

    with (
        patch("shared.ssh_tunnel.get_tunnel_manager") as get_manager,
        patch("shared.db_connection._create_mysql_connection") as connect,
    ):
        get_manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        assert cancel_query(connection, "mysql", target_config) is True

    get_manager.return_value.ensure_tunnel.assert_called_once_with(
        "database.internal",
        target_config["ssh"],
        "database.internal",
        3306,
    )
    assert connect.call_args.args[:2] == ("127.0.0.1", 49152)
    connect.return_value.cursor.return_value.execute.assert_called_once_with(
        "KILL QUERY 42"
    )


def test_cancel_mysql_by_thread_id_uses_resolved_tunnel_endpoint():
    target_config = {
        "engine": "mysql",
        "host": "database.internal",
        "port": 3306,
        "user": "app",
        "password": "password",
        "database": "appdb",
        "ssh": {"host": "jump.example.com"},
    }

    with (
        patch("shared.ssh_tunnel.get_tunnel_manager") as get_manager,
        patch("shared.db_connection._create_mysql_connection") as connect,
    ):
        get_manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49153)
        assert cancel_mysql_by_thread_id(target_config, 43) is True

    get_manager.return_value.ensure_tunnel.assert_called_once_with(
        "database.internal",
        target_config["ssh"],
        "database.internal",
        3306,
    )
    assert connect.call_args.args[:2] == ("127.0.0.1", 49153)
    connect.return_value.cursor.return_value.execute.assert_called_once_with(
        "KILL QUERY 43"
    )


def test_reusable_ssh_profile_resolves_latest_config_at_tunnel_open(tmp_path):
    from shared.config.targets import TargetsConfig

    config = TargetsConfig(str(tmp_path / "config.toml"))
    config.load()
    config._data["ssh_hosts"]["prod-jump"] = {
        "host": "old.example.com",
        "port": 22,
    }
    config.save()

    with patch("shared.config.targets.TargetsConfig", return_value=config):
        first = TunnelManager()._resolve_ssh_config(
            "orders", {"profile": "prod-jump"}
        )
        config._data["ssh_hosts"]["prod-jump"] = {
            "host": "new.example.com",
            "port": 2222,
            "user": "ops",
        }
        config.save()
        second = TunnelManager()._resolve_ssh_config(
            "users", {"profile": "prod-jump"}
        )

    assert first["host"] == "old.example.com"
    assert second["host"] == "new.example.com"
    assert second["port"] == 2222
    assert second["user"] == "ops"


def test_missing_reusable_ssh_profile_is_typed_and_actionable(tmp_path):
    from shared.config.targets import TargetsConfig
    from shared.ssh_tunnel import SshConfigurationError

    config = TargetsConfig(str(tmp_path / "config.toml"))
    config.load()
    with patch("shared.config.targets.TargetsConfig", return_value=config):
        with pytest.raises(SshConfigurationError, match="Recreate the profile"):
            TunnelManager()._resolve_ssh_config(
                "orders", {"profile": "missing-jump"}
            )
