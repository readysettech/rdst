from __future__ import annotations

"""
RDST How Do I - Quick documentation lookup using Haiku.

Usage:
    rdst howdoi "how do I analyze a query?"
    rdst howdoi "what's the difference between top and analyze?"
"""

from dataclasses import dataclass
from typing import Optional
import os

# Rich for terminal formatting
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.status import Status
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Embedded documentation for RDST
RDST_DOCS = """
# RDST (ReadySet Diagnostics & SQL Tuning) Documentation

## Overview
RDST is a CLI tool for database performance analysis and SQL query optimization.
It connects to PostgreSQL or MySQL databases and provides AI-powered recommendations.

## Installation
```bash
pip install rdst
```

## Quick Start
```bash
# First-time setup wizard
rdst init

# Or manually add a database target
rdst configure add --target mydb --engine postgresql --host localhost --port 5432 --user postgres --database myapp --password-env MY_DB_PASSWORD

# Set your password (never stored in config)
export MY_DB_PASSWORD="your-password"

# Analyze a slow query
rdst analyze -q "SELECT * FROM users WHERE email = 'test@example.com'" --target mydb

# Monitor slow queries in real-time
rdst top --target mydb
```

## Commands

### rdst init
Interactive setup wizard for first-time configuration.
- Guides you through adding database targets
- Configures LLM API key (Anthropic recommended)
- Tests connectivity

### rdst configure
Manage database targets.

```bash
# Add a new target
rdst configure add --target prod-db --engine postgresql --host db.example.com --port 5432 --user admin --database myapp --password-env PROD_DB_PASSWORD

# List all targets
rdst configure list

# Remove a target
rdst configure remove --target old-db

# Set default target
rdst configure default --target prod-db

# Configure LLM provider
rdst configure llm --provider anthropic
```

### rdst analyze
Analyze a SQL query for performance optimization.

```bash
# Basic analysis
rdst analyze -q "SELECT * FROM orders WHERE status = 'pending'" --target mydb

# Fast mode (10s timeout for slow queries)
rdst analyze -q "SELECT * FROM big_table" --target mydb --fast

# Test ReadySet cacheability (requires Docker)
rdst analyze -q "SELECT * FROM orders" --target mydb --readyset-cache

# Continue previous analysis interactively
rdst analyze --hash abc123 --interactive
```

Output includes:
- Execution plan analysis
- Index recommendations (CREATE INDEX statements)
- Query rewrites (optimized SQL)
- Performance rating

### rdst top
Monitor slow queries in real-time.

```bash
# Watch for slow queries (default 10 seconds)
rdst top --target mydb

# Run for 30 seconds
rdst top --target mydb --duration 30

# Set minimum query duration to capture (ms)
rdst top --target mydb --min-duration 100
```

Shows:
- Currently running queries
- Query duration
- Normalized query patterns
- Execution counts

### rdst query
Manage saved queries in your registry.

```bash
# Save a query for later analysis
rdst query add my-slow-query -q "SELECT * FROM orders JOIN items ON ..." --target mydb

# List saved queries
rdst query list

# Delete a saved query
rdst query delete my-slow-query
```

### rdst report
Send feedback to the RDST team.

```bash
# Report an issue
rdst report --reason "Analysis gave wrong recommendation" --hash abc123 --negative

# Report positive feedback
rdst report --reason "Great index suggestion!" --hash abc123 --positive
```

### rdst ask
Generate SQL from natural language questions.

```bash
# Ask a question about your data
rdst ask "Show me top 10 customers by order value" --target mydb

# Dry run - generate SQL without executing
rdst ask "Count orders by status" --target mydb --dry-run

# Use agent mode for complex queries
rdst ask "What's the relationship between customers and orders?" --target mydb --agent
```

The ask command:
- Understands your database schema automatically
- Generates optimized SQL queries
- Validates SQL before execution
- Shows results in a readable table

### rdst schema
Manage semantic layer for better SQL generation.

```bash
# Initialize semantic layer from database
rdst schema init --target mydb

# View semantic layer
rdst schema show --target mydb

# AI-generate column/table descriptions
rdst schema annotate --target mydb --use-llm

# Edit semantic layer manually
rdst schema edit --target mydb

# Export semantic layer
rdst schema export --target mydb --format yaml

# Delete semantic layer
rdst schema delete --target mydb
```

The semantic layer stores:
- Table and column descriptions
- Enum value meanings
- Business terminology
- Relationships between tables

This helps `rdst ask` generate more accurate SQL.

## Password Handling
RDST never stores passwords in config files. Each target has a `password_env` field
specifying which environment variable holds the password.

```bash
# Config shows: password_env = "PROD_DB_PASSWORD"
# You must export this before running commands:
export PROD_DB_PASSWORD="your-actual-password"
```

## Common Workflows

### Optimizing a Slow Query
1. Identify slow query with `rdst top --target mydb`
2. Copy the query and run `rdst analyze -q "..." --target mydb`
3. Review index recommendations
4. Create suggested indexes
5. Re-run analysis to verify improvement

### Testing ReadySet Caching
1. Run `rdst analyze -q "..." --target mydb --readyset-cache`
2. Wait 30-60 seconds for containers to start
3. Review if query is cacheable
4. If cacheable, see the CREATE CACHE command for production

### Setting Up Multiple Databases
```bash
rdst configure add --target prod --engine postgresql --host prod.db.com ...
rdst configure add --target staging --engine postgresql --host staging.db.com ...
rdst configure default --target prod
```

## Supported Databases
- PostgreSQL (recommended)
- MySQL

## LLM Provider
- Anthropic - requires ANTHROPIC_API_KEY

## Troubleshooting

### "Authentication failed"
- Check if password environment variable is exported
- Verify the password is correct
- Check host/port connectivity

### "Connection refused"
- Verify database host and port
- Check firewall rules
- Ensure database is running

### "No LLM API key configured"
- Run `rdst configure llm --provider anthropic`
- Export ANTHROPIC_API_KEY environment variable

### ReadySet cache errors
- Docker not found: Install Docker Desktop
- If a query can't be cached, ReadySet will explain why in the output

## Config File Location
- Main config: ~/.rdst/config.toml
- Query registry: ~/.rdst/queries.toml
- Conversation history: ~/.rdst/conversations/
"""


