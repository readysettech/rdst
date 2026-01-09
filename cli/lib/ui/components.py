"""
RDST Design System - Components
================================

React-like reusable UI primitives for consistent output formatting.
Each component returns a Rich renderable (Panel, Table, Text, etc.).

Design Philosophy:
    Components follow atomic design with TRUE composability:
    1. ATOMS: Base primitives (StyledTable, StyledPanel) - raw building blocks
    2. MOLECULES: Intermediate components that add behavior (DataTable, StatusTable)
    3. ORGANISMS: Specialized components for specific domains (QueryTable, RegistryTable)

    Each layer MUST build on the previous layer, not skip to atoms.

Usage:
    from lib.ui.components import QueryPanel, DataTable, MessagePanel
    from lib.ui.console import console

    console.print(QueryPanel(sql, title="Generated SQL"))
    console.print(DataTable(columns, rows))
    console.print(MessagePanel("Query saved!", variant="success"))
    console.print(MessagePanel("Failed to connect", variant="error", hint="Check password"))

Component Hierarchy:
    ATOMS (raw building blocks):
        StyledTable - base table with RDST styling
        StyledPanel - base panel with RDST styling

    MOLECULES (add behavior to atoms):
        DataTable(StyledTable) - generic data display with formatting
        StatusTable(StyledTable) - key-value pairs with status indicators

    ORGANISMS (domain-specific, build on molecules):
        QueryTable(DataTable) - query listings with hash, sql, duration
        RegistryTable(DataTable) - query registry with metadata
        QueryStatsTable(DataTable) - execution statistics
        TopQueryTable(DataTable) - slow query display
        TargetsTable(StatusTable) - database target configuration
        KeyValueTable(StatusTable) - simple key-value display

    PANELS (StyledPanel derivatives):
        MessagePanel - status messages with variants
        QueryPanel - SQL display with syntax highlighting
        EmptyState - no data available
        SectionBox - bordered content sections
        AnalysisHeader - analysis metadata
        MonitorHeader - live monitoring displays
"""

from typing import List, Tuple, Any, Optional, Union, Literal

JustifyMethod = Literal["default", "left", "center", "right", "full"]

from .theme import StyleTokens, Icons, Layout, duration_style

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax
from rich.console import Group


# =============================================================================
# Base Primitives - Building Blocks for All Components
# =============================================================================


class StyledTable:
    """
    ATOM: Base table primitive with RDST styling.
    All molecules and organisms MUST build on this, not Rich Table directly.
    """

    def __init__(
        self,
        title: Optional[str] = None,
        box: Any = None,
        show_header: bool = True,
        show_lines: bool = False,
        caption: Optional[str] = None,
    ):
        self._table = Table(
            title=title,
            box=box or Layout.BOX_DEFAULT,
            border_style=StyleTokens.TABLE_BORDER,
            header_style=StyleTokens.TABLE_HEADER,
            show_header=show_header,
            show_lines=show_lines,
            caption=caption,
        )
        self._columns: List[str] = []
        self._rows: List[List[Any]] = []

    @classmethod
    def create(
        cls,
        title: Optional[str] = None,
        box: Any = None,
        show_header: bool = True,
        show_lines: bool = False,
        caption: Optional[str] = None,
    ) -> Table:
        instance = cls(
            title=title,
            box=box,
            show_header=show_header,
            show_lines=show_lines,
            caption=caption,
        )
        return instance._table

    def add_column(
        self,
        header: str,
        style: Optional[str] = None,
        justify: JustifyMethod = "left",
        width: Optional[int] = None,
        max_width: Optional[int] = None,
        no_wrap: bool = False,
    ) -> "StyledTable":
        self._table.add_column(
            header,
            style=style,
            justify=justify,
            width=width,
            max_width=max_width,
            no_wrap=no_wrap,
        )
        self._columns.append(header)
        return self

    def _add_row(self, *values: Any, **kwargs: Any) -> "StyledTable":
        self._table.add_row(*values, **kwargs)
        self._rows.append(list(values))
        return self

    def set_caption(self, caption: str) -> "StyledTable":
        self._table.caption = caption
        return self

    @property
    def table(self) -> Table:
        return self._table

    @property
    def column_count(self) -> int:
        return len(self._columns)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def __rich__(self):
        return self._table


class StyledPanel:
    """ATOM: Base panel primitive with RDST styling."""

    VARIANTS = {
        "default": (None, StyleTokens.PANEL_BORDER),
        "success": (Icons.SUCCESS, StyleTokens.PANEL_SUCCESS),
        "error": (Icons.ERROR, StyleTokens.PANEL_ERROR),
        "warning": (Icons.WARNING, StyleTokens.PANEL_WARNING),
        "info": (Icons.INFO, StyleTokens.PANEL_INFO),
    }

    def __init__(
        self,
        content: Union[str, Text, Any],
        title: Optional[str] = None,
        variant: str = "default",
        box: Any = None,
        width: Optional[int] = None,
        padding: Tuple[int, int] = (0, 1),
        border_style: Optional[str] = None,
    ):
        self.content = content
        self.title = title
        self.variant = variant
        self.box = box or Layout.BOX_DEFAULT
        self.width = width
        self.padding = padding
        self.border_style = border_style

    @classmethod
    def create(
        cls,
        content: Union[str, Text, Any],
        title: Optional[str] = None,
        variant: str = "default",
        box: Any = None,
        width: Optional[int] = None,
        padding: Tuple[int, int] = (0, 1),
        border_style: Optional[str] = None,
    ) -> Panel:
        instance = cls(content, title, variant, box, width, padding, border_style)
        return instance._build_panel()

    def _get_border_style(self) -> str:
        if self.border_style:
            return self.border_style
        _, border_style = self.VARIANTS.get(self.variant, self.VARIANTS["default"])
        return border_style

    def _format_title(self) -> Optional[str]:
        if not self.title:
            return None

        if self.variant == "error":
            return (
                f"[{StyleTokens.STATUS_ERROR}]{self.title}[/{StyleTokens.STATUS_ERROR}]"
            )
        elif self.variant == "warning":
            return f"[{StyleTokens.STATUS_WARNING}]{self.title}[/{StyleTokens.STATUS_WARNING}]"
        elif self.variant == "success":
            return f"[{StyleTokens.STATUS_SUCCESS}]{self.title}[/{StyleTokens.STATUS_SUCCESS}]"
        else:
            return f"[{StyleTokens.HEADER}]{self.title}[/{StyleTokens.HEADER}]"

    def _build_panel(self) -> Panel:
        return Panel(
            self.content,
            title=self._format_title(),
            border_style=self._get_border_style(),
            box=self.box,
            width=self.width,
            padding=self.padding,
        )

    def __rich__(self):
        return self._build_panel()


