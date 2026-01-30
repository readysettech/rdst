#!/usr/bin/env python3
"""
Integration tests for Configure API endpoints.

Tests all configure API endpoints with mocked ConfigureService.
Uses httpx AsyncClient for async API testing.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from lib.api.app import create_app
from lib.services.types import (
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureStatusEvent,
    ConfigureConnectionTestEvent,
)


@pytest.fixture
def app():
    """Create FastAPI app for testing."""
    return create_app()


# ============================================================================
# GET /api/configure/targets - List Targets
# ============================================================================


@pytest.mark.asyncio
async def test_list_targets_empty(app):
    """Test listing targets when none configured."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_list_targets(*args, **kwargs):
            yield ConfigureTargetListEvent(
                type="target_list",
                targets=[],
                default_target=None,
            )

        mock_service.list_targets = mock_list_targets

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets")

        assert response.status_code == 200
        data = response.json()
        assert "targets" in data
        assert data["targets"] == []
        assert data["default_target"] is None


@pytest.mark.asyncio
async def test_list_targets_with_targets(app):
    """Test listing targets when targets exist."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_list_targets(*args, **kwargs):
            yield ConfigureTargetListEvent(
                type="target_list",
                targets=[
                    {
                        "name": "prod",
                        "engine": "postgresql",
                        "has_password": True,
                        "is_default": True,
                    },
                    {
                        "name": "staging",
                        "engine": "mysql",
                        "has_password": False,
                        "is_default": False,
                    },
                ],
                default_target="prod",
            )

        mock_service.list_targets = mock_list_targets

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets")

        assert response.status_code == 200
        data = response.json()
        assert len(data["targets"]) == 2
        assert data["targets"][0]["name"] == "prod"
        assert data["targets"][0]["is_default"] is True
        assert data["targets"][1]["name"] == "staging"
        assert data["default_target"] == "prod"


@pytest.mark.asyncio
async def test_list_targets_error(app):
    """Test listing targets when service returns error."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_list_targets(*args, **kwargs):
            yield ConfigureErrorEvent(
                type="error",
                message="Failed to load config file",
                operation="list",
            )

        mock_service.list_targets = mock_list_targets

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets")

        assert response.status_code == 200  # API returns 200 with error in body
        data = response.json()
        assert data["success"] is False
        assert "Failed to load config file" in data["message"]


# ============================================================================
# GET /api/configure/targets/{name} - Get Target
# ============================================================================


@pytest.mark.asyncio
async def test_get_target_exists(app):
    """Test getting a target that exists."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_target(name):
            yield ConfigureTargetDetailEvent(
                type="target_detail",
                target_name="prod",
                engine="postgresql",
                host="db.example.com",
                port=5432,
                database="myapp",
                user="admin",
                has_password=True,
                is_default=True,
                tls=True,
            )

        mock_service.get_target = mock_get_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets/prod")

        assert response.status_code == 200
        data = response.json()
        assert data["target_name"] == "prod"
        assert data["engine"] == "postgresql"
        assert data["host"] == "db.example.com"
        assert data["port"] == 5432
        assert data["database"] == "myapp"
        assert data["user"] == "admin"
        assert data["has_password"] is True
        assert data["is_default"] is True
        assert data["tls"] is True


@pytest.mark.asyncio
async def test_get_target_not_found(app):
    """Test getting a target that doesn't exist."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_target(name):
            yield ConfigureErrorEvent(
                type="error",
                message=f"Target '{name}' not found",
                operation="get",
                target_name=name,
            )

        mock_service.get_target = mock_get_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"]


# ============================================================================
# POST /api/configure/targets - Add Target
# ============================================================================


