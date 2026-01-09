"""
RDST Design System
==================

Centralized UI components, theming, and console management.
Automatically handles Rich availability - no need to check in consuming code.

Quick Start:
    from lib.ui import get_console, StyleTokens

    console = get_console()
    console.print(f"[{StyleTokens.SUCCESS}]Done![/{StyleTokens.SUCCESS}]")
    console.print(f"[{StyleTokens.ERROR}]Something went wrong[/{StyleTokens.ERROR}]")
    console.print(f"[{StyleTokens.WARNING}]This might take a while[/{StyleTokens.WARNING}]")
    console.print(f"[{StyleTokens.MUTED}]Additional context[/{StyleTokens.MUTED}]")

    # Composed output
    console.print(f"  rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] --name [{StyleTokens.ACCENT}]my-query[/{StyleTokens.ACCENT}]")

    # Use StyleTokens for compound styles (bold + color)
    console.print(f"[{StyleTokens.STATUS_SUCCESS}]Operation completed[/{StyleTokens.STATUS_SUCCESS}]")

Module Overview:
    - theme: Color palette (Colors), compound styles (StyleTokens), icons (Icons), layout constants (Layout)
    - console: Configured Rich console instance with fallback
    - components: Reusable UI primitives (panels, tables, messages)

For full documentation, see individual modules.
"""

# Theme exports
from .theme import (
    Colors,
    StyleTokens,
    Icons,
    Layout,
)

# Prompt exports (Rich wrappers with fallback)
from .prompts import (
    Prompt,
    Confirm,
    IntPrompt,
    FloatPrompt,
    SelectPrompt,
)

# Console exports
from .console import (
    console,
    get_console,
    create_console,
    print_success,
    print_error,
    print_warning,
    print_info,
)

# Component exports
from .components import (
    # ATOMS - base primitives
    StyledTable,
    StyledPanel,
    # MOLECULES - composable building blocks
    DataTableBase,
    StatusTableBase,
    SelectionTableBase,
    HeaderPanelBase,
    NextStepsBuilder,
    # MOLECULES - metrics & status (tactical dashboard)
    MetricCard,
    MetricRow,
    MetricPanel,
    StatusBadge,
    PriorityTag,
    ProgressBar,
    # MOLECULES - factory functions
    DataTable,
    KeyValueTable,
    SelectionTable,
    NextSteps,
    # ORGANISMS - domain-specific (build on molecules)
    QueryTableBase,
    RegistryTableBase,
    TargetsTableBase,
    TopQueryTableBase,
    QueryStatsTableBase,
    AnalysisHeaderPanel,
    MonitorHeaderPanel,
    # ORGANISMS - factory functions
    QueryTable,
    RegistryTable,
    TargetsTable,
    TopQueryTable,
    QueryStatsTable,
    AnalysisHeader,
    MonitorHeader,
    # Panels (extend StyledPanel)
    MessagePanel,
    NoticePanel,
    QueryPanel,
    SQLPreview,
    InlineSQL,
    EmptyState,
    SectionBox,
    # Headers
    SectionHeader,
    # Status & Navigation
    StatusLine,
    DurationDisplay,
    Banner,
    # Keyboard
    KeyboardShortcuts,
    # Markdown & Tree
    MarkdownContent,
    SimpleTree,
    Rule,
    Tree,
    # Progress
    Spinner,
    Live,
    RichLayout,
    Group,
    Text,
)

__all__ = [
    # Theme
    "Colors",
    "StyleTokens",
    "Icons",
    "Layout",
    # Prompts
    "Prompt",
    "Confirm",
    "IntPrompt",
    "FloatPrompt",
    "SelectPrompt",
    # Console
    "console",
    "get_console",
    "create_console",
    "print_success",
    "print_error",
    "print_warning",
    "print_info",
    # ATOMS
    "StyledTable",
    "StyledPanel",
    # MOLECULES
    "DataTableBase",
    "StatusTableBase",
    "SelectionTableBase",
    "HeaderPanelBase",
    "NextStepsBuilder",
    "MetricCard",
    "MetricRow",
    "MetricPanel",
    "StatusBadge",
    "PriorityTag",
    "ProgressBar",
    "DataTable",
    "KeyValueTable",
    "SelectionTable",
    "NextSteps",
    # ORGANISMS
    "QueryTableBase",
    "RegistryTableBase",
    "TargetsTableBase",
    "TopQueryTableBase",
    "QueryStatsTableBase",
    "AnalysisHeaderPanel",
    "MonitorHeaderPanel",
    "QueryTable",
    "RegistryTable",
    "TargetsTable",
    "TopQueryTable",
    "QueryStatsTable",
    "AnalysisHeader",
    "MonitorHeader",
    # Panels
    "MessagePanel",
    "NoticePanel",
    "QueryPanel",
    "SQLPreview",
    "InlineSQL",
    "EmptyState",
    "SectionBox",
    # Headers
    "SectionHeader",
    # Status & Navigation
    "StatusLine",
    "DurationDisplay",
    "Banner",
    # Keyboard
    "KeyboardShortcuts",
    # Markdown & Tree
    "MarkdownContent",
    "SimpleTree",
    "Rule",
    "Tree",
    # Progress
    "Spinner",
    "Live",
    "RichLayout",
    "Group",
    "Text",
]
