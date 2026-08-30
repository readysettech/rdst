"""Repair model-routed endpoint identifiers in mirrored edge tables."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from shared.db_connection import quote_identifier


ENCODED_IDENTIFIER_STORAGE_VERSION = "model-routed-mirrored-edge-suffix-v1"
MAX_PROBES = 1
MIN_MIRRORED_GROUPS = 3

_TEXT_TYPES = frozenset(
    {"char", "character", "character varying", "text", "varchar"}
)
_EDGE_ID_TERMS = frozenset({"bond", "connection", "edge", "link", "relationship"})
_IDENTIFIER_TOKEN = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+"
)


def _identifier_tokens(identifier: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _IDENTIFIER_TOKEN.findall(identifier))


def _read_dialect(dialect: str) -> str:
    return "postgres" if dialect in {"postgres", "postgresql"} else "mysql"


def _schema_table(schema_info: Any, table_name: str) -> Any | None:
    if schema_info is None:
        return None
    for name, table in schema_info.tables.items():
        if name.casefold() == table_name.casefold():
            return table
    return None


def _schema_column(table: Any, column_name: str) -> Any | None:
    for name, column in table.columns.items():
        if name.casefold() == column_name.casefold():
            return column
    return None


def _is_text_column(column: Any) -> bool:
    data_type = str(getattr(column, "data_type", ""))
    return data_type.casefold().split("(", 1)[0].strip() in _TEXT_TYPES


def _endpoint_stem(identifier: str) -> str:
    return re.sub(r"(?:_?2|_?1)$", "", identifier.casefold())


def _edge_identifier_column(table: Any, endpoints: set[str]) -> Any | None:
    candidates = []
    for column in table.columns.values():
        folded = column.name.casefold()
        if folded in endpoints:
            continue
        terms = _identifier_tokens(column.name)
        if terms and terms[-1] == "id" and set(terms[:-1]) & _EDGE_ID_TERMS:
            candidates.append(column)
    return candidates[0] if len(candidates) == 1 else None


def _numeric_suffix(literal: str) -> str | None:
    canonical_literal = re.sub(r"[^a-z0-9]", "", literal.casefold())
    if canonical_literal.isdigit() and len(canonical_literal) <= 6:
        return f"_{canonical_literal}"
    literal_tokens = _identifier_tokens(literal)
    if len(canonical_literal) < 3 or not literal_tokens:
        return None
    number = literal_tokens[-1]
    if not number.isdigit() or len(number) > 6:
        return None
    return f"_{number}"


def _column_literal(predicate: exp.Expression) -> tuple[exp.Column, str] | None:
    if not isinstance(predicate, exp.EQ):
        return None
    column = predicate.this
    literal = predicate.expression
    if (
        not isinstance(column, exp.Column)
        or not isinstance(literal, exp.Literal)
        or not literal.is_string
    ):
        return None
    return column, str(literal.this)


def _endpoint_predicate(
    tree: exp.Expression,
) -> tuple[exp.Or, exp.Column, exp.Column, str] | None:
    where = tree.args.get("where")
    if not isinstance(where, exp.Where):
        return None
    expression = where.this
    while isinstance(expression, exp.Paren):
        expression = expression.this
    if not isinstance(expression, exp.Or):
        return None
    left = _column_literal(expression.this)
    right = _column_literal(expression.expression)
    if left is None or right is None or left[1] != right[1]:
        return None
    left_column, literal = left
    right_column, _ = right
    if left_column.name.casefold() == right_column.name.casefold():
        return None
    if _endpoint_stem(left_column.name) != _endpoint_stem(right_column.name):
        return None
    qualifiers = {
        column.table.casefold()
        for column in (left_column, right_column)
        if column.table
    }
    if len(qualifiers) > 1:
        return None
    return expression, left_column, right_column, literal


def _support_query(
    *,
    table: str,
    left_column: str,
    right_column: str,
    edge_column: str,
    literal: str,
    suffix: str,
    dialect: str,
) -> str:
    engine = "postgresql" if dialect in {"postgres", "postgresql"} else "mysql"
    read_dialect = _read_dialect(dialect)
    table_sql = quote_identifier(table, engine)
    left_sql = quote_identifier(left_column, engine)
    right_sql = quote_identifier(right_column, engine)
    edge_sql = quote_identifier(edge_column, engine)
    literal_sql = exp.Literal.string(literal).sql(dialect=read_dialect)
    suffix_sql = exp.Literal.string(suffix).sql(dialect=read_dialect)
    suffix_length = len(suffix)
    exact = f"({left_sql} = {literal_sql} OR {right_sql} = {literal_sql})"
    left_suffix = f"RIGHT({left_sql}, {suffix_length}) = {suffix_sql}"
    right_suffix = f"RIGHT({right_sql}, {suffix_length}) = {suffix_sql}"
    return (
        "SELECT COUNT(*), COALESCE(SUM(_exact_seen), 0), "
        "COALESCE(SUM(_left_seen), 0), COALESCE(SUM(_right_seen), 0), "
        "COALESCE(SUM(CASE WHEN _left_seen = 1 AND _right_seen = 1 "
        "THEN 1 ELSE 0 END), 0) FROM (SELECT "
        f"{edge_sql}, MAX(CASE WHEN {exact} THEN 1 ELSE 0 END) AS _exact_seen, "
        f"MAX(CASE WHEN {left_suffix} THEN 1 ELSE 0 END) AS _left_seen, "
        f"MAX(CASE WHEN {right_suffix} THEN 1 ELSE 0 END) AS _right_seen "
        f"FROM {table_sql} WHERE {exact} OR {left_suffix} OR {right_suffix} "
        f"GROUP BY {edge_sql}) "
        "AS _rdst_edge_support"
    )


def _probe_support(
    db_executor: Callable[..., Any],
    target_config: dict[str, Any],
    **query_args: Any,
) -> tuple[bool | None, str, str | None]:
    query = _support_query(**query_args)
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    try:
        result = db_executor(query, target_config)
        rows = result.get("rows") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not result.get("success") or not rows:
            return None, query_hash, None
        groups, exact, left, right, mirrored = (int(value) for value in rows[0])
    except (ArithmeticError, TypeError, ValueError):
        return None, query_hash, None
    except Exception:
        return None, query_hash, None
    supported = (
        groups >= MIN_MIRRORED_GROUPS
        and exact == 0
        and left == groups
        and right == groups
        and mirrored == groups
    )
    return supported, query_hash, None


def encoded_identifier_shape(sql: str, dialect: str) -> tuple[str, ...]:
    """Return mechanical SQL facts without interpreting the user's language."""
    try:
        statements = sqlglot.parse(sql, read=_read_dialect(dialect.casefold()))
    except (sqlglot.errors.SqlglotError, ValueError):
        return ()
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return ()
    tree = statements[0]
    tables = list(tree.find_all(exp.Table))
    counts = list(tree.find_all(exp.Count))
    if (
        len(tables) != 1
        or len(tree.expressions) != 1
        or len(counts) != 1
        or not isinstance(counts[0].this, exp.Star)
        or tree.args.get("group") is not None
        or tree.args.get("having") is not None
        or tree.args.get("joins")
    ):
        return ()
    candidate = _endpoint_predicate(tree)
    if candidate is None or _numeric_suffix(candidate[3]) is None:
        return ()
    return (
        "simple-count-over-one-table",
        "same-literal-compared-to-sibling-endpoint-columns",
        "endpoint-literal-ends-in-a-numeric-identifier",
    )


