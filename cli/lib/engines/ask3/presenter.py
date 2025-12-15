"""
Ask3Presenter - Handles all user-facing output.

Separates presentation from business logic, making the engine
easier to test and the output easier to customize.
"""

from __future__ import annotations

import sqlparse
from typing import Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Interpretation, ValidationError
    from .context import Ask3Context

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


class Ask3Presenter:
    """
    Handles all user-facing output for ask3 engine.

    All print statements are consolidated here, making it easy to:
    - Test the engine without output
    - Customize output format
    - Add logging/telemetry
    """

    def __init__(self, verbose: bool = False, use_rich: bool = True):
        """
        Initialize presenter.

        Args:
            verbose: Show detailed progress messages
            use_rich: Use Rich library for formatting (falls back to plain text)
        """
        self.verbose = verbose
        self.use_rich = use_rich and _RICH_AVAILABLE
        self._console = Console() if self.use_rich else None

    def _print(self, message: str) -> None:
        """Print message using Rich or plain text."""
        if self._console:
            self._console.print(message)
        else:
            print(message)

    # === Phase 1: Schema Loading ===

    def schema_loading(self, target: str) -> None:
        """Show schema loading progress."""
        if self.verbose:
            self._print(f"[dim]Loading schema for {target}...[/dim]" if self.use_rich
                       else f"Loading schema for {target}...")

    def schema_loaded(self, source: str, table_count: int) -> None:
        """Show schema loaded confirmation."""
        if self.verbose:
            self._print(f"[dim]Schema loaded from {source} ({table_count} tables)[/dim]" if self.use_rich
                       else f"Schema loaded from {source} ({table_count} tables)")

    # === Phase 1.5: Schema Filtering ===

    def schema_filtered(self, original: int, filtered: int, tables: List[str]) -> None:
        """Show schema filtering results."""
        if self.verbose:
            tables_str = ", ".join(tables)
            self._print(f"[dim]Schema filtered: {original} → {filtered} tables ({tables_str})[/dim]" if self.use_rich
                       else f"Schema filtered: {original} → {filtered} tables ({tables_str})")

    # === Phase 3.5: Schema Expansion ===

    def schema_expanded(self, added: List[str], total: int) -> None:
        """Show schema expansion results."""
        added_str = ", ".join(added)
        self._print(f"[cyan]Schema expanded: +{len(added)} tables ({added_str}) → {total} total[/cyan]" if self.use_rich
                   else f"Schema expanded: +{len(added)} tables ({added_str}) → {total} total")

    # === Phase 2: Clarification ===

    def analyzing_question(self) -> None:
        """Show question analysis progress."""
        self._print("\n[bold]Analyzing your question...[/bold]" if self.use_rich
                   else "\nAnalyzing your question...")

    def interpretations(self, items: List[Interpretation]) -> None:
        """Display possible interpretations for user selection."""
        if not items:
            return

        self._print("\n[bold yellow]I found multiple ways to interpret your question:[/bold yellow]\n" if self.use_rich
                   else "\nI found multiple ways to interpret your question:\n")

        for i, interp in enumerate(items, 1):
            if self.use_rich:
                # Apply styling based on likelihood
                style = self._get_likelihood_style(interp.likelihood)
                if style:
                    self._print(f"  [cyan]{i}.[/cyan] [{style}]{interp.description}[/{style}]")
                else:
                    self._print(f"  [cyan]{i}.[/cyan] {interp.description}")
            else:
                # Plain text - add likelihood label
                label = self._get_likelihood_label(interp.likelihood)
                self._print(f"  {i}. {interp.description} [{label}]")

            for assumption in interp.assumptions:
                self._print(f"     [dim]- {assumption}[/dim]" if self.use_rich
                           else f"     - {assumption}")
        self._print("")

    def clarification_question(self, question: str, options: List[str]) -> None:
        """Display a clarification question with options."""
        self._print(f"\n[bold]{question}[/bold]\n" if self.use_rich
                   else f"\n{question}\n")

        for i, opt in enumerate(options, 1):
            self._print(f"  [{i}] {opt}")
        self._print("")

    def clarification_selected(self, choice: str) -> None:
        """Confirm clarification choice."""
        if self.verbose:
            self._print(f"[dim]Selected: {choice}[/dim]" if self.use_rich
                       else f"Selected: {choice}")

    def high_confidence_proceed(self, confidence: float) -> None:
        """Show we're proceeding due to high confidence."""
        self._print(f"[dim]High confidence ({confidence:.0%}), proceeding without clarification[/dim]" if self.use_rich
                   else f"High confidence ({confidence:.0%}), proceeding without clarification")

    # === Phase 3: SQL Generation ===

    def generating_sql(self) -> None:
        """Show SQL generation progress."""
        self._print("\n[bold]Generating SQL...[/bold]" if self.use_rich
                   else "\nGenerating SQL...")

    def sql_generated(self, sql: str, explanation: Optional[str] = None) -> None:
        """Display generated SQL."""
        # Format SQL for readability
        formatted_sql = sqlparse.format(
            sql,
            reindent=True,
            keyword_case='upper',
            indent_width=2,
            wrap_after=80
        )

        if self.use_rich and self._console:
            syntax = Syntax(formatted_sql, "sql", theme="monokai", line_numbers=False)
            self._console.print(Panel(syntax, title="Generated SQL", border_style="cyan"))
            if explanation:
                self._console.print(f"\n[bold]Explanation:[/bold] {explanation}")
        else:
            self._print("\n" + "=" * 60)
            self._print("Generated SQL:")
            self._print("-" * 60)
            self._print(formatted_sql)
            self._print("-" * 60)
            if explanation:
                self._print(f"\nExplanation: {explanation}")

    # === Phase 4: Validation ===

    def validation_error(self, errors: List[ValidationError]) -> None:
        """Display SQL validation errors."""
        self._print("\n[bold red]SQL Validation Errors:[/bold red]" if self.use_rich
                   else "\nSQL Validation Errors:")

        for err in errors:
            ref = f"{err.table_alias}.{err.column}" if err.table_alias else err.column
            self._print(f"  [red]- Column '{ref}': {err.message}[/red]" if self.use_rich
                       else f"  - Column '{ref}': {err.message}")
            if err.suggestions:
                sugg = ", ".join(err.suggestions[:3])
                self._print(f"    [dim]Did you mean: {sugg}?[/dim]" if self.use_rich
                           else f"    Did you mean: {sugg}?")

    def retry_info(self, attempt: int, max_attempts: int) -> None:
        """Show retry progress."""
        self._print(f"\n[yellow]Retrying SQL generation (attempt {attempt}/{max_attempts})...[/yellow]" if self.use_rich
                   else f"\nRetrying SQL generation (attempt {attempt}/{max_attempts})...")

    # === Phase 5: Execution ===

    def executing_query(self) -> None:
        """Show query execution progress."""
        self._print("\n[bold]Executing query...[/bold]" if self.use_rich
                   else "\nExecuting query...")

    def execution_result(
        self,
        columns: List[str],
        rows: List[List[Any]],
        time_ms: float,
        truncated: bool = False
    ) -> None:
        """Display query execution results."""
        row_count = len(rows)

        if not rows:
            self._print(f"\n[dim]No results returned (0 rows in {time_ms:.1f}ms)[/dim]" if self.use_rich
                       else f"\nNo results returned (0 rows in {time_ms:.1f}ms)")
            return

        if self.use_rich and self._console:
            table = Table(title=f"Results ({row_count} rows, {time_ms:.1f}ms)")
            for col in columns:
                table.add_column(col, style="cyan")

            for row in rows:
                str_row = [self._format_value(v) for v in row]
                table.add_row(*str_row)

            self._console.print(table)

            if truncated:
                self._console.print(f"\n[yellow]Note: Results truncated[/yellow]")
        else:
            self._print(f"\nResults ({row_count} rows, {time_ms:.1f}ms):")
            self._print("-" * 60)

            # Calculate column widths
            widths = [len(c) for c in columns]
            for row in rows:
                for i, val in enumerate(row):
                    widths[i] = max(widths[i], len(self._format_value(val)))

            # Header
            header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
            self._print(header)
            self._print("-+-".join("-" * w for w in widths))

            # Rows
            for row in rows:
                row_str = " | ".join(
                    self._format_value(v).ljust(widths[i])
                    for i, v in enumerate(row)
                )
                self._print(row_str)

            if truncated:
                self._print("\nNote: Results truncated")

    def execution_error(self, error: str) -> None:
        """Display execution error."""
        self._print(f"\n[bold red]Query Execution Error:[/bold red]\n  {error}" if self.use_rich
                   else f"\nQuery Execution Error:\n  {error}")

    # === General ===

    def error(self, message: str) -> None:
        """Display a general error message."""
        self._print(f"\n[bold red]Error:[/bold red] {message}" if self.use_rich
                   else f"\nError: {message}")

    def warning(self, message: str) -> None:
        """Display a warning message."""
        self._print(f"\n[yellow]Warning:[/yellow] {message}" if self.use_rich
                   else f"\nWarning: {message}")

    def info(self, message: str) -> None:
        """Display an info message."""
        self._print(f"[dim]{message}[/dim]" if self.use_rich
                   else message)

    def cancelled(self) -> None:
        """Show operation was cancelled."""
        self._print("\n[yellow]Operation cancelled[/yellow]" if self.use_rich
                   else "\nOperation cancelled")

    def success(self, message: str) -> None:
        """Display a success message."""
        self._print(f"\n[bold green]{message}[/bold green]" if self.use_rich
                   else f"\n{message}")

    # === User Input ===

    def prompt_choice(self, prompt: str, choices: List[str]) -> str:
        """
        Prompt user to make a choice.

        Args:
            prompt: The prompt text
            choices: Valid choice values

        Returns:
            User's choice
        """
        while True:
            choice = input(f"{prompt} [{'/'.join(choices)}]: ").strip()
            if choice in choices:
                return choice
            self._print(f"Invalid choice. Please enter one of: {', '.join(choices)}")

    def prompt_number(self, prompt: str, min_val: int, max_val: int) -> int:
        """
        Prompt user for a number in range.

        Args:
            prompt: The prompt text
            min_val: Minimum valid value
            max_val: Maximum valid value

        Returns:
            User's numeric choice
        """
        while True:
            try:
                choice = int(input(f"{prompt} [{min_val}-{max_val}]: ").strip())
                if min_val <= choice <= max_val:
                    return choice
                self._print(f"Please enter a number between {min_val} and {max_val}")
            except ValueError:
                self._print("Please enter a valid number")

    def prompt_yes_no(self, prompt: str, default: bool = True) -> bool:
        """
        Prompt user for yes/no confirmation.

        Args:
            prompt: The prompt text
            default: Default value if user presses enter

        Returns:
            True for yes, False for no
        """
        default_str = "Y/n" if default else "y/N"
        response = input(f"{prompt} [{default_str}]: ").strip().lower()

        if not response:
            return default

        return response in ('y', 'yes')

    # === Helper Methods ===

    def _get_likelihood_style(self, likelihood: float) -> str:
        """Get Rich style based on likelihood threshold."""
        if likelihood >= 0.7:
            return "bold cyan"  # High confidence
        elif likelihood >= 0.3:
            return ""  # Normal (medium)
        else:
            return "dim"  # Low confidence

    def _get_likelihood_label(self, likelihood: float) -> str:
        """Get text label for likelihood (plain text fallback)."""
        if likelihood >= 0.7:
            return "High"
        elif likelihood >= 0.3:
            return "Medium"
        else:
            return "Low"

    def _format_value(self, val: Any) -> str:
        """Format a value for display."""
        if val is None:
            return "NULL"
        elif isinstance(val, (bytes, bytearray)):
            return f"<binary: {len(val)} bytes>"
        elif isinstance(val, str) and len(val) > 50:
            return val[:47] + "..."
        else:
            return str(val)


class QuietPresenter(Ask3Presenter):
    """
    A presenter that suppresses most output.

    Useful for testing or programmatic use.
    """

    def __init__(self):
        super().__init__(verbose=False, use_rich=False)

    def _print(self, message: str) -> None:
        """Suppress all output."""
        pass

    def error(self, message: str) -> None:
        """Still show errors even in quiet mode."""
        print(f"Error: {message}")
