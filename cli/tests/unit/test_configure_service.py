"""
Unit tests for ConfigureService.

Tests the async generator-based configuration management service including
target listing, connection testing, and configuration operations.
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Any, Dict, Optional

from features.configure.events import (
    ConfigureConnectionTestEvent,
    ConfigureErrorEvent,
    ConfigureInputNeededEvent,
    ConfigureStatusEvent,
    ConfigureSuccessEvent,
    ConfigureTargetDetailEvent,
    ConfigureTargetListEvent,
)
from features.configure.models import ConfigureInput, ConfigureOptions


@pytest.mark.asyncio
async def test_remove_target_cancels_runs_and_retires_sandbox_before_config(
    monkeypatch,
):
    from features.configure.service import ConfigureService

    actions: list[str] = []

    class Config:
        def get(self, name):
            return {"engine": "postgresql"} if name == "app" else None

        def remove(self, name):
            assert name == "app"
            actions.append("config")

        def save(self):
            actions.append("save")

    class Registry:
        def cancel_target(self, name):
            assert name == "app"
            actions.append("runs")
            return 1

    class Manager:
        async def start(self):
            actions.append("start")

        async def remove_target(self, name):
            assert name == "app"
            actions.append("sandbox")
            return True

        async def stop(self):
            actions.append("stop")

    service = ConfigureService()
    monkeypatch.setattr(service, "_load_config", lambda: Config())
    monkeypatch.setattr("shared.run_registry.run_registry", Registry())
    monkeypatch.setattr(
        "shared.deploy.sandbox_manager.sandbox_manager", Manager()
    )

    events = [event async for event in service.remove_target("app")]

    assert any(isinstance(event, ConfigureSuccessEvent) for event in events)
    assert actions == [
        "runs",
        "start",
        "sandbox",
        "stop",
        "config",
        "save",
    ]


class TestConfigureServiceInit:
    """Tests for ConfigureService initialization."""

    def test_initialization(self):
        """Test service initializes correctly.

        Verifies that ConfigureService can be instantiated without errors.
        This test will fail until ConfigureService is implemented.
        """
        from features.configure.service import ConfigureService

        service = ConfigureService()
        assert service is not None

    @pytest.mark.asyncio
    async def test_add_target_keeps_password_session_only_without_keychain(
        self, tmp_path, monkeypatch
    ):
        from features.configure.api.routes import AddTargetRequest, TargetData, add_target
        from shared.config.targets import TargetsConfig
        from shared.secret_store_service import SecretStoreService

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("RDST_HEADLESS_PASSWORD", raising=False)
        monkeypatch.setattr(SecretStoreService, "is_available", lambda self: False)

        response = await add_target(
            AddTargetRequest(
                name="headless",
                target=TargetData(
                    host="db.example.com",
                    database="app",
                    user="appuser",
                    password="durable-secret",
                ),
            )
        )

        assert response.success is True
        cfg = TargetsConfig()
        cfg.load()
        saved = cfg.get("headless")
        assert "password" not in saved
        assert saved["password_env"] == "RDST_HEADLESS_PASSWORD"
        assert os.environ["RDST_HEADLESS_PASSWORD"] == "durable-secret"


@pytest.mark.parametrize(
    ("module_name", "expected"),
    [
        (
            "psycopg2",
            "Missing database driver: psycopg2. Install with: pip install psycopg2",
        ),
        (
            "paramiko",
            "SSH support is unavailable in this build because the paramiko module is missing.",
        ),
        ("dependency_from_plugin", "Missing required module: dependency_from_plugin."),
    ],
)
@pytest.mark.asyncio
async def test_connection_test_names_the_module_that_failed_to_import(
    monkeypatch, module_name, expected
):
    from features.configure.service import ConfigureService

    missing = ModuleNotFoundError(
        f"No module named '{module_name}'",
        name=module_name,
    )
    monkeypatch.setattr(
        "features.configure.service.resolve_connection_params",
        Mock(side_effect=missing),
    )

    result = await ConfigureService().perform_connection_test(
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "app",
            "user": "app",
            "password": "secret",
        }
    )

    assert result == {"success": False, "message": expected}


class TestConfigureServiceListTargets:
    """Tests for list_targets() method."""

    @pytest.fixture
    def service(self):
        """Create ConfigureService instance.

        Provides a fresh service instance for each test.
        """
        from features.configure.service import ConfigureService

        return ConfigureService()

    @pytest.fixture
    def input_data(self):
        """Create test input data for list operation.

        Returns ConfigureInput with no target specified (for list operation).
        """
        return ConfigureInput(target_name=None)

    @pytest.fixture
    def options(self):
        """Create test options for list operation.

        Returns ConfigureOptions configured for listing targets.
        """
        return ConfigureOptions(operation="list")

    @pytest.mark.asyncio
    async def test_list_targets_yields_events(self, service, input_data, options):
        """Test that list_targets() yields at least one event.

        Verifies that the async generator yields events when listing targets.
        This is the RED phase test - it will fail because ConfigureService
        doesn't exist yet. Once implemented, it should yield at least one
        event (status, target_list, or error).
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This assertion will fail until list_targets is implemented
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_list_targets_yields_target_list_event(
        self, service, input_data, options
    ):
        """Test that list_targets() yields a ConfigureTargetListEvent.

        Verifies that the service yields a target list event containing
        the configured targets. This test expects at least one event of
        type ConfigureTargetListEvent.
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This will fail until list_targets is implemented
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) >= 1

    @pytest.mark.asyncio
    async def test_list_targets_event_has_targets_field(
        self, service, input_data, options
    ):
        """Test that ConfigureTargetListEvent has targets field.

        Verifies that the target list event contains a targets field
        with the expected structure (list of dicts with target info).
        """
        events = []

        async for event in service.list_targets(input_data, options):
            events.append(event)

        # RED phase: This will fail until list_targets is implemented
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) > 0
        assert hasattr(target_list_events[0], "targets")
        assert isinstance(target_list_events[0].targets, list)


class TestConfigureServiceEventTypes:
    """Tests for event type structure and validation."""

    def test_configure_status_event_structure(self):
        """Test ConfigureStatusEvent has correct structure.

        Verifies that the status event dataclass has the expected fields
        and type discriminator.
        """
        event = ConfigureStatusEvent(type="status", message="Loading targets...")
        assert event.type == "status"
        assert event.message == "Loading targets..."

    def test_configure_target_list_event_structure(self):
        """Test ConfigureTargetListEvent has correct structure.

        Verifies that the target list event has targets and optional
        default_target fields.
        """
        event = ConfigureTargetListEvent(
            type="target_list",
            targets=[
                {
                    "name": "prod",
                    "engine": "postgresql",
                    "has_password": True,
                    "is_default": True,
                }
            ],
            default_target="prod",
        )
        assert event.type == "target_list"
        assert len(event.targets) == 1
        assert event.default_target == "prod"

    def test_configure_error_event_structure(self):
        """Test ConfigureErrorEvent has correct structure.

        Verifies that error events contain message and optional operation/target fields.
        """
        event = ConfigureErrorEvent(
            type="error",
            message="Connection failed",
            operation="test",
            target_name="prod",
        )
        assert event.type == "error"
        assert event.message == "Connection failed"
        assert event.operation == "test"
        assert event.target_name == "prod"


class TestTargetNotFoundIncludesHint:
    """Target-not-found errors must include 'rdst configure list' hint."""

    @pytest.fixture
    def service(self):
        from features.configure.service import ConfigureService
        return ConfigureService()

    @pytest.fixture
    def mock_config(self):
        cfg = Mock()
        cfg.get.return_value = None
        cfg.list_targets.return_value = []
        cfg.get_default.return_value = None
        return cfg

    @pytest.mark.asyncio
    async def test_get_target_not_found_includes_hint(self, service, mock_config):
        with patch.object(service, "_load_config", return_value=mock_config):
            events = []
            async for event in service.get_target("nonexistent"):
                events.append(event)
        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "rdst configure list" in error_events[0].message

    @pytest.mark.asyncio
    async def test_get_target_uses_engine_default_port(self, service):
        config = Mock()
        config.get.return_value = {
            "engine": "mysql",
            "host": "db.example.com",
            "database": "app",
            "user": "readonly",
        }
        config.get_default.return_value = None

        with patch.object(service, "_load_config", return_value=config):
            events = [event async for event in service.get_target("mysql-db")]

        detail = next(
            event for event in events if isinstance(event, ConfigureTargetDetailEvent)
        )
        assert detail.port == 3306

    @pytest.mark.asyncio
    async def test_test_connection_not_found_includes_hint(self, service, mock_config):
        with patch.object(service, "_load_config", return_value=mock_config):
            events = []
            async for event in service.test_connection("nonexistent"):
                events.append(event)
        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "rdst configure list" in error_events[0].message

    @pytest.mark.asyncio
    async def test_connection_success_includes_privilege_detection(self, service):
        config = Mock()
        config.get.return_value = {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "app",
            "user": "readonly",
        }
        privilege_result = {
            "writable": True,
            "evidence": "PostgreSQL role is a superuser.",
        }

        with (
            patch.object(service, "_load_config", return_value=config),
            patch.object(
                service,
                "perform_connection_test",
                new=AsyncMock(
                    return_value={
                        "success": True,
                        "server_version": "PostgreSQL 17",
                        "privileges": privilege_result,
                    }
                ),
            ),
        ):
            events = [event async for event in service.test_connection("prod")]

        result = next(
            event
            for event in events
            if isinstance(event, ConfigureConnectionTestEvent)
            and event.status == "success"
        )
        assert result.privileges == privilege_result


class TestConfigureCommandNoTargetError:
    """When no target is specified and no default is configured, configure test
    should return a non-empty error message (not silently empty)."""

    def test_configure_test_no_target_returns_error_message(self):
        """Bug fix: 'rdst configure test' with no target and no default
        should return a clear error message, not an empty string."""
        from features.configure.cli.command import ConfigureCommand

        cmd = ConfigureCommand(client=None)

        with (
            patch("shared.config.targets.TargetsConfig.load", return_value=None),
            patch("shared.config.targets.TargetsConfig.get_default", return_value=None),
        ):
            result = cmd.execute(subcommand="test")

        assert result.ok is False
        assert result.message != "", (
            "Error message should not be empty when no target is specified "
            "and no default is configured"
        )
        assert "target" in result.message.lower() or "configure" in result.message.lower(), (
            f"Error message should mention target or configure. Got: {result.message!r}"
        )


class TestPgVersionNotTruncated:
    """PG version string must not be truncated at 80 chars.

    Tests the truncation logic used in perform_connection_test:
        (version[:120] + "...") if len(version) > 120 else version
    """

    @staticmethod
    def _apply_version_truncation(version: str) -> str:
        """Reproduce the truncation logic from ConfigureService."""
        return (version[:120] + "...") if len(version) > 120 else version

    def test_120_char_version_not_truncated(self):
        """A 120-char version string should be preserved in full."""
        version = "P" * 120
        result = self._apply_version_truncation(version)
        assert result == version
        assert "..." not in result

    def test_short_version_not_truncated(self):
        """A normal PG version string should not be truncated."""
        version = "PostgreSQL 16.2 on x86_64-pc-linux-gnu"
        result = self._apply_version_truncation(version)
        assert result == version

    def test_very_long_version_truncated_with_ellipsis(self):
        """Versions over 120 chars should be truncated with '...'."""
        version = "P" * 200
        result = self._apply_version_truncation(version)
        assert len(result) == 123
        assert result.endswith("...")


class TestConnectionFailureClassification:
    @pytest.fixture
    def service(self):
        from features.configure.service import ConfigureService

        return ConfigureService()

    @pytest.mark.asyncio
    async def test_verbatim_supabase_refusal_is_categorized(self, service):
        config = {
            "name": "supabase-db",
            "engine": "postgresql",
            "host": "db.supabase.co",
            "port": 5432,
            "database": "postgres",
            "user": "postgres",
            "password": "test-password",
            "tags": ["provider:supabase"],
        }
        refusal = (
            'connection to server at "db.supabase.co", port 5432 failed: '
            "FATAL:  Address not allowed. Address is not in the allowed list"
        )

        with patch(
            "features.configure.service.resolve_connection_params",
            side_effect=RuntimeError(refusal),
        ):
            result = await service.perform_connection_test(config)

        assert result["success"] is False
        assert result["category"] == "provider_ip_blocked_maybe"
        assert "password" not in result["message"].lower()

    @pytest.mark.asyncio
    async def test_verbatim_supabase_refusal_beats_missing_password(self, service):
        config = {
            "name": "supabase-db",
            "engine": "postgresql",
            "host": "db.supabase.co",
            "port": 5432,
            "database": "postgres",
            "user": "postgres",
            "password_env": "SUPABASE_DB_PASSWORD",
            "tags": ["provider:supabase"],
        }
        refusal = (
            "FATAL:  (EADDRNOTALLOWED) address not in tenant allow_list: "
            "{71, 218, 135, 182}"
        )

        with patch(
            "features.configure.service.resolve_connection_params",
            side_effect=RuntimeError(refusal),
        ):
            result = await service.perform_connection_test(config)

        assert result["success"] is False
        assert result["category"] == "provider_ip_blocked_maybe"
        assert result.get("code") != "TARGET_PASSWORD_REQUIRED"

    @pytest.mark.asyncio
    async def test_reachable_provider_without_password_requests_password(self, service):
        config = {
            "name": "supabase-db",
            "engine": "postgresql",
            "host": "db.supabase.co",
            "port": 5432,
            "database": "postgres",
            "user": "postgres",
            "password_env": "SUPABASE_DB_PASSWORD",
            "tags": ["provider:supabase"],
        }
        params = {
            **config,
            "password": "",
            "sslmode": "prefer",
            "tls_verify": False,
            "tls_ca": None,
        }
        psycopg2 = MagicMock()
        psycopg2.connect.side_effect = RuntimeError(
            'password authentication failed for user "postgres"'
        )

        with (
            patch(
                "features.configure.service.resolve_connection_params",
                return_value=params,
            ),
            patch.dict(sys.modules, {"psycopg2": psycopg2}),
        ):
            result = await service.perform_connection_test(config)

        assert result["success"] is False
        assert result["code"] == "TARGET_PASSWORD_REQUIRED"
        assert result["password_env"] == "SUPABASE_DB_PASSWORD"
        assert psycopg2.connect.call_args.kwargs["password"] == (
            "rdst-connectivity-probe-invalid-password"
        )

class TestTestConnectionSingleStatusMessage:
    """test_connection must emit exactly ONE status/progress message, not two."""

    @pytest.fixture
    def service(self):
        from features.configure.service import ConfigureService
        return ConfigureService()

    @pytest.mark.asyncio
    async def test_no_duplicate_testing_connection_status(self, service):
        """Bug fix: test_connection previously emitted a duplicate
        'Testing connection to' ConfigureStatusEvent before the
        ConfigureConnectionTestEvent. Verify only one status-like
        message is emitted before the result.

        The expected event sequence is:
          1. ConfigureConnectionTestEvent (status="in_progress") - the connecting message
          2. ConfigureConnectionTestEvent (status="success"|"failed") - the result
        There should be NO ConfigureStatusEvent with "Testing connection" text.
        """
        mock_config = {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "database": "testdb",
        }

        mock_cfg = Mock()
        mock_cfg.get.return_value = mock_config
        mock_cfg.get_default.return_value = "test-target"

        events = []
        with patch.object(service, "_load_config", return_value=mock_cfg):
            with patch.object(
                service,
                "perform_connection_test",
                new_callable=AsyncMock,
                return_value={"success": True, "message": "Connected!", "server_version": "PG 15"},
            ):
                async for event in service.test_connection("test-target"):
                    events.append(event)

        # No ConfigureStatusEvent should be emitted — only ConnectionTestEvents
        status_events = [
            e for e in events if isinstance(e, ConfigureStatusEvent)
        ]
        assert len(status_events) == 0, (
            f"Expected zero ConfigureStatusEvent in test_connection, "
            f"got {len(status_events)}: {[e.message for e in status_events]}"
        )

        # Should have exactly 2 ConnectionTestEvents: in_progress + success
        conn_events = [
            e for e in events if isinstance(e, ConfigureConnectionTestEvent)
        ]
        assert len(conn_events) == 2, (
            f"Expected 2 ConfigureConnectionTestEvent, got {len(conn_events)}"
        )
        assert conn_events[0].status == "in_progress"
        assert conn_events[1].status == "success"
