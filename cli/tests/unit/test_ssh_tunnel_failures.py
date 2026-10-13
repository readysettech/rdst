"""SSH outages gate all callers and preserve a useful connection failure."""

import errno
import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import paramiko
import pytest

from features.configure.prerequisites import check_target
from features.monitor.connection import ConnectionWatch
from shared.api.ssh_errors import connectivity_error_payload
from shared.db_connection import create_direct_connection
from shared.monitor.monitor_store import MonitorStore
from shared.ssh_tunnel import (
    MAX_CHANNELS_PER_TUNNEL,
    SshTunnelError,
    TunnelManager,
    _Tunnel,
)
from shared.target_backoff import TargetBackoff

SSH = {"host": "bastion.example.com", "user": "ops"}
CONFIG = {"engine": "postgresql", "host": "database.internal", "port": 5432,
          "user": "app", "database": "app", "password": "unused", "ssh": SSH}


@pytest.fixture
def harness(monkeypatch):
    now = [0.0]
    backoff = TargetBackoff(lambda: now[0])
    manager = TunnelManager(backoff=backoff)
    manager._resolve_ssh_config = lambda target, cfg: {
        "host": cfg["host"], "port": 22, "user": "ops", "key_path": None,
    }
    monkeypatch.setattr("shared.ssh_tunnel._ensure_known_hosts_file", lambda: "unused")
    monkeypatch.setattr(manager, "_remember_jump_host", lambda resolved: None)
    state = SimpleNamespace(connect_error=None, channel_error=None, connects=0, channels=0,
                            clients=[], entered=None, release=None)

    class Transport:
        active = True
        log_channel = "shared.ssh_tunnel.transport"

        def is_active(self):
            return self.active

        def set_keepalive(self, value):
            pass

        def set_log_channel(self, value):
            self.log_channel = value

        def open_channel(self, *args, **kwargs):
            assert kwargs["timeout"] == 10
            state.channels += 1
            if state.channel_error:
                logging.getLogger(self.log_channel).error(
                    "Secsh channel 1 open FAILED: %s: Connect failed", state.channel_error
                )
                raise state.channel_error
            return MagicMock()

        def close(self):
            self.active = False

    class Client:
        def __init__(self):
            self.transport = Transport()
            state.clients.append(self)

        def load_system_host_keys(self):
            pass

        def load_host_keys(self, value):
            pass

        def set_missing_host_key_policy(self, value):
            pass

        def connect(self, **kwargs):
            state.connects += 1
            if state.entered:
                state.entered.set()
                assert state.release.wait(2)
            if state.connect_error:
                raise state.connect_error

        def get_transport(self):
            return self.transport

        def close(self):
            self.transport.close()

    monkeypatch.setattr(paramiko, "SSHClient", Client)
    monkeypatch.setattr("shared.ssh_tunnel.get_tunnel_manager", lambda: manager)
    yield manager, now, state
    manager.close_all()


def ensure(manager):
    return manager.ensure_tunnel("db", SSH, "database.internal", 5432)


UNREACHABLE = ("SSH jump host bastion.example.com:22 couldn't be reached. Check that it's running "
               "and that this computer can reach it.")


