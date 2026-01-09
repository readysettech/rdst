#!/usr/bin/env python3
"""Export storybook components to HTML for visual verification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.theme import Theme

from lib.ui import (
    StyleTokens,
    Icons,
    Rule,
    MessagePanel,
    QueryPanel,
    SQLPreview,
    InlineSQL,
    DataTable,
    QueryTable,
    KeyValueTable,
    QueryStatsTable,
    TopQueryTable,
    TargetsTable,
    RegistryTable,
    SelectionTable,
    SectionHeader,
    AnalysisHeader,
    MonitorHeader,
    StatusLine,
    DurationDisplay,
    Banner,
    EmptyState,
    NextSteps,
    MarkdownContent,
    SimpleTree,
    SectionBox,
    KeyboardShortcuts,
    MetricCard,
    MetricRow,
    MetricPanel,
    StatusBadge,
    PriorityTag,
    ProgressBar,
)
from lib.ui.theme import THEME_DEFINITION

OUTPUT_FILE = Path(__file__).parent / "storybook_output.html"


# =============================================================================
# Component Registry
# =============================================================================

COMPONENTS = {
    "MessagePanel": MessagePanel,
    "QueryPanel": QueryPanel,
    "SQLPreview": SQLPreview,
    "InlineSQL": InlineSQL,
    "DataTable": DataTable,
    "QueryTable": QueryTable,
    "KeyValueTable": KeyValueTable,
    "QueryStatsTable": QueryStatsTable,
    "TopQueryTable": TopQueryTable,
    "TargetsTable": TargetsTable,
    "RegistryTable": RegistryTable,
    "SelectionTable": SelectionTable,
    "SectionHeader": SectionHeader,
    "AnalysisHeader": AnalysisHeader,
    "MonitorHeader": MonitorHeader,
    "StatusLine": StatusLine,
    "DurationDisplay": DurationDisplay,
    "Banner": Banner,
    "EmptyState": EmptyState,
    "NextSteps": NextSteps,
    "MetricCard": MetricCard,
    "MetricRow": MetricRow,
    "MetricPanel": MetricPanel,
    "StatusBadge": StatusBadge,
    "PriorityTag": PriorityTag,
    "ProgressBar": ProgressBar,
    "MarkdownContent": MarkdownContent,
    "SimpleTree": SimpleTree,
    "SectionBox": SectionBox,
    "Rule": Rule,
    "KeyboardShortcuts": KeyboardShortcuts,
}

CATEGORIES = {
    "Messages": ["MessagePanel"],
    "SQL": ["QueryPanel", "SQLPreview", "InlineSQL"],
    "Tables": [
        "DataTable",
        "QueryTable",
        "KeyValueTable",
        "QueryStatsTable",
        "TopQueryTable",
        "TargetsTable",
        "RegistryTable",
        "SelectionTable",
    ],
    "Headers": ["SectionHeader", "AnalysisHeader", "MonitorHeader"],
    "Status": ["StatusLine", "DurationDisplay", "Banner"],
    "Navigation": ["EmptyState", "NextSteps"],
    "Tactical Dashboard": [
        "MetricCard",
        "MetricRow",
        "MetricPanel",
        "StatusBadge",
        "PriorityTag",
        "ProgressBar",
    ],
    "Other": [
        "MarkdownContent",
        "SimpleTree",
        "SectionBox",
        "Rule",
        "KeyboardShortcuts",
    ],
}


# =============================================================================
# Sample Data Builders
# =============================================================================


def _build_sample_tree() -> SimpleTree:
    tree = SimpleTree("Database Schema")
    users = tree.add("users", style=StyleTokens.SUCCESS)
    users.add("id (PK)")
    users.add("name")
    users.add("email")
    users.add("created_at")
    orders = tree.add("orders", style=StyleTokens.SUCCESS)
    orders.add("id (PK)")
    orders.add("user_id (FK)")
    orders.add("total")
    orders.add("status")
    return tree


def _build_sample_metric_row() -> MetricRow:
    return (
        MetricRow()
        .add_metric("37", "Active", "+5")
        .add_metric("129", "Completed")
        .add_metric("12", "On Hold", "-2")
    )


# =============================================================================
# Sample Data for Each Component
# =============================================================================

SAMPLE_DATA = {
    "MessagePanel": {
        "props": {"content": "This is a sample message", "variant": "info"},
        "variants": [
            {"content": "Operation completed successfully!", "variant": "success"},
            {
                "content": "Something went wrong",
                "variant": "error",
                "hint": "Check your configuration",
            },
            {"content": "This action cannot be undone", "variant": "warning"},
            {"content": "Processing your request...", "variant": "info"},
        ],
    },
    "QueryPanel": {
        "props": {
            "sql": "SELECT u.name, u.email, COUNT(o.id) as order_count\nFROM users u\nLEFT JOIN orders o ON o.user_id = u.id\nWHERE u.created_at > '2024-01-01'\nGROUP BY u.id\nORDER BY order_count DESC\nLIMIT 10;",
            "title": "User Orders Query",
        },
    },
    "SQLPreview": {
        "props": {
            "sql": "SELECT * FROM customers WHERE status = 'active' LIMIT 100;",
            "explanation": "Retrieves all active customers, limited to 100 rows",
            "confidence": 0.92,
            "warnings": ["Query uses SELECT * - consider specifying columns"],
        },
    },
    "InlineSQL": {
        "props": {
            "sql": "SELECT id, name, email FROM users WHERE status = 'active' ORDER BY created_at DESC",
            "max_length": 60,
        },
    },
    "DataTable": {
        "props": {
            "columns": ["ID", "Name", "Email", "Status"],
            "rows": [
                (1, "John Doe", "john@example.com", "Active"),
                (2, "Jane Smith", "jane@example.com", "Active"),
                (3, "Bob Wilson", "bob@example.com", "Inactive"),
            ],
            "title": "Users Table",
            "justifications": ["right", "left", "left", "left"],
        },
    },
    "QueryTable": {
        "props": {
            "queries": [
                {
                    "hash": "abc123def456",
                    "name": "user-lookup",
                    "sql": "SELECT * FROM users WHERE id = $1",
                    "target": "prod-db",
                    "duration_ms": 12.5,
                },
                {
                    "hash": "xyz789ghi012",
                    "name": "order-summary",
                    "sql": "SELECT COUNT(*) FROM orders WHERE status = 'pending'",
                    "target": "prod-db",
                    "duration_ms": 245.8,
                },
                {
                    "hash": "mno345pqr678",
                    "name": "slow-report",
                    "sql": "SELECT * FROM analytics WHERE date > $1",
                    "target": "analytics-db",
                    "duration_ms": 3420.1,
                },
            ],
        },
    },
    "KeyValueTable": {
        "props": {
            "data": {
                "Target": "prod-db",
                "Engine": "PostgreSQL 15.2",
                "Host": "db.example.com:5432",
                "Database": "myapp",
                "SSL": True,
                "Pool Size": 20,
            },
            "title": "Connection Details",
        },
    },
    "QueryStatsTable": {
        "props": {
            "stats": {
                "query_stats": {
                    "q1": type(
                        "QueryStats",
                        (),
                        {
                            "query_name": "user-lookup",
                            "executions": 1500,
                            "successes": 1498,
                            "failures": 2,
                            "timings_ms": [10, 12, 15],
                            "min_ms": 8.2,
                            "avg_ms": 12.4,
                            "p95_ms": 18.7,
                            "max_ms": 45.2,
                        },
                    )(),
                    "q2": type(
                        "QueryStats",
                        (),
                        {
                            "query_name": "order-summary",
                            "executions": 500,
                            "successes": 500,
                            "failures": 0,
                            "timings_ms": [200, 250, 300],
                            "min_ms": 180.5,
                            "avg_ms": 245.8,
                            "p95_ms": 312.4,
                            "max_ms": 520.1,
                        },
                    )(),
                },
                "elapsed_seconds": 60.0,
                "total_executions": 2000,
            },
            "title": "Query Performance Summary",
        },
    },
    "TopQueryTable": {
        "props": {
            "queries": [
                {
                    "query_hash": "abc123def456",
                    "query_text": "SELECT * FROM users",
                    "freq": 1250,
                    "total_time": "15.2s",
                    "avg_time": "12ms",
                    "pct_load": "45%",
                },
                {
                    "query_hash": "xyz789ghi012",
                    "query_text": "SELECT COUNT(*) FROM orders",
                    "freq": 890,
                    "total_time": "8.5s",
                    "avg_time": "9ms",
                    "pct_load": "25%",
                },
            ],
            "source": "pg_stat",
            "target_name": "prod-db",
            "db_engine": "postgresql",
        },
    },
    "TargetsTable": {
        "props": {
            "targets": [
                {
                    "name": "prod-db",
                    "engine": "postgresql",
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "myapp",
                    "proxy": "none",
                    "verified": True,
                },
                {
                    "name": "staging",
                    "engine": "postgresql",
                    "host": "staging.example.com",
                    "port": 5432,
                    "database": "myapp_staging",
                    "proxy": "readyset",
                    "verified": True,
                },
                {
                    "name": "analytics",
                    "engine": "mysql",
                    "host": "analytics.example.com",
                    "port": 3306,
                    "database": "analytics",
                    "proxy": "none",
                    "verified": False,
                },
            ],
            "default_target": "prod-db",
        },
    },
    "RegistryTable": {
        "props": {
            "queries": [
                type(
                    "QueryEntry",
                    (),
                    {
                        "tag": "user-lookup",
                        "hash": "abc123def456",
                        "last_target": "prod-db",
                        "source": "manual",
                        "last_analyzed": "2024-01-15T10:30:00",
                        "sql": "SELECT * FROM users WHERE id = $1",
                    },
                )(),
                type(
                    "QueryEntry",
                    (),
                    {
                        "tag": "order-report",
                        "hash": "xyz789ghi012",
                        "last_target": "prod-db",
                        "source": "top",
                        "last_analyzed": "2024-01-14T15:45:00",
                        "sql": "SELECT COUNT(*) FROM orders GROUP BY status",
                    },
                )(),
            ],
            "show_numbers": True,
        },
    },
    "SelectionTable": {
        "props": {
            "items": ["PostgreSQL", "MySQL", "SQLite", "MariaDB"],
            "prompt": "Select database engine",
            "default_idx": 0,
        },
    },
    "SectionHeader": {
        "props": {"title": "Query Analysis Results", "icon": Icons.SUCCESS},
    },
    "AnalysisHeader": {
        "props": {
            "target": "prod-db",
            "engine": "postgresql",
            "analysis_id": "abc123def456789",
            "llm_info": {"model": "claude-sonnet-4", "tokens": 5392, "cost": 0.036},
        },
    },
    "MonitorHeader": {
        "props": {
            "title": "RDST Top - Real-Time Query Monitor",
            "stats": {"Runtime": "42s", "Tracked": "15", "Polling": "200ms"},
            "hint": "Press Ctrl+C to exit",
            "warning": None,
        },
        "variants": [
            {
                "title": "RDST Top - MySQL Monitor",
                "stats": {"Runtime": "10s", "Tracked": "8"},
                "warning": "MySQL: Queries <1s may not be tracked",
            },
        ],
    },
    "StatusLine": {
        "props": {
            "label": "Status",
            "value": "Connected",
            "style": StyleTokens.SUCCESS,
        },
    },
    "DurationDisplay": {
        "props": {"ms": 245.8, "label": "Execution Time"},
        "variants": [
            {"ms": 5.2, "label": "Fast Query"},
            {"ms": 150.0, "label": "Medium Query"},
            {"ms": 2500.0, "label": "Slow Query"},
        ],
    },
    "Banner": {
        "props": {"title": "Found existing conversation", "width": 60},
    },
    "EmptyState": {
        "props": {
            "message": "No queries found in the registry",
            "title": "Empty Registry",
            "suggestion": "Use 'rdst query add' to save your first query",
        },
    },
    "NextSteps": {
        "props": {
            "steps": [
                ("rdst analyze --hash abc123", "Analyze this query"),
                ("rdst top --target prod-db", "Monitor slow queries"),
                ("rdst query list", "View saved queries"),
            ],
        },
    },
    "MarkdownContent": {
        "props": {
            "content": "# Analysis Complete\n\n**Summary:** Query performs well.\n\n- Index usage: *optimal*\n- Execution time: `12ms`\n\n> Tip: Consider adding a covering index for better performance."
        },
    },
    "SimpleTree": {
        "props": {},
        "builder": _build_sample_tree,
    },
    "SectionBox": {
        "props": {
            "title": "Parameterized Query",
            "content": "SELECT * FROM users WHERE id = $1 AND status = $2",
            "subtitle": "2 parameters needed",
            "hint": "Press Enter to provide values, Ctrl+C to cancel",
        },
    },
    "Rule": {
        "props": {"title": "Results"},
        "variants": [
            {"title": None},
            {"title": "Section Break", "style": "bold blue"},
        ],
    },
    "KeyboardShortcuts": {
        "props": {
            "shortcuts": [
                ("0-9", "save query", "success"),
                ("a", "save all", "info"),
                ("q", "quit", "error"),
            ],
            "title": "Quick Actions",
        },
    },
    "MetricCard": {
        "props": {"value": "37", "label": "Active", "trend": "+5"},
        "variants": [
            {"value": "129", "label": "Completed", "trend": None},
            {"value": "12", "label": "On Hold", "trend": "-2"},
        ],
    },
    "MetricRow": {
        "props": {},
        "builder": _build_sample_metric_row,
    },
    "MetricPanel": {
        "props": {
            "metrics": [
                ("84/112", "Agents Deployed"),
                ("80.2", "Efficiency Index"),
                ("91.3%", "Success Rate"),
            ],
            "title": "OPERATION ACTIVITY",
        },
    },
    "StatusBadge": {
        "props": {"label": "ONLINE", "variant": "active"},
        "variants": [
            {"label": "ACTIVE", "variant": "active"},
            {"label": "PENDING", "variant": "pending"},
            {"label": "CRITICAL", "variant": "critical"},
            {"label": "OFFLINE", "variant": "offline"},
        ],
    },
    "PriorityTag": {
        "props": {"priority": "CRITICAL"},
        "variants": [
            {"priority": "HIGH"},
            {"priority": "MEDIUM"},
            {"priority": "LOW"},
        ],
    },
    "ProgressBar": {
        "props": {"value": 80.2, "max_value": 100, "width": 20, "label": "Efficiency"},
        "variants": [
            {"value": 95, "label": "High"},
            {"value": 50, "label": "Medium"},
            {"value": 25, "label": "Low"},
        ],
    },
}


# =============================================================================
# HTML Rendering
# =============================================================================


def render_component(console, comp_name):
    if comp_name not in COMPONENTS:
        return

    component_fn = COMPONENTS[comp_name]
    sample = SAMPLE_DATA.get(comp_name, {"props": {}})

    console.print(
        f"[{StyleTokens.HEADER}]{Icons.SECTION} {comp_name}[/{StyleTokens.HEADER}]"
    )

    try:
        if "builder" in sample:
            result = sample["builder"]()
        else:
            result = component_fn(**sample.get("props", {}))
        console.print(result)
    except Exception as e:
        console.print(
            f"[{StyleTokens.ERROR}]Error rendering {comp_name}: {e}[/{StyleTokens.ERROR}]"
        )


def main():
    console = Console(
        record=True,
        width=120,
        force_terminal=True,
        theme=Theme(THEME_DEFINITION),
    )

    console.print(
        "\n[bold cyan]═══════════════════════════════════════════════════════════════[/]"
    )
    console.print(
        "[bold cyan]         RDST DESIGN SYSTEM - COMPONENT GALLERY               [/]"
    )
    console.print(
        "[bold cyan]═══════════════════════════════════════════════════════════════[/]\n"
    )

    for category, items in CATEGORIES.items():
        console.print(Rule(category))
        console.print()
        for item in items:
            render_component(console, item)
        console.print()

    html = console.export_html(
        inline_styles=True,
        code_format='<pre class="rich-output">{code}</pre>',
    )

    html = html.replace("background-color: #ffffff", "background-color: transparent")
    html = html.replace("background-color: #000000", "background-color: transparent")

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RDST Design System - Component Gallery</title>
    <style>
        body {{
            background-color: #1e1e1e;
            color: #e0e0e0;
            padding: 20px;
            margin: 0;
            min-width: fit-content;
        }}
        .rich-output {{
            font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
            font-size: 14px;
            line-height: 1.2;
            white-space: pre;
            overflow-x: auto;
            margin: 0;
            display: block;
        }}
        code {{
            white-space: pre;
        }}
    </style>
</head>
<body>
{html}
</body>
</html>"""

    OUTPUT_FILE.write_text(full_html)
    print(f"Exported to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
