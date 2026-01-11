"""
Agent CLI command implementation.

Handles rdst agent subcommands: create, list, show, delete, chat, serve, mcp, slack.
"""

import sys
from dataclasses import dataclass
from typing import Any

try:
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.table import Table

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


@dataclass
class RdstResult:
    """Result from an rdst command."""

    ok: bool
    message: str = ""
    data: dict | None = None


class AgentCommand:
    """Handles rdst agent subcommands."""

    def __init__(self):
        """Initialize the agent command handler."""
        self._console = Console() if _RICH_AVAILABLE else None
        self._manager = None

    def _get_manager(self):
        """Lazy-load AgentManager."""
        if self._manager is None:
            from ..agent import AgentManager

            self._manager = AgentManager()
        return self._manager

    def execute(
        self,
        subcommand: str | None,
        name: str | None = None,
        target: str | None = None,
        description: str = "",
        max_rows: int = 1000,
        timeout: int = 30,
        port: int = 8080,
        deny_columns: list[str] | None = None,
        allow_tables: list[str] | None = None,
        **kwargs,
    ) -> RdstResult:
        """
        Execute an agent subcommand.

        Args:
            subcommand: The subcommand (create, list, show, delete, chat, serve, mcp, slack).
            name: Agent name.
            target: Database target for create.
            description: Agent description for create.
            max_rows: Max rows for create.
            timeout: Timeout seconds for create.
            port: Port for serve command.
            deny_columns: Columns to deny for create.
            allow_tables: Tables to allow for create.
            **kwargs: Additional arguments.

        Returns:
            RdstResult with success/failure and message.
        """
        if not subcommand:
            return self._help()

        handlers = {
            "create": self._create,
            "list": self._list,
            "show": self._show,
            "delete": self._delete,
            "chat": self._chat,
            "serve": self._serve,
            "mcp": self._mcp,
            "slack": self._slack,
        }

        handler = handlers.get(subcommand)
        if not handler:
            return RdstResult(
                ok=False,
                message=f"Unknown subcommand: {subcommand}. Use: create, list, show, delete, chat, serve, mcp, slack",
            )

        return handler(
            name=name,
            target=target,
            description=description,
            max_rows=max_rows,
            timeout=timeout,
            port=port,
            deny_columns=deny_columns,
            allow_tables=allow_tables,
            **kwargs,
        )

    def _help(self) -> RdstResult:
        """Show help for agent commands."""
        help_text = """
rdst agent - Data Agent Management

Commands:
  create    Create a new data agent
  list      List all agents
  show      Show agent details
  delete    Delete an agent
  chat      Interactive chat with agent
  serve     Start HTTP API server
  mcp       Start MCP server mode
  slack     Start Slack bot mode

Examples:
  rdst agent create --name sales-agent --target prod-db --description "Sales data agent"
  rdst agent list
  rdst agent chat --name sales-agent
  rdst agent serve --name sales-agent --port 8080
"""
        print(help_text)
        return RdstResult(ok=True)

    def _create(
        self,
        name: str | None = None,
        target: str | None = None,
        description: str = "",
        max_rows: int = 1000,
        timeout: int = 30,
        deny_columns: list[str] | None = None,
        allow_tables: list[str] | None = None,
        **kwargs,
    ) -> RdstResult:
        """Create a new agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")
        if not target:
            return RdstResult(ok=False, message="Target is required. Use --target")

        try:
            manager = self._get_manager()
            agent = manager.create(
                name=name,
                target=target,
                description=description,
                max_rows=max_rows,
                timeout_seconds=timeout,
                denied_columns=deny_columns,
                allowed_tables=allow_tables,
            )

            return RdstResult(
                ok=True,
                message=f"Created agent '{name}' targeting '{target}'",
                data=agent.to_dict(),
            )
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _list(self, **kwargs) -> RdstResult:
        """List all agents."""
        manager = self._get_manager()
        names = manager.list()

        if not names:
            return RdstResult(ok=True, message="No agents configured")

        if _RICH_AVAILABLE and self._console:
            table = Table(title="Data Agents")
            table.add_column("Name", style="cyan")
            table.add_column("Target", style="green")
            table.add_column("Description")
            table.add_column("Max Rows", justify="right")
            table.add_column("Timeout", justify="right")

            for name in names:
                try:
                    agent = manager.get(name)
                    table.add_row(
                        agent.name,
                        agent.target,
                        agent.description or "-",
                        str(agent.safety.max_rows),
                        f"{agent.safety.timeout_seconds}s",
                    )
                except Exception:
                    table.add_row(name, "?", "Error loading", "-", "-")

            self._console.print(table)
        else:
            print(f"\nAgents ({len(names)}):")
            for name in names:
                try:
                    agent = manager.get(name)
                    print(f"  {name} -> {agent.target} ({agent.description or 'no description'})")
                except Exception:
                    print(f"  {name} -> (error loading)")

        return RdstResult(ok=True, data={"agents": names})

    def _show(self, name: str | None = None, **kwargs) -> RdstResult:
        """Show agent details."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            if _RICH_AVAILABLE and self._console:
                console = self._console
                console.print(f"\n[bold]Agent: {agent.name}[/bold]\n")
                console.print(f"  Target: [cyan]{agent.target}[/cyan]")
                console.print(f"  Description: {agent.description or '(none)'}")
                console.print(f"  Created: {agent.created_at}")

                console.print("\n[bold]Safety:[/bold]")
                console.print(f"  Read-only: {agent.safety.read_only}")
                console.print(f"  Max rows: {agent.safety.max_rows}")
                console.print(f"  Timeout: {agent.safety.timeout_seconds}s")

                if agent.restrictions.denied_columns:
                    console.print("\n[bold]Denied Columns:[/bold]")
                    for col in agent.restrictions.denied_columns:
                        console.print(f"  - {col}")

                if agent.restrictions.allowed_tables:
                    console.print("\n[bold]Allowed Tables:[/bold]")
                    for tbl in agent.restrictions.allowed_tables:
                        console.print(f"  - {tbl}")

                if agent.restrictions.masked_columns:
                    console.print("\n[bold]Masked Columns:[/bold]")
                    for col, mask in agent.restrictions.masked_columns.items():
                        console.print(f"  - {col} -> {mask}")

                console.print()
            else:
                print(f"\nAgent: {agent.name}")
                print(f"  Target: {agent.target}")
                print(f"  Description: {agent.description or '(none)'}")
                print(f"  Created: {agent.created_at}")
                print(f"  Max rows: {agent.safety.max_rows}")
                print(f"  Timeout: {agent.safety.timeout_seconds}s")

            return RdstResult(ok=True, data=agent.to_dict())
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _delete(self, name: str | None = None, **kwargs) -> RdstResult:
        """Delete an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required")

        manager = self._get_manager()

        if not manager.exists(name):
            return RdstResult(ok=False, message=f"Agent '{name}' not found")

        manager.delete(name)
        return RdstResult(ok=True, message=f"Deleted agent '{name}'")

    def _chat(self, name: str | None = None, **kwargs) -> RdstResult:
        """Interactive chat with an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            from ..agent import AgentRuntime

            runtime = AgentRuntime(agent)

            print(f"\nChat with agent '{name}' (target: {agent.target})")
            print("Type 'exit' or 'quit' to end, 'help' for commands\n")

            while True:
                try:
                    question = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if not question:
                    continue

                if question.lower() in ("exit", "quit", "q"):
                    print("Goodbye!")
                    break

                if question.lower() == "help":
                    print("  exit/quit - End chat")
                    print("  schema - Show database schema")
                    print("  <question> - Ask a question about your data")
                    continue

                if question.lower() == "schema":
                    schema = runtime.get_schema_summary()
                    print(f"\nSchema ({schema.get('source', 'unknown')}):")
                    for table in schema.get("tables", []):
                        print(f"  {table['name']}")
                        if "columns" in table:
                            cols = ", ".join(table["columns"][:5])
                            if len(table["columns"]) > 5:
                                cols += f", ... ({len(table['columns'])} total)"
                            print(f"    Columns: {cols}")
                    print()
                    continue

                # Ask the question
                print("\nThinking...")
                response = runtime.ask(question)

                if response.success:
                    if response.sql:
                        print(f"\nSQL: {response.sql}\n")

                    if response.columns and response.rows:
                        self._print_results(response.columns, response.rows)
                        print(f"\n({response.row_count} rows, {response.execution_time_ms:.1f}ms)")
                        if response.truncated:
                            print("(Results truncated)")
                    elif response.row_count == 0:
                        print("No results found")
                else:
                    print(f"\nError: {response.error}")

                print()

            return RdstResult(ok=True)
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _print_results(self, columns: list[str], rows: list[list[Any]]) -> None:
        """Print query results as a table."""
        if _RICH_AVAILABLE and self._console:
            table = Table()
            for col in columns:
                table.add_column(col)
            for row in rows[:50]:  # Limit display
                table.add_row(*[str(v) if v is not None else "" for v in row])
            self._console.print(table)
        else:
            # Simple text table
            print(" | ".join(columns))
            print("-" * (sum(len(c) for c in columns) + 3 * (len(columns) - 1)))
            for row in rows[:50]:
                print(" | ".join(str(v) if v is not None else "" for v in row))

    def _serve(
        self,
        name: str | None = None,
        port: int = 8080,
        **kwargs,
    ) -> RdstResult:
        """Start HTTP API server for an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            from ..agent.http_server import AgentHTTPServer

            server = AgentHTTPServer(agent)
            print(f"Starting HTTP API server for agent '{name}' on port {port}...")
            print(f"  POST http://localhost:{port}/ask - Ask a question")
            print(f"  GET  http://localhost:{port}/health - Health check")
            print(f"  GET  http://localhost:{port}/schema - Get schema")
            print("\nPress Ctrl+C to stop\n")

            server.run(port=port)
            return RdstResult(ok=True)
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _mcp(self, name: str | None = None, **kwargs) -> RdstResult:
        """Start MCP server mode for an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            # For MCP mode, we need to modify the existing MCP server
            # to use the agent configuration
            print(f"Starting MCP server for agent '{name}'...")
            print("Note: MCP server mode sets the agent as the default target")
            print("Use mcp_server.py with --agent flag instead")

            return RdstResult(
                ok=False,
                message="MCP agent mode not yet implemented. Use 'rdst mcp' with existing target configuration.",
            )
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _slack(self, name: str | None = None, **kwargs) -> RdstResult:
        """Start Slack bot for an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            # Check for Slack credentials
            from ..slack.config import load_credentials

            credentials = load_credentials()
            if not credentials:
                return RdstResult(
                    ok=False,
                    message="No Slack credentials found. Run 'rdst slack setup' first.",
                )

            # Use first workspace
            workspace_id = list(credentials.keys())[0]
            creds = credentials[workspace_id]

            print(f"Starting Slack bot for agent '{name}'...")
            print(f"  Workspace: {creds.workspace_name or workspace_id}")
            print(f"  Target: {agent.target}")
            print("\nPress Ctrl+C to stop\n")

            # Create runtime and start bot
            from ..agent import AgentRuntime
            from ..slack.bot import SlackBot

            runtime = AgentRuntime(agent)

            # Create a wrapper to use AgentRuntime with SlackBot
            # The existing SlackBot expects a different interface,
            # so we need to create an adapter

            # For MVP, we'll create a simple adapter
            class AgentSlackBot:
                """Adapter to run SlackBot with AgentRuntime."""

                def __init__(self, runtime: AgentRuntime, creds):
                    self.runtime = runtime
                    self.creds = creds

                def start(self):
                    from slack_bolt import App
                    from slack_bolt.adapter.socket_mode import SocketModeHandler

                    app = App(token=self.creds.bot_token)

                    @app.event("app_mention")
                    def handle_mention(event, say):
                        text = event.get("text", "")
                        # Remove the bot mention
                        question = text.split(">", 1)[-1].strip() if ">" in text else text

                        if not question:
                            say("Please ask me a question about your data!")
                            return

                        response = self.runtime.ask(question)

                        if response.success:
                            blocks = []

                            if response.sql:
                                blocks.append({
                                    "type": "section",
                                    "text": {"type": "mrkdwn", "text": f"```{response.sql}```"},
                                })

                            if response.columns and response.rows:
                                # Format as simple table
                                header = " | ".join(response.columns)
                                rows = "\n".join(
                                    " | ".join(str(v) for v in row)
                                    for row in response.rows[:10]
                                )
                                table_text = f"```{header}\n{rows}```"

                                if response.row_count > 10:
                                    table_text += f"\n_({response.row_count} total rows)_"

                                blocks.append({
                                    "type": "section",
                                    "text": {"type": "mrkdwn", "text": table_text},
                                })
                            elif response.row_count == 0:
                                blocks.append({
                                    "type": "section",
                                    "text": {"type": "mrkdwn", "text": "_No results found_"},
                                })

                            say(blocks=blocks)
                        else:
                            say(f":x: Error: {response.error}")

                    handler = SocketModeHandler(app, self.creds.app_token)
                    handler.start()

            bot = AgentSlackBot(runtime, creds)
            bot.start()

            return RdstResult(ok=True)
        except ImportError:
            return RdstResult(
                ok=False,
                message="Slack integration requires slack-bolt. Install with: pip install rdst[slack]",
            )
        except Exception as e:
            return RdstResult(ok=False, message=str(e))
