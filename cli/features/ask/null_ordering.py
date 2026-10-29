"""Rank NULLs after values in ORDER BY keys that a LIMIT bounds."""

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from .encoded_identifier_storage import _schema_column, _schema_table

_DIALECTS = {"postgresql": "postgres", "postgres": "postgres", "mysql": "mysql"}
_MISSING_VALUES = re.compile(
    r"\b(?:nulls?|missing|unknown|blank|unrated|unscored|not recorded"
    r"|(?:no|without(?: an?)?) (?:recorded |known )?"
    r"(?:value|score|rating|data|date|entry|number|amount|price)s?)\b",
    re.IGNORECASE,
)


def mentions_missing_values(question: str | None) -> bool:
    """The question says where rows without a value belong, so the SQL already does."""
    return bool(question and _MISSING_VALUES.search(question))


def place_nulls_last(sql: str, db_type: str | None, schema_info: Any = None) -> str | None:
    """Return SQL whose LIMIT-bounded ORDER BY keys sort NULLs after values.

    A bounded ranking otherwise gives its top places to rows whose ranked value is missing
    wherever the dialect sorts NULL first: PostgreSQL for DESC, MySQL for ASC. Keys with a
    written NULLS FIRST/LAST or an earlier IS NULL key, and keys that are never NULL, keep
    their order. Returns None when no key changes.
    """
    read = _DIALECTS.get((db_type or "").casefold())
    if read is None:
        return None
    # Parsed without implied NULL placement, a key keeps nulls_first only when written.
    written_dialect = Dialect.get_or_raise(read)
    written_dialect.NULL_ORDERING = "nulls_are_last"
    try:
        statements = sqlglot.parse(sql, read=read)
        written_statements = sqlglot.parse(sql, read=written_dialect)
    except sqlglot.errors.SqlglotError:
        return None
    if len(statements) != 1 or statements[0] is None:
        return None
    tree = statements[0]
    # A join without ON or USING is written back as a comma join, which binds looser than
    # JOIN and can hide an earlier table from a later one (JSON_TABLE over a joined column).
    if any(not (join.args.get("on") or join.args.get("using"))
           for join in tree.find_all(exp.Join)):
        return None
    changed = False
    selects = list(tree.find_all(exp.Select))
    for select, written in zip(selects, written_statements[0].find_all(exp.Select)):
        order = select.args.get("order")
        if order is None or select.args.get("limit") is None:
            continue
        keys = order.expressions
        placed = []
        for position, (key, written_key) in enumerate(
            zip(keys, written.args["order"].expressions)
        ):
            value, ranked = _positional_target(key.this, select)
            if (
                value is None
                or (read == "mysql" and not _orderable_after_grouping(value, ranked, select))
                or key.args.get("nulls_first") is not True
                or written_key.args.get("nulls_first")
                or _never_null(ranked, select, schema_info)
                or any(
                    null_test.this == value
                    for earlier in keys[:position]
                    for null_test in _null_tests(earlier)
                )
            ):
                placed.append(key)
                continue
            if read == "mysql":
                # MySQL has no NULLS LAST. A leading IS NULL key keeps the key as written, so
                # a select-list alias over a GROUP BY expression still satisfies
                # only_full_group_by.
                null_test = exp.Is(this=value.copy(), expression=exp.Null())
                placed.append(exp.Ordered(this=null_test, nulls_first=True))
                placed.append(key)
            else:
                key.set("nulls_first", False)
                placed.append(key)
            changed = True
        order.set("expressions", placed)
    return tree.sql(dialect=read) if changed else None


def _positional_target(
    value: exp.Expression, select: exp.Select
) -> tuple[exp.Expression | None, exp.Expression | None]:
    """A key's reference and the expression it ranks. A select-list position stands for that
    item, referenced by its alias when it has one; neither is known for a position the
    select list does not spell out, as under a star."""
    if not (isinstance(value, exp.Literal) and value.is_int):
        return value, value
    items = select.expressions
    index = int(value.name) - 1
    if not 0 <= index < len(items) or any(item.is_star for item in items):
        return None, None
    item = items[index]
    ranked = item.this if isinstance(item, exp.Alias) else item
    return (exp.column(item.alias) if item.alias else ranked.copy()), ranked


def _orderable_after_grouping(value: exp.Expression, ranked: exp.Expression,
                              select: exp.Select) -> bool:
    """Whether MySQL's only_full_group_by accepts an IS NULL test on the key. A grouped
    statement only admits an aggregate, a grouped expression as written, or a select-list
    alias that no column of the same name hides."""
    group = select.args.get("group")
    if group is None or any(ranked.find_all(exp.AggFunc)):
        return True
    if any(value == grouped for grouped in group.expressions):
        return True
    if not isinstance(value, exp.Column) or value.table:
        return False
    name = value.name.casefold()
    if not any(item.alias.casefold() == name for item in select.expressions):
        return False
    return not any(column.name.casefold() == name for column in select.find_all(exp.Column)
                   if not column.find_ancestor(exp.Order))


def _null_tests(node: exp.Expression) -> list[exp.Is]:
    return [n for n in node.find_all(exp.Is) if isinstance(n.expression, exp.Null)]


def _never_null(value: exp.Expression, select: exp.Select, schema_info: Any) -> bool:
    # Constants and null tests rank no missing value, and COUNT is never NULL.
    if not any(value.find_all(exp.Column)) or _null_tests(value):
        return True
    if isinstance(value, exp.Count):
        return True
    return _primary_key_column(value, select, schema_info)


def _primary_key_column(value: exp.Expression, select: exp.Select, schema_info: Any) -> bool:
    """A declared primary key column, unless an outer join can fill it with NULL."""
    if schema_info is None or not isinstance(value, exp.Column) or value.is_star:
        return False
    if any(join.side for join in select.args.get("joins") or []):
        return False
    tables = [t for t in select.find_all(exp.Table) if t.parent_select is select]
    if value.table:
        names = [t.name for t in tables if t.alias_or_name.casefold() == value.table.casefold()]
    else:
        # A bare name may refer to a select-list alias rather than the column.
        if any(e.alias.casefold() == value.name.casefold() for e in select.expressions):
            return False
        names = [t.name for t in tables]
    if len(names) != 1:
        return False
    table = _schema_table(schema_info, names[0])
    column = _schema_column(table, value.name) if table is not None else None
    return bool(getattr(column, "is_primary_key", False))
