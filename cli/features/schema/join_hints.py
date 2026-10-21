"""Likely join paths that a semantic layer does not declare as foreign keys.

Two kinds, both derived from the layer alone:

- Columns in different tables that reference the same key (``a.zoneref`` and
  ``b.zoneref`` both reference ``sites.zoneregistry``) hold the same values, so the tables
  can be joined on them directly.
- Columns with the same key-like name and a compatible type in different tables, where no
  declared foreign key links them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

VERSION = "join-hints-v1"

_SQL_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_EQUALITY = re.compile(
    rf"^\s*({_SQL_IDENTIFIER})\.({_SQL_IDENTIFIER})\s*=\s*"
    rf"({_SQL_IDENTIFIER})\.({_SQL_IDENTIFIER})\s*$"
)
# Names that repeat across tables without meaning the same thing.
_GENERIC_NAMES = frozenset(
    {
        "id", "name", "title", "description", "type", "kind", "status", "state", "label",
        "category", "notes", "note", "comment", "comments", "value", "amount", "count",
        "price", "quantity", "date", "time", "timestamp", "year", "month", "day",
        "created", "updated", "created_at", "updated_at", "deleted_at", "version",
    }
)  # fmt: skip
_KEY_SUFFIX = re.compile(r"(?:id|ref|key|code|registry|uuid)$", re.IGNORECASE)
# Words that end like a key suffix without being one ("amount_paid", "is_valid").
_NOT_A_KEY_WORD = re.compile(
    r"(?:paid|unpaid|prepaid|valid|invalid|void|avoid|said|liquid|rapid|hybrid|solid|humid"
    r"|hockey|turkey|monkey|jockey)$",
    re.IGNORECASE,
)
# Flags are never keys, even when their name ends like one. MySQL's introspection stores
# tinyint(1) as tinyint.
_BOOLEAN = re.compile(r"bool|bit\b|tinyint", re.IGNORECASE)
_KEY_FAMILIES = ("integer", "text", "uuid")
_TYPE_FAMILIES = (
    ("uuid", re.compile(r"uuid", re.IGNORECASE)),
    ("integer", re.compile(r"int|serial", re.IGNORECASE)),
    ("text", re.compile(r"char|text|string|enum|clob", re.IGNORECASE)),
)


@dataclass(frozen=True)
class PossibleJoin:
    """Columns, as (table, column), that hold the same key; ``references`` is the declared
    key they all reference, if any."""

    columns: tuple[tuple[str, str], ...]
    references: tuple[str, str] | None = None


def possible_joins(layer: Any) -> list[PossibleJoin]:
    """Join paths between tables that no declared foreign key states, in a stable order."""
    declared, referencing = _declared_edges(layer)
    joins = []
    covered = set(declared)
    for key, columns in sorted(referencing.items()):
        group = sorted(columns)
        pairs = _cross_table_pairs(group)
        if len({table for table, _ in group}) == len(group):
            if pairs - covered:
                joins.append(PossibleJoin(tuple(group), key))
                covered |= pairs
            continue
        # Two columns of one table that reference the same key (an origin and a destination)
        # do not hold the same value, so each cross-table pair is its own join.
        for pair in sorted(pairs - covered, key=sorted):
            joins.append(PossibleJoin(tuple(sorted(pair)), key))
            covered.add(pair)

    # A shared name is a key when its suffix says so or one table makes it the primary key.
    by_name: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    keys: set[tuple[str, str]] = set()
    primary_columns: set[tuple[str, str]] = set()
    for table_name, table in sorted(layer.tables.items()):
        primary = _primary_key(table)
        primary_columns |= {(table_name, column) for column in primary}
        for column_name, column in sorted(table.columns.items()):
            if column_name.casefold() in _GENERIC_NAMES or _BOOLEAN.search(
                column.data_type or ""
            ):
                continue
            name = (column_name.casefold(), _type_family(column.data_type or ""))
            by_name[name].append((table_name, column_name))
            # Keys are whole numbers or text; a shared name on amounts or dates is not one.
            if name[1] in _KEY_FAMILIES and (
                column_name in primary
                or (_KEY_SUFFIX.search(column_name) and not _NOT_A_KEY_WORD.search(column_name))
            ):
                keys.add(name)
    for name, group in sorted(by_name.items()):
        # Each table's own primary key sharing a name ("uuid", "code") joins nothing.
        if name not in keys or all(column in primary_columns for column in group):
            continue
        pairs = _cross_table_pairs(group)
        if pairs - covered:
            joins.append(PossibleJoin(tuple(group)))
            covered |= pairs
    return joins


def _declared_edges(layer: Any):
    """Declared column pairs, and the columns that reference each referenced key."""
    declared: set[frozenset[tuple[str, str]]] = set()
    referencing: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for table in layer.tables.values():
        for relationship in table.relationships:
            match = _EQUALITY.fullmatch(relationship.join_pattern or "")
            if not match:
                continue
            left_table, left_column, right_table, right_column = (
                _unquote(part) for part in match.groups()
            )
            left, right = (left_table, left_column), (right_table, right_column)
            declared.add(frozenset((left, right)))
            if left_table == right_table:
                continue
            # The referenced side is the target of a many-to-one and the source of a
            # one-to-many; one-to-one and many-to-many state no direction.
            kind = relationship.relationship_type
            if kind == "many_to_one":
                referencing[right].add(left)
            elif kind == "one_to_many":
                referencing[left].add(right)
    return declared, referencing


def _cross_table_pairs(
    columns: list[tuple[str, str]],
) -> set[frozenset[tuple[str, str]]]:
    return {
        frozenset((a, b))
        for i, a in enumerate(columns)
        for b in columns[i + 1 :]
        if a[0] != b[0]
    }


def _type_family(data_type: str) -> str:
    for family, pattern in _TYPE_FAMILIES:
        if pattern.search(data_type):
            return family
    return data_type.casefold()


def _primary_key(table: Any) -> frozenset[str]:
    primary = [index for index in table.indexes.values() if index.is_primary is True]
    if len(primary) != 1 or len(primary[0].columns) != 1:
        return frozenset()
    return frozenset(primary[0].columns)


def _unquote(identifier: str) -> str:
    if identifier.startswith('"') and identifier.endswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier
