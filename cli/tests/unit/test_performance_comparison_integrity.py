from __future__ import annotations

from features.cache import performance_comparison


class _Connection:
    def close(self) -> None:
        pass


def test_partial_measured_failure_rejects_the_comparison(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    outcomes = iter(
        [
            {"success": True, "execution_time_ms": 10.0},
            {"success": False, "error": "statement timeout"},
            {"success": True, "execution_time_ms": 3.0},
            {"success": True, "execution_time_ms": 2.0},
        ]
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: next(outcomes),
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=0,
    )

    assert result["success"] is False
    assert "original" in result["error"].lower()
    assert "iteration 2" in result["error"].lower()


def test_warmup_failure_rejects_before_measurement(monkeypatch):
    connections = iter([(_Connection(), "postgresql"), (_Connection(), "postgresql")])
    monkeypatch.setattr(
        performance_comparison,
        "_open_persistent_connection",
        lambda _config: next(connections),
    )
    monkeypatch.setattr(
        performance_comparison,
        "_execute_on_connection",
        lambda *_args, **_kwargs: {
            "success": False,
            "error": "connection reset",
        },
    )

    result = performance_comparison.run_comparison(
        query="SELECT 1",
        original_db_config={"engine": "postgresql"},
        readyset_db_config={"engine": "postgresql"},
        iterations=2,
        warmup_iterations=1,
    )

    assert result["success"] is False
    assert "warmup" in result["error"].lower()
