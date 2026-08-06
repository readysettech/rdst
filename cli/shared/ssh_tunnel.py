"""In-process SSH port forwarding for database targets."""

from __future__ import annotations

import atexit
import getpass
import logging
import os
import select
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


MAX_TUNNELS = 10
KEEPALIVE_SECONDS = 30
ACCEPT_READY_TIMEOUT_SECONDS = 2.0
CHANNEL_OPEN_RETRY_SECONDS = 0.25

logger = logging.getLogger(__name__)


def _ensure_known_hosts_file() -> str:
    """Return the user's known_hosts path, creating it if absent.

    paramiko refuses to load a file that is not there, and the file has to
    exist before it can record anything.
    """
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    known_hosts = ssh_dir / "known_hosts"
    if not known_hosts.exists():
        known_hosts.touch(mode=0o600)
    return str(known_hosts)


class SshTunnelError(RuntimeError):
    """Base error for SSH tunnel failures."""


class SshConfigurationError(SshTunnelError):
    """Raised when SSH tunnel configuration is invalid."""


class SshKeyError(SshTunnelError):
    """Raised when an SSH private key cannot be used."""

    def __init__(self, message: str, key_path: Optional[str] = None):
        super().__init__(message)
        self.key_path = key_path


class SshPassphraseRequired(SshKeyError):
    """Raised when an SSH private key requires a passphrase."""


class SshAuthenticationError(SshTunnelError):
    """Raised when the jump host rejects SSH authentication."""


class SshConnectionError(SshTunnelError):
    """Raised when the jump host cannot be reached."""


@dataclass
class _Tunnel:
    target: str
    jump_host: str
    local_port: int
    transport: Any
    listener: socket.socket
    last_used: float
    raw_config_fingerprint: tuple = field(default_factory=tuple)
    config_fingerprint: tuple = field(default_factory=tuple)
    destination: tuple[str, int] = ("", 0)
    stop_event: threading.Event = field(default_factory=threading.Event)
    accept_ready: threading.Event = field(default_factory=threading.Event)
    clients: set = field(default_factory=set)
    clients_lock: threading.Lock = field(default_factory=threading.Lock)
    accept_thread: Optional[threading.Thread] = None


