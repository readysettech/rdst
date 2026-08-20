"""Recognize RDST's own diagnostic SQL by the shape of its templates.

RDST reads user relations while profiling a schema. Those statements now
carry an ``/*rdst:...*/`` self marker (see ``shared.db_connection``) and
registry admission drops them on sight, but statements issued before the
marker existed were admitted as if a user had run them, and no marker can
reach them retroactively. This module recognizes them by structure.

Recognition is exact, not fuzzy. Each shape here mirrors one
template-generating site:

- ``features/schema/semantic_layer/data_profiler.py``: ``_pg_column_stats``
  / ``_mysql_column_stats``, ``_pg_top_values`` / ``_mysql_top_values``,
  ``_pg_sample_rows``.
- ``features/schema/semantic_layer/introspector.py``:
  ``_sample_postgres_enum_values`` and ``_sample_mysql_enum_values``.
- ``features/schema/semantic_layer/pattern_detector.py``:
  ``detect_delimiter_columns_sql_postgres`` / ``_mysql``.

A candidate is matched by extracting the table and column names out of the
stored text, re-instantiating the template for exactly those names, and
comparing the two placeholder-blind shapes for equality. Extraction only
has to be a good enough guess: the equality check is the proof, so a query
that merely resembles a template (a user's own ``TABLESAMPLE`` query, say)
fails on the first clause the template does not account for.

The shapes are frozen copies rather than imports of the live builders: the
statements to recognize are historical, so the recognizer has to keep
describing the templates as they stood when those rows were admitted.
``tests/unit/test_library_store.py`` pins the delimiter shapes against the
live ``pattern_detector`` builders, which are pure functions.

Two template forms are deliberately absent, because their emitted text is
indistinguishable from an ordinary user query: the small-table
``_pg_sample_rows`` / ``_mysql_sample_rows`` form (``SELECT * FROM t LIMIT
n``) and the full-scan enum sample (``SELECT DISTINCT c FROM t WHERE c IS
NOT NULL LIMIT n``). Both stay in the library.
"""

from __future__ import annotations

import re
from typing import Callable, Iterator, Optional

from shared.db_connection import quote_identifier

__all__ = ["match_self_template"]

# Literal-bearing tokens: engine placeholders and the literals they stand
# for. The templates vary only in numbers and the delimiter string, so
# blanking these leaves the structure to compare.
_LITERAL = re.compile(r"'(?:[^']|'')*'|\$\d+|:p\d+|\?|(?<![\w\"`])\d+(?:\.\d+)?")
_PUNCTUATION = re.compile(r"\s*([(),])\s*")
_LEADING_COMMENT = re.compile(r"^\s*/\*.*?\*/\s*")

# A quoted identifier in either dialect, captured without its quotes.
_IDENT = r'"((?:[^"]|"")+)"|`((?:[^`]|``)+)`'


def _shape(sql: str) -> str:
    """Placeholder-blind, whitespace-blind, case-blind form of a statement."""
    text = _LEADING_COMMENT.sub("", sql)
    text = _LITERAL.sub("#", " ".join(text.split()))
    return _PUNCTUATION.sub(r"\1", text).lower()


def _names(match: re.Match[str]) -> tuple[str, str]:
    """Identifier text and its dialect from an ``_IDENT`` group pair."""
    if match.group(1) is not None:
        return match.group(1).replace('""', '"'), "postgresql"
    return match.group(2).replace("``", "`"), "mysql"


# -- template shapes -------------------------------------------------------


def _column_stats(table: str, columns: list[str], engine: str, sampled: bool) -> str:
    """data_profiler ``_pg_column_stats`` / ``_mysql_column_stats``."""
    parts = []
    for col in columns:
        q = quote_identifier(col, engine)
        parts.append(
            f'COUNT({q}) AS {quote_identifier(col + "__cnt", engine)}, '
            f'COUNT(DISTINCT {q}) AS {quote_identifier(col + "__dist", engine)}, '
            f'SUM(CASE WHEN {q} IS NULL THEN 1 ELSE 0 END) AS '
            f'{quote_identifier(col + "__nulls", engine)}'
        )
    head = f'SELECT COUNT(*) AS __total, {", ".join(parts)} FROM '
    quoted_table = quote_identifier(table, engine)
    if not sampled:
        return head + quoted_table
    if engine == "mysql":
        inner = ", ".join(quote_identifier(col, engine) for col in columns)
        return f"{head}(SELECT {inner} FROM {quoted_table} LIMIT 10000) sampled"
    return f"{head}{quoted_table} TABLESAMPLE SYSTEM(1)"


