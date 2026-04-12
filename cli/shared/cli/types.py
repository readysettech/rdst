"""Shared CLI result models."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class RdstResult:
    ok: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None

    def __bool__(self):
        return self.ok

