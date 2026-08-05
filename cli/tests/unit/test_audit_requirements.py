"""Tests for the capture preflight check and GET /api/audit/requirements."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from features.audit.api import routes as audit_routes
from features.audit.capture_service import CaptureService
from shared.api.target_guard import TargetGuard
from shared.ssh_tunnel import SshKeyError


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


def _pg_connection(execute_side_effect=None):
    connection = MagicMock()
    cursor = connection.cursor.return_value
    if execute_side_effect is not None:
        cursor.execute.side_effect = execute_side_effect
    return connection


def _mysql_connection(row):
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = row
    return connection


class TestCheckQueryStats:
    def test_postgresql_ok(self):
        result = CaptureService.check_query_stats(_pg_connection(), "postgresql")
        assert result["status"] == "ok"
        assert result["remediation"] is None

    def test_postgresql_missing_extension(self):
        connection = _pg_connection(
            Exception('relation "pg_stat_statements" does not exist')
        )
        result = CaptureService.check_query_stats(connection, "postgresql")
        assert result["status"] == "missing"
        assert "pg_stat_statements" in result["detail"]
        assert "CREATE EXTENSION pg_stat_statements" in result["remediation"]
        assert "shared_preload_libraries" in result["remediation"]

    def test_postgresql_unrelated_error(self):
        connection = _pg_connection(Exception("server closed the connection"))
        result = CaptureService.check_query_stats(connection, "postgresql")
        assert result["status"] == "error"
        assert "server closed" in result["detail"]
        assert result["remediation"] is None

    def test_mysql_performance_schema_on(self):
        connection = _mysql_connection(("performance_schema", "ON"))
        result = CaptureService.check_query_stats(connection, "mysql")
        assert result["status"] == "ok"

    def test_mysql_performance_schema_off(self):
        connection = _mysql_connection(("performance_schema", "OFF"))
        result = CaptureService.check_query_stats(connection, "mysql")
        assert result["status"] == "missing"
        assert "performance_schema is OFF" in result["detail"]
        assert "performance_schema=ON" in result["remediation"]

    def test_mysql_dict_cursor_row(self):
        connection = _mysql_connection({"Variable_name": "performance_schema", "Value": "ON"})
        result = CaptureService.check_query_stats(connection, "mysql")
        assert result["status"] == "ok"

    def test_mysql_query_error_is_error_status(self):
        connection = MagicMock()
        connection.cursor.return_value.execute.side_effect = Exception("timeout")
        result = CaptureService.check_query_stats(connection, "mysql")
        assert result["status"] == "error"


class TestRunCapturePreflight:
    @pytest.mark.asyncio
    async def test_missing_extension_aborts_with_combined_message(
        self, tmp_rdst_home
    ):
        class _FakeConfig:
            def get(self, name):
                return {"engine": "postgresql", "host": "localhost"}

        connection = _pg_connection(
            Exception('relation "pg_stat_statements" does not exist')
        )
        service = CaptureService(config=_FakeConfig())

        with patch(
            "shared.db_connection.create_direct_connection", return_value=connection
        ):
            events = [
                e async for e in service.run_capture("mydb", duration_seconds=10)
            ]

        error = events[-1]
        assert error.type == "error"
        assert error.phase == "preflight"
        assert error.message == (
            "pg_stat_statements extension is not installed. Query capture requires it.\n"
            "Fix: CREATE EXTENSION pg_stat_statements;\n"
            "For RDS: add pg_stat_statements to shared_preload_libraries in the parameter group."
        )
        connection.close.assert_called()


class TestRequirementsRoute:
    @pytest.mark.asyncio
    async def test_ok(self):
        guard = TargetGuard("mydb", {"engine": "postgresql", "host": "h"}, "postgresql")
        with patch(
            "shared.db_connection.create_direct_connection",
            return_value=_pg_connection(),
        ):
            response = await audit_routes.get_capture_requirements(guard)
        body = response.model_dump()
        assert body["target"] == "mydb"
        assert body["engine"] == "postgresql"
        assert body["query_stats"] == "ok"
        assert body["remediation"] is None

    @pytest.mark.asyncio
    async def test_missing_returns_remediation(self):
        guard = TargetGuard("mydb", {"engine": "postgresql", "host": "h"}, "postgresql")
        connection = _pg_connection(
            Exception('relation "pg_stat_statements" does not exist')
        )
        with patch(
            "shared.db_connection.create_direct_connection", return_value=connection
        ):
            response = await audit_routes.get_capture_requirements(guard)
        body = response.model_dump()
        assert body["query_stats"] == "missing"
        assert "CREATE EXTENSION pg_stat_statements" in body["remediation"]
        connection.close.assert_called()

    @pytest.mark.asyncio
    async def test_connection_failure_is_error(self):
        guard = TargetGuard("mydb", {"engine": "mysql", "host": "h"}, "mysql")
        with patch(
            "shared.db_connection.create_direct_connection",
            side_effect=Exception("Authentication failed for user\nextra context"),
        ):
            response = await audit_routes.get_capture_requirements(guard)
        body = response.model_dump()
        assert body["query_stats"] == "error"
        assert body["category"] == "target_password_required"
        assert body["detail"].startswith(
            "No password is available for target 'mydb'"
        )
        assert "\n" not in body["detail"]
        assert body["remediation"] is None

    @pytest.mark.asyncio
    async def test_ssh_failure_is_blocking_and_categorized(self):
        guard = TargetGuard(
            "private-db",
            {
                "engine": "postgresql",
                "host": "db.internal",
                "port": 5432,
                "ssh": {
                    "host": "jump.example.com",
                    "key_path": "~/.ssh/missing.pem",
                },
            },
            "postgresql",
        )
        with patch(
            "shared.db_connection.resolve_connection_params",
            side_effect=SshKeyError("missing"),
        ):
            response = await audit_routes.get_capture_requirements(guard)

        body = response.model_dump()
        assert body["query_stats"] == "error"
        assert body["category"] == "ssh_key_missing"
        assert "SSH key not found:" in body["detail"]
        assert "/.ssh/missing.pem" in body["detail"]
        assert "Choose an existing private key" in body["detail"]

    @pytest.mark.asyncio
    async def test_healthy_ssh_target_uses_local_endpoint_without_extra_noise(self):
        guard = TargetGuard(
            "private-db",
            {
                "engine": "postgresql",
                "host": "db.internal",
                "port": 5432,
                "database": "app",
                "user": "app",
                "password": "not-a-real-secret",
                "ssh": {"host": "jump.example.com"},
            },
            "postgresql",
        )
        connection = _pg_connection()
        params = {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 41001,
            "user": "app",
            "password": "not-a-real-secret",
            "database": "app",
            "tls": False,
        }
        manager = MagicMock()
        with (
            patch(
                "shared.ssh_tunnel.get_tunnel_manager",
                return_value=manager,
            ),
            patch(
                "shared.db_connection.resolve_connection_params",
                return_value=params,
            ) as resolve_params,
            patch(
                "shared.db_connection.create_direct_connection",
                return_value=connection,
            ) as create_connection,
        ):
            response = await audit_routes.get_capture_requirements(guard)

        body = response.model_dump()
        assert body["query_stats"] == "ok"
        assert "category" not in body
        effective = create_connection.call_args.args[0]
        assert effective["host"] == "127.0.0.1"
        assert effective["port"] == 41001
        assert "ssh" not in effective
        resolve_params.assert_called_once_with(
            target="private-db",
            target_config=guard.target_config,
            force_fresh_tunnel=True,
        )
        manager.close.assert_not_called()