# =============================================================================
# Message Components (built on StyledPanel)
# =============================================================================


def MessagePanel(
    content: str,
    variant: str = "info",
    title: Optional[str] = None,
    hint: Optional[str] = None,
) -> "StyledPanel":
    """
    Unified message panel component - the atom for all message types.

    Args:
        content: Message content
        variant: "success" | "error" | "warning" | "info"
        title: Optional panel title (defaults vary by variant)
        hint: Optional hint text (shown below content)

    Returns:
        Rich Panel configured for the specified variant

    Examples:
        MessagePanel("Query saved!", variant="success")
        MessagePanel("Connection failed", variant="error", hint="Check password")
        MessagePanel("Slow query detected", variant="warning")
        MessagePanel("Processing...", variant="info")
    """
    variant_config = {
        "success": (Icons.SUCCESS, StyleTokens.SUCCESS, None),
        "error": (Icons.ERROR, StyleTokens.ERROR, "Error"),
        "warning": (Icons.WARNING, StyleTokens.WARNING, "Warning"),
        "info": (Icons.INFO, StyleTokens.INFO, None),
    }

    icon, color, default_title = variant_config.get(variant, variant_config["info"])
    display_title = title if title is not None else default_title

    if variant == "success":
        body = f"[{color}]{icon} {content}[/{color}]"
    else:
        body = f"[{color}]{content}[/{color}]"

    if hint:
        body += f"\n\n[{StyleTokens.MUTED}]Hint: {hint}[/{StyleTokens.MUTED}]"

    return StyledPanel(body, title=display_title, variant=variant, padding=(0, 1))


def NoticePanel(
    title: str,
    description: str,
    variant: str = "warning",
    bullets: Optional[List[str]] = None,
    bullets_header: Optional[str] = None,
    action_hint: Optional[str] = None,
    action_command: Optional[str] = None,
) -> "StyledPanel":
    """
    Structured notice panel with description, bullet points, and action hints.

    Use for skip notices, informational warnings, and structured error explanations.
    Builds on MessagePanel pattern but supports richer content structure.

    Args:
        title: Panel title (e.g., "REWRITE TESTING SKIPPED")
        description: Main explanation text
        variant: "warning" | "error" | "info" | "success"
        bullets: Optional list of bullet point strings
        bullets_header: Header text before bullets (e.g., "This typically happens when:")
        action_hint: Action suggestion text (e.g., "To test rewrites with actual execution times:")
        action_command: Command example (e.g., 'rdst analyze --query "SELECT ..."')

    Returns:
        Rich Panel configured for the notice type

    Examples:
        NoticePanel(
            title="REWRITE TESTING SKIPPED",
            description="This query contains parameter placeholders without actual values.",
            variant="warning",
            bullets=["Query was captured from rdst top", "Query was normalized from performance_schema"],
            bullets_header="This typically happens when:",
            action_hint="To test rewrites with actual execution times:",
            action_command='rdst analyze --query "SELECT ... WHERE id = 123"'
        )
    """
    variant_config = {
        "success": (Icons.SUCCESS, StyleTokens.SUCCESS),
        "error": (Icons.ERROR, StyleTokens.ERROR),
        "warning": (Icons.WARNING, StyleTokens.WARNING),
        "info": (Icons.INFO, StyleTokens.INFO),
    }

    icon, color = variant_config.get(variant, variant_config["warning"])

    parts = [f"[{color}]{description}[/{color}]"]

    if bullets:
        parts.append("")
        if bullets_header:
            parts.append(f"[{StyleTokens.MUTED}]{bullets_header}[/{StyleTokens.MUTED}]")
        for bullet in bullets:
            parts.append(f"  [{StyleTokens.MUTED}]•[/{StyleTokens.MUTED}] {bullet}")

    if action_hint:
        parts.append("")
        parts.append(
            f"[{StyleTokens.SECONDARY}]{action_hint}[/{StyleTokens.SECONDARY}]"
        )
        if action_command:
            parts.append(
                f"  [{StyleTokens.ACCENT}]{action_command}[/{StyleTokens.ACCENT}]"
            )

    body = "\n".join(parts)
    formatted_title = f"{icon}  {title}"

    return StyledPanel(body, title=formatted_title, variant=variant, padding=(1, 2))


# =============================================================================
# Metric Components (tactical dashboard style)
# =============================================================================


class MetricCard:
    """MOLECULE: Large metric display card with value and label."""

    def __init__(self, value: str, label: str, trend: Optional[str] = None):
        self.value = value
        self.label = label
        self.trend = trend

    def __rich__(self):
        content = Text()
        content.append(f"{self.value}\n", style=StyleTokens.METRIC_VALUE)
        content.append(self.label, style=StyleTokens.METRIC_LABEL)
        if self.trend:
            trend_style = (
                StyleTokens.SUCCESS if self.trend.startswith("+") else StyleTokens.ERROR
            )
            content.append(f"  {self.trend}", style=trend_style)
        return Panel(
            content,
            box=Layout.BOX_DEFAULT,
            border_style=StyleTokens.PANEL_BORDER,
            padding=(0, 1),
        )


class MetricRow:
    """MOLECULE: Horizontal row of metric cards."""

    def __init__(self):
        self._metrics: List[Tuple[str, str, Optional[str]]] = []

    def add_metric(
        self, value: str, label: str, trend: Optional[str] = None
    ) -> "MetricRow":
        self._metrics.append((value, label, trend))
        return self

    def __rich__(self):
        from rich.columns import Columns

        cards = []
        for value, label, trend in self._metrics:
            cards.append(MetricCard(value, label, trend))
        return Columns(cards, equal=True, expand=True)


class StatusBadge:
    """MOLECULE: Inline status badge with color indicator."""

    VARIANTS = {
        "active": (Icons.ACTIVE, StyleTokens.STATUS_ACTIVE),
        "online": (Icons.ONLINE, StyleTokens.STATUS_ACTIVE),
        "pending": (Icons.PENDING, StyleTokens.STATUS_PENDING),
        "critical": (Icons.CRITICAL, StyleTokens.STATUS_CRITICAL),
        "inactive": (Icons.INACTIVE, StyleTokens.STATUS_INACTIVE),
        "offline": (Icons.OFFLINE, StyleTokens.STATUS_INACTIVE),
    }

    def __init__(self, label: str, variant: str = "active"):
        self.label = label
        self.variant = variant

    def __rich__(self):
        icon, color = self.VARIANTS.get(self.variant, self.VARIANTS["inactive"])
        return Text(f"{icon} {self.label}", style=color)


