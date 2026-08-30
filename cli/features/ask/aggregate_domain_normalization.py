"""Remove unsupported row-domain exclusions from explicit all-row aggregates."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

ALL_ROWS_AGGREGATE_NORMALIZER_VERSION = "all-rows-aggregate-domain-v1"

_ALL_ROWS = re.compile(r"\ball\b", re.IGNORECASE)
_DOMAIN_EXCLUSION_INTENT = re.compile(
    r"\b(?:positive|non[-\s]?zero|greater\s+than\s+zero|above\s+zero|"
    r"valid|known|recorded|exclude|excluding|ignore|ignoring)\b",
    re.IGNORECASE,
)
_EVIDENCE_AVG = re.compile(
    r"\bAVG\s*\(\s*(?:`(?P<backtick>[^`]+)`|\"(?P<double>[^\"]+)\"|"
    r"(?P<bare>[A-Za-z_][A-Za-z0-9_]*))\s*\)",
    re.IGNORECASE,
)


def _value(expression: exp.Expression) -> exp.Expression:
    return expression.this if isinstance(expression, exp.Alias) else expression


def _evidence_average_column(provided_context: str) -> str | None:
    columns = {
        next(value for value in match.groupdict().values() if value is not None)
        for match in _EVIDENCE_AVG.finditer(provided_context)
    }
    return next(iter(columns)) if len(columns) == 1 else None


def _flatten_and(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return [*_flatten_and(expression.this), *_flatten_and(expression.expression)]
    return [expression]


def _is_strict_positive_filter(
    expression: exp.Expression,
    column_name: str,
) -> bool:
    if not isinstance(expression, exp.GT):
        return False
    left, right = expression.this, expression.expression
    if (
        not isinstance(left, exp.Column)
        or left.name.casefold() != column_name.casefold()
    ):
        return False
    return bool(
        isinstance(right, exp.Literal)
        and not right.is_string
        and str(right.this).strip() in {"0", "0.0"}
    )


def normalize_all_rows_aggregate_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Drop one unrequested ``aggregate_column > 0`` conjunct."""
    diagnostics: dict[str, Any] = {
        "version": ALL_ROWS_AGGREGATE_NORMALIZER_VERSION,
        "status": "unchanged",
        "removed_domain_filters": 0,
        "model_calls": 0,
        "execution_feedback": False,
    }
    routed_intent = "all_rows_population" in intent_hints
    all_rows_intent = _ALL_ROWS.search(question) is not None or routed_intent
    if not all_rows_intent or (
        not routed_intent and _DOMAIN_EXCLUSION_INTENT.search(question)
    ):
        diagnostics["reason"] = "no-unqualified-all-rows-intent"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics
    if (
        not isinstance(tree, exp.Select)
        or len(tree.expressions) != 1
        or tree.args.get("group") is not None
        or tree.args.get("having") is not None
    ):
        diagnostics["reason"] = "not-one-scalar-average"
        return sql, diagnostics
    projection = _value(tree.expressions[0])
    hinted_population = routed_intent
    column_name = _evidence_average_column(provided_context)
    if (
        column_name is None
        and hinted_population
        and isinstance(projection, exp.Avg)
        and isinstance(projection.this, exp.Column)
    ):
        column_name = projection.this.name
    if column_name is None:
        diagnostics["reason"] = "no-unique-evidence-average-column"
        return sql, diagnostics
    if (
        not isinstance(projection, exp.Avg)
        or not isinstance(projection.this, exp.Column)
        or projection.this.name.casefold() != column_name.casefold()
    ):
        diagnostics["reason"] = "projection-not-evidence-average"
        return sql, diagnostics
    where = tree.args.get("where")
    if where is None:
        diagnostics["reason"] = "no-row-domain-filter"
        return sql, diagnostics
    conjuncts = _flatten_and(where.this)
    removable = [
        expression
        for expression in conjuncts
        if _is_strict_positive_filter(expression, column_name)
    ]
    if len(removable) != 1:
        diagnostics["reason"] = (
            "no-unsupported-positive-domain-filter"
            if not removable
            else "multiple-unsupported-positive-domain-filters"
        )
        return sql, diagnostics
    remaining = [
        expression.copy() for expression in conjuncts if expression is not removable[0]
    ]
    tree.set(
        "where",
        exp.Where(this=exp.and_(*remaining)) if remaining else None,
    )
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "removed_domain_filters": 1,
            "aggregate_column": column_name,
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any) -> Any:
    sql = ctx.sql or ""
    selected_intents = selected_correction_intents(
        getattr(ctx, "correction_intent_routing", None)
    )
    normalized, diagnostics = normalize_all_rows_aggregate_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.all_rows_aggregate_normalization = diagnostics
    return ctx


__all__ = [
    "ALL_ROWS_AGGREGATE_NORMALIZER_VERSION",
    "normalize_all_rows_aggregate_sql",
    "normalize_context",
]
