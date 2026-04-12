"""ConfigureRenderer - maps ConfigureService events to Rich terminal output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.ui import (
    KeyValueTable,
    MessagePanel,
    StyleTokens,
    TargetsTable,
    get_console,
)

if TYPE_CHECKING:
    from features.configure.events import (
        ConfigureConnectionTestEvent,
        ConfigureErrorEvent,
        ConfigureEvent,
        ConfigureInputNeededEvent,
        ConfigureStatusEvent,
        ConfigureSuccessEvent,
        ConfigureTargetDetailEvent,
        ConfigureTargetListEvent,
    )


class ConfigureRenderer:
    """Renders ConfigureEvent stream to the terminal."""

    def __init__(self):
        self._console = get_console()

    def render(self, event: "ConfigureEvent") -> None:
        from features.configure.events import (
            ConfigureConnectionTestEvent,
            ConfigureErrorEvent,
            ConfigureInputNeededEvent,
            ConfigureStatusEvent,
            ConfigureSuccessEvent,
            ConfigureTargetDetailEvent,
            ConfigureTargetListEvent,
        )

        if isinstance(event, ConfigureStatusEvent):
            self._render_status(event)
        elif isinstance(event, ConfigureTargetListEvent):
            self._render_target_list(event)
        elif isinstance(event, ConfigureTargetDetailEvent):
            self._render_target_detail(event)
        elif isinstance(event, ConfigureConnectionTestEvent):
            self._render_connection_test(event)
        elif isinstance(event, ConfigureSuccessEvent):
            self._render_success(event)
        elif isinstance(event, ConfigureErrorEvent):
            self._render_error(event)
        elif isinstance(event, ConfigureInputNeededEvent):
            self._render_input_needed(event)

    def cleanup(self) -> None:
        pass

    def _render_status(self, event: "ConfigureStatusEvent") -> None:
        self._console.print(f"[dim]{event.message}[/dim]")

    def _render_target_list(self, event: "ConfigureTargetListEvent") -> None:
        if not event.targets:
            self._console.print(
                MessagePanel(
                    "No targets configured yet.",
                    variant="info",
                    hint="Use 'rdst configure add' to add a database target.",
                )
            )
            return

        table = TargetsTable(
            targets=event.targets,
            default_target=event.default_target,
            title="Database Targets",
        )
        self._console.print(table)

    def _render_target_detail(self, event: "ConfigureTargetDetailEvent") -> None:
        details = {
            "Name": event.target_name,
            "Engine": event.engine,
            "Host": event.host,
            "Port": str(event.port),
            "Database": event.database,
            "User": event.user,
            "Password": "Set" if event.has_password else "Not set",
            "TLS": "Enabled" if event.tls else "Disabled",
            "Default": "Yes" if event.is_default else "No",
        }
        table = KeyValueTable(details, title=f"Target: {event.target_name}")
        self._console.print(table)

    def _render_connection_test(self, event: "ConfigureConnectionTestEvent") -> None:
        if event.status == "in_progress":
            self._console.print(
                f"[dim]Testing connection to {event.target_name}...[/dim]"
            )
        elif event.status == "success":
            message = "Connection successful"
            if event.server_version:
                message += f" (Server: {event.server_version})"
            self._console.print(
                MessagePanel(
                    message,
                    variant="success",
                    title=f"Connection Test: {event.target_name}",
                )
            )
        elif event.status == "failed":
            self._console.print(
                MessagePanel(
                    event.message or "Connection failed",
                    variant="error",
                    title=f"Connection Test: {event.target_name}",
                )
            )

    def _render_success(self, event: "ConfigureSuccessEvent") -> None:
        operation_display = {
            "add": "Target added",
            "update": "Target updated",
            "edit": "Target updated",
            "remove": "Target removed",
            "test": "Connection test completed",
            "list": "Targets listed",
            "set_default": "Default target set",
            "default": "Default target set",
        }
        title = operation_display.get(event.operation, "Operation completed")
        message = event.message or title

        self._console.print(
            MessagePanel(
                message,
                variant="success",
                title=title,
            )
        )

    def _render_error(self, event: "ConfigureErrorEvent") -> None:
        title = "Configuration Error"
        if event.operation:
            operation_display = {
                "add": "Failed to add target",
                "update": "Failed to update target",
                "edit": "Failed to update target",
                "remove": "Failed to remove target",
                "test": "Connection test failed",
                "list": "Failed to list targets",
                "set_default": "Failed to set default target",
                "default": "Failed to set default target",
            }
            title = operation_display.get(event.operation, "Configuration Error")

        self._console.print(
            MessagePanel(
                event.message,
                variant="error",
                title=title,
            )
        )

    def _render_input_needed(self, event: "ConfigureInputNeededEvent") -> None:
        self._console.print(f"[{StyleTokens.INFO}]{event.prompt}[/{StyleTokens.INFO}]")