def _top_values(table: str, column: str, engine: str, sampled: bool) -> str:
    """data_profiler ``_pg_top_values`` / ``_mysql_top_values``."""
    q = quote_identifier(column, engine)
    quoted_table = quote_identifier(table, engine)
    if engine == "mysql":
        source = quoted_table
        if sampled:
            source = f"(SELECT {q} FROM {quoted_table} LIMIT 20000) sampled"
        return (
            f"SELECT CAST({q} AS CHAR) AS val, COUNT(*) AS cnt "
            f"FROM {source} WHERE {q} IS NOT NULL "
            f"GROUP BY val ORDER BY cnt DESC LIMIT 10"
        )
    sample = " TABLESAMPLE SYSTEM(1)" if sampled else ""
    return (
        f"SELECT {q}::text, COUNT(*) AS cnt FROM {quoted_table}{sample} "
        f"WHERE {q} IS NOT NULL GROUP BY {q} ORDER BY cnt DESC LIMIT 10"
    )


def _sample_rows(table: str) -> str:
    """data_profiler ``_pg_sample_rows``, sampled form only."""
    quoted_table = quote_identifier(table, "postgresql")
    return f"SELECT * FROM {quoted_table} TABLESAMPLE SYSTEM(1) LIMIT 5"


def _enum_sample(table: str, column: str, engine: str) -> str:
    """introspector ``_sample_postgres_enum_values`` / ``_sample_mysql_...``."""
    q = quote_identifier(column, engine)
    quoted_table = quote_identifier(table, engine)
    if engine == "mysql":
        return (
            f"SELECT DISTINCT {q} FROM (SELECT {q} FROM {quoted_table} "
            f"LIMIT 10000) subq WHERE {q} IS NOT NULL LIMIT 21"
        )
    return (
        f"WITH sampled AS (SELECT {q} FROM {quoted_table} "
        f"TABLESAMPLE SYSTEM(1) LIMIT 50000) SELECT DISTINCT {q} FROM sampled "
        f"WHERE {q} IS NOT NULL LIMIT 21"
    )


def _delimiter(table: str, columns: list[str], engine: str, sampled: bool) -> str:
    """pattern_detector ``detect_delimiter_columns_sql_postgres`` / ``_mysql``."""
    cast = "" if engine == "mysql" else "::float"
    parts = []
    for col in columns:
        q = quote_identifier(col, engine)
        parts.append(
            f"SUM(CASE WHEN {q} LIKE '%,%' THEN 1 ELSE 0 END){cast} "
            f"/ NULLIF(COUNT({q}), 0) AS {q}"
        )
    head = f'SELECT {", ".join(parts)} FROM '
    quoted_table = quote_identifier(table, engine)
    if not sampled:
        return head + quoted_table
    if engine == "mysql":
        inner = ", ".join(quote_identifier(col, engine) for col in columns)
        return f"{head}(SELECT {inner} FROM {quoted_table} LIMIT 10000) sampled"
    return f"{head}{quoted_table} TABLESAMPLE SYSTEM(1)"


# -- candidate extraction --------------------------------------------------

_LAST_FROM = re.compile(rf"\bFROM\s+(?:{_IDENT})", re.IGNORECASE)
_STATS_COLUMNS = re.compile(rf"\bCOUNT\(\s*(?:{_IDENT})\s*\)\s+AS\b", re.IGNORECASE)
_TOP_VALUES_COLUMN = re.compile(
    rf"^\s*SELECT\s+(?:CAST\(\s*)?(?:{_IDENT})\s*(?:::text|\s+AS\s+CHAR\s*\))",
    re.IGNORECASE,
)
_ENUM_PG_COLUMN = re.compile(
    rf"^\s*WITH\s+sampled\s+AS\s*\(\s*SELECT\s+(?:{_IDENT})\s+FROM\b",
    re.IGNORECASE,
)
_ENUM_MYSQL_COLUMN = re.compile(
    rf"^\s*SELECT\s+DISTINCT\s+(?:{_IDENT})\s+FROM\s*\(", re.IGNORECASE
)
_DELIMITER_COLUMNS = re.compile(
    rf"\bSUM\(\s*CASE\s+WHEN\s+(?:{_IDENT})\s+LIKE\b", re.IGNORECASE
)


