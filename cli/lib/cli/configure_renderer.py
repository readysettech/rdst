"""
ConfigureRenderer - Maps ConfigureService events to Rich terminal output.

Pure rendering, no business logic. Consumes ConfigureEvent stream and
displays appropriate output for each event type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from lib.ui import (
    get_console,
    MessagePanel,
    TargetsTable,
    KeyValueTable,
    StatusLine,
    StyleTokens,
)

if TYPE_CHECKING:
    from lib.services.types import (
        ConfigureEvent,
        ConfigureStatusEvent,
        ConfigureTargetListEvent,
        ConfigureTargetDetailEvent,
        ConfigureConnectionTestEvent,
        ConfigureSuccessEvent,
        ConfigureErrorEvent,
        ConfigureInputNeededEvent,
    )


class ConfigureRenderer:
    """
    Renders ConfigureEvent stream to terminal using Rich.

    Handles status messages, target lists, connection test results,
    and error display.

    Usage:
        renderer = ConfigureRenderer()
        async for event in service.list_targets():
            renderer.render(event)
        renderer.cleanup()
    """

    def __init__(self):
        self._console = get_console()

    def render(self, event: "ConfigureEvent") -> None:
        """Render an event to the terminal."""
        from lib.services.types import (
            ConfigureStatusEvent,
            ConfigureTargetListEvent,
            ConfigureTargetDetailEvent,
            ConfigureConnectionTestEvent,
            ConfigureSuccessEvent,
            ConfigureErrorEvent,
            ConfigureInputNeededEvent,
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
        """Cleanup any active displays. Call when done processing events."""
        pass

    def _render_status(self, event: "ConfigureStatusEvent") -> None:
        """Render status message."""
        self._console.print(f"[dim]{event.message}[/dim]")

    def _render_target_list(self, event: "ConfigureTargetListEvent") -> None:
        """Render list of targets."""
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
        """Render details of a single target."""
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
        """Render connection test result."""
        if event.status == "in_progress":
            self._console.print(
                f"[dim]Testing connection to {event.target_name}...[/dim]"
            )
        elif event.status == "success":
            message = f"Connection successful"
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
        """Render success message."""
        operation_display = {
            "add": "Target added",
            "edit": "Target updated",
            "remove": "Target removed",
            "test": "Connection test completed",
            "list": "Targets listed",
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
        """Render error message."""
        title = "Configuration Error"
        if event.operation:
            operation_display = {
                "add": "Failed to add target",
                "edit": "Failed to update target",
                "remove": "Failed to remove target",
                "test": "Connection test failed",
                "list": "Failed to list targets",
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
        """Render input prompt (informational - actual input handled by CLI)."""
        # This is informational - the actual input collection is handled
        # by the CLI command's input handler
        self._console.print(f"[{StyleTokens.INFO}]{event.prompt}[/{StyleTokens.INFO}]")
