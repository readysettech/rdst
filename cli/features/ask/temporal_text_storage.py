"""Repair model-routed time literals after an empty query result."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

import sqlglot
from sqlglot import exp

TEMPORAL_TEXT_STORAGE_VERSION = "model-routed-temporal-text-prefix-v1"
MAX_PROBES = 1

_TWO_PART_CLOCK = re.compile(
    r"(?P<minutes>\d{1,3}):(?P<seconds>[0-5]\d)"
    r"(?:\.(?P<fraction>\d+))?"
)
_THREE_PART_CLOCK = re.compile(
    r"(?P<hours>\d{1,2}):(?P<minutes>[0-5]\d):"
    r"(?P<seconds>[0-5]\d)(?:\.(?P<fraction>\d+))?"
)
_TEXT_TIME_TYPES = frozenset(
    {
        "char",
        "character",
        "character varying",
        "enum",
        "text",
        "varchar",
    }
)


def _read_dialect(dialect: str) -> str:
    return "postgres" if dialect in {"postgres", "postgresql"} else "mysql"


def _literal_prefix(literal: str) -> str | None:
    match = _TWO_PART_CLOCK.fullmatch(literal)
    if match is not None:
        return f"{int(match.group('minutes'))}:{match.group('seconds')}"
    match = _THREE_PART_CLOCK.fullmatch(literal)
    if match is None or int(match.group("hours")) != 0:
        return None
    return f"{int(match.group('minutes'))}:{match.group('seconds')}"


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            return {}
        alias = table.alias_or_name.casefold()
        name = table.name
        previous = aliases.get(alias)
        if previous is not None and previous.casefold() != name.casefold():
            return {}
        aliases[alias] = name
    return aliases


def _schema_column(schema_info: Any, table_name: str, column_name: str) -> Any | None:
    if schema_info is None:
        return None
    for name, table in schema_info.tables.items():
        if name.casefold() != table_name.casefold():
            continue
        for candidate_name, column in table.columns.items():
            if candidate_name.casefold() == column_name.casefold():
                return column
    return None


def _is_text_time_column(column: Any) -> bool:
    data_type = str(getattr(column, "data_type", ""))
    return data_type.casefold().split("(", 1)[0].strip() in _TEXT_TIME_TYPES


def _scope_query(
    tree: exp.Select,
    *,
    target_column: exp.Column,
    source_literal: str,
    prefix: str | None,
    dialect: str,
) -> str | None:
    read_dialect = _read_dialect(dialect)
    scope = tree.copy()
    matches: list[tuple[exp.EQ, exp.Column]] = []
    for predicate in scope.find_all(exp.EQ):
        column = predicate.this
        literal = predicate.expression
        if (
            isinstance(column, exp.Column)
            and isinstance(literal, exp.Literal)
            and literal.is_string
            and column.name.casefold() == target_column.name.casefold()
            and (column.table or "").casefold()
            == (target_column.table or "").casefold()
            and str(literal.this) == source_literal
        ):
            matches.append((predicate, column))
    if len(matches) != 1:
        return None
    predicate, column = matches[0]
    if prefix is not None:
        predicate.replace(
            exp.Paren(
                this=exp.Or(
                    this=exp.EQ(
                        this=column.copy(),
                        expression=exp.Literal.string(prefix),
                    ),
                    expression=exp.Like(
                        this=column.copy(),
                        expression=exp.Literal.string(f"{prefix}.%"),
                    ),
                )
            )
        )
    scope.set("expressions", [exp.Literal.number(1)])
    scope.set("order", None)
    scope.set("limit", exp.Limit(expression=exp.Literal.number(1)))
    scope.set("offset", None)
    scope.set("locks", None)
    return scope.sql(dialect=read_dialect)


def _support_query(
    *,
    tree: exp.Select,
    target_column: exp.Column,
    source_literal: str,
    prefix: str,
    dialect: str,
) -> str | None:
    exact_scope = _scope_query(
        tree,
        target_column=target_column,
        source_literal=source_literal,
        prefix=None,
        dialect=dialect,
    )
    prefix_scope = _scope_query(
        tree,
        target_column=target_column,
        source_literal=source_literal,
        prefix=prefix,
        dialect=dialect,
    )
    if exact_scope is None or prefix_scope is None:
        return None
    return (
        "SELECT "
        f"EXISTS({exact_scope}), "
        f"EXISTS({prefix_scope})"
    )


def _probe_support(
    db_executor: Callable[..., Any],
    target_config: dict[str, Any],
    *,
    tree: exp.Select,
    target_column: exp.Column,
    source_literal: str,
    prefix: str,
    dialect: str,
) -> tuple[bool | None, str]:
    query = _support_query(
        tree=tree,
        target_column=target_column,
        source_literal=source_literal,
        prefix=prefix,
        dialect=dialect,
    )
    if query is None:
        return None, hashlib.sha256(b"scope-query-unavailable").hexdigest()
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    try:
        result = db_executor(query, target_config)
        rows = result.get("rows") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not result.get("success") or not rows:
            return None, query_hash
        exact_exists, prefix_exists = (bool(int(value)) for value in rows[0])
        return not exact_exists and prefix_exists, query_hash
    except Exception:
        return None, query_hash


def temporal_text_storage_shape(sql: str, dialect: str) -> tuple[str, ...]:
    """Return syntax facts only; the model router supplies semantic intent."""
    try:
        statements = sqlglot.parse(sql, read=_read_dialect(dialect.casefold()))
    except (sqlglot.errors.SqlglotError, ValueError):
        return ()
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return ()
    candidates = [
        predicate
        for predicate in statements[0].find_all(exp.EQ)
        if isinstance(predicate.this, exp.Column)
        and isinstance(predicate.expression, exp.Literal)
        and predicate.expression.is_string
        and _literal_prefix(str(predicate.expression.this)) is not None
    ]
    if len(candidates) != 1:
        return ()
    return (
        "one-column-equality-uses-a-two-part-or-zero-hour-clock-string",
        "candidate-requires-an-empty-primary-result-and-database-support",
    )


def normalize_temporal_text_storage_sql(
    *,
    sql: str,
    dialect: str,
    schema_info: Any,
    db_executor: Callable[..., Any] | None,
    target_config: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Rewrite one source-derived exact clock comparison after an empty result."""
    diagnostics: dict[str, Any] = {
        "version": TEMPORAL_TEXT_STORAGE_VERSION,
        "status": "unchanged",
        "probe_count": 0,
        "candidate_execution_count": 0,
        "execution_feedback": True,
        "database_value_feedback": True,
    }
    normalized_dialect = dialect.casefold()
    if normalized_dialect not in {"mysql", "postgres", "postgresql"}:
        diagnostics["reason"] = "unsupported-dialect"
        return sql, diagnostics
    if db_executor is None or target_config is None:
        diagnostics["reason"] = "database-probe-unavailable"
        return sql, diagnostics

    read_dialect = _read_dialect(normalized_dialect)
    try:
        statements = sqlglot.parse(sql, read=read_dialect)
    except sqlglot.errors.SqlglotError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        diagnostics["reason"] = "not-one-statement"
        return sql, diagnostics
    tree = statements[0]
    if (
        len(list(tree.find_all(exp.Select))) != 1
        or tree.find(exp.AggFunc) is not None
        or tree.args.get("limit") is not None
        or tree.args.get("offset") is not None
        or tree.args.get("locks")
    ):
        diagnostics["reason"] = "unsupported-query-shape"
        return sql, diagnostics
    aliases = _alias_map(tree)
    unique_tables = {table.casefold() for table in aliases.values()}

    candidates: list[tuple[exp.EQ, exp.Column, str, str]] = []
    for predicate in tree.find_all(exp.EQ):
        column = predicate.this
        literal = predicate.expression
        if not isinstance(column, exp.Column) or not isinstance(literal, exp.Literal):
            continue
        if not literal.is_string:
            continue
        prefix = _literal_prefix(str(literal.this))
        if prefix is None:
            continue
        if column.table:
            table_name = aliases.get(column.table.casefold())
        elif len(unique_tables) == 1:
            table_name = next(iter(aliases.values()), None)
        else:
            table_name = None
        if not table_name:
            continue
        schema_column = _schema_column(schema_info, table_name, column.name)
        if (
            schema_column is None
            or not _is_text_time_column(schema_column)
        ):
            continue
        candidates.append((predicate, column, table_name, prefix))

    if len(candidates) != 1:
        diagnostics["reason"] = (
            "no-eligible-clock-equality"
            if not candidates
            else "multiple-eligible-clock-equalities"
        )
        return sql, diagnostics

    predicate, column, table_name, prefix = candidates[0]
    source_literal = str(predicate.expression.this)
    supported, probe_hash = _probe_support(
        db_executor,
        target_config,
        tree=tree,
        target_column=column,
        source_literal=source_literal,
        prefix=prefix,
        dialect=normalized_dialect,
    )
    diagnostics.update(
        {
            "probe_count": MAX_PROBES,
            "probe_sha256": [probe_hash],
            "source_column": f"{table_name}.{column.name}",
        }
    )
    if supported is None:
        diagnostics["reason"] = "database-probe-failed"
        return sql, diagnostics
    if not supported:
        diagnostics["reason"] = "source-prefix-unsupported"
        return sql, diagnostics

    predicate.replace(
        exp.Paren(
            this=exp.Or(
                this=exp.EQ(
                    this=column.copy(),
                    expression=exp.Literal.string(prefix),
                ),
                expression=exp.Like(
                    this=column.copy(),
                    expression=exp.Literal.string(f"{prefix}.%"),
                ),
            )
        )
    )
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "selection_policy": "single-source-clock-supported-prefix-v3",
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any, db_executor: Callable[..., Any] | None) -> Any:
    sql = ctx.sql or ""
    result = getattr(ctx, "execution_result", None)
    if (
        result is None
        or result.error
        or result.truncated
        or result.row_count != 0
    ):
        ctx.temporal_text_storage = {
            "version": TEMPORAL_TEXT_STORAGE_VERSION,
            "status": "unchanged",
            "probe_count": 0,
            "candidate_execution_count": 0,
            "execution_feedback": True,
            "database_value_feedback": True,
            "reason": "primary-result-not-empty-and-complete",
        }
        return ctx
    normalized, diagnostics = normalize_temporal_text_storage_sql(
        sql=sql,
        dialect=ctx.db_type,
        schema_info=ctx.schema_info,
        db_executor=db_executor,
        target_config=ctx.target_config,
    )
    ctx.temporal_text_storage = diagnostics
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    return ctx


__all__ = [
    "MAX_PROBES",
    "TEMPORAL_TEXT_STORAGE_VERSION",
    "normalize_context",
    "normalize_temporal_text_storage_sql",
    "temporal_text_storage_shape",
]
