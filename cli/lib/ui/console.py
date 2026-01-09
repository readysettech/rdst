"""
RDST Design System - Console
=============================

Provides a configured Rich Console instance with theme awareness.
Use this instead of creating Console() instances throughout the codebase.

Usage:
    from lib.ui import get_console, StyleTokens

    console = get_console()
    console.print(f"[{StyleTokens.SUCCESS}]Operation completed[/{StyleTokens.SUCCESS}]")
    console.print(f"[{StyleTokens.STATUS_SUCCESS}]Bold success message[/{StyleTokens.STATUS_SUCCESS}]")
    console.print(Panel("Content", title="Title"))
"""

import sys
from typing import Optional

from rich.console import Console as RichConsole
from rich.theme import Theme as RichTheme

from .theme import THEME_DEFINITION


def _disable_focus_reporting() -> None:
    """Disable terminal focus reporting to prevent ^[[I/^[[O sequences."""
    if sys.stdout.isatty():
        # Send DECRST 1004 to disable focus reporting
        sys.stdout.write("\x1b[?1004l")
        sys.stdout.flush()


def _create_console() -> "RichConsole":
    """Create a configured Rich Console instance."""
    # Disable focus reporting to prevent spurious escape sequences
    _disable_focus_reporting()

    theme = RichTheme(THEME_DEFINITION)
    return RichConsole(theme=theme, highlight=False)


def create_console(width: Optional[int] = None, **kwargs) -> "RichConsole":
    theme = RichTheme(THEME_DEFINITION)
    return RichConsole(theme=theme, highlight=False, width=width, **kwargs)


# Singleton console instance
console: "RichConsole" = _create_console()


def get_console() -> "RichConsole":
    """Get the console instance."""
    return console


def print_success(message: str):
    """Print a success message."""
    from .theme import StyleTokens

    c = get_console()
    c.print(f"[{StyleTokens.STATUS_SUCCESS}]{message}[/{StyleTokens.STATUS_SUCCESS}]")


def print_error(message: str):
    """Print an error message."""
    from .theme import StyleTokens

    c = get_console()
    c.print(f"[{StyleTokens.STATUS_ERROR}]{message}[/{StyleTokens.STATUS_ERROR}]")


def print_warning(message: str):
    """Print a warning message."""
    from .theme import StyleTokens

    c = get_console()
    c.print(f"[{StyleTokens.STATUS_WARNING}]{message}[/{StyleTokens.STATUS_WARNING}]")


def print_info(message: str):
    """Print an info message."""
    from .theme import StyleTokens

    c = get_console()
    c.print(f"[{StyleTokens.STATUS_INFO}]{message}[/{StyleTokens.STATUS_INFO}]")
