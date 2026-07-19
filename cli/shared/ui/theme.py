"""
RDST Design System - Theme
===========================

Tactical/ops-inspired dark theme with cyan accents.
Inspired by command & control interfaces.
"""

import os
import re
import select
import sys
import time
from typing import Literal, Optional

from rich import box

BOX_HEAVY = box.HEAVY
BOX_SQUARE = box.SQUARE
BOX_SIMPLE = box.SIMPLE
BOX_MINIMAL = box.MINIMAL
BOX_ROUNDED = box.ROUNDED

ThemeName = Literal["dark", "light"]

_OSC_11_QUERY = b"\x1b]11;?\x07"
_OSC_11_RESPONSE_RE = re.compile(
    r"\x1b\]11;rgb:([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})(?:\x07|\x1b\\)"
 )
_OSC_11_TIMEOUT_SECONDS = 0.15
_LIGHT_BACKGROUND_THRESHOLD = 0.5


class _DarkColors:
    """Core color palette for dark terminals."""

    # === Primary Colors ===
    PRIMARY = "cyan"  # Main accent - borders, key UI elements
    SECONDARY = "bright_cyan"  # Highlighted accent - active states

    # === Semantic Colors ===
    SUCCESS = "green"  # Online, active, positive outcomes
    WARNING = "yellow"  # Caution, medium priority, pending
    ERROR = "bright_red"  # Critical, errors, high priority
    INFO = "bright_blue"  # Informational, links

    # === Text Colors ===
    TEXT = "white"  # Primary text
    TEXT_DIM = "bright_black"  # Secondary text, timestamps
    MUTED = "dim"  # Disabled, tertiary info
    ACCENT = "magenta"  # Special highlights, categories

    # === Data Colors ===
    HIGHLIGHT = "bright_cyan"  # Important values (IDs, hashes)
    PARAM = "bright_magenta"  # SQL parameters
    NUMBER = "bright_white"  # Large metric numbers
    LABEL = "bright_black"  # Metric labels

    # === Status Colors (traffic light) ===
    STATUS_ACTIVE = "bright_green"  # Online, running, active
    STATUS_PENDING = "yellow"  # In progress, on hold
    STATUS_CRITICAL = "bright_red"  # Critical, requires attention
    STATUS_INACTIVE = "bright_black"  # Offline, disabled

    # === Performance Colors ===
    PERF_FAST = "bright_green"  # Fast execution (<100ms)
    PERF_MODERATE = "yellow"  # Moderate execution (100-1000ms)
    PERF_SLOW = "bright_red"  # Slow execution (>1000ms)

    # === Priority Colors ===
    PRIORITY_CRITICAL = "bright_red"
    PRIORITY_HIGH = "red"
    PRIORITY_MEDIUM = "yellow"
    PRIORITY_LOW = "bright_black"

    # === Syntax Highlighting ===
    SQL_THEME = "monokai"


class _LightColors:
    """High-contrast palette for light terminals."""

    # === Primary Colors ===
    PRIMARY = "#00796B"
    SECONDARY = "#00695C"

    # === Semantic Colors ===
    SUCCESS = "#2E7D32"
    WARNING = "#BF360C"
    ERROR = "#C62828"
    INFO = "#1565C0"

    # === Text Colors ===
    TEXT = "#1A1A1A"
    TEXT_DIM = "#616161"
    MUTED = "#9E9E9E"
    ACCENT = "#6A1B9A"

    # === Data Colors ===
    HIGHLIGHT = "#00838F"
    PARAM = "#6A1B9A"
    NUMBER = "#212121"
    LABEL = "#757575"

    # === Status Colors (traffic light) ===
    STATUS_ACTIVE = "#2E7D32"
    STATUS_PENDING = "#BF360C"
    STATUS_CRITICAL = "#C62828"
    STATUS_INACTIVE = "#9E9E9E"

    # === Performance Colors ===
    PERF_FAST = "#2E7D32"
    PERF_MODERATE = "#BF360C"
    PERF_SLOW = "#C62828"

    # === Priority Colors ===
    PRIORITY_CRITICAL = "#C62828"
    PRIORITY_HIGH = "#AD1457"
    PRIORITY_MEDIUM = "#BF360C"
    PRIORITY_LOW = "#9E9E9E"

    # === Syntax Highlighting ===
    SQL_THEME = "tango"


