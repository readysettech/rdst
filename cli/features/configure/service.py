"""Service for database target configuration with async event streaming."""

from typing import AsyncGenerator, Any, Dict, Optional

from shared.config.targets import TargetsConfig, default_port_for
from shared.db_connection import (
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    resolve_connection_params,
)
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

    @staticmethod
    def _public_ssh_config(target_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ssh = target_data.get("ssh")
        if not isinstance(ssh, dict) or not (ssh.get("host") or ssh.get("profile")):
            return None
        return {
            key: ssh[key]
            for key in ("host", "port", "user", "key_path", "profile")
            if key in ssh and ssh[key] is not None
        }

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
                    summary_data = target_summary_to_dict(summary)
                    summary_data["ssh"] = self._public_ssh_config(target_data)
                    if "publicly_accessible" in target_data:
                        summary_data["publicly_accessible"] = bool(
                            target_data["publicly_accessible"]
                        )
                    targets.append(summary_data)

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
            engine = target_data.get("engine", "postgresql")
            detail = TargetDetail(
                target_name=name,
                engine=engine,
                host=target_data.get("host", ""),
                port=target_data.get("port", default_port_for(engine)),
                database=target_data.get("database", ""),
                user=target_data.get("user", ""),
                password_env=target_data.get("password_env"),
                has_password=resolve_password(target_data).available,
                is_default=name == default_target,
                tls=target_data.get("tls", False),
                tls_verify=target_data.get("tls_verify", False),
                tls_ca=target_data.get("tls_ca"),
                read_only=target_data.get("read_only", False),
            )

            detail_data = target_detail_to_dict(detail)
            detail_data["ssh"] = self._public_ssh_config(target_data)
            if "publicly_accessible" in target_data:
                detail_data["publicly_accessible"] = bool(
                    target_data["publicly_accessible"]
                )
            for key in ("tags", "region", "instance_class", "group"):
                if key in target_data:
                    detail_data[key] = target_data[key]

            yield ConfigureTargetDetailEvent(type="target_detail", **detail_data)

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
            if "password" in target_data:
                if target_data["password"] is None:
                    merged.pop("password", None)
                else:
                    merged.pop("password_env", None)
            if "ssh" in target_data and not target_data["ssh"]:
                merged.pop("ssh", None)
            if existing.get("ssh") != merged.get("ssh"):
                from shared.ssh_tunnel import get_tunnel_manager

                get_tunnel_manager().close(name)
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

            # Stop target-owned work and retire its local sandbox before the
            # credentials required to settle that work are deleted.
            from shared.deploy.sandbox_manager import sandbox_manager
            from shared.run_registry import run_registry

            run_registry.cancel_target(name)
            await sandbox_manager.start()
            try:
                await sandbox_manager.remove_target(name)
            finally:
                await sandbox_manager.stop()
            cfg.remove(name)
            cfg.save()
            from shared.ssh_tunnel import get_tunnel_manager

            get_tunnel_manager().close(name)

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
        target_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[ConfigureEvent, None]:
        manager = None
        test_succeeded = False
        try:
            if target_config is None:
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

            # An explicit test must validate the configuration supplied for this
            # request, never a tunnel left over from an earlier test or edit.
            # Freshness is part of ensure_tunnel so a concurrent resolve cannot
            # slip between a separate close and reopen.
            from shared.ssh_tunnel import get_tunnel_manager

            manager = get_tunnel_manager()

            yield ConfigureConnectionTestEvent(
                type="connection_test",
                target_name=name,
                status="in_progress",
                message="Connecting...",
            )

            test_config = dict(target_config)
            test_config["name"] = name
            result = await self.perform_connection_test(
                test_config,
                force_fresh_tunnel=True,
            )

            if result["success"]:
                test_succeeded = True
                yield ConfigureConnectionTestEvent(
                    type="connection_test",
                    target_name=name,
                    status="success",
                    message=result.get("message", "Connection successful"),
                    server_version=result.get("server_version"),
                    privileges=result.get("privileges"),
                )
            else:
                yield ConfigureConnectionTestEvent(
                    type="connection_test",
                    target_name=name,
                    status="failed",
                    message=result.get("message", "Connection failed"),
                    code=result.get("code"),
                    category=result.get("category"),
                    password_env=result.get("password_env"),
                )

        except Exception as e:
            yield ConfigureErrorEvent(
                type="error",
                message=f"Connection test failed: {e}",
                operation=operation_name("test"),
                target_name=name,
            )
        finally:
            # Successful tests deliberately keep their freshly-opened tunnel so
            # status surfaces immediately report Active. Failed/cancelled tests
            # do not leave a misleading path behind.
            if manager is not None and not test_succeeded:
                manager.close(name)

    async def perform_connection_test(
        self,
        config: Dict[str, Any],
        *,
        force_fresh_tunnel: bool = False,
    ) -> Dict[str, Any]:
        engine = config.get("engine", "postgresql")
        host = config.get("host")
        port = config.get("port")
        user = config.get("user")
        database = config.get("database")
        password = resolve_password_value(config)
        password_missing = not password
        provider_password_probe = False
        if password_missing:
            from features.allowlist.providers import provider_for_target

            provider_password_probe = provider_for_target(config) is not None
            password_env = config.get("password_env", "")
            if password_env and not provider_password_probe:
                return {
                    "success": False,
                    "code": "TARGET_PASSWORD_REQUIRED",
                    "password_env": password_env,
                    "message": "Enter the password for this target again.",
                }

        conn = None
        try:
            params = resolve_connection_params(
                target_config=config,
                force_fresh_tunnel=force_fresh_tunnel,
            )
            if provider_password_probe:
                # Provider network restrictions reject the connection before
                # authentication. A deliberately invalid password lets that
                # refusal surface without treating a missing password as the
                # first failure.
                params["password"] = "rdst-connectivity-probe-invalid-password"
            if engine == "postgresql":
                import psycopg2

                conn = psycopg2.connect(**postgres_connection_kwargs(params))
                if provider_password_probe:
                    return self._password_required_result(config)
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                from .privileges import detect_write_privileges

                privileges = detect_write_privileges(conn, engine)
                return {
                    "success": True,
                    "message": "Connected successfully!",
                    "server_version": (version[:120] + "...") if len(version) > 120 else version,
                    "privileges": privileges,
                }

            if engine == "mysql":
                import pymysql

                conn = create_mysql_connection_from_params(params)
                if provider_password_probe:
                    return self._password_required_result(config)
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                from .privileges import detect_write_privileges

                privileges = detect_write_privileges(conn, engine)
                return {
                    "success": True,
                    "message": "Connected successfully!",
                    "server_version": f"MySQL {version}",
                    "privileges": privileges,
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
            from shared.api.ssh_errors import (
                ssh_error_payload,
                tls_verification_error_payload,
            )
            from shared.ssh_tunnel import SshTunnelError

            if isinstance(e, SshTunnelError):
                payload = ssh_error_payload(
                    e,
                    str(config.get("name") or config.get("host") or "target"),
                    config.get("ssh"),
                )
                return {
                    "success": False,
                    "category": payload["category"],
                    "message": payload["message"],
                }

            tls_payload = tls_verification_error_payload(
                e,
                str(config.get("name") or config.get("host") or "target"),
            )
            if tls_payload:
                return {
                    "success": False,
                    "category": tls_payload["category"],
                    "message": tls_payload["message"],
                }

            error_msg = str(e)
            from features.allowlist.service import (
                connection_failure_category,
                provider_network_hint,
            )

            provider_category = connection_failure_category(config, error_msg)
            provider_network_failure = provider_category is not None
            if provider_password_probe and self._is_authentication_failure(error_msg):
                return self._password_required_result(config)
            if (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                return {
                    "success": False,
                    "message": f"Connection refused: Cannot reach {host}:{port}. "
                    + (
                        provider_network_hint(config)
                        if provider_network_failure
                        else "Check that the database is running and accessible."
                    ),
                    "category": (
                        provider_category if provider_network_failure else None
                    ),
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
                    + (
                        provider_network_hint(config)
                        if provider_network_failure
                        else "Check network connectivity and firewall rules."
                    ),
                    "category": (
                        provider_category if provider_network_failure else None
                    ),
                }
            if provider_network_failure:
                return {
                    "success": False,
                    "category": provider_category,
                    "message": f"Connection failed: {error_msg}. "
                    f"{provider_network_hint(config)}",
                }
            return {
                "success": False,
                "message": f"Connection failed: {error_msg}",
            }
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    @staticmethod
    def _password_required_result(config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": False,
            "code": "TARGET_PASSWORD_REQUIRED",
            "password_env": config.get("password_env", ""),
            "message": "Enter the password for this target again.",
        }

    @staticmethod
    def _is_authentication_failure(message: str) -> bool:
        lowered = message.lower()
        return any(
            marker in lowered
            for marker in (
                "authentication failed",
                "password authentication failed",
                "access denied",
                "invalid password",
                "unknown user",
            )
        )
