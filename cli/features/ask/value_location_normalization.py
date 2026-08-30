"""Bounded exact-value grounding for ambiguous sibling columns."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from shared.db_connection import quote_identifier

VALUE_LOCATION_NORMALIZER_VERSION = "ambiguous-exact-value-location-v2"
MAX_SUPPORT = 101
MIN_STRONG_SUPPORT = 8
MIN_SUPPORT_RATIO = 4

_TEXT_TYPES = frozenset({"char", "enum", "json", "text", "varchar"})
_PARTIAL_CUES = re.compile(
    r"\b(?:contain(?:s|ing)?|include(?:s|ing)?|partial|substring|pattern|"
    r"starts?\s+with|ends?\s+with|matches?)\b",
    re.IGNORECASE,
)
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _term_root(token: str) -> str:
    token = token.casefold()
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _identifier_terms(identifier: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identifier)
    separated = separated.replace("_", " ")
    return tuple(_term_root(token) for token in _IDENTIFIER_TOKEN.findall(separated))


def _text_type(value: str) -> bool:
    return value.casefold().split("(", 1)[0].strip() in _TEXT_TYPES


def _source_has_exact_literal(source: str, literal: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(literal)}(?![A-Za-z0-9])",
            source,
            re.IGNORECASE,
        )
    )


def _partial_match_requested(source: str, literal: str) -> bool:
    for match in re.finditer(re.escape(literal), source, re.IGNORECASE):
        start = max(0, match.start() - 64)
        end = min(len(source), match.end() + 64)
        if _PARTIAL_CUES.search(source[start:end]):
            return True
    return False


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        alias = table.alias_or_name.casefold()
        name = table.name
        previous = aliases.get(alias)
        if previous is not None and previous.casefold() != name.casefold():
            return {}
        aliases[alias] = name
    return aliases


def _schema_table(schema_info: Any, table_name: str) -> Any | None:
    if schema_info is None:
        return None
    for name, table in schema_info.tables.items():
        if name.casefold() == table_name.casefold():
            return table
    return None


def _equivalent_sibling(table: Any, column_name: str) -> Any | None:
    current = None
    for name, column in table.columns.items():
        if name.casefold() == column_name.casefold():
            current = column
            break
    if current is None or not _text_type(current.data_type):
        return None
    terms = _identifier_terms(current.name)
    siblings = [
        column
        for column in table.columns.values()
        if column.name.casefold() != current.name.casefold()
        and _text_type(column.data_type)
        and _identifier_terms(column.name) == terms
    ]
    return siblings[0] if len(siblings) == 1 else None


def _support_query(*, table: str, column: str, literal: str, dialect: str) -> str:
    engine = "postgresql" if dialect in {"postgres", "postgresql"} else "mysql"
    read_dialect = "postgres" if engine == "postgresql" else "mysql"
    table_sql = quote_identifier(table, engine)
    column_sql = quote_identifier(column, engine)
    literal_sql = exp.Literal.string(literal).sql(dialect=read_dialect)
    return (
        "SELECT COUNT(*) FROM (SELECT 1 FROM "
        f"{table_sql} WHERE {column_sql} = {literal_sql} LIMIT {MAX_SUPPORT}) "
        "AS _rdst_value_support"
    )


def _probe_support(
    db_executor: Callable[..., Any],
    target_config: dict[str, Any],
    *,
    table: str,
    column: str,
    literal: str,
    dialect: str,
) -> tuple[int | None, str]:
    query = _support_query(
        table=table,
        column=column,
        literal=literal,
        dialect=dialect,
    )
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    try:
        result = db_executor(query, target_config)
        rows = result.get("rows") if isinstance(result, dict) else None
        if not isinstance(result, dict) or not result.get("success") or not rows:
            return None, query_hash
        return int(rows[0][0]), query_hash
    # Database adapters may raise transport-, driver-, or timeout-specific
    # exceptions.  Grounding is an optional refinement, so every ordinary
    # probe failure must leave the generated SQL untouched.
    except Exception:
        return None, query_hash


def normalize_ambiguous_value_location_sql(
    *,
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
    schema_info: Any,
    db_executor: Callable[..., Any] | None,
    target_config: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Replace one unsupported contains predicate with a strongly grounded sibling."""
    diagnostics: dict[str, Any] = {
        "version": VALUE_LOCATION_NORMALIZER_VERSION,
        "status": "unchanged",
        "probe_count": 0,
        "execution_feedback": False,
        "database_value_feedback": True,
    }
    if db_executor is None or target_config is None:
        diagnostics["reason"] = "database-probe-unavailable"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics

    aliases = _alias_map(tree)
    source = f"{question}\n{provided_context}"
    candidates: list[tuple[exp.Like, exp.Column, str, str, Any]] = []
    unique_tables = {table.casefold() for table in aliases.values()}
    for predicate in tree.find_all(exp.Like):
        column = predicate.this
        pattern = predicate.expression
        if not isinstance(column, exp.Column) or not isinstance(pattern, exp.Literal):
            continue
        raw = str(pattern.this)
        if (
            len(raw) < 3
            or not raw.startswith("%")
            or not raw.endswith("%")
            or "%" in raw[1:-1]
            or "_" in raw[1:-1]
        ):
            continue
        literal = raw[1:-1]
        if not _source_has_exact_literal(source, literal) or _partial_match_requested(
            source, literal
        ):
            continue
        if column.table:
            table_name = aliases.get(column.table.casefold())
        elif len(unique_tables) == 1:
            table_name = next(iter(aliases.values()), None)
        else:
            table_name = None
        if not table_name:
            continue
        table = _schema_table(schema_info, table_name)
        sibling = _equivalent_sibling(table, column.name) if table is not None else None
        if sibling is not None:
            candidates.append((predicate, column, literal, table.name, sibling))

    if len(candidates) != 1:
        diagnostics["reason"] = (
            "no-eligible-ambiguous-location"
            if not candidates
            else "multiple-eligible-ambiguous-locations"
        )
        return sql, diagnostics

    predicate, column, literal, table_name, sibling = candidates[0]
    current_support, current_hash = _probe_support(
        db_executor,
        target_config,
        table=table_name,
        column=column.name,
        literal=literal,
        dialect=dialect,
    )
    sibling_support, sibling_hash = _probe_support(
        db_executor,
        target_config,
        table=table_name,
        column=sibling.name,
        literal=literal,
        dialect=dialect,
    )
    diagnostics.update(
        {
            "probe_count": 2,
            "probe_sha256": [current_hash, sibling_hash],
            "source_column": f"{table_name}.{column.name}",
            "candidate_column": f"{table_name}.{sibling.name}",
        }
    )
    if current_support is None or sibling_support is None:
        diagnostics["reason"] = "database-probe-failed"
        return sql, diagnostics
    if not (
        sibling_support >= MIN_STRONG_SUPPORT
        and sibling_support >= max(1, current_support) * MIN_SUPPORT_RATIO
    ):
        diagnostics["reason"] = "candidate-support-not-dominant"
        return sql, diagnostics

    replacement_column = exp.column(sibling.name, table=column.table or None)
    predicate.replace(
        exp.EQ(
            this=replacement_column,
            expression=exp.Literal.string(literal),
        )
    )
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "selection_policy": "equivalent-field-dominant-exact-support-v1",
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any, db_executor: Callable[..., Any] | None) -> Any:
    sql = ctx.sql or ""
    normalized, diagnostics = normalize_ambiguous_value_location_sql(
        question=ctx.refined_question or ctx.question,
        provided_context=ctx.provided_context,
        sql=sql,
        dialect=ctx.db_type,
        schema_info=ctx.schema_info,
        db_executor=db_executor,
        target_config=ctx.target_config,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.value_location_normalization = diagnostics
    return ctx


__all__ = [
    "VALUE_LOCATION_NORMALIZER_VERSION",
    "normalize_ambiguous_value_location_sql",
    "normalize_context",
]
