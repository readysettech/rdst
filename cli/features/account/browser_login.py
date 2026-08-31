"""Launch the RDST-owned account UI for an interactive CLI sign-in."""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

import uvicorn

from features.account.service import account_service
from shared.api.app import create_app

LOGIN_TIMEOUT_SECONDS = 600


class BrowserLoginError(RuntimeError):
    """The local RDST account UI could not be started."""


def _frontend_dist() -> Path:
    configured = os.getenv("RDST_WEB_DIST_DIR")
    rdst_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(configured).expanduser() if configured else None,
        rdst_root.parent / "web-apps" / "apps" / "rdst" / "dist",
        rdst_root / "web_dist",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "index.html").is_file():
            return candidate.resolve()
    raise BrowserLoginError(
        "RDST account UI assets are missing. Reinstall RDST or build the rdst-web app."
    )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_ready(url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise BrowserLoginError("The local RDST account UI did not start")


def run_browser_login(
    timeout: float = LOGIN_TIMEOUT_SECONDS,
    on_ready: Callable[[str], None] | None = None,
) -> str:
    """Serve the bundled RDST UI temporarily and wait for OAuth completion."""
    dist = _frontend_dist()
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    app = create_app(static_dist_dir=str(dist))
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    account_service.prepare_browser_login()
    thread.start()
    try:
        _wait_until_ready(base_url)
        login_url = f"{base_url}/account-login"
        if on_ready is not None:
            on_ready(login_url)
        webbrowser.open(login_url)
        if not account_service.wait_for_browser_login(timeout):
            raise BrowserLoginError(
                f"Readyset sign-in timed out. Open {login_url} and try again."
            )
        return login_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