def _read_theme_override(env_name: str) -> Optional[ThemeName]:
    """Return an explicit theme override, treating auto/invalid values as unset."""
    configured_theme = os.getenv(env_name, "").strip().lower()
    if configured_theme in {"light", "dark"}:
        return configured_theme
    return None


def _detect_theme_from_colorfgbg() -> Optional[ThemeName]:
    """Interpret COLORFGBG as a legacy background hint when present."""
    colorfgbg = os.getenv("COLORFGBG", "").strip()
    if not colorfgbg:
        return None

    parts = [part.strip() for part in colorfgbg.split(";") if part.strip()]
    if not parts:
        return None

    try:
        background = int(parts[-1])
    except ValueError:
        return None

    return "light" if background >= 7 else "dark"


def _normalize_rgb_channel(channel: str) -> Optional[float]:
    try:
        value = int(channel, 16)
    except ValueError:
        return None

    max_value = (16 ** len(channel)) - 1
    if max_value <= 0:
        return None

    return value / max_value


def _classify_background_rgb(red: float, green: float, blue: float) -> ThemeName:
    """Classify a terminal background using perceived luminance."""
    brightness = (0.299 * red) + (0.587 * green) + (0.114 * blue)
    return "light" if brightness >= _LIGHT_BACKGROUND_THRESHOLD else "dark"


def _detect_theme_from_osc_11_response(response: str) -> Optional[ThemeName]:
    match = _OSC_11_RESPONSE_RE.search(response)
    if match is None:
        return None

    channels = [_normalize_rgb_channel(channel) for channel in match.groups()]
    if any(channel is None for channel in channels):
        return None

    red, green, blue = channels
    return _classify_background_rgb(red, green, blue)


def _detect_theme_from_terminal() -> Optional[ThemeName]:
    """Query the active terminal background color via OSC 11 when available."""
    # A server (e.g. `rdst web`) has no interactive terminal to theme, and under
    # --reload its respawned workers would race each other on /dev/tty. The web
    # entrypoint sets this so worker imports never open the terminal.
    if os.getenv("RDST_NO_TTY_THEME_PROBE"):
        return None
    try:
        if not sys.stdout.isatty() or os.name != "posix":
            return None
    except Exception:
        return None

    try:
        import termios
        import tty
    except ImportError:
        return None

    tty_stream = None
    saved_state = None
    try:
        tty_stream = open("/dev/tty", "r+b", buffering=0)
        tty_fd = tty_stream.fileno()
        saved_state = termios.tcgetattr(tty_fd)
        tty.setcbreak(tty_fd)
        os.set_blocking(tty_fd, False)
        os.write(tty_fd, _OSC_11_QUERY)

        response_parts: list[str] = []
        deadline = time.monotonic() + _OSC_11_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([tty_fd], [], [], max(remaining, 0))
            if not ready:
                break

            try:
                chunk = os.read(tty_fd, 256)
            except BlockingIOError:
                # select() reported the fd readable, but another reader (e.g. a
                # concurrent reload worker) drained it first. Bail rather than
                # block or spin the deadline out on a raced read.
                break
            if not chunk:
                break

            response_parts.append(chunk.decode("ascii", errors="ignore"))
            decoded_buffer = "".join(response_parts)
            detected_theme = _detect_theme_from_osc_11_response(decoded_buffer)
            if detected_theme is not None:
                return detected_theme

            if decoded_buffer.endswith("\x07") or decoded_buffer.endswith("\x1b\\"):
                break

        return None
    except Exception:
        return None
    finally:
        if saved_state is not None and tty_stream is not None:
            try:
                termios.tcsetattr(tty_stream.fileno(), termios.TCSADRAIN, saved_state)
            except Exception:
                pass
        if tty_stream is not None:
            try:
                tty_stream.close()
            except Exception:
                pass