class PriorityTag:
    """MOLECULE: Priority/severity tag with appropriate styling."""

    def __init__(self, priority: str):
        self.priority = priority.upper()

    def __rich__(self):
        style_map = {
            "CRITICAL": StyleTokens.TAG_CRITICAL,
            "HIGH": StyleTokens.TAG_HIGH,
            "MEDIUM": StyleTokens.TAG_MEDIUM,
            "LOW": StyleTokens.TAG_LOW,
        }
        style = style_map.get(self.priority, StyleTokens.TAG_LOW)
        return Text(f" {self.priority} ", style=style)


class ProgressBar:
    """MOLECULE: Text-based progress bar for terminal."""

    def __init__(
        self,
        value: float,
        max_value: float = 100,
        width: int = 20,
        label: Optional[str] = None,
    ):
        self.value = value
        self.max_value = max_value
        self.width = width
        self.label = label

    def __rich__(self):
        pct = min(self.value / self.max_value, 1.0) if self.max_value > 0 else 0
        filled = int(pct * self.width)
        empty = self.width - filled

        if pct >= 0.9:
            bar_color = StyleTokens.SUCCESS
        elif pct >= 0.5:
            bar_color = StyleTokens.WARNING
        else:
            bar_color = StyleTokens.ERROR

        text = Text()
        text.append(Icons.BAR_FULL * filled, style=bar_color)
        text.append(Icons.BAR_EMPTY * empty, style=StyleTokens.TEXT_DIM)
        text.append(f" {self.value:.1f}%", style=StyleTokens.TEXT)
        if self.label:
            text.append(f" {self.label}", style=StyleTokens.TEXT_DIM)
        return text


def MetricPanel(
    metrics: List[Tuple[str, str]],
    title: Optional[str] = None,
) -> "StyledPanel":
    """MOLECULE: Panel containing multiple metrics in a grid."""
    content = Text()
    for i, (value, label) in enumerate(metrics):
        if i > 0:
            content.append("  │  ", style=StyleTokens.TEXT_DIM)
        content.append(value, style=StyleTokens.METRIC_VALUE)
        content.append(f" {label}", style=StyleTokens.METRIC_LABEL)
    return StyledPanel(content, title=title)


# =============================================================================
# SQL Components
# =============================================================================


def QueryPanel(
    sql: str, title: str = "Query", border_style: Optional[str] = None
) -> "StyledPanel":
    """
    SQL query display with syntax highlighting.

    Args:
        sql: SQL query string
        title: Panel title (default: "Query")
        border_style: Override border style

    Returns:
        Rich Panel with syntax-highlighted SQL
    """
    syntax = Syntax(
        sql.strip(),
        "sql",
        theme=StyleTokens.SQL_THEME,
        word_wrap=True,
        background_color="default",
    )

    return StyledPanel(syntax, title=title, border_style=border_style)


def SQLPreview(
    sql: str, explanation: str, confidence: float, warnings: Optional[List[str]] = None
) -> "Group":
    """
    SQL preview with explanation and confidence score.

    Args:
        sql: Generated SQL query
        explanation: Plain English explanation
        confidence: Confidence score (0.0-1.0)
        warnings: Optional list of warnings

    Returns:
        Rich Group containing SQL panel and metadata
    """
    warnings = warnings or []

    # Determine confidence color
    if confidence >= 0.9:
        conf_style = StyleTokens.SUCCESS
    elif confidence >= 0.7:
        conf_style = StyleTokens.WARNING
    else:
        conf_style = StyleTokens.ERROR

    # Build components
    sql_panel = QueryPanel(sql, title="Generated SQL")

    info_text = Text()
    info_text.append("\nExplanation: ", style="bold")
    info_text.append(explanation)
    info_text.append("\n\nConfidence: ", style="bold")
    info_text.append(f"{confidence * 100:.1f}%", style=conf_style)

    if warnings:
        info_text.append("\n\nWarnings:", style=StyleTokens.STATUS_WARNING)
        for warning in warnings:
            info_text.append(
                f"\n  {Icons.WARNING} {warning}", style=StyleTokens.WARNING
            )

    return Group(sql_panel, info_text)


def InlineSQL(sql: str, max_length: int = 80) -> "Text":
    """
    Inline SQL for tables (truncated, no syntax highlighting).

    Args:
        sql: SQL query string
        max_length: Maximum display length

    Returns:
        Rich Text with SQL (truncated if needed)
    """
    # Normalize whitespace
    normalized = " ".join(sql.split())

    if len(normalized) > max_length:
        normalized = normalized[: max_length - 3] + "..."

    return Text(normalized, style=StyleTokens.SQL)


# =============================================================================
# MOLECULES - Intermediate components adding behavior to atoms
# =============================================================================


class DataTableBase(StyledTable):
    """MOLECULE: Extends StyledTable with data formatting and cell processing."""

    def __init__(
        self,
        title: Optional[str] = None,
        box: Any = None,
        show_header: bool = True,
        show_row_numbers: bool = False,
    ):
        super().__init__(title=title, box=box, show_header=show_header)
        self._show_row_numbers = show_row_numbers
        self._row_number = 0
        if show_row_numbers:
            self.add_column("#", style=StyleTokens.MUTED, width=4)

    def add_row(self, *values: Any, **kwargs: Any) -> "DataTableBase":
        self._row_number += 1
        formatted = [_format_cell(v) for v in values]
        if self._show_row_numbers:
            formatted = [str(self._row_number)] + formatted
        self._add_row(*formatted, **kwargs)
        return self

    def add_rows(self, rows: List[Tuple]) -> "DataTableBase":
        for row in rows:
            self.add_row(*row)
        return self


def DataTable(
    columns: List[str],
    rows: List[Tuple],
    title: Optional[str] = None,
    show_row_numbers: bool = False,
    justifications: Optional[List[JustifyMethod]] = None,
) -> "Table":
    """MOLECULE: Standard data table with formatting. Builds on StyledTable."""
    table = DataTableBase(title=title, show_row_numbers=show_row_numbers)
    for i, col in enumerate(columns):
        justify = (
            justifications[i] if justifications and i < len(justifications) else "left"
        )
        table.add_column(col, justify=justify)
    table.add_rows(rows)
    return table.table


class StatusTableBase(StyledTable):
    """MOLECULE: Key-value table with status indicators. Builds on StyledTable."""

    def __init__(
        self,
        title: Optional[str] = None,
        box: Any = None,
        show_header: bool = False,
        key_style: Optional[str] = None,
        value_style: Optional[str] = None,
    ):
        super().__init__(
            title=title, box=box or Layout.BOX_DEFAULT, show_header=show_header
        )
        self.add_column("Key", style=key_style or StyleTokens.TABLE_ROW_KEY)
        self.add_column("Value", style=value_style or StyleTokens.TABLE_ROW_VALUE)

    def add_status(
        self, key: str, value: Any, value_style: Optional[str] = None
    ) -> "StatusTableBase":
        formatted_value = _format_cell(value)
        if value_style:
            formatted_value = Text(formatted_value, style=value_style)
        self._add_row(str(key), formatted_value)
        return self

    def add_statuses(self, data: dict) -> "StatusTableBase":
        for key, value in data.items():
            self.add_status(key, value)
        return self