@pytest.mark.parametrize("phase,error,reason,cause", [
    ("connect", socket.gaierror(-2, "Name or service not known"),
     "SSH jump host bastion.example.com wasn't found. Check its host name.", "dns"),
    ("connect", paramiko.AuthenticationException("rejected"), "SSH authentication failed", None),
    ("channel", paramiko.ChannelException(2, "Name or service not known"),
     "SSH jump host couldn't find the database host database.internal.", "dns"),
    ("channel", paramiko.ChannelException(2, "Connection refused"),
     "SSH jump host reached database.internal:5432, but the database refused the connection.",
     "refused"),
    ("channel", socket.timeout("timed out"),
     "SSH jump host couldn't reach the database at database.internal:5432 in time.",
     "timeout"),
    ("connect", paramiko.ssh_exception.NoValidConnectionsError({
        ("192.0.2.1", 22): ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")}),
     "SSH jump host bastion.example.com:22 refused the connection.", "refused"),
    ("connect", paramiko.SSHException("No existing session"),
     UNREACHABLE, "connection"),
    ("connect", paramiko.SSHException("Error reading SSH protocol banner"),
     UNREACHABLE, "connection"),
])
def test_busy_callers_share_bounded_retries_and_surface_reason(harness, caplog, phase, error, reason, cause):
    manager, now, state = harness
    setattr(state, phase + "_error", error)
    caplog.set_level(logging.INFO)
    # One hundred requests per second for a minute: the whole SSH setup runs twice.
    for tick in range(6000):
        now[0] = tick / 100
        with pytest.raises(SshTunnelError) as caught:
            ensure(manager)
        assert reason in str(caught.value)
        assert getattr(caught.value, "cause", None) == cause
        traceback = caught.value.__traceback__
        depth = 0
        while traceback is not None:
            depth += 1
            traceback = traceback.tb_next
        assert depth < 10
    assert state.connects == 2
    assert state.channels == (2 if phase == "channel" else 0)
    assert not manager.status()
    assert all(not client.transport.active for client in state.clients)
    assert sum("SSH tunnel for db failed:" in row.message for row in caplog.records) == 1
    assert not any("Secsh channel" in row.message for row in caplog.records)
    payload = connectivity_error_payload(caught.value, "db", CONFIG)
    assert reason in payload["message"]
    if cause:
        assert payload["category"] == "ssh_jump_unreachable"
    result = check_target("db", CONFIG)
    assert reason in result["connection_error"]
    # An unmonitored target has no scheduled tunnel work, even after its deadline.
    now[0] = 86400
    assert state.connects == 2


def test_retry_deadlines_recovery_and_state_change_logs(harness, caplog):
    manager, now, state = harness
    caplog.set_level(logging.INFO)
    state.channel_error = paramiko.ChannelException(2, "Name or service not known")
    for delay in (30, 60, 120, 240, 480, 900, 900):
        assert manager.backoff.acquire("db")
        with pytest.raises(SshTunnelError):
            ensure(manager)
        # Reporting the driver failure does not advance the tunnel's deadline twice.
        manager.backoff.finish("db", success=False)
        assert manager.backoff.retry_after("db") == delay
        with pytest.raises(SshTunnelError):
            manager.ensure_tunnel("db", SSH, "database.internal", 5432, force_fresh=True)
        now[0] += delay
    assert state.connects == 7
    state.channel_error = paramiko.ChannelException(2, "Connection refused")
    with pytest.raises(SshTunnelError):
        ensure(manager)
    now[0] += 900
    state.channel_error = None
    ensure(manager)
    manager.backoff.finish("db", success=True)
    assert manager.failure_reason("db") is None
    assert not manager.backoff.failed("db")
    ensure(manager)
    messages = [row.message for row in caplog.records if row.name == "shared.ssh_tunnel"]
    assert len(messages) == 3
    assert "recovered" in messages[-1]


def test_only_one_setup_in_flight_per_target(harness):
    manager, now, state = harness
    state.connect_error = socket.gaierror(-2, "Name or service not known")
    state.entered, state.release = threading.Event(), threading.Event()

    def connect():
        with pytest.raises(SshTunnelError):
            ensure(manager)

    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(connect)
        assert state.entered.wait(1)
        rest = [pool.submit(connect) for _ in range(20)]
        state.release.set()
        first.result(timeout=2)
        for future in rest:
            future.result(timeout=2)
    assert state.connects == 1


def test_runtime_failure_stops_all_waiting_channels_and_closes_listener(harness):
    manager, now, state = harness
    ensure(manager)
    tunnel = manager._tunnels["db"]
    state.channel_error = paramiko.ChannelException(2, "Name or service not known")
    clients = [MagicMock() for _ in range(8)]
    tunnel.clients.update(clients)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(manager._forward_connection, tunnel, client,
                               ("127.0.0.1", 40000), "database.internal", 5432)
                   for client in clients]
        for future in futures:
            future.result(timeout=2)
    assert state.channels == 2  # Setup probe plus the one failing live channel.
    assert tunnel.stop_event.is_set()
    assert tunnel.listener.fileno() == -1
    assert not tunnel.transport.is_active()
    assert not tunnel.clients
    for _ in range(1000):
        with pytest.raises(SshTunnelError, match="couldn't find the database host"):
            ensure(manager)
    assert state.channels == 2


def test_accept_loop_caps_forwarding_workers(monkeypatch):
    clients = [MagicMock() for _ in range(50)]
    listener = MagicMock()
    listener.accept.side_effect = [(client, ("127.0.0.1", 1)) for client in clients] + [OSError("closed")]
    listener.fileno.return_value = -1
    tunnel = _Tunnel("db", "bastion", 40000, MagicMock(), listener, 0)
    worker = MagicMock()
    monkeypatch.setattr("shared.ssh_tunnel.threading.Thread", worker)
    TunnelManager._accept_connections(tunnel, "database.internal", 5432)
    assert worker.call_count == MAX_CHANNELS_PER_TUNNEL
    assert len(tunnel.clients) == MAX_CHANNELS_PER_TUNNEL
    assert sum(client.close.call_count for client in clients) == 50 - MAX_CHANNELS_PER_TUNNEL


def test_shared_failure_preserves_background_admission():
    backoff = TargetBackoff(lambda: 0)
    assert backoff.acquire("db")
    backoff.record_failure("db")
    assert not backoff.acquire("db")
    assert backoff._states["db"].active
    backoff.finish("db", success=False)
    assert backoff.retry_after("db") == 30


def test_reason_reaches_monitor_pulse_inbox_and_driver(harness, tmp_path, monkeypatch):
    manager, now, state = harness
    state.channel_error = paramiko.ChannelException(2, "Name or service not known")
    with pytest.raises(SshTunnelError):
        ensure(manager)
    reason = manager.failure_reason("db")
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    watch = ConnectionWatch(store)
    watch.observe("db", False, 1000)
    watch.collection("db", False, 1060)
    assert not store.read("SELECT id FROM inbox_item")
    watch.collection("db", False, 1120)
    body = json.loads(store.read("SELECT body_json FROM inbox_item")[0]["body_json"])
    assert body["error"] == reason
    assert reason in body["message"]
    assert [pulse["detail"] for pulse in store.pulses("db")] == [reason, reason]
    store.set_settings("db", {"enabled": False})
    watch.collection("db", False, 1180)
    assert len(store.pulses("db")) == 2

    # A channel can fail after params were resolved but before the driver returns.
    monkeypatch.setattr(manager, "ensure_tunnel", lambda *args, **kwargs: ("127.0.0.1", 40000))
    monkeypatch.setattr("shared.db_connection._create_postgres_connection",
                        MagicMock(side_effect=RuntimeError("server closed the connection")))
    with pytest.raises(SshTunnelError, match="couldn't find the database host"):
        create_direct_connection(CONFIG, target="db")


def test_unmonitored_observation_never_retries_failed_tunnel(harness, tmp_path, monkeypatch):
    manager, now, state = harness
    state.connect_error = socket.gaierror(-2, "Name or service not known")
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr("shared.monitor.monitor_store.default_monitor_db_path", lambda: store.path)
    with pytest.raises(SshTunnelError):
        create_direct_connection(CONFIG, target="db", lane="rdst/observe")
    for tick in range(1, 100):
        now[0] = tick * 900
        with pytest.raises(SshTunnelError):
            create_direct_connection(CONFIG, target="db", lane="rdst/observe")
    assert state.connects == 1
    store.set_settings("db", {"enabled": True})
    with pytest.raises(SshTunnelError):
        create_direct_connection(CONFIG, target="db", lane="rdst/observe")
    assert state.connects == 2


def test_superseded_tunnel_cannot_fail_its_replacement(harness):
    manager, now, state = harness
    ensure(manager)
    old = manager._tunnels["db"]
    manager.ensure_tunnel("db", SSH, "database.internal", 5432, force_fresh=True)
    old.on_failure(TunnelManager._connection_error(socket.gaierror(-2, "not found"), "old", 5432))
    assert manager.failure_reason("db") is None
    assert manager._tunnels["db"].transport.is_active()
