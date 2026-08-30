"""Deterministic completion of unbounded entity superlative queries."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

EXTREMUM_ENTITY_NORMALIZER_VERSION = "unbounded-extremum-entity-v5"

_ENTITY_QUESTION = re.compile(r"^\s*(?:which|who)\b", re.IGNORECASE)
_SUPERLATIVE = re.compile(
    r"\b(?:highest|lowest|most|least|heaviest|lightest|fastest|slowest|latest|earliest)\b",
    re.IGNORECASE,
)
_MAXIMUM_SUPERLATIVE = re.compile(
    r"\b(?:highest|most|heaviest|fastest|latest)\b",
    re.IGNORECASE,
)
_MINIMUM_SUPERLATIVE = re.compile(
    r"\b(?:lowest|least|lightest|slowest|earliest)\b",
    re.IGNORECASE,
)
_EXPLICIT_EXTREMUM_VALUE_REQUEST = re.compile(
    r"\band\s+(?:what|how\s+(?:many|much)|show|include|return|give)\b",
    re.IGNORECASE,
)
_PLURAL_WHICH_ENTITY = re.compile(
    r"^\s*which\s+(?P<entity>.+?)\s+(?:has|have|had|is|are|was|were)\s+"
    r"(?:the\s+)?(?:highest|lowest|most|least|heaviest|lightest|fastest|"
    r"slowest|latest|earliest)\b",
    re.IGNORECASE,
)
_PLURAL_WHO_ENTITY = re.compile(
    r"^\s*who\s+(?:are|were)\s+(?:the\s+)?(?P<entity>.+?)\s+(?:with|having)\s+"
    r"(?:the\s+)?(?:highest|lowest|most|least|heaviest|lightest|fastest|"
    r"slowest|latest|earliest)\b",
    re.IGNORECASE,
)
_IDENTIFIER_WORD = re.compile(r"[A-Za-z]+")
_SINGULAR_EXCEPTIONS = frozenset(
    {"analysis", "business", "class", "news", "series", "species"}
)


def _same_column(left: exp.Expression, right: exp.Expression) -> bool:
    if isinstance(left, exp.Alias):
        left = left.this
    return (
        isinstance(left, exp.Column)
        and isinstance(right, exp.Column)
        and left.name.casefold() == right.name.casefold()
        and (left.table or "").casefold() == (right.table or "").casefold()
    )


def _explicit_plural_entity_intent(question: str) -> bool:
    # Bare "Who?", "Who is ...?", and "Who has ...?" have singular/cardinality-
    # neutral grammar. They do not ask us to widen a correct LIMIT 1.
    match = _PLURAL_WHICH_ENTITY.search(question) or _PLURAL_WHO_ENTITY.search(question)
    if match is None:
        return False
    tokens = _IDENTIFIER_WORD.findall(match.group("entity").casefold())
    if not tokens:
        return False
    noun = tokens[-1]
    return noun.endswith("s") and noun not in _SINGULAR_EXCEPTIONS


def _is_singleton_limit(limit: exp.Expression | None) -> bool:
    if limit is None:
        return False
    expression = limit.args.get("expression")
    return (
        isinstance(expression, exp.Literal)
        and not expression.is_string
        and str(expression.this) == "1"
    )


def _safe_outer_where_equality(
    equality: exp.EQ,
    *,
    where: exp.Where,
) -> bool:
    current = equality.parent
    while current is not None:
        if isinstance(current, (exp.Or, exp.Not)):
            return False
        if current is where:
            return True
        if isinstance(current, (exp.Select, exp.Subquery)):
            return False
        current = current.parent
    return False


def _question_extremum_kind(question: str) -> str | None:
    maximum = bool(_MAXIMUM_SUPERLATIVE.search(question))
    minimum = bool(_MINIMUM_SUPERLATIVE.search(question))
    if maximum == minimum:
        return None
    return "maximum" if maximum else "minimum"


def _prune_existing_extremum_metric_projection(
    *,
    tree: exp.Select,
    question: str,
    routed_intent: bool = False,
) -> tuple[exp.Select, int, str] | None:
    """Remove a ranking metric already constrained by one scalar MAX or MIN."""
    if (not routed_intent and _EXPLICIT_EXTREMUM_VALUE_REQUEST.search(question)) or any(
        tree.args.get(key) is not None
        for key in (
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "distinct",
            "with_",
            "qualify",
        )
    ):
        return None
    where = tree.args.get("where")
    expected_kind = _question_extremum_kind(question)
    if where is None or (expected_kind is None and not routed_intent):
        return None

    candidates: list[tuple[exp.Column, str]] = []
    for equality in where.this.find_all(exp.EQ):
        if not _safe_outer_where_equality(equality, where=where):
            continue
        column: exp.Column
        scalar: exp.Subquery
        if isinstance(equality.this, exp.Column) and isinstance(
            equality.expression, exp.Subquery
        ):
            column, scalar = equality.this, equality.expression
        elif isinstance(equality.expression, exp.Column) and isinstance(
            equality.this, exp.Subquery
        ):
            column, scalar = equality.expression, equality.this
        else:
            continue
        select = scalar.this
        if (
            not isinstance(select, exp.Select)
            or len(select.expressions) != 1
            or sum(1 for _ in select.find_all(exp.Select)) != 1
            or any(
                select.args.get(key) is not None
                for key in (
                    "group",
                    "having",
                    "order",
                    "limit",
                    "offset",
                    "distinct",
                    "with_",
                    "qualify",
                )
            )
        ):
            continue
        aggregate = select.expressions[0]
        if isinstance(aggregate, exp.Alias):
            aggregate = aggregate.this
        if isinstance(aggregate, exp.Max):
            kind = "maximum"
        elif isinstance(aggregate, exp.Min):
            kind = "minimum"
        else:
            continue
        aggregate_column = aggregate.this
        if (
            (expected_kind is not None and kind != expected_kind)
            or not isinstance(aggregate_column, exp.Column)
            or aggregate_column.name.casefold() != column.name.casefold()
        ):
            continue
        candidates.append((column, kind))
    unique_candidates = {
        ((column.table or "").casefold(), column.name.casefold(), kind): (
            column,
            kind,
        )
        for column, kind in candidates
    }
    if len(unique_candidates) != 1:
        return None
    metric, kind = next(iter(unique_candidates.values()))
    projections = list(tree.expressions)
    remaining = [
        expression for expression in projections if not _same_column(expression, metric)
    ]
    removed = len(projections) - len(remaining)
    if removed != 1 or not remaining:
        return None
    normalized = tree.copy()
    normalized.set("expressions", [expression.copy() for expression in remaining])
    return normalized, removed, kind


def normalize_extremum_entity_sql(
    *,
    question: str,
    sql: str,
    dialect: str,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Replace an unbounded extremum sort with a tie-preserving max/min filter."""
    diagnostics: dict[str, Any] = {
        "version": EXTREMUM_ENTITY_NORMALIZER_VERSION,
        "status": "unchanged",
    }
    lexical_intent = bool(
        _ENTITY_QUESTION.search(question) and _SUPERLATIVE.search(question)
    )
    routed_intent = "entity_at_extremum" in intent_hints
    if not lexical_intent and not routed_intent:
        diagnostics["reason"] = "no-entity-superlative-intent"
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
    order = tree.args.get("order")
    if order is None:
        pruned = _prune_existing_extremum_metric_projection(
            tree=tree,
            question=question,
            routed_intent=routed_intent,
        )
        if pruned is None:
            diagnostics["reason"] = "not-one-order-expression"
            return sql, diagnostics
        tree, removed_projections, extremum_kind = pruned
        normalized = tree.sql(dialect=read_dialect)
        diagnostics.update(
            {
                "status": "normalized",
                "extremum": extremum_kind,
                "removed_order_metric_projections": removed_projections,
                "removed_unrequested_singleton_limit": False,
                "existing_extremum_filter_preserved": True,
                "tie_policy": "preserve-existing-extremum-filter-v1",
                "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
            }
        )
        return normalized, diagnostics
    if len(order.expressions) != 1:
        diagnostics["reason"] = "not-one-order-expression"
        return sql, diagnostics
    limit = tree.args.get("limit")
    removable_singleton = _is_singleton_limit(limit) and (
        routed_intent or _explicit_plural_entity_intent(question)
    )
    if limit is not None and not removable_singleton:
        diagnostics["reason"] = "bounded-or-complex-query"
        return sql, diagnostics
    if any(
        tree.args.get(key) is not None
        for key in ("group", "having", "with_", "distinct", "qualify", "offset")
    ):
        diagnostics["reason"] = "bounded-or-complex-query"
        return sql, diagnostics
    ordered = order.expressions[0]
    order_expression = ordered.this
    if not isinstance(order_expression, exp.Column):
        diagnostics["reason"] = "non-column-order-expression"
        return sql, diagnostics

    projections = list(tree.expressions)
    remaining = [
        expression
        for expression in projections
        if not _same_column(expression, order_expression)
    ]
    removed_projections = len(projections) - len(remaining)
    if removed_projections and not remaining:
        diagnostics["reason"] = "no-entity-projection-remains"
        return sql, diagnostics

    extremum = tree.copy()
    aggregate = exp.Max if ordered.args.get("desc") else exp.Min
    extremum.set("expressions", [aggregate(this=order_expression.copy())])
    extremum.set("order", None)
    extremum.set("limit", None)
    extremum.set("offset", None)

    predicate = exp.EQ(
        this=order_expression.copy(),
        expression=exp.Subquery(this=extremum),
    )
    where = tree.args.get("where")
    tree.set(
        "where",
        exp.Where(this=exp.and_(where.this.copy(), predicate) if where else predicate),
    )
    if removed_projections:
        tree.set("expressions", remaining)
    tree.set("order", None)
    tree.set("limit", None)
    tree.set("offset", None)
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "extremum": "maximum" if ordered.args.get("desc") else "minimum",
            "removed_order_metric_projections": removed_projections,
            "removed_unrequested_singleton_limit": removable_singleton,
            "tie_policy": "return-all-extremum-ties-v1",
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
    normalized, diagnostics = normalize_extremum_entity_sql(
        question=ctx.refined_question or ctx.question,
        sql=sql,
        dialect=ctx.db_type,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.extremum_entity_normalization = diagnostics
    return ctx


__all__ = [
    "EXTREMUM_ENTITY_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_extremum_entity_sql",
]
