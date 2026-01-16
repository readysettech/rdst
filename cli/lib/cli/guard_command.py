"""
Guard command - CLI for managing reusable safety policies.

Commands:
    rdst guard create --name NAME [options]
    rdst guard create --name NAME --intent "policy description"
    rdst guard list
    rdst guard show NAME
    rdst guard delete NAME
    rdst guard edit NAME
    rdst guard check "SQL" --guard NAME
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any

import yaml

from ..guard import (
    GuardConfig,
    GuardManager,
    GuardNotFoundError,
    GuardExistsError,
    derive_rules_from_intent,
    format_derived_rules,
)
from .rdst_cli import RdstResult


class GuardCommand:
    """CLI command handler for guard operations."""

    def __init__(self):
        self.manager = GuardManager()

    def execute(
        self,
        subcommand: str | None = None,
        name: str | None = None,
        description: str = "",
        mask: list[str] | None = None,
        deny_columns: list[str] | None = None,
        allow_tables: list[str] | None = None,
        require_where: bool = False,
        require_limit: bool = False,
        no_select_star: bool = False,
        max_tables: int | None = None,
        cost_limit: int | None = None,
        max_estimated_rows: int | None = None,
        required_filters: list[str] | None = None,
        intent: str | None = None,
        schema_context: str | None = None,
        max_rows: int = 1000,
        timeout: int = 30,
        sql: str | None = None,
        check_guard: str | None = None,
        target: str | None = None,
        **kwargs: Any,
    ) -> RdstResult:
        """Execute a guard subcommand."""

        if subcommand is None:
            return self._show_help()

        if subcommand == "create":
            return self._create(
                name=name,
                description=description,
                mask=mask,
                deny_columns=deny_columns,
                allow_tables=allow_tables,
                require_where=require_where,
                require_limit=require_limit,
                no_select_star=no_select_star,
                max_tables=max_tables,
                cost_limit=cost_limit,
                max_estimated_rows=max_estimated_rows,
                required_filters=required_filters,
                intent=intent,
                schema_context=schema_context,
                max_rows=max_rows,
                timeout=timeout,
            )
        elif subcommand == "list":
            return self._list()
        elif subcommand == "show":
            return self._show(name)
        elif subcommand == "delete":
            return self._delete(name)
        elif subcommand == "edit":
            return self._edit(name)
        elif subcommand == "check":
            # For check, the guard name comes from --guard flag
            return self._check(sql=sql, guard_name=check_guard or name, target=target)
        else:
            return RdstResult(False, f"Unknown subcommand: {subcommand}")

    def _show_help(self) -> RdstResult:
        """Show help message."""
        help_text = """Guard - Reusable safety policies for data agents

Usage:
  rdst guard <subcommand> [options]

Subcommands:
  create    Create a new guard (manual or intent-based)
  list      List all configured guards
  show      Show guard details
  delete    Delete a guard
  edit      Edit guard in $EDITOR
  check     Test SQL against a guard

Examples:
  # Manual creation with explicit rules
  rdst guard create --name pii-safe --mask "*.email:email" --require-where

  # Intent-based creation (LLM derives rules)
  rdst guard create --name support-guard \\
    --intent "Support agents can look up customers by ID or email.
              Prevent bulk exports. Protect passwords."

  # With required filters (blocks trivial WHERE bypasses)
  rdst guard create --name strict --required-filters "users:id,email"

  rdst guard list
  rdst guard show pii-safe
  rdst guard check "SELECT * FROM users" --guard pii-safe

Masking patterns:
  redact      -> [REDACTED]
  email       -> u***@d***.com
  partial:N   -> ****1234 (show last N chars)
  hash        -> a1b2c3d4 (SHA256 truncated)