def KeyValueTable(data: dict, title: Optional[str] = None) -> "Table":
    """MOLECULE: Simple key-value display. Builds on StatusTableBase."""
    table = StatusTableBase(title=title, show_header=True)
    table.add_statuses(data)
    return table.table


# =============================================================================
# ORGANISMS - Domain-specific components building on molecules
# =============================================================================


class QueryTableBase(DataTableBase):
    """ORGANISM: Query listing table. Builds on DataTableBase."""

    COLUMN_PRESETS = {
        "hash": ("Hash", StyleTokens.HASH, 12, None),
        "name": ("Name", StyleTokens.TABLE_ROW_KEY, None, None),
        "sql": ("Query", StyleTokens.SQL, None, 50),
        "target": ("Target", StyleTokens.INFO, None, None),
        "duration": ("Duration", None, None, None),
    }

    def __init__(
        self,
        title: Optional[str] = None,
        show_target: bool = True,
        show_duration: bool = True,
    ):
        super().__init__(title=title)
        self._show_target = show_target
        self._show_duration = show_duration
        self._setup_columns()

    def _setup_columns(self):
        self._add_preset_column("hash")
        self._add_preset_column("name")
        self._add_preset_column("sql")
        if self._show_target:
            self._add_preset_column("target")
        if self._show_duration:
            self.add_column("Duration", justify="right")

    def _add_preset_column(self, preset: str):
        header, style, width, max_width = self.COLUMN_PRESETS[preset]
        self.add_column(header, style=style, width=width, max_width=max_width)

    def add_query(self, query: dict) -> "QueryTableBase":
        row: List[Any] = [
            query.get("hash", "")[:12],
            query.get("name", "-"),
            _truncate(query.get("sql", ""), 50),
        ]
        if self._show_target:
            row.append(query.get("target", "-"))
        if self._show_duration:
            ms = query.get("duration_ms")
            if ms is not None:
                row.append(Text(f"{ms:.1f}ms", style=duration_style(ms)))
            else:
                row.append("-")
        self.add_row(*row)
        return self

    def add_queries(self, queries: List[dict]) -> "QueryTableBase":
        for q in queries:
            self.add_query(q)
        return self


def QueryTable(
    queries: List[dict],
    show_target: bool = True,
    show_duration: bool = True,
) -> "Table":
    """ORGANISM: Query listing display. Builds on DataTableBase."""
    table = QueryTableBase(show_target=show_target, show_duration=show_duration)
    table.add_queries(queries)
    return table.table


# =============================================================================
# Header Components
# =============================================================================


def SectionHeader(title: str, icon: Optional[str] = None) -> "Text":
    text = Text()
    if icon:
        text.append(f"{icon} ")
    text.append(title.upper(), style=StyleTokens.HEADER)
    return text


class HeaderPanelBase(StyledPanel):
    """MOLECULE: Base for header panels with metadata display. Extends StyledPanel."""

    def __init__(self, title: Optional[str] = None):
        self._text = Text()
        self._panel_title = title
        super().__init__(content=self._text, title=title)

    def add_field(
        self, label: str, value: str, value_style: Optional[str] = None
    ) -> "HeaderPanelBase":
        if self._text.plain:
            self._text.append("\n")
        self._text.append(f"{label}: ", style="bold")
        self._text.append(value, style=value_style or StyleTokens.TEXT)
        self.content = self._text
        return self

    def add_line(self, text: str, style: Optional[str] = None) -> "HeaderPanelBase":
        if self._text.plain:
            self._text.append("\n")
        self._text.append(text, style=style)
        self.content = self._text
        return self

    def add_warning(self, message: str) -> "HeaderPanelBase":
        self._text.append("\n\n")
        self._text.append(f"{Icons.WARNING} ", style=f"bold {StyleTokens.WARNING}")
        self._text.append(message, style=StyleTokens.WARNING)
        self.content = self._text
        return self

    def add_hint(self, hint: str) -> "HeaderPanelBase":
        self._text.append("\n\n")
        self._text.append(hint, style=StyleTokens.SECONDARY)
        self.content = self._text
        return self


class AnalysisHeaderPanel(HeaderPanelBase):
    """ORGANISM: Analysis header with target, engine, LLM info. Builds on HeaderPanelBase."""

    def __init__(self, target: str, engine: str):
        super().__init__(title="RDST Query Analysis")
        self.add_field("Target", target, StyleTokens.INFO)
        self.add_field(
            "Engine", engine.upper() if engine else "Unknown", StyleTokens.ACCENT
        )

    def with_analysis_id(self, analysis_id: str) -> "AnalysisHeaderPanel":
        self.add_field("Analysis ID", analysis_id[:12], StyleTokens.HASH)
        return self

    def with_llm_info(
        self, model: str, tokens: int, cost: float
    ) -> "AnalysisHeaderPanel":
        model_short = (
            model.replace("claude-", "")
            .replace("-20250514", "")
            .replace("-20250929", "")
        )
        self._text.append("\n")
        self._text.append("LLM: ", style="bold")
        self._text.append(f"{model_short} ", style=StyleTokens.SECONDARY)
        self._text.append(f"({tokens:,} tokens, ~${cost:.3f})", style=StyleTokens.MUTED)
        self.content = self._text
        return self


def AnalysisHeader(
    target: str,
    engine: str,
    analysis_id: Optional[str] = None,
    llm_info: Optional[dict] = None,
) -> "Panel":
    """ORGANISM: Analysis header display. Builds on HeaderPanelBase."""
    header = AnalysisHeaderPanel(target, engine)
    if analysis_id:
        header.with_analysis_id(analysis_id)
    if llm_info:
        model = llm_info.get("model", "")
        if model:
            header.with_llm_info(
                model, llm_info.get("tokens", 0), llm_info.get("cost", 0)
            )
    return header._build_panel()


# =============================================================================
# Progress/Status Components
# =============================================================================


def StatusLine(label: str, value: str, style: Optional[str] = None) -> "Text":
    """
    Single status line with label and value.

    Args:
        label: Status label
        value: Status value
        style: Optional style for the value

    Returns:
        Rich Text with formatted status line
    """
    text = Text()
    text.append(f"{label}: ", style="bold")
    if style:
        text.append(value, style=style)
    else:
        text.append(value)
    return text


