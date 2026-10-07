"""Read-only views of write statements.

An UPDATE or DELETE has to find its rows before it changes them. `lookup_select` turns that
search into a SELECT over the same tables and WHERE clause, which plans the same way and needs
only read privilege, so deep analysis can study it without running or explaining the write.
`write_shape` reports the table a write changes and the columns an UPDATE sets.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import sqlglot
from sqlglot import exp

DIALECTS = {"postgresql": "postgres", "mysql": "mysql"}


def _parse(sql: str, engine: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read=DIALECTS.get(engine, "postgres"))
    except Exception:
        return None


def _table_name(node: exp.Expression | None) -> str | None:
    table = node.find(exp.Table) if node is not None and not isinstance(node, exp.Table) else node
    return table.name.lower() if isinstance(table, exp.Table) and table.name else None


@lru_cache(maxsize=2048)
def write_shape(sql: str, engine: str) -> dict[str, Any] | None:
    """{verb, table, set_columns} for INSERT, UPDATE and DELETE; None for anything else."""
    tree = _parse(sql, engine)
    if isinstance(tree, exp.Update):
        columns = sorted({assignment.this.name.lower() for assignment in tree.expressions
                          if isinstance(assignment, exp.EQ) and isinstance(assignment.this, exp.Column)})
        return {"verb": "update", "table": _table_name(tree.this), "set_columns": columns}
    if isinstance(tree, exp.Delete):
        return {"verb": "delete", "table": _table_name(tree.this), "set_columns": []}
    if isinstance(tree, exp.Insert):
        return {"verb": "insert", "table": _table_name(tree.this), "set_columns": []}
    return None


def _placeholders(node: exp.Expression | None) -> int:
    return 0 if node is None else sum(1 for _ in node.find_all(exp.Placeholder, exp.Parameter))


@lru_cache(maxsize=2048)
def lookup_select(sql: str, engine: str) -> dict[str, Any] | None:
    """{sql, table, skip_params} for the row search inside a write, or None when there is none.

    skip_params counts the positional parameters that belong to the SET list ahead of the
    WHERE clause; a sampled value list drops that many from its front. PostgreSQL's numbered
    parameters keep their numbers, so it is always 0 there.
    """
    tree = _parse(sql, engine)
    dialect = DIALECTS.get(engine, "postgres")
    if isinstance(tree, exp.Insert):
        source = tree.expression
        if isinstance(source, (exp.Select, exp.Union)):
            return {"sql": source.sql(dialect=dialect), "table": _table_name(tree.this),
                    "skip_params": 0}
        return None
    if not isinstance(tree, (exp.Update, exp.Delete)):
        return None
    where = tree.args.get("where")
    if where is None:
        return None
    sources = [tree.this]
    for key in ("from_", "from", "using"):
        extra = tree.args.get(key)
        if isinstance(extra, exp.From):
            sources.append(extra.this)
        elif isinstance(extra, list):
            sources.extend(extra)
    tables = ", ".join(source.sql(dialect=dialect) for source in sources)
    joins = "".join(" " + join.sql(dialect=dialect) for join in tree.args.get("joins") or [])
    text = f"SELECT 1 FROM {tables}{joins} WHERE {where.this.sql(dialect=dialect)}"
    # MySQL's UPDATE/DELETE ... ORDER BY ... LIMIT finds only its first rows; keep that shape.
    for key in ("order", "limit"):
        part = tree.args.get(key)
        if part is not None:
            text += " " + part.sql(dialect=dialect)
    cte = tree.args.get("with") or tree.args.get("with_")
    if cte is not None:
        text = f"{cte.sql(dialect=dialect)} {text}"
    skip = _placeholders(exp.Tuple(expressions=list(tree.expressions))) \
        if engine == "mysql" and isinstance(tree, exp.Update) else 0
    return {"sql": text, "table": _table_name(tree.this), "skip_params": skip}


def lookup_for(sql: str, engine: str | None = None) -> dict[str, Any] | None:
    """lookup_select in the target's dialect, or in either when the engine isn't known."""
    engines = [engine] if engine else list(DIALECTS)
    return next((found for name in engines if (found := lookup_select(sql, name))), None)


_EQUALITY = (exp.EQ, exp.In, exp.Is)
_RANGE = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.Like, exp.ILike)


@lru_cache(maxsize=2048)
def column_roles(sql: str, engine: str) -> dict[str, str]:
    """How a query uses each column it filters or sorts on: equality, sort or range.

    Equality wins when a column is used more than one way. Names are lowercased, unqualified.
    """
    # Parameter numbers and casts on parameters ($1::date) don't matter here, and some
    # spellings of them don't parse.
    tree = _parse(re.sub(r"(\$\d+|\?)(::\w+(\[\])?)?", "?", sql), engine)
    if tree is None:
        return {}
    roles: dict[str, str] = {}
    rank = {"equality": 0, "sort": 1, "range": 2}

    def mark(node: exp.Expression, role: str) -> None:
        for column in node.find_all(exp.Column):
            name = column.name.lower()
            if name and (name not in roles or rank[role] < rank[roles[name]]):
                roles[name] = role

    for predicate in tree.find_all(*_EQUALITY):
        if isinstance(predicate, exp.EQ) and all(isinstance(side, exp.Column)
                                                 for side in (predicate.this, predicate.expression)):
            continue  # A join condition says nothing about lookup order.
        mark(predicate.this, "equality")
    for predicate in tree.find_all(*_RANGE):
        mark(predicate.this, "range")
    for order in tree.find_all(exp.Ordered):
        mark(order.this, "sort")
    return roles


def order_index_columns(columns: list[str], roles: dict[str, str]) -> list[str]:
    """Equality columns first, then sort, then range, then the rest, each in the given order."""
    rank = {"equality": 0, "sort": 1, "range": 2}
    return [column for _, _, column in sorted(
        (rank.get(roles.get(column.lower(), ""), 3), position, column)
        for position, column in enumerate(columns))]
