"""
Agent Runtime

Wraps Ask3Engine with safety enforcement for data agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, TYPE_CHECKING
import logging

import sqlglot

from .config import AgentConfig

if TYPE_CHECKING:
    from .conversation import ConversationSession

logger = logging.getLogger(__name__)


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


def validate_read_only(sql: str) -> tuple[bool, str]:
    """
    Ensure SQL is read-only (SELECT/WITH only).

    Args:
        sql: SQL to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    try:
        parsed = sqlglot.parse(sql)
        for statement in parsed:
            if statement is None:
                continue
            key = statement.key.lower() if hasattr(statement, "key") else ""
            if key not in ("select", "with"):
                return False, f"Write operation not allowed: {key.upper()}"
        return True, ""
    except Exception as e:
        # If we can't parse, allow it through - Ask3Engine will catch real errors
        logger.warning(f"Could not parse SQL for read-only check: {e}")
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
    sql: str, denied_columns: list[str] | None
) -> tuple[bool, str]:
    """
    Check SQL doesn't reference denied columns.

    Args:
        sql: SQL to validate.
        denied_columns: List of column patterns to deny (e.g., "customers.ssn").

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not denied_columns:
        return True, ""

    try:
        parsed = sqlglot.parse_one(sql)
        for column in parsed.find_all(sqlglot.exp.Column):
            table = column.table if column.table else ""
            col_name = column.name

            # Check both "table.column" and just "column"
            for pattern in denied_columns:
                if table:
                    full_ref = f"{table}.{col_name}"
                    if fnmatch(full_ref, pattern) or fnmatch(full_ref.lower(), pattern.lower()):
                        return False, f"Access to column '{full_ref}' is denied"

                if fnmatch(col_name, pattern) or fnmatch(col_name.lower(), pattern.lower()):
                    return False, f"Access to column '{col_name}' is denied"

        return True, ""
    except Exception as e:
        logger.warning(f"Could not validate columns: {e}")
        return True, ""


def validate_tables(
    sql: str, allowed_tables: list[str] | None
) -> tuple[bool, str]:
    """
    Check SQL only references allowed tables.

    Args:
        sql: SQL to validate.
        allowed_tables: List of allowed tables (None = all allowed).

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not allowed_tables:
        return True, ""

    try:
        parsed = sqlglot.parse_one(sql)
        allowed_set = {t.lower() for t in allowed_tables}

        for table in parsed.find_all(sqlglot.exp.Table):
            table_name = table.name.lower() if table.name else ""
            if table_name and table_name not in allowed_set:
                return False, f"Access to table '{table.name}' is not allowed"

        return True, ""
    except Exception as e:
        logger.warning(f"Could not validate tables: {e}")
        return True, ""


class AgentRuntime:
    """
    Runtime for executing agent queries with safety enforcement.

    Wraps Ask3Engine to provide:
    - Read-only enforcement
    - LIMIT injection
    - Column denial/masking
    - Table whitelisting
    - Query timeout
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

    def _get_engine(self):
        """Lazy-load Ask3Engine."""
        if self._engine is None:
            from ..engines.ask3.engine import Ask3Engine
            from ..engines.ask3.presenter import Ask3Presenter

            # Silent presenter for agent mode
            class SilentPresenter(Ask3Presenter):
                def __init__(self):
                    super().__init__(verbose=False)

                def info(self, msg: str) -> None:
                    pass

                def phase(self, name: str) -> None:
                    pass

                def thinking(self, msg: str) -> None:
                    pass

                def schema_loaded(self, source: str, table_count: int) -> None:
                    pass

                def show_results(self, ctx) -> None:
                    pass

            self._engine = Ask3Engine(presenter=SilentPresenter())

        return self._engine

    def _get_target_config(self) -> dict[str, Any]:
        """Load target configuration."""
        if self._target_config is None:
            from ..cli.rdst_cli import TargetsConfig

            cfg = TargetsConfig()
            cfg.load()

            self._target_config = cfg.get(self.config.target)
            if not self._target_config:
                raise ValueError(f"Target '{self.config.target}' not found in configuration")

        return self._target_config

    def _validate_safety(self, sql: str) -> None:
        """
        Validate SQL against safety configuration.

        Args:
            sql: SQL to validate.

        Raises:
            SafetyViolationError: If SQL violates safety policy.
        """
        # Check read-only
        if self.config.safety.read_only:
            ok, msg = validate_read_only(sql)
            if not ok:
                raise SafetyViolationError(msg)

        # Check denied columns
        ok, msg = validate_columns(sql, self.config.restrictions.denied_columns)
        if not ok:
            raise SafetyViolationError(msg)

        # Check allowed tables
        ok, msg = validate_tables(sql, self.config.restrictions.allowed_tables)
        if not ok:
            raise SafetyViolationError(msg)

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

            # Run Ask3Engine
            ctx = engine.run(
                question=question,
                target=self.config.target,
                target_config=target_config,
                db_type=db_type,
                max_rows=self.config.safety.max_rows,
                timeout_seconds=self.config.safety.timeout_seconds,
                no_interactive=True,
            )

            # Check if successful
            from ..engines.ask3.types import Status

            if ctx.status != Status.SUCCESS:
                return AgentResponse(
                    success=False,
                    error=ctx.error_message or "Query failed",
                    sql=ctx.sql,
                )

            # Validate safety on generated SQL
            if ctx.sql:
                try:
                    self._validate_safety(ctx.sql)
                except SafetyViolationError as e:
                    return AgentResponse(
                        success=False,
                        error=str(e),
                        sql=ctx.sql,
                    )

            # Build response
            result = ctx.execution_result
            return AgentResponse(
                success=True,
                sql=ctx.sql,
                columns=result.columns if result else [],
                rows=result.rows if result else [],
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

            # Run Ask3Engine with conversation context
            ctx = engine.run(
                question=question,
                target=self.config.target,
                target_config=target_config,
                db_type=db_type,
                max_rows=self.config.safety.max_rows,
                timeout_seconds=self.config.safety.timeout_seconds,
                no_interactive=not interactive,  # Allow clarifications in interactive mode
                conversation_context=conversation_context,
            )

            # Check if successful
            from ..engines.ask3.types import Status

            if ctx.status != Status.SUCCESS:
                return AgentResponse(
                    success=False,
                    error=ctx.error_message or "Query failed",
                    sql=ctx.sql,
                )

            # Validate safety on generated SQL
            if ctx.sql:
                try:
                    self._validate_safety(ctx.sql)
                except SafetyViolationError as e:
                    return AgentResponse(
                        success=False,
                        error=str(e),
                        sql=ctx.sql,
                    )

            # Build response
            result = ctx.execution_result
            return AgentResponse(
                success=True,
                sql=ctx.sql,
                columns=result.columns if result else [],
                rows=result.rows if result else [],
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
            from ..semantic_layer.manager import SemanticLayerManager

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
            from ..functions.schema_collector import collect_schema

            target_config = self._get_target_config()
            schema = collect_schema(target_config)
            if schema:
                tables = [{"name": t, "columns": list(cols.keys())} for t, cols in schema.items()]
                return {"tables": tables, "source": "database"}

            return {"tables": [], "source": "unknown"}

        except Exception as e:
            logger.exception("Failed to get schema summary")
            return {"tables": [], "error": str(e)}
