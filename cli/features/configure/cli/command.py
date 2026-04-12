"""Configure command orchestration."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from shared.cli.types import RdstResult
from shared.config.targets import TargetsConfig
from shared.ui import get_console

from ..adapters import cli_subcommand_to_operation
from ..models import ConfigureInput, ConfigureOptions
from ..service import ConfigureService
from .renderer import ConfigureRenderer
from .wizard import ConfigurationWizard


class ConfigureCommand:
    """Owns non-LLM configure command execution."""

    def __init__(self, client: Any | None = None):
        self.client = client

    def execute(self, config_path: Optional[str] = None, **kwargs) -> RdstResult:
        try:
            if config_path and self.client is not None:
                cm = self.client.configuration_manager()
                cm.load_from_json_config(config_path)
                self.client.print_panel(
                    "configure", f"Loaded agent config from {config_path}"
                )

            subcmd = (kwargs.get("subcommand") or "menu").lower()
            valid_subcommands = {
                "add",
                "edit",
                "list",
                "remove",
                "default",
                "menu",
                "test",
            }
            if subcmd not in valid_subcommands:
                return RdstResult(False, f"Unknown subcommand: {subcmd}")

            cfg = TargetsConfig()
            cfg.load()

            console = getattr(self.client, "_console", None) if self.client else None

            if subcmd == "menu":
                wizard = ConfigurationWizard(console=console or get_console())
                return wizard.configure_targets(subcmd, cfg, **kwargs)

            service = ConfigureService()
            renderer = ConfigureRenderer()

            result = None
            error = None

            async def _run():
                nonlocal result, error
                try:
                    if subcmd == "list":
                        async for event in service.list_targets(
                            ConfigureInput(),
                            ConfigureOptions(operation=cli_subcommand_to_operation(subcmd)),
                        ):
                            renderer.render(event)
                            if event.type == "target_list":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "test":
                        target_name = kwargs.get("target") or kwargs.get("name")
                        if not target_name:
                            target_name = cfg.get_default()
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {
                                    "message": "No target specified and no default target configured"
                                },
                            )()
                            return
                        async for event in service.test_connection(target_name):
                            renderer.render(event)
                            if (
                                event.type == "connection_test"
                                and event.status == "success"
                            ):
                                result = event
                            elif event.type == "error":
                                error = event
                            elif (
                                event.type == "connection_test"
                                and event.status == "failed"
                            ):
                                error = event

                    elif subcmd == "add":
                        target_name = kwargs.get("name")
                        if not target_name:
                            return
                        target_data = {
                            "engine": kwargs.get("engine", "postgresql"),
                            "host": kwargs.get("host", "localhost"),
                            "port": kwargs.get("port", 5432),
                            "database": kwargs.get("database", "postgres"),
                            "user": kwargs.get("user", "postgres"),
                            "password_env": kwargs.get("password_env", ""),
                            "tls": kwargs.get("tls", False),
                        }
                        async for event in service.add_target(
                            ConfigureInput(target_name=target_name),
                            ConfigureOptions(
                                operation=cli_subcommand_to_operation(subcmd),
                                target_data=target_data,
                            ),
                        ):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "edit":
                        target_name = kwargs.get("name")
                        if not target_name:
                            return
                        target_data = {}
                        for key in [
                            "engine",
                            "host",
                            "port",
                            "database",
                            "user",
                            "password_env",
                            "tls",
                        ]:
                            if kwargs.get(key) is not None:
                                target_data[key] = kwargs.get(key)
                        async for event in service.update_target(
                            target_name,
                            ConfigureInput(target_name=target_name),
                            ConfigureOptions(
                                operation=cli_subcommand_to_operation(subcmd),
                                target_data=target_data,
                            ),
                        ):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "remove":
                        target_name = kwargs.get("name")
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {"message": "Target name is required for remove"},
                            )()
                            return
                        async for event in service.remove_target(target_name):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event

                    elif subcmd == "default":
                        target_name = kwargs.get("name")
                        if not target_name:
                            error = type(
                                "ErrorEvent",
                                (),
                                {"message": "Target name is required for set-default"},
                            )()
                            return
                        async for event in service.set_default(target_name):
                            renderer.render(event)
                            if event.type == "success":
                                result = event
                            elif event.type == "error":
                                error = event
                finally:
                    renderer.cleanup()

            asyncio.run(_run())

            if result is None and error is None and subcmd in ("add", "edit"):
                wizard = ConfigurationWizard(console=console or get_console())
                return wizard.configure_targets(subcmd, cfg, **kwargs)

            if result:
                return RdstResult(True, "Operation completed successfully")
            if error:
                return RdstResult(False, getattr(error, "message", str(error)))
            return RdstResult(True, "Operation completed")
        except Exception as e:
            return RdstResult(False, f"configure failed: {e}")
