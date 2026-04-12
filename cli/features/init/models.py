"""Init models.

This slice owns first-run status checks and validation for configured targets
and Anthropic connectivity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InitStatus:
    initialized: bool
    targets: list[dict[str, Any]]
    default_target: str | None = None
    llm_configured: bool = False


@dataclass
class InitValidationResult:
    target_results: list[dict[str, Any]]
    llm_result: dict[str, Any]