def _detect_theme() -> ThemeName:
    """Resolve the active terminal theme from overrides, terminal probing, or hints."""
    configured_theme = _read_theme_override("RDST_THEME")
    if configured_theme is not None:
        return configured_theme

    cli_theme = _read_theme_override("CLITHEME")
    if cli_theme is not None:
        return cli_theme

    terminal_theme = _detect_theme_from_terminal()
    if terminal_theme is not None:
        return terminal_theme

    colorfgbg_theme = _detect_theme_from_colorfgbg()
    if colorfgbg_theme is not None:
        return colorfgbg_theme

    return "dark"


_active_theme: ThemeName = _detect_theme()
Colors = _LightColors if _active_theme == "light" else _DarkColors


def get_theme() -> ThemeName:
    """Return the resolved RDST terminal theme name."""
    return _active_theme


class StyleTokens:
    """Compound styles for specific UI elements derived from the active palette."""

    TEXT = Colors.TEXT
    TEXT_DIM = Colors.TEXT_DIM
    MUTED = Colors.MUTED
    EMPHASIS = f"bold {Colors.TEXT}"
    EMPHASIS_MUTED = f"bold {Colors.TEXT_DIM}"

    PRIMARY = Colors.PRIMARY
    SECONDARY = Colors.SECONDARY
    SUCCESS = Colors.SUCCESS
    WARNING = Colors.WARNING
    ERROR = Colors.ERROR
    INFO = Colors.INFO
    ACCENT = Colors.ACCENT

    HIGHLIGHT = Colors.HIGHLIGHT
    PARAM = Colors.PARAM
    NUMBER = Colors.NUMBER
    LABEL = Colors.LABEL

    STATUS_ACTIVE = Colors.STATUS_ACTIVE
    STATUS_PENDING = Colors.STATUS_PENDING
    STATUS_CRITICAL = Colors.STATUS_CRITICAL
    STATUS_INACTIVE = Colors.STATUS_INACTIVE

    PRIORITY_CRITICAL = Colors.PRIORITY_CRITICAL
    PRIORITY_HIGH = Colors.PRIORITY_HIGH
    PRIORITY_MEDIUM = Colors.PRIORITY_MEDIUM
    PRIORITY_LOW = Colors.PRIORITY_LOW

    PERF_FAST = Colors.PERF_FAST
    PERF_MODERATE = Colors.PERF_MODERATE
    PERF_SLOW = Colors.PERF_SLOW

    SQL_THEME = Colors.SQL_THEME

    # === Headers & Titles ===
    HEADER = f"bold {Colors.PRIMARY}"
    SUBHEADER = f"bold {Colors.TEXT_DIM}"
    TITLE = f"bold {Colors.NUMBER}"
    SECTION_TITLE = f"bold {Colors.PRIMARY} reverse"

    # === Table Styles ===
    TABLE_HEADER = f"bold {Colors.SECONDARY}"
    TABLE_BORDER = Colors.TEXT_DIM
    TABLE_ROW_KEY = Colors.PRIMARY
    TABLE_ROW_VALUE = Colors.TEXT

    # === Panel Styles ===
    PANEL_BORDER = Colors.TEXT_DIM
    PANEL_SUCCESS = Colors.SUCCESS
    PANEL_WARNING = Colors.WARNING
    PANEL_ERROR = Colors.ERROR
    PANEL_INFO = Colors.PRIMARY
    PANEL_TITLE = f"bold {Colors.PRIMARY}"

    # === Interactive Styles ===
    PROMPT = f"bold {Colors.PRIMARY}"
    CHOICE_ACTIVE = Colors.SUCCESS
    CHOICE_INACTIVE = Colors.TEXT_DIM
    HINT = Colors.TEXT_DIM

    # === Status Styles ===
    STATUS_SUCCESS = f"bold {Colors.STATUS_ACTIVE}"
    STATUS_WARNING = f"bold {Colors.WARNING}"
    STATUS_ERROR = f"bold {Colors.STATUS_CRITICAL}"
    STATUS_INFO = f"bold {Colors.PRIMARY}"
    STATUS_ONLINE = f"bold {Colors.STATUS_ACTIVE}"
    STATUS_OFFLINE = f"bold {Colors.STATUS_INACTIVE}"

    # === Code Styles ===
    CODE = Colors.PRIMARY
    SQL = Colors.TEXT
    COMMAND = Colors.PRIMARY

    # === Data Styles ===
    HASH = Colors.HIGHLIGHT
    DURATION = Colors.TEXT
    DURATION_SLOW = f"bold {Colors.ERROR}"
    COUNT = Colors.NUMBER
    METRIC_VALUE = f"bold {Colors.NUMBER}"
    METRIC_LABEL = Colors.LABEL

    # === Priority/Severity Tags ===
    TAG_CRITICAL = f"bold {Colors.PRIORITY_CRITICAL} reverse"
    TAG_HIGH = f"bold {Colors.PRIORITY_HIGH}"
    TAG_MEDIUM = f"bold {Colors.PRIORITY_MEDIUM}"
    TAG_LOW = Colors.PRIORITY_LOW

    # === Badges/Indicators ===
    BADGE_ACTIVE = f"bold {Colors.STATUS_ACTIVE}"
    BADGE_PENDING = f"bold {Colors.STATUS_PENDING}"
    BADGE_INACTIVE = Colors.STATUS_INACTIVE


