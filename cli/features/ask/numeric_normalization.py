"""Deterministic SQL normalization for explicitly requested ratios."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

EXPLICIT_RATIO_NORMALIZER_VERSION = "explicit-floating-ratio-v6"

_RATIO_INTENT = re.compile(
    r"\b(?:percentage|percent|proportion|ratio)\b",
    re.IGNORECASE,
)
_PERCENT_INTENT = re.compile(r"\b(?:percentage|percent)\b|%", re.IGNORECASE)
_FLOAT_TYPES = {
    exp.DataType.Type.DOUBLE,
    exp.DataType.Type.FLOAT,
}


def _contains_float_cast(expression: exp.Expression) -> bool:
    return any(
        isinstance(node, (exp.Cast, exp.TryCast))
        and isinstance(node.args.get("to"), exp.DataType)
        and node.args["to"].this in _FLOAT_TYPES
        for node in expression.walk()
    )


def _nearest_select(expression: exp.Expression) -> exp.Select | None:
    current = expression.parent
    while current is not None and not isinstance(current, exp.Select):
        current = current.parent
    return current if isinstance(current, exp.Select) else None


def _expression_contributes_to_projected_value(
    expression: exp.Expression,
) -> bool:
    current = expression
    while current.parent is not None and not isinstance(current.parent, exp.Select):
        parent = current.parent
        if isinstance(parent, (exp.If, exp.Case)) and current is parent.this:
            return False
        if isinstance(parent, exp.Filter) and current is parent.expression:
            return False
        if isinstance(parent, (exp.Order, exp.WindowSpec)):
            return False
        if isinstance(parent, exp.Window) and current in tuple(
            parent.args.get("partition_by") or ()
        ):
            return False
        current = parent
    return True


def _root_projection_divisions(tree: exp.Select) -> tuple[exp.Div, ...]:
    divisions: list[exp.Div] = []
    for projection in tree.expressions:
        divisions.extend(
            node
            for node in projection.walk()
            if isinstance(node, exp.Div)
            and _nearest_select(node) is tree
            and _expression_contributes_to_projected_value(node)
        )
    return tuple(divisions)


def _root_projection_divide_functions(
    tree: exp.Select,
) -> tuple[exp.Anonymous, ...]:
    return tuple(
        node
        for projection in tree.expressions
        for node in projection.walk()
        if isinstance(node, exp.Anonymous)
        and node.name.casefold() == "divide"
        and len(node.expressions) == 2
        and _nearest_select(node) is tree
        and _expression_contributes_to_projected_value(node)
    )


def _numeric_value(expression: exp.Expression) -> str | None:
    while isinstance(expression, (exp.Cast, exp.TryCast, exp.Paren)):
        expression = expression.this
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        return str(float(str(expression.this)))
    except ValueError:
        return None


def _is_hundred(expression: exp.Expression) -> bool:
    return _numeric_value(expression) == "100.0"


def _multiplication_scales_by_hundred(expression: exp.Expression) -> bool:
    return any(
        isinstance(node, exp.Mul)
        and (_is_hundred(node.this) or _is_hundred(node.expression))
        for node in expression.walk()
    )


def _already_percent_scaled(division: exp.Div) -> bool:
    if _multiplication_scales_by_hundred(division.this):
        return True
    current = division.parent
    while current is not None and not isinstance(current, exp.Select):
        if isinstance(current, exp.Mul) and (
            _is_hundred(current.this) or _is_hundred(current.expression)
        ):
            return True
        current = current.parent
    return False


def normalize_explicit_ratio_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Use floating division when the request explicitly asks for a ratio."""
    diagnostics: dict[str, Any] = {
        "version": EXPLICIT_RATIO_NORMALIZER_VERSION,
        "status": "unchanged",
        "changed_divisions": 0,
        "converted_divide_functions": 0,
        "scaled_percentages": 0,
    }
    hinted_ratio = bool(
        {"percentage_output", "ratio_output"}.intersection(intent_hints)
    )
    model_ratio_hint = "ratio_output" in intent_hints
    model_percentage_hint = "percentage_output" in intent_hints
    lexical_ratio_intent = bool(_RATIO_INTENT.search(f"{question}\n{provided_context}"))
    if not hinted_ratio and not lexical_ratio_intent:
        diagnostics["reason"] = "no-explicit-ratio-intent"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics

    converted = 0
    divide_functions: tuple[exp.Anonymous, ...] = ()
    if model_ratio_hint:
        divide_functions = _root_projection_divide_functions(tree)
    elif lexical_ratio_intent and re.search(
        r"\bDIVIDE\s*\(", provided_context, re.IGNORECASE
    ):
        divide_functions = tuple(tree.find_all(exp.Anonymous))
    if divide_functions:
        for function in divide_functions:
            if function.name.casefold() != "divide" or len(function.expressions) != 2:
                continue
            numerator, denominator = function.expressions
            function.replace(
                exp.Div(
                    this=numerator.copy(),
                    expression=denominator.copy(),
                )
            )
            converted += 1

    scaled = 0
    percentage_divisions = _root_projection_divisions(tree)
    if (
        (_PERCENT_INTENT.search(question) or "percentage_output" in intent_hints)
        and len(percentage_divisions) == 1
        and not _already_percent_scaled(percentage_divisions[0])
    ):
        division = percentage_divisions[0]
        division.set(
            "this",
            exp.Mul(
                this=division.this.copy(),
                expression=exp.Literal.number(100),
            ),
        )
        scaled = 1

    changed = 0
    divisions = (
        _root_projection_divisions(tree)
        if model_ratio_hint or model_percentage_hint
        else tuple(tree.find_all(exp.Div))
    )
    for division in divisions:
        if _contains_float_cast(division):
            continue
        division.set(
            "this",
            exp.Cast(
                this=division.this.copy(),
                to=exp.DataType.build(exp.DataType.Type.DOUBLE),
            ),
        )
        changed += 1
    if not changed and not converted and not scaled:
        diagnostics["reason"] = "no-eligible-division"
        return sql, diagnostics

    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "changed_divisions": changed,
            "converted_divide_functions": converted,
            "scaled_percentages": scaled,
            "division_scope": (
                "root-projection"
                if model_ratio_hint or model_percentage_hint
                else "statement"
            ),
            "routed_effect": ("floating-cast-only" if model_ratio_hint else "none"),
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
    normalized, diagnostics = normalize_explicit_ratio_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.explicit_ratio_normalization = diagnostics
    return ctx


__all__ = [
    "EXPLICIT_RATIO_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_explicit_ratio_sql",
]