def _table_of(sql: str) -> Optional[tuple[str, str]]:
    """Name and dialect of the relation the innermost FROM reads."""
    matches = list(_LAST_FROM.finditer(sql))
    return _names(matches[-1]) if matches else None


def _column_list(pattern: re.Pattern[str], sql: str) -> Optional[tuple[list[str], str]]:
    names = [_names(match) for match in pattern.finditer(sql)]
    if not names:
        return None
    engines = {engine for _, engine in names}
    if len(engines) != 1:
        return None
    return [name for name, _ in names], engines.pop()


def _single_column(pattern: re.Pattern[str], sql: str) -> Optional[tuple[str, str]]:
    match = pattern.search(sql)
    return _names(match) if match else None


# -- matching --------------------------------------------------------------


def _column_stats_candidates(sql: str) -> Iterator[tuple[str, str]]:
    relation = _table_of(sql)
    columns = _column_list(_STATS_COLUMNS, sql)
    if relation is None or columns is None or columns[1] != relation[1]:
        return
    for sampled in (False, True):
        yield "column_stats", _column_stats(
            relation[0], columns[0], relation[1], sampled
        )


def _top_values_candidates(sql: str) -> Iterator[tuple[str, str]]:
    relation = _table_of(sql)
    column = _single_column(_TOP_VALUES_COLUMN, sql)
    if relation is None or column is None or column[1] != relation[1]:
        return
    for sampled in (False, True):
        yield "top_values", _top_values(relation[0], column[0], relation[1], sampled)


def _sample_rows_candidates(sql: str) -> Iterator[tuple[str, str]]:
    relation = _table_of(sql)
    if relation is None or relation[1] != "postgresql":
        return
    yield "sample_rows", _sample_rows(relation[0])


def _enum_sample_candidates(sql: str) -> Iterator[tuple[str, str]]:
    relation = _table_of(sql)
    if relation is None:
        return
    pattern = _ENUM_PG_COLUMN if relation[1] == "postgresql" else _ENUM_MYSQL_COLUMN
    column = _single_column(pattern, sql)
    if column is None or column[1] != relation[1]:
        return
    yield "enum_sample", _enum_sample(relation[0], column[0], relation[1])


def _delimiter_candidates(sql: str) -> Iterator[tuple[str, str]]:
    relation = _table_of(sql)
    columns = _column_list(_DELIMITER_COLUMNS, sql)
    if relation is None or columns is None or columns[1] != relation[1]:
        return
    for sampled in (False, True):
        yield "delimiter_probe", _delimiter(
            relation[0], columns[0], relation[1], sampled
        )


_SHAPES: tuple[Callable[[str], Iterator[tuple[str, str]]], ...] = (
    _column_stats_candidates,
    _top_values_candidates,
    _sample_rows_candidates,
    _enum_sample_candidates,
    _delimiter_candidates,
)


def match_self_template(sql: str) -> Optional[str]:
    """Name the RDST diagnostic template a statement is an exact instance of.

    Returns the shape name (``column_stats``, ``top_values``,
    ``sample_rows``, ``enum_sample``, ``delimiter_probe``) or None when the
    text is anything else.
    """
    if not sql or not sql.strip():
        return None
    # Extraction anchors on the statement itself, so a self marker (or any
    # other leading comment) comes off before the clauses are read.
    text = _LEADING_COMMENT.sub("", sql)
    subject = _shape(text)
    for candidates in _SHAPES:
        try:
            for name, template in candidates(text):
                if _shape(template) == subject:
                    return name
        except ValueError:
            continue
    return None