THEME_DEFINITION = {
    "primary": Colors.PRIMARY,
    "secondary": Colors.SECONDARY,
    "success": Colors.SUCCESS,
    "warning": Colors.WARNING,
    "error": Colors.ERROR,
    "info": Colors.INFO,
    "text": Colors.TEXT,
    "text.dim": Colors.TEXT_DIM,
    "muted": Colors.MUTED,
    "accent": Colors.ACCENT,
    "highlight": Colors.HIGHLIGHT,
    "param": Colors.PARAM,
    "number": Colors.NUMBER,
    "label": Colors.LABEL,
    "status.active": Colors.STATUS_ACTIVE,
    "status.pending": Colors.STATUS_PENDING,
    "status.critical": Colors.STATUS_CRITICAL,
    "status.inactive": Colors.STATUS_INACTIVE,
    "priority.critical": Colors.PRIORITY_CRITICAL,
    "priority.high": Colors.PRIORITY_HIGH,
    "priority.medium": Colors.PRIORITY_MEDIUM,
    "priority.low": Colors.PRIORITY_LOW,
    "perf.fast": Colors.PERF_FAST,
    "perf.moderate": Colors.PERF_MODERATE,
    "perf.slow": Colors.PERF_SLOW,
    "header": StyleTokens.HEADER,
    "subheader": StyleTokens.SUBHEADER,
    "title": StyleTokens.TITLE,
    "section.title": StyleTokens.SECTION_TITLE,
    "table.header": StyleTokens.TABLE_HEADER,
    "table.border": StyleTokens.TABLE_BORDER,
    "table.key": StyleTokens.TABLE_ROW_KEY,
    "table.value": StyleTokens.TABLE_ROW_VALUE,
    "panel.border": StyleTokens.PANEL_BORDER,
    "panel.success": StyleTokens.PANEL_SUCCESS,
    "panel.warning": StyleTokens.PANEL_WARNING,
    "panel.error": StyleTokens.PANEL_ERROR,
    "panel.info": StyleTokens.PANEL_INFO,
    "panel.title": StyleTokens.PANEL_TITLE,
    "prompt": StyleTokens.PROMPT,
    "choice.active": StyleTokens.CHOICE_ACTIVE,
    "choice.inactive": StyleTokens.CHOICE_INACTIVE,
    "hint": StyleTokens.HINT,
    "status.success": StyleTokens.STATUS_SUCCESS,
    "status.warning": StyleTokens.STATUS_WARNING,
    "status.error": StyleTokens.STATUS_ERROR,
    "status.info": StyleTokens.STATUS_INFO,
    "status.online": StyleTokens.STATUS_ONLINE,
    "status.offline": StyleTokens.STATUS_OFFLINE,
    "code": StyleTokens.CODE,
    "sql": StyleTokens.SQL,
    "command": StyleTokens.COMMAND,
    "hash": StyleTokens.HASH,
    "duration": StyleTokens.DURATION,
    "duration.slow": StyleTokens.DURATION_SLOW,
    "count": StyleTokens.COUNT,
    "metric.value": StyleTokens.METRIC_VALUE,
    "metric.label": StyleTokens.METRIC_LABEL,
    "tag.critical": StyleTokens.TAG_CRITICAL,
    "tag.high": StyleTokens.TAG_HIGH,
    "tag.medium": StyleTokens.TAG_MEDIUM,
    "tag.low": StyleTokens.TAG_LOW,
    "badge.active": StyleTokens.BADGE_ACTIVE,
    "badge.pending": StyleTokens.BADGE_PENDING,
    "badge.inactive": StyleTokens.BADGE_INACTIVE,
    "emphasis": StyleTokens.EMPHASIS,
    "emphasis.muted": StyleTokens.EMPHASIS_MUTED,
}


