"""
RDST Design System - Interactive Prompts
==========================================

Wrapper classes for Rich interactive prompts with plain text fallback.
These handle Rich availability internally - consuming code never needs to check.

Usage:
    from lib.ui import Prompt, Confirm, IntPrompt, SelectPrompt

    # These work with or without Rich installed
    name = Prompt.ask("Enter name")
    confirmed = Confirm.ask("Continue?")
    age = IntPrompt.ask("Enter age", default=25)

    # Numbered selection
    choice = SelectPrompt.ask(
        "How was your experience?",
        options=["Positive", "Negative", "Neutral"],
        default=3
    )
"""

from typing import List, Optional, Any, Union

from rich.prompt import (
    Prompt as RichPrompt,
    Confirm as RichConfirm,
    IntPrompt as RichIntPrompt,
)


class Prompt:
    """
    Text prompt with Rich formatting when available.

    Usage:
        name = Prompt.ask("Enter your name")
        choice = Prompt.ask("Select option", choices=["a", "b", "c"])
        value = Prompt.ask("Enter value", default="default")
    """

    @staticmethod
    def ask(
        prompt: str,
        *,
        default: str = "",
        choices: Optional[List[str]] = None,
        show_default: bool = True,
        show_choices: bool = True,
        password: bool = False,
    ) -> str:
        """
        Prompt for text input.

        Args:
            prompt: The prompt text to display
            default: Default value if user presses Enter
            choices: List of valid choices (user must pick one)
            show_default: Whether to show the default value in prompt
            show_choices: Whether to show the choices in prompt
            password: Whether to hide input (for passwords)

        Returns:
            User input string
        """
        return RichPrompt.ask(
            prompt,
            default=default if default else None,
            choices=choices,
            show_default=show_default,
            show_choices=show_choices,
            password=password,
        )


class Confirm:
    """
    Yes/No confirmation prompt with Rich formatting when available.

    Usage:
        if Confirm.ask("Do you want to continue?"):
            print("Continuing...")
    """

    @staticmethod
    def ask(
        prompt: str,
        *,
        default: bool = True,
    ) -> bool:
        """
        Prompt for yes/no confirmation.

        Args:
            prompt: The prompt text to display
            default: Default value if user presses Enter (True=yes, False=no)

        Returns:
            True for yes, False for no
        """
        return RichConfirm.ask(prompt, default=default)


class IntPrompt:
    """
    Integer prompt with Rich formatting when available.

    Usage:
        age = IntPrompt.ask("Enter your age")
        port = IntPrompt.ask("Enter port", default=5432)
    """

    @staticmethod
    def ask(
        prompt: str,
        *,
        default: Optional[int] = None,
        show_default: bool = True,
    ) -> int:
        """
        Prompt for integer input.

        Args:
            prompt: The prompt text to display
            default: Default value if user presses Enter
            show_default: Whether to show the default value in prompt

        Returns:
            Integer value from user
        """
        return RichIntPrompt.ask(
            prompt,
            default=default,
            show_default=show_default,
        )


class FloatPrompt:
    """
    Float prompt with Rich formatting when available.

    Usage:
        rate = FloatPrompt.ask("Enter rate", default=0.5)
    """

    @staticmethod
    def ask(
        prompt: str,
        *,
        default: Optional[float] = None,
        show_default: bool = True,
    ) -> float:
        """
        Prompt for float input.

        Args:
            prompt: The prompt text to display
            default: Default value if user presses Enter
            show_default: Whether to show the default value in prompt

        Returns:
            Float value from user
        """
        # Rich doesn't have FloatPrompt, use Prompt and convert
        while True:
            result = RichPrompt.ask(
                prompt,
                default=str(default) if default is not None else None,
                show_default=show_default,
            )
            try:
                return float(result)
            except ValueError:
                print("Please enter a valid number")


class SelectPrompt:
    """
    Numbered selection prompt with Rich formatting when available.

    Displays a question with numbered options and prompts for selection.

    Usage:
        choice = SelectPrompt.ask(
            "How was your experience?",
            options=["Positive", "Negative", "Neutral"],
            default=3
        )
        # Returns: 1, 2, or 3

        # Or get the option text:
        text = SelectPrompt.ask(
            "Select database",
            options=["PostgreSQL", "MySQL"],
            return_index=False
        )
        # Returns: "PostgreSQL" or "MySQL"
    """

    @staticmethod
    def ask(
        question: str,
        options: List[str],
        *,
        default: Optional[int] = None,
        return_index: bool = True,
        allow_cancel: bool = False,
        cancel_value: str = "q",
    ) -> Optional[Union[int, str]]:
        """
        Display numbered options and prompt for selection.

        Args:
            question: The question to display above options
            options: List of option strings
            default: Default option number (1-indexed)
            return_index: If True, return the number (1-indexed). If False, return the option text.
            allow_cancel: If True, allow 'q' to cancel and return None
            cancel_value: The value that triggers cancel (default: 'q')

        Returns:
            Selected option number (1-indexed) or option text, or None if cancelled
        """
        from .theme import StyleTokens

        # Get console for output
        from .console import get_console

        console = get_console()

        # Display question
        console.print(f"\n{question}")

        # Display numbered options
        # Note: Escape brackets with \[ so Rich treats them as literal text
        for i, option in enumerate(options, 1):
            if default and i == default:
                console.print(
                    f"  [{StyleTokens.CHOICE_ACTIVE}]\\[{i}][/{StyleTokens.CHOICE_ACTIVE}] {option} [{StyleTokens.MUTED}](default)[/{StyleTokens.MUTED}]"
                )
            else:
                console.print(
                    f"  [{StyleTokens.MUTED}]\\[{i}][/{StyleTokens.MUTED}] {option}"
                )

        if allow_cancel:
            console.print(
                f"  [{StyleTokens.MUTED}]\\[{cancel_value}][/{StyleTokens.MUTED}] Cancel"
            )

        # Build prompt
        valid_range = f"1-{len(options)}"
        if default:
            prompt_text = f"Select [{valid_range}]"
        else:
            prompt_text = f"Select [{valid_range}]"

        # Get input
        while True:
            response = RichPrompt.ask(
                prompt_text,
                default=str(default) if default else None,
                show_default=bool(default),
            )

            # Check for cancel
            if allow_cancel and response.lower() == cancel_value.lower():
                return None

            # Validate input
            try:
                choice = int(response)
                if 1 <= choice <= len(options):
                    if return_index:
                        return choice
                    else:
                        return options[choice - 1]
                else:
                    console.print(
                        f"[{StyleTokens.WARNING}]Please enter a number between 1 and {len(options)}[/{StyleTokens.WARNING}]"
                    )
            except ValueError:
                console.print(
                    f"[{StyleTokens.WARNING}]Please enter a valid number[/{StyleTokens.WARNING}]"
                )
