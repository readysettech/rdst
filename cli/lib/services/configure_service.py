"""Service for database target configuration with async event streaming.

This service provides configuration management for database targets,
following the stateless async generator pattern used by other services.
"""

import os
from typing import AsyncGenerator, Any, Dict, Optional

from .password_resolver import resolve_password
from .types import (
    ConfigureInput,
    ConfigureOptions,
    ConfigureEvent,
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
)


class ConfigureService:
    """Service for database target configuration.

    Supports target listing, adding, updating, removing, and connection testing.
    Both CLI and Web API can consume the same event stream.

    Usage:
        service = ConfigureService()

        # List targets
        async for event in service.list_targets(input, options):
            handle_event(event)

        # Test connection
        async for event in service.test_connection("prod"):
            handle_event(event)
    """

    def __init__(self) -> None:
        """Initialize the configure service (stateless)."""
        pass

    def _load_config(self) -> Any:
        """Load TargetsConfig from CLI module."""
        from lib.cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        return cfg

    async def list_targets(
        self,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """List all configured targets.

        Yields:
            ConfigureStatusEvent: Loading status
            ConfigureTargetListEvent: List of targets with metadata
        """
        try:
            yield ConfigureStatusEvent(type="status", message="Loading targets...")

            cfg = self._load_config()
            target_names = cfg.list_targets()
            default_target = cfg.get_default()

            targets = []
            for name in target_names:
                target_data = cfg.get(name)
                if target_data:
                    targets.append(
                        {
                            "name": name,
                            "engine": target_data.get("engine", "postgresql"),
                            "host": target_data.get("host", ""),
                            "port": target_data.get("port", ""),
                            "database": target_data.get("database", ""),
                            "proxy": target_data.get("proxy", "none"),
                            "endpoint_verified": bool(
                                target_data.get("endpoint_verified", False)
                            ),
                            "verified": bool(target_data.get("verified", False)),
                            "has_password": resolve_password(target_data).available,
                            "is_default": name == default_target,
                        }
                    )

            yield ConfigureTargetListEvent(
                type="target_list",
                targets=targets,
                default_target=default_target,
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to list targets: {e}",
                operation="list",
            )

    async def get_target(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Get details of a specific target.

        Args:
            name: Target name to retrieve

        Yields:
            ConfigureTargetDetailEvent: Target details
            ConfigureErrorEvent: If target not found
        """
        try:
            cfg = self._load_config()
            target_data = cfg.get(name)

            if target_data is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found",
                    operation="get",
                    target_name=name,
                )
                return

            default_target = cfg.get_default()

            yield ConfigureTargetDetailEvent(
                type="target_detail",
                target_name=name,
                engine=target_data.get("engine", "postgresql"),
                host=target_data.get("host", ""),
                port=target_data.get("port", 5432),
                database=target_data.get("database", ""),
                user=target_data.get("user", ""),
                has_password=resolve_password(target_data).available,
                is_default=name == default_target,
                tls=target_data.get("tls", False),
                read_only=target_data.get("read_only", False),
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to get target: {e}",
                operation="get",
                target_name=name,
            )

    async def add_target(
        self,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Add a new target configuration.

        Args:
            input: ConfigureInput with target_name
            options: ConfigureOptions with target_data containing connection details

        Yields:
            ConfigureStatusEvent: Progress updates
            ConfigureSuccessEvent: On successful add
            ConfigureErrorEvent: On failure
        """
        try:
            name = input.target_name
            if not name:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target name is required",
                    operation="add",
                )
                return

            target_data = options.target_data
            if not target_data:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target data is required",
                    operation="add",
                    target_name=name,
                )
                return

            yield ConfigureStatusEvent(
                type="status", message=f"Adding target '{name}'..."
            )

            cfg = self._load_config()

            # Check if target already exists
            if cfg.get(name) is not None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' already exists. Use update to modify.",
                    operation="add",
                    target_name=name,
                )
                return

            # Add the target
            cfg.upsert(name, target_data)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation="add",
                target_name=name,
                message=f"Target '{name}' added successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to add target: {e}",
                operation="add",
                target_name=input.target_name,
            )

    async def update_target(
        self,
        name: str,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Update an existing target configuration.

        Args:
            name: Target name to update
            input: ConfigureInput (unused, kept for consistency)
            options: ConfigureOptions with target_data containing updated connection details

        Yields:
            ConfigureStatusEvent: Progress updates
            ConfigureSuccessEvent: On successful update
            ConfigureErrorEvent: On failure
        """
        try:
            target_data = options.target_data
            if not target_data:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target data is required",
                    operation="update",
                    target_name=name,
                )
                return

            yield ConfigureStatusEvent(
                type="status", message=f"Updating target '{name}'..."
            )

            cfg = self._load_config()

            # Check if target exists
            existing = cfg.get(name)
            if existing is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found",
                    operation="update",
                    target_name=name,
                )
                return

            # Merge existing with new data
            merged = {**existing, **target_data}
            cfg.upsert(name, merged)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation="update",
                target_name=name,
                message=f"Target '{name}' updated successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to update target: {e}",
                operation="update",
                target_name=name,
            )

    async def remove_target(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Remove a target configuration.

        Args:
            name: Target name to remove

        Yields:
            ConfigureStatusEvent: Progress updates
            ConfigureSuccessEvent: On successful removal
            ConfigureErrorEvent: On failure
        """
        try:
            yield ConfigureStatusEvent(
                type="status", message=f"Removing target '{name}'..."
            )

            cfg = self._load_config()

            # Check if target exists
            if cfg.get(name) is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found",
                    operation="remove",
                    target_name=name,
                )
                return

            # Remove the target
            cfg.remove(name)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation="remove",
                target_name=name,
                message=f"Target '{name}' removed successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to remove target: {e}",
                operation="remove",
                target_name=name,
            )

    async def set_default(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Set a target as the default.

        Args:
            name: Target name to set as default

        Yields:
            ConfigureSuccessEvent: On successful update
            ConfigureErrorEvent: On failure
        """
        try:
            cfg = self._load_config()

            # Check if target exists
            if cfg.get(name) is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found",
                    operation="set_default",
                    target_name=name,
                )
                return

            cfg.set_default(name)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation="set_default",
                target_name=name,
                message=f"Target '{name}' set as default",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to set default: {e}",
                operation="set_default",
                target_name=name,
            )

    async def test_connection(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        """Test connection to a target.

        Args:
            name: Target name to test

        Yields:
            ConfigureStatusEvent: Progress updates
            ConfigureConnectionTestEvent: Test result
        """
        try:
            yield ConfigureStatusEvent(
                type="status", message=f"Testing connection to '{name}'..."
            )

            cfg = self._load_config()
            target_config = cfg.get(name)

            if target_config is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found",
                    operation="test",
                    target_name=name,
                )
                return

            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="in_progress",
                message="Connecting...",
            )

            # Perform the actual connection test
            result = await self._perform_connection_test(target_config)

            if result["success"]:
                yield ConfigureConnectionTestEvent(
                    type="connection_test",
                    target_name=name,
                    status="success",
                    message=result.get("message", "Connection successful"),
                    server_version=result.get("server_version"),
                )
            else:
                yield ConfigureConnectionTestEvent(
                    type="connection_test",
                    target_name=name,
                    status="failed",
                    message=result.get("message", "Connection failed"),
                )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Connection test failed: {e}",
                operation="test",
                target_name=name,
            )

    async def _perform_connection_test(
        self,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Perform the actual database connection test.

        Args:
            config: Target configuration dict

        Returns:
            Dict with success, message, and optional server_version
        """
        engine = config.get("engine", "postgresql")
        host = config.get("host")
        port = config.get("port")
        user = config.get("user")
        database = config.get("database")
        password_env = config.get("password_env")
        tls = config.get("tls", False)

        # Get password from environment variable
        password = ""
        if password_env:
            password = os.environ.get(password_env, "")
            if not password:
                return {
                    "success": False,
                    "message": f"Environment variable '{password_env}' is not set. "
                    f'Set it with: export {password_env}="your_password"',
                }

        try:
            if engine == "postgresql":
                import psycopg2

                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    sslmode="require" if tls else "prefer",
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return {
                    "success": True,
                    "message": "Connected successfully!",
                    "server_version": version[:80] if len(version) > 80 else version,
                }

            elif engine == "mysql":
                import pymysql

                conn = pymysql.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    ssl={"ssl": {}} if tls else None,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return {
                    "success": True,
                    "message": "Connected successfully!",
                    "server_version": f"MySQL {version}",
                }

            else:
                return {"success": False, "message": f"Unknown engine: {engine}"}

        except ImportError:
            driver = "psycopg2" if engine == "postgresql" else "pymysql"
            return {
                "success": False,
                "message": f"Missing database driver: {driver}. Install with: pip install {driver}",
            }
        except Exception as e:
            error_msg = str(e)
            # Clean up common error messages
            if (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                return {
                    "success": False,
                    "message": f"Connection refused: Cannot reach {host}:{port}. "
                    "Check that the database is running and accessible.",
                }
            elif (
                "authentication failed" in error_msg.lower()
                or "access denied" in error_msg.lower()
            ):
                return {
                    "success": False,
                    "message": f"Authentication failed for user '{user}'. "
                    "Check your username and password.",
                }
            elif "does not exist" in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Database '{database}' does not exist. Check the database name.",
                }
            elif "timeout" in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Connection timed out to {host}:{port}. "
                    "Check network connectivity and firewall rules.",
                }
            else:
                return {"success": False, "message": f"Connection failed: {error_msg}"}
