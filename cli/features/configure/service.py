"""Service for database target configuration with async event streaming."""

from typing import AsyncGenerator, Any, Dict

from shared.config.targets import TargetsConfig
from shared.password_resolver import resolve_password, resolve_password_value

from .adapters import operation_name, target_detail_to_dict, target_summary_to_dict
from .events import (
    ConfigureConnectionTestEvent,
    ConfigureErrorEvent,
    ConfigureEvent,
    ConfigureStatusEvent,
    ConfigureSuccessEvent,
    ConfigureTargetDetailEvent,
    ConfigureTargetListEvent,
)
from .models import ConfigureInput, ConfigureOptions, TargetDetail, TargetSummary


class ConfigureService:
    """Service for database target configuration."""

    def __init__(self) -> None:
        pass

    def _load_config(self) -> Any:
        cfg = TargetsConfig()
        cfg.load()
        return cfg

    async def list_targets(
        self,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            yield ConfigureStatusEvent(type="status", message="Loading targets...")

            cfg = self._load_config()
            target_names = cfg.list_targets()
            default_target = cfg.get_default()

            targets = []
            for name in target_names:
                target_data = cfg.get(name)
                if target_data:
                    summary = TargetSummary(
                        name=name,
                        engine=target_data.get("engine", "postgresql"),
                        host=target_data.get("host", ""),
                        port=target_data.get("port", ""),
                        database=target_data.get("database", ""),
                        proxy=target_data.get("proxy", "none"),
                        endpoint_verified=bool(
                            target_data.get("endpoint_verified", False)
                        ),
                        verified=bool(target_data.get("verified", False)),
                        has_password=resolve_password(target_data).available,
                        is_default=name == default_target,
                    )
                    targets.append(target_summary_to_dict(summary))

            yield ConfigureTargetListEvent(
                type="target_list",
                targets=targets,
                default_target=default_target,
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to list targets: {e}",
                operation=operation_name("list"),
            )

    async def get_target(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            cfg = self._load_config()
            target_data = cfg.get(name)

            if target_data is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found. Run 'rdst configure list' to see available targets",
                    operation=operation_name("get"),
                    target_name=name,
                )
                return

            default_target = cfg.get_default()
            detail = TargetDetail(
                target_name=name,
                engine=target_data.get("engine", "postgresql"),
                host=target_data.get("host", ""),
                port=target_data.get("port", 5432),
                database=target_data.get("database", ""),
                user=target_data.get("user", ""),
                password_env=target_data.get("password_env"),
                has_password=resolve_password(target_data).available,
                is_default=name == default_target,
                tls=target_data.get("tls", False),
                read_only=target_data.get("read_only", False),
            )

            yield ConfigureTargetDetailEvent(
                type="target_detail", **target_detail_to_dict(detail)
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to get target: {e}",
                operation=operation_name("get"),
                target_name=name,
            )

    async def add_target(
        self,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            name = input.target_name
            if not name:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target name is required",
                    operation=operation_name("add"),
                )
                return

            target_data = options.target_data
            if not target_data:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target data is required",
                    operation=operation_name("add"),
                    target_name=name,
                )
                return

            yield ConfigureStatusEvent(
                type="status", message=f"Adding target '{name}'..."
            )

            cfg = self._load_config()

            if cfg.get(name) is not None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' already exists. Use update to modify.",
                    operation=operation_name("add"),
                    target_name=name,
                )
                return

            cfg.upsert(name, target_data)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation=operation_name("add"),
                target_name=name,
                message=f"Target '{name}' added successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to add target: {e}",
                operation=operation_name("add"),
                target_name=input.target_name,
            )

    async def update_target(
        self,
        name: str,
        input: ConfigureInput,
        options: ConfigureOptions,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            target_data = options.target_data
            if not target_data:
                yield ConfigureErrorEvent(
                    type="error",
                    message="Target data is required",
                    operation=operation_name("update"),
                    target_name=name,
                )
                return

            yield ConfigureStatusEvent(
                type="status", message=f"Updating target '{name}'..."
            )

            cfg = self._load_config()
            existing = cfg.get(name)
            if existing is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found. Run 'rdst configure list' to see available targets",
                    operation=operation_name("update"),
                    target_name=name,
                )
                return

            merged = {**existing, **target_data}
            cfg.upsert(name, merged)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation=operation_name("update"),
                target_name=name,
                message=f"Target '{name}' updated successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to update target: {e}",
                operation=operation_name("update"),
                target_name=name,
            )

    async def remove_target(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            yield ConfigureStatusEvent(
                type="status", message=f"Removing target '{name}'..."
            )

            cfg = self._load_config()
            if cfg.get(name) is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found. Run 'rdst configure list' to see available targets",
                    operation=operation_name("remove"),
                    target_name=name,
                )
                return

            cfg.remove(name)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation=operation_name("remove"),
                target_name=name,
                message=f"Target '{name}' removed successfully",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to remove target: {e}",
                operation=operation_name("remove"),
                target_name=name,
            )

    async def set_default(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            cfg = self._load_config()
            if cfg.get(name) is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found. Run 'rdst configure list' to see available targets",
                    operation=operation_name("set_default"),
                    target_name=name,
                )
                return

            cfg.set_default(name)
            cfg.save()

            yield ConfigureSuccessEvent(
                type="success",
                operation=operation_name("set_default"),
                target_name=name,
                message=f"Target '{name}' set as default",
            )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Failed to set default: {e}",
                operation=operation_name("set_default"),
                target_name=name,
            )

    async def test_connection(
        self,
        name: str,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        try:
            cfg = self._load_config()
            target_config = cfg.get(name)

            if target_config is None:
                yield ConfigureErrorEvent(
                    type="error",
                    message=f"Target '{name}' not found. Run 'rdst configure list' to see available targets",
                    operation=operation_name("test"),
                    target_name=name,
                )
                return

            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="in_progress",
                message="Connecting...",
            )

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
                operation=operation_name("test"),
                target_name=name,
            )

    async def _perform_connection_test(
        self,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        engine = config.get("engine", "postgresql")
        host = config.get("host")
        port = config.get("port")
        user = config.get("user")
        database = config.get("database")
        tls = config.get("tls", False)

        password = resolve_password_value(config)
        if not password:
            password_env = config.get("password_env", "")
            if password_env:
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
                    "server_version": (version[:120] + "...") if len(version) > 120 else version,
                }

            if engine == "mysql":
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

            return {
                "success": False,
                "message": f"Unknown engine: {engine}",
            }

        except ImportError:
            driver = "psycopg2" if engine == "postgresql" else "pymysql"
            return {
                "success": False,
                "message": f"Missing database driver: {driver}. Install with: pip install {driver}",
            }

        except Exception as e:
            error_msg = str(e)
            if (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                return {
                    "success": False,
                    "message": f"Connection refused: Cannot reach {host}:{port}. "
                    f"Check that the database is running and accessible.",
                }
            if (
                "authentication failed" in error_msg.lower()
                or "access denied" in error_msg.lower()
            ):
                return {
                    "success": False,
                    "message": f"Authentication failed for user '{user}'. "
                    f"Check your username and password.",
                }
            if "does not exist" in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Database '{database}' does not exist. "
                    f"Check the database name.",
                }
            if "timeout" in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Connection timed out to {host}:{port}. "
                    f"Check network connectivity and firewall rules.",
                }
            return {
                "success": False,
                "message": f"Connection failed: {error_msg}",
            }
