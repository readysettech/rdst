"""
Agent Runtime

Wraps Ask3Engine with safety enforcement for data agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
import logging
import time

import sqlglot
from sqlglot import exp

from .config import AgentConfig
from shared.config.targets import create_targets_config
from shared.query_safety import (
    GENERATED_SQL_DENIED_FUNCTIONS, code_views, side_effect_calls, statement_count)

from features.guard import GuardManager, GuardNotFoundError
from features.guard.access import (
    Tables, default_schema_for, denied_column_violation, disallowed_table_violation)

if TYPE_CHECKING:
    from .conversation import ConversationSession

logger = logging.getLogger(__name__)

# How long a failed read of the target's schema stands before a statement tries it again.
SCHEMA_RETRY_SECONDS = 60.0


def _get_guard_module():
    """Lazy import of the guard package for check_query/mask_results."""
    import features.guard

    return features.guard

class SafetyViolationError(Exception):
    """SQL violates agent safety policy."""

    pass


@dataclass
class AgentResponse:
    """Response from an agent query."""

    success: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    truncated: bool = False
    error: str | None = None
    explanation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {"success": self.success}

        if self.sql:
            result["sql"] = self.sql
        if self.columns:
            result["columns"] = self.columns
        if self.rows:
            result["rows"] = self.rows
            result["row_count"] = self.row_count
        if self.execution_time_ms:
            result["execution_time_ms"] = self.execution_time_ms
        if self.truncated:
            result["truncated"] = self.truncated
        if self.error:
            result["error"] = self.error
        if self.explanation:
            result["explanation"] = self.explanation

        return result


# Statements, clauses and commands that write, lock or leave the query: a data-modifying CTE
# sits inside a SELECT, so the whole tree is checked, not just its top.
_NOT_READ_ONLY_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Into", "Lock", "Command", "Copy", "Set", "Grant", "Revoke", "Transaction", "Commit",
        "Rollback", "Use", "LoadData",
    ) if hasattr(exp, name)
)


def validate_read_only(sql: str, dialect: str = "") -> tuple[bool, str]:
    """
    Ensure SQL is a single read-only statement (SELECT/WITH only).

    Args:
        sql: SQL to validate.
        dialect: Target engine; selects the quoting rules and the parser.

    Returns:
        Tuple of (is_valid, error_message).
    """
    # Read-only statement types (compound SELECTs like UNION are allowed)
    READ_ONLY_KEYS = {"select", "with", "union", "intersect", "except"}

    views = code_views(sql, dialect)
    if not views:
        return False, "Could not verify SQL is read-only: a string, quoted name or comment is open"
    if any(statement_count(view) != 1 for view in views):
        return False, "Only a single statement is allowed"
    if any(view.escaped_name for view in views):
        return False, "Unicode-escaped quoted names are not allowed"
    calls = side_effect_calls(sql, dialect, GENERATED_SQL_DENIED_FUNCTIONS)
    if calls:
        return False, f"Function not allowed: {calls[0].lower()}()"

    read = {"postgresql": "postgres", "postgres": "postgres", "mysql": "mysql"}.get(
        (dialect or "").lower())
    try:
        parsed = [statement for statement in sqlglot.parse(sql, read=read) if statement]
    except Exception as e:
        # Unparseable SQL cannot be shown to be read-only, and the agent is the
        # component this check exists to constrain, so refuse it.
        logger.warning(f"Could not parse SQL for read-only check: {e}")
        return False, f"Could not verify SQL is read-only: {e}"
    if len(parsed) != 1:
        return False, "Only a single statement is allowed"
    key = parsed[0].key.lower()
    if key not in READ_ONLY_KEYS:
        return False, f"Write operation not allowed: {key.upper()}"
    for node in parsed[0].walk():
        if isinstance(node, _NOT_READ_ONLY_NODES):
            return False, f"Write operation not allowed: {node.key.upper()}"
        name = node.name if isinstance(node, exp.Anonymous) else ""
        if name.upper() in GENERATED_SQL_DENIED_FUNCTIONS:
            return False, f"Function not allowed: {name.lower()}()"
    return True, ""


def inject_limit(sql: str, max_rows: int) -> str:
    """
    Add LIMIT if not present.

    Args:
        sql: SQL to modify.
        max_rows: Maximum rows to return.

    Returns:
        SQL with LIMIT clause.
    """
    try:
        parsed = sqlglot.parse_one(sql)
        if parsed.find(sqlglot.exp.Limit) is None:
            parsed = parsed.limit(max_rows)
        return parsed.sql()
    except Exception as e:
        logger.warning(f"Could not inject LIMIT: {e}")
        return sql


def validate_columns(
    sql: str,
    denied_columns: list[str] | None,
    *,
    dialect: str = "",
    tables: Tables = None,
    default_schema: str | None = None,
) -> tuple[bool, str]:
    """
    Check SQL doesn't read denied columns, through aliases, CTEs and stars included.

    Args:
        sql: SQL to validate.
        denied_columns: List of column patterns to deny (e.g., "customers.ssn").
        dialect: Target engine.
        tables: How the target resolves table names and their columns (``SchemaInfo``), or
            the default schema's tables and their columns, so stars resolve.
        default_schema: Schema an unqualified table name resolves to, when known exactly.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not denied_columns:
        return True, ""
    violation = denied_column_violation(
        sql, denied_columns, dialect=dialect, tables=tables, default_schema=default_schema
    )
    return (False, violation) if violation else (True, "")


