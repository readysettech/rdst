"""
RDST Design System - Theme
===========================

Tactical/ops-inspired dark theme with cyan accents.
Inspired by command & control interfaces.
"""

from rich import box

BOX_HEAVY = box.HEAVY
BOX_SQUARE = box.SQUARE
BOX_SIMPLE = box.SIMPLE
BOX_MINIMAL = box.MINIMAL
BOX_ROUNDED = box.ROUNDED


class Colors:
    """
    Core color palette - tactical dark theme with cyan accents.
    """

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


class StyleTokens:
    """
    Compound styles for specific UI elements - tactical ops theme.
    """

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
    TITLE = "bold bright_white"
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
