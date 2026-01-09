"""
RDST Report Command

Allows users to submit feedback about RDST analysis results.
Feedback is sent to PostHog for analytics and Slack for immediate visibility.
"""

import sys
from typing import Optional, Tuple, List

# Import UI system - handles Rich availability internally
from lib.ui import (
    get_console,
    StyleTokens,
    Prompt,
    MessagePanel,
    SelectPrompt,
    SelectionTableBase,
    QueryPanel,
    KeyValueTable,
    Group,
    Text,
    StatusLine,
)


class ReportCommand:
    """Implements the `rdst report` command for user feedback."""

    def __init__(self, console=None):
        self.console = console or get_console()

    def run(
        self,
        query_hash: Optional[str] = None,
        reason: Optional[str] = None,
        email: Optional[str] = None,
        positive: bool = False,
        negative: bool = False,
        include_query: bool = False,
        include_plan: bool = False,
    ) -> bool:
        """
        Run the report command.

        If no arguments provided, runs fully interactive mode.
        Flags can be used for scripting/automation.

        Args:
            query_hash: Hash of the query to report on (optional)
            reason: Feedback reason (if not provided, will prompt)
            email: Email for follow-up (optional)
            positive: Mark as positive feedback
            negative: Mark as negative feedback
            include_query: Include raw SQL in feedback
            include_plan: Include execution plan in feedback

        Returns:
            True if feedback was submitted successfully
        """
        try:
            return self._run_report_flow(
                query_hash=query_hash,
                reason=reason,
                email=email,
                positive=positive,
                negative=negative,
                include_query=include_query,
                include_plan=include_plan,
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print(MessagePanel("Feedback cancelled", variant="warning"))
            return False

    def _run_report_flow(
        self,
        query_hash: Optional[str] = None,
        reason: Optional[str] = None,
        email: Optional[str] = None,
        positive: bool = False,
        negative: bool = False,
        include_query: bool = False,
        include_plan: bool = False,
    ) -> bool:
        """Internal report flow - wrapped by run() for Ctrl-C handling."""
        from lib.telemetry import telemetry

        # Check if we're in fully interactive mode (no args provided)
        fully_interactive = (
            not reason
            and not query_hash
            and not positive
            and not negative
            and sys.stdin.isatty()
        )

        # Determine sentiment from flags
        if positive:
            sentiment = "positive"
        elif negative:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        # Get query context if hash provided via flag
        query_sql = None
        plan_json = None
        suggestion_text = None

        if query_hash:
            query_sql, plan_json, suggestion_text = self._load_query_context(query_hash)
            if not query_sql:
                self.console.print(
                    MessagePanel(
                        f"Could not find query with hash '{query_hash}' in registry",
                        variant="warning",
                    )
                )

        # Fully interactive mode - guide through everything
        if fully_interactive:
            result = self._run_interactive_flow()
            if result is None:
                self.console.print(
                    MessagePanel("Feedback cancelled", variant="warning")
                )
                return False

            query_hash, reason, sentiment, email, include_query, include_plan = result

            # Load query context if hash was selected
            if query_hash:
                query_sql, plan_json, suggestion_text = self._load_query_context(
                    query_hash
                )

        # Partial interactive - just need reason
        elif not reason:
            if not sys.stdin.isatty():
                self.console.print(
                    MessagePanel(
                        "Reason is required when not running interactively. Use --reason 'your feedback'",
                        variant="error",
                    )
                )
                return False

            self._print_header()

            if query_hash:
                self.console.print(
                    StatusLine("Context", f"Providing feedback for query: {query_hash}")
                )

            reason = self._prompt_feedback_text(sentiment)

            if not reason:
                self.console.print(
                    MessagePanel("Feedback cancelled", variant="warning")
                )
                return False

            if not email:
                email = self._prompt_email()

            # Auto-include query/plan if they specified a query hash
            if query_sql and not include_query:
                include_query = True
            if plan_json and not include_plan:
                include_plan = True

        # Submit feedback
        try:
            telemetry.submit_feedback(
                reason=reason,
                query_hash=query_hash,
                query_sql=query_sql,
                plan_json=plan_json,
                suggestion_text=suggestion_text,
                sentiment=sentiment,
                email=email,
                include_query=include_query,
                include_plan=include_plan,
            )

            self.console.print(
                MessagePanel("Thank you for your feedback!", variant="success")
            )

            return True

        except Exception as e:
            self.console.print(MessagePanel(f"Error: {e}", variant="error"))
            return False

    def _run_interactive_flow(self) -> Optional[Tuple]:
        """
        Run the fully interactive feedback flow.

        Returns tuple of (query_hash, reason, sentiment, email, include_query, include_plan)
        or None if cancelled.
        """
        try:
            self._print_header()

            # Step 1: Ask if about a specific query
            choice = SelectPrompt.ask(
                "Is this feedback about a specific query analysis?",
                options=[
                    "Yes, about a specific query",
                    "No, general feedback about RDST",
                ],
                default=2,
                allow_cancel=True,
            )
            if choice is None:
                return None

            query_hash = None
            include_query = False
            include_plan = False

            if choice == 1:
                # Show recent queries and let them pick or enter hash
                query_hash = self._prompt_query_selection()
                if query_hash == "CANCEL":
                    return None  # User wants to cancel entire feedback
                if query_hash:
                    # Load and display query context
                    query_sql, plan_json, _ = self._load_query_context(query_hash)

                    # Show what query they selected (parameterized for privacy)
                    if query_sql:
                        # Truncate for display, show parameterized version
                        display_sql = query_sql[:200]
                        if len(query_sql) > 200:
                            display_sql += "..."

                        self.console.print(
                            QueryPanel(
                                display_sql, title=f"Selected Query ({query_hash[:12]})"
                            )
                        )

                        # Auto-include query since they explicitly selected it
                        include_query = True
                        include_plan = plan_json is not None
                    else:
                        self.console.print(
                            MessagePanel(
                                f"Could not load query details for {query_hash[:12]}",
                                variant="warning",
                            )
                        )

            # Step 2: Ask for sentiment directly
            sentiment_choice = SelectPrompt.ask(
                "How was your experience with RDST?",
                options=["Positive", "Negative", "Neutral"],
                default=3,
                allow_cancel=True,
            )
            if sentiment_choice is None:
                return None

            sentiment_map = {1: "positive", 2: "negative", 3: "neutral"}
            sentiment = sentiment_map[sentiment_choice]

            # Step 3: Get feedback text
            reason = self._prompt_feedback_text(sentiment)
            if not reason:
                return None

            # Step 4: Ask for email
            email = self._prompt_email()

            return (query_hash, reason, sentiment, email, include_query, include_plan)

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

    def _prompt_query_selection(self) -> Optional[str]:
        """Prompt user to select a query or enter a hash."""
        try:
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()

            # Get recent queries (sorted by last_analyzed descending)
            recent = registry.list_queries(limit=10)

            if recent:
                self.console.print(
                    MessagePanel(
                        "Recent queries:", title="Select Query", variant="info"
                    )
                )

                table = SelectionTableBase()

                for i, entry in enumerate(recent, 1):
                    # Show tag if available, otherwise show hash prefix
                    tag_display = entry.tag if entry.tag else "(untagged)"
                    # Truncate query for display - show more context
                    query_preview = entry.sql[:60].replace("\n", " ")
                    if len(entry.sql) > 60:
                        query_preview += "..."

                    label = f"{tag_display} [{StyleTokens.HASH}]({entry.hash[:8]})[/{StyleTokens.HASH}]\n[{StyleTokens.MUTED}]{query_preview}[/{StyleTokens.MUTED}]"
                    table.add_choice(i, label)

                table.add_choice(0, "Enter hash manually")
                table.add_choice("q", "Cancel feedback")
                table.add_choice("Enter", "Skip - general feedback")

                self.console.print(table.table)

                choice = Prompt.ask("\nSelect query", default="", show_default=False)

                # Allow 'q' to cancel entire feedback
                if choice.lower() == "q":
                    return "CANCEL"  # Special sentinel to cancel entire flow

                if not choice:
                    return None  # Skip query selection, continue with general feedback
                elif choice == "0":
                    return Prompt.ask(
                        "Enter query hash", default="", show_default=False
                    )
                elif choice.isdigit() and 1 <= int(choice) <= len(recent):
                    return recent[int(choice) - 1].hash
                else:
                    # Maybe they entered a hash directly
                    return choice
            else:
                self.console.print("\nNo queries found in registry.")
                hash_input = Prompt.ask(
                    "Enter query hash (or press Enter to skip)",
                    default="",
                    show_default=False,
                )
                return hash_input if hash_input else None

        except Exception:
            hash_input = Prompt.ask(
                "Enter query hash (or press Enter to skip)",
                default="",
                show_default=False,
            )
            return hash_input if hash_input else None

    def _prompt_feedback_text(self, sentiment: str) -> Optional[str]:
        """Prompt for feedback text based on sentiment."""
        if sentiment == "positive":
            prompt_text = "What did RDST do well? What was helpful?"
        elif sentiment == "negative":
            prompt_text = "What went wrong? How can we improve?"
        else:
            prompt_text = "Please describe your feedback:"

        self.console.print(f"\n{prompt_text}")
        return self._prompt_multiline()

    def _prompt_multiline(self) -> Optional[str]:
        """Collect multi-line input."""
        self.console.print(
            Text(
                "(Type your message, then press Enter twice to submit)",
                style=StyleTokens.MUTED,
            )
        )
        self.console.print("")

        lines = []
        try:
            while True:
                line = Prompt.ask(">", default="", show_default=False)

                if not line and lines:
                    # Empty line after content = done
                    break
                elif line:
                    lines.append(line)

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

        return "\n".join(lines) if lines else None

    def _load_query_context(self, query_hash: str):
        """Load query context from registry."""
        try:
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()

            # Find the query (supports both exact hash and prefix matching)
            entry = registry.get_query(query_hash)
            if not entry:
                return None, None, None

            query_sql = entry.sql  # Parameterized SQL with ? placeholders

            # Note: Analysis results (suggestions, plans) aren't currently persisted
            # Just return the query SQL for now
            return query_sql, None, None

        except Exception:
            return None, None, None

    def _prompt_email(self) -> Optional[str]:
        """Prompt for optional email."""
        try:
            email = Prompt.ask(
                "\nEmail for follow-up (optional, press Enter to skip)",
                default="",
                show_default=False,
            )

            # Basic validation
            if email and "@" in email and "." in email:
                return email
            elif email:
                self.console.print(
                    MessagePanel("Invalid email format, skipping", variant="warning")
                )
                return None

            return None

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

    def _print_header(self):
        """Print feedback header."""
        self.console.print("")
        self.console.print(
            MessagePanel(
                "[bold]RDST Feedback[/bold]\n\n"
                "Help us improve RDST by sharing your experience.\n"
                "Your feedback goes directly to our team.",
                variant="info",
                title="Report",
            )
        )
