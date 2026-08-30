"""Deterministic cleanup of scalar derived-metric projections."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

SCALAR_DERIVED_METRIC_NORMALIZER_VERSION = "scalar-derived-metric-v6"

_DERIVED_METRIC_INTENT = re.compile(
    r"\b(?:percentage|percent|proportion|ratio)\b",
    re.IGNORECASE,
)
_SCALAR_DIFFERENCE_INTENT = re.compile(
    r"\b(?:how\s+(?:many|much)\s+(?:more|fewer|less)|"
    r"by\s+how\s+(?:many|much)|(?:absolute\s+)?difference)\b",
    re.IGNORECASE,
)
_RATE_INTENT = re.compile(r"\brate\b", re.IGNORECASE)
_EXPLICIT_COMPONENT_OUTPUT_INTENT = re.compile(
    r"\b(?:both|each|individual|respective)\b[^?.]{0,80}"
    r"\b(?:components?|counts?|figures?|numbers?|totals?|amounts?|values?)\b|"
    r"\b(?:all|both|each|individual|respective|two|three)\b[^?.]{0,40}"
    r"\b(?:components?|counts?|figures?|numbers?|totals?|amounts?|values?)\b|"
    r"\b(?:components?|counts?|figures?|numbers?|totals?|amounts?|values?)\b"
    r"[^?.]{0,80}"
    r"\b(?:and|along\s+with|plus)\b[^?.]{0,40}"
    r"\b(?:the\s+)?difference\b",
    re.IGNORECASE,
)
_EXPLICIT_RATIO_COMPONENT_OUTPUT_INTENT = re.compile(
    r"\b(?:return|show|give|provide|list|report|include|output|what\s+are)\b"
    r"[^?.\n]{0,180}"
    r"\b(?:numerator|denominator|components?|counts?|figures?|numbers?|totals?|"
    r"amounts?|values?)\b"
    r"[^?.\n]{0,120}(?:,|;|\band\b|\bwith\b|\balong\s+with\b|\bplus\b)"
    r"[^?.\n]{0,120}"
    r"\b(?:percentage|percent|proportion|ratio|rate|share|average|mean)\b|"
    r"\b(?:return|show|give|provide|list|report|include|output|what\s+are)\b"
    r"[^?.\n]{0,180}"
    r"\b(?:percentage|percent|proportion|ratio|rate|share|average|mean)\b"
    r"[^?.\n]{0,80}(?:,|;|\balong\s+with\b|\bplus\b|\bas\s+well\s+as\b)"
    r"[^?.\n]{0,120}"
    r"\b(?:numerator|denominator|components?|counts?|figures?|numbers?|totals?|"
    r"amounts?|values?)\b|"
    r"\bhow\s+(?:many|much)\b(?!\s+times\b)"
    r"[^?.\n]{0,120}\b(?:and|plus|along\s+with)\b"
    r"[^?.\n]{0,120}"
    r"\b(?:percentage|percent|proportion|ratio|rate|share|average|mean)\b",
    re.IGNORECASE,
)
_HOW_MANY_OR_MUCH = re.compile(r"\bhow\s+(?:many|much)\b", re.IGNORECASE)
_ROUNDING_INTENT = re.compile(
    r"\b(?:round(?:ed|ing)?|precision|decimal\s+places?|nearest|significant\s+digits?)\b",
    re.IGNORECASE,
)
_COMPARISON_INTENT = re.compile(
    r"\b(?:more|fewer|greater|less|higher|lower|larger|smaller)\b",
    re.IGNORECASE,
)
_DEFINED_METRIC = re.compile(
    r"\b(?:percentage|percent|proportion|ratio)\b\s*=",
    re.IGNORECASE,
)
_COMPARISONS = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)


def _value(expression: exp.Expression) -> exp.Expression:
    return expression.this if isinstance(expression, exp.Alias) else expression


def _signature(expression: exp.Expression, dialect: str) -> str:
    return expression.sql(dialect=dialect, normalize=True, comments=False)


def _is_descendant(
    needle: exp.Expression,
    haystack: exp.Expression,
    dialect: str,
) -> bool:
    signature = _signature(needle, dialect)
    return any(
        node is not haystack and _signature(node, dialect) == signature
        for node in haystack.walk()
    )


def _unwrap_unrequested_ratio_rounds(
    expression: exp.Expression,
) -> tuple[exp.Expression, int]:
    """Remove only ROUND nodes that format a completed division result.

    A safety guard can place the ratio under a CASE expression, so checking only
    the projection root misses otherwise identical output formatting.  Rounding
    inside a numerator or denominator is retained because it can be part of the
    requested metric rather than presentation formatting.
    """
    removed = 0

    def unwrap(node: exp.Expression) -> exp.Expression:
        nonlocal removed
        if isinstance(node, exp.Round) and node.find(exp.Div) is not None:
            removed += 1
            return node.this.copy()
        return node

    return expression.transform(unwrap), removed


def _aggregate_signatures(
    expression: exp.Expression,
    dialect: str,
) -> set[str]:
    return {_signature(node, dialect) for node in expression.find_all(exp.AggFunc)}


def _direct_aggregate_difference(
    projection: exp.Expression,
    dialect: str,
) -> tuple[str, str] | None:
    def unwrap(value: exp.Expression) -> exp.Expression:
        while isinstance(value, (exp.Cast, exp.TryCast, exp.Paren)):
            value = value.this
        return value

    def standalone_aggregate(value: exp.Expression) -> bool:
        value = unwrap(value)
        return isinstance(value, exp.AggFunc) or (
            isinstance(value, exp.Filter) and isinstance(value.this, exp.AggFunc)
        )

    value = _value(projection)
    value = unwrap(value)
    if not isinstance(value, exp.Sub):
        return None
    left = unwrap(value.this)
    right = unwrap(value.expression)
    if not standalone_aggregate(left) or not standalone_aggregate(right):
        return None
    signatures = (_signature(left, dialect), _signature(right, dialect))
    return signatures if signatures[0] != signatures[1] else None


def _canonical_difference_operand(
    value: exp.Expression,
    dialect: str,
) -> str:
    while isinstance(value, (exp.Cast, exp.TryCast, exp.Paren)):
        value = value.this
    return _signature(value, dialect)


def _is_star_projection(projection: exp.Expression) -> bool:
    value = _value(projection)
    return isinstance(value, exp.Star) or (
        isinstance(value, exp.Column) and isinstance(value.this, exp.Star)
    )


def _is_redundant_metric_comparison(
    projection: exp.Expression,
    *,
    derived_value: exp.Expression,
    question: str,
    provided_context: str,
    dialect: str,
) -> bool:
    """Recognize a literal label comparing the ratio's exact two aggregates."""
    if not _COMPARISON_INTENT.search(question) or not _DEFINED_METRIC.search(
        provided_context
    ):
        return False
    value = _value(projection)
    if not isinstance(value, exp.Case) or value.args.get("this") is not None:
        return False

    derived_aggregates = _aggregate_signatures(derived_value, dialect)
    case_aggregates = _aggregate_signatures(value, dialect)
    if len(derived_aggregates) != 2 or case_aggregates != derived_aggregates:
        return False

    branches = list(value.args.get("ifs") or ())
    if not branches or not isinstance(value.args.get("default"), exp.Literal):
        return False
    for branch in branches:
        condition = branch.this
        if not isinstance(condition, _COMPARISONS):
            return False
        if not isinstance(branch.args.get("true"), exp.Literal):
            return False
        left = condition.this
        right = condition.expression
        if {
            _signature(left, dialect),
            _signature(right, dialect),
        } != derived_aggregates:
            return False
    return True