def validate_tables(
    sql: str,
    allowed_tables: list[str] | None,
    *,
    dialect: str = "",
    default_schema: str | None = None,
    tables: Tables = None,
) -> tuple[bool, str]:
    """
    Check SQL only reads allowed tables (None = all allowed).

    Args:
        sql: SQL to validate.
        allowed_tables: List of allowed tables; one without a schema is allowed in any
            schema, ``schema.table`` only in that schema.
        dialect: Target engine.
        default_schema: Schema an unqualified table name resolves to, when known exactly.
        tables: How the target resolves table names (``SchemaInfo``).

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not allowed_tables:
        return True, ""
    violation = disallowed_table_violation(
        sql, allowed_tables, dialect=dialect, default_schema=default_schema, tables=tables
    )
    return (False, violation) if violation else (True, "")


class AgentRuntime:
    """
    Runtime for executing agent queries with safety enforcement.

    Wraps Ask3Engine to provide:
    - Read-only enforcement
    - LIMIT injection
    - Column denial/masking
    - Table whitelisting
    - Query timeout
    - Guard-based policies (when guard is specified)
    """

    def __init__(self, agent_config: AgentConfig):
        """
        Initialize the runtime.

        Args:
            agent_config: Agent configuration.
        """
        self.config = agent_config
        self._engine = None
        self._target_config = None
        self._guard_config = None
        self._tables: Tables = None
        self._tables_retry_at = 0.0

    def _get_guard_config(self):
        """Load guard configuration if agent has a guard reference."""
        if self._guard_config is None and self.config.guard:
            manager = GuardManager()
            try:
                self._guard_config = manager.get(self.config.guard)
            except GuardNotFoundError:
                logger.warning(f"Guard '{self.config.guard}' not found, using inline config")
        return self._guard_config

    def _get_engine(self):
        """Lazy-load Ask3Engine."""
        if self._engine is None:
            from features.ask.engine.ask3.engine import Ask3Engine

            # Silent presenter for agent mode — suppress all output
            from features.ask.engine.ask3.presenter import QuietPresenter
            class SilentPresenter(QuietPresenter):
                def sql_generated(self, sql, explanation=None) -> None:
                    pass

                def execution_result(self, columns, rows, time_ms, truncated=False) -> None:
                    pass

                def interpretations(self, items) -> None:
                    pass

                def clarification_question(self, question, options) -> None:
                    pass

            self._engine = Ask3Engine(presenter=SilentPresenter())

        return self._engine

    def _get_target_config(self) -> dict[str, Any]:
        """Load target configuration."""
        if self._target_config is None:
            cfg = create_targets_config()
            cfg.load()

            self._target_config = cfg.get(self.config.target)
            if not self._target_config:
                raise ValueError(f"Target '{self.config.target}' not found in configuration")

        return self._target_config

    def _has_restrictions(self) -> bool:
        restrictions = self.config.restrictions
        return bool(restrictions.denied_columns or restrictions.allowed_tables)

    def _restriction_tables(self) -> Tables:
        """How the target resolves table names and their columns, read once, for table
        and column restrictions.

        A failed read stands for ``SCHEMA_RETRY_SECONDS`` instead of being repeated for
        every statement; meanwhile nothing is known, so stars and unqualified names checked
        against a schema-qualified entry are refused. Never None, so ``check_query`` does
        not read the schema again.
        """
        now = time.monotonic()
        if self._tables is None and self._target_config and now >= self._tables_retry_at:
            from features.guard.checker import load_default_schema_tables

            self._tables = load_default_schema_tables(self._target_config, self.config.target)
            if self._tables is None:
                self._tables_retry_at = now + SCHEMA_RETRY_SECONDS
        return self._tables or {}

    def _validate_safety(self, sql: str) -> None:
        """
        Validate SQL against safety configuration.

        Uses guard config if agent has a guard reference, otherwise falls back
        to inline safety/restrictions config.

        Args:
            sql: SQL to validate.

        Raises:
            SafetyViolationError: If SQL violates safety policy.
        """
        guard = self._get_guard_config()
        dialect = str((self._target_config or {}).get("engine") or "")
        default_schema = default_schema_for(self._target_config)

        if guard:
            # A guard always means read-only, and its own read-only check looks only at the
            # leading keyword.
            ok, msg = validate_read_only(sql, dialect)
            if not ok:
                raise SafetyViolationError(msg)

            # Use guard-based validation
            check_query = _get_guard_module().check_query

            # Cost and row-estimate guards need a connection; without the target
            # they report nothing and the policy silently loses those checks.
            results = check_query(
                sql,
                guard,
                target_name=self.config.target,
                target_config=self._target_config,
                tables=(self._restriction_tables() if guard.restrictions.denied_columns
                        or guard.restrictions.allowed_tables else None),
            )
            for result in results:
                if not result.passed and result.level == "block":
                    raise SafetyViolationError(result.message)
        else:
            # Use legacy inline config
            # Check read-only
            if self.config.safety.read_only:
                ok, msg = validate_read_only(sql, dialect)
                if not ok:
                    raise SafetyViolationError(msg)

            # Check denied columns
            denied = self.config.restrictions.denied_columns
            ok, msg = validate_columns(
                sql, denied, dialect=dialect, default_schema=default_schema,
                tables=self._restriction_tables() if denied else None,
            )
            if not ok:
                raise SafetyViolationError(msg)

            # Check allowed tables
            allowed = self.config.restrictions.allowed_tables
            ok, msg = validate_tables(
                sql, allowed, dialect=dialect, default_schema=default_schema,
                tables=self._restriction_tables() if allowed else None,
            )
            if not ok:
                raise SafetyViolationError(msg)

    def _apply_masking(self, columns: list[str], rows: list[list[Any]]) -> list[list[Any]]:
        """
        Apply masking to query results.

        Uses guard config if agent has a guard reference, otherwise falls back
        to inline restrictions config.

        Args:
            columns: Column names from query result.
            rows: Row data from query result.

        Returns:
            Masked rows.
        """
        guard = self._get_guard_config()

        if guard and guard.masking.patterns:
            mask_results = _get_guard_module().mask_results
            return mask_results(columns, rows, guard.masking.patterns)
        elif self.config.restrictions.masked_columns:
            mask_results = _get_guard_module().mask_results
            return mask_results(columns, rows, self.config.restrictions.masked_columns)

        return rows

    def ask(self, question: str) -> AgentResponse:
        """
        Execute a natural language question.

        Args:
            question: Natural language question.

        Returns:
            AgentResponse with results.
        """
        try:
            engine = self._get_engine()
            target_config = self._get_target_config()

            # Determine database type
            db_type = target_config.get("db_type", "postgresql")

            # Get limits from guard or inline config
            guard = self._get_guard_config()
            if guard:
                max_rows = guard.limits.max_rows
                timeout_seconds = guard.limits.timeout_seconds
            else:
                max_rows = self.config.safety.max_rows
                timeout_seconds = self.config.safety.timeout_seconds

            # Create pre-execution validator callback if guard is configured
            pre_execute_validator = None
            if guard or self.config.safety.read_only or self._has_restrictions():
                def pre_execute_validator(sql: str) -> None:
                    self._validate_safety(sql)

            # Run Ask3Engine with pre-execution validation
            ctx = engine.run(
                question=question,
                target=self.config.target,
                target_config=target_config,
                db_type=db_type,
                max_rows=max_rows,
                timeout_seconds=timeout_seconds,
                no_interactive=True,
                pre_execute_validator=pre_execute_validator,
            )

            # Check if successful
            from features.ask.engine.ask3.types import Status

            if ctx.status != Status.SUCCESS:
                return AgentResponse(
                    success=False,
                    error=ctx.error_message or "Query failed",
                    sql=ctx.sql,
                )

            # Build response with masking applied
            result = ctx.execution_result
            columns = result.columns if result else []
            rows = result.rows if result else []

            # Apply masking to results
            masked_rows = self._apply_masking(columns, rows)

            return AgentResponse(
                success=True,
                sql=ctx.sql,
                columns=columns,
                rows=masked_rows,
                row_count=result.row_count if result else 0,
                execution_time_ms=result.execution_time_ms if result else 0.0,
                truncated=result.truncated if result else False,
                explanation=ctx.sql_explanation,
            )

        except Exception as e:
            logger.exception("Agent query failed")
            return AgentResponse(
                success=False,
                error=str(e),
            )

    def ask_with_history(
        self,
        question: str,
        session: "ConversationSession | None" = None,
        interactive: bool = True,
    ) -> AgentResponse:
        """
        Execute a natural language question with conversation context.

        This method enables follow-up questions by including previous
        conversation exchanges in the LLM context.

        Args:
            question: Natural language question.
            session: Active conversation session with history (optional).
            interactive: If True, allow clarification prompts (for terminal use).
                        If False, auto-select first interpretation (for API use).

        Returns:
            AgentResponse with results.
        """
        try:
            engine = self._get_engine()
            target_config = self._get_target_config()

            # Determine database type
            db_type = target_config.get("db_type", "postgresql")

            # Build conversation context from session history
            conversation_context = ""
            if session and session.turns:
                conversation_context = session.format_history()

            # Get limits from guard or inline config
            guard = self._get_guard_config()
            if guard:
                max_rows = guard.limits.max_rows
                timeout_seconds = guard.limits.timeout_seconds
            else:
                max_rows = self.config.safety.max_rows
                timeout_seconds = self.config.safety.timeout_seconds

            # Create pre-execution validator callback if guard is configured
            pre_execute_validator = None
            if guard or self.config.safety.read_only or self._has_restrictions():
                def pre_execute_validator(sql: str) -> None:
                    self._validate_safety(sql)

            # Run Ask3Engine with conversation context and pre-execution validation
            ctx = engine.run(
                question=question,
                target=self.config.target,
                target_config=target_config,
                db_type=db_type,
                max_rows=max_rows,
                timeout_seconds=timeout_seconds,
                no_interactive=not interactive,  # Allow clarifications in interactive mode
                conversation_context=conversation_context,
                pre_execute_validator=pre_execute_validator,
            )

            # Check if successful
            from features.ask.engine.ask3.types import Status

            if ctx.status != Status.SUCCESS:
                return AgentResponse(
                    success=False,
                    error=ctx.error_message or "Query failed",
                    sql=ctx.sql,
                )

            # Build response with masking applied
            result = ctx.execution_result
            columns = result.columns if result else []
            rows = result.rows if result else []

            # Apply masking to results
            masked_rows = self._apply_masking(columns, rows)

            return AgentResponse(
                success=True,
                sql=ctx.sql,
                columns=columns,
                rows=masked_rows,
                row_count=result.row_count if result else 0,
                execution_time_ms=result.execution_time_ms if result else 0.0,
                truncated=result.truncated if result else False,
                explanation=ctx.sql_explanation,
            )

        except Exception as e:
            logger.exception("Agent query failed")
            return AgentResponse(
                success=False,
                error=str(e),
            )

    def get_schema_summary(self) -> dict[str, Any]:
        """
        Get a summary of the database schema.

        Returns:
            Dictionary with schema information.
        """
        try:
            from features.schema.semantic_layer.manager import SemanticLayerManager

            mgr = SemanticLayerManager()
            if mgr.exists(self.config.target):
                layer = mgr.load(self.config.target)
                tables = []
                for name, table in layer.tables.items():
                    tables.append({
                        "name": name,
                        "description": table.description,
                        "columns": list(table.columns.keys()),
                    })
                return {"tables": tables, "source": "semantic_layer"}

            # Fall back to database introspection
            from features.schema.schema_collector import collect_schema, build_schema_hint

            target_config = self._get_target_config()
            schema = collect_schema(target_config)
            hint = build_schema_hint(self.config.target, False, False)
            if schema:
                tables = [{"name": t, "columns": list(cols.keys())} for t, cols in schema.items()]
                return {"tables": tables, "source": "database", "schema_hint": hint}

            return {"tables": [], "source": "unknown", "schema_hint": hint}

        except Exception as e:
            logger.exception("Failed to get schema summary")
            return {"tables": [], "error": str(e)}