def DurationDisplay(ms: float, label: str = "Duration") -> "Text":
    """
    Duration display with automatic color coding.

    Args:
        ms: Duration in milliseconds
        label: Label text (default: "Duration")

    Returns:
        Rich Text with color-coded duration
    """
    return StatusLine(label, f"{ms:.1f}ms", style=duration_style(ms))


def Banner(
    title: str,
    width: int = 80,
    style: str = "=",
) -> "Rule":
    """
    Section banner with border lines.

    Args:
        title: Banner title
        width: Total width (ignored, uses console width)
        style: Border character (ignored, uses theme style)

    Returns:
        Rich Rule with consistent styling
    """
    return Rule(title, style=StyleTokens.HEADER)


class TopQueryTableBase(DataTableBase):
    """ORGANISM: Slow query monitor table. Builds on DataTableBase."""

    def __init__(
        self, source: str, target_name: str, db_engine: str, title: Optional[str] = None
    ):
        display_title = title or f"Top queries: {target_name} ({db_engine}) - {source}"
        super().__init__(title=display_title)
        self._source = source
        self._setup_columns()

    def _setup_columns(self):
        self.add_column("QUERY HASH", style=StyleTokens.HASH, width=12)
        self.add_column("QUERY", style=StyleTokens.SQL, width=15)
        self.add_column("FREQ", style=StyleTokens.SUCCESS, width=7, justify="right")
        self.add_column(
            "TOTAL TIME", style=StyleTokens.WARNING, width=10, justify="right"
        )
        self.add_column("AVG TIME", style=StyleTokens.WARNING, width=8, justify="right")
        self.add_column("% LOAD", style=StyleTokens.ERROR, width=6, justify="right")
        self.add_column("SOURCE", style=StyleTokens.ACCENT, width=8)
        self.add_column("READYSET", style=StyleTokens.INFO, width=8)

    def add_slow_query(self, query: dict) -> "TopQueryTableBase":
        self.add_row(
            query.get("query_hash", "")[:12],
            _truncate(query.get("query_text", ""), 15),
            str(query.get("freq", 0)),
            query.get("total_time", "-"),
            query.get("avg_time", "-"),
            query.get("pct_load", "-"),
            self._source,
            "N/A",
        )
        return self

    def add_slow_queries(self, queries: List[dict]) -> "TopQueryTableBase":
        for q in queries:
            self.add_slow_query(q)
        return self


def TopQueryTable(
    queries: List[dict],
    source: str,
    target_name: str,
    db_engine: str,
    title: Optional[str] = None,
) -> "Table":
    """ORGANISM: Slow query display for rdst top. Builds on DataTableBase."""
    table = TopQueryTableBase(source, target_name, db_engine, title)
    table.add_slow_queries(queries)
    return table.table


class QueryStatsTableBase(DataTableBase):
    """ORGANISM: Query execution statistics. Builds on DataTableBase."""

    def __init__(
        self,
        title: str = "Query Run Summary",
        show_qps: bool = False,
        show_percentiles: bool = True,
    ):
        super().__init__(title=title)
        self._show_qps = show_qps
        self._show_percentiles = show_percentiles
        self._elapsed: float = 1.0
        self._setup_columns()

    def _setup_columns(self):
        self.add_column("Query", style=StyleTokens.TABLE_ROW_KEY)
        self.add_column("Execs", justify="right")
        self.add_column("OK", justify="right", style=StyleTokens.SUCCESS)
        self.add_column("Fail", justify="right", style=StyleTokens.ERROR)

        if self._show_percentiles:
            self.add_column("Min", justify="right")
            self.add_column("Avg", justify="right")
            self.add_column("p95", justify="right")
            self.add_column("Max", justify="right")
        else:
            self.add_column("Avg", justify="right")

        if self._show_qps:
            self.add_column("QPS", justify="right", style=StyleTokens.WARNING)

    def set_elapsed(self, elapsed: float) -> "QueryStatsTableBase":
        self._elapsed = elapsed
        return self

    def _extract_field(self, obj: Any, field: str, default: Any = None) -> Any:
        val = getattr(obj, field, None)
        if val is None and isinstance(obj, dict):
            val = obj.get(field, default)
        return val if val is not None else default

    def add_query_stats(self, qs: Any) -> "QueryStatsTableBase":
        query_name = self._extract_field(qs, "query_name") or self._extract_field(
            qs, "name", ""
        )
        executions = self._extract_field(qs, "executions", 0)
        successes = self._extract_field(qs, "successes", 0)
        failures = self._extract_field(qs, "failures", 0)
        has_timings = hasattr(qs, "timings_ms") and qs.timings_ms

        row: List[Any] = [
            str(query_name),
            str(executions),
            str(successes),
            str(failures),
        ]

        if self._show_percentiles:
            row.extend(
                [
                    f"{qs.min_ms:.1f}ms" if has_timings else "-",
                    f"{qs.avg_ms:.1f}ms" if has_timings else "-",
                    f"{qs.p95_ms:.1f}ms" if has_timings else "-",
                    f"{qs.max_ms:.1f}ms" if has_timings else "-",
                ]
            )
        else:
            row.append(f"{qs.avg_ms:.1f}ms" if has_timings else "-")

        if self._show_qps:
            qps = successes / self._elapsed if self._elapsed > 0 else 0
            row.append(f"{qps:.1f}")

        self.add_row(*row)
        return self


def QueryStatsTable(
    stats: Any,
    title: str = "Query Run Summary",
    show_qps: bool = False,
    show_percentiles: bool = True,
    show_caption: bool = False,
) -> "Table":
    """ORGANISM: Query execution statistics display. Builds on DataTableBase."""
    table = QueryStatsTableBase(
        title=title, show_qps=show_qps, show_percentiles=show_percentiles
    )

    elapsed = getattr(stats, "elapsed_seconds", None)
    if elapsed is None:
        elapsed = stats.get("elapsed_seconds", 1) if isinstance(stats, dict) else 1
    table.set_elapsed(elapsed)

    query_stats = getattr(stats, "query_stats", None)
    if query_stats is None:
        query_stats = stats.get("query_stats", {}) if isinstance(stats, dict) else {}

    for qs in query_stats.values():
        table.add_query_stats(qs)

    if show_caption:
        total_execs = getattr(stats, "total_executions", None)
        if total_execs is None:
            total_execs = (
                stats.get("total_executions", 0) if isinstance(stats, dict) else 0
            )
        table.set_caption(
            f"Elapsed: {elapsed:.1f}s | Total: {total_execs:,} | Ctrl+C to stop"
        )

    return table.table


