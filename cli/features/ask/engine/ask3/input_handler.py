"""
AskInputHandler - Handles CLI input collection for Ask flow.

Pure input collection, no rendering. Called when service yields
events that require user input.
"""

from __future__ import annotations

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from features.ask.events import AskClarificationNeededEvent
    from features.ask.models import AskClarificationQuestion

from shared.ui import (
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
        question_text = question.question.strip()
        if not question_text.endswith("?"):
            question_text += "?"

        # Display the question (without inline options - we show them separately)
        self._console.print(MessagePanel(question_text, variant="info"))

        # An open question asks for a value or definition: the user picks one of Ask's
        # suggested answers by number or types their own. With no input left (stdin
        # closed), it is skipped like an empty answer.
        if not question.options:
            if question.examples:
                self._console.print(SelectionTable(list(question.examples)))
                prompt = "Pick a number or type your own answer (Enter skips): "
            else:
                prompt = "Your answer: "
            try:
                answer = input(prompt).strip()
            except EOFError:
                return ""
            if answer.isdigit() and 1 <= int(answer) <= len(question.examples):
                return question.examples[int(answer) - 1]
            return answer

        # Each option shows the schema identifier it reads when there is one, and
        # "None of these" lets the user describe the answer in their own words.
        hints = question.hints or [""] * len(question.options)
        all_options = [
            f"{option} ({hint})" if hint else option
            for option, hint in zip(question.options, hints)
        ] + ["None of these (describe what you mean)"]
        self._console.print(SelectionTable(all_options))
        self._console.print()

        if question.multiple:
            return self._prompt_several(question.options)

        # Get user's choice
        while True:
            try:
                choice = IntPrompt.ask(
                    f"[{StyleTokens.EMPHASIS}]Your choice[/{StyleTokens.EMPHASIS}]",
                    default=1,
                )
                if choice == len(all_options):
                    # Free-text: let user describe what they mean
                    answer = input("Describe what you mean: ")
                    self._console.print(
                        f"[{StyleTokens.MUTED}]Got it: {answer}[/{StyleTokens.MUTED}]"
                    )
                    return answer
                if 1 <= choice <= len(question.options):
                    selected = question.options[choice - 1]
                    self._console.print(
                        f"[{StyleTokens.MUTED}]Selected: {selected}[/{StyleTokens.MUTED}]"
                    )
                    return selected
                else:
                    self._console.print(
                        f"[{StyleTokens.WARNING}]Please enter a number between 1 and "
                        f"{len(all_options)}[/{StyleTokens.WARNING}]"
                    )
            except ValueError:
                self._console.print(
                    f"[{StyleTokens.WARNING}]Please enter a valid number[/{StyleTokens.WARNING}]"
                )
            except EOFError:
                # No input left (stdin closed): skipped like an open question.
                return ""

    def _prompt_several(self, options: List[str]) -> str:
        """Every option the user picks, by number; the number after the options lets them
        describe the answer instead."""
        while True:
            try:
                reply = input("Pick one or more numbers, separated by commas: ").strip()
            except EOFError:
                return ""
            numbers = [part.strip() for part in reply.split(",") if part.strip()]
            if numbers and all(
                part.isdigit() and 1 <= int(part) <= len(options) + 1 for part in numbers
            ):
                if any(int(part) == len(options) + 1 for part in numbers):
                    return input("Describe what you mean: ")
                return "; ".join(
                    options[index - 1] for index in dict.fromkeys(map(int, numbers))
                )
            self._console.print(
                f"[{StyleTokens.WARNING}]Please enter numbers between 1 and "
                f"{len(options) + 1}[/{StyleTokens.WARNING}]"
            )

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
        """Abstain; ranked auto-resolution belongs in AskService."""
        return {}

    def prompt_choice(self, prompt_text: str, choices: List[str]) -> str:
        """Refuse to invent a choice in non-interactive mode."""
        raise RuntimeError("Non-interactive mode cannot answer a choice prompt")

    def prompt_number(self, prompt_text: str, min_val: int, max_val: int) -> int:
        """Refuse to invent a number in non-interactive mode."""
        raise RuntimeError("Non-interactive mode cannot answer a number prompt")
