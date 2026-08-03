from __future__ import annotations

import inspect
import os
import signal
import subprocess
import threading
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from features.analyze.cli.command import AnalyzeCommand
from features.cache.events import CacheRunCompleteEvent
from features.cache.api.routes import router as cache_router
from features.bootstrap.api.routes import BootstrapStartRequest
from features.bootstrap.service import BootstrapOptions
from features.cache.experiment_service import ReadysetExperimentService
from features.fleet.cli.command import FleetCommand
from features.query_registry.cli.command import QueryCommand
from shared.cli.parser_data import COMMANDS
from shared.cli.rdst_cli import RdstCLI
from shared.cli.types import RdstResult
from shared.deploy.sandbox_manager import sandbox_manager
from mcp_server import get_tools, handle_tool_call, run_rdst_command


def test_persistent_cache_management_command_is_not_exposed() -> None:
    """RDST exposes comparisons, not Readyset deployment management."""
    assert "cache" not in COMMANDS


def test_analyze_requires_explicit_readyset_verification() -> None:
    argument_names = {
        argument.name
        for argument in COMMANDS["analyze"].args
        if hasattr(argument, "name")
    }
    execute_parameters = inspect.signature(AnalyzeCommand.execute_analyze).parameters
    async_parameters = inspect.signature(
        AnalyzeCommand._execute_analyze_async
    ).parameters
    cli_parameters = inspect.signature(RdstCLI.analyze).parameters

    assert "--readyset-cache" in argument_names
    assert execute_parameters["readyset_cache"].default is False
    assert async_parameters["readyset_cache"].default is False
    assert cli_parameters["readyset_cache"].default is False
    analyze_tool = next(tool for tool in get_tools() if tool["name"] == "rdst_analyze")
    assert "readyset_cache" in analyze_tool["inputSchema"]["properties"]


def test_cli_analyze_forwards_explicit_readyset_verification() -> None:
    client = MagicMock()
    resolved_input = SimpleNamespace(registry_target="")
    with patch("features.analyze.cli.command.AnalyzeCommand") as command_type:
        command = command_type.return_value
        command.resolve_input.return_value = resolved_input
        command.resolve_target.return_value = "app"

        RdstCLI(client=client).analyze(
            query="SELECT 1",
            target="app",
            readyset_cache=True,
        )

    command.execute_analyze.assert_called_once_with(
        resolved_input,
        target="app",
        fast=False,
        interactive=False,
        review=False,
        output_json=False,
        skip_warning=False,
        readyset_cache=True,
    )


def test_mcp_analyze_forwards_explicit_readyset_verification() -> None:
    with patch("mcp_server.run_rdst_command") as run:
        handle_tool_call(
            "rdst_analyze",
            {
                "query": "SELECT 1",
                "target": "app",
                "readyset_cache": True,
            },
        )

    run.assert_called_once_with(
        [
            "analyze",
            "-q",
            "SELECT 1",
            "--target",
            "app",
            "--readyset-cache",
        ]
    )


def test_cache_compare_exposes_bounded_load_controls() -> None:
    command = COMMANDS["query"]
    speed_test = next(
        item for item in command.subcommand_defs if item.name == "cache-compare"
    )
    argument_names = {argument.name for argument in speed_test.args}

    assert {
        "queries",
        "--target",
        "--interval",
        "--concurrency",
        "--duration",
        "--count",
        "--quiet",
        "--skip-warning",
    } <= argument_names


def test_cache_compare_runs_on_main_thread_for_signal_cancellation() -> None:
    calls: list[tuple[str, dict, bool]] = []

    def execute(_command, subcommand, **kwargs):
        calls.append(
            (
                subcommand,
                kwargs,
                threading.current_thread() is threading.main_thread(),
            )
        )
        return RdstResult(ok=True, message="done")

    with patch.object(QueryCommand, "execute", execute):
        result = RdstCLI(client=MagicMock()).query(
            "cache-compare", queries=["SELECT 1"], target="app"
        )

    assert result.ok is True
    assert calls == [
        (
            "cache-compare",
            {"queries": ["SELECT 1"], "target": "app"},
            True,
        )
    ]


