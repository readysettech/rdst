"""
Unified Clarification Question UX Component.

Provides a consistent UX for asking clarification questions:
- Always shows numbered options with likelihood-based styling
- Always includes "Other" option for custom input
- Default selection based on highest likelihood
- Sample hints when "Other" is selected
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

try:
    from rich.console import Console
    from rich.prompt import Prompt
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


@dataclass
class ClarificationOption:
    """A single option for a clarification question."""
    text: str
    likelihood: float = 0.5


class ClarificationPrompt:
    """
    Unified UX for asking clarification questions.

    Always shows:
    - Numbered options with likelihood styling
    - "Other" option for custom input (if allow_custom=True)
    - Default selection based on highest likelihood
    - Sample hints when "Other" is selected
    """

    def __init__(self, console: Optional[Console] = None, use_rich: bool = True):
        """
        Initialize the clarification prompt.

        Args:
            console: Rich Console instance (optional, creates one if needed)
            use_rich: Whether to use Rich formatting
        """
        self.use_rich = use_rich and _RICH_AVAILABLE
        self._console = console if self.use_rich else None
        if self.use_rich and self._console is None:
            self._console = Console()

    def ask(
        self,
        question: str,
        options: List[ClarificationOption],
        category: str = "",
        term: str = "",
        allow_custom: bool = True
    ) -> str:
        """
        Ask a clarification question with consistent UX.

        Args:
            question: The question to display
            options: List of ClarificationOption objects
            category: Ambiguity category (for sample hints)
            term: The term being clarified (for sample hints)
            allow_custom: Whether to show "Other" option

        Returns:
            The selected option text or custom input
        """
        # Extract just the question text (remove embedded options)
        clean_question = self._extract_question_text(question)

        # Find default option (highest likelihood)
        default_idx = self._get_default_index(options)

        # Display question and options
        self._display_question(clean_question)
        self._display_options(options, allow_custom)

        # Get user choice
        max_choice = len(options) + (1 if allow_custom else 0)
        choice = self._prompt_choice(1, max_choice, default_idx)

        # Handle "Other" selection
        if allow_custom and choice == len(options) + 1:
            return self._prompt_custom_input(category, term)

        # Return selected option text
        return options[choice - 1].text

    def _extract_question_text(self, question: str) -> str:
        """
        Extract question text, removing embedded option lists.

        LLM sometimes returns questions like:
        "What threshold is 'high'? [1] Above 1,000, [2] Above 10,000..."

        This extracts just: "What threshold is 'high'?"
        """
        # Find where embedded options start
        import re

        # Pattern: [1] or (1) or 1. or 1)
        pattern = r'\s*[\[\(]?[1]\s*[\]\)]?\s*'
        match = re.search(pattern, question)

        if match:
            # Return text before the options
            return question[:match.start()].strip()

        return question.strip()

    def _get_default_index(self, options: List[ClarificationOption]) -> int:
        """Get 1-based index of highest likelihood option."""
        if not options:
            return 1

        max_likelihood = -1.0
        max_idx = 1

        for i, opt in enumerate(options, 1):
            if opt.likelihood > max_likelihood:
                max_likelihood = opt.likelihood
                max_idx = i

        return max_idx

    def _display_question(self, question: str) -> None:
        """Display the question."""
        if self.use_rich and self._console:
            self._console.print(f"\n[bold]{question}[/bold]\n")
        else:
            print(f"\n{question}\n")

    def _display_options(
        self,
        options: List[ClarificationOption],
        allow_custom: bool
    ) -> None:
        """Display numbered options with likelihood styling."""
        for i, opt in enumerate(options, 1):
            if self.use_rich and self._console:
                style = self._get_likelihood_style(opt.likelihood)
                if style:
                    self._console.print(f"  [{style}][{i}] {opt.text}[/{style}]")
                else:
                    self._console.print(f"  [{i}] {opt.text}")
            else:
                label = self._get_likelihood_label(opt.likelihood)
                print(f"  [{i}] {opt.text} [{label}]")

        # Add "Other" option
        if allow_custom:
            other_idx = len(options) + 1
            if self.use_rich and self._console:
                self._console.print(f"  [dim][{other_idx}] Other (enter custom value)[/dim]")
            else:
                print(f"  [{other_idx}] Other (enter custom value)")

        print()  # Blank line after options

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

    def _prompt_choice(self, min_val: int, max_val: int, default: int) -> int:
        """Prompt user for a choice in range."""
        prompt_text = f"Your choice [{min_val}-{max_val}] (default: {default})"

        while True:
            try:
                if self.use_rich and self._console:
                    response = Prompt.ask(prompt_text, default=str(default))
                else:
                    response = input(f"{prompt_text}: ").strip()
                    if not response:
                        response = str(default)

                choice = int(response)
                if min_val <= choice <= max_val:
                    return choice

                self._print_error(f"Please enter a number between {min_val} and {max_val}")
            except ValueError:
                self._print_error("Please enter a valid number")

    def _prompt_custom_input(self, category: str, term: str) -> str:
        """Prompt for custom input with sample hints."""
        hints = self._get_sample_hints(category, term)

        if self.use_rich and self._console:
            self._console.print(f"\n[dim]Examples: {hints}[/dim]")
            return Prompt.ask("Enter your clarification")
        else:
            print(f"\nExamples: {hints}")
            return input("Enter your clarification: ").strip()

    def _get_sample_hints(self, category: str, term: str) -> str:
        """Get context-aware sample hints for custom input."""
        hints_by_category = {
            'unclear_value_reference': f'"{term} > 5000", "{term} >= 1000", "top 100 by {term}"',
            'unclear_schema_reference': f'"use {term} table", "join with {term}", "{term} column"',
            'missing_sql_keywords': '"count only", "list all", "show top 10", "group by category"',
            'temporal_spatial_ambiguity': '"last 30 days", "this year", "all time", "since 2020"',
            'unclear_knowledge_source': f'"{term} means X", "define {term} as Y"',
            'insufficient_reasoning_context': '"include inactive", "exclude deleted", "only verified"',
        }
        return hints_by_category.get(category, f'"{term} = specific_value", "custom criteria"')

    def _print_error(self, message: str) -> None:
        """Print an error message."""
        if self.use_rich and self._console:
            self._console.print(f"[red]{message}[/red]")
        else:
            print(message)