class TunnelManager:
    """Manage per-target SSH tunnels within the current process."""

    def __init__(self, max_tunnels: int = MAX_TUNNELS):
        self._max_tunnels = max_tunnels
        self._tunnels: Dict[str, _Tunnel] = {}
        self._lock = threading.RLock()
        self._target_locks: Dict[str, threading.RLock] = {}

    def _target_lock(self, target_name: str) -> threading.RLock:
        """Return the stable lifecycle lock for one target."""
        with self._lock:
            return self._target_locks.setdefault(target_name, threading.RLock())

    @contextmanager
    def target_lifecycle(self, target_name: str):
        """Keep a target's refresh and immediate connection probe atomic."""
        with self._target_lock(target_name):
            yield

    def ensure_tunnel(
        self,
        target_name: str,
        ssh_cfg: dict,
        dest_host: str,
        dest_port: int,
        passphrase: Optional[str] = None,
        force_fresh: bool = False,
    ) -> Tuple[str, int]:
        """Return a live local endpoint for a target, opening it if needed."""
        # A forced refresh is one lifecycle operation. Keeping close + reopen
        # under this target lock prevents another resolve from returning the
        # endpoint between those two steps and then having it closed underneath
        # its first database connection.
        with self._target_lock(target_name):
            with self._lock:
                raw_fingerprint = self._raw_config_fingerprint(ssh_cfg)
                destination = (str(dest_host), int(dest_port))
                tunnel = self._tunnels.get(target_name)
                fully_inline = (
                    not ssh_cfg.get("profile")
                    and bool(ssh_cfg.get("user"))
                    and bool(ssh_cfg.get("key_path"))
                )
                if (
                    not force_fresh
                    and fully_inline
                    and tunnel is not None
                    and tunnel.transport.is_active()
                    and tunnel.raw_config_fingerprint == raw_fingerprint
                    and tunnel.destination == destination
                ):
                    tunnel.last_used = time.time()
                    return ("127.0.0.1", tunnel.local_port)

                resolved = self._resolve_ssh_config(target_name, ssh_cfg)
                fingerprint = self._config_fingerprint(ssh_cfg, resolved)
                if (
                    not force_fresh
                    and tunnel is not None
                    and tunnel.transport.is_active()
                    and tunnel.config_fingerprint == fingerprint
                    and tunnel.destination == destination
                ):
                    tunnel.raw_config_fingerprint = raw_fingerprint
                    tunnel.last_used = time.time()
                    return ("127.0.0.1", tunnel.local_port)

                if tunnel is not None:
                    self._tunnels.pop(target_name, None)
                    self._close_tunnel(tunnel)

                if len(self._tunnels) >= self._max_tunnels:
                    oldest = min(
                        self._tunnels.values(), key=lambda item: item.last_used
                    )
                    self._tunnels.pop(oldest.target, None)
                    self._close_tunnel(oldest)

                if passphrase is None:
                    tunnel = self._open_tunnel(
                        target_name,
                        ssh_cfg,
                        dest_host,
                        int(dest_port),
                    )
                else:
                    tunnel = self._open_tunnel(
                        target_name,
                        ssh_cfg,
                        dest_host,
                        int(dest_port),
                        passphrase=passphrase,
                    )
                tunnel.raw_config_fingerprint = raw_fingerprint
                tunnel.config_fingerprint = fingerprint
                tunnel.destination = destination
                self._tunnels[target_name] = tunnel
                return ("127.0.0.1", tunnel.local_port)

    @staticmethod
    def _raw_config_fingerprint(ssh_cfg: dict) -> tuple:
        """Identify the SSH fields exactly as supplied by the caller."""
        return tuple(
            ssh_cfg.get(field)
            for field in ("host", "port", "user", "key_path", "profile")
        )

    @staticmethod
    def _config_fingerprint(ssh_cfg: dict, resolved: dict) -> tuple:
        """Identify the effective SSH path, including its reusable profile ref."""
        return (
            str(resolved["host"]),
            int(resolved["port"]),
            str(resolved["user"]),
            str(resolved.get("key_path") or ""),
            str(ssh_cfg.get("profile") or ""),
        )

    def status(self) -> List[dict]:
        """Return current tunnel state for management surfaces."""
        with self._lock:
            return [
                {
                    "target": tunnel.target,
                    "jump_host": tunnel.jump_host,
                    "local_port": tunnel.local_port,
                    "state": (
                        "active"
                        if not tunnel.stop_event.is_set()
                        and tunnel.transport.is_active()
                        else "dead"
                    ),
                    "last_used": tunnel.last_used,
                }
                for tunnel in self._tunnels.values()
            ]

    def close(self, target_name: str) -> None:
        """Close and forget a target's tunnel."""
        with self._target_lock(target_name):
            with self._lock:
                tunnel = self._tunnels.pop(target_name, None)
            if tunnel is not None:
                self._close_tunnel(tunnel)

    def close_all(self) -> None:
        """Close and forget every managed tunnel."""
        with self._lock:
            tunnels = list(self._tunnels.values())
            self._tunnels.clear()
        for tunnel in tunnels:
            self._close_tunnel(tunnel)

    def _open_tunnel(
        self,
        target_name: str,
        ssh_cfg: dict,
        dest_host: str,
        dest_port: int,
        passphrase: Optional[str] = None,
    ) -> _Tunnel:
        import paramiko

        resolved = self._resolve_ssh_config(target_name, ssh_cfg)
        jump_host = resolved["host"]
        key_path = resolved.get("key_path")
        pkey = None

        if key_path:
            if not Path(key_path).is_file():
                raise SshKeyError(
                    self._message(
                        target_name,
                        jump_host,
                        f"SSH key file does not exist: {key_path}",
                    ),
                    key_path=key_path,
                )
            pkey = self._load_private_key(
                paramiko,
                target_name,
                jump_host,
                key_path,
                passphrase,
            )

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        # AutoAddPolicy only persists a newly seen key when a host-keys file is
        # set, and load_system_host_keys() does not set one. Without this the
        # first sight of a host is accepted and then forgotten, so every later
        # connection accepts whatever answers -- there is nothing to compare
        # against. Loading the file gives accept-new semantics instead: unknown
        # hosts are recorded, and a key that changes afterwards is refused.
        client.load_host_keys(_ensure_known_hosts_file())
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_args = {
                "hostname": jump_host,
                "port": resolved["port"],
                "username": resolved["user"],
                "allow_agent": True,
                "look_for_keys": False,
                "timeout": 10,
                "banner_timeout": 10,
                "auth_timeout": 10,
            }
            if pkey is not None:
                connect_args["pkey"] = pkey
            else:
                connect_args["key_filename"] = None
                connect_args["passphrase"] = passphrase
            client.connect(
                **connect_args,
            )
        except paramiko.PasswordRequiredException as exc:
            client.close()
            raise SshPassphraseRequired(
                self._message(
                    target_name,
                    jump_host,
                    f"SSH key requires a passphrase: {key_path}",
                )
            ) from exc
        except paramiko.AuthenticationException as exc:
            client.close()
            raise SshAuthenticationError(
                self._message(
                    target_name,
                    jump_host,
                    "SSH authentication failed; check the user, key, and ssh-agent",
                )
            ) from exc
        except (paramiko.ssh_exception.NoValidConnectionsError, OSError) as exc:
            client.close()
            raise SshConnectionError(
                self._message(
                    target_name,
                    jump_host,
                    f"Could not reach SSH jump host: {exc}",
                )
            ) from exc
        except paramiko.SSHException as exc:
            client.close()
            if "authentication" in str(exc).lower():
                raise SshAuthenticationError(
                    self._message(
                        target_name,
                        jump_host,
                        "SSH authentication failed; check the user, key, and ssh-agent",
                    )
                ) from exc
            raise SshTunnelError(
                self._message(target_name, jump_host, f"SSH setup failed: {exc}")
            ) from exc

        transport = client.get_transport()
        if transport is None or not transport.is_active():
            client.close()
            raise SshConnectionError(
                self._message(
                    target_name,
                    jump_host,
                    "SSH connection closed during setup",
                )
            )
        transport.set_keepalive(KEEPALIVE_SECONDS)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.5)
        except OSError as exc:
            listener.close()
            client.close()
            raise SshTunnelError(
                self._message(
                    target_name,
                    jump_host,
                    f"Could not open local tunnel listener: {exc}",
                )
            ) from exc

        tunnel = _Tunnel(
            target=target_name,
            jump_host=jump_host,
            local_port=listener.getsockname()[1],
            transport=transport,
            listener=listener,
            last_used=time.time(),
        )
        tunnel.accept_thread = threading.Thread(
            target=self._accept_connections,
            args=(tunnel, dest_host, dest_port),
            name=f"rdst-ssh-{target_name}",
            daemon=True,
        )
        tunnel.accept_thread.start()
        if not tunnel.accept_ready.wait(ACCEPT_READY_TIMEOUT_SECONDS):
            self._close_tunnel(tunnel)
            raise SshTunnelError(
                self._message(
                    target_name,
                    jump_host,
                    "Local tunnel listener did not become ready",
                )
            )
        self._remember_jump_host(resolved)
        return tunnel

    def _resolve_ssh_config(self, target_name: str, ssh_cfg: dict) -> dict:
        import paramiko

        profile_name = ssh_cfg.get("profile")
        if profile_name:
            from shared.config.targets import TargetsConfig

            config = TargetsConfig()
            config.load()
            profile = config.get_ssh_host(str(profile_name))
            if profile is None:
                raise SshConfigurationError(
                    f"SSH profile '{profile_name}' used by target '{target_name}' "
                    "was not found. Recreate the profile or choose another jump host."
                )
            ssh_cfg = profile

        configured_host = ssh_cfg.get("host")
        if not configured_host:
            raise SshConfigurationError(
                f"SSH tunnel for target '{target_name}' requires a jump host"
            )

        lookup: dict = {}
        config_path = Path.home() / ".ssh" / "config"
        if config_path.is_file():
            try:
                config = paramiko.SSHConfig()
                with config_path.open(encoding="utf-8") as config_file:
                    config.parse(config_file)
                lookup = config.lookup(str(configured_host))
            except (OSError, paramiko.SSHException) as exc:
                raise SshConfigurationError(
                    self._message(
                        target_name,
                        str(configured_host),
                        f"Could not read SSH config: {exc}",
                    )
                ) from exc

        identity_files = lookup.get("identityfile") or []
        key_path = ssh_cfg.get("key_path")
        if not key_path and identity_files:
            key_path = identity_files[0]
        if key_path:
            key_path = os.path.expandvars(os.path.expanduser(str(key_path)))

        port_value = ssh_cfg.get("port", lookup.get("port", 22))
        try:
            port = int(port_value)
        except (TypeError, ValueError) as exc:
            raise SshConfigurationError(
                self._message(
                    target_name,
                    str(configured_host),
                    f"Invalid SSH port: {port_value}",
                )
            ) from exc

        return {
            "host": lookup.get("hostname", configured_host),
            "port": port,
            "user": ssh_cfg.get("user") or lookup.get("user") or getpass.getuser(),
            "key_path": key_path,
        }

    @staticmethod
    def _load_private_key(
        paramiko: Any,
        target_name: str,
        jump_host: str,
        key_path: str,
        passphrase: Optional[str] = None,
    ) -> Any:
        key_types = (
            paramiko.RSAKey,
            paramiko.ECDSAKey,
            paramiko.Ed25519Key,
        )
        for key_type in key_types:
            try:
                return key_type.from_private_key_file(key_path, password=passphrase)
            except paramiko.PasswordRequiredException as exc:
                raise SshPassphraseRequired(
                    TunnelManager._message(
                        target_name,
                        jump_host,
                        f"SSH key requires a passphrase: {key_path}",
                    ),
                    key_path=key_path,
                ) from exc
            except (OSError, ValueError, paramiko.SSHException):
                continue
        raise SshKeyError(
            TunnelManager._message(
                target_name,
                jump_host,
                f"Could not parse SSH private key: {key_path}",
            ),
            key_path=key_path,
        )

    @staticmethod
    def _remember_jump_host(resolved: dict) -> None:
        try:
            from shared.config.targets import TargetsConfig

            config = TargetsConfig()
            config.load()
            changed = config.remember_ssh_host(
                str(resolved["host"]),
                int(resolved["port"]),
                str(resolved["user"]) if resolved.get("user") else None,
            )
            if changed:
                config.save()
        except Exception:
            pass

    @staticmethod
    def _accept_connections(
        tunnel: _Tunnel,
        dest_host: str,
        dest_port: int,
    ) -> None:
        tunnel.accept_ready.set()
        while not tunnel.stop_event.is_set():
            try:
                client, address = tunnel.listener.accept()
            except socket.timeout:
                continue
            except (OSError, ValueError) as exc:
                if not (
                    tunnel.stop_event.is_set()
                    or TunnelManager._endpoint_is_closed(tunnel.listener)
                ):
                    logger.warning(
                        "SSH tunnel listener for %s stopped unexpectedly: %s",
                        tunnel.target,
                        exc,
                    )
                break

            with tunnel.clients_lock:
                tunnel.clients.add(client)
            threading.Thread(
                target=TunnelManager._forward_connection,
                args=(tunnel, client, address, dest_host, dest_port),
                name=f"rdst-ssh-client-{tunnel.target}",
                daemon=True,
            ).start()

    @staticmethod
    def _forward_connection(
        tunnel: _Tunnel,
        client: socket.socket,
        address: tuple,
        dest_host: str,
        dest_port: int,
    ) -> None:
        import paramiko

        channel = None
        try:
            for attempt in range(2):
                try:
                    channel = tunnel.transport.open_channel(
                        "direct-tcpip",
                        (dest_host, dest_port),
                        address,
                    )
                except (OSError, EOFError, paramiko.SSHException):
                    if attempt > 0 or tunnel.stop_event.is_set():
                        raise
                if channel is not None:
                    break
                if attempt == 0 and not tunnel.stop_event.wait(
                    CHANNEL_OPEN_RETRY_SECONDS
                ):
                    continue
                break
            if channel is None:
                return

            pumps = (
                threading.Thread(
                    target=TunnelManager._pump,
                    args=(client, channel, tunnel.stop_event),
                    daemon=True,
                ),
                threading.Thread(
                    target=TunnelManager._pump,
                    args=(channel, client, tunnel.stop_event),
                    daemon=True,
                ),
            )
            for pump in pumps:
                pump.start()
            for pump in pumps:
                pump.join()
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            if channel is not None:
                channel.close()
            try:
                client.close()
            finally:
                with tunnel.clients_lock:
                    tunnel.clients.discard(client)

    @staticmethod
    def _pump(source: Any, destination: Any, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                readable, _, _ = select.select([source], [], [], 0.5)
                if not readable:
                    continue
                data = source.recv(65536)
                if not data:
                    break
                destination.sendall(data)
        except (OSError, ValueError, EOFError) as exc:
            if not (
                stop_event.is_set()
                or TunnelManager._endpoint_is_closed(source)
                or TunnelManager._endpoint_is_closed(destination)
            ):
                logger.warning("SSH tunnel relay stopped unexpectedly: %s", exc)
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except (AttributeError, OSError, ValueError):
                pass

    @staticmethod
    def _endpoint_is_closed(endpoint: Any) -> bool:
        if getattr(endpoint, "closed", False) is True:
            return True
        if getattr(endpoint, "_closed", False) is True:
            return True
        fileno = getattr(endpoint, "fileno", None)
        if not callable(fileno):
            return False
        try:
            descriptor = fileno()
        except (OSError, ValueError):
            return True
        return isinstance(descriptor, int) and descriptor < 0

    @staticmethod
    def _close_tunnel(tunnel: _Tunnel) -> None:
        tunnel.stop_event.set()
        try:
            tunnel.listener.close()
        except OSError:
            pass
        with tunnel.clients_lock:
            clients = list(tunnel.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        tunnel.transport.close()

    @staticmethod
    def _message(target_name: str, jump_host: str, detail: str) -> str:
        return (
            f"SSH tunnel for target '{target_name}' via jump host "
            f"'{jump_host}': {detail}"
        )


_tunnel_manager = TunnelManager()
atexit.register(_tunnel_manager.close_all)


def get_tunnel_manager() -> TunnelManager:
    """Return the process-wide SSH tunnel manager."""
    return _tunnel_manager