class TargetsTableBase(DataTableBase):
    """ORGANISM: Database targets table. Builds on DataTableBase."""

    ENGINE_DISPLAY = {"postgresql": "PostgreSQL", "mysql": "MySQL"}
    PROXY_DISPLAY = {
        "none": "None",
        "readyset": "Readyset",
        "proxysql": "ProxySQL",
        "pgbouncer": "PgBouncer",
        "tunnel": "SSH Tunnel",
        "custom": "Custom",
    }

    def __init__(
        self, title: str = "Database Targets", default_target: Optional[str] = None
    ):
        super().__init__(title=title)
        self._default_target = default_target
        self._setup_columns()

    def _setup_columns(self):
        self.add_column("Name", style=StyleTokens.TABLE_ROW_KEY, no_wrap=True)
        self.add_column("Engine", style=StyleTokens.ACCENT)
        self.add_column("Connection", style=StyleTokens.TABLE_ROW_VALUE)
        self.add_column("Proxy", style=StyleTokens.INFO)
        self.add_column("Verified", justify="center")
        self.add_column("Status", justify="center")

    def add_target(self, target: dict) -> "TargetsTableBase":
        name = target.get("name", "unknown")
        conn_str = f"{target.get('host', '?')}:{target.get('port', '?')}/{target.get('database', '?')}"
        engine = self.ENGINE_DISPLAY.get(
            target.get("engine", ""), target.get("engine", "unknown")
        )
        proxy = self.PROXY_DISPLAY.get(
            target.get("proxy", "none"), target.get("proxy", "none")
        )
        verified = (
            "Yes" if target.get("endpoint_verified") or target.get("verified") else "No"
        )
        status = "Default" if name == self._default_target else "Available"
        self.add_row(name, engine, conn_str, proxy, verified, status)
        return self

    def add_targets(self, targets: List[dict]) -> "TargetsTableBase":
        for target in targets:
            self.add_target(target)
        return self


def TargetsTable(
    targets: List[dict],
    default_target: Optional[str] = None,
    title: str = "Database Targets",
) -> "Table":
    """ORGANISM: Database targets display. Builds on DataTableBase."""
    table = TargetsTableBase(title=title, default_target=default_target)
    table.add_targets(targets)
    return table.table


class RegistryTableBase(DataTableBase):
    """ORGANISM: Query registry table. Builds on DataTableBase."""

    def __init__(self, title: Optional[str] = None, show_numbers: bool = False):
        super().__init__(title=title, show_row_numbers=show_numbers)
        self._setup_columns()

    def _setup_columns(self):
        self.add_column("Name", style=StyleTokens.TABLE_ROW_KEY)
        self.add_column("Hash", style=StyleTokens.HASH)
        self.add_column("Target", style=StyleTokens.ACCENT)
        self.add_column("Source", style=StyleTokens.SUCCESS)
        self.add_column("Last Analyzed", style=StyleTokens.INFO)
        self.add_column("SQL Preview", style=StyleTokens.SQL)

    def _extract_field(self, obj: Any, field: str, default: str = "") -> str:
        return getattr(obj, field, None) or (
            obj.get(field, default) if isinstance(obj, dict) else default
        )

    def add_registry_entry(self, entry: Any) -> "RegistryTableBase":
        tag = self._extract_field(entry, "tag") or "(unnamed)"
        hash_val = self._extract_field(entry, "hash")
        target = self._extract_field(entry, "last_target") or "-"
        source = self._extract_field(entry, "source")
        last_analyzed = self._extract_field(entry, "last_analyzed")
        sql = self._extract_field(entry, "sql")

        timestamp = last_analyzed[:19].replace("T", " ") if last_analyzed else "never"
        sql_preview = _truncate(sql, 50)

        self.add_row(
            tag,
            hash_val[:8] if hash_val else "",
            target,
            source,
            timestamp,
            sql_preview,
        )
        return self

    def add_registry_entries(self, entries: List[Any]) -> "RegistryTableBase":
        for entry in entries:
            self.add_registry_entry(entry)
        return self


def RegistryTable(
    queries: List[Any],
    title: Optional[str] = None,
    show_numbers: bool = False,
) -> "Table":
    """ORGANISM: Query registry display. Builds on DataTableBase."""
    table = RegistryTableBase(title=title, show_numbers=show_numbers)
    table.add_registry_entries(queries)
    return table.table


# =============================================================================
# Empty State & Navigation Components
# =============================================================================


