"""Shared filesystem locations for RDST.

Exposed as functions, not module-level constants, so callers resolve
the path at *call* time rather than *import* time. This matters for
tests: monkeypatching ``HOME`` (or these getters directly) reaches
every site, instead of only the modules that read the constant via
attribute access.
"""

from pathlib import Path

__all__ = ["rdst_data_dir", "rdst_semantic_layer_dir"]


def rdst_data_dir() -> Path:
    """Return the RDST data directory (``~/.rdst`` by default)."""
    return Path.home() / ".rdst"


def rdst_semantic_layer_dir() -> Path:
    """Return the semantic-layer directory (``~/.rdst/semantic-layer``)."""
    return rdst_data_dir() / "semantic-layer"
