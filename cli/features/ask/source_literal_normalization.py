"""Deterministic correction of explicit year literals and date-part syntax."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import sqlglot
from sqlglot import exp

EXPLICIT_YEAR_SPAN_NORMALIZER_VERSION = "explicit-source-year-v2"

_SHORT_YEAR_SPAN = re.compile(r"^(?P<start>\d{4})(?P<sep>[-/])(?P<end>\d{2})$")
_FULL_YEAR_SPAN = re.compile(
    r"(?<!\d)(?P<start>\d{4})(?P<sep>[-/])(?P<end>\d{4})(?!\d)"
)
_STRFTIME_CALL = re.compile(r"\bstrftime\s*\(", re.IGNORECASE)


def _in_filter(expression: exp.Expression) -> bool:
    parent = expression.parent
    while parent is not None:
        if isinstance(parent, (exp.Where, exp.Having)):
            return True
        parent = parent.parent
    return False


def _full_span_for(short_span: str, source_text: str) -> str | None:
    short = _SHORT_YEAR_SPAN.fullmatch(short_span)
    if short is None:
        return None
    matches = {
        match.group(0)
        for match in _FULL_YEAR_SPAN.finditer(source_text)
        if match.group("start") == short.group("start")
        and match.group("end")[-2:] == short.group("end")
    }
    return next(iter(matches)) if len(matches) == 1 else None


def normalize_explicit_year_span_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
) -> tuple[str, dict[str, Any]]:
    """Normalize an explicit year span and exact SQLite year extraction."""
    diagnostics: dict[str, Any] = {
        "version": EXPLICIT_YEAR_SPAN_NORMALIZER_VERSION,
        "status": "unchanged",
        "changed_literals": 0,
        "translated_date_parts": 0,
    }
    source_text = f"{question}\n{provided_context}"
    has_full_span = _FULL_YEAR_SPAN.search(source_text) is not None
    has_strftime_call = _STRFTIME_CALL.search(sql) is not None
    if not has_full_span and not has_strftime_call:
        diagnostics["reason"] = "no-explicit-full-year-span"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics

    literal_changes = []
    if has_full_span:
        for literal in tuple(tree.find_all(exp.Literal)):
            if not literal.is_string or not _in_filter(literal):
                continue
            replacement = _full_span_for(str(literal.this), source_text)
            if replacement is None or replacement == literal.this:
                continue
            literal_changes.append({"from": str(literal.this), "to": replacement})
            literal.replace(exp.Literal.string(replacement))

    translated_date_parts = 0
    for function in tuple(tree.find_all(exp.Anonymous)):
        if str(function.this).casefold() != "strftime":
            continue
        arguments = list(function.expressions)
        if (
            len(arguments) != 2
            or not isinstance(arguments[0], exp.Literal)
            or not arguments[0].is_string
            or str(arguments[0].this) != "%Y"
        ):
            continue
        function.replace(exp.Year(this=arguments[1].copy()))
        translated_date_parts += 1

    if not literal_changes and not translated_date_parts:
        diagnostics["reason"] = (
            "no-supported-dialect-date-part"
            if has_strftime_call
            else "no-lossy-year-span-filter"
        )
        return sql, diagnostics

    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "changed_literals": len(literal_changes),
            "translated_date_parts": translated_date_parts,
            "changes": literal_changes,
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any) -> Any:
    sql = ctx.sql or ""
    normalized, diagnostics = normalize_explicit_year_span_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.explicit_year_span_normalization = diagnostics
    return ctx


__all__ = [
    "EXPLICIT_YEAR_SPAN_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_explicit_year_span_sql",
]