def EmptyState(
    message: str,
    title: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> "StyledPanel":
    """
    Empty state message for when no data is available.

    Args:
        message: Main message explaining the empty state
        title: Optional panel title
        suggestion: Optional suggestion for what to do

    Returns:
        Rich Panel configured for empty states
    """
    content = f"[{StyleTokens.MUTED}]{message}[/{StyleTokens.MUTED}]"
    if suggestion:
        content += (
            f"\n\n[{StyleTokens.SECONDARY}]{suggestion}[/{StyleTokens.SECONDARY}]"
        )

    return StyledPanel(content, title=title, variant="warning")


class NextStepsBuilder:
    """MOLECULE: Builder for next steps / breadcrumb displays."""

    def __init__(self, title: str = "Next Steps"):
        self._title = title
        self._steps: List[Tuple[str, str]] = []

    def add_step(self, command: str, description: str) -> "NextStepsBuilder":
        self._steps.append((command, description))
        return self

    def add_steps(self, steps: List[Tuple[str, str]]) -> "NextStepsBuilder":
        self._steps.extend(steps)
        return self

    def build(self) -> "Text":
        text = Text()
        text.append(f"\n{self._title}:\n", style=StyleTokens.HEADER)
        for cmd, desc in self._steps:
            text.append("  ")
            if "[" in cmd and "]" in cmd:
                text.append(Text.from_markup(cmd))
            else:
                text.append(cmd, style=StyleTokens.SECONDARY)
            text.append("   ")
            text.append(desc, style=StyleTokens.MUTED)
            text.append("\n")
        return text

    def __rich__(self):
        return self.build()

    def __str__(self):
        lines = [f"\n{self._title}:"]
        for cmd, desc in self._steps:
            lines.append(f"  {cmd}   {desc}")
        return "\n".join(lines)


def NextSteps(
    steps: List[Tuple[str, str]],
    title: str = "Next Steps",
) -> "Text":
    """MOLECULE: Next steps display. Factory function for NextStepsBuilder."""
    return NextStepsBuilder(title).add_steps(steps).build()


class SelectionTableBase(StyledTable):
    """MOLECULE: Interactive selection table. Builds on StyledTable."""

    def __init__(self, show_header: bool = False):
        super().__init__(show_header=show_header, box=None)
        self._table.show_edge = False
        self._table.padding = (0, 2)
        self.add_column("Choice", style=StyleTokens.EMPHASIS)
        self.add_column("Option", style=StyleTokens.SECONDARY)

    def add_choice(self, index: int, label: str) -> "SelectionTableBase":
        self._add_row(f"[{index}]", label)
        return self

    def add_choices(
        self, items: List[str], start_index: int = 1
    ) -> "SelectionTableBase":
        for i, item in enumerate(items, start_index):
            self.add_choice(i, item)
        return self


def SelectionTable(
    items: List[str],
    prompt: str = "",
    default_idx: int = 0,
) -> "Table":
    """MOLECULE: Interactive selection display. Builds on StyledTable."""
    table = SelectionTableBase()
    table.add_choices(items)
    return table.table


# =============================================================================
# Utility Functions
# =============================================================================


def _format_cell(value: Any) -> str:
    """Format a cell value for display."""
    if value is None:
        return "NULL"
    elif isinstance(value, bool):
        return "Yes" if value else "No"
    elif isinstance(value, float):
        return f"{value:.2f}"
    elif isinstance(value, str) and len(value) > 100:
        return value[:97] + "..."
    return str(value)


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max length."""
    if not text:
        return ""
    # Normalize whitespace
    text = " ".join(text.split())
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


# =============================================================================
# Markdown & Tree Components
# =============================================================================


def MarkdownContent(content: str):
    """
    Render markdown content with Rich formatting when available.

    Args:
        content: Markdown text to render

    Returns:
        Rich Markdown object if available, otherwise plain text

    Usage:
        from lib.ui import MarkdownContent, get_console
        console = get_console()
        console.print(MarkdownContent("# Heading\\n\\nSome **bold** text"))
    """
    from rich.markdown import Markdown

    return Markdown(content)


class SimpleTree:
    """
    Tree structure with Rich fallback for plain text.

    Usage:
        from lib.ui import SimpleTree, get_console

        tree = SimpleTree("Root")
        branch = tree.add("Branch 1")
        branch.add("Leaf 1.1")
        branch.add("Leaf 1.2")
        tree.add("Branch 2")

        console = get_console()
        console.print(tree)

    Output (Rich):
        Root
        ├── Branch 1
        │   ├── Leaf 1.1
        │   └── Leaf 1.2
        └── Branch 2

    Output (Plain):
        Root
          ├── Branch 1
          │   ├── Leaf 1.1
          │   └── Leaf 1.2
          └── Branch 2
    """

    def __init__(self, label: str, style: str = None):
        """
        Create a tree node.

        Args:
            label: The label for this node
            style: Optional Rich style for the label
        """
        self.label = label
        self.style = style
        self.children: list = []

    def add(self, label: str, style: str = None) -> "SimpleTree":
        """
        Add a child node.

        Args:
            label: The label for the child node
            style: Optional Rich style for the label

        Returns:
            The created child node (for chaining)
        """
        child = SimpleTree(label, style=style)
        self.children.append(child)
        return child

    def __rich__(self):
        """Render as Rich Tree."""
        from rich.tree import Tree as RichTree

        def build_tree(node: SimpleTree, parent: RichTree = None) -> RichTree:
            # Only pass style if it's not None (Rich doesn't handle None gracefully)
            if parent is None:
                if node.style:
                    tree = RichTree(node.label, style=node.style)
                else:
                    tree = RichTree(node.label)
            else:
                if node.style:
                    tree = parent.add(node.label, style=node.style)
                else:
                    tree = parent.add(node.label)

            for child in node.children:
                build_tree(child, tree)

            return tree

        return build_tree(self)

    def _render_plain(
        self, prefix: str = "", is_last: bool = True, is_root: bool = True
    ) -> list:
        """Render tree as plain text lines."""
        lines = []

        if is_root:
            lines.append(self.label)
        else:
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{self.label}")

        # Calculate child prefix
        if is_root:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")

        for i, child in enumerate(self.children):
            is_last_child = i == len(self.children) - 1
            lines.extend(
                child._render_plain(child_prefix, is_last_child, is_root=False)
            )

        return lines

    def __str__(self):
        """Render as plain text."""
        return "\n".join(self._render_plain())


class SectionBox(StyledPanel):
    """MOLECULE: Bordered section with title, content, subtitle, hint. Extends StyledPanel."""

    def __init__(
        self,
        title: str,
        content: Union[str, Text, Any] = "",
        subtitle: Optional[str] = None,
        hint: Optional[str] = None,
        width: Optional[int] = None,
        border_style: str = StyleTokens.PANEL_BORDER,
    ):
        self.subtitle = subtitle
        self.hint = hint
        self._raw_content = content

        formatted_content = self._format_content(content, subtitle, hint)
        super().__init__(
            content=formatted_content,
            title=title,
            width=width,
            padding=(1, 2),
            border_style=border_style,
        )

    def _format_content(
        self,
        content: Union[str, Text, Any],
        subtitle: Optional[str],
        hint: Optional[str],
    ) -> Union[str, Any]:
        if isinstance(content, str):
            parts = []
            if content:
                parts.append(content)
            if subtitle:
                parts.append(
                    f"\n[{StyleTokens.SECONDARY}]{subtitle}[/{StyleTokens.SECONDARY}]"
                )
            if hint:
                parts.append(f"\n[{StyleTokens.MUTED}]{hint}[/{StyleTokens.MUTED}]")
            return "".join(parts)

        parts: List[Any] = []
        if content:
            parts.append(content)
        if subtitle:
            parts.append(
                Text.from_markup(
                    f"[{StyleTokens.SECONDARY}]{subtitle}[/{StyleTokens.SECONDARY}]"
                )
            )
        if hint:
            parts.append(
                Text.from_markup(f"[{StyleTokens.MUTED}]{hint}[/{StyleTokens.MUTED}]")
            )

        if not parts:
            return ""
        return Group(*parts)

    def __str__(self):
        lines = []
        border = "-" * (self.width or 60)
        lines.append("")
        lines.append(border)
        lines.append(self.title or "")
        lines.append(border)
        if self._raw_content:
            if isinstance(self._raw_content, str):
                for line in self._raw_content.split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append(f"  {self._raw_content}")
        if self.subtitle:
            lines.append(f"\n{self.subtitle}")
        if self.hint:
            lines.append(f"({self.hint})")
        lines.append("")
        return "\n".join(lines)


class Rule:
    """
    Horizontal rule/separator with Rich fallback.

    Usage:
        from lib.ui import Rule, get_console
        console = get_console()
        console.print(Rule("Section Title"))
        console.print(Rule())  # Just a line
    """

    def __init__(self, title: str = None, style: str = StyleTokens.MUTED):
        """
        Create a horizontal rule.

        Args:
            title: Optional title text in the middle of the rule
            style: Style for the rule (default: muted)
        """
        self.title = title
        self.style = style

    def __rich__(self):
        """Render as Rich Rule."""
        from rich.rule import Rule as RichRule

        return RichRule(self.title, style=self.style)

    def __str__(self):
        """Render as plain text."""
        if self.title:
            # Center title in a line of dashes
            total_width = 60
            title_len = len(self.title) + 2  # Add spaces around title
            side_len = (total_width - title_len) // 2
            return f"{'─' * side_len} {self.title} {'─' * side_len}"
        return "─" * 60


def Live(
    renderable=None,
    *,
    console=None,
    refresh_per_second: float = 4,
    screen: bool = False,
    **kwargs,
):
    from rich.live import Live as RichLive

    if console is None:
        from .console import get_console

        console = get_console()

    return RichLive(
        renderable,
        console=console,
        refresh_per_second=refresh_per_second,
        screen=screen,
        **kwargs,
    )


def RichLayout(*args, **kwargs):
    from rich.layout import Layout as RichLayoutImpl

    return RichLayoutImpl(*args, **kwargs)


def Tree(label: str, *args, **kwargs):
    from rich.tree import Tree as RichTree

    return RichTree(label, *args, **kwargs)


# =============================================================================
# Spinner / Progress Components
# =============================================================================


class Spinner:
    """
    Context manager for showing a spinner during long operations.

    With Rich: Uses rich.status.Status with animated spinner
    Without Rich: Shows simple text-based spinner

    Usage:
        from lib.ui import Spinner

        with Spinner("Loading..."):
            do_slow_operation()

        # Or with custom message:
        with Spinner("Thinking...", spinner="dots"):
            result = call_llm()
    """

    def __init__(
        self,
        message: str = "Working...",
        spinner: str = "dots",
        style: str = StyleTokens.TEXT,
    ):
        """
        Create a spinner context manager.

        Args:
            message: Message to show next to spinner
            spinner: Spinner style ("dots", "line", "arc", etc.)
            style: Color style for spinner
        """
        self.message = message
        self.spinner = spinner
        self.style = style
        self._status = None

    def __enter__(self):
        """Start the spinner."""
        from rich.status import Status
        from .console import get_console

        self._status = Status(
            self.message,
            spinner=self.spinner,
            spinner_style=self.style,
            console=get_console(),
        )
        self._status.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the spinner."""
        if self._status:
            self._status.stop()
        return False  # Don't suppress exceptions

    def update(self, message: str):
        """Update the spinner message."""
        self.message = message
        if self._status:
            self._status.update(message)


# =============================================================================
# Live Monitor Components
# =============================================================================


class MonitorHeaderPanel(HeaderPanelBase):
    """ORGANISM: Live monitor header with stats, warning, hint. Builds on HeaderPanelBase."""

    def __init__(self, title: str):
        super().__init__(title=None)
        self._header_title = title
        self._stats: dict = {}
        self._warning: Optional[str] = None
        self._hint: Optional[str] = None

    def with_stats(self, stats: dict) -> "MonitorHeaderPanel":
        self._stats = stats
        return self

    def with_warning(self, warning: str) -> "MonitorHeaderPanel":
        self._warning = warning
        return self

    def with_hint(self, hint: str) -> "MonitorHeaderPanel":
        self._hint = hint
        return self

    def _build_panel(self) -> Panel:
        content = Text()
        content.append(f"{self._header_title}\n", style=StyleTokens.HEADER)
        if self._stats:
            stat_parts = [f"{k}: {v}" for k, v in self._stats.items()]
            content.append("  │  ".join(stat_parts), style=StyleTokens.MUTED)
        if self._warning:
            content.append("\n\n")
            content.append(f"{Icons.WARNING} ", style=f"bold {StyleTokens.WARNING}")
            content.append(self._warning, style=StyleTokens.WARNING)
        if self._hint:
            content.append("\n\n")
            content.append(self._hint, style=StyleTokens.SECONDARY)
        self.content = content
        return super()._build_panel()

    def __str__(self):
        lines = [self._header_title]
        if self._stats:
            lines.append(" | ".join(f"{k}: {v}" for k, v in self._stats.items()))
        if self._warning:
            lines.append(f"WARNING: {self._warning}")
        if self._hint:
            lines.append(self._hint)
        return "\n".join(lines)


class MonitorHeader(MonitorHeaderPanel):
    """ORGANISM: Live monitoring header. Backward-compatible wrapper."""

    def __init__(
        self,
        title: str,
        stats: dict = None,
        hint: str = None,
        warning: str = None,
    ):
        super().__init__(title)
        if stats:
            self.with_stats(stats)
        if warning:
            self.with_warning(warning)
        if hint:
            self.with_hint(hint)


class KeyboardShortcuts:
    """
    Display keyboard shortcuts in a compact format.

    Usage:
        from lib.ui import KeyboardShortcuts, get_console

        shortcuts = KeyboardShortcuts(
            title="Quick Actions",
            shortcuts=[
                ("0-9", "save", "success"),
                ("a", "save all", "success"),
                ("q", "quit", "error"),
            ]
        )
        console.print(shortcuts)
    """

    def __init__(
        self,
        shortcuts: List[Tuple[str, str, str]],
        title: str = None,
        as_panel: bool = True,
    ):
        """
        Create keyboard shortcuts display.

        Args:
            shortcuts: List of (key, action, style) tuples.
                       style can be: "success", "warning", "error", "info", or any Theme color
            title: Optional panel title
            as_panel: Whether to wrap in a panel (default True)
        """
        self.shortcuts = shortcuts
        self.title = title
        self.as_panel = as_panel

    def _get_style(self, style_name: str) -> str:
        """Map style name to Theme color."""
        style_map = {
            "success": StyleTokens.SUCCESS,
            "warning": StyleTokens.WARNING,
            "error": StyleTokens.ERROR,
            "info": StyleTokens.INFO,
            "muted": StyleTokens.MUTED,
            "accent": StyleTokens.ACCENT,
        }
        return style_map.get(style_name, style_name)

    def __rich__(self):
        """Render as Rich Text or Panel."""
        content = Text()
        content.append("Commands: ", style=StyleTokens.HEADER)

        for i, (key, action, style) in enumerate(self.shortcuts):
            if i > 0:
                content.append(" │ ", style=StyleTokens.MUTED)
            content.append(key, style=self._get_style(style))
            content.append(f" ({action})", style=StyleTokens.MUTED)

        if self.as_panel and self.title:
            from rich.panel import Panel

            return Panel(
                content,
                border_style=StyleTokens.PANEL_BORDER,
                box=Layout.BOX_DEFAULT,
                padding=(0, 1),
                title=f"[{StyleTokens.HEADER}]{self.title}[/{StyleTokens.HEADER}]",
            )

        return content

    def __str__(self):
        parts = [f"{key} ({action})" for key, action, _ in self.shortcuts]
        result = "Commands: " + " | ".join(parts)
        if self.title:
            result = f"{self.title}\n{result}"
        return result
