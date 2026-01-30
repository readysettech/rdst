#!/usr/bin/env python3
"""
Integration tests for Configure feature.

Tests the full flow: CLI -> Service -> Config file
and API endpoints with httpx test client.

These tests mock database connections and use mock config objects
to avoid requiring real database access or modifying real config files.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Add rdst root to path for imports
script_dir = Path(__file__).parent
rdst_root = script_dir.parent.parent.parent
if str(rdst_root) not in sys.path:
    sys.path.insert(0, str(rdst_root))


# ============================================================================
# Mock Config Helper
# ============================================================================


def create_mock_config(targets=None, default_target=None):
    """Create a mock TargetsConfig object with specified targets."""
    if targets is None:
        targets = {}

    mock_cfg = MagicMock()
    mock_cfg._targets = dict(targets)
    mock_cfg._default = default_target

    def list_targets():
        return sorted(mock_cfg._targets.keys())

    def get(name):
        return mock_cfg._targets.get(name)

    def upsert(name, entry):
        mock_cfg._targets[name] = entry

    def remove(name):
        if name in mock_cfg._targets:
            del mock_cfg._targets[name]
            if mock_cfg._default == name:
                mock_cfg._default = None
            return True
        return False

    def set_default(name):
        mock_cfg._default = name

    def get_default():
        return mock_cfg._default

    def save():
        pass  # No-op for mock

    mock_cfg.list_targets = list_targets
    mock_cfg.get = get
    mock_cfg.upsert = upsert
    mock_cfg.remove = remove
    mock_cfg.set_default = set_default
    mock_cfg.get_default = get_default
    mock_cfg.save = save
    mock_cfg.load = MagicMock()

    return mock_cfg


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_config_with_target():
    """Create a mock config with one target."""
    return create_mock_config(
        targets={
            "test_db": {
                "engine": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
                "password_env": "TEST_DB_PASSWORD",
                "tls": False,
            }
        },
        default_target="test_db",
    )


@pytest.fixture
def mock_empty_config():
    """Create a mock config with no targets."""
    return create_mock_config(targets={}, default_target=None)


@pytest.fixture
def patch_load_config_with_target(mock_config_with_target):
    """Patch _load_config to return mock config with target."""
    with patch(
        "lib.services.configure_service.ConfigureService._load_config",
        return_value=mock_config_with_target,
    ):
        yield mock_config_with_target


@pytest.fixture
def patch_load_config_empty(mock_empty_config):
    """Patch _load_config to return empty mock config."""
    with patch(
        "lib.services.configure_service.ConfigureService._load_config",
        return_value=mock_empty_config,
    ):
        yield mock_empty_config


# ============================================================================
# Service Integration Tests
# ============================================================================


class TestConfigureServiceIntegration:
    """Integration tests for ConfigureService."""

    @pytest.mark.asyncio
    async def test_list_targets_with_targets(self, patch_load_config_with_target):
        """Test listing targets when targets exist."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureTargetListEvent,
            ConfigureStatusEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput()
        options = ConfigureOptions()

        events = []
        async for event in service.list_targets(input_data, options):
            events.append(event)

        # Should have status and target_list events
        assert len(events) >= 2

        # Check for status event
        status_events = [e for e in events if isinstance(e, ConfigureStatusEvent)]
        assert len(status_events) >= 1

        # Check for target list event
        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) == 1

        target_list = target_list_events[0]
        assert len(target_list.targets) == 1
        assert target_list.targets[0]["name"] == "test_db"
        assert target_list.targets[0]["engine"] == "postgresql"
        assert target_list.default_target == "test_db"

    @pytest.mark.asyncio
    async def test_list_targets_empty(self, patch_load_config_empty):
        """Test listing targets when no targets exist."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureTargetListEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput()
        options = ConfigureOptions()

        events = []
        async for event in service.list_targets(input_data, options):
            events.append(event)

        target_list_events = [
            e for e in events if isinstance(e, ConfigureTargetListEvent)
        ]
        assert len(target_list_events) == 1
        assert target_list_events[0].targets == []

    @pytest.mark.asyncio
    async def test_get_target_existing(self, patch_load_config_with_target):
        """Test getting an existing target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureTargetDetailEvent

        service = ConfigureService()

        events = []
        async for event in service.get_target("test_db"):
            events.append(event)

        detail_events = [e for e in events if isinstance(e, ConfigureTargetDetailEvent)]
        assert len(detail_events) == 1

        detail = detail_events[0]
        assert detail.target_name == "test_db"
        assert detail.engine == "postgresql"
        assert detail.host == "localhost"
        assert detail.port == 5432
        assert detail.database == "testdb"
        assert detail.user == "testuser"
        assert detail.is_default is True

    @pytest.mark.asyncio
    async def test_get_target_not_found(self, patch_load_config_with_target):
        """Test getting a non-existent target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureErrorEvent

        service = ConfigureService()

        events = []
        async for event in service.get_target("nonexistent"):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_add_target_success(self, patch_load_config_empty):
        """Test adding a new target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureSuccessEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput(target_name="new_db")
        options = ConfigureOptions(
            target_data={
                "engine": "postgresql",
                "host": "newhost",
                "port": 5432,
                "database": "newdb",
                "user": "newuser",
                "password_env": "NEW_DB_PASSWORD",
            }
        )

        events = []
        async for event in service.add_target(input_data, options):
            events.append(event)

        success_events = [e for e in events if isinstance(e, ConfigureSuccessEvent)]
        assert len(success_events) == 1
        assert success_events[0].target_name == "new_db"
        assert "added" in success_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_add_target_duplicate(self, patch_load_config_with_target):
        """Test adding a duplicate target fails."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureErrorEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput(target_name="test_db")
        options = ConfigureOptions(
            target_data={
                "engine": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
            }
        )

        events = []
        async for event in service.add_target(input_data, options):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "already exists" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_update_target_success(self, patch_load_config_with_target):
        """Test updating an existing target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureSuccessEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput(target_name="test_db")
        options = ConfigureOptions(
            target_data={
                "host": "newhost",
                "port": 5433,
            }
        )

        events = []
        async for event in service.update_target("test_db", input_data, options):
            events.append(event)

        success_events = [e for e in events if isinstance(e, ConfigureSuccessEvent)]
        assert len(success_events) == 1
        assert "updated" in success_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_update_target_not_found(self, patch_load_config_with_target):
        """Test updating a non-existent target fails."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureInput,
            ConfigureOptions,
            ConfigureErrorEvent,
        )

        service = ConfigureService()
        input_data = ConfigureInput(target_name="nonexistent")
        options = ConfigureOptions(target_data={"host": "newhost"})

        events = []
        async for event in service.update_target("nonexistent", input_data, options):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_remove_target_success(self, patch_load_config_with_target):
        """Test removing an existing target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureSuccessEvent

        service = ConfigureService()

        events = []
        async for event in service.remove_target("test_db"):
            events.append(event)

        success_events = [e for e in events if isinstance(e, ConfigureSuccessEvent)]
        assert len(success_events) == 1
        assert "removed" in success_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_remove_target_not_found(self, patch_load_config_with_target):
        """Test removing a non-existent target fails."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureErrorEvent

        service = ConfigureService()

        events = []
        async for event in service.remove_target("nonexistent"):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_set_default_success(self, patch_load_config_with_target):
        """Test setting a target as default."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureSuccessEvent

        service = ConfigureService()

        events = []
        async for event in service.set_default("test_db"):
            events.append(event)

        success_events = [e for e in events if isinstance(e, ConfigureSuccessEvent)]
        assert len(success_events) == 1
        assert "default" in success_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_set_default_not_found(self, patch_load_config_with_target):
        """Test setting a non-existent target as default fails."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureErrorEvent

        service = ConfigureService()

        events = []
        async for event in service.set_default("nonexistent"):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_test_connection_target_not_found(
        self, patch_load_config_with_target
    ):
        """Test connection test for non-existent target."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureErrorEvent

        service = ConfigureService()

        events = []
        async for event in service.test_connection("nonexistent"):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ConfigureErrorEvent)]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()

    @pytest.mark.asyncio
    async def test_test_connection_success(self, patch_load_config_with_target):
        """Test connection test with mocked database connection."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import (
            ConfigureStatusEvent,
            ConfigureConnectionTestEvent,
        )

        # Set the password env var
        os.environ["TEST_DB_PASSWORD"] = "testpass"

        try:
            service = ConfigureService()

            # Mock the actual connection test
            with patch.object(
                service,
                "_perform_connection_test",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "message": "Connected successfully!",
                    "server_version": "PostgreSQL 14.5",
                },
            ):
                events = []
                async for event in service.test_connection("test_db"):
                    events.append(event)

                # Should have status and connection test events
                status_events = [
                    e for e in events if isinstance(e, ConfigureStatusEvent)
                ]
                assert len(status_events) >= 1

                test_events = [
                    e for e in events if isinstance(e, ConfigureConnectionTestEvent)
                ]
                assert len(test_events) >= 1

                # Final event should be success
                final_test = test_events[-1]
                assert final_test.status == "success"
                assert final_test.server_version == "PostgreSQL 14.5"
        finally:
            if "TEST_DB_PASSWORD" in os.environ:
                del os.environ["TEST_DB_PASSWORD"]

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, patch_load_config_with_target):
        """Test connection test with failed connection."""
        from lib.services.configure_service import ConfigureService
        from lib.services.types import ConfigureConnectionTestEvent

        # Set the password env var
        os.environ["TEST_DB_PASSWORD"] = "testpass"

        try:
            service = ConfigureService()

            # Mock the actual connection test to fail
            with patch.object(
                service,
                "_perform_connection_test",
                new_callable=AsyncMock,
                return_value={
                    "success": False,
                    "message": "Connection refused: Cannot reach localhost:5432",
                },
            ):
                events = []
                async for event in service.test_connection("test_db"):
                    events.append(event)

                test_events = [
                    e for e in events if isinstance(e, ConfigureConnectionTestEvent)
                ]
                assert len(test_events) >= 1

                # Final event should be failure
                final_test = test_events[-1]
                assert final_test.status == "failed"
                assert "refused" in final_test.message.lower()
        finally:
            if "TEST_DB_PASSWORD" in os.environ:
                del os.environ["TEST_DB_PASSWORD"]


# ============================================================================
# API Integration Tests
# ============================================================================


class TestConfigureAPIIntegration:
    """Integration tests for Configure API endpoints."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app for testing."""
        from lib.api.app import create_app

        return create_app()

    @pytest.mark.asyncio
    async def test_list_targets_endpoint(self, app, mock_config_with_target):
        """Test GET /api/configure/targets endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/configure/targets")

                assert response.status_code == 200
                data = response.json()

                assert "targets" in data
                assert isinstance(data["targets"], list)
                assert len(data["targets"]) == 1
                assert data["targets"][0]["name"] == "test_db"
                assert data["default_target"] == "test_db"

    @pytest.mark.asyncio
    async def test_list_targets_empty(self, app, mock_empty_config):
        """Test GET /api/configure/targets with no targets."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_empty_config,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/configure/targets")

                assert response.status_code == 200
                data = response.json()

                assert "targets" in data
                assert data["targets"] == []

    @pytest.mark.asyncio
    async def test_get_target_endpoint(self, app, mock_config_with_target):
        """Test GET /api/configure/targets/{name} endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/configure/targets/test_db")

                assert response.status_code == 200
                data = response.json()

                assert data["target_name"] == "test_db"
                assert data["engine"] == "postgresql"
                assert data["host"] == "localhost"
                assert data["port"] == 5432
                assert data["database"] == "testdb"
                assert data["user"] == "testuser"
                assert data["is_default"] is True

    @pytest.mark.asyncio
    async def test_get_target_not_found(self, app, mock_config_with_target):
        """Test GET /api/configure/targets/{name} for non-existent target."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/configure/targets/nonexistent")

                assert response.status_code == 200  # Returns ErrorResponse, not 404
                data = response.json()

                assert data["success"] is False
                assert "not found" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_add_target_endpoint(self, app, mock_empty_config):
        """Test POST /api/configure/targets endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_empty_config,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {
                    "name": "new_db",
                    "target": {
                        "engine": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "newdb",
                        "user": "newuser",
                        "password_env": "NEW_DB_PASSWORD",
                    },
                }
                response = await client.post("/api/configure/targets", json=payload)

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is True
                assert data["target_name"] == "new_db"

    @pytest.mark.asyncio
    async def test_add_target_duplicate(self, app, mock_config_with_target):
        """Test POST /api/configure/targets with duplicate name."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {
                    "name": "test_db",
                    "target": {
                        "engine": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "testdb",
                        "user": "testuser",
                    },
                }
                response = await client.post("/api/configure/targets", json=payload)

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is False
                assert "already exists" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_update_target_endpoint(self, app, mock_config_with_target):
        """Test PUT /api/configure/targets/{name} endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {
                    "target": {
                        "engine": "postgresql",
                        "host": "newhost",
                        "port": 5433,
                        "database": "testdb",
                        "user": "testuser",
                    }
                }
                response = await client.put(
                    "/api/configure/targets/test_db", json=payload
                )

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is True
                assert data["target_name"] == "test_db"

    @pytest.mark.asyncio
    async def test_update_target_not_found(self, app, mock_config_with_target):
        """Test PUT /api/configure/targets/{name} for non-existent target."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {
                    "target": {
                        "engine": "postgresql",
                        "host": "newhost",
                        "port": 5433,
                        "database": "testdb",
                        "user": "testuser",
                    }
                }
                response = await client.put(
                    "/api/configure/targets/nonexistent", json=payload
                )

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is False
                assert "not found" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_remove_target_endpoint(self, app, mock_config_with_target):
        """Test DELETE /api/configure/targets/{name} endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.delete("/api/configure/targets/test_db")

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is True
                assert "removed" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_remove_target_not_found(self, app, mock_config_with_target):
        """Test DELETE /api/configure/targets/{name} for non-existent target."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.delete("/api/configure/targets/nonexistent")

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is False
                assert "not found" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_set_default_endpoint(self, app, mock_config_with_target):
        """Test PUT /api/configure/default endpoint."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {"name": "test_db"}
                response = await client.put("/api/configure/default", json=payload)

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is True
                assert "default" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_set_default_not_found(self, app, mock_config_with_target):
        """Test PUT /api/configure/default for non-existent target."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                payload = {"name": "nonexistent"}
                response = await client.put("/api/configure/default", json=payload)

                assert response.status_code == 200
                data = response.json()

                assert data["success"] is False
                assert "not found" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_test_connection_endpoint_sse(self, app, mock_config_with_target):
        """Test POST /api/configure/targets/{name}/test SSE endpoint."""
        from httpx import AsyncClient, ASGITransport
        from lib.services.configure_service import ConfigureService

        # Set the password env var
        os.environ["TEST_DB_PASSWORD"] = "testpass"

        try:
            # Mock both _load_config and _perform_connection_test
            with (
                patch(
                    "lib.services.configure_service.ConfigureService._load_config",
                    return_value=mock_config_with_target,
                ),
                patch.object(
                    ConfigureService,
                    "_perform_connection_test",
                    new_callable=AsyncMock,
                    return_value={
                        "success": True,
                        "message": "Connected successfully!",
                        "server_version": "PostgreSQL 14.5",
                    },
                ),
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    async with client.stream(
                        "POST", "/api/configure/targets/test_db/test"
                    ) as response:
                        assert response.status_code == 200

                        # Parse SSE events
                        events = []
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                try:
                                    event_data = json.loads(line[5:].strip())
                                    events.append(event_data)
                                except json.JSONDecodeError:
                                    pass

                        # Should have received events
                        assert len(events) >= 1

                        # Check for success event
                        success_events = [
                            e for e in events if e.get("status") == "success"
                        ]
                        assert len(success_events) >= 1
        finally:
            if "TEST_DB_PASSWORD" in os.environ:
                del os.environ["TEST_DB_PASSWORD"]

    @pytest.mark.asyncio
    async def test_test_connection_target_not_found_sse(
        self, app, mock_config_with_target
    ):
        """Test POST /api/configure/targets/{name}/test for non-existent target."""
        from httpx import AsyncClient, ASGITransport

        with patch(
            "lib.services.configure_service.ConfigureService._load_config",
            return_value=mock_config_with_target,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                async with client.stream(
                    "POST", "/api/configure/targets/nonexistent/test"
                ) as response:
                    assert response.status_code == 200

                    # Parse SSE events
                    events = []
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                event_data = json.loads(line[5:].strip())
                                events.append(event_data)
                            except json.JSONDecodeError:
                                pass

                    # Should have error event
                    error_events = [
                        e for e in events if "not found" in e.get("message", "").lower()
                    ]
                    assert len(error_events) >= 1


# ============================================================================
# CLI Integration Tests (via subprocess)
# ============================================================================


class TestConfigureCLIIntegration:
    """Integration tests for Configure CLI commands.

    Note: These tests run the actual CLI and use the real config file.
    They are smoke tests to verify the CLI works end-to-end.
    """

    def test_configure_list_command(self):
        """Test 'rdst configure list' command runs without error."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(rdst_root / "rdst.py"), "configure", "list"],
            capture_output=True,
            text=True,
            cwd=str(rdst_root),
        )

        # Command should succeed (exit code 0) or show targets
        # We just verify it doesn't crash
        assert result.returncode == 0 or "error" not in result.stderr.lower()


def main():
    """Run tests directly."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            __file__,
            "-v",
            "--tb=short",
        ],
        cwd=str(rdst_root),
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