def normalize_encoded_identifier_storage_sql(
    *,
    sql: str,
    dialect: str,
    schema_info: Any,
    db_executor: Callable[..., Any] | None,
    target_config: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Repair one unsupported encoded endpoint count using mirrored-edge facts."""
    diagnostics: dict[str, Any] = {
        "version": ENCODED_IDENTIFIER_STORAGE_VERSION,
        "status": "unchanged",
        "probe_count": 0,
        "model_calls": 0,
        "execution_feedback": False,
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
        diagnostics["reason"] = "not-one-select"
        return sql, diagnostics
    tree = statements[0]
    tables = list(tree.find_all(exp.Table))
    counts = list(tree.find_all(exp.Count))
    if (
        len(tables) != 1
        or len(tree.expressions) != 1
        or len(counts) != 1
        or not isinstance(counts[0].this, exp.Star)
        or tree.args.get("group") is not None
        or tree.args.get("having") is not None
        or tree.args.get("joins")
    ):
        diagnostics["reason"] = "not-simple-edge-count"
        return sql, diagnostics

    candidate = _endpoint_predicate(tree)
    if candidate is None:
        diagnostics["reason"] = "no-single-sibling-endpoint-predicate"
        return sql, diagnostics
    predicate, left_column, right_column, literal = candidate
    suffix = _numeric_suffix(literal)
    if suffix is None:
        diagnostics["reason"] = "no-literal-derived-numeric-suffix"
        return sql, diagnostics

    table_expression = tables[0]
    if (
        table_expression.args.get("db") is not None
        or table_expression.args.get("catalog") is not None
    ):
        diagnostics["reason"] = "qualified-table-unsupported"
        return sql, diagnostics
    endpoint_qualifier = left_column.table or right_column.table or ""
    if endpoint_qualifier and endpoint_qualifier.casefold() != (
        table_expression.alias_or_name.casefold()
    ):
        diagnostics["reason"] = "endpoint-qualifier-mismatch"
        return sql, diagnostics
    table = _schema_table(schema_info, table_expression.name)
    if table is None:
        diagnostics["reason"] = "table-not-in-schema"
        return sql, diagnostics
    left_schema = _schema_column(table, left_column.name)
    right_schema = _schema_column(table, right_column.name)
    if (
        left_schema is None
        or right_schema is None
        or not _is_text_column(left_schema)
        or not _is_text_column(right_schema)
    ):
        diagnostics["reason"] = "endpoint-columns-not-text"
        return sql, diagnostics
    edge_column = _edge_identifier_column(
        table, {left_column.name.casefold(), right_column.name.casefold()}
    )
    if edge_column is None:
        diagnostics["reason"] = "no-unique-edge-identifier"
        return sql, diagnostics

    supported, probe_hash, unsupported_reason = _probe_support(
        db_executor,
        target_config,
        table=table.name,
        left_column=left_column.name,
        right_column=right_column.name,
        edge_column=edge_column.name,
        literal=literal,
        suffix=suffix,
        dialect=normalized_dialect,
    )
    diagnostics.update({"probe_count": MAX_PROBES, "probe_sha256": [probe_hash]})
    if supported is None:
        diagnostics["reason"] = "database-probe-failed"
        return sql, diagnostics
    if not supported:
        diagnostics["reason"] = (
            unsupported_reason or "mirrored-suffix-not-proven"
        )
        return sql, diagnostics

    qualifier = endpoint_qualifier
    edge_expression = exp.column(edge_column.name, table=qualifier or None)
    counts[0].replace(
        exp.Count(this=exp.Distinct(expressions=[edge_expression]))
    )
    predicate.replace(
        exp.Or(
            this=exp.EQ(
                this=exp.func(
                    "RIGHT", left_column.copy(), exp.Literal.number(len(suffix))
                ),
                expression=exp.Literal.string(suffix),
            ),
            expression=exp.EQ(
                this=exp.func(
                    "RIGHT", right_column.copy(), exp.Literal.number(len(suffix))
                ),
                expression=exp.Literal.string(suffix),
            ),
        )
    )
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "selection_policy": "source-suffix-global-mirrored-edge-v4",
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any, db_executor: Callable[..., Any] | None) -> Any:
    sql = ctx.sql or ""
    normalized, diagnostics = normalize_encoded_identifier_storage_sql(
        sql=sql,
        dialect=ctx.db_type,
        schema_info=ctx.schema_info,
        db_executor=db_executor,
        target_config=ctx.target_config,
    )
    ctx.encoded_identifier_storage = diagnostics
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    return ctx


__all__ = [
    "ENCODED_IDENTIFIER_STORAGE_VERSION",
    "MAX_PROBES",
    "encoded_identifier_shape",
    "normalize_context",
    "normalize_encoded_identifier_storage_sql",
]