class Icons:
    """
    Unicode icons - modern emoji-based style for clarity and accessibility.
    """

    # === Status Icons ===
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    ACTIVE = "●"
    INACTIVE = "○"

    # === Indicators ===
    ONLINE = "●"
    OFFLINE = "○"
    CRITICAL = "!"
    PENDING = "◐"

    # === Arrows/Navigation ===
    ARROW_RIGHT = "›"
    ARROW_LEFT = "‹"
    ARROW_UP = "▲"
    ARROW_DOWN = "▼"
    CHEVRON_RIGHT = "»"
    CHEVRON_LEFT = "«"

    # === Separators ===
    BULLET = "•"
    PIPE = "│"
    DOT = "·"

    # === Bars (for metrics) ===
    BAR_FULL = "█"
    BAR_HALF = "▄"
    BAR_EMPTY = "▁"  # Flat design - low block instead of dithered

    # === Brackets ===
    BRACKET_LEFT = "["
    BRACKET_RIGHT = "]"

    # === Section Markers ===
    SECTION = "■"
    SUBSECTION = "▸"

    # === Domain Specific ===
    LIGHTNING = "⚡"
    ROCKET = "🚀"
    BULB = "💡"
    TOOL = "🔧"
    CHART = "📊"
    MEMO = "📝"
    CHECK = "✓"
    CROSS = "✗"
    ARROW = "→"
    UNKNOWN = "?"


class Layout:
    """
    Layout constants for consistent spacing and sizing.
    """

    # === Widths ===
    MAX_WIDTH = 100  # Maximum content width
    PANEL_WIDTH = 65  # Standard panel width
    TABLE_MAX_WIDTH = 120  # Maximum table width

    # === Indentation ===
    INDENT = "  "  # Standard 2-space indent
    INDENT_DEEP = "    "  # Deep indent (4 spaces)

    # === Truncation ===
    SQL_PREVIEW_LENGTH = 80  # SQL truncation for tables
    HASH_DISPLAY_LENGTH = 12  # Hash truncation for display

    # === Box Styles ===
    BOX_DEFAULT = BOX_ROUNDED
    BOX_SIMPLE = BOX_SIMPLE
    BOX_MINIMAL = BOX_MINIMAL
    BOX_HEAVY = BOX_HEAVY


def duration_style(ms: float) -> str:
    """
    Get the appropriate style for a duration value.

    Args:
        ms: Duration in milliseconds

    Returns:
        Style string based on performance thresholds
    """
    if ms < 100:
        return Colors.PERF_FAST
    elif ms < 1000:
        return Colors.PERF_MODERATE
    else:
        return Colors.PERF_SLOW


def impact_style(impact: str) -> str:
    """
    Get the appropriate style for an impact level.

    Args:
        impact: Impact level string (HIGH, MEDIUM, LOW)

    Returns:
        Style string for the impact level
    """
    impact_upper = impact.upper()
    if impact_upper == "HIGH":
        return Colors.SUCCESS
    elif impact_upper == "MEDIUM":
        return Colors.WARNING
    else:
        return Colors.MUTED


def improvement_style(pct: float) -> str:
    """
    Get the appropriate style for an improvement percentage.

    Args:
        pct: Improvement percentage (positive = faster)

    Returns:
        Style string based on improvement
    """
    if pct >= 10:
        return Colors.SUCCESS
    elif pct >= 0:
        return Colors.WARNING
    else:
        return Colors.ERROR


class Tokens:
    colors = Colors()
    styles = StyleTokens()
    layout = Layout()
    icons = Icons()
