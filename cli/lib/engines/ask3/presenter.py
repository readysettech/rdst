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

# Import UI system - handles Rich availability internally
from lib.ui import (
    get_console,
    StyleTokens,
    Prompt,
    Confirm,
    IntPrompt,
    DataTable,
    MessagePanel,
    SectionBox,
    SelectionTable,
)


class Ask3Presenter:
    """
    Handles all user-facing output for ask3 engine.

    All print statements are consolidated here, making it easy to:
    - Test the engine without output
    - Customize output format
    - Add logging/telemetry
    """

    def __init__(self, verbose: bool = False):
        """
        Initialize presenter.

        Args:
            verbose: Show detailed progress messages
        """
        self.verbose = verbose
        self._console = get_console()

    def _print(self, message: str) -> None:
        """Print message using console."""
        self._console.print(message)

    # === Phase 1: Schema Loading ===

    def schema_loading(self, target: str) -> None:
        """Show schema loading progress."""
        if self.verbose:
            self._print(
                f"[{StyleTokens.MUTED}]Loading schema for {target}...[/{StyleTokens.MUTED}]"
            )

    def schema_loaded(self, source: str, table_count: int) -> None:
        """Show schema loaded confirmation."""
        if self.verbose:
            self._print(
                f"[{StyleTokens.MUTED}]Schema loaded from {source} ({table_count} tables)[/{StyleTokens.MUTED}]"
            )

    # === Phase 1.5: Schema Filtering ===

    def schema_filtered(self, original: int, filtered: int, tables: List[str]) -> None:
        """Show schema filtering results."""
        if self.verbose:
            tables_str = ", ".join(tables)
            self._print(
                f"[{StyleTokens.MUTED}]Schema filtered: {original} → {filtered} tables ({tables_str})[/{StyleTokens.MUTED}]"
            )

    # === Phase 3.5: Schema Expansion ===

    def schema_expanded(self, added: List[str], total: int) -> None:
        """Show schema expansion results."""
        added_str = ", ".join(added)
        self._print(
            f"[{StyleTokens.SECONDARY}]Schema expanded: +{len(added)} tables ({added_str}) → {total} total[/{StyleTokens.SECONDARY}]"
        )

    # === Phase 2: Clarification ===

    def analyzing_question(self) -> None:
        """Show question analysis progress."""
        self._print(
            f"\n[{StyleTokens.HEADER}]Analyzing your question...[/{StyleTokens.HEADER}]"
        )

    def interpretations(self, items: List[Interpretation]) -> None:
        if not items:
            return

        self._console.print(
            MessagePanel(
                "I found multiple ways to interpret your question.",
                variant="warning",
            )
        )

        option_texts = []
        for interp in items:
            label = self._get_likelihood_label(interp.likelihood)
            style = self._get_likelihood_style(interp.likelihood)
            styled_label = f"[{style}][{label}][/{style}]" if style else f"[{label}]"
            assumptions = ", ".join(interp.assumptions) if interp.assumptions else ""
            suffix = f" — {assumptions}" if assumptions else ""
            option_texts.append(f"{interp.description}\n{styled_label}{suffix}")

        self._console.print(SelectionTable(option_texts))
        self._console.print()

    def clarification_question(self, question: str, options: List[str]) -> None:
        self._console.print(MessagePanel(question, variant="info"))
        self._console.print(SelectionTable(options))
        self._print("")

    def clarification_selected(self, choice: str) -> None:
        """Confirm clarification choice."""
        if self.verbose:
            self._print(
                f"[{StyleTokens.MUTED}]Selected: {choice}[/{StyleTokens.MUTED}]"
            )

    def high_confidence_proceed(self, confidence: float) -> None:
        """Show we're proceeding due to high confidence."""
        self._print(
            f"[{StyleTokens.MUTED}]High confidence ({confidence:.0%}), proceeding without clarification[/{StyleTokens.MUTED}]"
        )

    # === Phase 3: SQL Generation ===

    def generating_sql(self) -> None:
        """Show SQL generation progress."""
        self._print(f"\n[{StyleTokens.HEADER}]Generating SQL...[/{StyleTokens.HEADER}]")

    def sql_generated(self, sql: str, explanation: Optional[str] = None) -> None:
        """Display generated SQL."""
        # Format SQL for readability
        formatted_sql = sqlparse.format(
            sql, reindent=True, keyword_case="upper", indent_width=2, wrap_after=80
        )

        self._console.print(
            SectionBox(
                title="Generated SQL",
                content=formatted_sql,
                subtitle=f"Explanation: {explanation}" if explanation else None,
            )
        )

    # === Phase 4: Validation ===

    def validation_error(self, errors: List[ValidationError]) -> None:
        """Display SQL validation errors."""
        error_lines = []
        for err in errors:
            ref = f"{err.table_alias}.{err.column}" if err.table_alias else err.column
            error_lines.append(f"- Column '{ref}': {err.message}")
            if err.suggestions:
                sugg = ", ".join(err.suggestions[:3])
                error_lines.append(f"  Did you mean: {sugg}?")

        self._console.print(
            MessagePanel(
                "\n".join(error_lines),
                variant="error",
                title="SQL Validation Errors",
            )
        )

    def retry_info(self, attempt: int, max_attempts: int) -> None:
        """Show retry progress."""
        self._print(
            f"\n[{StyleTokens.WARNING}]Retrying SQL generation (attempt {attempt}/{max_attempts})...[/{StyleTokens.WARNING}]"
        )

    # === Phase 5: Execution ===

    def executing_query(self) -> None:
        """Show query execution progress."""
        self._print(
            f"\n[{StyleTokens.HEADER}]Executing query...[/{StyleTokens.HEADER}]"
        )

    def execution_result(
        self,
        columns: List[str],
        rows: List[List[Any]],
        time_ms: float,
        truncated: bool = False,
    ) -> None:
        """Display query execution results."""
        row_count = len(rows)

        if not rows:
            self._print(
                f"[{StyleTokens.MUTED}]No results returned (0 rows in {time_ms:.1f}ms)[/{StyleTokens.MUTED}]"
            )
            return

        # Format rows as strings
        str_rows = [tuple(self._format_value(v) for v in row) for row in rows]

        # Use DataTable component for consistent display
        table = DataTable(
            columns=columns,
            rows=str_rows,
            title=f"Results ({row_count} rows, {time_ms:.1f}ms)",
        )
        self._console.print(table)

        if truncated:
            self._console.print(
                f"\n[{StyleTokens.WARNING}]Note: Results truncated[/{StyleTokens.WARNING}]"
            )

    def execution_error(self, error: str) -> None:
        """Display execution error."""
        self._print(
            f"\n[{StyleTokens.STATUS_ERROR}]Query Execution Error:[/{StyleTokens.STATUS_ERROR}]\n  {error}"
        )

    # === General ===

    def error(self, message: str) -> None:
        """Display a general error message."""
        self._print(
            f"\n[{StyleTokens.STATUS_ERROR}]Error:[/{StyleTokens.STATUS_ERROR}] {message}"
        )

    def warning(self, message: str) -> None:
        """Display a warning message."""
        self._print(
            f"\n[{StyleTokens.WARNING}]Warning:[/{StyleTokens.WARNING}] {message}"
        )

    def info(self, message: str) -> None:
        """Display an info message."""
        self._print(f"[{StyleTokens.MUTED}]{message}[/{StyleTokens.MUTED}]")

    def cancelled(self) -> None:
        """Show operation was cancelled."""
        self._print(
            f"\n[{StyleTokens.WARNING}]Operation cancelled[/{StyleTokens.WARNING}]"
        )

    def success(self, message: str) -> None:
        """Display a success message."""
        self._print(
            f"\n[{StyleTokens.STATUS_SUCCESS}]{message}[/{StyleTokens.STATUS_SUCCESS}]"
        )

    # === User Input ===

    def prompt_choice(self, prompt_text: str, choices: List[str]) -> str:
        """
        Prompt user to make a choice.

        Args:
            prompt_text: The prompt text
            choices: Valid choice values

        Returns:
            User's choice
        """
        while True:
            choice = Prompt.ask(f"{prompt_text} [{'/'.join(choices)}]")
            if choice in choices:
                return choice
            self._print(
                f"[{StyleTokens.WARNING}]Invalid choice. Please enter one of: {', '.join(choices)}[/{StyleTokens.WARNING}]"
            )

    def prompt_number(self, prompt_text: str, min_val: int, max_val: int) -> int:
        """
        Prompt user for a number in range.

        Args:
            prompt_text: The prompt text
            min_val: Minimum valid value
            max_val: Maximum valid value

        Returns:
            User's numeric choice
        """
        while True:
            try:
                choice = IntPrompt.ask(f"{prompt_text} [{min_val}-{max_val}]")
                if min_val <= choice <= max_val:
                    return choice
                self._print(
                    f"[{StyleTokens.WARNING}]Please enter a number between {min_val} and {max_val}[/{StyleTokens.WARNING}]"
                )
            except ValueError:
                self._print(
                    f"[{StyleTokens.WARNING}]Please enter a valid number[/{StyleTokens.WARNING}]"
                )

    def prompt_yes_no(self, prompt_text: str, default: bool = True) -> bool:
        """
        Prompt user for yes/no confirmation.

        Args:
            prompt_text: The prompt text
            default: Default value if user presses enter

        Returns:
            True for yes, False for no
        """
        return Confirm.ask(prompt_text, default=default)

    # === Helper Methods ===

    def _get_likelihood_style(self, likelihood: float) -> str:
        """Get Rich style based on likelihood threshold."""
        if likelihood >= 0.7:
            return StyleTokens.SUCCESS
        elif likelihood >= 0.3:
            return StyleTokens.WARNING
        else:
            return StyleTokens.MUTED

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
        super().__init__(verbose=False)

    def _print(self, message: str) -> None:
        """Suppress all output."""
        pass

    def error(self, message: str) -> None:
        """Still show errors even in quiet mode."""
        print(f"Error: {message}")
