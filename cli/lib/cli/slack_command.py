"""
Slack CLI command implementation.

Handles rdst slack subcommands: setup, start, list, status.
"""

import sys
from dataclasses import dataclass
from typing import Optional

try:
    from rich.console import Console
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


@dataclass
class RdstResult:
    """Result from an rdst command."""

    ok: bool
    message: str = ""
    data: Optional[dict] = None


class SlackCommand:
    """Handles rdst slack subcommands."""

    def __init__(self, client=None):
        """
        Initialize the Slack command handler.

        Args:
            client: Optional CloudAgentClient (not used, for API consistency).
        """
        self.client = client
        self._console = Console() if _RICH_AVAILABLE else None

    def execute(
        self,
        subcommand: str,
        agent: Optional[str] = None,
        **kwargs,
    ) -> RdstResult:
        """
        Execute a slack subcommand.

        Args:
            subcommand: The subcommand (setup, start, list, status).
            agent: Agent name for start/status commands.
            **kwargs: Additional arguments.

        Returns:
            RdstResult with success/failure and message.
        """
        handlers = {
            "setup": self._setup,
            "start": self._start,
            "list": self._list,
            "status": self._status,
        }

        handler = handlers.get(subcommand)
        if not handler:
            return RdstResult(
                ok=False,
                message=f"Unknown subcommand: {subcommand}. Use: setup, start, list, status",
            )

        return handler(agent=agent, **kwargs)

    def _setup(self, **kwargs) -> RdstResult:
        """Run the interactive setup wizard."""
        from ..slack import (
            AgentConfig,
            SlackCredentials,
            generate_manifest,
            get_setup_instructions,
            save_agent_config,
            save_credentials,
        )
        from ..slack.bot import validate_app_token, validate_credentials
        from .rdst_cli import TargetsConfig

        if not _RICH_AVAILABLE:
            return RdstResult(
                ok=False,
                message="Interactive setup requires 'rich' library. Install with: pip install rich",
            )

        console = self._console

        # Print setup instructions
        console.print("\n[bold blue]rdst Slack Bot Setup[/bold blue]\n")
        console.print(get_setup_instructions())

        # Wait for user to complete Slack app creation
        console.print("\n[bold]Press Enter when you've created the Slack app...[/bold]")
        input()

        # Collect tokens
        console.print("\n[bold]Step 2: Enter Your Tokens[/bold]\n")

        bot_token = Prompt.ask("Bot Token (xoxb-...)")
        if not bot_token.startswith("xoxb-"):
            return RdstResult(ok=False, message="Bot token should start with 'xoxb-'")

        app_token = Prompt.ask("App Token (xapp-...)")
        valid, msg = validate_app_token(app_token)
        if not valid:
            return RdstResult(ok=False, message=msg)

        # Validate bot token
        console.print("\n[dim]Validating tokens...[/dim]")
        temp_creds = SlackCredentials(
            workspace_id="temp",
            bot_token=bot_token,
            app_token=app_token,
        )
        valid, msg = validate_credentials(temp_creds)
        if not valid:
            return RdstResult(ok=False, message=f"Token validation failed: {msg}")

        console.print(f"[green]{msg}[/green]\n")

        # Get workspace info
        try:
            from slack_sdk import WebClient

            client = WebClient(token=bot_token)
            auth_info = client.auth_test()
            workspace_id = auth_info["team_id"]
            workspace_name = auth_info["team"]
        except Exception as e:
            return RdstResult(ok=False, message=f"Failed to get workspace info: {e}")

        # Save credentials
        credentials = SlackCredentials(
            workspace_id=workspace_id,
            bot_token=bot_token,
            app_token=app_token,
            workspace_name=workspace_name,
        )
        save_credentials(credentials)
        console.print("[green]Credentials saved![/green]\n")

        # Configure agent
        console.print("[bold]Step 3: Configure Agent[/bold]\n")

        agent_name = Prompt.ask("Agent name", default="data-agent")

        # Get available targets
        config = TargetsConfig()
        config.load()
        targets = config.list_targets()

        if not targets:
            return RdstResult(
                ok=False,
                message="No database targets configured. Run 'rdst configure add' first.",
            )

        console.print(f"\nAvailable targets: {', '.join(targets)}")
        target = Prompt.ask("Database target", choices=targets, default=targets[0])

        description = Prompt.ask(
            "Description (optional)",
            default=f"Data agent for {target}",
        )

        max_rows = int(Prompt.ask("Max rows to return", default="50"))
        timeout = int(Prompt.ask("Query timeout (seconds)", default="30"))

        # Save agent config
        agent_config = AgentConfig(
            name=agent_name,
            target=target,
            workspace_id=workspace_id,
            description=description,
            max_rows=max_rows,
            timeout_seconds=timeout,
        )
        save_agent_config(agent_config)

        console.print(f"\n[green]Agent '{agent_name}' configured![/green]\n")

        console.print(
            "[bold yellow]Note:[/bold yellow] Anyone with access to your Slack channel "
            "will be able to query your database through RDST. "
            "Use [cyan]rdst guard[/cyan] to restrict access to sensitive data.\n"
        )

        console.print("[bold]Next Steps[/bold]\n")
        console.print(f"1. Start your bot:\n   [cyan]rdst slack start --agent {agent_name}[/cyan]\n")
        console.print("2. Add the bot to a Slack channel:")
        console.print("   - In the channel, type: [cyan]/invite @data-agent[/cyan]")
        console.print("   - Or: Click channel name > Integrations > Add apps\n")
        console.print("3. Query the bot:")
        console.print("   - In channel: [cyan]@data-agent how many users do we have?[/cyan]")
        console.print("   - Or DM the bot directly (no @mention needed)\n")

        return RdstResult(
            ok=True,
            message=f"Slack bot '{agent_name}' configured successfully",
            data={"agent": agent_name, "workspace": workspace_name},
        )

    def _start(self, agent: Optional[str] = None, **kwargs) -> RdstResult:
        """Start a Slack bot for an agent."""
        from ..slack import list_agents, load_agent_config, load_credentials
        from ..slack.bot import SlackBot

        if not agent:
            # List available agents
            agents = list_agents()
            if not agents:
                return RdstResult(
                    ok=False,
                    message="No agents configured. Run 'rdst slack setup' first.",
                )
            agent_names = [a.name for a in agents]
            return RdstResult(
                ok=False,
                message=f"Please specify an agent: --agent <name>\nAvailable: {', '.join(agent_names)}",
            )

        # Load agent config
        agent_config = load_agent_config(agent)
        if not agent_config:
            return RdstResult(
                ok=False,
                message=f"Agent '{agent}' not found. Run 'rdst slack list' to see available agents.",
            )

        # Load credentials
        all_creds = load_credentials(agent_config.workspace_id)
        if not all_creds:
            return RdstResult(
                ok=False,
                message=f"No credentials for workspace '{agent_config.workspace_id}'. Run 'rdst slack setup'.",
            )

        credentials = all_creds.get(agent_config.workspace_id)
        if not credentials:
            return RdstResult(
                ok=False,
                message=f"Credentials not found for workspace. Run 'rdst slack setup'.",
            )

        # Print startup info
        if self._console:
            self._console.print(f"\n[bold]Starting Slack bot '{agent}'[/bold]")
            self._console.print(f"  Workspace: {credentials.workspace_name or credentials.workspace_id}")
            self._console.print(f"  Target: {agent_config.target}")
            self._console.print(f"  Max rows: {agent_config.max_rows}")
            self._console.print(f"  Timeout: {agent_config.timeout_seconds}s")
            self._console.print(
                "\n[bold yellow]Note:[/bold yellow] Anyone with access to your Slack channel "
                "can query your database through RDST. "
                "Use [cyan]rdst guard[/cyan] to restrict access to sensitive data."
            )
            self._console.print("\n[dim]Press Ctrl+C to stop[/dim]\n")
        else:
            print(f"\nStarting Slack bot '{agent}'")
            print(f"  Workspace: {credentials.workspace_name or credentials.workspace_id}")
            print(f"  Target: {agent_config.target}")
            print("\nPress Ctrl+C to stop\n")

        # Create and start bot
        try:
            bot = SlackBot(agent_config, credentials)
            bot.start()  # Blocks until stopped
            return RdstResult(ok=True, message="Bot stopped")
        except ImportError as e:
            return RdstResult(ok=False, message=str(e))
        except Exception as e:
            return RdstResult(ok=False, message=f"Error starting bot: {e}")

    def _list(self, **kwargs) -> RdstResult:
        """List configured agents."""
        from ..slack import list_agents, load_credentials

        agents = list_agents()

        if not agents:
            return RdstResult(
                ok=True,
                message="No agents configured. Run 'rdst slack setup' to create one.",
            )

        # Get all credentials for workspace names
        all_creds = load_credentials()

        if self._console and _RICH_AVAILABLE:
            table = Table(title="Slack Agents")
            table.add_column("Name", style="cyan")
            table.add_column("Target", style="green")
            table.add_column("Workspace")
            table.add_column("Max Rows")
            table.add_column("Timeout")

            for agent in agents:
                workspace_name = agent.workspace_id
                if agent.workspace_id in all_creds:
                    workspace_name = all_creds[agent.workspace_id].workspace_name or agent.workspace_id

                table.add_row(
                    agent.name,
                    agent.target,
                    workspace_name,
                    str(agent.max_rows),
                    f"{agent.timeout_seconds}s",
                )

            self._console.print(table)
        else:
            print("\nSlack Agents:")
            for agent in agents:
                print(f"  {agent.name} -> {agent.target}")

        return RdstResult(
            ok=True,
            message=f"Found {len(agents)} agent(s)",
            data={"agents": [a.name for a in agents]},
        )

    def _status(self, agent: Optional[str] = None, **kwargs) -> RdstResult:
        """Show status of an agent."""
        from ..slack import load_agent_config, load_credentials

        if not agent:
            return self._list(**kwargs)

        agent_config = load_agent_config(agent)
        if not agent_config:
            return RdstResult(
                ok=False,
                message=f"Agent '{agent}' not found.",
            )

        # Get workspace info
        all_creds = load_credentials(agent_config.workspace_id)
        workspace_name = agent_config.workspace_id
        if agent_config.workspace_id in all_creds:
            workspace_name = all_creds[agent_config.workspace_id].workspace_name or workspace_name

        console = self._console
        console.print(f"\n[bold]Agent: {agent}[/bold]")
        console.print(f"  Description: {agent_config.description}")
        console.print(f"  Target: [{StyleTokens.SUCCESS}]{agent_config.target}[/{StyleTokens.SUCCESS}]")
        console.print(f"  Workspace: {workspace_name}")
        console.print(f"  Max rows: {agent_config.max_rows}")
        console.print(f"  Timeout: {agent_config.timeout_seconds}s")
        console.print(f"\n  Start with: [{StyleTokens.ACCENT}]rdst slack start --agent {agent}[/{StyleTokens.ACCENT}]")

        return RdstResult(
            ok=True,
            message=f"Agent '{agent}' status",
            data=agent_config.to_dict(),
        )