@pytest.mark.asyncio
async def test_add_target_success(app):
    """Test adding a new target successfully."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_add_target(*args, **kwargs):
            yield ConfigureSuccessEvent(
                type="success",
                operation="add",
                target_name="new_db",
                message="Target 'new_db' added successfully",
            )

        mock_service.add_target = mock_add_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/configure/targets",
                json={
                    "name": "new_db",
                    "target": {
                        "engine": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "testdb",
                        "user": "testuser",
                        "password_env": "TEST_DB_PASSWORD",
                        "tls": False,
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["target_name"] == "new_db"
        assert "added successfully" in data["message"]


@pytest.mark.asyncio
async def test_add_target_duplicate(app):
    """Test adding a target that already exists."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_add_target(*args, **kwargs):
            yield ConfigureErrorEvent(
                type="error",
                message="Target 'existing' already exists. Use update to modify.",
                operation="add",
                target_name="existing",
            )

        mock_service.add_target = mock_add_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/configure/targets",
                json={
                    "name": "existing",
                    "target": {
                        "engine": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "testdb",
                        "user": "testuser",
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "already exists" in data["message"]


@pytest.mark.asyncio
async def test_add_target_validation_error(app):
    """Test adding a target with missing required fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing required 'host' field
        response = await client.post(
            "/api/configure/targets",
            json={
                "name": "bad_target",
                "target": {
                    "engine": "postgresql",
                    # host is missing
                    "port": 5432,
                    "database": "testdb",
                    "user": "testuser",
                },
            },
        )

    assert response.status_code == 422  # Validation error


# ============================================================================
# PUT /api/configure/targets/{name} - Update Target
# ============================================================================


@pytest.mark.asyncio
async def test_update_target_success(app):
    """Test updating an existing target."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_update_target(*args, **kwargs):
            yield ConfigureSuccessEvent(
                type="success",
                operation="update",
                target_name="prod",
                message="Target 'prod' updated successfully",
            )

        mock_service.update_target = mock_update_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/configure/targets/prod",
                json={
                    "target": {
                        "engine": "postgresql",
                        "host": "new-host.example.com",
                        "port": 5433,
                        "database": "myapp",
                        "user": "admin",
                        "tls": True,
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["target_name"] == "prod"
        assert "updated successfully" in data["message"]


@pytest.mark.asyncio
async def test_update_target_not_found(app):
    """Test updating a target that doesn't exist."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_update_target(*args, **kwargs):
            yield ConfigureErrorEvent(
                type="error",
                message="Target 'nonexistent' not found",
                operation="update",
                target_name="nonexistent",
            )

        mock_service.update_target = mock_update_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/configure/targets/nonexistent",
                json={
                    "target": {
                        "engine": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "testdb",
                        "user": "testuser",
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"]


# ============================================================================
# DELETE /api/configure/targets/{name} - Remove Target
# ============================================================================


@pytest.mark.asyncio
async def test_remove_target_success(app):
    """Test removing an existing target."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_remove_target(name):
            yield ConfigureSuccessEvent(
                type="success",
                operation="remove",
                target_name=name,
                message=f"Target '{name}' removed successfully",
            )

        mock_service.remove_target = mock_remove_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/api/configure/targets/old_db")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["target_name"] == "old_db"
        assert "removed successfully" in data["message"]


@pytest.mark.asyncio
async def test_remove_target_not_found(app):
    """Test removing a target that doesn't exist."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_remove_target(name):
            yield ConfigureErrorEvent(
                type="error",
                message=f"Target '{name}' not found",
                operation="remove",
                target_name=name,
            )

        mock_service.remove_target = mock_remove_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/api/configure/targets/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"]


# ============================================================================
# PUT /api/configure/default - Set Default Target
# ============================================================================


@pytest.mark.asyncio
async def test_set_default_success(app):
    """Test setting a target as default."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_set_default(name):
            yield ConfigureSuccessEvent(
                type="success",
                operation="set_default",
                target_name=name,
                message=f"Target '{name}' set as default",
            )

        mock_service.set_default = mock_set_default

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/configure/default",
                json={"name": "prod"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["target_name"] == "prod"
        assert "set as default" in data["message"]


@pytest.mark.asyncio
async def test_set_default_not_found(app):
    """Test setting default to a target that doesn't exist."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_set_default(name):
            yield ConfigureErrorEvent(
                type="error",
                message=f"Target '{name}' not found",
                operation="set_default",
                target_name=name,
            )

        mock_service.set_default = mock_set_default

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/configure/default",
                json={"name": "nonexistent"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["message"]


# ============================================================================
# POST /api/configure/targets/{name}/test - Test Connection (SSE)
# ============================================================================


@pytest.mark.asyncio
async def test_connection_success(app):
    """Test connection test endpoint with successful connection."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_test_connection(name):
            yield ConfigureStatusEvent(
                type="status",
                message=f"Testing connection to '{name}'...",
            )
            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="in_progress",
                message="Connecting...",
            )
            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="success",
                message="Connected successfully!",
                server_version="PostgreSQL 15.2",
            )

        mock_service.test_connection = mock_test_connection

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/configure/targets/prod/test")

        # SSE response - check content type
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Parse SSE events from response body
        body = response.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    events.append(json.loads(data))

        # Verify we got the expected events
        assert len(events) >= 2
        # Find the success event
        success_events = [e for e in events if e.get("status") == "success"]
        assert len(success_events) == 1
        assert success_events[0]["server_version"] == "PostgreSQL 15.2"


@pytest.mark.asyncio
async def test_connection_failure(app):
    """Test connection test endpoint with failed connection."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_test_connection(name):
            yield ConfigureStatusEvent(
                type="status",
                message=f"Testing connection to '{name}'...",
            )
            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="in_progress",
                message="Connecting...",
            )
            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="failed",
                message="Connection refused: Cannot reach localhost:5432",
            )

        mock_service.test_connection = mock_test_connection

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/configure/targets/prod/test")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        body = response.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    events.append(json.loads(data))

        # Find the failed event
        failed_events = [e for e in events if e.get("status") == "failed"]
        assert len(failed_events) == 1
        assert "Connection refused" in failed_events[0]["message"]


@pytest.mark.asyncio
async def test_connection_target_not_found(app):
    """Test connection test endpoint with nonexistent target."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_test_connection(name):
            yield ConfigureErrorEvent(
                type="error",
                message=f"Target '{name}' not found",
                operation="test",
                target_name=name,
            )

        mock_service.test_connection = mock_test_connection

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/configure/targets/nonexistent/test")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        body = response.text
        events = []
        for line in body.split("\n"):
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    events.append(json.loads(data))

        # Find the error event
        error_events = [e for e in events if "not found" in e.get("message", "")]
        assert len(error_events) == 1


# ============================================================================
# Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_add_target_with_mysql_engine(app):
    """Test adding a MySQL target."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_add_target(*args, **kwargs):
            yield ConfigureSuccessEvent(
                type="success",
                operation="add",
                target_name="mysql_db",
                message="Target 'mysql_db' added successfully",
            )

        mock_service.add_target = mock_add_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/configure/targets",
                json={
                    "name": "mysql_db",
                    "target": {
                        "engine": "mysql",
                        "host": "mysql.example.com",
                        "port": 3306,
                        "database": "myapp",
                        "user": "root",
                        "password_env": "MYSQL_PASSWORD",
                        "tls": True,
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_add_target_minimal_fields(app):
    """Test adding a target with only required fields."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_add_target(*args, **kwargs):
            yield ConfigureSuccessEvent(
                type="success",
                operation="add",
                target_name="minimal",
                message="Target 'minimal' added successfully",
            )

        mock_service.add_target = mock_add_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/configure/targets",
                json={
                    "name": "minimal",
                    "target": {
                        "host": "localhost",
                        "database": "testdb",
                        "user": "testuser",
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


@pytest.mark.asyncio
async def test_list_targets_no_response(app):
    """Test list targets when service yields no events."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_list_targets(*args, **kwargs):
            # Empty generator - no events yielded
            return
            yield  # Make it a generator

        mock_service.list_targets = mock_list_targets

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "No response from service" in data["message"]


@pytest.mark.asyncio
async def test_get_target_no_response(app):
    """Test get target when service yields no events."""
    with patch("lib.api.routes.configure.ConfigureService") as mock_service_class:
        mock_service = mock_service_class.return_value

        async def mock_get_target(name):
            return
            yield

        mock_service.get_target = mock_get_target

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/configure/targets/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "No response from service" in data["message"]