Run 'rdst guard create --help' for full options."""
        return RdstResult(True, help_text)

    def _create(
        self,
        name: str | None,
        description: str,
        mask: list[str] | None,
        deny_columns: list[str] | None,
        allow_tables: list[str] | None,
        require_where: bool,
        require_limit: bool,
        no_select_star: bool,
        max_tables: int | None,
        cost_limit: int | None,
        max_estimated_rows: int | None,
        required_filters: list[str] | None,
        intent: str | None,
        schema_context: str | None,
        max_rows: int,
        timeout: int,
    ) -> RdstResult:
        """Create a new guard."""
        if not name:
            return RdstResult(False, "Guard name required. Use --name NAME")

        # Intent-based creation: derive rules from natural language
        if intent:
            return self._create_from_intent(
                name=name,
                intent=intent,
                schema_context=schema_context,
            )

        # Manual creation: build config from flags
        config = GuardConfig(name=name, description=description)

        # Parse masking patterns
        if mask:
            for pattern in mask:
                if ":" not in pattern:
                    return RdstResult(
                        False,
                        f"Invalid mask pattern '{pattern}'. Use format: column_pattern:mask_type"
                    )
                col_pattern, mask_type = pattern.split(":", 1)
                config.masking.patterns[col_pattern] = mask_type

        # Set restrictions
        if deny_columns:
            config.restrictions.denied_columns = deny_columns
        if allow_tables:
            config.restrictions.allowed_tables = allow_tables

        # Parse required_filters (format: "table:col1,col2")
        if required_filters:
            parsed_filters: dict[str, list[str]] = {}
            for rf in required_filters:
                if ":" not in rf:
                    return RdstResult(
                        False,
                        f"Invalid required-filter '{rf}'. Use format: table:col1,col2"
                    )
                table, cols = rf.split(":", 1)
                parsed_filters[table] = [c.strip() for c in cols.split(",")]
            config.restrictions.required_filters = parsed_filters

        # Set guards
        config.guards.require_where = require_where
        config.guards.require_limit = require_limit
        config.guards.no_select_star = no_select_star
        config.guards.max_tables = max_tables
        config.guards.cost_limit = cost_limit
        config.guards.max_estimated_rows = max_estimated_rows

        # Set limits
        config.limits.max_rows = max_rows
        config.limits.timeout_seconds = timeout

        try:
            path = self.manager.create(config)
            return RdstResult(
                True,
                f"Created guard '{name}' at {path}",
                data={"name": name, "path": str(path)},
            )
        except GuardExistsError:
            return RdstResult(False, f"Guard '{name}' already exists. Use 'rdst guard delete {name}' first.")

    def _create_from_intent(
        self,
        name: str,
        intent: str,
        schema_context: str | None,
    ) -> RdstResult:
        """Create guard from natural language intent using LLM."""
        print("Analyzing intent...")
        print()

        try:
            config = derive_rules_from_intent(
                intent=intent,
                name=name,
                schema_context=schema_context,
            )
        except ValueError as e:
            return RdstResult(False, f"Failed to derive rules: {e}")
        except Exception as e:
            return RdstResult(False, f"LLM error: {e}")

        # Display derived rules
        print("Derived rules:")
        print("-" * 40)
        print(format_derived_rules(config))
        print("-" * 40)
        print()

        # Ask for confirmation
        if sys.stdin.isatty():
            response = input("Save guard? [Y/n/edit]: ").strip().lower()
            if response == "n":
                return RdstResult(False, "Guard creation cancelled")
            elif response == "edit":
                return self._edit_derived_config(config)
        else:
            # Non-interactive: save without confirmation
            pass

        try:
            path = self.manager.create(config)
            return RdstResult(
                True,
                f"Created guard '{name}' at {path}",
                data={"name": name, "path": str(path), "derived": True},
            )
        except GuardExistsError:
            return RdstResult(False, f"Guard '{name}' already exists. Use 'rdst guard delete {name}' first.")

    def _edit_derived_config(self, config: GuardConfig) -> RdstResult:
        """Open derived config in editor for manual adjustment."""
        editor = os.environ.get("EDITOR", "vi")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)
            temp_path = f.name

        try:
            result = subprocess.run([editor, temp_path])
            if result.returncode != 0:
                return RdstResult(False, "Editor exited with error")

            with open(temp_path) as f:
                data = yaml.safe_load(f)

            edited_config = GuardConfig.from_dict(data)
            path = self.manager.create(edited_config)
            return RdstResult(
                True,
                f"Created guard '{edited_config.name}' at {path}",
                data={"name": edited_config.name, "path": str(path), "derived": True},
            )
        except GuardExistsError:
            return RdstResult(False, f"Guard '{config.name}' already exists.")
        finally:
            os.unlink(temp_path)

    def _list(self) -> RdstResult:
        """List all guards."""
        guards = list(self.manager.list_configs())

        if not guards:
            return RdstResult(True, "No guards configured. Create one with 'rdst guard create --name NAME'")

        # Build table output
        lines = []
        lines.append(f"{'NAME':<20} {'TYPE':<10} {'MASKS':<6} {'GUARDS':<20} {'MAX_ROWS':<10}")
        lines.append("-" * 70)

        for guard in guards:
            mask_count = len(guard.masking.patterns)
            guard_type = "derived" if guard.derived else "manual"

            # Build guards summary
            guards_list = []
            if guard.guards.require_where:
                guards_list.append("where")
            if guard.guards.require_limit:
                guards_list.append("limit")
            if guard.restrictions.required_filters:
                guards_list.append("filters")
            if guard.guards.max_estimated_rows:
                guards_list.append("est_rows")
            if guard.guards.max_tables:
                guards_list.append(f"tbl:{guard.guards.max_tables}")

            guards_str = ", ".join(guards_list) if guards_list else "-"
            if len(guards_str) > 20:
                guards_str = guards_str[:17] + "..."

            lines.append(
                f"{guard.name:<20} {guard_type:<10} {mask_count:<6} {guards_str:<20} {guard.limits.max_rows:<10}"
            )

        return RdstResult(True, "\n".join(lines), data={"guards": [g.name for g in guards]})

    def _show(self, name: str | None) -> RdstResult:
        """Show guard details."""
        if not name:
            return RdstResult(False, "Guard name required")

        try:
            config = self.manager.get(name)
        except GuardNotFoundError:
            return RdstResult(False, f"Guard '{name}' not found")

        # Format output
        lines = [f"Guard: {config.name}"]
        if config.description:
            lines.append(f"Description: {config.description}")

        # Show intent if present
        if config.intent:
            lines.append(f"Intent: {config.intent}")
            if config.derived:
                lines.append("Derived: Yes (rules auto-generated from intent)")

        lines.append(f"Created: {config.created_at}")
        lines.append("")

        # Masking
        if config.masking.patterns:
            lines.append("Masking:")
            for col, mask_type in config.masking.patterns.items():
                lines.append(f"  {col}: {mask_type}")
            lines.append("")

        # Restrictions
        if config.has_restrictions():
            lines.append("Restrictions:")
            if config.restrictions.denied_columns:
                lines.append(f"  Denied columns: {', '.join(config.restrictions.denied_columns)}")
            if config.restrictions.allowed_tables:
                lines.append(f"  Allowed tables: {', '.join(config.restrictions.allowed_tables)}")
            if config.restrictions.required_filters:
                lines.append("  Required filters:")
                for table, cols in config.restrictions.required_filters.items():
                    lines.append(f"    {table}: {', '.join(cols)}")
            lines.append("")

        # Guards
        if config.has_guards():
            lines.append("Query Guards:")
            if config.guards.require_where:
                lines.append("  require_where: true")
            if config.guards.require_limit:
                lines.append("  require_limit: true")
            if config.guards.no_select_star:
                lines.append("  no_select_star: true")
            if config.guards.max_tables:
                lines.append(f"  max_tables: {config.guards.max_tables}")
            if config.guards.cost_limit:
                lines.append(f"  cost_limit: {config.guards.cost_limit}")
            if config.guards.max_estimated_rows:
                lines.append(f"  max_estimated_rows: {config.guards.max_estimated_rows:,}")
            lines.append("")

        # Limits
        lines.append("Limits:")
        lines.append(f"  max_rows: {config.limits.max_rows}")
        lines.append(f"  timeout_seconds: {config.limits.timeout_seconds}")

        return RdstResult(True, "\n".join(lines), data=config.to_dict())

    def _delete(self, name: str | None) -> RdstResult:
        """Delete a guard."""
        if not name:
            return RdstResult(False, "Guard name required")

        try:
            self.manager.delete(name)
            return RdstResult(True, f"Deleted guard '{name}'")
        except GuardNotFoundError:
            return RdstResult(False, f"Guard '{name}' not found")

    def _edit(self, name: str | None) -> RdstResult:
        """Edit guard in $EDITOR."""
        if not name:
            return RdstResult(False, "Guard name required")

        try:
            config = self.manager.get(name)
        except GuardNotFoundError:
            return RdstResult(False, f"Guard '{name}' not found")

        editor = os.environ.get("EDITOR", "vi")

        # Write to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)
            temp_path = f.name

        try:
            # Open editor
            result = subprocess.run([editor, temp_path])
            if result.returncode != 0:
                return RdstResult(False, "Editor exited with error")

            # Read back and validate
            with open(temp_path) as f:
                data = yaml.safe_load(f)

            # Ensure name wasn't changed
            if data.get("name") != name:
                return RdstResult(
                    False,
                    f"Cannot change guard name. Got '{data.get('name')}', expected '{name}'"
                )

            # Update config
            updated_config = GuardConfig.from_dict(data)
            self.manager.update(updated_config)

            return RdstResult(True, f"Updated guard '{name}'")

        finally:
            os.unlink(temp_path)

    def _check(
        self,
        sql: str | None,
        guard_name: str | None,
        target: str | None,
    ) -> RdstResult:
        """Check SQL against a guard."""
        if not sql:
            return RdstResult(False, "SQL required. Use --sql 'SELECT ...' or positional argument")

        if not guard_name:
            return RdstResult(False, "Guard name required. Use --guard NAME")

        try:
            config = self.manager.get(guard_name)
        except GuardNotFoundError:
            return RdstResult(False, f"Guard '{guard_name}' not found")

        # Import checker (lazy to avoid circular imports)
        from ..guard.checker import check_query

        results = check_query(sql, config, target_name=target)

        # Format output
        lines = [f"Guard: {guard_name}", ""]
        lines.append("Checks:")

        passed = True
        warnings = []
        for result in results:
            if result.passed:
                symbol = "✓"
            elif result.level == "warn":
                symbol = "⚠"
                warnings.append(result)
            else:
                symbol = "✗"
                passed = False

            lines.append(f"  {symbol} {result.message}")
            if result.suggestion and not result.passed:
                lines.append(f"      → {result.suggestion}")

        lines.append("")
        if not passed:
            lines.append("Result: BLOCKED")
        elif warnings:
            lines.append("Result: ALLOWED (with warnings)")
        else:
            lines.append("Result: ALLOWED")

        return RdstResult(
            passed,
            "\n".join(lines),
            data={
                "guard": guard_name,
                "sql": sql,
                "passed": passed,
                "results": [r.__dict__ for r in results],
            },
        )
