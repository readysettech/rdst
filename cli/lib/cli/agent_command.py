"""
Agent CLI command implementation.

Handles rdst agent subcommands: create, list, show, delete, chat, serve, mcp, slack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import random
import sys
import threading
import time as _time

from lib.ui import DataTableBase, Prompt, StyleTokens, get_console


_CHAT_VERBS = [
    "Scanning tables", "Reading schema", "Exploring data",
    "Crunching rows", "Joining tables", "Filtering results",
    "Aggregating", "Indexing thoughts", "Querying",
    "Analyzing patterns", "Cross-referencing", "Correlating",
    "Pivoting", "Sorting insights", "Building query plan",
]


class _ChatSpinner:
    """Thread-safe spinner with rotating verbs for chat agent wait states."""

    def __init__(self, console):
        self._console = console
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._status = None
        self._thread = None

    def start(self) -> None:
        with self._lock:
            if self._status is not None:
                return
            from lib.ui import Status
            self._stop_event.clear()
            self._status = Status(
                random.choice(_CHAT_VERBS) + "...",
                spinner="dots",
                console=self._console,
            )
            self._status.start()
            self._thread = threading.Thread(target=self._rotate, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            if self._status is not None:
                self._status.stop()
                self._status = None

    def _rotate(self) -> None:
        while not self._stop_event.wait(3):
            with self._lock:
                if self._status is not None:
                    self._status.update(random.choice(_CHAT_VERBS) + "...")


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
        self._console = get_console()
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
        guard: str | None = None,
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
            guard: Guard name to apply to agent.
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
            guard=guard,
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
        guard: str | None = None,
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
                guard=guard,
            )

            msg = f"Created agent '{name}' targeting '{target}'"
            if guard:
                msg += f" with guard '{guard}'"

            return RdstResult(
                ok=True,
                message=msg,
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

        table = DataTableBase(title="Data Agents")
        table.add_column("Name", style=StyleTokens.ACCENT)
        table.add_column("Target", style=StyleTokens.SUCCESS)
        table.add_column("Guard", style=StyleTokens.WARNING)
        table.add_column("Description")
        table.add_column("Max Rows", justify="right")

        for name in names:
            try:
                agent = manager.get(name)
                table.add_row(
                    agent.name,
                    agent.target,
                    agent.guard or "-",
                    agent.description or "-",
                    str(agent.safety.max_rows),
                )
            except Exception:
                table.add_row(name, "?", "-", "Error loading", "-")

        self._console.print(table)

        return RdstResult(ok=True)

    def _show(self, name: str | None = None, **kwargs) -> RdstResult:
        """Show agent details."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            console = self._console
            console.print(f"\n[bold]Agent: {agent.name}[/bold]\n")
            console.print(f"  Target: [{StyleTokens.ACCENT}]{agent.target}[/{StyleTokens.ACCENT}]")
            console.print(f"  Description: {agent.description or '(none)'}")
            console.print(f"  Created: {agent.created_at}")

            if agent.guard:
                console.print(f"\n[bold]Guard:[/bold] [{StyleTokens.WARNING}]{agent.guard}[/{StyleTokens.WARNING}]")
                # Show guard details
                try:
                    from ..guard import GuardManager
                    guard_mgr = GuardManager()
                    guard = guard_mgr.get(agent.guard)
                    if guard.masking.patterns:
                        console.print(f"  Masks: {len(guard.masking.patterns)} pattern(s)")
                    if guard.guards.require_where:
                        console.print("  Requires: WHERE clause")
                    if guard.guards.require_limit:
                        console.print("  Requires: LIMIT clause")
                except Exception:
                    console.print("  (guard details unavailable)")
            else:
                console.print("\n[bold]Safety (inline):[/bold]")
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

    def _chat(self, name: str | None = None, timeout: int = 600, **kwargs) -> RdstResult:
        """Interactive chat with an agent."""
        if not name:
            return RdstResult(ok=False, message="Agent name is required. Use --name")

        try:
            manager = self._get_manager()
            agent = manager.get(name)

            # Use CLI timeout (overrides agent's saved config for this session)
            agent.safety.timeout_seconds = timeout

            from ..agent.chat_agent import ChatAgent
            from ..agent.chat_tools import format_tool_result_for_display

            chat_agent = ChatAgent(agent)

            timeout_display = f"{timeout}s" if timeout < 60 else f"{timeout // 60}m"
            print(f"\nChat with agent '{name}' (target: {agent.target}, timeout: {timeout_display})")
            print("Type 'exit' to end, 'clear' to reset history, 'help' for commands\n")

            # One-time hint if semantic layer is missing
            schema_summary = chat_agent.get_schema_summary()
            hint = schema_summary.get("schema_hint") if isinstance(schema_summary, dict) else None
            if hint:
                self._console.print(f"[{StyleTokens.MUTED}]{hint}[/{StyleTokens.MUTED}]\n")

            # Track queries saved during this session (hash -> tag, deduplicates)
            saved_queries: dict[str, str] = {}

            while True:
                try:
                    question = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not question:
                    continue

                if question.lower() in ("exit", "quit", "q"):
                    break

                if question.lower() == "help":
                    print("  exit/quit - End chat")
                    print("  clear     - Clear conversation history")
                    print("  schema    - Show database schema")
                    print("  history   - Show conversation history")
                    print("  <question> - Ask a question about your data")
                    continue

                if question.lower() == "clear":
                    chat_agent.clear_history()
                    print("Conversation history cleared.\n")
                    continue

                if question.lower() == "history":
                    history = chat_agent.get_history_summary()
                    if not history:
                        print("No conversation history.\n")
                    else:
                        print(f"\nConversation history ({len(history)} messages):")
                        for i, msg in enumerate(history, 1):
                            print(f"  {i}. [{msg['role']}] {msg['summary']}")
                        print()
                    continue

                if question.lower() == "schema":
                    schema = chat_agent.get_schema_summary()
                    print(f"\nSchema ({schema.get('source', 'unknown')}):")
                    for table in schema.get("tables", []):
                        print(f"  {table['name']}")
                        if "columns" in table:
                            cols = table["columns"]
                            if isinstance(cols, list):
                                col_str = ", ".join(cols[:5])
                                if len(cols) > 5:
                                    col_str += f", ... ({len(cols)} total)"
                                print(f"    Columns: {col_str}")
                    print()
                    continue

                # Chat with the agent, streaming progress
                console = self._console

                verbose = kwargs.get("verbose", False)

                def _on_chat_event(event: str, data: dict) -> None:
                    if event == "thinking":
                        text = data["text"].strip()
                        if text:
                            console.print(f"\n[{StyleTokens.MUTED}]{text}[/{StyleTokens.MUTED}]")

                    elif event == "tool_call":
                        name = data["name"]
                        if name == "query_database":
                            q = data["input"].get("question", "")
                            console.print(f"\n[{StyleTokens.EMPHASIS}]> {q}[/{StyleTokens.EMPHASIS}]")
                        elif name == "get_schema":
                            table = data["input"].get("table_name")
                            label = f"schema: {table}" if table else "schema: all tables"
                            console.print(f"\n[{StyleTokens.EMPHASIS}]> {label}[/{StyleTokens.EMPHASIS}]")

                    elif event == "tool_result":
                        result = data["result"]
                        if not result.success:
                            console.print(f"  [{StyleTokens.MUTED}]Query failed, retrying...[/{StyleTokens.MUTED}]")
                            return

                        if result.data and result.data.get("columns") and result.data.get("rows"):
                            row_count = result.data.get("row_count", len(result.data["rows"]))
                            exec_time = result.data.get("execution_time_ms", 0)
                            cols = len(result.data["columns"])

                            if verbose:
                                # Verbose mode: show SQL + full results
                                if result.data.get("sql"):
                                    console.print(f"  SQL: {result.data['sql']}")
                                self._print_results(result.data["columns"], result.data["rows"])
                                console.print(f"  ({row_count:,} rows, {cols} columns, {exec_time:.0f}ms)")
                            else:
                                # Compact mode: summary + small tables only
                                console.print(f"  [{StyleTokens.MUTED}]{row_count:,} rows, {cols} columns ({exec_time:.0f}ms)[/{StyleTokens.MUTED}]")
                                if row_count <= 20:
                                    self._print_results(result.data["columns"], result.data["rows"])

                            if result.data.get("truncated"):
                                console.print(f"  [{StyleTokens.MUTED}](results truncated)[/{StyleTokens.MUTED}]")

                            # Breadcrumb: show query hash for later access
                            qhash = result.data.get("query_hash")
                            qtag = result.data.get("query_tag")
                            if qhash:
                                console.print(f"  [{StyleTokens.MUTED}]saved as {qhash[:8]} — rdst analyze --hash {qhash[:8]}[/{StyleTokens.MUTED}]")
                                saved_queries[qhash[:8]] = qtag or qhash[:8]

                spinner = _ChatSpinner(console) if sys.stdout.isatty() else None

                def _on_event_with_spinner(event: str, data: dict) -> None:
                    if event == "llm_call":
                        if spinner:
                            spinner.start()
                        return
                    if event == "tool_call":
                        if spinner:
                            spinner.stop()
                        _on_chat_event(event, data)
                        if spinner:
                            spinner.start()
                        return
                    if spinner:
                        spinner.stop()
                    _on_chat_event(event, data)

                if spinner:
                    spinner.start()
                else:
                    console.print(f"[{StyleTokens.MUTED}]Thinking...[/{StyleTokens.MUTED}]")

                try:
                    response = chat_agent.chat(question, on_event=_on_event_with_spinner)
                except KeyboardInterrupt:
                    if spinner:
                        spinner.stop()
                    print("\n\nInterrupted. Type 'exit' to quit or ask another question.\n")
                    continue
                finally:
                    if spinner:
                        spinner.stop()

                # Display the agent's final response text with rich markdown
                if response.text:
                    from lib.ui import MarkdownContent
                    console.print()
                    console.print(MarkdownContent(response.text))

                print()

            # Session summary: show saved queries
            if saved_queries:
                console.print(f"\n[{StyleTokens.EMPHASIS}]Saved {len(saved_queries)} queries this session:[/{StyleTokens.EMPHASIS}]")
                for qhash, tag in saved_queries.items():
                    console.print(f"  [{StyleTokens.MUTED}]{tag:<45} {qhash}[/{StyleTokens.MUTED}]")
                console.print(f"\n[{StyleTokens.MUTED}]Run 'rdst analyze --hash <hash> --target {agent.target}' to analyze any query.[/{StyleTokens.MUTED}]")

            print("Goodbye!")
            return RdstResult(ok=True)
        except Exception as e:
            return RdstResult(ok=False, message=str(e))

    def _print_results(self, columns: list[str], rows: list[list[Any]]) -> None:
        """Print query results as a table."""
        table = DataTableBase()
        for col in columns:
            table.add_column(col)
        for row in rows[:50]:  # Limit display
            table.add_row(*[str(v) if v is not None else "" for v in row])
        self._console.print(table)

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
        """Start Slack bot for an agent (conversational mode)."""
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

            print(f"Starting conversational Slack bot for agent '{name}'...")
            print(f"  Workspace: {creds.workspace_name or workspace_id}")
            print(f"  Target: {agent.target}")
            print("  Mode: Conversational (ChatAgent)")
            print("\nPress Ctrl+C to stop\n")

            # Use ChatAgent for conversational responses
            from ..agent.chat_agent import ChatAgent

            # Create a conversational Slack bot
            class ConversationalSlackBot:
                """Slack bot using ChatAgent for conversational responses."""

                def __init__(self, agent_config, creds):
                    self.agent_config = agent_config
                    self.creds = creds
                    # Track conversations by thread (thread_ts or channel+user for DMs)
                    self.conversations: dict[str, ChatAgent] = {}

                def _get_conversation(self, thread_key: str) -> ChatAgent:
                    """Get or create a ChatAgent for a conversation thread."""
                    if thread_key not in self.conversations:
                        self.conversations[thread_key] = ChatAgent(self.agent_config)
                    return self.conversations[thread_key]

                def _format_response(self, response) -> list[dict]:
                    """Format ChatAgent response as Slack blocks."""
                    blocks = []

                    # Show tool results (SQL and data)
                    for result in response.tool_results:
                        if result.data and result.data.get("sql"):
                            blocks.append({
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"```{result.data['sql']}```"},
                            })

                        if result.data and result.data.get("columns") and result.data.get("rows"):
                            columns = result.data["columns"]
                            rows = result.data["rows"][:10]
                            row_count = result.data.get("row_count", len(rows))

                            header = " | ".join(str(c) for c in columns)
                            row_strs = "\n".join(
                                " | ".join(str(v) if v is not None else "NULL" for v in row)
                                for row in rows
                            )
                            table_text = f"```{header}\n{row_strs}```"

                            if row_count > 10:
                                table_text += f"\n_({row_count} total rows)_"

                            blocks.append({
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": table_text},
                            })

                    # Show agent's text response
                    if response.text:
                        # Truncate if too long for Slack
                        text = response.text
                        if len(text) > 2900:
                            text = text[:2900] + "..."

                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": text},
                        })

                    return blocks if blocks else [{"type": "section", "text": {"type": "mrkdwn", "text": "_No response_"}}]

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

                        # Use thread_ts for conversation continuity
                        thread_ts = event.get("thread_ts") or event.get("ts")
                        channel = event.get("channel")
                        thread_key = f"{channel}:{thread_ts}"

                        chat_agent = self._get_conversation(thread_key)

                        try:
                            response = chat_agent.chat(question)
                            blocks = self._format_response(response)
                            say(blocks=blocks, thread_ts=thread_ts)
                        except Exception as e:
                            say(f":x: Error: {e}", thread_ts=thread_ts)

                    @app.event("message")
                    def handle_dm(event, say):
                        # Handle DMs (no mention needed)
                        channel_type = event.get("channel_type")
                        if channel_type != "im":
                            return  # Only handle DMs here

                        # Ignore bot's own messages
                        if event.get("bot_id"):
                            return

                        text = event.get("text", "").strip()
                        if not text:
                            return

                        # Use channel + user as conversation key for DMs
                        channel = event.get("channel")
                        user = event.get("user")
                        thread_key = f"dm:{channel}:{user}"

                        chat_agent = self._get_conversation(thread_key)

                        try:
                            response = chat_agent.chat(text)
                            blocks = self._format_response(response)
                            say(blocks=blocks)
                        except Exception as e:
                            say(f":x: Error: {e}")

                    handler = SocketModeHandler(app, self.creds.app_token)
                    handler.start()

            bot = ConversationalSlackBot(agent, creds)
            bot.start()

            return RdstResult(ok=True)
        except ImportError:
            return RdstResult(
                ok=False,
                message="Slack integration requires slack-bolt. Install with: pip install rdst[slack]",
            )
        except Exception as e:
            return RdstResult(ok=False, message=str(e))
