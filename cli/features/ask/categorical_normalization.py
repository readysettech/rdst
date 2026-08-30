"""Deterministic completion of unbounded categorical-alternative queries."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

UNBOUNDED_CATEGORICAL_NORMALIZER_VERSION = "unbounded-categorical-alternatives-v2"

_ALTERNATIVES = re.compile(r"\bor\b", re.IGNORECASE)
_EXPLICIT_SINGLETON = re.compile(
    r"\b(?:first|single|one\s+(?:example|result|row)|any\s+one|an\s+example)\b",
    re.IGNORECASE,
)


def _projected_column(expression: exp.Expression) -> exp.Column | None:
    value = expression.this if isinstance(expression, exp.Alias) else expression
    return value if isinstance(value, exp.Column) else None


def _identifier_is_evidence_mapped(identifier: str, evidence: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
            evidence,
            re.IGNORECASE,
        )
    )


def normalize_unbounded_categorical_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Replace an arbitrary categorical singleton with all distinct values."""
    diagnostics: dict[str, Any] = {
        "version": UNBOUNDED_CATEGORICAL_NORMALIZER_VERSION,
        "status": "unchanged",
    }
    alternatives_intent = (
        _ALTERNATIVES.search(question) is not None
        or "all_matching_categories" in intent_hints
    )
    routed_intent = "all_matching_categories" in intent_hints
    if not alternatives_intent or (
        not routed_intent and _EXPLICIT_SINGLETON.search(question)
    ):
        diagnostics["reason"] = "no-unbounded-alternatives-intent"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics
    if not isinstance(tree, exp.Select):
        diagnostics["reason"] = "non-select-root"
        return sql, diagnostics
    if len(tree.expressions) != 1:
        diagnostics["reason"] = "not-one-categorical-projection"
        return sql, diagnostics
    column = _projected_column(tree.expressions[0])
    if column is None or (
        not routed_intent
        and not _identifier_is_evidence_mapped(column.name, provided_context)
    ):
        diagnostics["reason"] = "projection-not-evidence-mapped"
        return sql, diagnostics
    limit = tree.args.get("limit")
    limit_value = limit.expression if limit is not None else None
    if not isinstance(limit_value, exp.Literal) or str(limit_value.this) != "1":
        diagnostics["reason"] = "not-singleton-limit"
        return sql, diagnostics
    if (
        any(
            tree.args.get(key) is not None
            for key in ("order", "group", "having", "distinct", "qualify", "offset")
        )
        or tree.find(exp.AggFunc) is not None
    ):
        diagnostics["reason"] = "ordered-or-complex-query"
        return sql, diagnostics

    tree.set("limit", None)
    tree.set("distinct", exp.Distinct())
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "projected_category": column.name,
            "removed_limit": 1,
            "deduplication": "distinct-v1",
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
    normalized, diagnostics = normalize_unbounded_categorical_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.unbounded_categorical_normalization = diagnostics
    return ctx


__all__ = [
    "UNBOUNDED_CATEGORICAL_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_unbounded_categorical_sql",
]