def normalize_scalar_derived_metric_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Keep one scalar ratio instead of returning its intermediate aggregates."""
    diagnostics: dict[str, Any] = {
        "version": SCALAR_DERIVED_METRIC_NORMALIZER_VERSION,
        "status": "unchanged",
        "removed_intermediate_projections": 0,
        "removed_redundant_comparison_projections": 0,
        "removed_unrequested_rounds": 0,
    }
    intent = f"{question}\n{provided_context}"
    # Routed scalar-difference intent owns this invocation. Lexical ratio handling
    # remains available only for the legacy non-routed path.
    routed_difference_intent = "scalar_difference_output" in intent_hints
    ratio_intent = (
        not routed_difference_intent
        and _DERIVED_METRIC_INTENT.search(intent) is not None
    )
    diagnostics["ratio_router_hint_authorized_cleanup"] = False
    difference_intent = not ratio_intent and (
        routed_difference_intent
        or (
            _SCALAR_DIFFERENCE_INTENT.search(question) is not None
            and _RATE_INTENT.search(question) is None
            and _EXPLICIT_COMPONENT_OUTPUT_INTENT.search(intent) is None
            and len(_HOW_MANY_OR_MUCH.findall(question)) <= 1
        )
    )
    if not ratio_intent and not difference_intent:
        diagnostics["reason"] = "no-explicit-derived-metric-intent"
        return sql, diagnostics
    if ratio_intent and (
        _EXPLICIT_COMPONENT_OUTPUT_INTENT.search(intent)
        or _EXPLICIT_RATIO_COMPONENT_OUTPUT_INTENT.search(intent)
        or len(_HOW_MANY_OR_MUCH.findall(question)) >= 2
    ):
        diagnostics["reason"] = "explicit-component-output-request"
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
    if tree.args.get("group") is not None:
        diagnostics["reason"] = "grouped-result"
        return sql, diagnostics
    if difference_intent and (
        len(list(tree.find_all(exp.Select))) != 1
        or tree.args.get("having") is not None
        or tree.args.get("qualify") is not None
        or tree.args.get("order") is not None
        or tree.args.get("distinct") is not None
        or tree.find(exp.Window) is not None
        or any(_is_star_projection(projection) for projection in tree.expressions)
        or any(
            isinstance(node, (exp.Union, exp.Intersect, exp.Except))
            for node in tree.walk()
        )
    ):
        diagnostics["reason"] = "unsupported-difference-result-shape"
        return sql, diagnostics

    projections = list(tree.expressions)
    if len(projections) < 2:
        diagnostics["reason"] = "no-intermediate-projections"
        return sql, diagnostics
    if ratio_intent:
        derived = [
            projection
            for projection in projections
            if _value(projection).find(exp.Div) is not None
            and _value(projection).find(exp.AggFunc) is not None
        ]
    else:
        derived = [
            projection
            for projection in projections
            if _direct_aggregate_difference(projection, read_dialect) is not None
        ]
    if len(derived) != 1:
        diagnostics["reason"] = (
            "not-one-derived-aggregate-ratio"
            if ratio_intent
            else "not-one-derived-aggregate-difference"
        )
        return sql, diagnostics
    derived_projection = derived[0]
    derived_value = _value(derived_projection)
    difference_operands = (
        _direct_aggregate_difference(derived_projection, read_dialect)
        if difference_intent
        else None
    )
    if difference_intent and len(projections) != 3:
        diagnostics["reason"] = "not-exact-difference-projection-triple"
        return sql, diagnostics
    intermediates = []
    redundant_comparisons = []
    for projection in projections:
        if projection is derived_projection:
            continue
        value = _value(projection)
        if ratio_intent:
            if isinstance(value, exp.AggFunc) and _is_descendant(
                value, derived_value, read_dialect
            ):
                intermediates.append(projection)
                continue
        elif (
            difference_operands is not None
            and _canonical_difference_operand(value, read_dialect)
            in difference_operands
        ):
            intermediates.append(projection)
            continue
        if ratio_intent and _is_redundant_metric_comparison(
            projection,
            derived_value=derived_value,
            question=question,
            provided_context=provided_context,
            dialect=read_dialect,
        ):
            redundant_comparisons.append(projection)
            continue
        diagnostics["reason"] = "non-intermediate-output-present"
        return sql, diagnostics
    if not intermediates:
        diagnostics["reason"] = "no-intermediate-projections"
        return sql, diagnostics
    if difference_intent and (
        len(intermediates) != 2
        or {
            _canonical_difference_operand(_value(item), read_dialect)
            for item in intermediates
        }
        != set(difference_operands or ())
    ):
        diagnostics["reason"] = "difference-operands-not-exactly-projected"
        return sql, diagnostics

    output = derived_projection.copy()
    removed_rounds = 0
    if ratio_intent and not _ROUNDING_INTENT.search(intent):
        output, removed_rounds = _unwrap_unrequested_ratio_rounds(output)
    tree.set("expressions", [output])
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "removed_intermediate_projections": len(intermediates),
            "removed_redundant_comparison_projections": len(redundant_comparisons),
            "removed_projection_names": [
                projection.alias_or_name
                for projection in intermediates + redundant_comparisons
            ],
            "removed_unrequested_rounds": removed_rounds,
            "metric_kind": "ratio" if ratio_intent else "difference",
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
    normalized, diagnostics = normalize_scalar_derived_metric_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.scalar_derived_metric_normalization = diagnostics
    return ctx


__all__ = [
    "SCALAR_DERIVED_METRIC_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_scalar_derived_metric_sql",
]
