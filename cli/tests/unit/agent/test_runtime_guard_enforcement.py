"""Regression tests for guard-backed enforcement inside AgentRuntime.

Exercises the real guard check/mask path (no mocking of the guard module)
so a broken import or missing helper fails here instead of at chat time.
"""

from __future__ import annotations

import pytest

from features.agent.config import AgentConfig
from features.agent.runtime import AgentRuntime, SafetyViolationError
from features.guard.config import GuardConfig, GuardsConfig, MaskingConfig


def _runtime_with_guard(guard: GuardConfig) -> AgentRuntime:
    config = AgentConfig(name="guarded", target="db", guard=guard.name)
    runtime = AgentRuntime(config)
    runtime._guard_config = guard
    return runtime


def test_validate_safety_blocks_via_guard():
    guard = GuardConfig(name="strict", guards=GuardsConfig(require_where=True))
    runtime = _runtime_with_guard(guard)

    with pytest.raises(SafetyViolationError, match="WHERE"):
        runtime._validate_safety("SELECT id FROM orders")


def test_validate_safety_passes_via_guard():
    guard = GuardConfig(name="strict", guards=GuardsConfig(require_where=True))
    runtime = _runtime_with_guard(guard)

    runtime._validate_safety("SELECT id FROM orders WHERE id = 5")


def test_apply_masking_via_guard():
    guard = GuardConfig(
        name="masked", masking=MaskingConfig(patterns={"*.email": "email"})
    )
    runtime = _runtime_with_guard(guard)

    rows = runtime._apply_masking(["id", "email"], [[1, "alice@example.com"]])
    assert rows[0][0] == 1
    assert rows[0][1] != "alice@example.com"
    assert "@" in rows[0][1]