@dataclass
class HowDoIResult:
    """Result from howdoi command."""
    success: bool
    answer: str
    error: Optional[str] = None


class HowDoICommand:
    """Implements `rdst howdoi` quick docs lookup."""

    def __init__(self):
        self.console = Console() if RICH_AVAILABLE else None

    def print_formatted(self, text: str) -> None:
        """Print text with Rich formatting if available."""
        if RICH_AVAILABLE and self.console:
            # Render as markdown in a panel
            md = Markdown(text)
            self.console.print(Panel(md, title="[bold blue]RDST Help[/bold blue]", border_style="blue"))
        else:
            print(text)

    def run(self, question: str) -> HowDoIResult:
        """
        Answer a question about RDST using embedded docs and Haiku.

        Args:
            question: Natural language question like "how do I analyze a query?"

        Returns:
            HowDoIResult with the answer
        """
        # Check for API key first
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return HowDoIResult(
                success=False,
                answer="",
                error="""No Anthropic API key found.

To use 'rdst howdoi', you need to set up your API key:

1. Get an API key from https://console.anthropic.com/
2. Export it:
   export ANTHROPIC_API_KEY="your-key-here"

Or configure RDST with:
   rdst configure llm --provider anthropic
"""
            )

        try:
            from lib.llm_manager.llm_manager import LLMManager

            # Use Haiku for fast, cheap responses
            llm = LLMManager(defaults={"model": "claude-3-haiku-20240307"})

            # Build prompt
            system_message = """You are a helpful assistant for RDST, a database performance analysis CLI tool.
Answer the user's question based on the documentation provided. Be concise and practical.
Include command examples when relevant. If the question isn't covered in the docs, say so."""

            user_query = f"""## RDST Documentation
{RDST_DOCS}

## User Question
{question}

## Answer (be concise, include command examples):"""

            # Call LLM with spinner feedback
            if RICH_AVAILABLE and self.console:
                with self.console.status("Thinking...", spinner="dots", spinner_style="white"):
                    response = llm.query(
                        system_message=system_message,
                        user_query=user_query,
                        max_tokens=1000
                    )
            else:
                # Simple fallback spinner
                import sys
                import threading
                import time

                stop_spinner = False
                def spinner():
                    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
                    i = 0
                    while not stop_spinner:
                        sys.stderr.write(f"\r{chars[i % len(chars)]} Thinking...")
                        sys.stderr.flush()
                        time.sleep(0.1)
                        i += 1
                    sys.stderr.write("\r" + " " * 20 + "\r")
                    sys.stderr.flush()

                spinner_thread = threading.Thread(target=spinner)
                spinner_thread.start()

                response = llm.query(
                    system_message=system_message,
                    user_query=user_query,
                    max_tokens=1000
                )

                stop_spinner = True
                spinner_thread.join()

            # Response format: {"text": "...", "usage": {...}, "provider": "...", "model": "..."}
            if response.get("text"):
                return HowDoIResult(
                    success=True,
                    answer=response["text"].strip()
                )
            else:
                return HowDoIResult(
                    success=False,
                    answer="",
                    error=response.get("error") or "Failed to get response from LLM"
                )

        except Exception as e:
            # Fallback: simple keyword search if LLM fails
            return self._fallback_search(question, str(e))

    def _fallback_search(self, question: str, error: str) -> HowDoIResult:
        """Fallback when LLM is unavailable - basic keyword matching."""
        question_lower = question.lower()

        # Simple keyword matching
        if "analyze" in question_lower:
            answer = """To analyze a query:

```bash
rdst analyze -q "YOUR SQL QUERY" --target your-target
```

Options:
- --fast: Skip slow queries (10s timeout)
- --readyset-cache: Test if query can be cached by ReadySet
- --interactive: Continue analysis conversation

Example:
```bash
rdst analyze -q "SELECT * FROM users WHERE id = 1" --target mydb
```"""
        elif "top" in question_lower or "slow" in question_lower or "monitor" in question_lower:
            answer = """To monitor slow queries:

```bash
rdst top --target your-target
```

Options:
- --duration N: Run for N seconds (default 10)
- --min-duration N: Only show queries slower than N ms

Example:
```bash
rdst top --target mydb --duration 30
```"""
        elif "configure" in question_lower or "add" in question_lower or "target" in question_lower:
            answer = """To configure a database target:

```bash
rdst configure add --target NAME --engine postgresql --host HOST --port PORT --user USER --database DB --password-env ENV_VAR
```

Then export your password:
```bash
export ENV_VAR="your-password"
```

List targets: `rdst configure list`
Set default: `rdst configure default --target NAME`"""
        elif "password" in question_lower:
            answer = """RDST never stores passwords. Each target has a password_env field.

1. Check your target's password_env: `rdst configure list`
2. Export it: `export MY_DB_PASSWORD="your-password"`
3. Run your command

The password must be exported before each session."""
        elif "cache" in question_lower or "readyset" in question_lower:
            answer = """To test ReadySet caching:

```bash
rdst analyze -q "YOUR QUERY" --target your-target --readyset-cache
```

Requires Docker. Takes 30-60 seconds first time.

Shows:
- Whether query is cacheable
- Performance comparison (original vs cached)
- CREATE CACHE command for production"""
        elif "init" in question_lower or "setup" in question_lower or "start" in question_lower:
            answer = """To set up RDST for the first time:

```bash
rdst init
```

This wizard will:
1. Add your database target(s)
2. Configure LLM API key
3. Test connectivity

Or manually:
```bash
rdst configure add --target mydb --engine postgresql ...
rdst configure llm --provider anthropic
export ANTHROPIC_API_KEY="your-key"
```"""
        else:
            answer = f"""I couldn't find specific docs for your question.

Try:
- `rdst --help` for all commands
- `rdst COMMAND --help` for command-specific help
- Common commands: analyze, top, configure, init

(LLM unavailable: {error})"""

        return HowDoIResult(
            success=True,
            answer=answer
        )
