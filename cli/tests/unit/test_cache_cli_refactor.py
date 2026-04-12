"""Tests that CLI cache commands delegate to CacheService."""

from unittest.mock import patch

from features.cache.events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDropAllEvent,
    CacheListEvent,
    CacheDeployCompleteEvent,
)
from features.cache.models import CacheInput
from shared.service_events import ErrorEvent, ProgressEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_gen(*events):
    """Create an async generator yielding the given events."""
    for e in events:
        yield e


# ---------------------------------------------------------------------------
# CacheCommands.show delegates to CacheService.list_caches
# ---------------------------------------------------------------------------


class TestCacheShowDelegation:
    def test_show_delegates_to_service(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        list_event = CacheListEvent(
            type="cache_list", success=True, caches=[], count=0,
        )
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.list_caches.return_value = _async_gen(list_event)
            result = cmd.show(target="mydb-cache", target_config={"target_type": "readyset"})

        assert result.ok is True
        MockService.return_value.list_caches.assert_called_once()

    def test_show_error_returns_failed_result(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        error_event = ErrorEvent(type="error", message="Connection refused", stage="list")
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.list_caches.return_value = _async_gen(error_event)
            result = cmd.show(target="mydb-cache", target_config={"target_type": "readyset"})

        assert result.ok is False


# ---------------------------------------------------------------------------
# CacheCommands.add delegates to CacheService.add_cache
# ---------------------------------------------------------------------------


class TestCacheAddDelegation:
    def test_add_dry_run_delegates_to_service(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        add_event = CacheAddEvent(
            type="cache_add", success=True, supported=True,
            query="SELECT 1", detail="yes",
        )
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.add_cache.return_value = _async_gen(add_event)
            result = cmd.add(
                query="SELECT 1", target="mydb-cache",
                target_config={"target_type": "readyset"},
                dry_run=True,
            )

        assert result.ok is True
        MockService.return_value.add_cache.assert_called_once()

    def test_add_creates_cache_delegates(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        progress = ProgressEvent(type="progress", stage="create", percent=60, message="Creating...")
        add_event = CacheAddEvent(
            type="cache_add", success=True, supported=True,
            query="SELECT 1", query_hash="abc123",
        )
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.add_cache.return_value = _async_gen(progress, add_event)
            result = cmd.add(
                query="SELECT 1", target="mydb-cache",
                target_config={"target_type": "readyset"},
                dry_run=False,
            )

        assert result.ok is True


# ---------------------------------------------------------------------------
# CacheCommands.delete delegates to CacheService.delete_cache
# ---------------------------------------------------------------------------


class TestCacheDeleteDelegation:
    def test_delete_delegates_to_service(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        delete_event = CacheDeleteEvent(
            type="cache_delete", success=True, cache_id="q_abc123",
        )
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.delete_cache.return_value = _async_gen(delete_event)
            result = cmd.delete(
                cache_id="q_abc123", target="mydb-cache",
                target_config={"target_type": "readyset"},
            )

        assert result.ok is True
        MockService.return_value.delete_cache.assert_called_once()


# ---------------------------------------------------------------------------
# CacheCommands.drop_all delegates to CacheService.drop_all
# ---------------------------------------------------------------------------


class TestCacheDropAllDelegation:
    def test_drop_all_delegates_to_service(self):
        from features.cache.cli.command import CacheCommands

        cmd = CacheCommands()
        drop_event = CacheDropAllEvent(
            type="cache_drop_all", success=True, count=3,
        )
        with patch("features.cache.cli.command.CacheService") as MockService:
            MockService.return_value.drop_all.return_value = _async_gen(drop_event)
            result = cmd.drop_all(
                target="mydb-cache",
                target_config={"target_type": "readyset"},
                yes=True,
            )

        assert result.ok is True
        MockService.return_value.drop_all.assert_called_once()


# ---------------------------------------------------------------------------
# DeployCommand delegates to CacheService.deploy
# ---------------------------------------------------------------------------


class TestDeployDelegation:
    def test_deploy_delegates_to_service(self):
        from features.cache.cli.deploy import DeployCommand

        cmd = DeployCommand()
        progress = ProgressEvent(type="progress", stage="deploying", percent=50, message="Deploying...")
        complete = CacheDeployCompleteEvent(
            type="deploy_complete", success=True, deployed=True, running=True,
            endpoint="postgresql://admin@127.0.0.1:5433/myapp",
            cache_target="mydb-cache", container_name="rdst-readyset-mydb",
        )
        with patch("features.cache.cli.deploy.CacheService") as MockService:
            MockService.return_value.deploy.return_value = _async_gen(progress, complete)
            result = cmd.execute(target="mydb", mode="docker")

        assert result.ok is True
        MockService.return_value.deploy.assert_called_once()
        args = MockService.return_value.deploy.call_args.args
        assert args[0] == CacheInput(target="mydb")
        assert args[1].mode == "docker"

    def test_deploy_failure_returns_failed_result(self):
        from features.cache.cli.deploy import DeployCommand

        cmd = DeployCommand()
        error = ErrorEvent(type="error", message="Docker not running", stage="deploy")
        with patch("features.cache.cli.deploy.CacheService") as MockService:
            MockService.return_value.deploy.return_value = _async_gen(error)
            result = cmd.execute(target="mydb", mode="docker")

        assert result.ok is False

    def test_deploy_kubernetes_shows_access_guidance(self):
        from features.cache.cli.deploy import DeployCommand

        cmd = DeployCommand()
        progress = ProgressEvent(
            type="progress", stage="deploying", percent=30,
            message="Deploying ReadySet (kubernetes)...",
        )
        complete = CacheDeployCompleteEvent(
            type="deploy_complete", success=True, deployed=True, running=True,
            endpoint="postgresql://admin@readyset-cache-mydb.readyset.svc.cluster.local:5433/myapp",
            cache_target=None, container_name="",
        )
        with patch("features.cache.cli.deploy.CacheService") as MockService, patch(
            "features.cache.cli.deploy.get_console"
        ) as mock_console, patch(
            "features.cache.cli.deploy.StyledPanel",
            side_effect=lambda body, **_: body,
        ):
            MockService.return_value.deploy.return_value = _async_gen(progress, complete)
            result = cmd.execute(
                target="mydb", mode="kubernetes", namespace="readyset",
            )

        assert result.ok is True
        rendered = "".join(str(call.args[0]) for call in mock_console.return_value.print.call_args_list)
        assert "port-forward" in rendered
        assert "svc/readyset-cache-mydb" in rendered

    def test_deploy_remote_systemd_passes_host_options(self):
        from features.cache.cli.deploy import DeployCommand

        cmd = DeployCommand()
        complete = CacheDeployCompleteEvent(
            type="deploy_complete", success=True, deployed=True, running=True,
            endpoint="postgresql://admin@10.0.0.5:5433/myapp",
            cache_target="mydb-cache", container_name="",
        )
        with patch("features.cache.cli.deploy.CacheService") as MockService:
            MockService.return_value.deploy.return_value = _async_gen(complete)
            result = cmd.execute(
                target="mydb", mode="systemd", host="10.0.0.5",
                ssh_user="root", deploy_config="readyset-squeepy",
            )

        assert result.ok is True
        args = MockService.return_value.deploy.call_args.args
        assert args[1].mode == "systemd"
        assert args[1].host == "10.0.0.5"
        assert args[1].ssh_user == "root"
        assert args[1].deploy_config == "readyset-squeepy"
