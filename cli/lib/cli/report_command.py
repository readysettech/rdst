"""
RDST Report Command

Allows users to submit feedback about RDST analysis results.
Feedback is sent to PostHog for analytics and Slack for immediate visibility.
"""

import os
import sys
from typing import Optional, Tuple
from pathlib import Path

try:
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from rich.panel import Panel
    from rich.text import Text
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


class ReportCommand:
    """Implements the `rdst report` command for user feedback."""

    def __init__(self, console=None):
        self.console = console if console and _RICH_AVAILABLE else (Console() if _RICH_AVAILABLE else None)
        self._has_rich = _RICH_AVAILABLE and self.console is not None

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
            print("\n")  # Clean line after ^C or ^D
            self._print_info("Feedback cancelled")
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
            not reason and
            not query_hash and
            not positive and
            not negative and
            sys.stdin.isatty()
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
                self._print_warning(f"Could not find query with hash '{query_hash}' in registry")

        # Fully interactive mode - guide through everything
        if fully_interactive:
            result = self._run_interactive_flow()
            if result is None:
                self._print_info("\nFeedback cancelled")
                return False

            query_hash, reason, sentiment, email, include_query, include_plan = result

            # Load query context if hash was selected
            if query_hash:
                query_sql, plan_json, suggestion_text = self._load_query_context(query_hash)

        # Partial interactive - just need reason
        elif not reason:
            if not sys.stdin.isatty():
                self._print_error("Reason is required when not running interactively. Use --reason 'your feedback'")
                return False

            self._print_header()

            if query_hash:
                self._print_info(f"Providing feedback for query: {query_hash}")

            reason = self._prompt_feedback_text(sentiment)

            if not reason:
                self._print_info("Feedback cancelled")
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

            self._print_success("Thank you for your feedback!")

            return True

        except Exception as e:
            self._print_error(f"Failed to submit feedback: {e}")
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
            self._print("\nIs this feedback about a specific query analysis?")
            self._print("  [1] Yes, about a specific query")
            self._print("  [2] No, general feedback about RDST")

            choice = self._prompt_choice(["1", "2"], default="2")
            if choice is None:
                return None

            query_hash = None
            include_query = False
            include_plan = False

            if choice == "1":
                # Show recent queries and let them pick or enter hash
                query_hash = self._prompt_query_selection()
                if query_hash == "CANCEL":
                    return None  # User wants to cancel entire feedback
                if query_hash:
                    # Load and display query context
                    query_sql, plan_json, _ = self._load_query_context(query_hash)

                    # Show what query they selected (parameterized for privacy)
                    if query_sql:
                        self._print("")
                        if self._has_rich:
                            self.console.print(f"[bold]Selected query:[/bold] [yellow]{query_hash[:12]}[/yellow]")
                            # Truncate for display, show parameterized version
                            display_sql = query_sql[:200].replace('\n', ' ')
                            if len(query_sql) > 200:
                                display_sql += "..."
                            self.console.print(f"[dim]{display_sql}[/dim]")
                        else:
                            self._print(f"Selected query: {query_hash[:12]}")
                            display_sql = query_sql[:200].replace('\n', ' ')
                            if len(query_sql) > 200:
                                display_sql += "..."
                            self._print(f"  {display_sql}")

                        # Auto-include query since they explicitly selected it
                        include_query = True
                        include_plan = plan_json is not None
                    else:
                        self._print_warning(f"Could not load query details for {query_hash[:12]}")

            # Step 2: Ask for sentiment directly
            self._print("\nHow was your experience with RDST?")
            self._print("  [1] Positive")
            self._print("  [2] Negative")
            self._print("  [3] Neutral")

            sentiment_choice = self._prompt_choice(["1", "2", "3"], default="3")
            if sentiment_choice is None:
                return None

            sentiment_map = {
                "1": "positive",
                "2": "negative",
                "3": "neutral",
            }
            sentiment = sentiment_map[sentiment_choice]

            # Step 3: Get feedback text
            reason = self._prompt_input("\nPlease share your feedback: ")
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
                self._print("\nRecent queries:")
                self._print("")
                for i, entry in enumerate(recent, 1):
                    # Show tag if available, otherwise show hash prefix
                    tag_display = entry.tag if entry.tag else "(untagged)"
                    # Truncate query for display - show more context
                    query_preview = entry.sql[:60].replace('\n', ' ')
                    if len(entry.sql) > 60:
                        query_preview += "..."

                    if self._has_rich:
                        self.console.print(f"  [cyan][{i:2}][/cyan] {tag_display} [yellow]({entry.hash[:8]})[/yellow]")
                        self.console.print(f"       [dim]{query_preview}[/dim]")
                    else:
                        self._print(f"  [{i:2}] {tag_display} ({entry.hash[:8]})")
                        self._print(f"       {query_preview}")

                self._print("")
                self._print("  [0]  Enter hash manually")
                self._print("  [q]  Cancel feedback")
                self._print("  [Enter] Skip - general feedback")

                choice = self._prompt_input("\nSelect query: ", default="")

                # Allow 'q' to cancel entire feedback
                if choice.lower() == 'q':
                    return "CANCEL"  # Special sentinel to cancel entire flow

                if not choice:
                    return None  # Skip query selection, continue with general feedback
                elif choice == "0":
                    return self._prompt_input("Enter query hash: ")
                elif choice.isdigit() and 1 <= int(choice) <= len(recent):
                    return recent[int(choice) - 1].hash
                else:
                    # Maybe they entered a hash directly
                    return choice
            else:
                self._print("\nNo queries found in registry.")
                hash_input = self._prompt_input("Enter query hash (or press Enter to skip): ", default="")
                return hash_input if hash_input else None

        except Exception as e:
            hash_input = self._prompt_input(f"\nEnter query hash (or press Enter to skip): ", default="")
            return hash_input if hash_input else None

    def _prompt_feedback_text(self, sentiment: str) -> Optional[str]:
        """Prompt for feedback text based on sentiment."""
        if sentiment == "positive":
            prompt_text = "What did RDST do well? What was helpful?"
        elif sentiment == "negative":
            prompt_text = "What went wrong? How can we improve?"
        else:
            prompt_text = "Please describe your feedback:"

        self._print(f"\n{prompt_text}")
        return self._prompt_multiline()

    def _prompt_multiline(self) -> Optional[str]:
        """Collect multi-line input."""
        self._print_dim("(Type your message, then press Enter twice to submit)")
        self._print("")

        lines = []
        try:
            while True:
                line = self._prompt_input("> ", default="")

                if not line and lines:
                    # Empty line after content = done
                    break
                elif line:
                    lines.append(line)

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

        return "\n".join(lines) if lines else None

    def _prompt_choice(self, valid: list, default: str = None) -> Optional[str]:
        """Prompt for a single choice. 'q' or empty cancels."""
        try:
            prompt = f"Enter choice [{'/'.join(valid)}] (q to cancel)"
            if default:
                prompt += f" (default: {default})"
            prompt += ": "

            choice = self._prompt_input(prompt, default=default or "")

            # Allow 'q' or escape to cancel
            if choice.lower() == 'q' or choice == '\x1b':
                return None
            if not choice and default:
                return default
            elif choice in valid:
                return choice
            else:
                self._print_warning(f"Invalid choice. Please enter one of: {', '.join(valid)}")
                return self._prompt_choice(valid, default)

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

    def _prompt_input(self, prompt: str, default: str = "") -> str:
        """Simple input prompt. Re-raises KeyboardInterrupt/EOFError for clean exit."""
        try:
            if self._has_rich:
                return Prompt.ask(prompt.rstrip(": "), default=default, show_default=False)
            else:
                result = input(prompt).strip()
                return result if result else default
        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

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
            email = self._prompt_input("\nEmail for follow-up (optional, press Enter to skip): ", default="")

            # Basic validation
            if email and "@" in email and "." in email:
                return email
            elif email:
                self._print_warning("Invalid email format, skipping")
                return None

            return None

        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

    def _confirm(self, question: str, default: bool = True) -> bool:
        """Confirm with the user. Re-raises KeyboardInterrupt/EOFError for clean exit."""
        try:
            if self._has_rich:
                return Confirm.ask(question, default=default)
            else:
                suffix = " [Y/n]" if default else " [y/N]"
                response = input(f"{question}{suffix}: ").strip().lower()
                if not response:
                    return default
                return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise so top-level handler catches it

    def _print_header(self):
        """Print feedback header."""
        if self._has_rich:
            self.console.print()
            self.console.print(Panel(
                "[bold]RDST Feedback[/bold]\n\n"
                "Help us improve RDST by sharing your experience.\n"
                "Your feedback goes directly to our team.",
                title="Report",
                border_style="cyan"
            ))
        else:
            print("\n" + "=" * 50)
            print("RDST Feedback")
            print("=" * 50)
            print("Help us improve RDST by sharing your experience.")

    def _print(self, message: str):
        """Print a message."""
        if self._has_rich:
            self.console.print(message)
        else:
            print(message)

    def _print_dim(self, message: str):
        """Print a dimmed message."""
        if self._has_rich:
            self.console.print(f"[dim]{message}[/dim]")
        else:
            print(message)

    def _print_success(self, message: str):
        """Print success message."""
        if self._has_rich:
            self.console.print(f"[bold green]{message}[/bold green]")
        else:
            print(message)

    def _print_error(self, message: str):
        """Print error message."""
        if self._has_rich:
            self.console.print(f"[bold red]Error:[/bold red] {message}")
        else:
            print(f"Error: {message}")

    def _print_warning(self, message: str):
        """Print warning message."""
        if self._has_rich:
            self.console.print(f"[yellow]{message}[/yellow]")
        else:
            print(f"Warning: {message}")

    def _print_info(self, message: str):
        """Print info message."""
        if self._has_rich:
            self.console.print(f"[dim]{message}[/dim]")
        else:
            print(message)
