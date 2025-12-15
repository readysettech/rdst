"""
Output formatting utilities for rdstdbg.

Provides consistent formatting for tables, headers, timestamps, etc.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime
import json


class Formatter:
    """Formatting utilities for debug output."""

    # ANSI color codes
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # Colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'

    @classmethod
    def header(cls, text: str, width: int = 80, char: str = '=') -> str:
        """Format a header with border."""
        return f"\n{cls.BOLD}{cls.CYAN}{char * width}{cls.RESET}\n{cls.BOLD}{text}{cls.RESET}\n{cls.BOLD}{cls.CYAN}{char * width}{cls.RESET}"

    @classmethod
    def subheader(cls, text: str, width: int = 80, char: str = '-') -> str:
        """Format a subheader."""
        return f"\n{cls.BOLD}{text}{cls.RESET}\n{cls.DIM}{char * width}{cls.RESET}"

    @classmethod
    def success(cls, text: str) -> str:
        """Format success message."""
        return f"{cls.GREEN}✓{cls.RESET} {text}"

    @classmethod
    def error(cls, text: str) -> str:
        """Format error message."""
        return f"{cls.RED}✗{cls.RESET} {text}"

    @classmethod
    def warning(cls, text: str) -> str:
        """Format warning message."""
        return f"{cls.YELLOW}⚠{cls.RESET} {text}"

    @classmethod
    def info(cls, text: str) -> str:
        """Format info message."""
        return f"{cls.BLUE}ℹ{cls.RESET} {text}"

    @classmethod
    def label(cls, label: str, value: Any, color: str = None) -> str:
        """Format label-value pair."""
        label_color = color or cls.CYAN
        return f"{cls.BOLD}{label_color}{label}:{cls.RESET} {value}"

    @classmethod
    def timestamp(cls, ts: str) -> str:
        """Format timestamp."""
        if 'T' in ts:
            # ISO format - extract time
            time_part = ts.split('T')[1].split('.')[0]
            return f"{cls.GRAY}[{time_part}]{cls.RESET}"
        return ts

    @classmethod
    def session_id(cls, session_id: str, short: bool = True) -> str:
        """Format session ID."""
        if short:
            return f"{cls.MAGENTA}{session_id[:8]}...{cls.RESET}"
        return f"{cls.MAGENTA}{session_id}{cls.RESET}"

    @classmethod
    def state_name(cls, state: str) -> str:
        """Format state name with color coding."""
        # Color code by state type
        if state in ('done', 'completed'):
            return f"{cls.GREEN}{state}{cls.RESET}"
        elif state in ('error', 'failed', 'error_recovery'):
            return f"{cls.RED}{state}{cls.RESET}"
        elif state in ('collecting_clarifications', 'validating_accuracy', 'confirming_sql'):
            return f"{cls.YELLOW}{state}{cls.RESET}"
        elif 'generating' in state:
            return f"{cls.BLUE}{state}{cls.RESET}"
        else:
            return f"{cls.WHITE}{state}{cls.RESET}"

    @classmethod
    def table(cls, headers: List[str], rows: List[List[Any]], col_widths: Optional[List[int]] = None) -> str:
        """Format a simple table."""
        if not rows:
            return f"{cls.DIM}(No data){cls.RESET}"

        # Calculate column widths if not provided
        if not col_widths:
            col_widths = [len(h) for h in headers]
            for row in rows:
                for i, cell in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # Format header
        header_row = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
        separator = "-+-".join("-" * w for w in col_widths)

        output = [
            f"{cls.BOLD}{header_row}{cls.RESET}",
            f"{cls.DIM}{separator}{cls.RESET}"
        ]

        # Format rows
        for row in rows:
            formatted_row = " | ".join(str(cell).ljust(w) for cell, w in zip(row, col_widths))
            output.append(formatted_row)

        return "\n".join(output)

    @classmethod
    def json_pretty(cls, data: Dict[str, Any], indent: int = 2) -> str:
        """Format JSON with syntax highlighting."""
        return json.dumps(data, indent=indent, default=str)

    @classmethod
    def sql(cls, sql_text: str) -> str:
        """Format SQL with keyword highlighting."""
        keywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER',
                   'ON', 'GROUP BY', 'ORDER BY', 'LIMIT', 'OFFSET', 'HAVING', 'AS',
                   'AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN', 'IS', 'NULL', 'DISTINCT']

        highlighted = sql_text
        for keyword in keywords:
            highlighted = highlighted.replace(keyword, f"{cls.BLUE}{keyword}{cls.RESET}")
            highlighted = highlighted.replace(keyword.lower(), f"{cls.BLUE}{keyword.lower()}{cls.RESET}")

        return highlighted

    @classmethod
    def key_value_list(cls, items: Dict[str, Any], indent: int = 0) -> str:
        """Format key-value pairs as list."""
        output = []
        prefix = "  " * indent

        for key, value in items.items():
            if isinstance(value, dict):
                output.append(f"{prefix}{cls.BOLD}{key}:{cls.RESET}")
                output.append(cls.key_value_list(value, indent + 1))
            elif isinstance(value, list):
                output.append(f"{prefix}{cls.BOLD}{key}:{cls.RESET} {cls.DIM}[{len(value)} items]{cls.RESET}")
            else:
                output.append(f"{prefix}{cls.BOLD}{key}:{cls.RESET} {value}")

        return "\n".join(output)

    @classmethod
    def metric(cls, label: str, value: Any, unit: str = "") -> str:
        """Format a metric value."""
        if isinstance(value, (int, float)) and value > 0:
            color = cls.GREEN
        elif value == 0:
            color = cls.GRAY
        else:
            color = cls.WHITE

        return f"{cls.BOLD}{label}:{cls.RESET} {color}{value}{unit}{cls.RESET}"

    @classmethod
    def diff_marker(cls, change_type: str) -> str:
        """Format diff marker (+, -, ~)."""
        if change_type == 'added':
            return f"{cls.GREEN}+{cls.RESET}"
        elif change_type == 'removed':
            return f"{cls.RED}-{cls.RESET}"
        elif change_type == 'modified':
            return f"{cls.YELLOW}~{cls.RESET}"
        else:
            return " "

    @classmethod
    def truncate(cls, text: str, max_length: int = 100, suffix: str = "...") -> str:
        """Truncate text to max length."""
        if len(text) <= max_length:
            return text
        return text[:max_length - len(suffix)] + suffix

    @classmethod
    def format_duration(cls, ms: float) -> str:
        """Format duration in milliseconds to human-readable format."""
        if ms < 1000:
            return f"{ms:.0f}ms"
        elif ms < 60000:
            return f"{ms/1000:.1f}s"
        else:
            mins = int(ms / 60000)
            secs = (ms % 60000) / 1000
            return f"{mins}m {secs:.1f}s"

    @classmethod
    def progress_bar(cls, current: int, total: int, width: int = 40) -> str:
        """Format a progress bar."""
        if total == 0:
            return f"{cls.GRAY}[{'=' * width}]{cls.RESET} 0/0"

        progress = current / total
        filled = int(width * progress)
        bar = '=' * filled + '-' * (width - filled)

        return f"{cls.CYAN}[{bar}]{cls.RESET} {current}/{total} ({progress * 100:.0f}%)"
