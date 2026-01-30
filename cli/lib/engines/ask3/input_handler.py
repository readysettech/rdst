"""
AskInputHandler - Handles CLI input collection for Ask flow.

Pure input collection, no rendering. Called when service yields
events that require user input.
"""

from __future__ import annotations

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ...services.types import AskClarificationNeededEvent, AskClarificationQuestion

from lib.ui import (
    get_console,
    StyleTokens,
    Prompt,
    IntPrompt,
    MessagePanel,
    SelectionTable,
)


class AskInputHandler:
    """
    Handles user input collection for Ask flow.

    Usage:
        handler = AskInputHandler()

        # When service yields ClarificationNeededEvent:
        answers = handler.collect_clarifications(event)
        # Then resume service with answers
    """

    def __init__(self):
        self._console = get_console()

    def collect_clarifications(
        self, event: "AskClarificationNeededEvent"
    ) -> Dict[str, str]:
        """
        Collect clarification answers from user.

        Args:
            event: ClarificationNeededEvent with questions

        Returns:
            Dict mapping question_id -> selected answer
        """
        answers = {}

        for question in event.questions:
            answer = self._prompt_clarification(question)
            if answer:
                answers[question.id] = answer

        return answers

    def _prompt_clarification(self, question: "AskClarificationQuestion") -> str:
        """Prompt user for a single clarification question."""
        # Extract just the question part (before colon or bracket where options start)
        question_text = question.question.split(":")[0].split("[")[0].strip()
        if not question_text.endswith("?"):
            question_text += "?"

        # Display the question (without inline options - we show them separately)
        self._console.print(MessagePanel(question_text, variant="info"))

        # Display options as numbered list
        self._console.print(SelectionTable(question.options))
        self._console.print()

        # Get user's choice
        while True:
            try:
                choice = IntPrompt.ask(
                    f"[{StyleTokens.EMPHASIS}]Your choice[/{StyleTokens.EMPHASIS}]",
                    default=1,
                )
                if 1 <= choice <= len(question.options):
                    selected = question.options[choice - 1]
                    self._console.print(
                        f"[{StyleTokens.MUTED}]Selected: {selected}[/{StyleTokens.MUTED}]"
                    )
                    return selected
                else:
                    self._console.print(
                        f"[{StyleTokens.WARNING}]Please enter a number between 1 and "
                        f"{len(question.options)}[/{StyleTokens.WARNING}]"
                    )
            except ValueError:
                self._console.print(
                    f"[{StyleTokens.WARNING}]Please enter a valid number[/{StyleTokens.WARNING}]"
                )
            except (EOFError, KeyboardInterrupt):
                raise

    def prompt_choice(self, prompt_text: str, choices: List[str]) -> str:
        """Prompt user to make a choice from list."""
        while True:
            choice = Prompt.ask(f"{prompt_text} [{'/'.join(choices)}]")
            if choice in choices:
                return choice
            self._console.print(
                f"[{StyleTokens.WARNING}]Invalid choice. "
                f"Please enter one of: {', '.join(choices)}[/{StyleTokens.WARNING}]"
            )

    def prompt_number(self, prompt_text: str, min_val: int, max_val: int) -> int:
        """Prompt user for a number in range."""
        while True:
            try:
                choice = IntPrompt.ask(f"{prompt_text} [{min_val}-{max_val}]")
                if min_val <= choice <= max_val:
                    return choice
                self._console.print(
                    f"[{StyleTokens.WARNING}]Please enter a number between "
                    f"{min_val} and {max_val}[/{StyleTokens.WARNING}]"
                )
            except ValueError:
                self._console.print(
                    f"[{StyleTokens.WARNING}]Please enter a valid number[/{StyleTokens.WARNING}]"
                )


class NonInteractiveInputHandler(AskInputHandler):
    """
    Input handler that returns defaults without prompting.

    Used for --no-interactive mode.
    """

    def collect_clarifications(
        self, event: "AskClarificationNeededEvent"
    ) -> Dict[str, str]:
        """Return first option for each question without prompting."""
        answers = {}
        for question in event.questions:
            if question.options:
                answers[question.id] = question.options[0]
        return answers

    def prompt_choice(self, prompt_text: str, choices: List[str]) -> str:
        """Return first choice."""
        return choices[0] if choices else ""

    def prompt_number(self, prompt_text: str, min_val: int, max_val: int) -> int:
        """Return min value."""
        return min_val
