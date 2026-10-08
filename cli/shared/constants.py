"""Shared filesystem locations for RDST.

Exposed as functions, not module-level constants, so callers resolve
the path at *call* time rather than *import* time. This matters for
tests: monkeypatching ``HOME`` (or these getters directly) reaches
every site, instead of only the modules that read the constant via
attribute access.

A packaged desktop flavor (staging, CL) runs as a named instance whose
data and keyring entries stay apart from production's. The instance comes
from ``RDST_INSTANCE``, else from the ``instance.json`` marker shipped
beside a frozen sidecar; production's marker names the empty instance.
"""

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "INSTANCE_ENV_VAR",
    "rdst_data_dir",
    "rdst_instance",
    "rdst_instance_suffix",
    "rdst_semantic_layer_dir",
    "rdst_sidecar_marker",
]

INSTANCE_ENV_VAR = "RDST_INSTANCE"
_INSTANCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@lru_cache(maxsize=None)
def _read_marker(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    # A marker that exists but can't be parsed raises: falling back to
    # production would mix this flavor's state into production's.
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"RDST instance marker {path} must hold a JSON object")
    return data


def rdst_sidecar_marker() -> dict | None:
    """Return the ``instance.json`` beside a frozen sidecar, or None without one."""
    if not getattr(sys, "frozen", False):
        return None
    return _read_marker(Path(sys.executable).resolve().parent / "instance.json")


def rdst_instance() -> str:
    """Return the instance name (``""`` for production).

    A non-blank name that isn't valid raises rather than falling back to
    production's data.
    """
    instance = os.environ.get(INSTANCE_ENV_VAR, "").strip()
    if not instance:
        marker = rdst_sidecar_marker()
        if marker is None:
            return ""
        # Production's marker names the empty instance; one that names none is broken.
        instance = marker.get("instance")
        if instance == "":
            return ""
        if not isinstance(instance, str) or not instance.strip():
            raise ValueError(f"RDST instance marker names no instance: {marker!r}")
        instance = instance.strip()
    if not _INSTANCE_RE.fullmatch(instance):
        raise ValueError(f"Invalid RDST instance name: {instance!r}")
    return instance


def rdst_instance_suffix() -> str:
    """Return ``-<instance>`` for a named instance, ``""`` for production."""
    instance = rdst_instance()
    return f"-{instance}" if instance else ""


def rdst_data_dir() -> Path:
    """Return the RDST data directory (``~/.rdst`` by default).

    ``RDST_DATA_DIR`` wins; a named instance uses ``~/.rdst-<instance>``.
    """
    configured = os.environ.get("RDST_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / f".rdst{rdst_instance_suffix()}"


def rdst_semantic_layer_dir() -> Path:
    """Return the semantic-layer directory (``~/.rdst/semantic-layer``)."""
    return rdst_data_dir() / "semantic-layer"