def test_cache_compare_rejects_invalid_counts_before_starting_manager() -> None:
    command = QueryCommand()

    with patch.object(sandbox_manager, "start", new=AsyncMock()) as start:
        zero = command.cache_compare(
            queries=["SELECT 1"],
            target="app",
            count=0,
            quiet=True,
            skip_warning=True,
        )
        excessive = command.cache_compare(
            queries=["SELECT 1"],
            target="app",
            count=1001,
            quiet=True,
            skip_warning=True,
        )

    assert zero.ok is False
    assert excessive.ok is False
    start.assert_not_awaited()


def test_cache_api_only_exposes_temporary_sandbox_operations() -> None:
    paths = {route.path for route in cache_router.routes}

    assert paths == {
        "/cache/sandbox",
        "/cache/sandbox/prewarm",
        "/cache/test-runs",
    }


def test_database_bootstrap_has_no_readyset_deployment_options() -> None:
    assert {"deploy", "deploy_mode"}.isdisjoint(
        BootstrapStartRequest.model_fields
    )
    assert {"deploy", "deploy_mode"}.isdisjoint(
        field.name for field in fields(BootstrapOptions)
    )


def test_mcp_replaces_deployment_management_with_a_speed_test() -> None:
    names = {tool["name"] for tool in get_tools()}

    assert "rdst_query_cache_compare" in names
    assert names.isdisjoint(
        {
            "rdst_cache_deploy",
            "rdst_cache_add",
            "rdst_cache_show",
            "rdst_cache_delete",
            "rdst_cache_drop_all",
        }
    )
    speed_test = next(
        tool for tool in get_tools() if tool["name"] == "rdst_query_cache_compare"
    )
    properties = speed_test["inputSchema"]["properties"]
    assert properties["count"]["minimum"] == 1
    assert properties["count"]["maximum"] == 1000
    assert properties["interval"]["minimum"] == 0
    assert properties["concurrency"]["maximum"] == 64
    assert properties["duration"]["maximum"] == 300


def test_mcp_speed_test_delegates_to_manager_backed_cli_command() -> None:
    with patch("mcp_server.run_rdst_command") as run:
        handle_tool_call(
            "rdst_query_cache_compare",
            {
                "query": "SELECT 1",
                "target": "app",
                "count": 7,
            },
        )

    run.assert_called_once_with(
        [
            "query",
            "cache-compare",
            "SELECT 1",
            "--target",
            "app",
            "--count",
            "7",
            "--skip-warning",
        ],
        timeout_seconds=614,
        graceful_timeout=True,
    )


def test_mcp_speed_test_forwards_load_controls() -> None:
    with patch("mcp_server.run_rdst_command") as run:
        handle_tool_call(
            "rdst_query_cache_compare",
            {
                "query": "SELECT 1",
                "target": "app",
                "count": 20,
                "concurrency": 4,
                "duration": 10,
            },
        )

    run.assert_called_once_with(
        [
            "query",
            "cache-compare",
            "SELECT 1",
            "--target",
            "app",
            "--count",
            "20",
            "--skip-warning",
            "--concurrency",
            "4",
            "--duration",
            "10",
        ],
        timeout_seconds=640,
        graceful_timeout=True,
    )


def test_mcp_speed_test_timeout_includes_interval_pacing() -> None:
    with patch("mcp_server.run_rdst_command") as run:
        handle_tool_call(
            "rdst_query_cache_compare",
            {
                "query": "SELECT 1",
                "target": "app",
                "count": 7,
                "interval": 60_000,
            },
        )

    run.assert_called_once_with(
        [
            "query",
            "cache-compare",
            "SELECT 1",
            "--target",
            "app",
            "--count",
            "7",
            "--skip-warning",
            "--interval",
            "60000",
        ],
        timeout_seconds=1334,
        graceful_timeout=True,
    )


