"""Shared constants for RDST filesystem locations."""

from pathlib import Path

__all__ = ["RDST_DATA_DIR", "RDST_SEMANTIC_LAYER_DIR"]

RDST_DATA_DIR = Path.home() / ".rdst"
RDST_SEMANTIC_LAYER_DIR = RDST_DATA_DIR / "semantic-layer"
