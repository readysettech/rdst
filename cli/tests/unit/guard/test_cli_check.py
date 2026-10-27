"""`rdst guard check` resolves stars against the target it is given."""

from __future__ import annotations

import pytest

from features.guard.cli.command import GuardCommand
from features.guard.config import GuardConfig, RestrictionsConfig

TABLES = {"customers": ["id", "name", "ssn"], "orders": ["id", "customer_id", "total"]}


class _Manager:
    def get(self, name):
        return GuardConfig(
            name=name, restrictions=RestrictionsConfig(denied_columns=["customers.ssn"])
        )


class _Targets:
    def load(self):
        pass

    def get(self, name):
        return {"engine": "postgresql", "host": "h"} if name == "db" else None


@pytest.fixture
def command(monkeypatch):
    loaded = []
    monkeypatch.setattr("shared.config.targets.create_targets_config", lambda: _Targets())
    monkeypatch.setattr(
        "features.guard.checker.load_default_schema_tables",
        lambda config, name: loaded.append(name) or TABLES,
    )
    cmd = GuardCommand.__new__(GuardCommand)
    cmd.manager = _Manager()
    cmd.loaded = loaded
    return cmd


def test_star_resolves_against_the_target(command):
    allowed = command._check("SELECT * FROM orders", "pii", "db")
    blocked = command._check("SELECT o.* FROM customers o", "pii", "db")

    assert allowed.ok
    assert not blocked.ok
    assert "customers.ssn" in blocked.message
    assert command.loaded == ["db", "db"]


def test_without_a_target_a_star_warns_and_the_fix_is_named(command):
    result = command._check("SELECT * FROM orders", "pii", None)

    assert result.ok
    assert "ALLOWED (with warnings)" in result.message
    assert "--target" in result.message
    assert command.loaded == []


def test_explicit_denied_column_does_not_suggest_a_target(command):
    result = command._check("SELECT ssn FROM customers", "pii", None)

    assert not result.ok
    assert "--target" not in result.message


def test_unknown_target_is_an_error(command):
    result = command._check("SELECT 1", "pii", "nope")

    assert not result.ok
    assert "nope" in result.message