def test_mcp_timeout_interrupts_speed_test_and_waits_for_cleanup() -> None:
    process = MagicMock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="rdst", timeout=10),
        ("partial output", "cleanup complete"),
    ]

    with (
        patch("mcp_server._get_rdst_command", return_value=["rdst"]),
        patch("mcp_server.subprocess.Popen", return_value=process),
    ):
        result = run_rdst_command(
            ["query", "cache-compare", "SELECT 1"],
            timeout_seconds=10,
            graceful_timeout=True,
        )

    interrupt = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
    process.send_signal.assert_called_once_with(interrupt)
    assert process.communicate.call_args_list[-1].kwargs == {"timeout": 60}
    assert result["success"] is False
    assert result["stdout"] == "partial output"
    assert "waited for cleanup" in result["stderr"]
    assert "cleanup complete" in result["stderr"]


def test_mcp_timeout_kills_child_when_interrupt_delivery_fails() -> None:
    process = MagicMock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="rdst", timeout=10),
        ("partial output", "killed"),
    ]
    process.send_signal.side_effect = OSError("no console")

    with (
        patch("mcp_server._get_rdst_command", return_value=["rdst"]),
        patch("mcp_server.subprocess.Popen", return_value=process),
    ):
        result = run_rdst_command(
            ["query", "cache-compare", "SELECT 1"],
            timeout_seconds=10,
            graceful_timeout=True,
        )

    process.kill.assert_called_once_with()
    assert process.communicate.call_args_list[-1].kwargs == {}
    assert result["success"] is False
    assert result["stdout"] == "partial output"
    assert "killed" in result["stderr"]


