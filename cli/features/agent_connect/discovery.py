"""desktop-server.json: how local tools find the running RDST server and its MCP endpoint.

The file lives in the RDST data directory with owner-only permissions. It's rewritten on
every start and whenever the WSL address changes, and removed on a clean shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from shared.constants import rdst_data_dir

logger = logging.getLogger(__name__)

FILE_NAME = "desktop-server.json"
# How often a Windows server looks for the WSL adapter again: WSL can start, restart or
# take a new address while RDST is running.
WSL_PROBE_SECONDS = 30
# The only path the WSL-facing listener serves; everything else stays loopback-only.
WSL_PATHS = ("/mcp",)


def discovery_path() -> Path:
    return rdst_data_dir() / FILE_NAME


def local_url(host: str, port: int) -> str:
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def write_discovery(info: dict[str, Any], path: Path | None = None) -> Path:
    """Write ``info`` atomically, readable by the owner only."""
    path = path or discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".desktop-server.", dir=str(path.parent))
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return path


def remove_discovery(pid: int | None = None, path: Path | None = None) -> None:
    """Remove the file if it still describes this process (a newer start may own it)."""
    path = path or discovery_path()
    pid = os.getpid() if pid is None else pid
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if current.get("pid") == pid:
        try:
            path.unlink()
        except OSError:
            pass


def read_discovery(path: Path | None = None) -> dict[str, Any] | None:
    """The running server's discovery record, or None when there's no live server."""
    path = path or discovery_path()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("url"):
        return None
    try:
        import psutil

        if info.get("pid") and not psutil.pid_exists(int(info["pid"])):
            return None
    except Exception:
        pass
    return info


def wsl_adapter_ip() -> str | None:
    """The Windows host's IPv4 address on the WSL virtual adapter, when there is one."""
    if sys.platform != "win32":
        return None
    try:
        import socket

        import psutil

        for name, addresses in psutil.net_if_addrs().items():
            if not name.lower().startswith("vethernet (wsl"):
                continue
            for address in addresses:
                if address.family == socket.AF_INET and address.address:
                    return address.address
    except Exception:
        logger.debug("Couldn't read the WSL adapter address", exc_info=True)
    return None


def wsl_only(app: Any) -> Any:
    """``app`` narrowed to the paths an agent inside WSL needs."""

    async def asgi(scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in WSL_PATHS:
            await app(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Not Found"}'})

    return asgi


class WslListener:
    """A second listener on the WSL adapter, sharing the main app and its event loop."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(wsl_only(app), host=host, port=port, lifespan="off",
                                log_level="warning", access_log=False)
        self.server = uvicorn.Server(config)
        self.host = host
        self.url = local_url(host, port)
        self._task: asyncio.Task | None = None

    async def start(self) -> bool:
        async def run() -> None:
            try:
                # Not serve(): the main server owns the process's signal handlers.
                await self.server._serve()
            except (SystemExit, Exception):
                logger.warning("The WSL listener on %s stopped", self.url, exc_info=True)

        self._task = asyncio.create_task(run())
        for _ in range(50):
            if self.server.started or self._task.done():
                break
            await asyncio.sleep(0.05)
        return bool(self.server.started)

    async def stop(self) -> None:
        self.server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


class AgentConnectRuntime:
    """Writes the discovery file and runs the WSL listener for one server start."""

    def __init__(self) -> None:
        self.info: dict[str, Any] | None = None
        self.wsl: WslListener | None = None
        self._probe: asyncio.Task | None = None

    async def start(self, app: Any) -> None:
        app.state.agent_connect_runtime = self
        port_text = os.environ.get("RDST_WEB_PORT", "").strip()
        if not port_text.isdecimal():
            # Not started by `rdst web` (tests, embedded use): nothing to advertise.
            return
        port = int(port_text)
        host = os.environ.get("RDST_WEB_HOST", "127.0.0.1")
        info: dict[str, Any] = {"url": local_url(host, port), "port": port,
                                "pid": os.getpid(), "started_at": int(time.time())}
        await self._sync_wsl(app, info)
        self._publish(app, info)
        if sys.platform == "win32":
            self._probe = asyncio.create_task(self._probe_wsl(app, info))

    def _publish(self, app: Any, info: dict[str, Any]) -> None:
        app.state.agent_connect = info
        try:
            write_discovery(info)
            self.info = info
        except OSError:
            logger.warning("Couldn't write %s; agents can still connect from the app",
                           discovery_path(), exc_info=True)

    async def _sync_wsl(self, app: Any, info: dict[str, Any]) -> bool:
        """Serve the WSL adapter's current address; True when ``wsl_url`` changed."""
        ip = await asyncio.to_thread(wsl_adapter_ip)
        if self.wsl is not None and self.wsl.host != ip:
            await self.wsl.stop()
            self.wsl = None
        if ip and self.wsl is None:
            listener = WslListener(app, ip, info["port"])
            if await listener.start():
                self.wsl = listener
        before = info.get("wsl_url")
        if self.wsl is not None:
            info["wsl_url"] = self.wsl.url
        else:
            info.pop("wsl_url", None)
        return info.get("wsl_url") != before

    async def _probe_wsl(self, app: Any, info: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(WSL_PROBE_SECONDS)
            try:
                if await self._sync_wsl(app, info):
                    self._publish(app, info)
            except Exception:
                logger.debug("Couldn't refresh the WSL listener", exc_info=True)

    async def stop(self) -> None:
        if self._probe is not None:
            self._probe.cancel()
            self._probe = None
        if self.wsl is not None:
            await self.wsl.stop()
            self.wsl = None
        if self.info is not None:
            remove_discovery()
            self.info = None
