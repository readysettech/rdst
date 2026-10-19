"""A stable, salted identifier for this computer.

Hosted inference without sign-in is budgeted per machine, so reinstalling RDST keeps the
same allowance and trial clock. Only a salted SHA-256 of the operating system's machine id
leaves the process; the raw id is never sent or logged.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import subprocess
import threading
import uuid
from pathlib import Path

SALT = "rdst-machine-v1:"
_LINUX_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")
_MAC_UUID = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')

_lock = threading.Lock()
_cached: str | None = None

logger = logging.getLogger(__name__)


def _windows_machine_guid() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
        return str(value or "").strip() or None
    except Exception:
        return None


def _mac_platform_uuid() -> str | None:
    try:
        output = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                capture_output=True, text=True, timeout=5, check=False).stdout
    except Exception:
        return None
    match = _MAC_UUID.search(output or "")
    return match.group(1).strip() if match else None


def _linux_machine_id() -> str | None:
    for candidate in _LINUX_PATHS:
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def raw_machine_id() -> str | None:
    """The operating system's own id for this computer, or None when it can't be read.

    ``RDST_MACHINE_ID_OVERRIDE`` stands in for it so tests can simulate another computer.
    """
    override = os.environ.get("RDST_MACHINE_ID_OVERRIDE", "").strip()
    if override:
        return override
    system = platform.system()
    if system == "Windows":
        return _windows_machine_guid()
    if system == "Darwin":
        return _mac_platform_uuid()
    return _linux_machine_id()


def _device_id_fallback() -> str:
    """The install's random device id (``~/.rdst/device_id``), created when missing."""
    from shared.constants import rdst_data_dir

    path = rdst_data_dir() / "device_id"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        logger.debug("Could not persist the device id fallback")
    return value


def hash_id(raw: str) -> str:
    return hashlib.sha256(f"{SALT}{raw}".encode("utf-8")).hexdigest()


def machine_hash() -> str:
    """The salted SHA-256 of this machine's id, cached for the process."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = hash_id(raw_machine_id() or f"device:{_device_id_fallback()}")
        return _cached


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None


__all__ = ["SALT", "hash_id", "machine_hash", "raw_machine_id", "reset_cache"]
