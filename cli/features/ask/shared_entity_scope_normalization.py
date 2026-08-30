"""Propagate an explicit entity scope across paired scalar answer branches."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.correction_intent_state import selected_correction_intents

SHARED_ENTITY_SCOPE_NORMALIZER_VERSION = "shared-entity-scope-v5"

_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9]+")
_GENERIC_TERMS = frozenset({"id", "ids", "identifier", "key", "number", "num"})


@dataclass(frozen=True)
class _ScopeCandidate:
    source: exp.Select
    target: exp.Select
    source_table: exp.Table
    target_table: exp.Table
    column_name: str
    literal: exp.Literal


def _identifier_terms(identifier: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identifier)
    separated = separated.replace("_", " ")
    return tuple(
        token.casefold()
        for token in _IDENTIFIER_TOKEN.findall(separated)
        if token.casefold() not in _GENERIC_TERMS
    )


def _source_mentions_scope(
    question: str,
    column_name: str,
    literal: exp.Literal,
    *,
    require_identifier_term: bool = True,
) -> bool:
    value = str(literal.this)
    folded_question = question.casefold()
    folded_value = value.casefold()
    offset = 0
    matched = False
    while True:
        position = folded_question.find(folded_value, offset)
        if position < 0:
            break
        before = folded_question[position - 1] if position else ""
        end = position + len(folded_value)
        after = folded_question[end] if end < len(folded_question) else ""
        if (not before or not before.isalnum()) and (not after or not after.isalnum()):
            matched = True
            break
        offset = position + 1
    if not matched:
        return False
    if not require_identifier_term:
        return True
    return any(
        re.search(rf"\b{re.escape(term)}\b", folded_question)
        for term in _identifier_terms(column_name)
    )


def _scalar_select(expression: exp.Expression) -> exp.Select | None:
    node = expression.this if isinstance(expression, exp.Alias) else expression
    if not isinstance(node, exp.Subquery) or not isinstance(node.this, exp.Select):
        return None
    select = node.this
    if sum(1 for _ in select.find_all(exp.Select)) != 1:
        return None
    return select


def _single_table(select: exp.Select) -> exp.Table | None:
    from_clause = select.args.get("from_")
    if from_clause is None or not isinstance(from_clause.this, exp.Table):
        return None
    if select.args.get("joins"):
        return None
    return from_clause.this


def _direct_column(expression: exp.Expression) -> exp.Column | None:
    node = expression.this if isinstance(expression, exp.Alias) else expression
    return node if isinstance(node, exp.Column) else None


def _projects_column(select: exp.Select, column_name: str) -> bool:
    folded = column_name.casefold()
    return any(
        expression.alias_or_name.casefold() == folded
        for expression in select.expressions
        if expression.alias_or_name
    )


def _cross_join_answer_branch_pairs(
    tree: exp.Select,
) -> tuple[tuple[exp.Select, exp.Select], ...] | None:
    """Extract two one-column scalar derived tables joined only for presentation."""
    if any(
        tree.args.get(key) is not None
        for key in (
            "where",
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
    from_clause = tree.args.get("from_")
    joins = tree.args.get("joins") or ()
    if (
        from_clause is None
        or not isinstance(from_clause.this, exp.Subquery)
        or not isinstance(from_clause.this.this, exp.Select)
        or len(joins) != 1
    ):
        return None
    join = joins[0]
    if (
        str(join.args.get("kind") or "").casefold() != "cross"
        or not isinstance(join.this, exp.Subquery)
        or not isinstance(join.this.this, exp.Select)
    ):
        return None

    subqueries = (from_clause.this, join.this)
    selects = (subqueries[0].this, subqueries[1].this)
    if any(
        len(select.expressions) != 1 or sum(1 for _ in select.find_all(exp.Select)) != 1
        for select in selects
    ):
        return None
    aliases = tuple(subquery.alias_or_name for subquery in subqueries)
    if not all(aliases) or aliases[0].casefold() == aliases[1].casefold():
        return None
    columns = tuple(_direct_column(expression) for expression in tree.expressions)
    if any(column is None or not column.table for column in columns):
        return None
    projections = {
        column.table.casefold(): column
        for column in columns
        if column is not None and column.table
    }
    if set(projections) != {alias.casefold() for alias in aliases}:
        return None
    if not all(
        _projects_column(select, projections[alias.casefold()].name)
        for select, alias in zip(selects, aliases)
    ):
        return None
    return ((selects[0], selects[1]), (selects[1], selects[0]))


def _direct_table_cross_join_answer_branch_pairs(
    tree: exp.Select,
) -> tuple[tuple[exp.Select, exp.Select], ...] | None:
    """Extract a direct lookup presented beside one grouped scalar branch."""
    if any(
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
    from_clause = tree.args.get("from_")
    joins = tree.args.get("joins") or ()
    if (
        from_clause is None
        or not isinstance(from_clause.this, exp.Table)
        or len(joins) != 1
    ):
        return None
    join = joins[0]
    if (
        str(join.args.get("kind") or "").casefold() != "cross"
        or join.args.get("on") is not None
        or join.args.get("using") is not None
        or not isinstance(join.this, exp.Subquery)
        or not isinstance(join.this.this, exp.Select)
    ):
        return None

    target = join.this.this
    if len(target.expressions) != 1 or sum(1 for _ in target.find_all(exp.Select)) != 1:
        return None
    source_alias = from_clause.this.alias_or_name
    target_alias = join.this.alias_or_name
    if (
        not source_alias
        or not target_alias
        or source_alias.casefold() == target_alias.casefold()
    ):
        return None
    columns = tuple(_direct_column(expression) for expression in tree.expressions)
    if any(column is None or not column.table for column in columns):
        return None
    projections = {
        column.table.casefold(): column
        for column in columns
        if column is not None and column.table
    }
    if (
        len(columns) != 2
        or set(projections) != {source_alias.casefold(), target_alias.casefold()}
        or not _projects_column(
            target,
            projections[target_alias.casefold()].name,
        )
    ):
        return None

    # Candidate discovery only reads the direct branch.  Remove the presentation
    # join from a copy so _single_table cannot mistake the grouped branch for a
    # semantic join; the original target remains attached to the returned tree.
    source = tree.copy()
    source.set("joins", [])
    source.set(
        "expressions",
        [projections[source_alias.casefold()].copy()],
    )
    return ((source, target),)


def _answer_branch_pairs(
    tree: exp.Select,
) -> tuple[tuple[exp.Select, exp.Select], ...] | None:
    """Return possible (source, target) pairs for two supported answer shapes."""
    if len(tree.expressions) != 2:
        return None
    if tree.args.get("joins"):
        return _cross_join_answer_branch_pairs(
            tree
        ) or _direct_table_cross_join_answer_branch_pairs(tree)

    scalar_selects = tuple(
        _scalar_select(expression) for expression in tree.expressions
    )
    if tree.args.get("from_") is None:
        if any(select is None for select in scalar_selects):
            return None
        first, second = scalar_selects
        assert first is not None and second is not None
        return ((first, second), (second, first))

    scalar_indexes = tuple(
        index for index, select in enumerate(scalar_selects) if select is not None
    )
    if len(scalar_indexes) != 1:
        return None
    scalar_index = scalar_indexes[0]
    direct_index = 1 - scalar_index
    direct_column = _direct_column(tree.expressions[direct_index])
    group = tree.args.get("group")
    if direct_column is None or group is None:
        return None
    if not any(
        isinstance(grouped, exp.Column)
        and grouped.name.casefold() == direct_column.name.casefold()
        for grouped in group.expressions
    ):
        return None
    source = scalar_selects[scalar_index]
    assert source is not None
    return ((source, tree),)


def _safe_where_equalities(
    select: exp.Select,
) -> tuple[tuple[exp.Column, exp.Literal], ...]:
    where = select.args.get("where")
    if where is None:
        return ()
    pairs: list[tuple[exp.Column, exp.Literal]] = []
    for equality in where.find_all(exp.EQ):
        ancestor = equality.parent
        safe = True
        while ancestor is not None and ancestor is not where:
            if isinstance(ancestor, (exp.Or, exp.Not)):
                safe = False
                break
            ancestor = ancestor.parent
        if not safe:
            continue
        left, right = equality.this, equality.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            pairs.append((left, right))
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            pairs.append((right, left))
    return tuple(pairs)


def _has_grouped_aggregate_intent(select: exp.Select) -> bool:
    group = select.args.get("group")
    having = select.args.get("having")
    return bool(
        group is not None
        and group.expressions
        and having is not None
        and next(having.find_all(exp.AggFunc), None) is not None
    )


def _references_column(select: exp.Select, column_name: str) -> bool:
    folded = column_name.casefold()
    for clause_name in ("where", "having"):
        clause = select.args.get(clause_name)
        if clause is not None and any(
            column.name.casefold() == folded for column in clause.find_all(exp.Column)
        ):
            return True
    return False


def _schema_column(schema_info: Any, table_name: str, column_name: str) -> Any | None:
    if schema_info is None:
        return None
    for table_key, table in (getattr(schema_info, "tables", {}) or {}).items():
        actual_table = str(getattr(table, "name", "") or table_key)
        if actual_table.casefold() != table_name.casefold():
            continue
        for column_key, column in (getattr(table, "columns", {}) or {}).items():
            actual_column = str(getattr(column, "name", "") or column_key)
            if actual_column.casefold() == column_name.casefold():
                return column
    return None


def _compatible_shared_column(
    schema_info: Any,
    source_table: str,
    target_table: str,
    column_name: str,
) -> bool:
    source = _schema_column(schema_info, source_table, column_name)
    target = _schema_column(schema_info, target_table, column_name)
    if source is None or target is None:
        return False
    source_type = (
        str(getattr(source, "data_type", "") or "").casefold().split("(", 1)[0]
    )
    target_type = (
        str(getattr(target, "data_type", "") or "").casefold().split("(", 1)[0]
    )
    return bool(source_type and source_type == target_type)


def _candidate(
    *,
    question: str,
    schema_info: Any,
    source: exp.Select,
    target: exp.Select,
    routed_intent: bool = False,
) -> _ScopeCandidate | None:
    if not _has_grouped_aggregate_intent(target):
        return None
    source_table = _single_table(source)
    target_table = _single_table(target)
    if source_table is None or target_table is None:
        return None
    if source_table.name.casefold() == target_table.name.casefold():
        return None
    candidates: list[_ScopeCandidate] = []
    for column, literal in _safe_where_equalities(source):
        if column.table and column.table.casefold() not in {
            source_table.name.casefold(),
            source_table.alias_or_name.casefold(),
        }:
            continue
        if not _identifier_terms(column.name):
            continue
        if not _source_mentions_scope(
            question,
            column.name,
            literal,
            require_identifier_term=not routed_intent,
        ):
            continue
        if _references_column(target, column.name):
            continue
        if not _compatible_shared_column(
            schema_info,
            source_table.name,
            target_table.name,
            column.name,
        ):
            continue
        candidates.append(
            _ScopeCandidate(
                source=source,
                target=target,
                source_table=source_table,
                target_table=target_table,
                column_name=column.name,
                literal=literal,
            )
        )
    return candidates[0] if len(candidates) == 1 else None


def normalize_shared_entity_scope_sql(
    *,
    question: str,
    sql: str,
    dialect: str,
    schema_info: Any,
    intent_hints: Collection[str] = (),
) -> tuple[str, dict[str, Any]]:
    """Copy one explicit entity filter into a related aggregate answer branch."""
    diagnostics: dict[str, Any] = {
        "version": SHARED_ENTITY_SCOPE_NORMALIZER_VERSION,
        "status": "unchanged",
        "propagated_scopes": 0,
        "model_calls": 0,
        "execution_feedback": False,
    }
    routed_intent = "shared_scope_all_answers" in intent_hints
    paired_intent = question.count("?") >= 2 or routed_intent
    if not paired_intent:
        diagnostics["reason"] = "not-a-paired-question"
        return sql, diagnostics
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
    except sqlglot.errors.ParseError as exc:
        diagnostics["status"] = "parse-error"
        diagnostics["error_kind"] = type(exc).__name__
        return sql, diagnostics
    if not isinstance(tree, exp.Select):
        diagnostics["reason"] = "not-two-supported-answer-branches"
        return sql, diagnostics
    branch_pairs = _answer_branch_pairs(tree)
    if branch_pairs is None:
        diagnostics["reason"] = "not-two-supported-answer-branches"
        return sql, diagnostics
    candidates = tuple(
        candidate
        for source, target in branch_pairs
        for candidate in (
            _candidate(
                question=question,
                schema_info=schema_info,
                source=source,
                target=target,
                routed_intent=routed_intent,
            ),
        )
        if candidate is not None
    )
    if len(candidates) != 1:
        diagnostics["reason"] = (
            "no-eligible-shared-scope"
            if not candidates
            else "multiple-eligible-shared-scopes"
        )
        return sql, diagnostics

    candidate = candidates[0]
    target_alias = candidate.target_table.alias or None
    predicate = exp.EQ(
        this=exp.column(candidate.column_name, table=target_alias),
        expression=candidate.literal.copy(),
    )
    candidate.target.where(predicate, append=True, copy=False)
    normalized = tree.sql(dialect=read_dialect)
    diagnostics.update(
        {
            "status": "normalized",
            "propagated_scopes": 1,
            "source_table": candidate.source_table.name,
            "target_table": candidate.target_table.name,
            "column": candidate.column_name,
            "literal_sha256": hashlib.sha256(
                str(candidate.literal.this).encode()
            ).hexdigest(),
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
    normalized, diagnostics = normalize_shared_entity_scope_sql(
        question=ctx.refined_question or ctx.question,
        sql=sql,
        dialect=ctx.db_type,
        schema_info=ctx.schema_info,
        intent_hints=selected_intents,
    )
    if normalized != sql:
        ctx.sql = normalized
        ctx.generated_sql = normalized
    ctx.shared_entity_scope_normalization = diagnostics
    return ctx


__all__ = [
    "SHARED_ENTITY_SCOPE_NORMALIZER_VERSION",
    "normalize_context",
    "normalize_shared_entity_scope_sql",
]
