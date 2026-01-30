"""Unit tests for QueryCommand run-mode behavior."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.cli.query_command import QueryCommand


def test_run_singleton_succeeds_from_worker_thread_without_signal_error():
    """`query run` is invoked via QueryService in a worker thread; it should still work."""
    cmd = QueryCommand()

    entry = SimpleNamespace(tag="run-test-1", hash="a" * 12, last_target="prod")
    target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = "prod"
    cfg.get.return_value = target_cfg

    with (
        patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
        patch.object(cmd, "_print_run_summary", return_value=None),
        patch(
            "lib.cli.rdst_cli.TargetsConfig",
            return_value=cfg,
        ),
        patch.object(
            cmd,
            "_run_singleton",
            side_effect=lambda *args, **kwargs: args[2].record_execution(
                query_hash=entry.hash,
                query_name=entry.tag,
                duration_ms=1.0,
                success=True,
            ),
        ),
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(cmd.run, queries=["run-test-1"], quiet=True).result()

    assert result.ok is True
    assert result.message == "Completed 1 executions"


def test_run_singleton_tolerates_signal_install_failure():
    """Even if signal registration fails, query run should proceed."""
    cmd = QueryCommand()

    entry = SimpleNamespace(tag="run-test-1", hash="b" * 12, last_target="prod")
    target_cfg = {"engine": "postgresql", "host": "localhost", "port": 5432}

    cfg = Mock()
    cfg.load.return_value = None
    cfg.get_default.return_value = "prod"
    cfg.get.return_value = target_cfg

    with (
        patch.object(cmd, "_resolve_queries", return_value=[(entry, "SELECT 1")]),
        patch.object(cmd, "_print_run_summary", return_value=None),
        patch("lib.cli.rdst_cli.TargetsConfig", return_value=cfg),
        patch(
            "lib.cli.query_command.signal.signal",
            side_effect=ValueError(
                "signal only works in main thread of the main interpreter"
            ),
        ),
        patch(
            "lib.cli.query_command.signal.getsignal",
            return_value=object(),
        ),
        patch.object(
            cmd,
            "_run_singleton",
            side_effect=lambda *args, **kwargs: args[2].record_execution(
                query_hash=entry.hash,
                query_name=entry.tag,
                duration_ms=1.0,
                success=True,
            ),
        ),
    ):
        result = cmd.run(queries=["run-test-1"], quiet=True)

    assert result.ok is True
    assert result.message == "Completed 1 executions"
