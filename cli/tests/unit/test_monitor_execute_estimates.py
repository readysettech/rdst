"""With consent to measure (execute mode), a query without real parameter values still gets
a plan estimate: never EXPLAIN ANALYZE, never the "No real values" failure."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from features.monitor.analyze_queue import AnalyzeQueue
from shared.monitor.monitor_store import MonitorStore

NOW = 1_800_000_000


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    result = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    result.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    return result


class Service:
    """Answers like Deep Analyze after a plan-only run that filled in estimated values."""

    def __init__(self, placeholders):
        self.placeholders = placeholders
        self.calls = []

    async def suggest_parameter_values(self, sql, target, config, key):
        return {"placeholders": self.placeholders}

    async def analyze(self, input, options):
        self.calls.append((input, options))
        yield SimpleNamespace(type="complete", analysis_id="hash-1", query_hash="hash-1",
                              stored_analysis_id="analysis-1", llm_analysis={"success": True},
                              explain_results={"success": True, "plan_format": "json",
                                               "param_source": "estimated"})


def _engine(monkeypatch, engine):
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, target: {"engine": engine})


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholders", [
    [{"placeholder": "$1", "suggestions": []}],
    # The value pass found no placeholders, yet the text still carries one.
    [],
])
async def test_postgres_14_in_execute_mode_estimates_without_values(store, monkeypatch,
                                                                    placeholders):
    store.record("db", NOW, [{"query_key": "one", "calls": 10, "total_ms": 100}],
                 {"one": "SELECT * FROM one WHERE id = $1"}, [], None)
    _engine(monkeypatch, "postgresql")
    service = Service(placeholders)
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    sent, options = service.calls[0]
    assert options.fast, "EXPLAIN ANALYZE never runs without real values"
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "done" and job["mode"] == "explain"
    assert job["param_source"] == "estimated"
    assert not (job.get("error") or "").startswith("No real values")


@pytest.mark.asyncio
async def test_mysql_in_execute_mode_estimates_without_values(store, monkeypatch):
    store.record("db", NOW, [{"query_key": "one", "calls": 10, "total_ms": 100}],
                 {"one": "SELECT * FROM one WHERE id = ?"}, [], None)
    _engine(monkeypatch, "mysql")
    service = Service([{"placeholder": "?", "suggestions": []}])
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    sent, options = service.calls[0]
    assert options.fast and "?" not in sent.sql
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "done" and job["mode"] == "explain"
    assert job["param_source"] == "estimated"


def test_postgres_14_explain_falls_back_to_estimates_when_generic_planning_fails(monkeypatch):
    from features.analyze.functions import explain_analysis

    statements = []
    monkeypatch.setattr("features.analyze.parameter_suggestions._sample_column_values",
                        lambda *args, **kwargs: [])

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, *args):
            statements.append(sql)
            if "PREPARE" in sql:
                raise RuntimeError("could not determine data type of parameter $1")

        def fetchone(self):
            return ([{"Plan": {}}],)

    class Connection:
        server_version = 140008

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(explain_analysis, "_postgres_connection", lambda *args: Connection())
    result = explain_analysis._execute_postgres_explain_analyze(
        "SELECT * FROM t WHERE id = $1", {}, fast_mode=True)
    assert result["success"] and result["param_source"] == "estimated"
    assert "No real values" not in str(result.get("error"))
    assert not any("ANALYZE" in statement for statement in statements)
