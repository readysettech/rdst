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


def test_guard_path_also_refuses_writes_hidden_in_a_select():
    runtime = _runtime_with_guard(GuardConfig(name="plain"))
    runtime._target_config = {"engine": "postgresql"}

    with pytest.raises(SafetyViolationError):
        runtime._validate_safety("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")
    with pytest.raises(SafetyViolationError):
        runtime._validate_safety("SELECT pg_catalog.\"setval\"('s', 1, true)")


def test_guard_denied_columns_resolve_aliases_and_stars():
    from features.guard.config import RestrictionsConfig

    guard = GuardConfig(
        name="pii", restrictions=RestrictionsConfig(denied_columns=["customers.ssn"])
    )
    runtime = _runtime_with_guard(guard)
    runtime._target_config = {"engine": "postgresql"}
    runtime._tables = {"customers": ["id", "name", "ssn"], "orders": ["id"]}

    for sql in ("SELECT * FROM customers", "SELECT c.ssn AS value FROM customers c"):
        with pytest.raises(SafetyViolationError):
            runtime._validate_safety(sql)
    runtime._validate_safety("SELECT c.name FROM customers c WHERE c.id = 1")


def test_inline_restrictions_apply_without_read_only():
    from features.agent.config import RestrictionsConfig, SafetyConfig

    config = AgentConfig(
        name="open", target="db", safety=SafetyConfig(read_only=False),
        restrictions=RestrictionsConfig(allowed_tables=["public.customers"]),
    )
    runtime = AgentRuntime(config)
    runtime._target_config = {"engine": "postgresql"}

    assert runtime._has_restrictions()
    with pytest.raises(SafetyViolationError):
        runtime._validate_safety("SELECT ssn FROM secret.customers")


@pytest.mark.parametrize("use_guard", [True, False])
def test_schema_is_read_once_even_when_the_read_fails(monkeypatch, use_guard):
    """Each statement must not repeat an information_schema scan, on success or failure."""
    from features.agent import runtime as runtime_module
    from features.agent.config import RestrictionsConfig as AgentRestrictions
    from features.guard.config import RestrictionsConfig

    reads = []
    result = {"value": None}
    monkeypatch.setattr("features.guard.checker.load_default_schema_tables",
                        lambda config, name: reads.append(name) or result["value"])
    clock = {"now": 1000.0}
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock["now"])

    if use_guard:
        runtime = _runtime_with_guard(GuardConfig(
            name="pii", restrictions=RestrictionsConfig(denied_columns=["customers.ssn"])))
    else:
        runtime = AgentRuntime(AgentConfig(
            name="pii", target="db",
            restrictions=AgentRestrictions(denied_columns=["customers.ssn"])))
    runtime._target_config = {"engine": "postgresql"}

    for _ in range(3):
        runtime._validate_safety("SELECT c.name FROM customers c")
        with pytest.raises(SafetyViolationError):
            # Without the columns a star cannot be checked, so it is refused.
            runtime._validate_safety("SELECT * FROM orders")
    assert len(reads) == 1

    clock["now"] += runtime_module.SCHEMA_RETRY_SECONDS
    result["value"] = {"customers": ["id", "name", "ssn"], "orders": ["id"]}
    runtime._validate_safety("SELECT * FROM orders")
    runtime._validate_safety("SELECT * FROM orders")
    assert len(reads) == 2