def test_cache_compare_auto_provisions_through_sandbox_manager() -> None:
    """An origin target is sufficient; no generated `*-cache` target is required."""
    config = MagicMock()
    config.get_default.return_value = "app"
    config.get.return_value = {
        "name": "app",
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "user": "app",
    }
    config._data = {"targets": {"app": config.get.return_value}}

    command = QueryCommand()
    entry = SimpleNamespace(hash="abcdef1234567890", tag="orders")
    command._resolve_queries = MagicMock(
        return_value=[(entry, "SELECT * FROM orders WHERE id = 1")]
    )

    compare_calls: list[dict] = []

    async def fake_compare(_service, **kwargs):
        compare_calls.append(kwargs)
        yield CacheRunCompleteEvent(
            type="cache_run_complete",
            success=True,
            query=kwargs["query"],
            iterations=kwargs["iterations"],
            origin_stats={"mean": 10.0, "median": 9.0, "p95": 12.0},
            cache_stats={"mean": 1.0, "median": 0.9, "p95": 1.2},
            speedup_mean=10.0,
            speedup_median=10.0,
            improvement_pct=90.0,
            winner="cache",
        )

    with (
        patch(
            "shared.config.targets.create_targets_config",
            return_value=config,
        ),
        patch.object(ReadysetExperimentService, "compare", fake_compare),
        patch.object(sandbox_manager, "start", new=AsyncMock()) as start,
        patch.object(sandbox_manager, "stop", new=AsyncMock()) as stop,
    ):
        result = command.cache_compare(
            queries=["orders"],
            target="app",
            count=3,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is True
    assert result.message == "Comparison complete"
    assert result.data["target"] == "app"
    assert result.data["sandbox_managed"] is True
    assert compare_calls == [
        {
            "owner_id": "cli-cache-compare-0-abcdef123456",
            "target": "app",
            "query": "SELECT * FROM orders WHERE id = 1",
            "iterations": 3,
            "warmup": 1,
        }
    ]
    start.assert_awaited_once()
    stop.assert_awaited_once()


def test_cache_compare_executes_concrete_inline_sql_not_registry_template() -> None:
    concrete_sql = (
        "SELECT i_item_sk FROM item WHERE i_item_sk = 1"
    )
    parameterized_sql = (
        "SELECT i_item_sk FROM item WHERE i_item_sk = :p1"
    )
    entry = SimpleNamespace(hash="ec124db2da8d0000", tag=None)

    class FakeRegistry:
        def load(self) -> None:
            pass

        def get_or_create_hash(self, _sql: str) -> str:
            return entry.hash

        def get_query(self, _query_hash: str):
            return entry

    config = MagicMock()
    config.get.return_value = {
        "name": "tpcds-sf100",
        "engine": "postgresql",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "tpcds",
        "user": "postgres",
    }

    command = QueryCommand()
    command._resolve_queries = MagicMock(
        return_value=[(entry, parameterized_sql)]
    )
    compared_queries: list[str] = []

    async def fake_compare(_service, **kwargs):
        compared_queries.append(kwargs["query"])
        yield CacheRunCompleteEvent(
            type="cache_run_complete",
            success=True,
            query=kwargs["query"],
            iterations=kwargs["iterations"],
            origin_stats={"mean": 2.0, "median": 2.0, "p95": 2.0},
            cache_stats={"mean": 1.0, "median": 1.0, "p95": 1.0},
            speedup_mean=2.0,
            speedup_median=2.0,
            improvement_pct=50.0,
            winner="cache",
        )

    with (
        patch(
            "shared.config.targets.create_targets_config",
            return_value=config,
        ),
        patch(
            "features.query_registry.cli.command._get_query_registry_class",
            return_value=FakeRegistry,
        ),
        patch.object(ReadysetExperimentService, "compare", fake_compare),
        patch.object(sandbox_manager, "start", new=AsyncMock()),
        patch.object(sandbox_manager, "stop", new=AsyncMock()),
    ):
        result = command.cache_compare(
            queries=[concrete_sql],
            target="tpcds-sf100",
            count=3,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is True
    assert compared_queries == [concrete_sql]
    command._resolve_queries.assert_not_called()


def test_saved_query_resolution_falls_back_to_concrete_original_sql() -> None:
    command = QueryCommand()
    entry = SimpleNamespace(
        hash="ec124db2da8d0000",
        tag=None,
        sql="SELECT i_item_sk FROM item WHERE i_item_sk = :p1",
        original_sql="SELECT i_item_sk FROM item WHERE i_item_sk = 91064",
    )
    command.registry = MagicMock()
    command.registry.get_query_by_tag.return_value = None
    command.registry.get_query.return_value = entry
    command.registry.get_executable_query.return_value = None

    resolved = command._resolve_queries(["ec124db2"])

    assert resolved == [(entry, entry.original_sql)]


def test_cache_compare_forwards_load_generation_flags() -> None:
    config = MagicMock()
    config.get.return_value = {
        "name": "app",
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "user": "app",
    }
    command = QueryCommand()
    entry = SimpleNamespace(hash="abcdef1234567890", tag="orders")
    command._resolve_queries = MagicMock(return_value=[(entry, "SELECT 1")])
    compare_calls: list[dict] = []

    async def fake_compare(_service, **kwargs):
        compare_calls.append(kwargs)
        yield CacheRunCompleteEvent(
            type="cache_run_complete",
            success=True,
            query=kwargs["query"],
            iterations=kwargs["iterations"],
            origin_stats={"mean": 2.0, "median": 2.0, "p95": 2.0},
            cache_stats={"mean": 1.0, "median": 1.0, "p95": 1.0},
            speedup_mean=2.0,
            speedup_median=2.0,
            improvement_pct=50.0,
            winner="cache",
        )

    with (
        patch("shared.config.targets.create_targets_config", return_value=config),
        patch.object(ReadysetExperimentService, "compare", fake_compare),
        patch.object(sandbox_manager, "start", new=AsyncMock()),
        patch.object(sandbox_manager, "stop", new=AsyncMock()),
    ):
        result = command.cache_compare(
            queries=["orders"],
            target="app",
            interval=25,
            duration=10,
            count=20,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is True
    assert result.data["count"] == 20
    assert result.data["interval_ms"] == 25
    assert result.data["duration_seconds"] == 10
    assert compare_calls == [
        {
            "owner_id": "cli-cache-compare-0-abcdef123456",
            "target": "app",
            "query": "SELECT 1",
            "iterations": 20,
            "warmup": 1,
            "interval_ms": 25,
            "duration_seconds": 10,
        }
    ]


def test_cache_compare_shares_count_and_duration_across_queries() -> None:
    config = MagicMock()
    config.get.return_value = {
        "name": "app",
        "engine": "postgresql",
        "host": "db.example.com",
        "port": 5432,
        "database": "app",
        "user": "app",
    }
    command = QueryCommand()
    entries = [
        SimpleNamespace(hash="a" * 16, tag="first"),
        SimpleNamespace(hash="b" * 16, tag="second"),
    ]
    command._resolve_queries = MagicMock(
        side_effect=[
            [(entries[0], "SELECT 1")],
            [(entries[1], "SELECT 2")],
        ]
    )
    compare_calls: list[dict] = []

    async def fake_compare(_service, **kwargs):
        compare_calls.append(kwargs)
        yield CacheRunCompleteEvent(
            type="cache_run_complete",
            success=True,
            query=kwargs["query"],
            iterations=kwargs["iterations"],
            origin_stats={"mean": 2.0, "median": 2.0, "p95": 2.0},
            cache_stats={"mean": 1.0, "median": 1.0, "p95": 1.0},
            speedup_mean=2.0,
            speedup_median=2.0,
            improvement_pct=50.0,
            winner="cache",
        )

    with (
        patch("shared.config.targets.create_targets_config", return_value=config),
        patch.object(ReadysetExperimentService, "compare", fake_compare),
        patch.object(sandbox_manager, "start", new=AsyncMock()),
        patch.object(sandbox_manager, "stop", new=AsyncMock()),
    ):
        result = command.cache_compare(
            queries=["first", "second"],
            target="app",
            concurrency=2,
            duration=5,
            count=5,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is True
    assert [call["iterations"] for call in compare_calls] == [3, 2]
    assert [call["duration_seconds"] for call in compare_calls] == [3, 2]
    assert all(call["concurrency"] == 2 for call in compare_calls)


def test_cache_compare_rejects_too_small_multi_query_budget() -> None:
    config = MagicMock()
    config.get.return_value = {"engine": "postgresql"}
    command = QueryCommand()
    entries = [
        SimpleNamespace(hash="a" * 16, tag="first"),
        SimpleNamespace(hash="b" * 16, tag="second"),
    ]
    command._resolve_queries = MagicMock(
        side_effect=[
            [(entries[0], "SELECT 1")],
            [(entries[1], "SELECT 2")],
        ]
    )

    with (
        patch("shared.config.targets.create_targets_config", return_value=config),
        patch.object(sandbox_manager, "start", new=AsyncMock()) as start,
    ):
        result = command.cache_compare(
            queries=["first", "second"],
            target="app",
            count=1,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is False
    assert "at least 2" in result.message
    start.assert_not_awaited()


def test_cache_compare_rejects_conflicting_load_modes_before_starting_manager() -> None:
    command = QueryCommand()
    with patch.object(sandbox_manager, "start", new=AsyncMock()) as start:
        result = command.cache_compare(
            queries=["orders"],
            target="app",
            interval=25,
            concurrency=2,
            quiet=True,
            skip_warning=True,
        )

    assert result.ok is False
    assert "both --interval and --concurrency" in result.message
    start.assert_not_awaited()


def test_fleet_audit_duration_reaches_manager_backed_readyset_testing() -> None:
    """Fleet's captured queries are tested sequentially by the shared manager."""
    source = inspect.getsource(FleetCommand._handle_audit)

    assert "caches_available" not in source
    assert "if duration_seconds:" in source
    assert "consent = auto_yes" in source
    assert "consent = auto_yes or output_json" not in source
    assert "auto_yes=True" in source
    assert not hasattr(FleetCommand, "_preflight_cache_check")
