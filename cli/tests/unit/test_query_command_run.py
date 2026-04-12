"""Unit tests for QueryCommand run-mode behavior."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from features.query_registry.cli.command import QueryCommand


def test_run_singleton_succeeds_from_worker_thread_without_signal_error():
    """`query run` is invoked via QueryService in a worker thread; it should still work.
    Note: singleton mode now converts to interval mode with count=2 for warm-up exclusion.
    """
    cmd = QueryCommand()

    entry = SimpleNamespace(tag="run-test-1", hash="a" * 12, last_target="prod")
    target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = "prod"
    cfg.get.return_value = target_cfg

    def fake_interval(*args, **kwargs):
        stats = args[2]
        # Simulate 2 runs (1 warmup + 1 measured)
        stats.record_execution(query_hash=entry.hash, query_name=entry.tag, duration_ms=1.0, success=True)
        stats.record_execution(query_hash=entry.hash, query_name=entry.tag, duration_ms=1.0, success=True)

    with (
        patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
        patch.object(cmd, "_print_run_summary", return_value=None),
        patch("shared.config.targets.create_targets_config", return_value=cfg),
        patch.object(cmd, "_run_interval", side_effect=fake_interval),
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(cmd.run, queries=["run-test-1"], quiet=True).result()

    assert result.ok is True
    # Warm-up exclusion: 1 measured out of 2 total
    assert "1 measured executions" in result.message


def test_run_singleton_tolerates_signal_install_failure():
    """Even if signal registration fails, query run should proceed."""
    cmd = QueryCommand()

    entry = SimpleNamespace(tag="run-test-1", hash="b" * 12, last_target="prod")
    target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = "prod"
    cfg.get.return_value = target_cfg

    def fake_interval(*args, **kwargs):
        stats = args[2]
        stats.record_execution(query_hash=entry.hash, query_name=entry.tag, duration_ms=1.0, success=True)
        stats.record_execution(query_hash=entry.hash, query_name=entry.tag, duration_ms=1.0, success=True)

    with (
        patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
        patch.object(cmd, "_print_run_summary", return_value=None),
        patch("shared.config.targets.create_targets_config", return_value=cfg),
        patch(
            "features.query_registry.cli.command.signal.signal",
            side_effect=ValueError(
                "signal only works in main thread of the main interpreter"
            ),
        ),
        patch(
            "features.query_registry.cli.command.signal.getsignal",
            return_value=object(),
        ),
        patch.object(cmd, "_run_interval", side_effect=fake_interval),
    ):
        result = cmd.run(queries=["run-test-1"], quiet=True)

    assert result.ok is True
    assert "1 measured executions" in result.message


def test_warmup_first_execution_excluded_from_timings():
    """First successful execution per query is warm-up — excluded from timing stats."""
    from features.query_registry.cli.command import RunStatistics
    import time

    stats = RunStatistics(start_time=time.perf_counter())
    stats.record_execution("h1", "q1", 100.0, True)  # warmup
    stats.record_execution("h1", "q1", 5.0, True)    # measured
    stats.record_execution("h1", "q1", 6.0, True)    # measured

    qs = stats.query_stats["h1"]
    assert len(qs.timings_ms) == 2
    assert 100.0 not in qs.timings_ms
    assert 5.0 in qs.timings_ms


def test_warmup_per_query_independent():
    """Each query has its own independent warm-up."""
    from features.query_registry.cli.command import RunStatistics
    import time

    stats = RunStatistics(start_time=time.perf_counter())
    stats.record_execution("h1", "q1", 100.0, True)
    stats.record_execution("h2", "q2", 200.0, True)
    stats.record_execution("h1", "q1", 5.0, True)
    stats.record_execution("h2", "q2", 10.0, True)

    assert len(stats.query_stats["h1"].timings_ms) == 1
    assert len(stats.query_stats["h2"].timings_ms) == 1
    assert stats.query_stats["h1"].timings_ms[0] == 5.0
    assert stats.query_stats["h2"].timings_ms[0] == 10.0


def test_warmup_failure_not_counted_as_warmup():
    """Failed execution doesn't consume the warm-up slot."""
    from features.query_registry.cli.command import RunStatistics
    import time

    stats = RunStatistics(start_time=time.perf_counter())
    stats.record_execution("h1", "q1", 1.0, False)   # failure
    stats.record_execution("h1", "q1", 100.0, True)  # first success = warmup
    stats.record_execution("h1", "q1", 5.0, True)    # measured

    qs = stats.query_stats["h1"]
    assert qs.failures == 1
    assert len(qs.timings_ms) == 1
    assert qs.timings_ms[0] == 5.0
