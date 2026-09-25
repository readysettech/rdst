"""Timelines and checks of the local monitor scenario runner."""

import importlib.util
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/monitor_scenarios/scenarios.py"
spec = importlib.util.spec_from_file_location("monitor_scenarios", PATH)
scenarios = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scenarios  # dataclasses resolve their module while loading
spec.loader.exec_module(scenarios)


@pytest.mark.parametrize("judge_minutes", [5, 10])
def test_timelines_leave_room_for_baselines_and_recovery(judge_minutes):
    catalog = scenarios.scenarios(judge_minutes)
    assert set(catalog) == {"spike", "regression", "index_added", "muted"}
    for name in ("spike", "muted"):
        spike = next(step for step in catalog[name].steps if step.action == "rate:4")
        calm = next(step for step in catalog[name].steps if step.action == "rate:1")
        # Attention baselines need an hour of history plus one five-minute window.
        assert spike.at_minute >= 65
        assert calm.at_minute - spike.at_minute >= judge_minutes + 5
    steps = catalog["regression"].steps
    drop = next(step for step in steps if step.action.startswith("drop:"))
    back = [step for step in steps if step.action.startswith("create:")][-1]
    assert drop.at_minute >= 65 and back.at_minute - drop.at_minute >= judge_minutes + 5
    # No scenario talks about index verdicts any more.
    text = " ".join(step.expect for scenario in catalog.values() for step in scenario.steps)
    assert "verdict '" not in text and "Fix confirmed" not in text


class FakeApi:
    target = "localdev"

    def __init__(self, inbox=None, markers=None):
        self.inbox, self.markers = inbox or [], markers or []

    def target_path(self, rest):
        return rest

    def call(self, method, path, body=None):
        if path.startswith("/inbox"):
            state = "muted" if "state=muted" in path else "open"
            return {"items": [item for item in self.inbox if item["tab"] == state]}
        return {"fix": None, "markers": self.markers}


def test_expectations_match_the_contract_fields():
    item = {"type": "traffic_spike", "query_keys": ["k"], "created_at": 100, "muted_by": None,
            "tab": "open", "severity_band": "watch"}
    assert scenarios.expectation_met("spike", FakeApi([item]), "k", 50)
    assert not scenarios.expectation_met("spike", FakeApi([item]), "k", 150)
    muted = {**item, "muted_by": 3, "tab": "muted"}
    assert scenarios.expectation_met("muted", FakeApi([muted]), "k", 50)
    assert not scenarios.expectation_met("muted", FakeApi([item]), "k", 50)
    regression = {**item, "type": "latency_regression"}
    assert not scenarios.expectation_met("regression", FakeApi([regression]), "k", 50)
    assert scenarios.expectation_met(
        "regression", FakeApi([{**regression, "severity_band": "recovered"}]), "k", 50)
    marker = {"kind": "index_added", "label": "idx_scn_events_ref added"}
    assert scenarios.expectation_met("index_added", FakeApi([], [marker]), "k", 50)
    assert not scenarios.expectation_met("index_added", FakeApi([item], [marker]), "k", 50)
