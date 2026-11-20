"""The agent key: how a coding agent on someone's own machine reaches a server's /mcp.

A server-mode RDST holds at most one agent key. Creating a key replaces the previous one,
so agents still using the old key lose access at once. Only a SHA-256 hash of the key is
stored (in ``$RDST_DATA_DIR/agent-key.json``, mode 0600); the key itself is shown once,
inside the connect command, and the server can't show it again.

The key admits a request to /mcp and nothing else: it can't open the UI or call the API.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from shared.constants import rdst_data_dir

PREFIX = "rdst_ak_"
_LAST_USED_EVERY_SECONDS = 60
_lock = threading.Lock()


def key_path() -> Path:
    return rdst_data_dir() / "agent-key.json"


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _load() -> dict[str, Any] | None:
    try:
        record = json.loads(key_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("hash") else None


def _save(record: dict[str, Any]) -> None:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    os.replace(tmp, path)


def create() -> str:
    """Make a new key, replacing any earlier one, and return it (the only time it's shown)."""
    key = PREFIX + secrets.token_urlsafe(32)
    with _lock:
        _save({"hash": _digest(key), "created_at": time.time(), "last_used_at": None})
    return key


def revoke() -> bool:
    """Delete the key. Returns whether there was one."""
    with _lock:
        try:
            key_path().unlink()
        except FileNotFoundError:
            return False
        return True


def status() -> dict[str, Any]:
    record = _load()
    if record is None:
        return {"exists": False, "created_at": None, "last_used_at": None}
    return {"exists": True, "created_at": record.get("created_at"),
            "last_used_at": record.get("last_used_at")}


def matches(candidate: str) -> bool:
    """Whether ``candidate`` is the current key. Records when it was last used."""
    if not candidate.startswith(PREFIX):
        return False
    with _lock:
        record = _load()
        if record is None or not hmac.compare_digest(_digest(candidate), str(record["hash"])):
            return False
        now = time.time()
        if now - float(record.get("last_used_at") or 0) >= _LAST_USED_EVERY_SECONDS:
            record["last_used_at"] = now
            _save(record)
    return True
